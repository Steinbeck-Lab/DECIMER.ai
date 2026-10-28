"""`Capacity`, and what it protects: RDKit reads and ZIP downloads."""

from __future__ import annotations

import asyncio
import io
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from decimer_api import analysis_pool, result_routes
from decimer_api.capacity import Capacity
from decimer_api.errors import ApiError
from decimer_api.exports import Entry
from decimer_api.models import Job
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock
from tests.results import crop_path, make_done_job, molfile_of

# --- the primitive -------------------------------------------------------------------------------


def test_places_are_taken_and_given_back_and_the_next_one_is_refused_not_queued() -> None:
    capacity = Capacity(2, retry_after_seconds=7)
    first, second = capacity.take(), capacity.take()
    assert capacity.in_use == 2
    with pytest.raises(ApiError) as refused:
        capacity.take()
    assert (refused.value.status, refused.value.code.value) == (503, "busy")
    assert refused.value.headers == {"Retry-After": "7"}
    first.release()
    assert capacity.in_use == 1
    capacity.take()
    second.release()


def test_a_place_given_back_twice_counts_once() -> None:
    capacity = Capacity(1)
    place = capacity.take()
    place.release()
    place.release()
    assert capacity.in_use == 0
    capacity.take()
    with pytest.raises(ApiError):
        capacity.take()  # one place, not two, after the double release


def test_a_capacity_needs_a_place() -> None:
    with pytest.raises(ValueError):
        Capacity(0)


async def test_hold_gives_the_place_back_also_when_the_block_fails() -> None:
    capacity = Capacity(1)
    with pytest.raises(RuntimeError):
        async with capacity.hold():
            assert capacity.in_use == 1
            raise RuntimeError("boom")
    assert capacity.in_use == 0


async def test_threads_never_outnumber_the_places() -> None:
    capacity = Capacity(2)
    running = peak = 0
    lock = threading.Lock()

    def work() -> int:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return 1

    async def attempt() -> str:
        try:
            await capacity.run_in_thread(work)
        except ApiError:
            return "busy"
        return "done"

    outcomes = await asyncio.gather(*(attempt() for _ in range(6)))
    assert outcomes.count("done") == 2 and outcomes.count("busy") == 4
    assert peak == 2
    assert capacity.in_use == 0


async def test_a_cancelled_caller_does_not_free_the_place_before_the_thread_ends() -> None:
    capacity = Capacity(1)
    release = threading.Event()
    started = threading.Event()

    def work() -> None:
        started.set()
        release.wait(5)

    task = asyncio.create_task(capacity.run_in_thread(work))
    while not started.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert capacity.in_use == 1  # the thread is still running: its place is still taken
    release.set()
    for _ in range(200):
        if capacity.in_use == 0:
            break
        await asyncio.sleep(0.005)
    assert capacity.in_use == 0


async def test_the_place_is_given_back_when_the_function_raises() -> None:
    capacity = Capacity(1)

    def broken() -> None:
        raise ValueError("no")

    with pytest.raises(ValueError):
        await capacity.run_in_thread(broken)
    assert capacity.in_use == 0


# --- RDKit reads (finding 1) ---------------------------------------------------------------------


@pytest.fixture
async def job(store: JobStore, settings: Settings, clock: FakeClock) -> Job:
    return await make_done_job(store, settings, clock)


@pytest.fixture
def slow_analysis(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """`analyse_molfile` as the routes see it, held in its thread until `release` is set."""
    from decimer_api.chemistry import analyse_molfile as real

    release, inside = threading.Event(), threading.Event()

    def held(molfile: str) -> object:
        inside.set()
        release.wait(10)
        return real(molfile)

    monkeypatch.setattr(analysis_pool, "analyse_molfile", held)
    return release, inside


async def validate(client: httpx.AsyncClient, job_id: str, smiles: str = "CCO") -> httpx.Response:
    return await client.post(
        f"/api/jobs/{job_id}/results/0/validate", json={"molfile": molfile_of(smiles)}
    )


async def test_a_third_validate_while_two_are_reading_molfiles_is_503_busy_at_once(
    client: httpx.AsyncClient, job: Job, slow_analysis: tuple[threading.Event, threading.Event]
) -> None:
    release, inside = slow_analysis
    running = [asyncio.create_task(validate(client, job.id)) for _ in range(2)]
    while not inside.is_set():
        await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)
    started = time.monotonic()
    refused = await validate(client, job.id)
    assert refused.status_code == 503
    assert refused.json()["detail"]["code"] == "busy"
    assert refused.headers["retry-after"] == "2"
    assert time.monotonic() - started < 0.2  # refused, not queued behind the other two
    release.set()
    assert [r.status_code for r in await asyncio.gather(*running)] == [200, 200]
    assert (await validate(client, job.id)).status_code == 200  # and the places are free again


