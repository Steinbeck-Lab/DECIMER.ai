"""Slow, dead and restarting services; a job that is run again; saves that must not cross."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

from decimer_api.models import Job, JobStatus, JobWarning, StageName, StageState
from decimer_api.worker import errors
from decimer_api.worker.pipeline import run_pipeline
from tests.worker.conftest import Rig, stage_state, wait_until
from tests.worker.fakes import FakeBackend, crop_id

RigFactory = Callable[..., Rig]


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def raising(error: Exception) -> Callable[..., httpx.Response | None]:
    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        raise error

    return hook


# --- a service that restarts ---------------------------------------------------------------------


async def test_a_job_waits_for_a_service_that_is_starting(make_rig: RigFactory) -> None:
    """ocsr takes about 50 s to load. The job finds it down, waits, and carries on."""
    backend_state = {"calls": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        backend_state["calls"] += 1
        if sum(rig.sleep.delays) < 50:
            raise httpx.ConnectError("connection refused")
        return None

    rig = make_rig(
        backend=FakeBackend(layout={0: 2}, hooks={"ocsr": hook}),
        connect_retry_budget_seconds=90,
    )
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert all(r.valid for r in job.results)
    assert 50 <= sum(rig.sleep.delays) < 80
    assert codes(job) == []


async def test_a_crop_that_kills_the_service_is_one_bad_result_not_a_failed_job(
    make_rig: RigFactory,
) -> None:
    """The connection is reset twice on the same crop (an input that crashes ocsr): that crop is
    given up on after one retry, the others are recognised."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        if crop_id(parts["image"]) == (0, 1):
            raise httpx.RemoteProtocolError("server disconnected without sending a response")
        return None

    backend = FakeBackend(layout={0: 3}, hooks={"ocsr": hook})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [r.valid for r in job.results] == [True, False, True]
    assert codes(job) == ["recognition_failed"]
    assert sum(1 for p in backend.parts_of("ocsr") if crop_id(p["image"]) == (0, 1)) == 2


# --- timeouts -------------------------------------------------------------------------------------


async def test_one_ocsr_timeout_spoils_that_crop_only(make_rig: RigFactory) -> None:
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if seen["n"] == 2:
            raise httpx.ReadTimeout("no answer in 120 s")
        return None

    backend = FakeBackend(layout={0: 3}, hooks={"ocsr": hook})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert sorted(r.valid for r in job.results) == [False, True, True]
    assert codes(job) == ["recognition_failed"]


async def test_repeated_ocsr_timeouts_give_up_on_the_job(make_rig: RigFactory) -> None:
    """A service that times out is wedged or overloaded; every further crop would wait as long."""
    backend = FakeBackend(layout={0: 8}, hooks={"ocsr": raising(httpx.ReadTimeout("no answer"))})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.SERVICE_UNAVAILABLE.message
    assert rig.worker.ocsr_max_timeouts == 3
    # three timeouts end it, not eight crops' worth; a call that was already waiting for the
    # gate when the third came in may still go out
    assert 3 <= backend.count("ocsr") <= 4
    assert len(rig.store.finishes) == 1


async def test_the_number_of_timeouts_ocsr_may_have_is_a_setting(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 8}, hooks={"ocsr": raising(httpx.ReadTimeout("no answer"))})
    rig = make_rig(backend=backend, ocsr_max_timeouts=5)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert 5 <= backend.count("ocsr") <= 6


async def test_a_cls_timeout_leaves_cls_alone_for_the_rest_of_the_job(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 8}, hooks={"cls": raising(httpx.ReadTimeout("no answer"))})
    rig = make_rig(backend=backend, cls_concurrency=1, ocsr_concurrency=1)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [r.is_structure for r in job.results] == [None] * 8
    assert all(r.valid for r in job.results)
    assert backend.count("cls") == 1  # one wait for 60 s, not eight
    assert codes(job) == ["classifier_unavailable"]  # one warning


async def test_a_cls_timeout_with_calls_in_flight_still_gives_one_warning(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(layout={0: 6}, hooks={"cls": raising(httpx.ReadTimeout("no answer"))})
    rig = make_rig(backend=backend, cls_concurrency=2, ocsr_concurrency=2)
    job = await rig.run(await rig.pdf_job(1))
    assert codes(job) == ["classifier_unavailable"]
    assert backend.count("cls") <= 2


async def test_a_seg_timeout_loses_that_page_only(make_rig: RigFactory) -> None:
    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        if parts["page_index"] == b"1":
            raise httpx.ReadTimeout("no answer")
        return None

    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1, 2: 1}, hooks={"seg": hook}))
    job = await rig.run(await rig.pdf_job(3))
    assert [r.page_index for r in job.results] == [0, 2]
    assert codes(job) == ["page_segmentation_failed"]


