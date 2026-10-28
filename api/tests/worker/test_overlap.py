"""Overlapped stages: a page goes to seg as soon as it is rendered, a crop goes to
ocsr and cls (and the check) as soon as its place under the cap is known, and the result is exactly
what the four stages one after the other gave.

Three layers: the two rules alone (`PrefixNumbering`, `StageTracker`), what happens when (the order
of events with held calls, never how long something takes), and the results against an oracle that
is not the pipeline (flatten the pages in order, cut at the cap)."""

from __future__ import annotations

import asyncio
import itertools
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from decimer_api.chemistry import analyse_smiles
from decimer_api.models import Job, JobStatus, Stage, StageName, StageState, initial_stages
from decimer_api.worker import errors, pipeline
from decimer_api.worker.overlap import PIPELINE_STAGES, PrefixNumbering, Released, StageTracker
from decimer_api.worker.pipeline import run_pipeline
from decimer_api.worker.render import ThreadRenderer
from tests.worker.conftest import Rig, assert_legal_progress, stage_state, wait_until
from tests.worker.fakes import FailingPages, FakeBackend, FakeResolver, crop_id, smiles_for

RigFactory = Callable[..., Rig]
OCSR_TIMEOUT = httpx.ReadTimeout("no answer in 120 s")


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def oracle(
    layout: dict[int, int], cap: int, skipped: set[int] | None = None
) -> tuple[list[tuple[int, int]], int]:
    """What the job must come to, from the layout alone: every structure of the pages that were
    rendered and searched, in page order and seg's order within a page, cut at the cap. Returns
    the (page, place on the page) of the kept ones and the number found."""
    out = skipped or set()
    found = [(p, k) for p in sorted(layout) if p not in out for k in range(layout[p])]
    return found[:cap], len(found)


def shown(job: Job) -> list[tuple[int, int, str]]:
    """(index, page, smiles) of the results."""
    return [(r.index, r.page_index, r.smiles) for r in job.results]


