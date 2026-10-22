"""The enqueue seam. The API only knows `Enqueue`; production plugs in arq."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings

Enqueue = Callable[[str], Awaitable[None]]

# The arq function the worker registers. It receives the job id and nothing else:
# everything it needs is in the job record and under DATA_DIR/<job id>/.
PROCESS_JOB = "process_job"


class ArqEnqueuer:
    """Enqueues `process_job(job_id)` on arq, using the job id as the arq job id."""

    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()

    async def _get_pool(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(self._settings)
            return self._pool

    async def __call__(self, job_id: str) -> None:
        pool = await self._get_pool()
        queued = await pool.enqueue_job(PROCESS_JOB, job_id, _job_id=job_id)
        if queued is None:
            raise RuntimeError(f"arq refused job {job_id}: id already queued")

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
