"""Error reports from the worker: a job that crashes the pipeline, or escapes it,
reaches GlitchTip (here: a transport that keeps what would be sent) with no structure, file name or
address in it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import arq
import arq.worker
import fakeredis
import pytest
import sentry_sdk
from arq.connections import ArqRedis
from arq.worker import Worker
from fakeredis.aioredis import FakeAsyncRedisConnection
from redis.asyncio import ConnectionPool

from decimer_api.models import JobStatus
from decimer_api.observability import init_error_reporting
from decimer_api.queue import PROCESS_JOB
from decimer_api.settings import Settings
from decimer_api.worker import arq_app, pipeline
from tests.test_scrub import ASPIRIN, CAFFEINE, CLIENT_IP, FILENAME, Capture
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend

RigFactory = Callable[..., Rig]
QUEUE = "arq:queue"


@pytest.fixture
async def arq_redis(redis: fakeredis.FakeAsyncRedis) -> AsyncIterator[ArqRedis]:
    server = redis.connection_pool.connection_kwargs["server"]
    pool = ConnectionPool(connection_class=FakeAsyncRedisConnection, server=server)
    client = ArqRedis(connection_pool=pool)
    yield client
    await client.aclose()


@pytest.fixture
def reports() -> Capture:
    capture = Capture()
    assert init_error_reporting(
        "http://publickey@glitchtip:8000/1", service="worker", release="test", transport=capture
    )
    yield capture  # type: ignore[misc]
    sentry_sdk.get_client().close(timeout=1)
    sentry_sdk.init(dsn=None, default_integrations=False, auto_enabling_integrations=False)


async def run_one_job(rig: Rig, settings: Settings, arq_redis: ArqRedis, job_id: str) -> Worker:
    await arq_redis.enqueue_job(PROCESS_JOB, job_id, _job_id=job_id, _queue_name=QUEUE)

    async def start(ctx: dict[str, Any]) -> None:
        ctx[arq_app.DEPS_KEY] = rig.deps

    config = arq_app.build_arq_settings(settings, rig.worker, startup=start)
    # `create_worker` is what `run_worker` calls, and what the arq integration patches: looked up
    # now, on the module, because the patch is made when reporting starts.
    worker = arq.worker.create_worker(
        config, redis_pool=arq_redis, burst=True, poll_delay=0.01, handle_signals=False
    )
    await worker.async_run()
    await worker.close()
    return worker


def assert_clean(capture: Capture) -> None:
    payload = capture.payload()
    for secret in (ASPIRIN, CAFFEINE, CLIENT_IP, FILENAME):
        assert secret not in payload, secret
    for event in capture.events:
        assert "user" not in event and "extra" not in event and "breadcrumbs" not in event
        assert event["server_name"] == "worker"


async def test_a_crash_inside_the_pipeline_is_reported_scrubbed(
    make_rig: RigFactory,
    settings: Settings,
    arq_redis: ArqRedis,
    reports: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arq.worker, "log_redis_info", _no_redis_info)

    async def execute(self: Any) -> None:
        smiles = ASPIRIN  # a local variable: never sent with the frame
        raise ValueError(f"cannot check {smiles} for {CLIENT_IP} in '{FILENAME}'")

    monkeypatch.setattr(pipeline._Run, "execute", execute)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.pdf_job(1)
    await run_one_job(rig, settings, arq_redis, job.id)
    stored = await rig.store.get(job.id, include_expired=True)
    assert stored is not None and stored.status is JobStatus.FAILED  # the user got internal_error
    sentry_sdk.flush(timeout=2)
    assert reports.events, "the crash was not reported"
    payload = reports.payload()
    assert "ValueError" in payload and "execute" in payload
    assert_clean(reports)


async def test_an_exception_that_escapes_the_pipeline_is_reported_scrubbed(
    make_rig: RigFactory,
    settings: Settings,
    arq_redis: ArqRedis,
    reports: Capture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arq.worker, "log_redis_info", _no_redis_info)

    async def run_pipeline(job_id: str, deps: Any) -> None:
        raise RuntimeError(f"store is gone while reading {CAFFEINE} ({job_id}) at {CLIENT_IP}")

    monkeypatch.setattr(arq_app, "run_pipeline", run_pipeline)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.pdf_job(1)
    worker = await run_one_job(rig, settings, arq_redis, job.id)
    assert worker.jobs_failed == 1
    sentry_sdk.flush(timeout=2)
    payload = reports.payload()
    assert "RuntimeError" in payload and "process_job" in payload
    assert job.id not in payload  # arq tags the event with the job id: it is scrubbed to :id
    assert_clean(reports)


async def _no_redis_info(*_args: object, **_kwargs: object) -> None:
    """fakeredis has no INFO, which arq calls at start."""
