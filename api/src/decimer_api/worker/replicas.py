"""Replicas of a model service: discovery, least-busy routing, a fair dispatcher (P3-04b, D18).

One `ReplicaPool` per service (seg, ocsr, cls, chem), shared by every job of the worker process.

Discovery. A service is one host name and one port (`SEG_URL=http://seg:8001`); every replica
answers on that port and Docker's DNS lists all of them under the name. The pool resolves the name
(the `Resolver`), keeps one `Replica` per address and sends requests to `http://<address>:<port>`.
The first call waits for the first lookup (nothing is known before it). After that the pool looks
again every `refresh_seconds` in a background task, so a slow lookup (the system resolver shares
the default thread pool with the worker's file and image work, and may take up to 3 s) never holds
up a call, and at once when a call that could not connect asks (`refresh(force=True)`, awaited by
that call alone). An address that is gone from DNS is dropped as soon as nothing is in flight on
it; a new one joins and is used at once. If the lookup fails or is empty the replicas already
known are kept. A URL whose host is an IP literal or `localhost`, and a name that does not
resolve at all, is one replica reached under the URL as given (development, the in-process mode,
the tests). The A records are used; AAAA records only when there are no A records (a dual-stack
network would otherwise count every replica twice).

Routing. Every replica takes `per_replica` calls at a time. A free slot goes to the healthy
replica with the fewest calls in flight; among equals, to the one that went longest without a
call (round-robin). A replica that refused the connection or broke it (`Lease.release(alive=False)`)
is skipped for `cooldown_seconds`, and a waiting call is woken when the cooldown ends (a timer, so
a call is not left waiting for some other call to finish); an answer from it (`alive=True`) clears
the cooldown at once. When every replica is in its cooldown they are all tried again, so a service
that is down is still reported by the caller's own retry rules instead of by a pool that waits for
nothing.

Dispatching. Calls wait in one queue per service and a free slot goes to one of the waiting calls
at once (no sleeping, no polling). Which one:

* A call of a *small* job (`Ticket.small`: a pasted image, one page) goes before a call of a large
  job, so it does not queue behind the calls of big PDFs. It still waits for the call that is
  running on the replica (a running call is never interrupted: ocsr takes 3 s for a structure and
  up to 55 s for an image that is not one), for the calls of other small jobs ahead of it in the
  turn order, for the one slot in every `small_burst + 1` that goes to a large call, and for calls
  of other processes on the same replica (the fast and the default worker each count their own
  calls, and a replica serves one at a time).
* Among the calls of one size class the jobs take turns: the call of the job that was served
  longest ago goes first, so the crops of two big jobs interleave and a job with many waiting
  calls does not push out one with a single call.
* The priority is bounded: after `small_burst` small calls in a row, while a large call is
  waiting, the next slot goes to a large call. A stream of small jobs therefore cannot starve a
  large one; it gets at least one slot in `small_burst + 1`.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import socket
import time
import weakref
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("decimer_api.worker.replicas")

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]
Clock = Callable[[], float]

RESOLVE_TIMEOUT_SECONDS = 3.0
DEFAULT_PORTS = {"http": 80, "https": 443}


async def system_resolver(host: str, port: int) -> list[str]:
    """The addresses of `host` (A records; AAAA only when there are none), in the order the
    resolver gave them. Raises `OSError` when the name does not resolve."""
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(
        loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), RESOLVE_TIMEOUT_SECONDS
    )
    v4 = [str(info[4][0]) for info in infos if info[0] == socket.AF_INET]
    v6 = [str(info[4][0]) for info in infos if info[0] == socket.AF_INET6]
    return list(dict.fromkeys(v4 or v6))


def _is_fixed_host(host: str) -> bool:
    """A host that is never looked up: an IP literal, or `localhost` (which resolves to ::1 and
    127.0.0.1, two addresses of one server)."""
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


@dataclass(eq=False)
class Ticket:
    """Who is asking: one per job run. `small` is the job's size class."""

    job_id: str = ""
    small: bool = False


# Every call made outside a job (a validation request, a test) shares this one ticket, so that
# together they are one "job" in the turn order. A ticket per call would make each of them the
# job served longest ago, and they would all go ahead of the calls of real jobs.
OUTSIDE_A_JOB = Ticket()


@dataclass(eq=False)
class Replica:
    address: str  # an IP address, or the host name when nothing resolved
    base_url: str  # no trailing slash
    in_flight: int = 0
    unhealthy_until: float = 0.0
    gone: bool = False  # no longer in DNS; dropped when idle
    last_grant: int = -1  # the dispatch number of its latest call (round-robin among equals)
    wake: asyncio.TimerHandle | None = None  # fires when its cooldown ends