def want(kept: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    return [(n, p, smiles_for(p, k)) for n, (p, k) in enumerate(kept)]


def seen_states(rig: Rig) -> set[tuple[str, ...]]:
    """Every combination of stage states (render ... check) a poll could have found so far."""
    return {
        tuple(stage.state.value for stage in job.stages[1:])
        for job in rig.store.saves
        if job.status is JobStatus.RUNNING
    }


def no_slot_is_held(rig: Rig) -> None:
    """Every call has given its slot back, and nothing is queued for one."""
    for service, pool in rig.deps.services._caller._pools.items():
        assert all(n == 0 for n in pool.in_flight.values()), service
        assert pool.waiting == 0, service


# --- the numbering rule ------------------------------------------------------------------------


def numbered(released: list[Released[str]]) -> list[tuple[int, list[tuple[int, str]]]]:
    return [(r.position, r.kept) for r in released]


def test_pages_that_arrive_in_order_are_numbered_at_once() -> None:
    numbering = PrefixNumbering[str](3, cap=20)
    assert numbered(numbering.settle(0, 2, ["a", "b"])) == [(0, [(0, "a"), (1, "b")])]
    assert numbered(numbering.settle(1, 1, ["c"])) == [(1, [(2, "c")])]
    assert not numbering.complete
    assert numbered(numbering.settle(2, 0, [])) == [(2, [])]
    assert numbering.complete and (numbering.total, numbering.kept) == (3, 3)


def test_a_page_waits_for_the_pages_before_it() -> None:
    """The prefix rule: nothing is numbered until everything before it is known."""
    numbering = PrefixNumbering[str](3, cap=20)
    assert numbering.settle(2, 2, ["e", "f"]) == []
    assert numbering.settle(1, 1, ["c"]) == []
    released = numbering.settle(0, 2, ["a", "b"])
    assert numbered(released) == [
        (0, [(0, "a"), (1, "b")]),
        (1, [(2, "c")]),
        (2, [(3, "e"), (4, "f")]),
    ]
    assert numbering.complete


def test_a_failed_page_settles_as_nothing_and_moves_nobody() -> None:
    numbering = PrefixNumbering[str](3, cap=20)
    numbering.settle(2, 1, ["z"])
    numbering.settle(0, 0, [])  # failed
    assert numbered(numbering.settle(1, 1, ["y"])) == [(1, [(0, "y")]), (2, [(1, "z")])]


def test_the_cap_cuts_in_the_middle_of_a_page() -> None:
    numbering = PrefixNumbering[str](3, cap=4)
    numbering.settle(0, 3, ["a", "b", "c"])
    (second,) = numbering.settle(1, 3, ["d", "e", "f"])
    assert second.kept == [(3, "d")] and second.dropped == ["e", "f"]
    (third,) = numbering.settle(2, 2, ["g", "h"])
    assert third.kept == [] and third.dropped == ["g", "h"]
    assert (numbering.total, numbering.kept) == (8, 4)


def test_a_page_with_more_structures_than_were_kept_for_it_still_counts_them_all() -> None:
    """seg found 25 on page 0 but only the first 20 are written: the 25 move the numbering on."""
    numbering = PrefixNumbering[int](2, cap=20)
    (first,) = numbering.settle(0, 25, list(range(20)))
    assert len(first.kept) == 20 and first.dropped == []
    (second,) = numbering.settle(1, 2, [100, 101])
    assert second.kept == [] and second.dropped == [100, 101]
    assert (numbering.total, numbering.kept) == (27, 20)


def test_what_a_crop_is_called_does_not_depend_on_the_order_pages_arrive_in() -> None:
    """The property the prefix rule exists for: every arrival order of the same pages gives the
    same index to the same crop, and the same crops are dropped."""
    pages = [(2, ["a", "b"]), (0, []), (3, ["c", "d", "e"]), (1, ["f"])]  # (found, kept items)
    outcomes = set()
    for order in itertools.permutations(range(len(pages))):
        numbering = PrefixNumbering[str](len(pages), cap=5)
        kept: dict[str, int] = {}
        dropped: list[str] = []
        for position in order:
            found, items = pages[position]
            for released in numbering.settle(position, found, [f"{position}{i}" for i in items]):
                kept.update({item: index for index, item in released.kept})
                dropped += released.dropped
        assert numbering.complete
        outcomes.add((tuple(sorted(kept.items())), tuple(sorted(dropped))))
    assert len(outcomes) == 1


def test_a_page_cannot_be_settled_twice_or_out_of_range() -> None:
    numbering = PrefixNumbering[str](2, cap=5)
    numbering.settle(0, 0, [])
    for position in (0, 2, -1):
        with pytest.raises(ValueError):
            numbering.settle(position, 0, [])


# --- the stage states --------------------------------------------------------------------------


def test_a_stage_is_active_from_its_first_work_and_done_when_that_and_the_stages_before_end() -> (
    None
):
    tracker = StageTracker()
    assert set(tracker.states().values()) == {StageState.PENDING}
    tracker.start(StageName.RENDER)
    tracker.start(StageName.SEGMENT)
    assert [s.value for s in tracker.states().values()] == [
        "active",
        "active",
        "pending",
        "pending",
    ]
    tracker.finish(StageName.SEGMENT)  # its work is over, but the render is not
    assert tracker.states()[StageName.SEGMENT] is StageState.ACTIVE
    tracker.finish(StageName.RENDER)
    assert [s.value for s in tracker.states().values()] == ["done", "done", "pending", "pending"]


def test_a_stage_with_no_work_goes_straight_to_done() -> None:
    """A job without structures: recognise and check never start anything."""
    tracker = StageTracker()
    tracker.start(StageName.RENDER)
    tracker.start(StageName.SEGMENT)
    for name in PIPELINE_STAGES:
        tracker.finish(name)
    assert set(tracker.states().values()) == {StageState.DONE}


def test_a_later_stage_cannot_be_done_while_an_earlier_one_is_not() -> None:
    tracker = StageTracker()
    tracker.start(StageName.RENDER)
    tracker.start(StageName.RECOGNISE)
    tracker.finish(StageName.RECOGNISE)
    tracker.finish(StageName.CHECK)
    states = tracker.states()
    assert states[StageName.RENDER] is StageState.ACTIVE
    assert states[StageName.RECOGNISE] is StageState.ACTIVE
    assert (
        states[StageName.CHECK] is StageState.ACTIVE
    )  # finished, but it waits for the ones before


def test_apply_changes_only_the_pipeline_stages_and_says_whether_anything_changed() -> None:
    stages: list[Stage] = initial_stages()
    tracker = StageTracker()
    assert tracker.apply(stages) is False
    tracker.start(StageName.RENDER)
    assert tracker.apply(stages) is True
    assert tracker.apply(stages) is False
    assert [s.state.value for s in stages] == ["done", "active", "pending", "pending", "pending"]


# --- what happens when -------------------------------------------------------------------------


async def test_a_page_goes_to_seg_before_the_next_page_is_rendered(make_rig: RigFactory) -> None:
    """Render page by page: page 3 is still being rendered, pages 0 to 2 have been sent to seg and
    their structures to ocsr, and the record shows render and segment both active."""
    release = asyncio.Event()
    rendering: list[int] = []

    class Gated:
        async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]:
            if index >= 3:
                rendering.append(index)
                await release.wait()
            return await ThreadRenderer()(pdf, index, out)

    backend = FakeBackend(layout={0: 1, 1: 1, 2: 1, 3: 1, 4: 1})
    rig = make_rig(backend=backend)
    rig.deps.renderer = Gated()
    task = asyncio.create_task(rig.run(await rig.pdf_job(5)))
    await wait_until(lambda: backend.count("seg") == 3 and backend.count("ocsr") == 3, timeout=20)
    assert rendering == [3]  # one page at a time (RENDER_CONCURRENCY), and it is held
    assert sorted(int(p["page_index"]) for p in backend.parts_of("seg")) == [0, 1, 2]
    await wait_until(lambda: any(s[:3] == ("active",) * 3 for s in seen_states(rig)), timeout=20)
    assert_legal_progress(rig.store.saves)
    release.set()
    job = await asyncio.wait_for(task, 20)
    assert job.status is JobStatus.DONE and len(job.results) == 5


