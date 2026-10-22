"""The fast lane: which jobs are small, which queue a job goes on, what happens
when no fast worker is alive, and how a job stranded on the fast queue is rescued."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis
import httpx
import pytest

from decimer_api.app import create_app
from decimer_api.ingest_runner import InProcessIngest
from decimer_api.queue import (
    PROCESS_JOB,
    ArqEnqueuer,
    health_key,
    requeue_stranded,
    watch_fast_lane,
)
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from decimer_api.uploads import Limits
from tests.conftest import FakeEnqueue
from tests.factories import make_pdf, make_png, multipart

FAST = "decimer:fast"
DEFAULT = "arq:queue"


# --- which jobs are small ----------------------------------------------------------------------


def test_a_job_is_small_up_to_the_page_limit_for_small_jobs() -> None:
    settings = Settings()
    assert settings.small_job_max_pages == 1  # a pasted image, one upload, one page
    assert settings.is_small_job(1) and not settings.is_small_job(2)
    assert Settings(small_job_max_pages=3).is_small_job(3)
    assert not Settings(small_job_max_pages=0).is_small_job(1)  # 0: no job is small


def test_the_queue_names_have_defaults_and_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings()
    assert (settings.worker_queue_name, settings.worker_fast_queue_name) == (DEFAULT, FAST)
    monkeypatch.setenv("WORKER_QUEUE_NAME", "jobs:main")
    monkeypatch.setenv("WORKER_FAST_QUEUE_NAME", "jobs:fast")
    monkeypatch.setenv("SMALL_JOB_MAX_PAGES", "2")
    settings = Settings()
    assert (settings.worker_queue_name, settings.worker_fast_queue_name) == (
        "jobs:main",
        "jobs:fast",
    )
    assert settings.small_job_max_pages == 2


@pytest.mark.parametrize("name", ["", " ", "a b", " arq", "arq\n"])
def test_a_queue_name_must_be_a_plain_key(name: str) -> None:
    with pytest.raises(ValueError, match="queue name"):
        Settings(worker_queue_name=name)
    with pytest.raises(ValueError, match="queue name"):
        Settings(worker_fast_queue_name=name)


@pytest.mark.parametrize(
    ("uploads", "small"),
    [
        ([("pasted.png", make_png())], True),  # a pasted image is one part
        ([("a.png", make_png()), ("b.png", make_png())], False),  # two images
        ([("one.pdf", make_pdf(1))], True),  # a PDF of one page
        ([("three.pdf", make_pdf(3))], False),
    ],
    ids=["one image", "two images", "one-page pdf", "three-page pdf"],
)
async def test_the_api_tells_the_queue_whether_a_job_is_small(
    client: httpx.AsyncClient, enqueue: FakeEnqueue, uploads: list[tuple[str, bytes]], small: bool
) -> None:
    body, headers = multipart(uploads)
    response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == 202
    assert enqueue.small == {response.json()["id"]: small}


async def test_the_page_limit_for_small_jobs_is_the_settings(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue
) -> None:
    app = create_app(
        settings.model_copy(update={"small_job_max_pages": 3}),
        store,
        enqueue,
        ingest_runner=InProcessIngest(Limits.from_settings(settings)),
    )
    body, headers = multipart([("three.pdf", make_pdf(3))])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.7", 1)), base_url="http://api"
    ) as client:
        created = await client.post("/api/jobs", content=body, headers=headers)
    assert enqueue.small == {created.json()["id"]: True}


# --- the queue a job goes on -------------------------------------------------------------------


class StubPool:
    """What `ArqEnqueuer` needs of an arq pool: `enqueue_job` and `exists`."""

    def __init__(self) -> None:
        self.alive: set[str] = set()
        self.enqueued: list[tuple[str, str]] = []  # (job id, queue name)
        self.checks = 0
        self.error: Exception | None = None

    async def exists(self, key: str) -> int:
        self.checks += 1
        if self.error is not None:
            raise self.error
        return int(key in self.alive)

    async def enqueue_job(self, function: str, job_id: str, **kwargs: Any) -> object:
        assert function == PROCESS_JOB and kwargs["_job_id"] == job_id
        self.enqueued.append((job_id, kwargs["_queue_name"]))
        return object()

    async def aclose(self) -> None:
        pass


class Ticker:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def enqueuer(pool: StubPool, clock: Ticker | None = None, **kwargs: Any) -> ArqEnqueuer:
    result = ArqEnqueuer(
        "redis://unused:6379/0",
        queue_name=DEFAULT,
        fast_queue_name=FAST,
        clock=clock or Ticker(),
        **kwargs,
    )
    result._pool = pool  # type: ignore[assignment]
    return result


async def test_a_small_job_goes_on_the_fast_queue_while_a_fast_worker_is_alive() -> None:
    pool = StubPool()
    pool.alive.add(health_key(FAST))
    queue = enqueuer(pool)
    await queue("a" * 32, small=True)
    await queue("b" * 32)
    await queue("c" * 32, small=False)
    assert pool.enqueued == [("a" * 32, FAST), ("b" * 32, DEFAULT), ("c" * 32, DEFAULT)]


async def test_without_a_fast_worker_a_small_job_goes_on_the_default_queue() -> None:
    """`worker-fast` is a Compose profile and may not be running: nobody would ever take the job
    from a queue nobody serves, so the api does not use one."""
    pool = StubPool()
    queue = enqueuer(pool)
    await queue("a" * 32, small=True)
    assert pool.enqueued == [("a" * 32, DEFAULT)]


async def test_the_health_key_is_the_one_arq_keeps_for_the_queue() -> None:
    assert health_key("arq:queue") == "arq:queue:health-check"
    assert health_key(FAST) == "decimer:fast:health-check"


async def test_the_answer_is_kept_for_a_few_seconds_and_then_asked_again() -> None:
    pool, clock = StubPool(), Ticker()
    queue = enqueuer(pool, clock, fast_lane_check_seconds=5)
    await queue("a" * 32, small=True)
    pool.alive.add(health_key(FAST))  # a fast worker starts
    clock.now += 4.9
    await queue("b" * 32, small=True)
    assert pool.checks == 1 and pool.enqueued[1][1] == DEFAULT  # not asked yet
    clock.now += 0.2
    await queue("c" * 32, small=True)
    assert pool.checks == 2 and pool.enqueued[2][1] == FAST
    pool.alive.clear()  # and it stops (arq deletes its key when it shuts down)
    clock.now += 5
    await queue("d" * 32, small=True)
    assert pool.enqueued[3][1] == DEFAULT


async def test_large_jobs_never_ask_about_the_fast_worker() -> None:
    pool = StubPool()
    await enqueuer(pool)("a" * 32)
    assert pool.checks == 0


async def test_when_redis_cannot_answer_the_question_the_default_queue_is_used() -> None:
    pool = StubPool()
    pool.error = ConnectionError("redis went away")
    await enqueuer(pool)("a" * 32, small=True)
    assert pool.enqueued == [("a" * 32, DEFAULT)]


async def test_with_one_name_for_both_there_is_one_queue_and_nothing_to_ask() -> None:
    pool = StubPool()
    queue = ArqEnqueuer("redis://unused:6379/0", queue_name=DEFAULT, fast_queue_name=DEFAULT)
    queue._pool = pool  # type: ignore[assignment]
    await queue("a" * 32, small=True)
    assert pool.enqueued == [("a" * 32, DEFAULT)] and pool.checks == 0


# --- a job stranded on the fast queue ----------------------------------------------------------


async def test_jobs_on_the_fast_queue_move_to_the_default_queue_when_no_fast_worker_is_alive(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.zadd(FAST, {"job1": 1000, "job2": 2000})
    await redis.zadd(DEFAULT, {"job0": 500})
    assert await requeue_stranded(redis, FAST, DEFAULT) == 2
    assert await redis.zrange(FAST, 0, -1) == []
    assert await redis.zrange(DEFAULT, 0, -1, withscores=True) == [
        ("job0", 500.0),
        ("job1", 1000.0),  # the score (the time it was queued) is kept: it keeps its turn
        ("job2", 2000.0),
    ]


async def test_jobs_are_left_alone_while_a_fast_worker_is_alive(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await redis.zadd(FAST, {"job1": 1000})
    await redis.set(health_key(FAST), "alive")
    assert await requeue_stranded(redis, FAST, DEFAULT) == 0
    assert await redis.zrange(FAST, 0, -1) == ["job1"]
    assert await redis.zcard(DEFAULT) == 0


async def test_a_job_already_on_the_default_queue_keeps_its_place_there(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    """NX: a job that is on both queues (a second worker moved it first, or arq queued it twice)
    stays once on the default queue, with the score it already had."""
    await redis.zadd(FAST, {"job1": 1000})
    await redis.zadd(DEFAULT, {"job1": 500})
    await requeue_stranded(redis, FAST, DEFAULT)
    assert await redis.zrange(DEFAULT, 0, -1, withscores=True) == [("job1", 500.0)]
    assert await redis.zcard(FAST) == 0


async def test_a_job_taken_off_the_fast_queue_meanwhile_is_not_put_on_the_default_one(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The jobs are listed first and moved one by one. A fast worker that is starting (or a second
    default worker) may take a job in between: it is not added to the default queue after that,
    and it is not counted as moved."""
    await redis.zadd(FAST, {"job1": 1000, "job2": 2000})
    listing = redis.zrange

    async def zrange_then_race(*args: Any, **kwargs: Any) -> Any:
        listed = await listing(*args, **kwargs)
        await redis.zrem(FAST, "job1")  # the fast worker claims it right after the listing
        return listed

    monkeypatch.setattr(redis, "zrange", zrange_then_race)
    assert await requeue_stranded(redis, FAST, DEFAULT) == 1
    assert await redis.zrange(DEFAULT, 0, -1, withscores=True) == [("job2", 2000.0)]
    assert await redis.zcard(FAST) == 0


async def test_an_empty_fast_queue_or_one_queue_for_both_is_nothing_to_do(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    assert await requeue_stranded(redis, FAST, DEFAULT) == 0
    await redis.zadd(DEFAULT, {"job1": 1})
    assert await requeue_stranded(redis, DEFAULT, DEFAULT) == 0
    assert await redis.zrange(DEFAULT, 0, -1) == ["job1"]


async def test_the_watcher_rescues_stranded_jobs_and_survives_errors(
    redis: fakeredis.FakeAsyncRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    await redis.zadd(FAST, {"job1": 1000})
    real = redis.zcard
    failures = {"left": 1}

    async def flaky(name: str) -> int:
        if name == FAST and failures["left"]:
            failures["left"] -= 1
            raise ConnectionError("redis blinked")
        return int(await real(name))

    monkeypatch.setattr(redis, "zcard", flaky)
    watcher = asyncio.create_task(watch_fast_lane(redis, FAST, DEFAULT, 0.01))
    try:
        for _ in range(200):
            if await redis.zcard(DEFAULT):
                break
            await asyncio.sleep(0.01)
    finally:
        watcher.cancel()
    assert await redis.zrange(DEFAULT, 0, -1) == ["job1"]  # after the first look failed
    assert failures["left"] == 0
