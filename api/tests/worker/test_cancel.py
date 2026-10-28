"""A job its owner deletes is stopped by the worker (`worker/cancel.py`)."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable

import pytest

from decimer_api.models import JobStatus
from decimer_api.worker import arq_app
from decimer_api.worker.cancel import run_cancellable
from decimer_api.worker.inprocess import InProcessEnqueuer
from tests.worker.conftest import Rig, wait_until
from tests.worker.fakes import FakeBackend

RigFactory = Callable[..., Rig]
POLL = 0.02


async def delete_like_the_api(rig: Rig, job_id: str) -> None:
    """What `DELETE /api/jobs/{id}` does."""
    job = await rig.store.get(job_id)
    assert job is not None
    await rig.store.cancel(job)
    shutil.rmtree(rig.settings.data_dir / job_id, ignore_errors=True)


async def test_a_running_job_is_stopped_and_nothing_of_it_is_kept(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=30.0), cancel_poll_seconds=POLL)
    job = await rig.pdf_job(1)
    task = asyncio.create_task(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id))
    await wait_until(lambda: rig.backend.count("seg") == 1)
    started = asyncio.get_running_loop().time()
    await delete_like_the_api(rig, job.id)
    assert await asyncio.wait_for(task, 2) is None  # arq sees an ordinary return: no retry
    assert asyncio.get_running_loop().time() - started < 1  # within a poll, not the 30 s call
    assert rig.store.finishes == []  # never finished
    assert await rig.store.get(job.id, include_expired=True) is None
    assert not (rig.settings.data_dir / job.id).exists()
    assert rig.backend.count("seg") == 1  # and nothing was asked of the services after


async def test_the_clients_slot_is_held_for_its_whole_time_not_until_the_worker_stops(
    make_rig: RigFactory,
) -> None:
    """Reviews 2 and 3: the models go on computing the calls the worker abandoned, so the slot is
    not given back when the run stops either; only its TTL ends it."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=30.0), cancel_poll_seconds=0.3)
    job = await rig.pdf_job(1)
    task = asyncio.create_task(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id))
    await wait_until(lambda: rig.backend.count("seg") == 1)
    await delete_like_the_api(rig, job.id)
    assert await rig.store.active_count(job.client_ip_hash) == 1  # held: the run goes on a moment
    await asyncio.wait_for(task, 2)
    assert await rig.store.active_count(job.client_ip_hash) == 1  # and still held, run stopped
    ttl = await rig.store.redis.ttl(rig.store.cancelling_key(job.id))
    assert rig.settings.cancelling_slot_seconds - 5 < ttl <= rig.settings.cancelling_slot_seconds