async def test_ocsr_starts_on_the_first_pages_while_the_last_page_is_still_being_segmented(
    make_rig: RigFactory,
) -> None:
    release = asyncio.Event()

    async def hold_the_last_page(parts: dict[str, bytes]) -> None:
        if parts["page_index"] == b"9":
            await release.wait()

    # 10 pages of 3 structures, cap 20: the 20 that are kept are on pages 0 to 6.
    layout = {page: 3 for page in range(10)}
    backend = FakeBackend(layout=layout, pauses={"seg": hold_the_last_page})
    rig = make_rig(backend=backend, seg_concurrency=2)
    task = asyncio.create_task(rig.run(await rig.pdf_job(10)))

    await wait_until(
        lambda: (
            backend.count("ocsr") == 20
            and backend.count("seg") == 10
            and backend.active["seg"] == 1
        ),
        timeout=30,
    )  # page 9 is the one call seg is holding
    asked = {crop_id(parts["image"]) for parts in backend.parts_of("ocsr")}
    assert asked == set(oracle(layout, 20)[0])
    assert backend.count("seg") == 10 and backend.active["seg"] == 1  # page 9 is being segmented
    assert rig.store.finishes == []
    # render is over; segment, recognise and check are going, all at once
    await wait_until(lambda: ("done", "active", "active", "active") in seen_states(rig), timeout=20)
    assert_legal_progress(rig.store.saves)

    release.set()
    job = await asyncio.wait_for(task, 30)
    kept, found = oracle(layout, 20)
    assert job.status is JobStatus.DONE
    assert shown(job) == want(kept)
    assert (job.structures_found, job.structures_dropped, job.truncated) == (found, 10, True)
    assert backend.count("ocsr") == 20  # nothing was spent on the ten that were dropped
    assert not list(rig.file(job, ".").glob("*.part"))


async def test_a_crop_waits_for_every_page_before_its_own_to_be_segmented(
    make_rig: RigFactory,
) -> None:
    """The prefix rule at work: pages 1 and 2 are segmented and on disk while page 0 is still
    with seg; none of their structures can be numbered, so none is sent to ocsr."""
    release = asyncio.Event()

    async def hold_the_first_page(parts: dict[str, bytes]) -> None:
        if parts["page_index"] == b"0":
            await release.wait()

    backend = FakeBackend(layout={0: 2, 1: 2, 2: 2}, pauses={"seg": hold_the_first_page})
    rig = make_rig(backend=backend, seg_concurrency=3)
    job = await rig.pdf_job(3)
    task = asyncio.create_task(rig.run(job))
    folder = rig.file(job, ".")
    await wait_until(lambda: len(list(folder.glob("seg-*.part"))) == 4, timeout=20)
    await asyncio.sleep(0.05)  # time enough for a crop to be sent on, were it allowed to
    assert backend.count("ocsr") == 0
    assert backend.active["seg"] == 1  # page 0

    release.set()
    done = await asyncio.wait_for(task, 20)
    kept, _found = oracle({0: 2, 1: 2, 2: 2}, 20)
    assert shown(done) == want(kept)
    # one crop at a time here, so ocsr was asked in the numbers' order
    assert [crop_id(p["image"]) for p in backend.parts_of("ocsr")] == kept


# --- the same results as one stage after the other ---------------------------------------------


async def scrambled(
    make_rig: RigFactory,
    layout: dict[int, int],
    *,
    cap: int = 20,
    seg_fails: set[int] | None = None,
    render_fails: set[int] | None = None,
    **settings: object,
) -> tuple[Rig, Job]:
    """Run a PDF of `len(layout)` pages with seg answering the LAST page first and the first
    page last, so that the order of arrival is the opposite of the order of the document."""
    pages = len(layout)
    delays = {page: 0.04 * (pages - 1 - page) for page in range(pages)}

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if int(parts["page_index"]) in failing else None

    failing = seg_fails or set()
    backend = FakeBackend(layout=layout, page_delays=delays, hooks={"seg": hook})
    rig = make_rig(api={"max_structures": cap}, backend=backend, seg_concurrency=pages, **settings)
    rig.deps.renderer = FailingPages(ThreadRenderer(), render_fails or set())
    job = await rig.run(await rig.pdf_job(pages))
    assert_legal_progress(rig.store.saves)
    no_slot_is_held(rig)
    return rig, job


async def test_the_cap_crossing_mid_page_cuts_where_the_document_order_says(
    make_rig: RigFactory,
) -> None:
    layout = {0: 4, 1: 4, 2: 4}
    rig, job = await scrambled(make_rig, layout, cap=6)
    # [(0,0) .. (0,3), (1,0), (1,1)] are kept; (1,2), (1,3) and all of page 2 are dropped.
    assert shown(job) == want([(0, 0), (0, 1), (0, 2), (0, 3), (1, 0), (1, 1)])
    assert (job.structures_found, job.structures_dropped, job.truncated) == (12, 6, True)
    assert codes(job) == ["structures_truncated"]
    assert job.warnings[0].message == "12 structures were found. Only the first 6 were processed."
    assert {crop_id(p["image"]) for p in rig.backend.parts_of("ocsr")} == set(oracle(layout, 6)[0])
    assert not list(rig.file(job, ".").glob("*.part"))
    assert not rig.file(job, "crop-6.png").exists()


