"""The pipeline end to end, with fake services: stages, ordering, limits, fallbacks, failures."""

from __future__ import annotations

import asyncio
import io
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from PIL import Image

from decimer_api.chemistry import analyse_smiles
from decimer_api.models import Job, JobStatus, JobWarning, StageState
from decimer_api.worker import errors, render
from decimer_api.worker.pipeline import run_pipeline
from decimer_api.worker.render import ThreadRenderer
from tests.factories import make_corrupt_pdf
from tests.worker.conftest import Rig, all_done, assert_legal_progress, wait_until
from tests.worker.fakes import (
    FailingPages,
    FakeBackend,
    bbox_for,
    crop_id,
    json_response,
    make_crop,
    png_size,
    smiles_for,
)

RigFactory = Callable[..., Rig]


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


# --- a whole job -------------------------------------------------------------------------------


async def test_a_three_page_pdf_goes_through_every_stage_in_order(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2, 1: 0, 2: 3}))
    job = await rig.run(await rig.pdf_job(3))

    assert job.status is JobStatus.DONE
    assert job.error is None
    assert all_done(job)
    # The stages overlap, so the record shows them active together; what is fixed is that it is
    # saved at every change, that every state it shows is a legal one, and where it starts and ends.
    progress = assert_legal_progress(rig.store.saves)
    assert progress[0] == ("pending", "pending", "pending", "pending")  # mark_running
    assert progress[1] == (
        "active",
        "pending",
        "pending",
        "pending",
    )  # the first thing that happens
    assert progress[-1] == ("done", "done", "done", "done")
    for stage in range(4):  # the progress view can poll every one of them
        assert any(states[stage] == "active" for states in progress), stage
    # saved when a stage changes, not once per page or crop
    assert len([s for s in rig.store.saves if s.status is JobStatus.RUNNING]) <= 11
    assert len(rig.store.finishes) == 1


async def test_results_follow_reading_order_across_pages(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2, 1: 0, 2: 3}))
    job = await rig.run(await rig.pdf_job(3))

    expected = [(0, 0), (0, 1), (2, 0), (2, 1), (2, 2)]
    assert [r.index for r in job.results] == [0, 1, 2, 3, 4]
    assert [r.page_index for r in job.results] == [p for p, _ in expected]
    assert [r.smiles for r in job.results] == [smiles_for(p, k) for p, k in expected]
    assert [r.bbox for r in job.results] == [bbox_for(p, k) for p, k in expected]
    assert job.structures_found == 5
    assert job.structures_dropped == 0
    assert job.truncated is False


async def test_the_order_does_not_depend_on_which_page_answers_first(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 1, 1: 1, 2: 1}, page_delays={0: 0.06, 1: 0.03, 2: 0.0})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(3))
    assert [r.page_index for r in job.results] == [0, 1, 2]


async def test_the_result_carries_every_field(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.run(await rig.pdf_job(1))
    (result,) = job.results
    chemistry = analyse_smiles(smiles_for(0, 0))
    assert result.smiles == chemistry.smiles
    assert result.inchikey == chemistry.inchikey != ""
    assert result.molfile == chemistry.molfile and "V2000" in result.molfile
    assert result.svg == chemistry.svg and result.svg.startswith("<svg")
    assert result.valid is True
    assert result.is_structure is True
    assert result.confidence == 0.9  # the mean_confidence ocsr reported
    assert (result.name, result.name_tier) == (None, None)


async def test_pages_and_crops_are_written_where_the_media_endpoint_looks(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2, 2: 1}))
    job = await rig.run(await rig.pdf_job(3))
    for page in range(3):
        with Image.open(rig.file(job, f"page-{page}.png")) as image:
            assert image.size == (600, 600)  # 2 in at exactly 300 DPI
    assert rig.file(job, "crop-0.png").read_bytes() == make_crop(0, 0)
    assert rig.file(job, "crop-1.png").read_bytes() == make_crop(0, 1)
    assert rig.file(job, "crop-2.png").read_bytes() == make_crop(2, 0)
    assert not rig.file(job, "crop-3.png").exists()
    assert (rig.file(job, "input") / "document.pdf").is_file()  # the input is left alone
    leftovers = [p.name for p in rig.file(job, ".").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


async def test_seg_gets_each_page_exactly_as_it_was_rendered(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    job = await rig.run(await rig.pdf_job(3))
    sent = {int(p["page_index"]): p["image"] for p in rig.backend.parts_of("seg")}
    assert sorted(sent) == [0, 1, 2]
    for page, data in sent.items():
        assert data == rig.file(job, f"page-{page}.png").read_bytes()


async def test_a_page_image_job_is_copied_as_page_n(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1}))
    job = await rig.run(await rig.image_job([(300, 200), (250, 120)]))
    assert png_size(rig.file(job, "page-0.png").read_bytes()) == (300, 200)
    assert png_size(rig.file(job, "page-1.png").read_bytes()) == (250, 120)
    assert [r.page_index for r in job.results] == [0, 1]
    assert job.status is JobStatus.DONE


