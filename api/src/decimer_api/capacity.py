"""A fixed number of places for work that is costly in memory or file handles, with no queue.

Some requests cost more than their rate limit can see: reading a molfile with RDKit is CPU-bound
C++ that holds the GIL and can take hundreds of megabytes, and a ZIP download holds a file open
per crop for as long as the client reads. A queue would only turn "too much at once" into "all of
it, later, all waiting". So a `Capacity` has a few places, and a request that finds none is refused
at once with 503 `busy` and `Retry-After`: the caller can ask again in a moment, and the process
never does more than its places at the same time.

`ClientShares` is the same idea per client: at most so many places for one client at a time, so
that one address cannot take all the places there are. One api process (the compose file runs
exactly one), so the counters live in the process.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

from .errors import ApiError, ErrorCode


class Place:
    """One taken place; `release` gives it back (any number of times, once counts)."""

    def __init__(self, capacity: Capacity) -> None:
        self._capacity: Capacity | None = capacity

    def release(self) -> None:
        capacity, self._capacity = self._capacity, None
        if capacity is not None:
            capacity._free += 1  # noqa: SLF001


class Capacity:
    def __init__(self, places: int, *, retry_after_seconds: int = 2) -> None:
        if places < 1:
            raise ValueError("a capacity needs at least one place")
        self.places = places
        self._free = places
        self._retry_after = retry_after_seconds

    @property
    def in_use(self) -> int:
        return self.places - self._free

    def take(self) -> Place:
        """A place, or `ApiError` 503 `busy`. No awaiting between the check and the taking, so two
        requests can never both get the last place."""
        if self._free < 1:
            raise ApiError(
                503,
                ErrorCode.BUSY,
                "The service is busy with other requests. Please try again in a moment.",
                headers={"Retry-After": str(self._retry_after)},
            )
        self._free -= 1
        return Place(self)

    @contextlib.asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        place = self.take()
        try:
            yield
        finally:
            place.release()

    async def run_in_thread[T](self, function: Callable[..., T], *args: Any) -> T:
        """`function(*args)` in a thread, holding a place until the thread is really done. If the
        caller is cancelled meanwhile (the server stops) the thread cannot be, so the place stays
        taken until it ends: the number of threads running never exceeds the places."""
        place = self.take()
        task = asyncio.ensure_future(asyncio.to_thread(function, *args))

        def finished(done: asyncio.Future[T]) -> None:
            place.release()
            if not done.cancelled():
                done.exception()  # seen, so a cancelled caller leaves no "never retrieved" noise

        task.add_done_callback(finished)
        return await asyncio.shield(task)


class Share:
    """One place of one client; `release` gives it back (any number of times, once counts)."""

    def __init__(self, shares: ClientShares, client: str) -> None:
        self._shares: ClientShares | None = shares
        self._client = client

    def release(self) -> None:
        shares, self._shares = self._shares, None
        if shares is not None:
            shares._give_back(self._client)  # noqa: SLF001


class ClientShares:
    """At most `limit` places in use per client; the next one is 429 with `Retry-After`. A client
    with nothing in use has no entry, so the table cannot grow with the clients that came."""

    def __init__(
        self, limit: int, *, retry_after_seconds: int = 5, what: str = "downloads"
    ) -> None:
        if limit < 1:
            raise ValueError("a client needs at least one place")
        self.limit = limit
        self._retry_after = retry_after_seconds
        self._what = what
        self._in_use: dict[str, int] = {}

    @property
    def clients(self) -> int:
        """Clients with something in use (for the tests: the table must not grow)."""
        return len(self._in_use)

    def take(self, client: str) -> Share:
        used = self._in_use.get(client, 0)
        if used >= self.limit:
            raise ApiError(
                429,
                ErrorCode.RATE_LIMITED,
                f"You already have {self.limit} {self._what} in progress. "
                "Please wait for one to finish.",
                headers={"Retry-After": str(self._retry_after)},
            )
        self._in_use[client] = used + 1
        return Share(self, client)

    def _give_back(self, client: str) -> None:
        remaining = self._in_use.get(client, 1) - 1
        if remaining > 0:
            self._in_use[client] = remaining
        else:
            self._in_use.pop(client, None)