async def test_a_page_seg_failed_on_in_the_middle_is_skipped_and_the_numbers_close_up(
    make_rig: RigFactory,
) -> None:
    rig, job = await scrambled(make_rig, {0: 2, 1: 3, 2: 2}, seg_fails={1})
    assert shown(job) == want([(0, 0), (0, 1), (2, 0), (2, 1)])  # page 2 is 2 and 3, not 5 and 6
    assert codes(job) == ["page_segmentation_failed"] and "Page 2" in job.warnings[0].message
    assert (job.structures_found, job.structures_dropped, job.truncated) == (4, 0, False)


async def test_a_page_that_was_not_rendered_in_the_middle_is_skipped_the_same_way(
    make_rig: RigFactory,
) -> None:
    rig, job = await scrambled(make_rig, {0: 2, 1: 3, 2: 2}, render_fails={1})
    assert shown(job) == want([(0, 0), (0, 1), (2, 0), (2, 1)])
    assert codes(job) == ["page_render_failed"] and "Page 2" in job.warnings[0].message
    assert sorted(int(p["page_index"]) for p in rig.backend.parts_of("seg")) == [0, 2]


async def test_a_page_with_no_structures_takes_no_number(make_rig: RigFactory) -> None:
    rig, job = await scrambled(make_rig, {0: 2, 1: 0, 2: 0, 3: 1})
    assert shown(job) == want([(0, 0), (0, 1), (3, 0)])
    assert codes(job) == []


async def test_a_failed_page_before_the_cap_is_crossed_moves_the_cut(make_rig: RigFactory) -> None:
    layout = {0: 3, 1: 3, 2: 3}
    rig, job = await scrambled(make_rig, layout, cap=4, seg_fails={0})
    kept, found = oracle(layout, 4, skipped={0})
    assert shown(job) == want(kept) == want([(1, 0), (1, 1), (1, 2), (2, 0)])
    assert (job.structures_found, job.structures_dropped) == (found, 2)


