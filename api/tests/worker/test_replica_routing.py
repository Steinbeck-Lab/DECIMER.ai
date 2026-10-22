"""Calls to a service with several replicas: the load is spread, a dead replica is skipped
and the call retried elsewhere, and the retry rules of `test_services.py` hold per call across
replicas. Fake replicas are addresses behind `httpx.MockTransport`, found by a fake resolver."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from decimer_api.worker.errors import ServiceFailed, ServiceTimedOut, ServiceUnavailable
from decimer_api.worker.replicas import OUTSIDE_A_JOB, Lease, Ticket
from decimer_api.worker.services import Services, make_services
from decimer_api.worker.settings import WorkerSettings
from tests.worker.fakes import FakeBackend, FakeResolver, FakeSleep, fleet, make_crop
from tests.worker.test_replicas import Ticker, settle
from tests.worker.test_services import page_png

PNG = make_crop(0, 0)
PNG_PAGE = page_png()
Build = Callable[..., Services]


@pytest.fixture
async def build() -> AsyncIterator[Build]:
    clients: list[httpx.AsyncClient] = []

    def factory(
        backend: FakeBackend,
        resolver: FakeResolver,
        sleep: FakeSleep,
        clock: Ticker | None = None,
        **overrides: object,
    ) -> Services:
        values: dict[str, object] = {
            "seg_url": "http://seg.test:8001",
            "ocsr_url": "http://ocsr.test:8002",
            "cls_url": "http://cls.test:8003",
            "chem_url": "",
            "connect_retry_budget_seconds": 1.5,  # two waits: 0.5 s and 1 s
        }
        settings = WorkerSettings(**{**values, **overrides})  # type: ignore[arg-type]
        client = httpx.AsyncClient(transport=backend.transport)
        clients.append(client)
        return make_services(
            client, settings, sleep=sleep, resolver=resolver, clock=clock or Ticker()
        )

    yield factory
    for client in clients:
        await client.aclose()


def rig(**counts: int) -> tuple[FakeBackend, FakeResolver, FakeSleep]:
    hosts, resolver = fleet(**counts)
    return FakeBackend(hosts=hosts), resolver, FakeSleep()


# --- load spread -------------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["seg", "ocsr", "cls"])
async def test_the_load_is_spread_over_the_replicas_of_each_service(
    build: Build, service: str
) -> None:
    backend, resolver, sleep = rig(seg=2, ocsr=2, cls=2)
    backend.delay = 0.01
    services = build(backend, resolver, sleep)
    calls = {
        "seg": lambda: services.segment(PNG_PAGE, 0),
        "ocsr": lambda: services.recognise(make_crop(0, 0)),
        "cls": lambda: services.classify(PNG),
    }
    await asyncio.gather(*(calls[service]() for _ in range(8)))
    used = {host: backend.calls_to(host) for host in resolver.answers[f"{service}.test"]}
    assert sorted(used.values()) == [4, 4], used
    assert backend.peak[service] == 2  # both replicas at once, one call each
    assert max(backend.host_peak.values()) == 1


async def test_a_replica_takes_calls_up_to_its_own_limit(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    backend.delay = 0.01
    services = build(backend, resolver, sleep, ocsr_concurrency=3)
    await asyncio.gather(*(services.recognise(make_crop(0, i)) for i in range(20)))
    assert backend.peak["ocsr"] == 6  # 2 replicas x 3
    assert set(backend.host_peak.values()) == {3}  # never more than 3 on one replica
    assert await services.capacity("ocsr") == 6


async def test_a_job_may_ask_for_as_many_calls_as_all_replicas_take(build: Build) -> None:
    backend, resolver, sleep = rig(seg=6, ocsr=3, cls=3)
    services = build(backend, resolver, sleep)
    assert [await services.capacity(s) for s in ("seg", "ocsr", "cls")] == [6, 3, 3]


# --- a dead replica ----------------------------------------------------------------------------


async def test_a_replica_that_refuses_the_connection_is_skipped_and_the_call_goes_on(
    build: Build,
) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    dead, alive = resolver.answers["ocsr.test"]
    backend.down.add(dead)
    services = build(backend, resolver, sleep)
    assert (await services.recognise(make_crop(0, 0))).smiles == "CC"
    assert sleep.delays == []  # another replica was healthy: no waiting, no budget spent
    assert backend.calls_to(alive) == 1
    for _ in range(5):  # and later calls do not try it again
        await services.recognise(make_crop(0, 0))
    assert backend.host_calls.count(dead) == 0  # a refused connection never reached the fake
    assert backend.calls_to(alive) == 6


async def test_the_replica_is_tried_again_after_its_cooldown(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    dead, alive = resolver.answers["ocsr.test"]
    backend.down.add(dead)
    clock = Ticker()
    services = build(backend, resolver, sleep, clock, replica_cooldown_seconds=10)
    await services.recognise(make_crop(0, 0))
    backend.down.clear()  # it came back
    for _ in range(4):
        await services.recognise(make_crop(0, 0))
    assert backend.calls_to(dead) == 0  # still in its cooldown
    clock.advance(10)
    for _ in range(4):
        await services.recognise(make_crop(0, 0))
    assert backend.calls_to(dead) > 0  # and in use again


async def test_a_connection_that_breaks_after_the_request_is_retried_on_another_replica(
    build: Build,
) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    broken, other = resolver.answers["ocsr.test"]
    backend.reset.add(broken)
    services = build(backend, resolver, sleep)
    assert (await services.recognise(make_crop(0, 0))).smiles == "CC"
    assert backend.host_calls == [broken, other]  # the one sent-retry went elsewhere
    await services.recognise(make_crop(0, 0))
    assert backend.calls_to(broken) == 1  # skipped from now on


async def test_a_connection_that_breaks_on_every_replica_fails_the_call_after_one_retry(
    build: Build,
) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    backend.reset.update(resolver.answers["ocsr.test"])
    services = build(backend, resolver, sleep)
    with pytest.raises(ServiceFailed, match="connection lost"):
        await services.recognise(make_crop(0, 0))
    assert (
        len(backend.host_calls) == 2
    )  # the first try and sent_retries (1) more, not one per replica


async def test_with_every_replica_down_the_budget_is_spent_once_per_call(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    backend.down.update(resolver.answers["ocsr.test"])
    services = build(backend, resolver, sleep)
    with pytest.raises(ServiceUnavailable):
        await services.recognise(make_crop(0, 0))
    assert sleep.delays == [0.5, 1.0]  # the same waits as with one replica, not per replica


async def test_a_replica_that_answers_after_a_wait_is_used(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    backend.down.update(resolver.answers["ocsr.test"])

    original = sleep.__call__

    async def come_back(delay: float) -> None:
        backend.down.clear()  # the service finished starting during the wait
        await original(delay)

    services = build(backend, resolver, sleep)
    services._caller._sleep = come_back  # type: ignore[assignment]
    assert (await services.recognise(make_crop(0, 0))).smiles == "CC"


async def test_a_slow_replica_is_not_a_dead_one(build: Build) -> None:
    """A read timeout says the replica is slow, not that it is down: it stays in use, and the
    call itself is not retried (a slow model would only get a longer queue)."""
    backend, resolver, sleep = rig(ocsr=2)
    seen = {"n": 0}

    def hook(request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if seen["n"] == 1:
            raise httpx.ReadTimeout("no answer", request=request)
        return None

    backend.hooks["ocsr"] = hook
    services = build(backend, resolver, sleep)
    with pytest.raises(ServiceTimedOut):
        await services.recognise(make_crop(0, 0))
    await services.recognise(make_crop(0, 0))
    await services.recognise(make_crop(0, 0))
    first, second = resolver.answers["ocsr.test"]
    assert backend.host_calls == [first, second, first]  # `first` took the next turn it was due


async def test_a_crash_inducing_input_takes_down_at_most_two_replicas_per_call(
    build: Build,
) -> None:
    """The one retry after a broken connection is the only time a request is sent again after a
    replica may have started on it, so an input that kills the service it reaches kills at most
    `1 + SENT_RETRIES` replicas per call, however many replicas there are."""
    backend, resolver, sleep = rig(ocsr=3)
    backend.reset.update(resolver.answers["ocsr.test"])
    services = build(backend, resolver, sleep)
    with pytest.raises(ServiceFailed):
        await services.recognise(make_crop(0, 0))
    assert len(backend.host_calls) == 2
    assert len(set(backend.host_calls)) == 2  # two different replicas; the third was spared


@pytest.mark.parametrize("retries", [0, 2])
async def test_the_number_of_replicas_a_poison_input_can_reach_follows_the_sent_retries(
    build: Build, retries: int
) -> None:
    backend, resolver, sleep = rig(ocsr=4)
    backend.reset.update(resolver.answers["ocsr.test"])
    services = build(backend, resolver, sleep, sent_retries=retries)
    with pytest.raises(ServiceFailed):
        await services.recognise(make_crop(0, 0))
    assert len(backend.host_calls) == 1 + retries


async def test_an_answer_ends_the_cooldown_of_the_replica_that_gave_it(build: Build) -> None:
    """The only replica refuses a connection and is cooling; it comes back during the wait and
    answers the retry. Its answer is what shows it is alive again, not the cooldown running out."""
    backend, resolver, sleep = rig(ocsr=1)
    backend.down.update(resolver.answers["ocsr.test"])
    original = sleep.__call__

    async def come_back(delay: float) -> None:
        backend.down.clear()
        await original(delay)

    services = build(backend, resolver, sleep)
    services._caller._sleep = come_back  # type: ignore[assignment]
    await services.recognise(make_crop(0, 0))
    assert services._caller._pools[
        "ocsr"
    ].has_healthy()  # the clock has not moved: no cooldown ended


async def test_a_slot_or_a_call_outside_a_job_carries_the_shared_ticket(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=1)
    services = build(backend, resolver, sleep)
    pool = services._caller._pools["ocsr"]
    seen: list[Ticket] = []
    acquire = pool.acquire

    async def spy(ticket: Ticket) -> Lease:
        seen.append(ticket)
        return await acquire(ticket)

    pool.acquire = spy  # type: ignore[method-assign]
    async with services.slot("ocsr"):
        pass
    await services._caller.post("ocsr", "/infer", files={"image": ("c.png", PNG, "image/png")})
    assert seen == [OUTSIDE_A_JOB, OUTSIDE_A_JOB]


async def test_calls_outside_a_job_share_one_place_in_the_turn_order(build: Build) -> None:
    """Calls made outside a job (a validation request) are one "job" together. With a ticket
    per call each of them would be the job served longest ago and go ahead of real jobs."""
    backend, resolver, sleep = rig(ocsr=1)
    services = build(backend, resolver, sleep)
    pool = services._caller._pools["ocsr"]
    served: list[str] = []
    acquire = pool.acquire

    async def spy(ticket: Ticket) -> Lease:
        lease = await acquire(ticket)
        served.append(ticket.job_id or "outside")
        return lease

    pool.acquire = spy  # type: ignore[method-assign]
    held = await acquire(Ticket("holder"))
    job = services.for_job("job", False)
    outside = services._caller

    async def outside_call() -> None:
        await outside.post("ocsr", "/infer", files={"image": ("c.png", PNG, "image/png")})

    tasks = []
    for call in (job.recognise(PNG), job.recognise(PNG), outside_call(), outside_call()):
        tasks.append(asyncio.create_task(call))
        await settle()
    held.release()
    await asyncio.gather(*tasks)
    # The job's first call goes first (nobody has been served), then the outside calls as one
    # job, then the job again: not job, outside, outside, job.
    assert served == ["job", "outside", "job", "outside"]


# --- names change ------------------------------------------------------------------------------


async def test_the_names_are_looked_up_again_at_once_after_a_connect_failure(
    build: Build,
) -> None:
    """A replica that restarts comes back under another address: the call that found the old one
    dead looks the name up again and goes to the new address without waiting."""
    backend, resolver, sleep = rig(ocsr=1)
    (old,) = resolver.answers["ocsr.test"]
    new = "10.0.9.9"
    backend.hosts[new] = "ocsr"
    backend.down.add(old)
    answers = iter([[old], [new]])

    async def moved(host: str, port: int) -> list[str]:
        resolver.lookups.append((host, port))
        return next(answers)

    services = build(backend, moved, sleep)  # type: ignore[arg-type]
    assert (await services.recognise(make_crop(0, 0))).smiles == "CC"
    assert backend.host_calls == [new]
    assert sleep.delays == []
    assert len(resolver.lookups) == 2  # the first call, and the one after the failure


async def test_replicas_that_join_and_leave_change_where_calls_go(build: Build) -> None:
    backend, resolver, sleep = rig(ocsr=2)
    clock = Ticker()
    services = build(backend, resolver, sleep, clock, replica_refresh_seconds=10)
    first, second = resolver.answers["ocsr.test"]
    for _ in range(4):
        await services.recognise(make_crop(0, 0))
    assert {backend.calls_to(first), backend.calls_to(second)} == {2}
    third = "10.0.1.3"
    backend.hosts[third] = "ocsr"
    resolver.answers["ocsr.test"] = [second, third]  # `first` is gone from DNS, `third` is new
    clock.advance(10)
    await services.capacity("ocsr")  # the next call after the interval starts the lookup ...
    await settle()  # ... which runs in the background
    backend.host_calls.clear()
    for _ in range(4):
        await services.recognise(make_crop(0, 0))
    assert set(backend.host_calls) == {second, third}
    assert await services.capacity("ocsr") == 2


# --- the other rules still hold ----------------------------------------------------------------


async def test_a_busy_replica_is_waited_for_not_swapped(build: Build) -> None:
    """503 keeps the slot and the retry goes to the same replica: with several callers in line a
    busy replica is not hammered, and the budget is the call's own."""
    backend, resolver, sleep = rig(ocsr=2)
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        return httpx.Response(503, headers={"Retry-After": "2"}) if seen["n"] == 1 else None

    backend.hooks["ocsr"] = hook
    services = build(backend, resolver, sleep)
    await services.recognise(make_crop(0, 0))
    first, _ = resolver.answers["ocsr.test"]
    assert backend.host_calls == [first, first]
    assert sleep.delays == [2.0]