async def test_an_export_with_edits_takes_one_place_for_all_of_them_and_is_refused_when_full(
    client: httpx.AsyncClient,
    job: Job,
    app: FastAPI,
    slow_analysis: tuple[threading.Event, threading.Event],
) -> None:
    analysis: Capacity = app.state.deps.analysis.capacity
    held = [analysis.take() for _ in range(analysis.places)]
    edits = {"0": molfile_of("CCCl"), "1": molfile_of("CCBr"), "2": molfile_of("CCF")}
    refused = await client.post(
        f"/api/jobs/{job.id}/export", json={"format": "csv", "molfiles": edits}
    )
    assert refused.status_code == 503 and refused.json()["detail"]["code"] == "busy"
    # An export with nothing to read needs no place.
    assert (
        await client.post(f"/api/jobs/{job.id}/export", json={"format": "csv"})
    ).status_code == 200
    held[0].release()
    slow_analysis[0].set()
    ok = await client.post(f"/api/jobs/{job.id}/export", json={"format": "csv", "molfiles": edits})
    assert ok.status_code == 200
    assert analysis.in_use == 1  # one place per export, however many edits, and it is given back
    held[1].release()


async def test_a_molfile_that_cannot_be_read_gives_its_place_back(
    client: httpx.AsyncClient, job: Job, app: FastAPI
) -> None:
    bad = "title\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"  # cleans, RDKit reads it
    assert (
        await client.post(f"/api/jobs/{job.id}/results/0/validate", json={"molfile": bad})
    ).status_code == 200
    broken = "hello\nworld\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    assert (
        await client.post(f"/api/jobs/{job.id}/results/0/validate", json={"molfile": broken})
    ).status_code == 422
    assert app.state.deps.analysis.capacity.in_use == 0


# --- ZIP streams (finding 4) ---------------------------------------------------------------------


async def zip_export(client: httpx.AsyncClient, job_id: str) -> httpx.Response:
    return await client.post(f"/api/jobs/{job_id}/export", json={"format": "zip"})


async def test_a_zip_download_beyond_the_limit_is_503_busy_and_the_other_formats_are_not(
    client: httpx.AsyncClient, job: Job, app: FastAPI
) -> None:
    streams: Capacity = app.state.deps.streams
    held = [streams.take() for _ in range(streams.places)]
    assert streams.places == Settings().max_concurrent_downloads == 8
    refused = await zip_export(client, job.id)
    assert refused.status_code == 503
    assert refused.json()["detail"]["code"] == "busy"
    assert refused.headers["retry-after"] == "2"
    for fmt in ("sdf", "csv"):
        assert (
            await client.post(f"/api/jobs/{job.id}/export", json={"format": fmt})
        ).status_code == 200
    held.pop().release()
    assert (await zip_export(client, job.id)).status_code == 200