async def test_the_single_image_fallback_is_unchanged_and_waits_for_seg(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    job = await rig.run(await rig.image_job([(123, 77)]))
    assert shown(job) == [(0, 0, "CCO")]
    assert (job.results[0].bbox, job.structures_found, job.structures_dropped) == (
        (0, 0, 123, 77),
        1,
        0,
    )
    assert rig.backend.count("seg") == 1 and rig.backend.count("ocsr") == 1
    progress = assert_legal_progress(rig.store.saves)
    # the whole image is not recognised before seg has answered with nothing: segment is done
    # whenever recognise has started
    assert all(states[1] == "done" for states in progress if states[2] != "pending")


async def test_the_timeout_limit_of_ocsr_fails_the_job_as_before(make_rig: RigFactory) -> None:
    def times_out(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise OCSR_TIMEOUT

    backend = FakeBackend(layout={0: 3, 1: 3, 2: 3}, hooks={"ocsr": times_out})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(3))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.SERVICE_UNAVAILABLE.message
    assert job.results == []
    # two crops that gave up, then the third timeout ends the job: the same list as with the
    # stages one after the other
    assert codes(job) == ["recognition_failed", "recognition_failed", "service_unavailable"]
    assert len(rig.store.finishes) == 1
    assert 3 <= backend.count("ocsr") <= 4
    assert_legal_progress(rig.store.saves)
    no_slot_is_held(rig)


async def test_timeouts_below_the_limit_spoil_their_crops_and_nothing_else(
    make_rig: RigFactory,
) -> None:
    layout = {0: 2, 1: 2}

    def times_out_twice(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        if crop_id(parts["image"]) in {(0, 1), (1, 0)}:
            raise OCSR_TIMEOUT
        return None

    rig = make_rig(backend=FakeBackend(layout=layout, hooks={"ocsr": times_out_twice}))
    job = await rig.run(await rig.pdf_job(2))
    assert job.status is JobStatus.DONE
    assert [r.valid for r in job.results] == [True, False, False, True]
    assert [r.smiles for r in job.results] == [smiles_for(0, 0), "", "", smiles_for(1, 1)]
    assert codes(job) == ["recognition_failed"] * 2


# --- the order of the warnings ------------------------------------------------------------------


async def test_warnings_read_as_they_did_with_one_stage_after_the_other(
    make_rig: RigFactory,
) -> None:
    """Crop 1 of page 0 fails in ocsr long before seg gives up on page 2, and the cap is crossed at
    the very end: the list is still render, segment, recognise (the stage order), not the order
    in which things happened."""

    async def hold_page_two(parts: dict[str, bytes]) -> None:
        if parts["page_index"] == b"2":  # until the three crops that can be sent have been answered
            await wait_until(
                lambda: backend.count("ocsr") == 3 and backend.active["ocsr"] == 0, timeout=20
            )
            await asyncio.sleep(0.05)

    def seg(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if parts["page_index"] == b"2" else None

    def ocsr(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if crop_id(parts["image"]) == (0, 1) else None

    backend = FakeBackend(
        layout={0: 2, 1: 2, 3: 2}, pauses={"seg": hold_page_two}, hooks={"seg": seg, "ocsr": ocsr}
    )
    rig = make_rig(api={"max_structures": 3}, backend=backend, seg_concurrency=2)
    rig.deps.renderer = FailingPages(ThreadRenderer(), {4})
    job = await rig.run(await rig.pdf_job(5))
    assert codes(job) == [
        "page_render_failed",
        "page_segmentation_failed",
        "structures_truncated",
        "recognition_failed",
    ]
    assert (job.structures_found, job.structures_dropped) == (6, 3)


# --- cancellation ------------------------------------------------------------------------------


def held_everywhere(backend: FakeBackend, release: asyncio.Event) -> None:
    async def hold(_parts: dict[str, bytes]) -> None:
        await release.wait()

    backend.pauses.update({"seg": hold, "ocsr": hold, "cls": hold})


async def test_a_job_cancelled_in_the_middle_of_the_overlap_holds_no_slot(
    make_rig: RigFactory,
) -> None:
    """The worker is stopping: seg is busy with later pages, ocsr and cls with the first crops,
    and a render has not even started. Everything is cancelled and every slot is given back."""
    release = asyncio.Event()  # never set: the calls only end by being cancelled
    backend = FakeBackend(layout={page: 3 for page in range(6)})
    rig = make_rig(backend=backend, seg_concurrency=2, ocsr_concurrency=2, cls_concurrency=2)
    seg_pool = rig.deps.services._caller._pools["seg"]

    async def hold_later_pages(parts: dict[str, bytes]) -> None:
        if int(parts["page_index"]) >= 2:
            await release.wait()

    async def hold(_parts: dict[str, bytes]) -> None:
        await release.wait()

    backend.pauses.update({"seg": hold_later_pages, "ocsr": hold, "cls": hold})
    job = await rig.pdf_job(6)
    task = asyncio.create_task(run_pipeline(job.id, rig.deps))
    await wait_until(
        lambda: backend.active["seg"] == 2 and backend.active["ocsr"] == 2, timeout=20
    )  # page 2 and 3 at seg, two crops of the first pages at ocsr
    await wait_until(lambda: backend.active["cls"] == 2, timeout=20)
    assert seg_pool.waiting == 0  # the job asks for what the replicas take, no more
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    no_slot_is_held(rig)
    assert rig.store.finishes == []  # not finished: arq runs it again
    stored = await rig.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.RUNNING
    assert_legal_progress(rig.store.saves)


async def test_a_failure_in_the_middle_of_the_overlap_cancels_the_rest_and_holds_no_slot(
    make_rig: RigFactory,
) -> None:
    release = asyncio.Event()

    async def hold_last_pages(parts: dict[str, bytes]) -> None:
        if int(parts["page_index"]) >= 3:
            await release.wait()

    def times_out(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise OCSR_TIMEOUT

    backend = FakeBackend(
        layout={page: 3 for page in range(6)},
        pauses={"seg": hold_last_pages},
        hooks={"ocsr": times_out},
    )
    rig = make_rig(backend=backend, seg_concurrency=2)
    job = await rig.run(await rig.pdf_job(6))
    assert job.status is JobStatus.FAILED and job.error == errors.SERVICE_UNAVAILABLE.message
    assert backend.active["seg"] == 0  # the page seg was still busy with was cancelled
    no_slot_is_held(rig)


async def test_the_deadline_of_the_job_cancels_everything_in_flight(make_rig: RigFactory) -> None:
    release = asyncio.Event()
    backend = FakeBackend(layout={page: 2 for page in range(4)})
    held_everywhere(backend, release)
    rig = make_rig(backend=backend, pipeline_timeout_seconds=0.5)
    job = await rig.run(await rig.pdf_job(4))
    assert job.status is JobStatus.FAILED and job.error == errors.TIMEOUT.message
    assert backend.active["seg"] == 0
    no_slot_is_held(rig)


# --- when a stage may say it is done -----------------------------------------------------------


async def test_a_stage_is_not_done_while_its_work_is_not(make_rig: RigFactory) -> None:
    """At every save: render done means every page is on disk, segment done means every page has
    been answered, recognise done means every crop that will be recognised has been (and none is
    running). With page 2 answering after the
    crops of page 0 are done, a recognise stage that is flagged done when it has nothing in
    flight, instead of when it has nothing left to do, would show done too early."""
    layout = {0: 2, 1: 2, 2: 2}
    backend = FakeBackend(layout=layout, page_delays={1: 0.05, 2: 0.1})
    rig = make_rig(backend=backend, seg_concurrency=3)
    seen: list[tuple[tuple[str, ...], int, int, int, int, int]] = []
    original = rig.store.save

    async def watching(job: Job) -> None:
        if job.status is JobStatus.RUNNING:
            states = tuple(stage.state.value for stage in job.stages[1:])
            rendered = len(list(rig.file(job, ".").glob("page-*.png")))
            seen.append(
                (
                    states,
                    rendered,
                    backend.count("seg"),
                    backend.active["seg"],
                    backend.count("ocsr"),
                    backend.active["ocsr"],
                )
            )
        await original(job)

    rig.store.save = watching  # type: ignore[method-assign]
    job = await rig.run(await rig.pdf_job(3))
    assert len(job.results) == 6
    for states, rendered, seg_started, seg_active, ocsr_started, ocsr_active in seen:
        if states[0] == "done":
            assert rendered == 3, states
        if states[1] == "done":
            assert (seg_started, seg_active) == (3, 0), states
        if states[2] == "done":
            assert (ocsr_started, ocsr_active) == (6, 0), states
    assert any(states[2] == "done" for states, *_ in seen)  # the test saw the stage finish
    assert_legal_progress(rig.store.saves)


# --- results against the oracle ----------------------------------------------------------------


@pytest.mark.parametrize("seed", range(30))
async def test_random_jobs_give_the_results_the_document_order_says(
    make_rig: RigFactory, seed: int
) -> None:
    rnd = random.Random(seed)  # noqa: S311 (test data, not a secret)
    pages = rnd.randint(1, 7)
    layout = {page: rnd.choice([0, 0, 1, 2, 3, 5, 9]) for page in range(pages)}
    cap = rnd.choice([1, 3, 5, 20])
    render_fails = {p for p in range(pages) if rnd.random() < 0.2}
    seg_fails = {p for p in range(pages) if rnd.random() < 0.2}
    delays = {page: rnd.choice([0.0, 0.01, 0.03, 0.06]) for page in range(pages)}

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if int(parts["page_index"]) in seg_fails else None

    backend = FakeBackend(layout=layout, page_delays=delays, hooks={"seg": hook})
    rig = make_rig(api={"max_structures": cap}, backend=backend, seg_concurrency=rnd.randint(1, 3))
    rig.deps.renderer = FailingPages(ThreadRenderer(), render_fails)
    job = await rig.run(await rig.pdf_job(pages))

    rendered = set(range(pages)) - render_fails
    searched = rendered - seg_fails
    expected_warnings = ["page_render_failed"] * len(render_fails)
    expected_warnings += ["page_segmentation_failed"] * len(rendered & seg_fails)
    if not rendered:
        assert job.status is JobStatus.FAILED
        assert codes(job) == [*expected_warnings, "render_failed"]
    elif not searched:
        assert job.status is JobStatus.FAILED
        assert codes(job) == [*expected_warnings, "segmentation_failed"]
    else:
        kept, found = oracle(layout, cap, skipped=render_fails | seg_fails)
        assert job.status is JobStatus.DONE
        assert shown(job) == want(kept)
        assert (job.structures_found, job.structures_dropped) == (found, found - len(kept))
        assert job.truncated is (found > cap)
        truncated = ["structures_truncated"] if found > cap else []
        assert codes(job) == [*expected_warnings, *truncated]
        assert backend.count("ocsr") == len(kept)
        assert not list(rig.file(job, ".").glob("*.part"))
    assert_legal_progress(rig.store.saves)
    no_slot_is_held(rig)


# --- how much of one job is at a service at once ------------------------------------------------


async def test_a_job_has_no_more_pages_at_seg_than_seg_takes(make_rig: RigFactory) -> None:
    """All six pages are rendered and waiting; two are with seg (its slots) and the other four wait
    in the job, not in the dispatcher's queue, where they would be ahead of other jobs' calls."""
    release = asyncio.Event()

    async def hold(_parts: dict[str, bytes]) -> None:
        await release.wait()

    backend = FakeBackend(layout={page: 1 for page in range(6)}, pauses={"seg": hold})
    rig = make_rig(backend=backend, seg_concurrency=2)
    job = await rig.pdf_job(6)
    task = asyncio.create_task(rig.run(job))
    folder = rig.file(job, ".")
    await wait_until(
        lambda: backend.active["seg"] == 2 and len(list(folder.glob("page-*.png"))) == 6, timeout=20
    )
    seg = rig.deps.services._caller._pools["seg"]
    assert seg.waiting == 0 and backend.count("seg") == 2
    release.set()
    assert (await asyncio.wait_for(task, 20)).status is JobStatus.DONE
    assert backend.peak["seg"] == 2


async def test_a_job_has_no_more_crops_at_ocsr_than_the_services_take(make_rig: RigFactory) -> None:
    release = asyncio.Event()

    async def hold(_parts: dict[str, bytes]) -> None:
        await release.wait()

    backend = FakeBackend(layout={0: 6}, pauses={"ocsr": hold})
    rig = make_rig(backend=backend, ocsr_concurrency=2, cls_concurrency=1)
    job = await rig.pdf_job(1)
    task = asyncio.create_task(rig.run(job))
    await wait_until(
        lambda: backend.active["ocsr"] == 2 and rig.file(job, "crop-5.png").exists(), timeout=20
    )
    assert rig.deps.services._caller._pools["ocsr"].waiting == 0
    assert backend.count("ocsr") == 2  # the other four crops are waiting in the job
    release.set()
    assert (await asyncio.wait_for(task, 20)).status is JobStatus.DONE
    assert backend.peak["ocsr"] == 2


class HeldRenderer:
    """Renders for real, except that from page `held_from` on it waits for `release` first."""

    def __init__(self, release: asyncio.Event, held_from: int = 0) -> None:
        self.release = release
        self.held_from = held_from
        self.started: list[tuple[str, int]] = []  # (job folder, page) of every render that began

    async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]:
        self.started.append((out.parent.name, index))
        if index >= self.held_from:
            await self.release.wait()
        return await ThreadRenderer()(pdf, index, out)


async def test_render_concurrency_is_the_number_of_pages_rendered_at_once(
    make_rig: RigFactory,
) -> None:
    active = peak = 0

    class Counting:
        async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
                return await ThreadRenderer()(pdf, index, out)
            finally:
                active -= 1

    rig = make_rig(backend=FakeBackend(layout={}), render_concurrency=2)
    rig.deps.renderer = Counting()
    job = await rig.run(await rig.pdf_job(6))
    assert job.status is JobStatus.DONE and peak == 2


async def test_a_job_does_not_queue_all_its_pages_for_the_shared_renderer(
    make_rig: RigFactory,
) -> None:
    """Rendering is shared by every job in the process, one page at a time in turn. A 4-page job
    holding the renderer must not have all its pages in line ahead of a 1-page job that arrives."""
    release = asyncio.Event()
    renderer = HeldRenderer(release)
    rig = make_rig(backend=FakeBackend(layout={}))
    rig.deps.renderer = renderer
    big, small = await rig.pdf_job(4, ip="big"), await rig.pdf_job(1, ip="small")
    first = asyncio.create_task(rig.run(big))
    await wait_until(lambda: len(renderer.started) == 1, timeout=20)
    second = asyncio.create_task(rig.run(small))
    await wait_until(lambda: any(saved.id == small.id for saved in rig.store.saves), timeout=20)
    await asyncio.sleep(0.05)  # the small job's page is in line for the renderer by now
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 30)
    assert renderer.started[1][0] == small.id  # the next page rendered after the big job's first
    assert [job for job, _ in renderer.started].count(big.id) == 4


async def test_no_more_than_four_structures_are_checked_with_rdkit_at_once(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = threading.Lock()
    running = peak = 0

    def slow(smiles: str) -> object:
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            time.sleep(0.2)
            return analyse_smiles(smiles)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(pipeline, "analyse_smiles", slow)
    rig = make_rig(backend=FakeBackend(layout={0: 9}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE and len(job.results) == 9
    assert peak == pipeline.CHEMISTRY_THREADS == 4


async def test_a_replica_that_joins_during_the_job_is_used_by_the_crops_still_to_come(
    make_rig: RigFactory,
) -> None:
    """The room a job has at a service is looked up when a crop is about to start, not once at the
    start of the run: ocsr has one replica when the job begins and two when its first crop is
    done, and the rest of the crops use both."""
    old, new = "10.0.2.1", "10.0.2.2"
    resolver = FakeResolver(
        {"seg.test": ["10.0.1.1"], "ocsr.test": [old], "cls.test": ["10.0.3.1"]}
    )
    hosts = {"10.0.1.1": "seg", old: "ocsr", new: "ocsr", "10.0.3.1": "cls"}
    first_call = asyncio.Event()
    calls = 0

    async def hold_the_first(_parts: dict[str, bytes]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            await first_call.wait()

    backend = FakeBackend(
        layout={0: 6},
        hosts=hosts,
        pauses={"ocsr": hold_the_first},
        service_delays={"ocsr": 0.05},  # long enough for two calls to overlap
    )
    rig = make_rig(backend=backend, resolver=resolver)
    job = await rig.pdf_job(1)
    task = asyncio.create_task(rig.run(job))
    await wait_until(
        lambda: backend.active["ocsr"] == 1 and rig.file(job, "crop-5.png").exists(), 20
    )
    ocsr = rig.deps.services._caller._pools["ocsr"]
    resolver.answers["ocsr.test"] = [old, new]  # a second replica starts
    await ocsr.refresh(force=True)
    assert await ocsr.capacity() == 2
    first_call.set()
    done = await asyncio.wait_for(task, 20)
    assert done.status is JobStatus.DONE and len(done.results) == 6
    assert backend.peak["ocsr"] == 2 and backend.calls_to(new) > 0


async def test_segment_is_not_active_before_a_page_has_been_rendered(make_rig: RigFactory) -> None:
    """Page 0 cannot be rendered and page 1 is still being rendered: nothing is with seg, so the
    segment stage has not started (a page that failed is not work for seg)."""
    release = asyncio.Event()
    renderer = HeldRenderer(release, held_from=1)
    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1}))
    rig.deps.renderer = FailingPages(renderer, {0})
    job = await rig.pdf_job(2)
    task = asyncio.create_task(rig.run(job))
    await wait_until(lambda: renderer.started == [(job.id, 1)], timeout=20)
    await asyncio.sleep(0.05)  # page 0's failure has been dealt with and saved, if it is saved
    assert rig.backend.count("seg") == 0
    assert {states[1] for states in seen_states(rig)} == {"pending"}
    stored = await rig.store.get(job.id)
    assert stored is not None and stage_state(stored, StageName.SEGMENT) is StageState.PENDING
    release.set()
    done = await asyncio.wait_for(task, 20)
    assert done.status is JobStatus.DONE and [r.page_index for r in done.results] == [1]


# --- the record at the moments that matter ---------------------------------------------------


async def test_the_structure_count_and_the_end_of_segment_are_stored_as_soon_as_seg_is_through(
    make_rig: RigFactory,
) -> None:
    """Every crop is held at ocsr, so no other stage changes; the record is nevertheless written
    when the last page is segmented, since that is when the count is known."""
    release = asyncio.Event()

    async def hold(_parts: dict[str, bytes]) -> None:
        await release.wait()

    rig = make_rig(backend=FakeBackend(layout={0: 2, 1: 3}, pauses={"ocsr": hold}))
    job = await rig.pdf_job(2)
    task = asyncio.create_task(rig.run(job))

    async def stored_when_segment_is_done() -> Job:
        while True:
            stored = await rig.store.get(job.id)
            if stored is not None and stage_state(stored, StageName.SEGMENT) is StageState.DONE:
                return stored
            await asyncio.sleep(0.005)

    stored = await asyncio.wait_for(stored_when_segment_is_done(), 10)
    assert stored.status is JobStatus.RUNNING and stored.results == []
    assert (stored.structures_found, stored.structures_dropped) == (5, 0)
    assert stage_state(stored, StageName.RECOGNISE) is StageState.ACTIVE
    release.set()
    assert (await asyncio.wait_for(task, 20)).status is JobStatus.DONE


async def test_a_job_that_fails_on_ocsr_before_seg_is_through_has_no_structure_count(
    make_rig: RigFactory,
) -> None:
    """What a failed job's record holds now that the stages overlap (README, "When something goes
    wrong"): what was known when it failed. Pages 1 and 2 are still with seg, so nothing is known
    about the structures of the job: no count, no cap warning, no warning about those pages."""
    release = asyncio.Event()  # never set

    async def hold_later_pages(parts: dict[str, bytes]) -> None:
        if int(parts["page_index"]) >= 1:
            await release.wait()

    def times_out(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise OCSR_TIMEOUT

    backend = FakeBackend(
        layout={0: 4, 1: 3, 2: 3}, pauses={"seg": hold_later_pages}, hooks={"ocsr": times_out}
    )
    rig = make_rig(backend=backend, seg_concurrency=3, api={"max_structures": 5})
    job = await rig.run(await rig.pdf_job(3))
    assert job.status is JobStatus.FAILED and job.error == errors.SERVICE_UNAVAILABLE.message
    assert job.results == []
    assert (job.structures_found, job.structures_dropped, job.truncated) == (0, 0, False)
    assert codes(job) == ["recognition_failed", "recognition_failed", "service_unavailable"]
    states = [stage.state.value for stage in job.stages[1:]]
    assert states[1:] == ["active", "active", "active"]  # where they were; render may be over
    assert states[0] in ("active", "done")
    no_slot_is_held(rig)


async def test_seg_failing_after_a_recognition_failure_leaves_that_warning_in_the_record(
    make_rig: RigFactory,
) -> None:
    """Page 0's only crop fails in ocsr; only then does seg turn out to be unreachable for page 1.
    The job fails for seg's sake (`service_unavailable`) as it always did, but the record also
    holds the warning of the crop that had been recognised in the meantime."""

    async def after_the_first_crop(parts: dict[str, bytes]) -> None:
        if parts["page_index"] == b"1":
            await wait_until(
                lambda: backend.count("ocsr") == 1 and backend.active["ocsr"] == 0, timeout=20
            )
            await asyncio.sleep(0.05)

    def refuse_page_one(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        if parts["page_index"] == b"1":
            raise httpx.ConnectError("connection refused")
        return None

    def ocsr_crashes(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        return httpx.Response(500, json={"detail": "inference failed"})

    backend = FakeBackend(
        layout={0: 1, 1: 1},
        pauses={"seg": after_the_first_crop},
        hooks={"seg": refuse_page_one, "ocsr": ocsr_crashes},
    )
    rig = make_rig(backend=backend, seg_concurrency=2)
    job = await rig.run(await rig.pdf_job(2))
    assert job.status is JobStatus.FAILED and job.error == errors.SERVICE_UNAVAILABLE.message
    assert codes(job) == ["recognition_failed", "service_unavailable"]
    assert (job.results, job.structures_found) == ([], 0)
    no_slot_is_held(rig)
