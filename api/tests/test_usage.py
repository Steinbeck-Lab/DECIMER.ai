"""The usage counter: per day, jobs by kind and size, pages, structures, failures, and
nothing that identifies a client or a job."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from decimer_api.analysis_pool import InProcessAnalysis
from decimer_api.app import create_app
from decimer_api.ingest_runner import InProcessIngest
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from decimer_api.uploads import Limits
from decimer_api.usage import (
    PASTE_NAME_PREFIX,
    NullUsage,
    RedisUsage,
    day_key,
    kind_of,
    main,
    read_days,
    table,
)
from tests.conftest import FakeClock, FakeEnqueue
from tests.factories import make_pdf, make_png, multipart
from tests.results import FakeNamer

DAY = datetime(2026, 10, 23, 14, 30, tzinfo=UTC)
JOB = "7a9f7a9f7a9f7a9f7a9f7a9f7a9f7a9f"


class Today:
    def __init__(self, now: datetime = DAY) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def usage_redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)


@pytest.fixture
def today() -> Today:
    return Today()


@pytest.fixture
def usage(usage_redis: fakeredis.FakeAsyncRedis, today: Today) -> RedisUsage:
    return RedisUsage(usage_redis, clock=today)


async def counts(redis: fakeredis.FakeAsyncRedis, day: date | None = None) -> dict[str, int]:
    day = day or DAY.date()
    return {name: int(value) for name, value in (await redis.hgetall(day_key(day))).items()}


# --- the counter -----------------------------------------------------------------------------


async def test_a_created_job_is_counted_by_kind_and_size(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    await usage.job_created("pdf", small=False)
    await usage.job_created("pdf", small=False)
    await usage.job_created("pdf", small=True)
    await usage.job_created("image", small=True)
    await usage.job_created("paste", small=True)
    assert await counts(usage_redis) == {
        "jobs.pdf.large": 2,
        "jobs.pdf.small": 1,
        "jobs.image.small": 1,
        "jobs.paste.small": 1,
    }


async def test_a_finished_job_counts_its_pages_and_structures_and_a_failed_one_only_itself(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    await usage.job_finished(failed=False, pages=10, structures=17)
    await usage.job_finished(failed=False, pages=1, structures=0)
    await usage.job_finished(failed=True, pages=4, structures=9)
    assert await counts(usage_redis) == {"done": 2, "pages": 11, "structures": 17, "failed": 1}


async def test_a_refusal_is_counted_by_its_error_code(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    for code in ("queue_full", "queue_full", "corrupt_file"):
        await usage.refused(code)
    assert await counts(usage_redis) == {"refused.queue_full": 2, "refused.corrupt_file": 1}


async def test_each_utc_day_has_its_own_hash_and_a_long_ttl(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis, today: Today
) -> None:
    await usage.job_created("pdf", small=True)
    today.now += timedelta(hours=10)  # 00:30 the next day
    await usage.job_created("pdf", small=True)
    await usage.job_created("pdf", small=True)
    assert await counts(usage_redis, date(2026, 10, 23)) == {"jobs.pdf.small": 1}
    assert await counts(usage_redis, date(2026, 10, 24)) == {"jobs.pdf.small": 2}
    ttl = await usage_redis.ttl(day_key(date(2026, 10, 23)))
    assert 799 * 86400 < ttl <= 800 * 86400


async def test_the_counter_stores_no_address_and_no_job_id_and_no_name(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    await usage.job_created("paste", small=True)
    await usage.job_finished(failed=False, pages=1, structures=1)
    await usage.refused("rate_limited")
    keys = await usage_redis.keys("*")
    assert keys == [day_key(DAY.date())]
    dump = json.dumps(await usage_redis.hgetall(keys[0]))
    assert not re.search(r"[0-9a-f]{32}|\d+\.\d+\.\d+\.\d+|\.png|\.pdf", dump), dump


async def test_a_redis_that_fails_never_fails_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = fakeredis.FakeServer()
    server.connected = False  # every command fails with a ConnectionError
    broken = RedisUsage(
        fakeredis.FakeAsyncRedis(server=server, decode_responses=True), clock=Today()
    )
    await broken.job_created("pdf", small=True)
    await broken.job_finished(failed=False, pages=1, structures=1)
    await broken.refused("busy")
    assert "usage counter" in caplog.text


async def test_a_redis_that_hangs_is_given_up_on_quickly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio
    from typing import Any

    class HangingPipeline:
        async def __aenter__(self) -> HangingPipeline:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def hincrby(self, *_args: object) -> None:
            return None

        def expire(self, *_args: object) -> None:
            return None

        async def execute(self) -> None:
            await asyncio.sleep(30)

    class HangingRedis:
        def pipeline(self, **_kwargs: object) -> HangingPipeline:
            return HangingPipeline()

    stuck: Any = HangingRedis()
    counter = RedisUsage(stuck, clock=Today(), timeout_seconds=0.05)
    started = asyncio.get_running_loop().time()
    await counter.job_created("pdf", small=True)
    assert asyncio.get_running_loop().time() - started < 2
    assert "usage counter: TimeoutError" in caplog.text


async def test_the_null_counter_counts_nothing() -> None:
    null = NullUsage()
    await null.job_created("pdf", small=True)
    await null.job_finished(failed=True, pages=1, structures=1)
    await null.refused("busy")


# --- kinds -----------------------------------------------------------------------------------


def test_the_kind_of_an_upload() -> None:
    assert kind_of(is_pdf=True, names=["paper.pdf"]) == "pdf"
    assert kind_of(is_pdf=False, names=["pasted-image.png"]) == "paste"
    assert kind_of(is_pdf=False, names=["Pasted-Image-1.PNG"]) == "paste"
    assert kind_of(is_pdf=False, names=["scan.png"]) == "image"
    assert kind_of(is_pdf=False, names=["pasted-image.png", "pasted-image-2.png"]) == "image"
    assert kind_of(is_pdf=False, names=[""]) == "image"


def test_the_paste_name_is_the_one_the_web_app_uses() -> None:
    """The API tells a pasted image from an uploaded one by the name the app gives it
    (`strings.upload.pastedName`); if the copy changes, this fails, not the counts."""
    strings = Path(__file__).resolve().parents[2] / "web" / "src" / "strings.ts"
    if not strings.exists():
        pytest.skip("the web sources are not here")
    match = re.search(r"pastedName:\s*['\"]([^'\"]+)['\"]", strings.read_text())
    assert match is not None, "strings.upload.pastedName not found"
    assert match.group(1) == PASTE_NAME_PREFIX


# --- the routes ------------------------------------------------------------------------------


@pytest.fixture
def counting_app(
    settings: Settings,
    redis: fakeredis.FakeAsyncRedis,
    clock: FakeClock,
    enqueue: FakeEnqueue,
    namer: FakeNamer,
    usage_redis: fakeredis.FakeAsyncRedis,
) -> tuple[FastAPI, RedisUsage]:
    usage = RedisUsage(usage_redis, clock=lambda: DAY)
    app = create_app(
        settings,
        JobStore(redis, settings, clock=clock),
        enqueue,
        ingest_runner=InProcessIngest(Limits.from_settings(settings)),
        namer=namer,
        analysis=InProcessAnalysis(settings.max_concurrent_analyses),
        usage=usage,
    )
    return app, usage


def post_as(app: FastAPI, peer: str) -> Callable[..., httpx.AsyncClient]:
    return lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 5000)), base_url="http://api.test"
    )


async def upload(client: httpx.AsyncClient, files: list[tuple[str, bytes]]) -> httpx.Response:
    body, headers = multipart(files)
    return await client.post("/api/jobs", content=body, headers=headers)


async def test_an_upload_is_counted_by_kind_and_size(
    counting_app: tuple[FastAPI, RedisUsage], usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    app, _ = counting_app
    big = make_pdf(3, page_size=(144.0, 144.0))
    one_page = make_pdf(1, page_size=(144.0, 144.0))
    for peer, files in [
        ("203.0.113.1", [("paper.pdf", big)]),
        ("203.0.113.2", [("one.pdf", one_page)]),
        ("203.0.113.3", [("pasted-image.png", make_png())]),
        ("203.0.113.4", [("scan.png", make_png())]),
        ("203.0.113.5", [("a.png", make_png()), ("b.png", make_png())]),
    ]:
        async with post_as(app, peer)() as client:
            assert (await upload(client, files)).status_code == 202
    assert await counts(usage_redis) == {
        "jobs.pdf.large": 1,
        "jobs.pdf.small": 1,
        "jobs.paste.small": 1,
        "jobs.image.small": 1,
        "jobs.image.large": 1,
    }


async def test_a_refused_upload_counts_as_refused_and_not_as_a_job(
    counting_app: tuple[FastAPI, RedisUsage], usage_redis: fakeredis.FakeAsyncRedis
) -> None:
    app, _ = counting_app
    async with post_as(app, "203.0.113.1")() as client:
        assert (await upload(client, [("notes.txt", b"hello world")])).status_code == 415
        assert (
            await upload(client, [("bad.png", b"\x89PNG\r\n\x1a\nnot really")])
        ).status_code == 400
        assert (await upload(client, [("a.png", make_png())])).status_code == 202
        assert (await upload(client, [("b.png", make_png())])).status_code == 202
        third = await upload(client, [("c.png", make_png())])
    assert third.status_code == 429
    assert await counts(usage_redis) == {
        "refused.unsupported": 1,
        "refused.corrupt_file": 1,
        "refused.rate_limited": 1,
        "jobs.image.small": 2,
    }


async def test_a_job_that_could_not_be_queued_is_not_counted_as_one(
    counting_app: tuple[FastAPI, RedisUsage],
    usage_redis: fakeredis.FakeAsyncRedis,
    enqueue: FakeEnqueue,
) -> None:
    app, _ = counting_app
    enqueue.error = RuntimeError("redis is gone")
    async with post_as(app, "203.0.113.1")() as client:
        assert (await upload(client, [("a.png", make_png())])).status_code == 503
    assert await counts(usage_redis) == {"refused.unavailable": 1}


# --- reading it ------------------------------------------------------------------------------


async def seeded(usage: RedisUsage, today: Today) -> None:
    await usage.job_created("pdf", small=False)
    await usage.job_created("paste", small=True)
    await usage.job_finished(failed=False, pages=10, structures=18)
    await usage.refused("queue_full")
    today.now += timedelta(days=1)
    await usage.job_created("image", small=True)
    await usage.job_finished(failed=True, pages=1, structures=0)
    await usage.refused("queue_full")
    await usage.refused("corrupt_file")


async def test_the_days_come_back_oldest_first_with_the_empty_ones(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis, today: Today
) -> None:
    await seeded(usage, today)
    rows = await read_days(usage_redis, 4, today=date(2026, 10, 25))
    assert [row["date"] for row in rows] == ["2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"]
    assert rows[0] == {"date": "2026-10-22"} and rows[3] == {"date": "2026-10-25"}
    assert rows[1]["jobs.pdf.large"] == 1 and rows[2]["failed"] == 1


async def test_the_table_has_a_column_for_everything_and_a_total(
    usage: RedisUsage, usage_redis: fakeredis.FakeAsyncRedis, today: Today
) -> None:
    await seeded(usage, today)
    text = table(await read_days(usage_redis, 2, today=date(2026, 10, 24)))
    rows = [line.split() for line in text.splitlines() if line.strip()]
    assert rows[0] == [
        "date", "jobs", "pdf", "image", "paste", "small", "large",
        "done", "failed", "refused", "pages", "structures",
    ]  # fmt: skip
    assert rows[1] == ["2026-10-23", "2", "1", "0", "1", "1", "1", "1", "0", "1", "10", "18"]
    assert rows[2] == ["2026-10-24", "1", "0", "1", "0", "1", "0", "0", "1", "2", "0", "0"]
    assert rows[-2] == ["total", "3", "1", "1", "1", "2", "1", "1", "1", "3", "10", "18"]
    assert text.splitlines()[-1] == "refused by code: corrupt_file 1, queue_full 2"


def test_the_command_prints_a_table_or_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    server = fakeredis.FakeServer()

    def connect(*_args: object, **_kwargs: object) -> fakeredis.FakeAsyncRedis:
        return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)  # one per event loop

    async def fill() -> None:
        counter = RedisUsage(connect())
        await counter.job_created("pdf", small=False)
        await counter.job_finished(failed=False, pages=3, structures=5)

    asyncio.run(fill())
    monkeypatch.setattr("decimer_api.usage.Redis.from_url", connect)
    assert main(["--days", "2"]) == 0
    out = capsys.readouterr().out
    assert "structures" in out and "total" in out
    assert main(["--days", "1", "--json"]) == 0
    (row,) = json.loads(capsys.readouterr().out)
    assert row["jobs.pdf.large"] == 1 and row["pages"] == 3 and row["structures"] == 5


def test_the_command_says_so_when_redis_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens here
    monkeypatch.delenv("USAGE_REDIS_URL", raising=False)
    assert main(["--days", "1"]) == 1
    assert "could not read the usage counts" in capsys.readouterr().err