async def test_what_the_worker_writes_before_it_notices_does_not_stay(
    make_rig: RigFactory,
) -> None:
    """The run is cancelled a poll after the delete; whatever it saved or wrote in that moment is
    removed with the rest."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=30.0), cancel_poll_seconds=0.3)
    job = await rig.pdf_job(1)
    task = asyncio.create_task(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id))
    await wait_until(lambda: rig.backend.count("seg") == 1)
    await delete_like_the_api(rig, job.id)
    (rig.settings.data_dir / job.id).mkdir()  # the worker recreates its folder in the gap
    (rig.settings.data_dir / job.id / "page-0.png").write_bytes(b"x")
    await asyncio.wait_for(task, 2)
    assert not (rig.settings.data_dir / job.id).exists()
    assert await rig.store.get(job.id, include_expired=True) is None


async def test_a_job_deleted_while_queued_is_skipped_by_the_worker(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}), cancel_poll_seconds=POLL)
    job = await rig.pdf_job(1)
    await delete_like_the_api(rig, job.id)
    await arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id)
    assert rig.backend.calls == []
    assert rig.store.finishes == []


async def test_a_job_that_is_not_deleted_runs_to_the_end_as_before(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}), cancel_poll_seconds=POLL)
    job = await rig.pdf_job(1)
    await arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, job.id)
    stored = await rig.store.get(job.id, include_expired=True)
    assert stored is not None and stored.status is JobStatus.DONE and len(stored.results) == 2
    assert (rig.settings.data_dir / job.id / "crop-0.png").is_file()


async def test_deleting_one_job_does_not_stop_another(make_rig: RigFactory) -> None:
    rig = make_rig(
        backend=FakeBackend(layout={0: 1}, delay=0.3), cancel_poll_seconds=POLL, seg_concurrency=4
    )
    doomed = await rig.pdf_job(1, ip="a")
    spared = await rig.pdf_job(1, ip="b")
    tasks = [
        asyncio.create_task(arq_app.process_job({arq_app.DEPS_KEY: rig.deps}, j.id))
        for j in (doomed, spared)
    ]
    await wait_until(lambda: rig.backend.count("seg") == 2)
    await delete_like_the_api(rig, doomed.id)
    await asyncio.wait_for(asyncio.gather(*tasks), 5)
    survivor = await rig.store.get(spared.id, include_expired=True)
    assert survivor is not None and survivor.status is JobStatus.DONE
    assert await rig.store.get(doomed.id, include_expired=True) is None


async def test_the_in_process_mode_stops_a_deleted_job_too(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=30.0), cancel_poll_seconds=POLL)
    enqueuer = InProcessEnqueuer(rig.deps)
    job = await rig.pdf_job(1)
    await enqueuer(job.id)
    await wait_until(lambda: rig.backend.count("seg") == 1)
    await delete_like_the_api(rig, job.id)
    await wait_until(lambda: not enqueuer._tasks)  # noqa: SLF001
    assert rig.store.finishes == []
    await enqueuer.aclose()


# --- run_cancellable on its own ------------------------------------------------------------------


async def test_a_run_that_ends_just_as_the_job_is_deleted_leaves_nothing_behind(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(cancel_poll_seconds=30)  # the watcher would not notice in time
    job = await rig.pdf_job(1)
    stored = await rig.store.get(job.id)
    assert stored is not None

    async def run() -> None:
        # The record was written, then the user deleted the job, then the run ended.
        await rig.store.save(stored)
        await rig.store.cancel(stored)
        await rig.store.redis.set(rig.store.job_key(job.id), stored.model_dump_json(by_alias=True))

    cancelled = await run_cancellable(
        job.id, rig.store, run(), poll_seconds=30, folder=rig.settings.data_dir / job.id
    )
    assert cancelled is True
    assert await rig.store.get(job.id, include_expired=True) is None
    assert not (rig.settings.data_dir / job.id).exists()


async def test_a_run_that_is_not_cancelled_reports_false_and_leaves_the_job_alone(
    make_rig: RigFactory,
) -> None:
    rig = make_rig()
    job = await rig.pdf_job(1)

    async def run() -> None:
        return None

    assert (
        await run_cancellable(
            job.id, rig.store, run(), poll_seconds=POLL, folder=rig.settings.data_dir / job.id
        )
        is False
    )
    assert await rig.store.get(job.id) is not None
    assert (rig.settings.data_dir / job.id).is_dir()


async def test_what_the_run_raises_is_raised_and_the_watcher_is_gone(make_rig: RigFactory) -> None:
    rig = make_rig()
    job = await rig.pdf_job(1)

    async def run() -> None:
        raise RuntimeError("the pipeline broke")

    before = {t for t in asyncio.all_tasks() if not t.done()}
    with pytest.raises(RuntimeError, match="the pipeline broke"):
        await run_cancellable(
            job.id, rig.store, run(), poll_seconds=POLL, folder=rig.settings.data_dir / job.id
        )
    assert {t for t in asyncio.all_tasks() if not t.done()} == before  # no task left polling


async def test_a_restart_cancels_the_run_and_the_watcher_and_is_not_taken_for_a_delete(
    make_rig: RigFactory,
) -> None:
    rig = make_rig()
    job = await rig.pdf_job(1)
    ran = asyncio.Event()
    cancelled_inside = False

    async def run() -> None:
        nonlocal cancelled_inside
        ran.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled_inside = True
            raise

    before = {t for t in asyncio.all_tasks() if not t.done()}
    task = asyncio.create_task(
        run_cancellable(
            job.id, rig.store, run(), poll_seconds=POLL, folder=rig.settings.data_dir / job.id
        )
    )
    await ran.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled_inside
    assert {t for t in asyncio.all_tasks() if not t.done()} == before
    assert await rig.store.get(job.id) is not None  # a restart keeps the job: arq runs it again
    assert (rig.settings.data_dir / job.id).is_dir()


async def test_a_redis_that_fails_to_answer_does_not_stop_the_watch_or_the_job(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = make_rig()
    job = await rig.pdf_job(1)
    real = rig.store.is_cancelled
    answers = 0

    async def flaky(job_id: str) -> bool:
        nonlocal answers
        answers += 1
        if answers <= 2:
            raise ConnectionError("redis hiccup")
        return await real(job_id)

    monkeypatch.setattr(rig.store, "is_cancelled", flaky)
    stop = asyncio.Event()

    async def run() -> None:
        await stop.wait()

    task = asyncio.create_task(
        run_cancellable(
            job.id, rig.store, run(), poll_seconds=POLL, folder=rig.settings.data_dir / job.id
        )
    )
    await wait_until(lambda: answers >= 3)
    assert not task.done()  # the hiccups did not end the job or the watch
    stored = await rig.store.get(job.id)
    assert stored is not None
    await rig.store.cancel(stored)
    assert await asyncio.wait_for(task, 2) is True


async def test_a_job_id_that_is_not_one_never_reaches_rmtree(
    make_rig: RigFactory, tmp_path: object
) -> None:
    """The folder is only removed for a valid job id (the caller builds the path from it)."""
    from pathlib import Path

    rig = make_rig()
    victim = Path(str(tmp_path)) / "important"
    victim.mkdir()
    (victim / "file").write_text("keep")

    async def run() -> None:
        return None

    await rig.store.redis.set(rig.store.cancel_key("../important"), "1")
    cancelled = await run_cancellable(
        "../important", rig.store, run(), poll_seconds=POLL, folder=victim
    )
    assert cancelled is True
    assert (victim / "file").read_text() == "keep"
