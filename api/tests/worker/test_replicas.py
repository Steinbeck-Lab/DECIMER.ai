"""The replica pool: discovery, least-busy routing, health, and the fair dispatcher.

No HTTP here: the pool is driven directly with a fake resolver and a fake clock. What the
callers do with a lease (retries, budgets) is in `test_replica_routing.py`.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from decimer_api.worker.replicas import Lease, ReplicaPool, Ticket, system_resolver
from tests.worker.fakes import FakeResolver

URL = "http://seg.test:8001"
HOST = "seg.test"
A, B, C = "10.0.0.1", "10.0.0.2", "10.0.0.3"


class Ticker:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_pool(
    addresses: list[str] | None = None,
    *,
    per_replica: int = 1,
    small_burst: int = 4,
    url: str = URL,
    clock: Ticker | None = None,
    refresh_seconds: float = 10.0,
    cooldown_seconds: float = 10.0,
) -> tuple[ReplicaPool, FakeResolver, Ticker]:
    resolver = FakeResolver({HOST: list(addresses or [])})
    ticker = clock or Ticker()
    pool = ReplicaPool(
        "seg",
        url,
        per_replica,
        resolver=resolver,
        clock=ticker,
        refresh_seconds=refresh_seconds,
        cooldown_seconds=cooldown_seconds,
        small_burst=small_burst,
    )
    return pool, resolver, ticker


async def settle() -> None:
    """Let every task that can run, run."""
    for _ in range(6):
        await asyncio.sleep(0)


def address(lease: Lease) -> str:
    return lease.replica.address


# --- discovery ---------------------------------------------------------------------------------


async def test_a_name_with_several_addresses_is_several_replicas() -> None:
    pool, resolver, _ = make_pool([A, B, C], per_replica=2)
    assert await pool.capacity() == 6  # 3 replicas x 2 calls each
    assert pool.addresses == [A, B, C]
    assert resolver.lookups == [(HOST, 8001)]


async def test_a_call_goes_to_the_address_with_the_port_and_path_of_the_url() -> None:
    pool, _, _ = make_pool([A], url="http://seg.test:8001/prefix/")
    lease = await pool.acquire(Ticket())
    assert lease.url == "http://10.0.0.1:8001/prefix"


async def test_an_ipv6_address_is_put_in_brackets() -> None:
    pool, _, _ = make_pool(["fd00::5"])
    assert (await pool.acquire(Ticket())).url == "http://[fd00::5]:8001"


async def test_a_name_that_does_not_resolve_is_one_replica_under_its_own_name() -> None:
    pool, _, _ = make_pool([])
    lease = await pool.acquire(Ticket())
    assert lease.url == URL
    assert pool.addresses == [HOST]
    assert await pool.capacity() == 1


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:8001", "http://[::1]:8001", "http://localhost:8001"]
)
async def test_an_ip_literal_or_localhost_is_one_replica_and_is_never_looked_up(url: str) -> None:
    """Development and the in-process mode use these: `localhost` is two addresses (::1 and
    127.0.0.1) of one server, and counting them as two replicas would double its load."""
    pool, resolver, _ = make_pool(["10.9.9.9"], url=url)
    lease = await pool.acquire(Ticket())
    await pool.refresh(force=True)
    assert lease.url == url
    assert len(pool.addresses) == 1
    assert resolver.lookups == []


async def test_the_name_is_looked_up_again_after_the_refresh_interval() -> None:
    pool, resolver, clock = make_pool([A, B])
    await pool.capacity()
    resolver.answers[HOST] = [A, B, C]
    clock.advance(9)
    await pool.capacity()
    assert pool.addresses == [A, B]  # too early
    await settle()
    assert len(resolver.lookups) == 1  # nothing was looked up before the interval ended
    clock.advance(1)
    await pool.capacity()  # the first call after the interval starts a lookup in the background
    await settle()
    assert await pool.capacity() == 3
    assert pool.addresses == [A, B, C]
    assert len(resolver.lookups) == 2


async def test_a_new_replica_is_used_at_once() -> None:
    pool, resolver, clock = make_pool([A])
    first = await pool.acquire(Ticket())  # A is busy
    resolver.answers[HOST] = [A, B]
    clock.advance(10)
    second = await pool.acquire(Ticket())  # does not wait for A
    assert (address(first), address(second)) == (A, B)


async def test_a_new_replica_wakes_the_calls_that_were_waiting() -> None:
    pool, resolver, _ = make_pool([A])
    await pool.acquire(Ticket())
    waiting = asyncio.create_task(pool.acquire(Ticket()))
    await settle()
    assert not waiting.done()
    resolver.answers[HOST] = [A, B]
    await pool.refresh(force=True)
    assert address(await asyncio.wait_for(waiting, 1)) == B


async def test_a_replica_that_leaves_dns_is_dropped_once_it_is_idle() -> None:
    pool, resolver, clock = make_pool([A, B])
    on_b = None
    for _ in range(2):
        lease = await pool.acquire(Ticket())
        on_b = lease if address(lease) == B else on_b
    assert on_b is not None
    resolver.answers[HOST] = [A]
    clock.advance(10)
    await pool.refresh()
    assert pool.addresses == [A]  # B takes no new calls ...
    assert pool.in_flight == {A: 1, B: 1}  # ... but the one it has is left alone
    assert await pool.capacity() == 1
    on_b.release()
    assert pool.in_flight == {A: 1}  # dropped when idle


async def test_a_replica_that_leaves_and_comes_back_while_busy_is_the_same_replica() -> None:
    pool, resolver, _ = make_pool([A, B])
    leases = [await pool.acquire(Ticket()) for _ in range(2)]
    resolver.answers[HOST] = [A]
    await pool.refresh(force=True)
    resolver.answers[HOST] = [A, B]
    await pool.refresh(force=True)
    assert pool.addresses == [A, B]
    for lease in leases:
        lease.release()
    assert pool.in_flight == {A: 0, B: 0}


async def test_a_failed_or_empty_lookup_keeps_the_replicas_already_known() -> None:
    pool, resolver, _ = make_pool([A, B])
    await pool.capacity()
    resolver.error = OSError("temporary failure in name resolution")
    await pool.refresh(force=True)
    assert pool.addresses == [A, B]
    resolver.error = None
    resolver.answers[HOST] = []
    await pool.refresh(force=True)
    assert pool.addresses == [A, B]


async def test_addresses_that_appear_replace_the_host_name_fallback() -> None:
    pool, resolver, _ = make_pool([])
    await pool.capacity()
    assert pool.addresses == [HOST]
    resolver.answers[HOST] = [A, B]
    await pool.refresh(force=True)
    assert pool.addresses == [A, B]


async def test_callers_that_ask_for_a_refresh_together_share_one_lookup() -> None:
    gate = asyncio.Event()
    lookups: list[int] = []

    async def slow(_host: str, _port: int) -> list[str]:
        lookups.append(1)
        await gate.wait()
        return [A, B]

    pool = ReplicaPool("seg", URL, 1, resolver=slow)
    tasks = [asyncio.create_task(pool.refresh(force=True)) for _ in range(5)]
    await settle()
    gate.set()
    await asyncio.gather(*tasks)
    assert len(lookups) == 1
    assert pool.addresses == [A, B]


async def test_system_resolver_uses_a_records_and_ipv6_only_when_there_are_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def info(family: socket.AddressFamily, host: str) -> tuple[object, ...]:
        return (family, socket.SOCK_STREAM, 6, "", (host, 8001))

    answers: list[tuple[object, ...]] = []

    async def getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return answers

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    answers[:] = [
        info(socket.AF_INET6, "fd00::2"),
        info(socket.AF_INET, A),
        info(socket.AF_INET6, "fd00::3"),
        info(socket.AF_INET, B),
        info(socket.AF_INET, A),  # a duplicate
    ]
    assert await system_resolver(HOST, 8001) == [A, B]  # a dual-stack replica is not counted twice
    answers[:] = [info(socket.AF_INET6, "fd00::2")]
    assert await system_resolver(HOST, 8001) == ["fd00::2"]


# --- least-busy routing ------------------------------------------------------------------------


async def test_a_call_goes_to_the_replica_with_the_fewest_calls_in_flight() -> None:
    pool, _, _ = make_pool([A, B, C], per_replica=2)
    leases = {address(lease): lease for lease in [await pool.acquire(Ticket()) for _ in range(3)]}
    assert set(leases) == {A, B, C}  # one each before any gets a second
    leases[B].release()  # B is now the idle one
    assert address(await pool.acquire(Ticket())) == B


async def test_replicas_that_are_equally_busy_take_turns() -> None:
    pool, _, _ = make_pool([A, B, C])
    order = []
    for _ in range(6):
        lease = await pool.acquire(Ticket())
        order.append(address(lease))
        lease.release()
    assert order == [A, B, C, A, B, C]


async def test_a_replica_never_has_more_calls_than_its_limit() -> None:
    pool, _, _ = make_pool([A, B], per_replica=2)
    leases = [await pool.acquire(Ticket()) for _ in range(4)]
    assert pool.in_flight == {A: 2, B: 2}
    fifth = asyncio.create_task(pool.acquire(Ticket()))
    await settle()
    assert not fifth.done() and pool.waiting == 1
    leases[0].release()
    late = await asyncio.wait_for(fifth, 1)
    assert address(late) == address(leases[0])
    assert sorted(pool.in_flight.values()) == [2, 2]


async def test_the_limit_holds_when_many_callers_come_at_once() -> None:
    pool, _, _ = make_pool([A, B, C], per_replica=2)
    peak = {A: 0, B: 0, C: 0}
    done = 0

    async def call() -> None:
        nonlocal done
        lease = await pool.acquire(Ticket())
        for replica, n in pool.in_flight.items():
            peak[replica] = max(peak[replica], n)
        await asyncio.sleep(0)
        lease.release()
        done += 1

    await asyncio.gather(*(call() for _ in range(40)))
    assert done == 40
    assert peak == {A: 2, B: 2, C: 2}  # all of the capacity was used, and no more


# --- health ------------------------------------------------------------------------------------


async def test_a_replica_that_failed_is_skipped_until_its_cooldown_ends() -> None:
    pool, _, clock = make_pool([A, B])
    first = await pool.acquire(Ticket())
    assert address(first) == A
    first.release(alive=False)  # refused the connection
    on_b = await pool.acquire(Ticket())
    assert address(on_b) == B
    second = asyncio.create_task(pool.acquire(Ticket()))  # waits for B, does not go to A
    await settle()
    assert not second.done()
    on_b.release()
    assert address(await asyncio.wait_for(second, 1)) == B
    clock.advance(10)
    third = await pool.acquire(Ticket())  # A is back
    assert address(third) == A


async def test_an_answer_from_a_replica_ends_its_cooldown_at_once() -> None:
    pool, _, _ = make_pool([A, B])
    leases = [await pool.acquire(Ticket()) for _ in range(2)]
    for lease in leases:
        lease.release(alive=False)
    assert not pool.has_healthy()
    again = await pool.acquire(Ticket())  # every replica is in its cooldown: all are tried
    again.release(alive=True)
    assert pool.has_healthy()
    # Only the one that answered is healthy now, so it takes the next calls, one at a time.
    for _ in range(3):
        lease = await pool.acquire(Ticket())
        assert address(lease) == address(again)
        lease.release(alive=True)


async def test_a_timeout_says_nothing_about_a_replicas_health() -> None:
    pool, _, _ = make_pool([A, B])
    lease = await pool.acquire(Ticket())
    lease.release(alive=None)
    assert pool.has_healthy()
    assert [address(await pool.acquire(Ticket())) for _ in range(2)] == [B, A]


async def test_when_every_replica_is_in_its_cooldown_they_are_all_tried_again() -> None:
    pool, _, _ = make_pool([A, B])
    for _ in range(2):
        (await pool.acquire(Ticket())).release(alive=False)
    assert not pool.has_healthy()
    lease = await asyncio.wait_for(pool.acquire(Ticket()), 1)
    assert address(lease) in (A, B)


async def test_a_lease_is_released_once() -> None:
    pool, _, _ = make_pool([A])
    lease = await pool.acquire(Ticket())
    lease.release(alive=True)
    lease.release(alive=False)  # ignored
    assert pool.in_flight == {A: 0} and pool.has_healthy()


# --- who gets a free slot ----------------------------------------------------------------------


async def serve(pool: ReplicaPool, wanted: list[tuple[str, Ticket]]) -> list[str]:
    """One slot is held. Every wanted call queues up in the order given; the slot is released
    and each call is served and released in turn. Returns the order they were served in."""
    held = await pool.acquire(Ticket())
    order: list[str] = []
    leases: list[Lease] = []

    async def want(name: str, ticket: Ticket) -> None:
        leases.append(await pool.acquire(ticket))
        order.append(name)

    tasks = []
    for name, ticket in wanted:
        tasks.append(asyncio.create_task(want(name, ticket)))
        await settle()
    assert pool.waiting == len(wanted)
    held.release()
    while len(order) < len(wanted):
        await settle()
        if leases:
            leases.pop().release()
    await asyncio.gather(*tasks)
    return order


async def test_a_small_job_goes_before_the_large_jobs_that_are_already_waiting() -> None:
    pool, _, _ = make_pool([A])
    big_a, big_b, small = Ticket("a"), Ticket("b"), Ticket("s", small=True)
    order = await serve(pool, [("a1", big_a), ("b1", big_b), ("a2", big_a), ("s1", small)])
    assert order[0] == "s1"


async def test_the_jobs_of_one_size_class_take_turns() -> None:
    pool, _, _ = make_pool([A])
    x, y = Ticket("x"), Ticket("y")
    wanted = [("x1", x), ("x2", x), ("x3", x), ("y1", y), ("y2", y), ("y3", y)]
    # Not x1 x2 x3 y1 y2 y3: a job with many calls waiting does not push out one with few.
    assert await serve(pool, wanted) == ["x1", "y1", "x2", "y2", "x3", "y3"]


async def test_small_jobs_take_turns_too() -> None:
    pool, _, _ = make_pool([A])
    s, t = Ticket("s", small=True), Ticket("t", small=True)
    wanted = [("s1", s), ("s2", s), ("t1", t), ("t2", t)]
    assert await serve(pool, wanted) == ["s1", "t1", "s2", "t2"]


async def test_a_stream_of_small_jobs_cannot_starve_a_large_one() -> None:
    """Small calls go first, but after `small_burst` of them in a row a waiting large call is
    served; here 3, so the large job is fourth in the line, not last of eleven."""
    pool, _, _ = make_pool([A], small_burst=3)
    wanted = [("large", Ticket("big"))] + [
        (f"small{n}", Ticket(f"s{n}", small=True)) for n in range(10)
    ]
    order = await serve(pool, wanted)
    assert order.index("large") == 3
    assert order[:3] == ["small0", "small1", "small2"]


async def test_the_bound_comes_round_again_for_the_next_large_call() -> None:
    pool, _, _ = make_pool([A], small_burst=2)
    big = Ticket("big")
    wanted = [("L1", big), ("L2", big)] + [(f"s{n}", Ticket(f"s{n}", small=True)) for n in range(6)]
    order = await serve(pool, wanted)
    assert order == ["s0", "s1", "L1", "s2", "s3", "L2", "s4", "s5"]


async def test_small_calls_served_before_any_large_one_waited_do_not_count_against_the_burst() -> (
    None
):
    pool, _, _ = make_pool([A], small_burst=3)
    lone = [(f"solo{n}", Ticket(f"o{n}", small=True)) for n in range(6)]
    assert await serve(pool, lone) == [name for name, _ in lone]
    # A large call and a small one arrive now: the small one still goes first.
    order = await serve(pool, [("large", Ticket("big")), ("small", Ticket("s", small=True))])
    assert order == ["small", "large"]


async def test_a_call_cancelled_while_it_waits_gives_up_its_place() -> None:
    pool, _, _ = make_pool([A])
    held = await pool.acquire(Ticket())
    gone = asyncio.create_task(pool.acquire(Ticket("gone")))
    stays = asyncio.create_task(pool.acquire(Ticket("stays")))
    await settle()
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    assert pool.waiting == 1
    held.release()
    assert address(await asyncio.wait_for(stays, 1)) == A
    assert pool.in_flight == {A: 1}


async def test_a_slot_granted_at_the_moment_of_cancellation_is_given_back() -> None:
    pool, _, _ = make_pool([A])
    held = await pool.acquire(Ticket())
    waiting = asyncio.create_task(pool.acquire(Ticket()))
    await settle()
    held.release()  # the slot goes to `waiting` ...
    waiting.cancel()  # ... which is cancelled before it has seen it
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert pool.in_flight == {A: 0}
    assert address(await asyncio.wait_for(pool.acquire(Ticket()), 1)) == A  # not leaked


# --- a cooldown that ends wakes a waiting call ---------------------------------------------------


def quick_pool(cooldown: float, clock: Ticker | None = None) -> ReplicaPool:
    """Two replicas and a cooldown of a few milliseconds of real time (the timer is the loop's)."""
    resolver = FakeResolver({HOST: [A, B]})
    if clock is None:
        return ReplicaPool("seg", URL, 1, resolver=resolver, cooldown_seconds=cooldown)
    return ReplicaPool("seg", URL, 1, resolver=resolver, clock=clock, cooldown_seconds=cooldown)


async def test_a_waiting_call_is_woken_when_a_cooldown_ends() -> None:
    """A is cooling, B is busy with a long call (ocsr: up to 55 s). The waiting call gets A the
    moment its cooldown ends, not when B's call finishes."""
    pool = quick_pool(0.05)
    on_a = await pool.acquire(Ticket())
    on_b = await pool.acquire(Ticket())
    assert (address(on_a), address(on_b)) == (A, B)
    on_a.release(alive=False)
    waiting = asyncio.create_task(pool.acquire(Ticket()))
    await settle()
    assert not waiting.done()  # A is cooling and B is busy
    woken = await asyncio.wait_for(waiting, 2)
    assert address(woken) == A
    assert pool.in_flight == {A: 1, B: 1}  # B's call was never finished
    await pool.aclose()


async def test_closing_the_pool_cancels_the_cooldown_timers() -> None:
    pool = quick_pool(0.05)
    on_a = await pool.acquire(Ticket())
    await pool.acquire(Ticket())
    on_a.release(alive=False)
    waiting = asyncio.create_task(pool.acquire(Ticket()))
    await pool.aclose()
    await asyncio.sleep(0.15)
    assert not waiting.done()  # the timer did not run
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)