@dataclass(eq=False)
class _Waiter:
    ticket: Ticket
    number: int
    future: asyncio.Future[Replica] = field(repr=False)


class Lease:
    """A slot on one replica. Release it exactly as often as you like (the first counts)."""

    __slots__ = ("_pool", "alive", "replica")

    def __init__(self, pool: ReplicaPool, replica: Replica) -> None:
        self._pool: ReplicaPool | None = pool
        self.replica = replica
        # What the call showed about the replica: True an answer came, False the connection was
        # refused or broke, None nothing (a timeout says the replica is slow, not that it is down).
        self.alive: bool | None = None

    @property
    def url(self) -> str:
        return self.replica.base_url

    def release(self, alive: bool | None = None) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool._release(self.replica, self.alive if alive is None else alive)


class ReplicaPool:
    def __init__(
        self,
        service: str,
        url: str,
        per_replica: int,
        *,
        resolver: Resolver = system_resolver,
        clock: Clock = time.monotonic,
        refresh_seconds: float = 10.0,
        cooldown_seconds: float = 10.0,
        small_burst: int = 4,
    ) -> None:
        if per_replica < 1 or small_burst < 1:
            raise ValueError("per_replica and small_burst must be at least 1")
        self.service = service
        self._url = httpx.URL(url.rstrip("/"))
        self._host = self._url.host
        self._port = self._url.port or DEFAULT_PORTS.get(self._url.scheme, 80)
        self._per_replica = per_replica
        self._resolver = resolver
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._cooldown_seconds = cooldown_seconds
        self._small_burst = small_burst
        self._replicas: list[Replica] = []
        self._waiters: list[_Waiter] = []
        self._numbers = 0  # waiters and grants are numbered; the order is what fairness uses
        self._served: weakref.WeakKeyDictionary[Ticket, int] = weakref.WeakKeyDictionary()
        self._small_streak = 0
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._refreshes = 0
        self._last_refresh: float | None = None
        self._fixed = _is_fixed_host(self._host)
        if self._fixed:  # never looked up: the URL as given is the one replica
            self._replicas.append(self._new_replica(self._host))

    # --- what the caller may ask ---

    @property
    def addresses(self) -> list[str]:
        """The replicas in use (not those leaving), in the order they joined."""
        return [replica.address for replica in self._replicas if not replica.gone]

    @property
    def replica_count(self) -> int:
        return len(self.addresses)

    @property
    def in_flight(self) -> dict[str, int]:
        return {replica.address: replica.in_flight for replica in self._replicas}

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def has_healthy(self) -> bool:
        now = self._clock()
        return any(r.unhealthy_until <= now for r in self._replicas if not r.gone)

    async def capacity(self) -> int:
        """Calls this service takes at once: the slots of all replicas (at least one)."""
        await self._ensure_replicas()
        return max(1, self.replica_count * self._per_replica)

    async def aclose(self) -> None:
        """Stop the background lookup and the cooldown timers."""
        for replica in self._replicas:
            self._stop_wake(replica)
        task, self._refresh_task = self._refresh_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def acquire(self, ticket: Ticket) -> Lease:
        """Wait for a slot on a replica; see the module doc for who gets the next one."""
        await self._ensure_replicas()
        self._numbers += 1
        waiter = _Waiter(ticket, self._numbers, asyncio.get_running_loop().create_future())
        self._waiters.append(waiter)
        self._dispatch()
        try:
            replica = await waiter.future
        except BaseException:
            if waiter.future.done() and not waiter.future.cancelled():
                # The slot was granted at the moment we were cancelled: give it back.
                self._release(waiter.future.result(), None)
            elif waiter in self._waiters:
                self._waiters.remove(waiter)
            raise
        return Lease(self, replica)

    # --- discovery ---

    async def _ensure_replicas(self) -> None:
        """Before a call: wait for the first lookup, since nothing is known without it; after that
        never wait for DNS, only start a lookup in the background when one is due."""
        if self._fixed:
            return
        if self._last_refresh is None:
            await self.refresh()
        elif self._refresh_due():
            task = self._refresh_task
            if task is None or task.done():
                self._refresh_task = asyncio.get_running_loop().create_task(
                    self._refresh_quietly(), name=f"{self.service}-replicas"
                )

    async def _refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except Exception:
            log.exception("%s: looking up the replicas failed", self.service)

    async def refresh(self, *, force: bool = False) -> None:
        """Look the name up again when the last lookup is older than `refresh_seconds`, or
        always when `force`. Callers that arrive while a lookup runs share its result."""
        if self._fixed or (not force and not self._refresh_due()):
            return
        seen = self._refreshes
        async with self._refresh_lock:
            if self._refreshes != seen or (not force and not self._refresh_due()):
                return
            try:
                addresses = await self._resolver(self._host, self._port)
            except (OSError, TimeoutError) as error:
                log.warning("%s: could not resolve %s: %s", self.service, self._host, error)
                addresses = []
            self._last_refresh = self._clock()
            self._refreshes += 1
            self._apply(addresses)

    def _refresh_due(self) -> bool:
        if self._last_refresh is None:
            return True
        return self._clock() - self._last_refresh >= self._refresh_seconds

    def _apply(self, addresses: Sequence[str]) -> None:
        wanted = list(dict.fromkeys(addresses))
        if not wanted:
            # Nothing resolved: keep what we know; with nothing known, the name itself is the
            # one replica (the caller's own connect handling then reports it as unreachable).
            if not self._replicas:
                self._replicas.append(self._new_replica(self._host))
            return
        known = {replica.address for replica in self._replicas}
        for replica in self._replicas:
            gone = replica.address not in wanted
            if gone and not replica.gone:
                log.info("%s: replica %s left DNS", self.service, replica.address)
            replica.gone = gone
        for address in wanted:
            if address not in known:
                log.info("%s: replica %s joined", self.service, address)
                self._replicas.append(self._new_replica(address))
        for replica in self._replicas:
            if replica.gone and replica.in_flight == 0:
                self._stop_wake(replica)
        self._replicas = [r for r in self._replicas if not (r.gone and r.in_flight == 0)]
        self._dispatch()

    def _new_replica(self, address: str) -> Replica:
        url = self._url if address == self._host else self._url.copy_with(host=address)
        return Replica(address=address, base_url=str(url).rstrip("/"))

    # --- routing ---

    def _release(self, replica: Replica, alive: bool | None) -> None:
        replica.in_flight -= 1
        if alive is True:
            replica.unhealthy_until = 0.0
        elif alive is False:
            replica.unhealthy_until = self._clock() + self._cooldown_seconds
            log.warning(
                "%s: replica %s failed (refused or lost the connection); skipped for %.0f s",
                self.service,
                replica.address,
                self._cooldown_seconds,
            )
            self._schedule_wake(replica)
        if replica.gone and replica.in_flight == 0:
            self._stop_wake(replica)
            self._replicas.remove(replica)
        self._dispatch()

    def _schedule_wake(self, replica: Replica) -> None:
        """Dispatch again when the replica's cooldown ends: a call that waits because every other
        replica is busy would otherwise wait for one of those calls to finish (ocsr: up to 55 s)
        although this replica is free again."""
        self._stop_wake(replica)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # released after the loop stopped: nothing is waiting any more
            return
        delay = max(0.0, replica.unhealthy_until - self._clock())
        replica.wake = loop.call_later(delay, self._woken, replica)

    def _woken(self, replica: Replica) -> None:
        replica.wake = None
        if replica.unhealthy_until > self._clock():  # a hair early (timer granularity)
            self._schedule_wake(replica)
        else:
            self._dispatch()

    @staticmethod
    def _stop_wake(replica: Replica) -> None:
        if replica.wake is not None:
            replica.wake.cancel()
            replica.wake = None

    def _free_replicas(self) -> list[Replica]:
        live = [r for r in self._replicas if not r.gone]
        now = self._clock()
        healthy = [r for r in live if r.unhealthy_until <= now]
        # A dead replica is skipped while any other is healthy (full or not); when none is, all
        # are tried, so that the caller's retry rules decide how long to wait.
        return [r for r in healthy or live if r.in_flight < self._per_replica]

    def _dispatch(self) -> None:
        while self._waiters:
            free = self._free_replicas()
            if not free:
                return
            waiter = self._choose()
            if waiter is None:
                return
            replica = min(free, key=lambda r: (r.in_flight, r.last_grant))
            self._waiters.remove(waiter)
            self._numbers += 1
            replica.in_flight += 1
            replica.last_grant = self._numbers
            self._served[waiter.ticket] = self._numbers
            waiter.future.set_result(replica)

    def _choose(self) -> _Waiter | None:
        waiting = [w for w in self._waiters if not w.future.done()]
        small = [w for w in waiting if w.ticket.small]
        large = [w for w in waiting if not w.ticket.small]
        if small and (not large or self._small_streak < self._small_burst):
            self._small_streak = self._small_streak + 1 if large else 0
            return min(small, key=self._turn)
        self._small_streak = 0
        return min(large, key=self._turn) if large else None

    def _turn(self, waiter: _Waiter) -> tuple[int, int]:
        """Jobs take turns: the one served longest ago first; within a job, in order of arrival."""
        return self._served.get(waiter.ticket, -1), waiter.number
