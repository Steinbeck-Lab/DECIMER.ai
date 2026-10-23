"""A job of several images never calls seg: every image is one structure, read whole.

Legacy never segments an upload of several images: each image goes to ocsr and cls as it is, and
structure N is image N. The worker copies that for a job of MORE THAN ONE image: no seg call, the
crop of an image is a hard link to the image, its box is the whole image, the structures are
numbered in image order by the same prefix rule and the same cap (`MAX_STRUCTURES`, 20) as every
other job. A single image keeps the single-image rule (D20: seg first) and a PDF is untouched.

The fake seg here would find TWO structures on every page, so a pipeline that still asked it would
give twice as many results, with seg's boxes, and the tests below could not pass.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from decimer_api.models import Job, JobStatus
from decimer_api.worker import pipeline
from tests.worker.conftest import Rig, all_done, assert_legal_progress
from tests.worker.fakes import FakeBackend, json_response, png_size

RigFactory = Callable[..., Rig]


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def sizes_of(count: int) -> list[tuple[int, int]]:
    """Image N is (100 + N) x 60 pixels: told apart by its width, in any order."""
    return [(100 + n, 60) for n in range(count)]


def width_of(parts: dict[str, bytes]) -> int:
    return png_size(parts["image"])[0]


def by_width(request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
    """An ocsr that answers image N (width 100 + N) with a chain of N + 1 carbons."""
    smiles = "C" * (width_of(parts) - 99)
    return json_response({"smiles": smiles, "token_confidence": [], "mean_confidence": 0.9})


def seeing_two_everywhere(count: int, **kwargs: Any) -> FakeBackend:
    """A backend whose seg finds two structures on each of `count` pages."""
    return FakeBackend(
        layout={page: 2 for page in range(count)}, hooks={"ocsr": by_width}, **kwargs
    )


def chain(image: int) -> str:
    return "C" * (image + 1)


# --- several images: no seg, one result per image, in image order -------------------------------


@pytest.mark.parametrize("count", [2, 5])
async def test_an_upload_of_several_images_never_calls_seg_and_gives_one_result_each(
    make_rig: RigFactory, count: int
) -> None:
    rig = make_rig(backend=seeing_two_everywhere(count))
    job = await rig.run(await rig.image_job(sizes_of(count)))

    assert job.status is JobStatus.DONE and all_done(job)
    assert rig.backend.count("seg") == 0
    assert [(r.index, r.page_index) for r in job.results] == [(n, n) for n in range(count)]
    assert [r.bbox for r in job.results] == [(0, 0, *size) for size in sizes_of(count)]
    assert [r.smiles for r in job.results] == [chain(n) for n in range(count)]
    assert (job.structures_found, job.structures_dropped, job.truncated) == (count, 0, False)
    assert codes(job) == []
    # ocsr and cls got every image as it is, once
    for service in ("ocsr", "cls"):
        assert rig.backend.count(service) == count
        got = sorted(width_of(parts) for parts in rig.backend.parts_of(service))
        assert got == sorted(width for width, _ in sizes_of(count))


async def test_the_crop_of_an_image_is_the_image_itself_not_a_copy(make_rig: RigFactory) -> None:
    rig = make_rig(backend=seeing_two_everywhere(3))
    job = await rig.run(await rig.image_job(sizes_of(3)))
    for n in range(3):
        source = rig.file(job, f"input/{n + 1:04d}.png")
        for name in (f"page-{n}.png", f"crop-{n}.png"):  # one file, three names
            assert rig.file(job, name).stat().st_ino == source.stat().st_ino, name
        assert png_size(rig.file(job, f"crop-{n}.png").read_bytes()) == sizes_of(3)[n]
    assert not list(rig.file(job, ".").glob("*.part"))


async def test_the_stages_of_such_a_job_still_finish_in_order(make_rig: RigFactory) -> None:
    rig = make_rig(backend=seeing_two_everywhere(3))
    job = await rig.run(await rig.image_job(sizes_of(3)))
    progress = assert_legal_progress(rig.store.saves)
    assert progress[-1] == ("done", "done", "done", "done")
    assert all_done(job)


async def test_an_image_that_settles_first_waits_for_the_images_before_it(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prefix rule holds without seg: image 0 is slow to render, images 1 to 3 are settled
    before it and wait; nothing is numbered in the order the images settled."""
    real = pipeline.png_file_size

    def slow_first(path: Path) -> tuple[int, int]:
        if path.name == "page-0.png":
            time.sleep(0.2)
        return real(path)

    monkeypatch.setattr(pipeline, "png_file_size", slow_first)
    rig = make_rig(backend=seeing_two_everywhere(4), render_concurrency=4)
    job = await rig.run(await rig.image_job(sizes_of(4)))
    assert [r.smiles for r in job.results] == [chain(n) for n in range(4)]
    assert [(r.index, r.page_index) for r in job.results] == [(n, n) for n in range(4)]
    assert [rig.file(job, f"crop-{n}.png").is_file() for n in range(4)] == [True] * 4


