"""Per-IP and global limits on POST /api/jobs."""

from __future__ import annotations

import logging
from collections.abc import Callable
from ipaddress import ip_network

import httpx
import pytest

from decimer_api.ingest_runner import IngestRunner
from decimer_api.models import JobStatus
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock, FakeEnqueue
from tests.factories import make_png, multipart


async def submit(client: httpx.AsyncClient, **headers: str) -> httpx.Response:
    body, mp_headers = multipart([("a.png", make_png())])
    return await client.post("/api/jobs", content=body, headers={**mp_headers, **headers})


ClientFactory = Callable[..., httpx.AsyncClient]


async def test_a_third_active_job_from_one_ip_is_429(client: httpx.AsyncClient) -> None:
    first, second = await submit(client), await submit(client)
    assert (first.status_code, second.status_code) == (202, 202)
    assert (first.json()["queuePosition"], second.json()["queuePosition"]) == (1, 2)

    third = await submit(client)
    assert third.status_code == 429
    detail = third.json()["detail"]
    assert detail["code"] == "rate_limited"
    assert "2" in detail["message"]
    # A rejected job has no queue position and no record.
    assert "queuePosition" not in third.text and "id" not in third.json()


async def test_a_finished_job_frees_the_slot(client: httpx.AsyncClient, store: JobStore) -> None:
    first = (await submit(client)).json()
    await submit(client)
    assert (await submit(client)).status_code == 429
    job = await store.get(first["id"])
    assert job is not None
    job.status = JobStatus.DONE
    await store.finish(job)
    assert (await submit(client)).status_code == 202
    assert (await submit(client)).status_code == 429


async def test_other_addresses_have_their_own_slots(make_client: ClientFactory) -> None:
    a, b = make_client("203.0.113.1"), make_client("203.0.113.2")
    for _ in range(2):
        assert (await submit(a)).status_code == 202
    assert (await submit(a)).status_code == 429
    assert (await submit(b)).status_code == 202


async def test_a_spoofed_forwarded_for_from_an_untrusted_peer_is_ignored(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue, ingest_runner: IngestRunner
) -> None:
    """The attacker connects directly and names a different address in every request."""
    from decimer_api.app import create_app

    trusting = settings.model_copy(update={"trusted_proxy_cidr": [ip_network("10.0.0.0/8")]})
    app = create_app(trusting, store, enqueue, ingest_runner=ingest_runner)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.50", 4000))
    attacker = httpx.AsyncClient(transport=transport, base_url="http://api.test")

    statuses = [
        (await submit(attacker, **{"x-forwarded-for": f"198.51.100.{n}"})).status_code
        for n in range(1, 5)
    ]
    assert statuses == [202, 202, 429, 429]


async def test_forwarded_for_is_honoured_from_the_trusted_proxy(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue, ingest_runner: IngestRunner
) -> None:
    from decimer_api.app import create_app

    trusting = settings.model_copy(update={"trusted_proxy_cidr": [ip_network("10.0.0.0/8")]})
    app = create_app(trusting, store, enqueue, ingest_runner=ingest_runner)
    proxy = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.7", 4000)),
        base_url="http://api.test",
    )

    def via(real: str, forged: str = "6.6.6.6") -> dict[str, str]:
        return {"x-forwarded-for": f"{forged}, {real}"}

    # Client A fills its two slots; rotating the forged left-hand entry does not help it.
    assert (await submit(proxy, **via("198.51.100.1", "1.1.1.1"))).status_code == 202
    assert (await submit(proxy, **via("198.51.100.1", "2.2.2.2"))).status_code == 202
    assert (await submit(proxy, **via("198.51.100.1", "3.3.3.3"))).status_code == 429
    # Client B, behind the same proxy, is not affected by A.
    assert (await submit(proxy, **via("198.51.100.2"))).status_code == 202


async def test_a_full_queue_is_503_with_retry_after(
    settings: Settings,
    store: JobStore,
    enqueue: FakeEnqueue,
    clock: FakeClock,
    ingest_runner: IngestRunner,
) -> None:
    from decimer_api.app import create_app

    small = settings.model_copy(update={"queue_max": 2})
    small_store = JobStore(store.redis, small, clock=clock)
    app = create_app(small, small_store, enqueue, ingest_runner=ingest_runner)

    def client_from(peer: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=(peer, 1)), base_url="http://api.test"
        )

    assert (await submit(client_from("203.0.113.1"))).status_code == 202
    assert (await submit(client_from("203.0.113.2"))).status_code == 202
    rejected = await submit(client_from("203.0.113.3"))
    assert rejected.status_code == 503
    assert rejected.headers["retry-after"] == str(small.queue_retry_after_seconds)
    detail = rejected.json()["detail"]
    assert detail["code"] == "queue_full"
    assert "queuePosition" not in rejected.text
    assert len(enqueue.job_ids) == 2


async def test_an_ip_over_its_limit_is_refused_before_the_body_is_read(
    client: httpx.AsyncClient,
) -> None:
    await submit(client)
    await submit(client)
    garbage = await client.post(
        "/api/jobs", content=b"not multipart", headers={"content-type": "text/plain"}
    )
    assert garbage.status_code == 429


@pytest.mark.parametrize("n", [3, 6])
async def test_queue_position_is_reported_while_waiting(make_client: ClientFactory, n: int) -> None:
    positions = []
    for index in range(n):
        response = await submit(make_client(f"203.0.113.{index + 1}"))
        positions.append(response.json()["queuePosition"])
    assert positions == list(range(1, n + 1))


# --- a private peer that sends X-Forwarded-For but is not trusted ---------------------------


async def test_a_private_peer_sending_forwarded_for_is_logged_once(
    make_client: ClientFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """The usual sign that TRUSTED_PROXY_CIDR is missing the proxy: every client then counts as
    the proxy's address."""
    proxy = make_client("10.1.2.3")
    with caplog.at_level(logging.WARNING, logger="decimer_api"):
        await submit(proxy, **{"x-forwarded-for": "198.51.100.1"})
        await submit(proxy, **{"x-forwarded-for": "198.51.100.2"})
    records = [r for r in caplog.records if "X-Forwarded-For" in r.getMessage()]
    assert len(records) == 1
    assert "10.1.2.3" in records[0].getMessage() and "TRUSTED_PROXY_CIDR" in records[0].getMessage()


async def test_other_peers_do_not_trigger_the_warning(
    make_client: ClientFactory,
    caplog: pytest.LogCaptureFixture,
    settings: Settings,
    store: JobStore,
    enqueue: FakeEnqueue,
    ingest_runner: IngestRunner,
) -> None:
    from decimer_api.app import create_app

    trusting = settings.model_copy(update={"trusted_proxy_cidr": [ip_network("10.0.0.0/8")]})
    app = create_app(trusting, store, enqueue, ingest_runner=ingest_runner)
    trusted = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("10.0.0.7", 1)), base_url="http://api.test"
    )
    with caplog.at_level(logging.WARNING, logger="decimer_api"):
        await submit(
            make_client("8.8.8.8"), **{"x-forwarded-for": "198.51.100.1"}
        )  # a global address
        await submit(make_client("10.9.9.9"))  # private but sends no header
        await submit(trusted, **{"x-forwarded-for": "198.51.100.1"})  # trusted
    assert [r for r in caplog.records if "X-Forwarded-For" in r.getMessage()] == []