async def test_an_upload_that_was_cut_short_keeps_the_apis_warning(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    warning = JobWarning(code="pages_truncated", message="Only the first 1 pages were processed.")
    job = await rig.pdf_job(1, total_pages=4, warnings=[warning], truncated=True)
    done = await rig.run(job)
    assert done.truncated is True
    assert done.pages == 4 and done.pages_processed == 1
    assert codes(done) == ["pages_truncated"]


# --- the 20 structure cap --------------------------------------------------------------------


async def test_only_the_first_twenty_structures_are_recognised(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 9, 1: 9, 2: 7}))  # 25 in all
    job = await rig.run(await rig.pdf_job(3))

    assert len(job.results) == 20
    assert job.structures_found == 25
    assert job.structures_dropped == 5
    assert job.truncated is True
    assert "structures_truncated" in codes(job)
    kept = [(r.page_index, smiles_for(r.page_index, 0)) for r in job.results]
    assert [r.smiles for r in job.results][-2:] == [smiles_for(2, 0), smiles_for(2, 1)]
    assert kept[0][0] == 0
    # nothing was spent on the five that were dropped
    assert rig.backend.count("ocsr") == 20
    assert rig.backend.count("cls") == 20
    assert rig.file(job, "crop-19.png").is_file()
    assert not rig.file(job, "crop-20.png").exists()
    assert not list(rig.file(job, ".").glob("*.part"))  # the five that were dropped are gone
    sent = {crop_id(parts["image"]) for parts in rig.backend.parts_of("ocsr")}
    assert all(not (page == 2 and index >= 2) for page, index in sent)


async def test_the_cap_comes_from_the_settings(make_rig: RigFactory) -> None:
    rig = make_rig(api={"max_structures": 3}, backend=FakeBackend(layout={0: 2, 1: 2}))
    job = await rig.run(await rig.pdf_job(2))
    assert len(job.results) == 3
    assert (job.structures_found, job.structures_dropped) == (4, 1)


async def test_exactly_the_cap_is_not_truncated(make_rig: RigFactory) -> None:
    rig = make_rig(api={"max_structures": 4}, backend=FakeBackend(layout={0: 2, 1: 2}))
    job = await rig.run(await rig.pdf_job(2))
    assert len(job.results) == 4
    assert job.structures_dropped == 0
    assert job.truncated is False
    assert "structures_truncated" not in codes(job)


# --- the single-image fallback -----------------------------------------------------------------


