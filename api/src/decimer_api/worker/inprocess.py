"""Development mode: the API and the pipeline in one process, no arq worker.

    cd api
    DATA_DIR=/tmp/decimer-jobs REDIS_URL=redis://localhost:6379/0 MEDIA_STREAM_DIRECT=true \
    SEG_URL=http://127.0.0.1:8001 OCSR_URL=http://127.0.0.1:8002 CLS_URL=http://127.0.0.1:8003 \
      uv run python -m decimer_api.worker.inprocess

It is the production pipeline (`run_pipeline`) started from the API's enqueue seam instead of
from arq, so a job takes the same path. A job that is waiting for one of the `WORKER_MAX_JOBS`
places stays queued (and shows its queue position), small or not: there are no lanes here, only
the fair dispatching of the calls to the model services. Jobs do not survive a restart of this
process, which is the difference that matters: use the arq worker anywhere else.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn
from fastapi import FastAPI
from redis.asyncio import Redis

from ..app import create_app
from ..hardening import make_undumpable
from ..settings import Settings
from ..store import JobStore
from .pipeline import Deps, run_pipeline
from .runtime import build_deps
from .settings import WorkerSettings

log = logging.getLogger("decimer_api.worker")


class InProcessEnqueuer:
    """An `Enqueue` (see `decimer_api.queue`) that runs the job in this process."""

    def __init__(self, deps: Deps, max_jobs: int | None = None) -> None:
        self._deps = deps
        self._slots = asyncio.Semaphore(max_jobs or deps.worker.worker_max_jobs)
        self._tasks: set[asyncio.Task[None]] = set()

    async def __call__(self, job_id: str, *, small: bool = False) -> None:
        # There is one process and no queues here, so the size class only matters inside the
        # pipeline, where it decides whose calls to the replicas go first.
        task = asyncio.create_task(self._run(job_id), name=f"job-{job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str) -> None:
        async with self._slots:
            try:
                await run_pipeline(job_id, self._deps)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("job %s: the pipeline raised", job_id)

    async def aclose(self) -> None:
        """Cancel the jobs still running (they are left running, as when arq is stopped)."""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError):
                await task


def create_dev_app() -> FastAPI:
    # As the API does at start (`create_app_from_env`): the render children run as this user.
    make_undumpable()
    settings = Settings()
    worker = WorkerSettings()
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    store = JobStore(redis, settings)
    deps = build_deps(settings, worker, store)
    enqueuer = InProcessEnqueuer(deps)

    async def shutdown() -> None:
        await enqueuer.aclose()
        await deps.aclose()
        await redis.aclose()

    return create_app(settings, store, enqueuer, on_shutdown=shutdown)


def uvicorn_config(settings: Settings) -> uvicorn.Config:
    """The same server settings as `python -m decimer_api` (see its comments), other app."""
    return uvicorn.Config(
        "decimer_api.worker.inprocess:create_dev_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        proxy_headers=False,
        server_header=False,
        access_log=False,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.Server(uvicorn_config(Settings())).run()


if __name__ == "__main__":
    main()