# --- the cap: as for every job -------------------------------------------------------------------


async def test_twenty_five_images_follow_the_cap_rule(make_rig: RigFactory) -> None:
    """Legacy reads the first 20 and leaves the rest empty, with a warning that only the first 20
    are processed. Here: the first 20 are read, `structuresFound` counts all 25, nothing is sent
    for the last five, and the job says so."""
    rig = make_rig(backend=seeing_two_everywhere(25))
    job = await rig.run(await rig.image_job(sizes_of(25)))

    assert job.status is JobStatus.DONE
    assert len(job.results) == 20
    assert [r.smiles for r in job.results] == [chain(n) for n in range(20)]
    assert [(r.index, r.page_index) for r in job.results] == [(n, n) for n in range(20)]
    assert (job.structures_found, job.structures_dropped, job.truncated) == (25, 5, True)
    assert "structures_truncated" in codes(job)
    message = next(w.message for w in job.warnings if w.code == "structures_truncated")
    assert message == "25 structures were found. Only the first 20 were processed."
    assert rig.backend.count("seg") == 0
    assert rig.backend.count("ocsr") == rig.backend.count("cls") == 20
    sent = {width_of(parts) for parts in rig.backend.parts_of("ocsr")}
    assert sent == {width for width, _ in sizes_of(20)}  # the last five images were never sent
    assert rig.file(job, "crop-19.png").is_file()
    assert not rig.file(job, "crop-20.png").exists()
    assert not list(rig.file(job, ".").glob("*.part"))  # the five that were dropped are gone
    assert rig.file(job, "page-24.png").is_file()  # the pages are all kept


@pytest.mark.parametrize(
    ("images", "cap", "kept"),
    [(3, 3, 3), (4, 3, 3), (2, 1, 1), (21, 20, 20)],
)
async def test_the_cap_comes_from_the_settings_and_counts_images(
    make_rig: RigFactory, images: int, cap: int, kept: int
) -> None:
    rig = make_rig(api={"max_structures": cap}, backend=seeing_two_everywhere(images))
    job = await rig.run(await rig.image_job(sizes_of(images)))
    assert len(job.results) == kept
    assert (job.structures_found, job.structures_dropped) == (images, images - kept)
    assert job.truncated is (images > cap)
    assert ("structures_truncated" in codes(job)) is (images > cap)
    assert rig.backend.count("seg") == 0
    assert rig.backend.count("ocsr") == kept


# --- what is not several images -------------------------------------------------------------------


@pytest.mark.parametrize(("images", "pdf_pages", "seg_calls"), [(1, 0, 1), (0, 3, 3), (2, 0, 0)])
async def test_only_a_single_image_and_a_pdf_still_call_seg(
    make_rig: RigFactory, images: int, pdf_pages: int, seg_calls: int
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 1, 1: 1, 2: 1}))
    job = (
        await rig.pdf_job(pdf_pages)
        if pdf_pages
        else await rig.image_job([(200, 100), (210, 100)][:images])
    )
    done = await rig.run(job)
    assert done.status is JobStatus.DONE
    assert rig.backend.count("seg") == seg_calls


