"""The worker counts a finished job once (`decimer_api.usage`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import fakeredis
import pytest

from decimer_api.models import JobStatus
from decimer_api.usage import RedisUsage, day_key
from decimer_api.worker.pipeline import run_pipeline
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend

DAY = datetime(2026, 10, 23, 14, 30, tzinfo=UTC)


@pytest.fixture
def usage_redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def usage(usage_redis: fakeredis.FakeAsyncRedis) -> RedisUsage:
    return RedisUsage(usage_redis, clock=lambda: DAY)


async def counts(redis: fakeredis.FakeAsyncRedis) -> dict[str, int]:
    return {name: int(value) for name, value in (await redis.hgetall(day_key(DAY.date()))).items()}


async def test_a_finished_job_is_counted_once_by_the_worker(
    make_rig: Callable[..., Rig], usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2, 1: 1}))
    rig.deps.usage = usage
    job = await rig.run(await rig.pdf_job(2))
    assert job.status is JobStatus.DONE
    assert await counts(usage_redis) == {"done": 1, "pages": 2, "structures": 3}


async def test_a_failed_job_is_counted_as_failed(
    make_rig: Callable[..., Rig], usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}))
    rig.deps.usage = usage
    broken = await rig.pdf_job(1, data=b"%PDF-1.4 not a pdf")
    job = await rig.run(broken)
    assert job.status is JobStatus.FAILED
    assert await counts(usage_redis) == {"failed": 1}


async def test_a_job_the_worker_never_finishes_is_not_counted(
    make_rig: Callable[..., Rig], usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    rig = make_rig()
    rig.deps.usage = usage
    await run_pipeline("f" * 32, rig.deps)  # no such job: nothing to do
    assert await counts(usage_redis) == {}