async def test_a_timer_that_fires_before_the_cooldown_is_over_sets_another() -> None:
    """The timer is the loop's, the cooldown is the pool's clock (a test's, here): if the timer
    fires first the wake-up is set again for what is left instead of being lost."""
    pool = quick_pool(0.02, clock=Ticker())  # the fake clock does not move
    on_a = await pool.acquire(Ticket())
    await pool.acquire(Ticket())
    on_a.release(alive=False)
    waiting = asyncio.create_task(pool.acquire(Ticket()))
    await asyncio.sleep(0.1)
    replica = next(r for r in pool._replicas if r.address == A)
    assert replica.wake is not None  # set again, once, not a loop that spins
    assert not waiting.done()
    await pool.aclose()
    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)


# --- looking up replicas never holds up a call ---------------------------------------------------


class GatedResolver:
    """Answers the first lookup at once, later ones only when `gate` is set."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.gate = asyncio.Event()
        self.lookups = 0
        self.cancelled = False

    async def __call__(self, _host: str, _port: int) -> list[str]:
        self.lookups += 1
        if self.lookups > 1:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return list(self.answers)


async def test_a_slow_lookup_does_not_hold_up_calls() -> None:
    resolver = GatedResolver([A, B])
    clock = Ticker()
    pool = ReplicaPool("seg", URL, 1, resolver=resolver, clock=clock, refresh_seconds=10)
    first = await pool.acquire(Ticket())
    clock.advance(10)
    resolver.answers = [A, B, C]
    second = await asyncio.wait_for(
        pool.acquire(Ticket()), 1
    )  # the lookup is stuck, the call is not
    assert (address(first), address(second)) == (A, B)
    await settle()
    assert resolver.lookups == 2  # started in the background, and stuck at the gate
    third = asyncio.create_task(pool.acquire(Ticket()))
    await settle()
    assert resolver.lookups == 2  # one lookup at a time
    resolver.gate.set()  # DNS answers: C joins and takes the waiting call
    assert address(await asyncio.wait_for(third, 1)) == C
    await pool.aclose()


async def test_the_first_call_waits_for_the_first_lookup() -> None:
    """Nothing is known before it, so a call waits for it (and does not take the host name)."""
    gate = asyncio.Event()

    async def late(_host: str, _port: int) -> list[str]:
        await gate.wait()
        return [A, B]

    pool = ReplicaPool("seg", URL, 1, resolver=late)
    capacity = asyncio.create_task(pool.capacity())
    await settle()
    assert not capacity.done()
    gate.set()
    assert await asyncio.wait_for(capacity, 1) == 2


async def test_closing_the_pool_cancels_a_lookup_in_the_background() -> None:
    resolver = GatedResolver([A])
    clock = Ticker()
    pool = ReplicaPool("seg", URL, 1, resolver=resolver, clock=clock, refresh_seconds=10)
    await pool.capacity()
    clock.advance(10)
    await pool.capacity()  # starts the lookup, which waits on the gate
    await settle()
    assert resolver.lookups == 2
    await pool.aclose()
    assert resolver.cancelled