async def test_one_image_with_nothing_found_is_recognised_whole(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    job = await rig.run(await rig.image_job([(123, 77)]))

    assert job.status is JobStatus.DONE
    (result,) = job.results
    assert (result.index, result.page_index, result.bbox) == (0, 0, (0, 0, 123, 77))
    assert result.smiles == "CCO"  # what the fake ocsr says about any whole image
    assert rig.backend.count("ocsr") == 1
    assert rig.file(job, "crop-0.png").read_bytes() == rig.file(job, "page-0.png").read_bytes()
    assert job.structures_found == 1


async def test_one_image_with_a_found_structure_is_not_recognised_whole(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.run(await rig.image_job([(123, 77)]))
    assert len(job.results) == 1
    assert job.results[0].bbox != (0, 0, 123, 77)
    assert rig.backend.count("ocsr") == 1


async def test_a_pdf_gets_no_fallback_even_with_one_page(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert job.results == []
    assert rig.backend.count("ocsr") == 0
    assert rig.backend.count("cls") == 0
    assert job.structures_found == 0


async def test_several_images_get_no_fallback(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    job = await rig.run(await rig.image_job([(123, 77), (140, 90)]))
    assert job.status is JobStatus.DONE
    assert job.results == []
    assert rig.backend.count("ocsr") == 0
    assert all_done(job)  # the stages still complete, so the progress view finishes


async def test_the_fallback_does_not_apply_when_segmentation_failed(make_rig: RigFactory) -> None:
    def broken(request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        return httpx.Response(500, json={"detail": "inference failed"})

    rig = make_rig(backend=FakeBackend(hooks={"seg": broken}))
    job = await rig.run(await rig.image_job([(123, 77)]))
    assert job.status is JobStatus.FAILED
    assert rig.backend.count("ocsr") == 0


async def test_a_big_image_is_downscaled_for_the_models_but_the_crop_keeps_its_size(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(model_input_max_pixels=100_000)
    job = await rig.run(await rig.image_job([(2000, 1500)]))
    assert png_size(rig.file(job, "crop-0.png").read_bytes()) == (2000, 1500)
    for service in ("ocsr", "cls"):
        width, height = png_size(rig.backend.parts_of(service)[0]["image"])
        assert width * height <= 100_000
        assert abs(width / height - 2000 / 1500) < 0.02
    # the page the seg service saw was not touched
    assert png_size(rig.backend.parts_of("seg")[0]["image"]) == (2000, 1500)


# --- a service fails on one crop ---------------------------------------------------------------


def fail_crop(
    page: int, index: int, response: httpx.Response
) -> Callable[..., httpx.Response | None]:
    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return response if crop_id(parts["image"]) == (page, index) else None

    return hook


async def test_an_ocsr_crash_on_one_crop_only_spoils_that_result(make_rig: RigFactory) -> None:
    hook = fail_crop(0, 1, httpx.Response(500, json={"detail": "inference failed"}))
    rig = make_rig(backend=FakeBackend(layout={0: 3}, hooks={"ocsr": hook}))
    job = await rig.run(await rig.pdf_job(1))

    assert job.status is JobStatus.DONE
    assert [r.valid for r in job.results] == [True, False, True]
    broken = job.results[1]
    assert (broken.smiles, broken.inchikey, broken.molfile, broken.svg) == ("", "", "", "")
    assert broken.confidence == 0.0
    assert broken.is_structure is True  # the classifier still answered
    assert codes(job) == ["recognition_failed"]
    assert "Structure 2" in job.warnings[0].message
    assert rig.backend.count("ocsr") == 3  # a 500 is not retried
    assert rig.file(job, "crop-1.png").is_file()  # the crop is still shown


async def test_a_cls_crash_leaves_the_classification_unknown(make_rig: RigFactory) -> None:
    hook = fail_crop(0, 0, httpx.Response(500, json={"detail": "inference failed"}))
    rig = make_rig(backend=FakeBackend(layout={0: 2}, hooks={"cls": hook}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [r.is_structure for r in job.results] == [None, True]
    assert [r.valid for r in job.results] == [True, True]
    assert codes(job) == ["classification_failed"]


async def test_both_failing_on_one_crop_is_still_one_bad_result(make_rig: RigFactory) -> None:
    bad = httpx.Response(500, json={"detail": "inference failed"})
    rig = make_rig(
        backend=FakeBackend(
            layout={0: 2}, hooks={"ocsr": fail_crop(0, 0, bad), "cls": fail_crop(0, 0, bad)}
        )
    )
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert (job.results[0].valid, job.results[0].is_structure) == (False, None)
    assert (job.results[1].valid, job.results[1].is_structure) == (True, True)
    assert sorted(codes(job)) == ["classification_failed", "recognition_failed"]


async def test_a_refused_crop_is_not_retried_and_leaves_a_warning(make_rig: RigFactory) -> None:
    refused = httpx.Response(422, json={"detail": "image too elongated"})
    rig = make_rig(backend=FakeBackend(layout={0: 2}, hooks={"ocsr": fail_crop(0, 0, refused)}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert job.results[0].valid is False
    assert rig.backend.count("ocsr") == 2  # one call per crop, no retry of the 422
    assert rig.sleep.delays == []
    assert codes(job) == ["recognition_failed"]
    assert "elongated" not in job.warnings[0].message  # service text stays in the log


async def test_a_busy_service_is_retried_after_retry_after_and_the_job_succeeds(
    make_rig: RigFactory,
) -> None:
    busy = httpx.Response(503, json={"detail": "busy"}, headers={"Retry-After": "2"})
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        return busy if seen["n"] == 1 else None

    rig = make_rig(backend=FakeBackend(layout={0: 1}, hooks={"ocsr": hook}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert job.results[0].valid is True
    assert rig.sleep.delays == [2.0]
    assert codes(job) == []


# --- a service is down ---------------------------------------------------------------------------


async def test_ocsr_staying_busy_fails_the_job_with_a_safe_message(make_rig: RigFactory) -> None:
    busy = httpx.Response(503, json={"detail": "busy"}, headers={"Retry-After": "1"})
    rig = make_rig(backend=FakeBackend(layout={0: 4}, hooks={"ocsr": lambda *_: busy}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.SERVICE_UNAVAILABLE.message
    assert job.results == []
    assert len(rig.store.finishes) == 1
    # a dead ocsr is noticed at once, not once per crop: at most the crops that were already in
    # flight try it (two, see `_recognise`), not all four
    assert rig.backend.count("ocsr") <= 2 * (rig.worker.busy_retries + 1)


async def test_seg_unreachable_fails_the_job_after_the_backoff(make_rig: RigFactory) -> None:
    def refuse(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        raise httpx.ConnectError("connection refused")

    rig = make_rig(backend=FakeBackend(hooks={"seg": refuse}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.SERVICE_UNAVAILABLE.message
    assert rig.sleep.delays[:2] == [0.5, 1.0]


async def test_a_dead_classifier_costs_the_job_nothing_but_the_flag(make_rig: RigFactory) -> None:
    busy = httpx.Response(503, json={"detail": "busy"})
    rig = make_rig(backend=FakeBackend(layout={0: 6}, hooks={"cls": lambda *_: busy}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [r.is_structure for r in job.results] == [None] * 6
    assert all(r.valid for r in job.results)
    assert codes(job).count("classifier_unavailable") == 1
    assert rig.backend.count("cls") <= 2 * (rig.worker.busy_retries + 1)  # not 6 x 4


async def test_seg_failing_on_one_page_skips_that_page(make_rig: RigFactory) -> None:
    def broken(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        return httpx.Response(500, json={}) if parts["page_index"] == b"1" else None

    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1, 2: 1}, hooks={"seg": broken}))
    job = await rig.run(await rig.pdf_job(3))
    assert job.status is JobStatus.DONE
    assert [r.page_index for r in job.results] == [0, 2]
    assert codes(job) == ["page_segmentation_failed"]
    assert "Page 2" in job.warnings[0].message


async def test_seg_failing_on_every_page_fails_the_job(make_rig: RigFactory) -> None:
    broken = httpx.Response(500, json={})
    rig = make_rig(backend=FakeBackend(layout={0: 1}, hooks={"seg": lambda *_: broken}))
    job = await rig.run(await rig.pdf_job(2))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.SEGMENTATION_FAILED.message


# --- the IUPAC name -------------------------------------------------------------------------------


async def test_without_a_chem_service_the_names_are_null(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}))
    job = await rig.run(await rig.pdf_job(1))
    assert [(r.name, r.name_tier) for r in job.results] == [(None, None)] * 2
    assert rig.backend.count("chem") == 0
    assert codes(job) == []


# --- RDKit ---------------------------------------------------------------------------------------


async def test_an_invalid_smiles_is_kept_as_text_with_no_picture(make_rig: RigFactory) -> None:
    def ocsr(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        return json_response({"smiles": "c1ccccc", "token_confidence": [], "mean_confidence": 0.77})

    rig = make_rig(backend=FakeBackend(layout={0: 1}, hooks={"ocsr": ocsr}))
    job = await rig.run(await rig.pdf_job(1))
    (result,) = job.results
    assert result.valid is False
    assert result.smiles == "c1ccccc"
    assert (result.inchikey, result.molfile, result.svg) == ("", "", "")
    assert result.confidence == 0.77
    assert result.is_structure is True
    assert job.status is JobStatus.DONE


async def test_an_empty_prediction_is_an_invalid_result(make_rig: RigFactory) -> None:
    def ocsr(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        return json_response({"smiles": "", "token_confidence": [], "mean_confidence": 0.0})

    rig = make_rig(backend=FakeBackend(layout={0: 1}, hooks={"ocsr": ocsr}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.results[0].valid is False
    assert job.results[0].molfile == ""


# --- finishing ------------------------------------------------------------------------------------


async def test_finish_is_called_once_with_the_final_state(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.run(await rig.pdf_job(1))
    assert len(rig.store.finishes) == 1
    assert rig.store.finishes[0].status is JobStatus.DONE
    assert await rig.store.active_count("client-a") == 0  # the slot is free again
    ttl = (job.expires_at - rig.clock()).total_seconds()
    assert ttl == rig.settings.job_ttl_seconds  # the one-hour countdown started


async def test_a_stage_that_blows_up_still_finishes_the_job_once_with_a_safe_message(
    make_rig: RigFactory,
) -> None:
    def boom(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise RuntimeError("secret detail at /data/jobs/abc: Traceback (most recent call last)")

    rig = make_rig(backend=FakeBackend(layout={0: 1}, hooks={"seg": boom}))
    job = await rig.run(await rig.pdf_job(1))

    assert len(rig.store.finishes) == 1
    assert job.status is JobStatus.FAILED
    assert job.error == errors.INTERNAL_ERROR.message
    for text in [job.error, *(w.message for w in job.warnings)]:
        assert "secret" not in text and "Traceback" not in text and "/data" not in text
    assert await rig.store.active_count("client-a") == 0


async def test_a_failure_in_the_check_stage_also_finishes_once(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimer_api.worker import pipeline

    def explode(_smiles: str) -> None:
        raise ValueError("rdkit exploded")

    monkeypatch.setattr(pipeline, "analyse_smiles", explode)
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert len(rig.store.finishes) == 1
    assert "rdkit" not in (job.error or "")


async def test_a_page_that_cannot_be_rendered_is_skipped_with_a_warning(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1, 2: 1}))
    rig.deps.renderer = FailingPages(ThreadRenderer(), {1})
    job = await rig.run(await rig.pdf_job(3))
    assert job.status is JobStatus.DONE
    assert [r.page_index for r in job.results] == [0, 2]  # the numbers are the document's
    assert codes(job) == ["page_render_failed"]
    assert "Page 2" in job.warnings[0].message
    assert rig.file(job, "page-0.png").is_file() and rig.file(job, "page-2.png").is_file()
    assert not rig.file(job, "page-1.png").exists()
    assert sorted(int(p["page_index"]) for p in rig.backend.parts_of("seg")) == [0, 2]


async def test_when_no_page_can_be_rendered_the_job_fails(make_rig: RigFactory) -> None:
    rig = make_rig()
    rig.deps.renderer = FailingPages(ThreadRenderer(), {0, 1})
    job = await rig.run(await rig.pdf_job(2))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.RENDER_FAILED.message
    assert rig.backend.calls == []


async def test_a_damaged_pdf_fails_with_render_failed(make_rig: RigFactory) -> None:
    rig = make_rig()
    job = await rig.run(await rig.pdf_job(1, data=make_corrupt_pdf()))
    assert job.status is JobStatus.FAILED
    assert job.error == errors.RENDER_FAILED.message
    assert len(rig.store.finishes) == 1
    assert rig.backend.calls == []


async def test_a_job_whose_input_is_missing_fails_cleanly(make_rig: RigFactory) -> None:
    rig = make_rig()
    job = await rig.image_job()
    for path in (rig.file(job, "input")).iterdir():
        path.unlink()
    done = await rig.run(job)
    assert done.status is JobStatus.FAILED
    assert done.error == errors.INPUT_MISSING.message


async def test_a_job_that_runs_too_long_is_stopped(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=1.0), pipeline_timeout_seconds=0.1)
    started = time.monotonic()
    job = await rig.run(await rig.pdf_job(1))
    assert time.monotonic() - started < 0.9
    assert job.status is JobStatus.FAILED
    assert job.error == errors.TIMEOUT.message
    assert len(rig.store.finishes) == 1


async def test_a_failed_job_says_why_in_a_machine_readable_warning_too(
    make_rig: RigFactory,
) -> None:
    rig = make_rig()
    job = await rig.run(await rig.pdf_job(1, data=make_corrupt_pdf()))
    assert "render_failed" in codes(job)


# --- cancellation and re-delivery -----------------------------------------------------------------


async def test_a_cancelled_job_is_not_finished_and_runs_again_cleanly(make_rig: RigFactory) -> None:
    """On shutdown arq cancels the job and runs it again later: the pipeline must leave the job
    running (not failed), and a second run must not inherit anything from the first."""
    slow = make_rig(backend=FakeBackend(layout={0: 3}, delay=5.0))
    job = await slow.pdf_job(1)
    task = asyncio.create_task(run_pipeline(job.id, slow.deps))
    await wait_until(lambda: slow.backend.count("seg") == 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert slow.store.finishes == []
    stored = await slow.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.RUNNING

    again = make_rig(backend=FakeBackend(layout={0: 3}))
    done = await again.run(job)
    assert done.status is JobStatus.DONE
    assert len(done.results) == 3
    assert done.structures_found == 3
    assert codes(done) == []
    assert all_done(done)
    assert len(again.store.finishes) == 1


async def test_a_job_that_is_already_finished_is_not_run_again(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    job = await rig.run(await rig.pdf_job(1))
    seg_calls = rig.backend.count("seg")
    await run_pipeline(job.id, rig.deps)  # arq delivered it twice
    assert rig.backend.count("seg") == seg_calls
    assert len(rig.store.finishes) == 1
    stored = await rig.store.get(job.id)
    assert stored is not None and stored.status is JobStatus.DONE


async def test_a_job_whose_record_expired_is_skipped(rig: Rig) -> None:
    await run_pipeline("0" * 32, rig.deps)
    assert rig.store.finishes == []
    assert rig.backend.calls == []


async def test_a_bad_job_id_never_reaches_the_file_system(rig: Rig) -> None:
    await run_pipeline("../../etc", rig.deps)
    assert rig.store.saves == [] and rig.backend.calls == []


# --- not blocking, not flooding -------------------------------------------------------------------


async def test_a_blocking_render_does_not_stop_the_event_loop(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_render(pdf: Path, index: int, out: Path, dpi: int = 300) -> tuple[int, int]:
        time.sleep(0.3)
        buffer = io.BytesIO()
        Image.new("RGB", (60, 60), "white").save(buffer, format="PNG")
        out.write_bytes(buffer.getvalue())
        return (60, 60)

    monkeypatch.setattr(render, "render_page", slow_render)
    rig = make_rig()
    job = await rig.pdf_job(1)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        done = await rig.run(job)
    finally:
        task.cancel()
    assert done.status is JobStatus.DONE
    assert ticks >= 15


async def test_calls_to_a_service_stay_within_its_limit(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 10}, delay=0.01)
    rig = make_rig(backend=backend, ocsr_concurrency=2, cls_concurrency=3)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert backend.peak["ocsr"] == 2
    assert backend.peak["cls"] <= 3


async def test_two_jobs_share_the_limits_of_the_process(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 5}, delay=0.01)
    rig = make_rig(backend=backend, ocsr_concurrency=1)
    first, second = await rig.pdf_job(1), await rig.pdf_job(1)
    await asyncio.gather(run_pipeline(first.id, rig.deps), run_pipeline(second.id, rig.deps))
    assert backend.peak["ocsr"] == 1
    for job in (first, second):
        stored = await rig.store.get(job.id, include_expired=True)
        assert stored is not None and stored.status is JobStatus.DONE


async def test_a_long_stage_keeps_the_record_alive(make_rig: RigFactory) -> None:
    """The record of a running job expires 20 minutes after its last save: a stage that takes
    longer than that must save in between."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}, delay=0.2), heartbeat_seconds=0.02)
    job = await rig.run(await rig.pdf_job(1))
    running_saves = [s for s in rig.store.saves if s.status is JobStatus.RUNNING]
    assert len(running_saves) >= 15  # the saves at a change of stage plus the beats
    assert_legal_progress(rig.store.saves)  # a beat in the middle of the overlap shows legal states
    saves_at_the_end = len(rig.store.saves)
    await asyncio.sleep(0.1)
    assert len(rig.store.saves) == saves_at_the_end  # the beat stopped before finish()
    stored = await rig.store.get(job.id, include_expired=True)
    assert stored is not None and stored.status is JobStatus.DONE


# --- what a running record may show ---------------------------------------------------------------


async def test_no_running_save_has_results_before_the_check_stage_is_done(
    make_rig: RigFactory,
) -> None:
    """Partly filled results would show half-made structures to a polling browser."""
    rig = make_rig(backend=FakeBackend(layout={0: 3, 1: 2}), heartbeat_seconds=0.005)
    rig.backend.delay = 0.02  # long enough for the heartbeat to save in every stage
    job = await rig.run(await rig.pdf_job(2))
    assert len(job.results) == 5

    running = [s for s in rig.store.saves if s.status is JobStatus.RUNNING]
    before_check_done = [s for s in running if s.stages[-1].state is not StageState.DONE]
    assert len(before_check_done) >= 8  # the stage saves and some beats
    assert [s.results for s in before_check_done] == [[]] * len(before_check_done)
    assert any(s.stages[-1].state is StageState.ACTIVE for s in running)  # check was observed
    assert len(running[-1].results) == 5  # the save that ends the check stage has them all
    found_early = [s for s in before_check_done if s.structures_found == 5]
    assert found_early  # the count is there from the segment stage on


async def test_a_failed_job_has_no_results(make_rig: RigFactory) -> None:
    busy = httpx.Response(503, json={"detail": "busy"})
    rig = make_rig(backend=FakeBackend(layout={0: 3}, hooks={"ocsr": lambda *_: busy}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED
    assert job.results == []
    assert all(s.results == [] for s in rig.store.saves)


# --- many images ----------------------------------------------------------------------------------


async def test_more_than_ten_images_keep_the_order_they_were_uploaded_in(
    make_rig: RigFactory,
) -> None:
    """The inputs are 0001.png, 0002.png, ...: page-N is the (N+1)th upload, not the Nth in an
    order that puts 10 before 2."""
    sizes = [(200 + 7 * n, 150) for n in range(12)]
    rig = make_rig(backend=FakeBackend(layout={n: 1 for n in range(12)}))
    job = await rig.image_job(sizes)
    done = await rig.run(job)
    assert done.status is JobStatus.DONE
    for number, size in enumerate(sizes):
        assert png_size(rig.file(done, f"page-{number}.png").read_bytes()) == size
    assert [r.page_index for r in done.results] == list(range(12))
    asked = [int(p["page_index"]) for p in rig.backend.parts_of("seg")]
    assert sorted(asked) == list(range(12))
    for number in range(12):  # each page was sent as the image of that position
        sent = next(p for p in rig.backend.parts_of("seg") if int(p["page_index"]) == number)
        assert png_size(sent["image"]) == sizes[number]
    assert rig.backend.count("ocsr") == 12  # twelve images are not "one image": no fallback needed


async def test_twelve_images_with_nothing_found_get_no_fallback(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    done = await rig.run(await rig.image_job([(100, 100)] * 12))
    assert done.results == []
    assert rig.backend.count("ocsr") == 0
