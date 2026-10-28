"""The client's slot is taken before the upload is read, and ingest capacity is bounded.

These tests cover a real problem: parallel uploads from one address used to be read and
decoded in full, and only the last step (admit) refused them."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from decimer_api.app import create_app
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from decimer_api.uploads import Ingested, Upload
from tests.conftest import FakeEnqueue
from tests.factories import make_png, multipart

ClientFactory = Callable[..., httpx.AsyncClient]


class CountingIngest:
    """A runner that records how many ingests start and how many overlap, and takes a while."""

    def __init__(self, seconds: float = 0.15) -> None:
        self.seconds = seconds
        self.started = 0
        self.running = 0
        self.peak = 0
        self.names: list[str] = []  # upload names, in the order their ingests started
        self.peak_per_client: dict[str, int] = {}  # clients are the part of a name before "-"
        self._running_per_client: dict[str, int] = {}
        self._lock = threading.Lock()
        self.gate: asyncio.Event | None = None

    async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
        client = uploads[0].name.split("-")[0]
        with self._lock:
            self.started += 1
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.names.append(uploads[0].name)
            now = self._running_per_client.get(client, 0) + 1
            self._running_per_client[client] = now
            self.peak_per_client[client] = max(self.peak_per_client.get(client, 0), now)
        try:
            if self.gate is not None:
                await self.gate.wait()
            else:
                await asyncio.sleep(self.seconds)
            (job_dir / "input").mkdir(parents=True)
            (job_dir / "input" / "0001.png").write_bytes(b"x")
            return Ingested(pages=1, pages_processed=1)
        finally:
            with self._lock:
                self.running -= 1
                self._running_per_client[client] -= 1


def client_for(app: FastAPI, peer: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(peer, 1))
    return httpx.AsyncClient(transport=transport, base_url="http://api.test")


async def submit(client: httpx.AsyncClient, name: str = "a.png") -> httpx.Response:
    body, headers = multipart([(name, make_png())])
    return await client.post("/api/jobs", content=body, headers=headers)


def job_dirs(settings: Settings) -> list[Path]:
    return sorted(settings.data_dir.iterdir()) if settings.data_dir.exists() else []


async def test_parallel_uploads_from_one_ip_never_start_more_ingests_than_it_may_hold(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    """BEFORE the fix: 6 ingests started (4 of them wasted, refused only at the end).
    AFTER: 2 start; the other 4 get 429 as soon as they arrive."""
    runner = CountingIngest()
    app = create_app(settings, store, enqueue, ingest_runner=runner)
    client = client_for(app, "203.0.113.77")

    responses = await asyncio.gather(*(submit(client) for _ in range(6)))

    assert sorted(r.status_code for r in responses) == [202, 202, 429, 429, 429, 429]
    assert runner.started == settings.max_active_jobs_per_ip
    assert len(enqueue.job_ids) == 2


async def test_a_refused_client_costs_nothing_but_the_slot_check(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    runner = CountingIngest()
    app = create_app(settings, store, enqueue, ingest_runner=runner)
    client = client_for(app, "203.0.113.78")
    assert (await submit(client)).status_code == 202
    assert (await submit(client)).status_code == 202

    chunks_read = 0
    body, headers = multipart([("a.png", make_png())])

    async def stream() -> AsyncIterator[bytes]:
        nonlocal chunks_read
        for start in range(0, len(body), 100):
            chunks_read += 1
            yield body[start : start + 100]

    request = client.build_request("POST", "/api/jobs", content=stream(), headers=headers)
    response = await client.send(request)

    assert response.status_code == 429
    assert chunks_read == 0  # the body was never read
    assert runner.started == 2


async def test_slots_of_different_ips_are_independent_and_all_get_ingested(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    runner = CountingIngest()
    app = create_app(settings, store, enqueue, ingest_runner=runner)
    responses = await asyncio.gather(
        *(submit(client_for(app, f"203.0.113.{n}")) for n in range(1, 5))
    )
    assert [r.status_code for r in responses] == [202] * 4


async def test_ingests_overall_never_exceed_the_ingest_concurrency(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    configured = settings.model_copy(update={"ingest_concurrency": 2, "ingest_wait_seconds": 30})
    runner = CountingIngest(seconds=0.1)
    app = create_app(configured, store, enqueue, ingest_runner=runner)
    responses = await asyncio.gather(
        *(submit(client_for(app, f"203.0.113.{n}")) for n in range(1, 9))
    )
    assert [r.status_code for r in responses] == [202] * 8
    assert runner.peak == 2  # never more, and the limit was actually reached


async def test_waiting_too_long_for_an_ingest_slot_is_503_and_leaves_nothing_behind(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    configured = settings.model_copy(update={"ingest_concurrency": 1, "ingest_wait_seconds": 0.2})
    runner = CountingIngest()
    runner.gate = asyncio.Event()
    app = create_app(configured, store, enqueue, ingest_runner=runner)

    holder = asyncio.create_task(submit(client_for(app, "203.0.113.1")))
    while runner.started < 1:
        await asyncio.sleep(0.01)  # the first upload now owns the only ingest slot

    started = time.monotonic()
    # (a limit on the test itself: without the wait budget this request would wait for ever)
    late = await asyncio.wait_for(submit(client_for(app, "203.0.113.2")), timeout=5)
    waited = time.monotonic() - started

    assert late.status_code == 503
    assert late.json()["detail"]["code"] == "busy"
    assert late.headers["retry-after"] == str(configured.queue_retry_after_seconds)
    assert 0.15 < waited < 2  # it waited for its budget, not forever
    assert runner.started == 1  # the late upload never got to ingest
    # nothing of the late upload remains: no slot, no queue entry, no folder
    assert await store.active_count(_hash(app, "203.0.113.2")) == 0
    assert await store.queue_length() == 0  # the first upload is still being ingested

    runner.gate.set()
    assert (await holder).status_code == 202
    assert len(job_dirs(configured)) == 1  # the first job's folder; none for the late one
    assert await store.queue_length() == 1
    # and the late client can simply try again
    assert (await submit(client_for(app, "203.0.113.2"))).status_code == 202


def _hash(app: FastAPI, peer: str) -> str:
    return str(app.state.deps.hash_client(peer))


async def test_a_failed_upload_gives_its_slot_back(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    body, headers = multipart([("a.png", b"not an image at all")])
    for _ in range(settings.max_active_jobs_per_ip + 2):
        assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 415
    assert (await submit(client)).status_code == 202


async def test_an_unexpected_error_gives_its_slot_back_and_has_the_standard_shape(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    class Broken:
        async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
            job_dir.mkdir(parents=True)
            raise RuntimeError("bug in the runner")

    app = create_app(settings, store, enqueue, ingest_runner=Broken())
    transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 1), raise_app_exceptions=False)
    client = httpx.AsyncClient(transport=transport, base_url="http://api.test")
    for _ in range(4):  # more than the limit: only possible if each failure freed its slot
        response = await submit(client)
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "internal_error"
    assert job_dirs(settings) == []
    assert await store.queue_length() == 0


async def test_a_client_that_hangs_up_mid_upload_gives_its_slot_back(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    runner = CountingIngest()
    app = create_app(settings, store, enqueue, ingest_runner=runner)
    client = client_for(app, "203.0.113.9")
    body, headers = multipart([("a.png", make_png())])

    async def stalls() -> AsyncIterator[bytes]:
        yield body[:50]
        await asyncio.sleep(30)  # the client goes quiet, then the request is cancelled
        yield body[50:]

    request = client.build_request("POST", "/api/jobs", content=stalls(), headers=headers)
    task = asyncio.create_task(client.send(request))
    await asyncio.sleep(0.1)
    assert await store.active_count(_hash(app, "203.0.113.9")) == 1  # the reservation is held
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert await store.active_count(_hash(app, "203.0.113.9")) == 0
    assert runner.started == 0


async def until(condition: Callable[[], bool], seconds: float = 3) -> None:
    async with asyncio.timeout(seconds):
        while not condition():
            await asyncio.sleep(0.01)


async def test_one_client_cannot_occupy_every_ingest_slot(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    """MAX_ACTIVE_JOBS_PER_IP (2) equals INGEST_CONCURRENCY (2): without a per-client limit one
    address could hold both ingest slots and starve everyone else for as long as it likes."""
    configured = settings.model_copy(
        update={"ingest_concurrency": 2, "ingest_wait_seconds": 30, "max_active_jobs_per_ip": 2}
    )
    runner = CountingIngest()
    runner.gate = asyncio.Event()
    app = create_app(configured, store, enqueue, ingest_runner=runner)

    first = asyncio.create_task(submit(client_for(app, "203.0.113.1"), "a-1.png"))
    await until(lambda: "a-1.png" in runner.names)
    second = asyncio.create_task(submit(client_for(app, "203.0.113.1"), "a-2.png"))
    await asyncio.sleep(0.1)  # a's second upload now waits for a's first, not for a free slot
    other = asyncio.create_task(submit(client_for(app, "203.0.113.2"), "b-1.png"))

    await until(lambda: "b-1.png" in runner.names)  # B starts at once, a-2 still waiting
    assert "a-2.png" not in runner.names

    runner.gate.set()
    assert [r.status_code for r in await asyncio.gather(first, second, other)] == [202] * 3
    assert runner.names.index("a-2.png") > runner.names.index("a-1.png")
    assert runner.peak_per_client["a"] == 1  # never two of a's uploads at the same time


async def test_a_clients_second_upload_waits_only_for_its_own_first(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    configured = settings.model_copy(update={"ingest_concurrency": 2, "ingest_wait_seconds": 0.3})
    runner = CountingIngest()
    runner.gate = asyncio.Event()  # a's first upload never finishes within the budget
    app = create_app(configured, store, enqueue, ingest_runner=runner)

    first = asyncio.create_task(submit(client_for(app, "203.0.113.1"), "a-1.png"))
    await until(lambda: "a-1.png" in runner.names)
    second = await asyncio.wait_for(submit(client_for(app, "203.0.113.1"), "a-2.png"), timeout=5)
    assert second.status_code == 503 and second.json()["detail"]["code"] == "busy"
    assert runner.names == ["a-1.png"]
    assert await store.active_count(_hash(app, "203.0.113.1")) == 1  # a-2 gave its slot back

    runner.gate.set()
    assert (await first).status_code == 202
