"""The arq worker: `python -m decimer_api.worker`.

arq calls `process_job(ctx, job_id)` for every job the API enqueued (`decimer_api.queue`). The
function returns `None`, so arq keeps no result in Redis: the job record is the result.
`startup` builds the pieces once per worker process (the Redis client, the shared HTTP client,
the per-service concurrency limits); `shutdown` closes them.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import arq
from arq.connections import RedisSettings
from arq.worker import run_worker
from redis.asyncio import Redis

from ..hardening import make_undumpable
from ..queue import PROCESS_JOB
from ..settings import Settings
from ..store import JobStore
from .pipeline import Deps, run_pipeline
from .runtime import build_deps
from .settings import ARQ_TIMEOUT_MARGIN_SECONDS, WorkerSettings

log = logging.getLogger("decimer_api.worker")

DEPS_KEY = "deps"
REDIS_KEY = "redis"
# Runs that arq may start for one job: the first, and one more after each cancellation (a
# restart of the worker). Past that the job is dropped and its record expires by itself.
MAX_TRIES = 3
# A run cancelled after this share of arq's job timeout was cut by that timeout, not stopped by
# a restart (the pipeline's own, shorter deadline never leaves a run for arq to cut).
TIMEOUT_CANCEL_SHARE = 0.95

Hook = Callable[[dict[str, Any]], Awaitable[None]]


async def process_job(ctx: dict[str, Any], job_id: str) -> None:
    deps: Deps = ctx[DEPS_KEY]
    limit = deps.worker.pipeline_timeout_seconds + ARQ_TIMEOUT_MARGIN_SECONDS
    started = time.monotonic()
    try:
        await run_pipeline(job_id, deps)
    except asyncio.CancelledError:
        elapsed = time.monotonic() - started
        if elapsed >= TIMEOUT_CANCEL_SHARE * limit:
            # The pipeline's own deadline comes first and tells the user. This one does not: the
            # job was cancelled without a word and its record is left running until it expires.
            log.error(
                "job %s: arq's job_timeout (%.0f s) cancelled the run after %.0f s; "
                "the record is left running and will expire on its own",
                job_id,
                limit,
                elapsed,
            )
        raise


def make_startup(settings: Settings, worker: WorkerSettings) -> Hook:
    async def startup(ctx: dict[str, Any]) -> None:
        redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
        ctx[REDIS_KEY] = redis
        ctx[DEPS_KEY] = build_deps(settings, worker, JobStore(redis, settings))
        log.info(
            "worker ready: seg=%s ocsr=%s cls=%s chem=%s",
            worker.seg_url,
            worker.ocsr_url,
            worker.cls_url,
            worker.chem_url or "off",
        )

    return startup


async def shutdown(ctx: dict[str, Any]) -> None:
    deps: Deps | None = ctx.pop(DEPS_KEY, None)
    if deps is not None:
        await deps.aclose()
    redis: Redis | None = ctx.pop(REDIS_KEY, None)
    if redis is not None:
        await redis.aclose()


def build_arq_settings(
    settings: Settings | None = None,
    worker: WorkerSettings | None = None,
    *,
    startup: Hook | None = None,
) -> dict[str, Any]:
    """The keyword arguments of `arq.Worker`, as a dict (arq accepts that in place of a class)."""
    settings = settings or Settings()
    worker = worker or WorkerSettings()
    worker.check_against(settings)
    return {
        "functions": [arq.func(process_job, name=PROCESS_JOB, max_tries=MAX_TRIES)],
        "redis_settings": RedisSettings.from_dsn(settings.redis_url),
        "on_startup": startup or make_startup(settings, worker),
        "on_shutdown": shutdown,
        "max_jobs": worker.worker_max_jobs,
        "job_timeout": worker.pipeline_timeout_seconds + ARQ_TIMEOUT_MARGIN_SECONDS,
        "max_tries": MAX_TRIES,
        "keep_result": 0,  # nothing is kept: the job record is the result
        "retry_jobs": True,  # a job cancelled by a restart runs again
        "job_completion_wait": worker.shutdown_wait_seconds,
        "health_check_interval": 60,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # The render children run as the same user: without this they could read this process's
    # environment (REDIS_URL, ...) and memory through /proc. The API does the same at start.
    make_undumpable()
    run_worker(build_arq_settings())