# --- running a job again --------------------------------------------------------------------------


async def _interrupt_in_recognition(rig: Rig, job: Job) -> Job:
    """Start the job, cancel it once ocsr is being asked and the segmentation is in the STORED
    record (the stages overlap: ocsr is asked about the first page while the others are still
    being searched), and return the record it left."""
    task = asyncio.create_task(run_pipeline(job.id, rig.deps))

    async def segmentation_is_stored() -> None:
        while True:
            stored = await rig.store.get(job.id)
            if (
                rig.backend.count("ocsr") >= 1
                and stored is not None
                and stage_state(stored, StageName.SEGMENT) is StageState.DONE
            ):
                return
            await asyncio.sleep(0.005)

    await asyncio.wait_for(segmentation_is_stored(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    stored = await rig.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.RUNNING
    return stored


async def test_a_second_run_drops_the_structure_cap_warning_and_truncation_of_the_first(
    make_rig: RigFactory,
) -> None:
    first = make_rig(backend=FakeBackend(layout={0: 15, 1: 15}, service_delays={"ocsr": 5.0}))
    job = await first.pdf_job(2)
    left = await _interrupt_in_recognition(first, job)
    # the first run got as far as saving what it had found: the test means something only if so
    assert left.truncated is True
    assert "structures_truncated" in codes(left)
    assert left.structures_dropped == 10

    second = make_rig(backend=FakeBackend(layout={0: 2}))
    done = await second.run(job)
    assert done.status is JobStatus.DONE
    assert len(done.results) == 2
    assert (done.structures_found, done.structures_dropped) == (2, 0)
    assert done.truncated is False  # nothing is cut off any more
    assert codes(done) == []


async def test_a_second_run_keeps_the_page_limit_the_api_recorded(make_rig: RigFactory) -> None:
    api_warning = JobWarning(
        code="pages_truncated", message="Only the first 2 pages were processed."
    )
    first = make_rig(backend=FakeBackend(layout={0: 15, 1: 15}, service_delays={"ocsr": 5.0}))
    job = await first.pdf_job(2, total_pages=4, warnings=[api_warning], truncated=True)
    await _interrupt_in_recognition(first, job)

    second = make_rig(backend=FakeBackend(layout={0: 2}))
    done = await second.run(job)
    assert done.truncated is True  # the pages were cut, whatever the structures did
    assert codes(done) == ["pages_truncated"]  # the API's warning, once
    assert (done.pages, done.pages_processed) == (4, 2)


async def test_a_problem_that_happens_again_is_not_reported_twice(make_rig: RigFactory) -> None:
    def broken_page(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if parts["page_index"] == b"1" else None

    first = make_rig(
        backend=FakeBackend(
            layout={0: 1, 1: 1}, hooks={"seg": broken_page}, service_delays={"ocsr": 5.0}
        )
    )
    job = await first.pdf_job(2)
    left = await _interrupt_in_recognition(first, job)
    assert codes(left) == ["page_segmentation_failed"]  # the first run saved it

    second = make_rig(backend=FakeBackend(layout={0: 1, 1: 1}, hooks={"seg": broken_page}))
    done = await second.run(job)
    assert codes(done) == ["page_segmentation_failed"]  # once, not twice


async def test_a_run_that_is_cancelled_in_the_render_stage_runs_again_from_the_start(
    make_rig: RigFactory,
) -> None:
    first = make_rig(backend=FakeBackend(layout={0: 1}, delay=5.0))
    job = await first.pdf_job(1)
    task = asyncio.create_task(run_pipeline(job.id, first.deps))
    await wait_until(lambda: first.backend.count("seg") >= 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    second = make_rig(backend=FakeBackend(layout={0: 1}))
    done = await second.run(job)
    assert done.status is JobStatus.DONE and len(done.results) == 1


# --- saves ----------------------------------------------------------------------------------------


async def test_the_saves_of_one_job_never_overlap(make_rig: RigFactory) -> None:
    """The heartbeat and a stage can both want to save at once; the record written last must be
    the one saved last. So the saves of a job take turns."""
    rig = make_rig(backend=FakeBackend(layout={0: 2}, delay=0.05), heartbeat_seconds=0.005)
    original = rig.store.save
    running = {"now": 0, "peak": 0}

    async def slow_save(job: Job) -> None:
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.004)
        await original(job)
        running["now"] -= 1

    rig.store.save = slow_save  # type: ignore[method-assign]
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert running["peak"] == 1
    assert len(rig.store.saves) > 12  # the heartbeat really was saving in between


async def test_the_last_record_saved_is_the_final_one(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}, delay=0.02), heartbeat_seconds=0.003)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert len(job.results) == 2
