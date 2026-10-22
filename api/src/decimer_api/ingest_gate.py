"""Who may ingest now: a few uploads at once overall, and one at a time per client.

The per-client limit is what keeps one address from using every slot. A client may have
`MAX_ACTIVE_JOBS_PER_IP` jobs, and that number can equal the number of ingest slots; without
this, two uploads from one address could hold both slots for as long as the files take, while
every other client waits. The client's second upload waits for its own first (not for a free
slot), so the other slots stay available to others.

This is per API process. With several API processes behind one proxy, each would run its own
gate, so a client could use one slot per process; a Redis counter per client hash (INCR with an
expiry, released in a `finally`) would make the limit global. One process is the plan,
so it is not built.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


class IngestBusy(Exception):  # noqa: N818  (not an error: the caller answers 503 and moves on)
    """No ingest turn was available within the wait budget."""


@dataclass
class _Client:
    turn: asyncio.Semaphore  # one at a time
    users: int = 0  # uploads of this client waiting or running; the entry goes when it is 0


class IngestGate:
    def __init__(self, concurrency: int) -> None:
        self._slots = asyncio.Semaphore(concurrency)
        self._clients: dict[str, _Client] = {}

    @property
    def tracked_clients(self) -> int:
        """Clients with an upload waiting or running (for tests: the table must not grow)."""
        return len(self._clients)

    @asynccontextmanager
    async def slot(self, client: str, wait: float) -> AsyncIterator[None]:
        """Hold the client's turn and one overall slot. Raises `IngestBusy` when both are not
        obtained within `wait` seconds, having given back anything it had taken."""
        entry = self._clients.setdefault(client, _Client(turn=asyncio.Semaphore(1)))
        entry.users += 1
        has_turn = has_slot = False
        try:
            try:
                async with asyncio.timeout(wait):
                    await entry.turn.acquire()
                    has_turn = True
                    await self._slots.acquire()
                    has_slot = True
            except TimeoutError as error:
                raise IngestBusy from error
            yield
        finally:
            if has_slot:
                self._slots.release()
            if has_turn:
                entry.turn.release()
            entry.users -= 1
            if entry.users == 0:
                del self._clients[client]
