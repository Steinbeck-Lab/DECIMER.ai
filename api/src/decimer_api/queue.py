"""The enqueue seam. The API only knows `Enqueue`; production plugs in arq.

Two lanes. A small job (a pasted image, one page: `Settings.is_small_job`) goes
on the fast queue, every other job on the default queue, so that a small job does not wait for a
worker place behind a 10-page PDF. The fast queue is served by its own worker (`worker-fast`),
which may not be running (stopped, starting, a development setup), and arq cannot tell a queue
nobody serves from a slow one. So the api sends
a job to the fast queue only while a fast worker is alive: arq's worker sets the key
`<queue name>:health-check` every minute, with a lifetime a second longer, and deletes it when
it stops. No fast worker, no key, and the small job goes on the default queue, where the default
worker takes it (and its calls to the model services still go first, see `worker/replicas.py`).
A job that was sent to the fast queue and whose worker has died since is moved to the default
queue by the default worker (`requeue_stranded`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from arq.constants import default_queue_name, health_check_key_suffix
from redis.asyncio import Redis

log = logging.getLogger("decimer_api")


class Enqueue(Protocol):
    """Queue a job. `small` is its size class (`Settings.is_small_job`)."""

    def __call__(self, job_id: str, *, small: bool = False) -> Awaitable[None]: ...


# The arq function the worker registers. It receives the job id and nothing else:
# everything it needs is in the job record and under DATA_DIR/<job id>/.
PROCESS_JOB = "process_job"


def health_key(queue_name: str) -> str:
    """The key arq's worker of `queue_name` keeps alive while it runs."""
    return queue_name + health_check_key_suffix


class ArqEnqueuer:
    """Enqueues `process_job(job_id)` on arq, using the job id as the arq job id."""

    def __init__(
        self,
        redis_url: str,
        *,
        queue_name: str = default_queue_name,
        fast_queue_name: str | None = None,
        fast_lane_check_seconds: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._queue_name = queue_name
        # The same name for both: one queue, nothing to choose.
        self._fast_queue_name = fast_queue_name if fast_queue_name != queue_name else None
        self._check_seconds = fast_lane_check_seconds
        self._clock = clock
        self._pool: ArqRedis | None = None
        self._lock = asyncio.Lock()
        self._fast_alive = False
        self._checked_at: float | None = None

    async def _get_pool(self) -> ArqRedis:
        async with self._lock:
            if self._pool is None:
                self._pool = await create_pool(self._settings)
            return self._pool

    async def _queue_for(self, pool: ArqRedis, small: bool) -> str:
        if not small or self._fast_queue_name is None:
            return self._queue_name
        now = self._clock()
        if self._checked_at is None or now - self._checked_at >= self._check_seconds:
            try:
                self._fast_alive = bool(await pool.exists(health_key(self._fast_queue_name)))
            except Exception:
                log.warning("could not check for a fast worker; using the default queue")
                self._fast_alive = False
            self._checked_at = now
        return self._fast_queue_name if self._fast_alive else self._queue_name

    async def __call__(self, job_id: str, *, small: bool = False) -> None:
        pool = await self._get_pool()
        queue = await self._queue_for(pool, small)
        queued = await pool.enqueue_job(PROCESS_JOB, job_id, _job_id=job_id, _queue_name=queue)
        if queued is None:
            raise RuntimeError(f"arq refused job {job_id}: id already queued")

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None


# Move one job from the fast queue to the default one, but only if it is still on the fast queue:
# a fast worker that is starting, or a second default worker, may have taken it since the list was
# read, and a job taken is not put back. One script, so no other client sees the job in neither
# queue, or in both. NX: a job that is already on the default queue keeps its place there.
MOVE_JOB = """
if redis.call('zrem', KEYS[1], ARGV[1]) == 1 then
    redis.call('zadd', KEYS[2], 'NX', ARGV[2], ARGV[1])
    return 1
end
return 0
"""


async def requeue_stranded(redis: Redis, fast_queue_name: str, queue_name: str) -> int:
    """Move the jobs waiting on the fast queue to the default queue when no fast worker is alive.
    Returns how many were moved. Safe to run from several workers at once and while a fast worker
    is starting: each move is one atomic step that happens only if the job is still on the fast
    queue, so a job is never in neither queue, and never runs twice on account of the move (arq's
    in-progress key is per job id, not per queue, and the pipeline skips a finished job).

    A job that was already *running* on the dead fast worker is still in the fast queue too (arq
    removes a job from its queue when it finishes), so it is moved as well; the default worker
    starts it only when arq's in-progress key has expired, `job_timeout + 10 s` after the run
    began (about 18 minutes with the defaults)."""
    if fast_queue_name == queue_name or not await redis.zcard(fast_queue_name):
        return 0
    if await redis.exists(health_key(fast_queue_name)):
        return 0
    moved = 0
    waiting: list[tuple[str, float]] = await redis.zrange(fast_queue_name, 0, -1, withscores=True)
    for job_id, score in waiting:
        # redis-py types `eval` for both the sync and the async client.
        answer = cast(
            "Awaitable[int]",
            redis.eval(MOVE_JOB, 2, fast_queue_name, queue_name, job_id, repr(score)),
        )
        moved += int(await answer)
    if moved:
        log.warning("no fast worker is alive: moved %d job(s) to %s", moved, queue_name)
    return moved


async def watch_fast_lane(
    redis: Redis, fast_queue_name: str, queue_name: str, interval_seconds: float
) -> None:
    """Run `requeue_stranded` every `interval_seconds` until cancelled; never raises."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await requeue_stranded(redis, fast_queue_name, queue_name)
        except Exception:
            log.warning("could not look for stranded fast-lane jobs", exc_info=True)