async def test_a_single_image_keeps_seg_crop_when_seg_finds_a_small_structure(
    make_rig: RigFactory,
) -> None:
    """D20 is unchanged: one image goes to seg and only a big enough box makes it whole."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}))
    done = await rig.run(await rig.image_job([(600, 600)]))
    assert rig.backend.count("seg") == 1
    assert done.results[0].bbox != (0, 0, 600, 600)


# --- one image goes wrong -------------------------------------------------------------------------


async def test_an_image_that_is_no_png_keeps_its_number_as_an_empty_result(
    make_rig: RigFactory,
) -> None:
    """Legacy keeps every image in its slot: one it cannot read gets its place with an empty
    SMILES, and the images after it are not renumbered."""
    rig = make_rig(backend=seeing_two_everywhere(4))
    job = await rig.image_job(sizes_of(4))
    rig.file(job, "input/0002.png").write_bytes(b"this was not a PNG")
    done = await rig.run(job)

    assert done.status is JobStatus.DONE
    assert [(r.index, r.page_index) for r in done.results] == [(0, 0), (1, 1), (2, 2), (3, 3)]
    assert [r.smiles for r in done.results] == [chain(0), "", chain(2), chain(3)]
    assert [r.valid for r in done.results] == [True, False, True, True]
    assert done.results[1].bbox == (0, 0, 0, 0)  # its size is unknown
    # no link to bytes that are no PNG: the media endpoint would serve them as image/png
    assert not rig.file(job, "page-1.png").exists() and not rig.file(job, "crop-1.png").exists()
    assert all(rig.file(job, f"crop-{n}.png").is_file() for n in (0, 2, 3))
    assert done.results[1].is_structure is None
    assert codes(done) == ["recognition_failed"]
    assert done.warnings[0].message == "Structure 2 could not be recognised."
    assert (done.structures_found, done.structures_dropped) == (4, 0)
    assert rig.backend.count("seg") == 0
    assert rig.backend.count("ocsr") == rig.backend.count("cls") == 3  # nothing sent for it


async def test_an_image_ocsr_refuses_is_one_invalid_result_and_the_others_are_read(
    make_rig: RigFactory,
) -> None:
    def refuses_the_second(request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        if width_of(parts) == 101:
            return httpx.Response(422, json={"detail": "no complete structure"})
        return by_width(request, parts)

    backend = FakeBackend(layout={n: 2 for n in range(3)}, hooks={"ocsr": refuses_the_second})
    rig = make_rig(backend=backend)
    done = await rig.run(await rig.image_job(sizes_of(3)))

    assert done.status is JobStatus.DONE
    assert [(r.index, r.page_index, r.valid) for r in done.results] == [
        (0, 0, True),
        (1, 1, False),
        (2, 2, True),
    ]
    assert [r.smiles for r in done.results] == [chain(0), "", chain(2)]
    assert codes(done) == ["recognition_failed"]
    assert done.warnings[0].message == "Structure 2 could not be recognised."
    assert rig.backend.count("seg") == 0


async def test_an_image_whose_crop_cannot_be_written_keeps_its_number_too(
    make_rig: RigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = pipeline.link_or_copy

    def failing(source: Path, target: Path) -> None:
        if target.name == "seg-1-0.part":
            raise OSError("no space left on device")
        real(source, target)

    monkeypatch.setattr(pipeline, "link_or_copy", failing)
    rig = make_rig(backend=seeing_two_everywhere(3))
    job = await rig.image_job(sizes_of(3))
    rig.file(job, "crop-1.png").write_bytes(b"left over from a run that was cancelled")
    done = await rig.run(job)

    assert done.status is JobStatus.DONE
    assert [(r.index, r.page_index, r.smiles) for r in done.results] == [
        (0, 0, chain(0)),
        (1, 1, ""),
        (2, 2, chain(2)),
    ]
    assert done.results[1].bbox == (0, 0, *sizes_of(3)[1])  # the image is readable: its own box
    assert done.results[1].valid is False
    assert codes(done) == ["recognition_failed"]  # not "page could not be rendered": it was
    assert done.warnings[0].message == "Structure 2 could not be recognised."
    assert rig.file(job, "page-1.png").is_file()
    assert not rig.file(job, "crop-1.png").exists()  # nor is an old crop taken for this one
    assert (done.structures_found, done.structures_dropped) == (3, 0)
    assert rig.backend.count("ocsr") == 2


async def test_when_no_image_of_several_can_be_read_every_one_is_an_empty_result(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=seeing_two_everywhere(2))
    job = await rig.image_job(sizes_of(2))
    for name in ("0001.png", "0002.png"):
        rig.file(job, f"input/{name}").write_bytes(b"nothing")
    done = await rig.run(job)
    assert done.status is JobStatus.DONE  # as legacy: empty rows, not a failed upload
    assert not list(rig.file(job, ".").glob("page-*.png"))
    assert not list(rig.file(job, ".").glob("crop-*.png"))
    assert [(r.index, r.smiles, r.valid) for r in done.results] == [(0, "", False), (1, "", False)]
    assert codes(done) == ["recognition_failed", "recognition_failed"]
    assert [w.message for w in done.warnings] == [
        "Structure 1 could not be recognised.",
        "Structure 2 could not be recognised.",
    ]
    assert rig.backend.count("seg") == rig.backend.count("ocsr") == 0


async def test_an_unreadable_image_counts_against_the_cap_in_its_place(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(api={"max_structures": 3}, backend=seeing_two_everywhere(5))
    job = await rig.image_job(sizes_of(5))
    rig.file(job, "input/0002.png").write_bytes(b"not a png")
    done = await rig.run(job)
    assert [r.smiles for r in done.results] == [chain(0), "", chain(2)]
    assert (done.structures_found, done.structures_dropped) == (5, 2)


# --- seg is dead --------------------------------------------------------------------------------


async def test_a_job_of_several_images_finishes_with_seg_dead_but_a_single_image_fails(
    make_rig: RigFactory,
) -> None:
    """The point of D23: seg is not needed for several images. The same outage still fails a
    single image (which needs seg's answer) as before."""
    several = make_rig(backend=FakeBackend(hooks={"ocsr": by_width}, down={"seg.test"}))
    done = await several.run(await several.image_job(sizes_of(3)))
    assert done.status is JobStatus.DONE
    assert [r.smiles for r in done.results] == [chain(n) for n in range(3)]
    assert codes(done) == []
    assert several.backend.count("seg") == 0  # not even tried

    single = make_rig(backend=FakeBackend(hooks={"ocsr": by_width}, down={"seg.test"}))
    failed = await single.run(await single.image_job(sizes_of(1)))
    assert failed.status is JobStatus.FAILED
    assert codes(failed)[-1] == "service_unavailable"
    assert failed.results == []
