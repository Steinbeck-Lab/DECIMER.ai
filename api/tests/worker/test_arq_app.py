"""The arq glue and the in-process mode: the same pipeline, reached as production reaches it."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import arq
import arq.worker
import fakeredis
import httpx
import pytest
from arq.connections import ArqRedis
from arq.worker import Worker, create_worker
from fakeredis.aioredis import FakeAsyncRedisConnection
from redis.asyncio import ConnectionPool

from decimer_api.app import create_app
from decimer_api.ingest_runner import InProcessIngest
from decimer_api.models import JobStatus
from decimer_api.queue import PROCESS_JOB, ArqEnqueuer, health_key
from decimer_api.settings import Settings
from decimer_api.uploads import Limits
from decimer_api.worker import arq_app
from decimer_api.worker.inprocess import InProcessEnqueuer
from decimer_api.worker.settings import WorkerSettings
from tests.factories import make_png, multipart
from tests.worker.conftest import Rig, wait_until
from tests.worker.fakes import FakeBackend, smiles_for

RigFactory = Callable[..., Rig]


async def _no_redis_info(*_args: object, **_kwargs: object) -> None:
    """fakeredis has no INFO, which arq calls at start."""


@pytest.fixture
async def arq_redis(redis: fakeredis.FakeAsyncRedis) -> AsyncIterator[ArqRedis]:
    """An arq pool on the same fake Redis server as the API's store (bytes, not decoded)."""
    server = redis.connection_pool.connection_kwargs["server"]
    pool = ConnectionPool(connection_class=FakeAsyncRedisConnection, server=server)
    client = ArqRedis(connection_pool=pool)
    yield client
    await client.aclose()


def test_arq_accepts_our_settings(settings: Settings) -> None:
    config = arq_app.build_arq_settings(settings, WorkerSettings(worker_max_jobs=3))
    worker = create_worker(config, handle_signals=False)
    assert list(worker.functions) == [PROCESS_JOB] == ["process_job"]
    assert worker.max_jobs == 3
    assert worker.keep_result_s == 0  # arq keeps nothing; the record is the result
    assert worker.retry_jobs is True
    assert worker.job_timeout_s > WorkerSettings().pipeline_timeout_seconds
    assert worker.functions[PROCESS_JOB].max_tries == arq_app.MAX_TRIES