async def test_the_place_is_given_back_after_a_finished_download_and_after_a_failed_one(
    client: httpx.AsyncClient,
    job: Job,
    app: FastAPI,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streams: Capacity = app.state.deps.streams
    response = await zip_export(client, job.id)
    assert zipfile.ZipFile(io.BytesIO(response.content)).testzip() is None
    assert streams.in_use == 0
    crop = crop_path(settings, job, 2)
    real_open = Path.open

    def unreadable(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == crop:  # a crop that is there but cannot be opened: 404 before the first byte
            raise PermissionError("denied")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", unreadable)
    assert (await zip_export(client, job.id)).status_code == 404
    assert streams.in_use == 0


async def test_the_place_is_held_while_a_slow_client_reads_and_freed_when_it_goes_away(
    app: FastAPI, job: Job, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ASGI call is driven by hand: the 'client' reads one piece, then stops reading, then
    hangs up. The place (and with it the open files) lasts exactly that long."""
    streams: Capacity = app.state.deps.streams
    opened: list[object] = []
    real_zip_stream = result_routes.zip_stream

    def watching(entries: list[Entry], moment: object) -> object:
        opened.extend(entry.source for entry in entries if not isinstance(entry.source, bytes))
        return real_zip_stream(entries, moment)  # type: ignore[arg-type]

    monkeypatch.setattr(result_routes, "zip_stream", watching)
    body = b'{"format": "zip"}'
    big = crop_path(settings, job, 0)
    big.write_bytes(big.read_bytes() + bytes(range(256)) * 4096)  # 1 MB: more than one piece
    path = f"/api/jobs/{job.id}/export"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("203.0.113.9", 1),
        "server": ("api", 80),
    }
    sent_one = asyncio.Event()
    hang_up = asyncio.Event()
    delivered: list[dict[str, object]] = []
    request_sent = False

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await hang_up.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        delivered.append(message)
        if message["type"] == "http.response.body":
            sent_one.set()
            await hang_up.wait()  # the client does not read any more
            raise OSError("the client went away")

    call = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
    await asyncio.wait_for(sent_one.wait(), 5)
    assert streams.in_use == 1  # a download is going on, holding its files
    assert len(opened) == 5 and not any(f.closed for f in opened)  # type: ignore[attr-defined]
    hang_up.set()
    await asyncio.wait_for(asyncio.gather(call, return_exceptions=True), 5)
    for _ in range(100):
        if streams.in_use == 0:
            break
        await asyncio.sleep(0.01)
    assert streams.in_use == 0
    assert all(f.closed for f in opened)  # type: ignore[attr-defined]  # and no file left open


async def test_the_limit_is_a_setting(
    settings: Settings, store: JobStore, enqueue: object, namer: object
) -> None:
    from decimer_api.app import create_app

    small = create_app(
        settings.model_copy(update={"max_concurrent_downloads": 1, "max_concurrent_analyses": 1}),
        store,
        enqueue,  # type: ignore[arg-type]
        namer=namer,  # type: ignore[arg-type]
    )
    assert (small.state.deps.streams.places, small.state.deps.analysis.capacity.places) == (1, 1)


# --- downloads: a share per client, and a time limit ---------------------------------------


class Download:
    """A ZIP download driven by hand at the ASGI level: the 'client' takes the first piece and
    then stops reading until `hang_up` is set."""

    def __init__(self, app: FastAPI, job_id: str, peer: str, fmt: str = "zip") -> None:
        body = ('{"format": "%s"}' % fmt).encode()  # noqa: UP031
        path = f"/api/jobs/{job_id}/export"
        self.scope: dict[str, object] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": (peer, 1),
            "server": ("api", 80),
        }
        self.body = body
        self.app = app
        self.sent_one = asyncio.Event()
        self.hang_up = asyncio.Event()
        self.messages: list[dict[str, object]] = []
        self._asked = False
        self.task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, object]:
        if not self._asked:
            self._asked = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        await self.hang_up.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, object]) -> None:
        self.messages.append(message)
        if message["type"] == "http.response.body" and message.get("more_body"):
            self.sent_one.set()
            await self.hang_up.wait()  # the client does not read any more
            raise OSError("the client went away")

    def start(self) -> Download:
        self.task = asyncio.create_task(self.app(self.scope, self._receive, self._send))  # type: ignore[arg-type]
        return self

    @property
    def status(self) -> int:
        return int(self.messages[0]["status"])  # type: ignore[call-overload]

    def header(self, name: str) -> str | None:
        for key, value in self.messages[0]["headers"]:  # type: ignore[attr-defined]
            if key == name.encode():
                return str(value.decode())
        return None

    async def finish(self) -> None:
        self.hang_up.set()
        assert self.task is not None
        await asyncio.wait_for(asyncio.gather(self.task, return_exceptions=True), 5)


@pytest.fixture
def big_crop(job: Job, settings: Settings) -> None:
    """A first crop of about 1 MB, so that a download is more than one piece."""
    path = crop_path(settings, job, 0)
    path.write_bytes(path.read_bytes() + bytes(range(256)) * 4096)


async def test_one_client_may_have_two_downloads_at_once_and_the_third_is_429(
    app: FastAPI, job: Job, big_crop: None
) -> None:
    mine = [Download(app, job.id, "203.0.113.5").start() for _ in range(2)]
    for download in mine:
        await asyncio.wait_for(download.sent_one.wait(), 5)
    third = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(third.finish(), 5)
    assert third.status == 429
    assert third.header("retry-after") is not None and int(third.header("retry-after") or 0) >= 1
    assert b"rate_limited" in b"".join(
        m["body"]
        for m in third.messages
        if m["type"] == "http.response.body"  # type: ignore[misc]
    )
    # Another client is not held back by it, and neither is the same client's CSV.
    other = Download(app, job.id, "203.0.113.6").start()
    await asyncio.wait_for(other.sent_one.wait(), 5)
    csv = Download(app, job.id, "203.0.113.5", fmt="csv").start()
    await asyncio.wait_for(csv.finish(), 5)
    assert csv.status == 200
    # One of mine ends: the share comes back.
    await mine[0].finish()
    again = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(again.sent_one.wait(), 5)
    for download in (*mine[1:], other, again):
        await download.finish()
    assert app.state.deps.streams.in_use == 0


async def test_a_refused_download_leaves_nothing_taken(
    app: FastAPI, job: Job, big_crop: None
) -> None:
    streams: Capacity = app.state.deps.streams
    holding = [Download(app, job.id, "203.0.113.5").start() for _ in range(2)]
    for download in holding:
        await asyncio.wait_for(download.sent_one.wait(), 5)
    refused = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(refused.finish(), 5)
    assert streams.in_use == 2  # the two that run; the refused one took no place
    for download in holding:
        await download.finish()
    assert streams.in_use == 0
    shares = app.state.deps.client_streams
    assert shares.clients == 0  # and the table of clients does not keep entries


async def test_a_global_refusal_gives_the_clients_share_back(
    app: FastAPI, job: Job, big_crop: None
) -> None:
    streams: Capacity = app.state.deps.streams
    held = [streams.take() for _ in range(streams.places)]
    refused = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(refused.finish(), 5)
    assert refused.status == 503
    for place in held:
        place.release()
    assert app.state.deps.client_streams.clients == 0
    ok = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(ok.sent_one.wait(), 5)
    await ok.finish()


async def test_a_download_is_cut_off_at_the_time_limit_and_everything_is_given_back(
    settings: Settings,
    store: JobStore,
    enqueue: object,
    namer: object,
    job: Job,
    big_crop: None,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that takes the first piece and never reads another holds a place and a file per
    crop for ever; after DOWNLOAD_TIMEOUT_SECONDS the stream is aborted and all of it freed."""
    import logging

    from decimer_api.analysis_pool import InProcessAnalysis
    from decimer_api.app import create_app

    quick = settings.model_copy(update={"download_timeout_seconds": 0.4})
    app = create_app(
        quick,
        store,
        enqueue,  # type: ignore[arg-type]
        namer=namer,  # type: ignore[arg-type]
        analysis=InProcessAnalysis(2),
    )
    opened: list[object] = []
    real_zip_stream = result_routes.zip_stream

    def watching(entries: list[Entry], moment: object) -> object:
        opened.extend(e.source for e in entries if not isinstance(e.source, bytes))
        return real_zip_stream(entries, moment)  # type: ignore[arg-type]

    monkeypatch.setattr(result_routes, "zip_stream", watching)
    download = Download(app, job.id, "203.0.113.5").start()
    await asyncio.wait_for(download.sent_one.wait(), 5)
    assert app.state.deps.streams.in_use == 1
    started = asyncio.get_running_loop().time()
    with caplog.at_level(logging.WARNING, logger="decimer_api"):
        assert download.task is not None
        await asyncio.wait_for(
            asyncio.gather(download.task, return_exceptions=True), 5
        )  # NOT hung up
    assert asyncio.get_running_loop().time() - started < 2  # it ended on its own, at the limit
    assert app.state.deps.streams.in_use == 0
    assert app.state.deps.client_streams.clients == 0
    assert opened and all(f.closed for f in opened)  # type: ignore[attr-defined]
    assert "download" in caplog.text and "0.4" in caplog.text


def test_the_limits_are_settings() -> None:
    settings = Settings()
    assert settings.max_downloads_per_client == 2
    assert settings.download_timeout_seconds == 120.0