def test_the_worker_makes_itself_undumpable_before_it_starts(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """The render children run as the same user and must not read the worker's environment."""
    order: list[str] = []
    monkeypatch.setattr(arq_app, "make_undumpable", lambda: order.append("undumpable") or True)
    monkeypatch.setattr(arq_app, "run_worker", lambda config: order.append("run"))
    monkeypatch.setattr(arq_app, "build_arq_settings", lambda: {})
    arq_app.main()
    assert order == ["undumpable", "run"]


async def test_process_job_runs_the_pipeline_and_returns_none(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.pdf_job(1)
    result = await arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id)
    assert result is None
    stored = await rig.store.get(job.id, include_expired=True)
    assert stored is not None and stored.status is JobStatus.DONE


async def test_startup_builds_the_deps_and_shutdown_closes_them(settings: Settings) -> None:
    worker = WorkerSettings(seg_url="http://seg.test", chem_url="")
    ctx: dict[str, Any] = {}
    await arq_app.make_startup(settings, worker)(ctx)
    deps = ctx[arq_app.DEPS_KEY]
    assert deps.http is not None and deps.http.is_closed is False
    assert deps.http._trust_env is False  # no HTTP_PROXY for calls inside our network
    await arq_app.shutdown(ctx)
    assert deps.http.is_closed
    assert ctx == {}


async def test_a_job_goes_from_the_api_through_arq_to_done(
    make_rig: RigFactory,
    settings: Settings,
    arq_redis: ArqRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arq.worker, "log_redis_info", _no_redis_info)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    enqueuer = ArqEnqueuer("redis://unused:6379/0")
    enqueuer._pool = arq_redis  # the pool the real one would open
    app = create_app(
        settings,
        rig.store,
        enqueuer,
        ingest_runner=InProcessIngest(Limits.from_settings(settings)),
    )
    body, headers = multipart([("structure.png", make_png((120, 90)))])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://api"
    ) as client:
        created = await client.post("/api/jobs", content=body, headers=headers)
        assert created.status_code == 202
        job_id = created.json()["id"]
        assert (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "queued"

        async def start(ctx: dict[str, Any]) -> None:
            ctx[arq_app.DEPS_KEY] = rig.deps

        config = arq_app.build_arq_settings(settings, rig.worker, startup=start)
        worker = Worker(
            **config, redis_pool=arq_redis, burst=True, poll_delay=0.01, handle_signals=False
        )
        await worker.async_run()
        await worker.close()
        assert (worker.jobs_complete, worker.jobs_failed) == (1, 0)

        job = (await client.get(f"/api/jobs/{job_id}")).json()
        assert job["status"] == "done"
        assert [r["smiles"] for r in job["results"]] == [smiles_for(0, 0)]
        assert job["results"][0]["cropUrl"] == f"/api/jobs/{job_id}/media/crop-0.png"
        assert [s["state"] for s in job["stages"]] == ["done"] * 5
    # arq itself kept nothing
    leftovers = [key for key in await arq_redis.keys("arq:*") if not key.endswith(b"health-check")]
    assert leftovers == []
    await enqueuer.aclose()


# --- the queues -------------------------------------------------------------

FAST = "decimer:fast"
DEFAULT = "arq:queue"


@pytest.mark.parametrize("name", [DEFAULT, FAST, "jobs:other"])
def test_a_worker_serves_the_queue_named_in_the_settings(name: str) -> None:
    config = arq_app.build_arq_settings(Settings(worker_queue_name=name), WorkerSettings())
    worker = create_worker(config, handle_signals=False)
    assert worker.queue_name == name
    assert worker.health_check_key == health_key(name)


@pytest.mark.parametrize("name", [None, DEFAULT, FAST])
def test_the_container_health_check_looks_for_the_key_the_worker_keeps(
    monkeypatch: pytest.MonkeyPatch, name: str | None
) -> None:
    """deploy/worker_healthcheck.py builds the key from WORKER_QUEUE_NAME on its own; it must be
    the key arq's worker of that queue sets, or a healthy worker would be reported unhealthy."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).parents[3] / "deploy" / "worker_healthcheck.py"
    if name is None:
        monkeypatch.delenv("WORKER_QUEUE_NAME", raising=False)
    else:
        monkeypatch.setenv("WORKER_QUEUE_NAME", name)
    spec = importlib.util.spec_from_file_location("worker_healthcheck", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = arq_app.build_arq_settings(
        Settings(worker_queue_name=name or DEFAULT), WorkerSettings()
    )
    assert module.KEY == create_worker(config, handle_signals=False).health_check_key


async def _post(client: httpx.AsyncClient, files: list[tuple[str, bytes]]) -> str:
    body, headers = multipart(files)
    created = await client.post("/api/jobs", content=body, headers=headers)
    assert created.status_code == 202
    return str(created.json()["id"])


async def _run_worker_on(queue: str, rig: Rig, settings: Settings, arq_redis: ArqRedis) -> Worker:
    async def start(ctx: dict[str, Any]) -> None:
        ctx[arq_app.DEPS_KEY] = rig.deps

    config = arq_app.build_arq_settings(
        settings.model_copy(update={"worker_queue_name": queue}), rig.worker, startup=start
    )
    worker = Worker(
        **config, redis_pool=arq_redis, burst=True, poll_delay=0.01, handle_signals=False
    )
    await worker.async_run()
    await worker.close()
    return worker


async def test_small_jobs_take_the_fast_lane_and_large_jobs_the_default_one(
    make_rig: RigFactory,
    settings: Settings,
    arq_redis: ArqRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a fast worker alive: a pasted image is queued on the fast queue and a worker that
    serves it runs the job; a two-image upload is queued on the default queue, which the fast
    worker does not look at."""
    monkeypatch.setattr(arq.worker, "log_redis_info", _no_redis_info)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    enqueuer = ArqEnqueuer("redis://unused:6379/0", queue_name=DEFAULT, fast_queue_name=FAST)
    enqueuer._pool = arq_redis
    await arq_redis.set(health_key(FAST), b"alive")  # the fast worker is running
    app = create_app(
        settings, rig.store, enqueuer, ingest_runner=InProcessIngest(Limits.from_settings(settings))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://api"
    ) as client:
        small = await _post(client, [("pasted.png", make_png((120, 90)))])
        large = await _post(
            client, [("a.png", make_png((120, 90))), ("b.png", make_png((100, 80)))]
        )
        assert await arq_redis.zrange(FAST, 0, -1) == [small.encode()]
        assert await arq_redis.zrange(DEFAULT, 0, -1) == [large.encode()]

        fast_worker = await _run_worker_on(FAST, rig, settings, arq_redis)
        assert fast_worker.jobs_complete == 1
        assert (await client.get(f"/api/jobs/{small}")).json()["status"] == "done"
        assert (await client.get(f"/api/jobs/{large}")).json()["status"] == "queued"

        default_worker = await _run_worker_on(DEFAULT, rig, settings, arq_redis)
        assert default_worker.jobs_complete == 1
        assert (await client.get(f"/api/jobs/{large}")).json()["status"] == "done"
    await enqueuer.aclose()


async def test_without_a_fast_worker_a_small_job_still_runs_on_the_default_worker(
    make_rig: RigFactory,
    settings: Settings,
    arq_redis: ArqRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(arq.worker, "log_redis_info", _no_redis_info)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    enqueuer = ArqEnqueuer("redis://unused:6379/0", queue_name=DEFAULT, fast_queue_name=FAST)
    enqueuer._pool = arq_redis  # no health key for the fast queue: the profile is off
    app = create_app(
        settings, rig.store, enqueuer, ingest_runner=InProcessIngest(Limits.from_settings(settings))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://api"
    ) as client:
        job = await _post(client, [("pasted.png", make_png((120, 90)))])
        assert await arq_redis.zcard(FAST) == 0
        assert await arq_redis.zrange(DEFAULT, 0, -1) == [job.encode()]
        worker = await _run_worker_on(DEFAULT, rig, settings, arq_redis)
        assert worker.jobs_complete == 1
        assert (await client.get(f"/api/jobs/{job}")).json()["status"] == "done"
    await enqueuer.aclose()


async def test_the_default_worker_watches_the_fast_queue_and_the_fast_worker_does_not(
    settings: Settings,
) -> None:
    worker_settings = WorkerSettings(seg_url="http://seg.test", chem_url="")
    ctx: dict[str, Any] = {}
    await arq_app.make_startup(settings, worker_settings)(ctx)
    rescue = ctx[arq_app.RESCUE_KEY]
    assert isinstance(rescue, asyncio.Task) and not rescue.done()
    await arq_app.shutdown(ctx)
    assert rescue.cancelled() and ctx == {}

    fast = settings.model_copy(update={"worker_queue_name": FAST})  # a fast worker
    ctx = {}
    await arq_app.make_startup(fast, worker_settings)(ctx)
    assert arq_app.RESCUE_KEY not in ctx
    await arq_app.shutdown(ctx)

    ctx = {}  # and switched off
    await arq_app.make_startup(settings, WorkerSettings(fast_lane_rescue_seconds=0))(ctx)
    assert arq_app.RESCUE_KEY not in ctx
    await arq_app.shutdown(ctx)


async def test_the_worker_process_never_loads_the_pdf_parser() -> None:
    """Rendering happens in children; the worker process imports no PDF code."""
    code = (
        "import sys\n"
        "import decimer_api.worker.arq_app, decimer_api.worker.inprocess\n"
        "print([m for m in ('pypdfium2', 'pillow_heif') if m in sys.modules])\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", code, stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    assert stdout.decode().strip() == "[]"


# --- the in-process mode -------------------------------------------------------------------------


async def test_the_in_process_mode_runs_a_job_started_by_the_api(
    make_rig: RigFactory, settings: Settings
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}))
    enqueuer = InProcessEnqueuer(rig.deps)
    app = create_app(
        settings, rig.store, enqueuer, ingest_runner=InProcessIngest(Limits.from_settings(settings))
    )
    body, headers = multipart([("structures.png", make_png((600, 600)))])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://api"
    ) as client:
        job_id = (await client.post("/api/jobs", content=body, headers=headers)).json()["id"]

        async def finished() -> bool:
            return (await client.get(f"/api/jobs/{job_id}")).json()["status"] == "done"

        for _ in range(200):
            if await finished():
                break
            await asyncio.sleep(0.01)
        job = (await client.get(f"/api/jobs/{job_id}")).json()
    assert job["status"] == "done"
    assert len(job["results"]) == 2
    await enqueuer.aclose()


async def test_the_in_process_mode_runs_at_most_max_jobs_at_once(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 1}, page_delays={0: 0.1})
    rig = make_rig(backend=backend, seg_concurrency=8)
    enqueuer = InProcessEnqueuer(rig.deps, max_jobs=2)
    jobs = [await rig.pdf_job(1, ip=f"client-{n}") for n in range(4)]
    for job in jobs:
        await enqueuer(job.id)
    await wait_until(lambda: backend.count("seg") >= 2)
    await asyncio.sleep(0.03)
    assert backend.peak["seg"] == 2  # the other two are still waiting for a place
    await wait_until(lambda: len(rig.store.finishes) == 4, timeout=5)
    assert backend.peak["seg"] == 2
    await enqueuer.aclose()


async def test_closing_the_in_process_mode_cancels_running_jobs_without_finishing_them(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=5.0))
    enqueuer = InProcessEnqueuer(rig.deps)
    job = await rig.pdf_job(1)
    await enqueuer(job.id)
    await wait_until(lambda: rig.backend.count("seg") == 1)
    await enqueuer.aclose()
    assert rig.store.finishes == []
    stored = await rig.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.RUNNING


# --- the timeout arq enforces ---------------------------------------------------------------------


async def test_a_run_that_arq_cuts_at_its_own_timeout_is_reported_loudly(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The pipeline's own deadline fires first and explains itself to the user. If arq's
    (a minute later) fires instead, the job is cancelled without a word to the user and its
    record is left running: the log is then the only place to see it."""
    rig = make_rig(
        backend=FakeBackend(layout={0: 1}, delay=5.0),
        pipeline_timeout_seconds=30,  # the pipeline would wait; arq's limit comes first
    )
    monkeypatch.setattr(arq_app, "ARQ_TIMEOUT_MARGIN_SECONDS", -29.9)  # arq's limit: 0.1 s
    job = await rig.pdf_job(1)
    with caplog.at_level(logging.ERROR, logger="decimer_api.worker"):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id), 0.1)
    assert "job_timeout" in caplog.text
    assert job.id in caplog.text
    stored = await rig.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.RUNNING  # left for its TTL


async def test_a_run_that_is_cancelled_early_is_not_called_a_timeout(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """A restart cancels jobs too; that is routine and arq runs them again."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=5.0))
    job = await rig.pdf_job(1)
    task = asyncio.create_task(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id))
    await wait_until(lambda: rig.backend.count("seg") == 1)
    with caplog.at_level(logging.ERROR, logger="decimer_api.worker"):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert "job_timeout" not in caplog.text


async def test_the_in_process_mode_makes_itself_undumpable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    from decimer_api.worker import inprocess

    called: list[str] = []
    monkeypatch.setattr(inprocess, "make_undumpable", lambda: called.append("yes") or True)
    monkeypatch.setenv("DATA_DIR", str(settings.data_dir))
    app = inprocess.create_dev_app()
    assert called == ["yes"]
    assert app.title  # the app was built
