"""A pasted image that is already a tight crop is recognised whole.

seg's box is grown from its mask and can clip the edge of a drawing (the methoxy group of
shentonin A), so for a job made of exactly one uploaded image where seg finds exactly ONE
structure covering at least `SINGLE_IMAGE_WHOLE_FRACTION` (0.6) of the image, ocsr and cls get the
whole image and the reported box is the whole image. Several structures, a small structure in a big
image and PDFs keep seg's crops; zero structures keep the older fallback. A job of several images
never calls seg at all (`test_multi_image_whole.py`).

The fake seg answers every page with the boxes a test lists (in pixels of the image it was sent).
The crop it returns is a white PNG of the box's size, so a crop and the whole image are told apart
by their size, and the whole image by being the bytes of the input.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from decimer_api.models import Job, JobStatus
from decimer_api.worker import pipeline
from decimer_api.worker.settings import WorkerSettings
from tests.worker.conftest import Rig, all_done, assert_legal_progress
from tests.worker.fakes import FakeBackend, json_response, png_size, seg_json

RigFactory = Callable[..., Rig]
Box = tuple[int, int, int, int]
BIG = (8000, 6000)  # 48 MP: seg is sent a smaller copy


def seg_boxes(*boxes: Box) -> FakeBackend:
    """A backend whose seg finds `boxes` on every page."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        width, height = png_size(parts["image"])
        return json_response(seg_json(int(parts["page_index"]), width, height, list(boxes)))

    return FakeBackend(hooks={"seg": hook})


def sent(rig: Rig, service: str) -> list[tuple[int, int]]:
    """The size of every image `service` was sent, in call order."""
    return [png_size(parts["image"]) for parts in rig.backend.parts_of(service)]


def same_file(rig: Rig, job: Job, first: str, second: str) -> bool:
    return rig.file(job, first).stat().st_ino == rig.file(job, second).stat().st_ino


def no_parts_left(rig: Rig, job: Job) -> bool:
    return not list(rig.file(job, ".").glob("*.part"))


# --- one image, one structure -------------------------------------------------------------------


async def test_one_box_covering_most_of_the_image_sends_the_whole_image_on(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=seg_boxes((10, 5, 190, 95)))  # 81 % of 200 x 100
    job = await rig.image_job([(200, 100)])
    page = (rig.file(job, "input") / "0001.png").read_bytes()
    done = await rig.run(job)

    assert done.status is JobStatus.DONE
    (result,) = done.results
    assert (result.index, result.page_index, result.bbox) == (0, 0, (0, 0, 200, 100))
    assert (done.structures_found, done.structures_dropped, done.truncated) == (1, 0, False)
    # ocsr and cls were given the image itself, not seg's 180 x 90 crop
    assert sent(rig, "ocsr") == sent(rig, "cls") == [(200, 100)]
    assert rig.backend.parts_of("ocsr")[0]["image"] == page
    assert rig.backend.parts_of("cls")[0]["image"] == page
    assert result.smiles == "CCO"  # what the fake ocsr says about any image that is not a crop
    # the crop file is a link to the page, as for the zero-structure fallback
    assert same_file(rig, done, "page-0.png", "crop-0.png")
    assert not rig.file(done, "crop-0.png").is_symlink()
    assert rig.backend.count("seg") == 1 and rig.backend.count("ocsr") == 1
    assert no_parts_left(rig, done)
    assert all_done(done)
    assert assert_legal_progress(rig.store.saves)[-1] == ("done",) * 4


async def test_one_box_covering_a_small_part_of_the_image_keeps_the_crop(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=seg_boxes((0, 0, 100, 100)))  # 50 % of 200 x 100
    done = await rig.run(await rig.image_job([(200, 100)]))

    (result,) = done.results
    assert result.bbox == (0, 0, 100, 100)
    assert sent(rig, "ocsr") == sent(rig, "cls") == [(100, 100)]
    assert png_size(rig.file(done, "crop-0.png").read_bytes()) == (100, 100)
    assert not same_file(rig, done, "page-0.png", "crop-0.png")
    assert no_parts_left(rig, done)


@pytest.mark.parametrize(
    ("box", "whole"),
    [
        ((0, 0, 100, 50), True),  # exactly 50 %: "at least" includes the threshold
        ((0, 0, 100, 51), True),
        ((0, 0, 100, 49), False),
        ((0, 0, 50, 100), True),  # the same area, the other way round
        ((0, 0, 50, 99), False),
        ((10, 10, 90, 90), True),  # not touching the edges
    ],
)
async def test_the_threshold_is_at_least_the_fraction_of_the_image_area(
    make_rig: RigFactory, box: Box, whole: bool
) -> None:
    rig = make_rig(backend=seg_boxes(box), single_image_whole_fraction=0.5)
    done = await rig.run(await rig.image_job([(100, 100)]))
    (result,) = done.results
    if whole:
        assert result.bbox == (0, 0, 100, 100)
        assert same_file(rig, done, "page-0.png", "crop-0.png")
    else:
        assert result.bbox == box
        assert not same_file(rig, done, "page-0.png", "crop-0.png")


@pytest.mark.parametrize(("height", "whole"), [(59, False), (61, True)])
async def test_the_default_fraction_is_sixty_percent(
    make_rig: RigFactory, height: int, whole: bool
) -> None:
    rig = make_rig(backend=seg_boxes((0, 0, 100, height)))
    done = await rig.run(await rig.image_job([(100, 100)]))
    assert (done.results[0].bbox == (0, 0, 100, 100)) is whole


async def test_the_fraction_is_a_setting(make_rig: RigFactory) -> None:
    strict = make_rig(backend=seg_boxes((0, 0, 100, 90)), single_image_whole_fraction=1.0)
    assert (await strict.run(await strict.image_job([(100, 100)]))).results[0].bbox == (
        0,
        0,
        100,
        90,
    )
    loose = make_rig(backend=seg_boxes((0, 0, 100, 20)), single_image_whole_fraction=0.1)
    assert (await loose.run(await loose.image_job([(100, 100)]))).results[0].bbox == (
        0,
        0,
        100,
        100,
    )


async def test_a_box_over_the_whole_image_needs_no_setting_to_be_whole(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=seg_boxes((0, 0, 100, 100)), single_image_whole_fraction=1.0)
    done = await rig.run(await rig.image_job([(100, 100)]))
    assert same_file(rig, done, "page-0.png", "crop-0.png")


async def test_an_image_seg_was_sent_a_smaller_copy_of_is_sent_on_whole_too(
    make_rig: RigFactory,
) -> None:
    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        width, height = png_size(parts["image"])  # the copy's size: the box is in its pixels
        box = (width // 20, height // 20, width * 19 // 20, height * 19 // 20)
        return json_response(seg_json(int(parts["page_index"]), width, height, [box]))

    rig = make_rig(backend=FakeBackend(hooks={"seg": hook}))
    done = await rig.run(await rig.image_job([BIG]))
    assert png_size(rig.backend.parts_of("seg")[0]["image"]) != BIG  # it was a copy
    (result,) = done.results
    assert result.bbox == (0, 0, *BIG)  # in the pixels of the image, not of the copy
    assert png_size(rig.file(done, "crop-0.png").read_bytes()) == BIG
    assert same_file(rig, done, "page-0.png", "crop-0.png")
    # ocsr and cls were given the image fitted to the models' limits, not 48 MP
    fitted = sent(rig, "ocsr")[0]
    assert fitted != BIG
    assert fitted[0] * fitted[1] <= rig.worker.model_input_max_pixels
    assert max(fitted) <= rig.worker.model_input_max_side
    assert sent(rig, "cls") == [fitted]


# --- seg's crops are not cut when the rule fires --------------------------------------------------


@pytest.fixture
def cuts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The pages `write_parts` was asked to cut crops of, by page index."""
    cut: list[int] = []
    real = pipeline.write_parts

    def spy(*args: object, **kwargs: Any) -> Any:
        cut.append(kwargs["page_index"])
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "write_parts", spy)
    return cut


async def test_a_fired_rule_never_cuts_the_crop_it_replaces(
    make_rig: RigFactory, cuts: list[int]
) -> None:
    rig = make_rig(backend=seg_boxes((10, 5, 190, 95)))
    done = await rig.run(await rig.image_job([(200, 100)]))
    assert done.results[0].bbox == (0, 0, 200, 100)
    assert cuts == []


@pytest.mark.parametrize(
    ("boxes", "images", "pdf", "cut"),
    [
        ([(0, 0, 100, 100)], [(200, 100)], False, [0]),  # one small box
        ([(0, 0, 180, 90), (10, 5, 190, 95)], [(200, 100)], False, [0]),  # two
        ([], [(200, 100)], False, [0]),  # none: write_parts finds nothing, the fallback follows
        ([(30, 30, 570, 570)], [], True, [0]),  # a PDF page
    ],
)
async def test_every_other_case_still_cuts_seg_crops(
    make_rig: RigFactory,
    cuts: list[int],
    boxes: list[Box],
    images: list[tuple[int, int]],
    pdf: bool,
    cut: list[int],
) -> None:
    rig = make_rig(backend=seg_boxes(*boxes))
    job = await rig.pdf_job(1) if pdf else await rig.image_job(images)
    await rig.run(job)
    assert sorted(cuts) == cut


# --- what keeps seg's crops ----------------------------------------------------------------------


async def test_two_structures_keep_their_crops_however_big_they_are(make_rig: RigFactory) -> None:
    boxes = [(0, 0, 180, 90), (10, 5, 190, 95)]  # each over 60 %
    rig = make_rig(backend=seg_boxes(*boxes))
    done = await rig.run(await rig.image_job([(200, 100)]))

    assert [r.bbox for r in done.results] == boxes
    assert sent(rig, "ocsr") == sent(rig, "cls") == [(180, 90), (180, 90)]
    assert not same_file(rig, done, "page-0.png", "crop-0.png")
    assert not same_file(rig, done, "page-0.png", "crop-1.png")
    assert done.structures_found == 2
    assert no_parts_left(rig, done)


async def test_two_found_with_room_for_one_is_still_two_not_one(make_rig: RigFactory) -> None:
    """The rule counts what seg found, not what the cap kept: MAX_STRUCTURES=1 keeps one crop."""
    rig = make_rig(backend=seg_boxes((0, 0, 180, 90), (10, 5, 190, 95)), api={"max_structures": 1})
    done = await rig.run(await rig.image_job([(200, 100)]))

    (result,) = done.results
    assert result.bbox == (0, 0, 180, 90)
    assert (done.structures_found, done.structures_dropped, done.truncated) == (2, 1, True)
    assert sent(rig, "ocsr") == [(180, 90)]
    assert not same_file(rig, done, "page-0.png", "crop-0.png")


async def test_nothing_found_is_the_old_fallback_whole_image_and_all(make_rig: RigFactory) -> None:
    rig = make_rig(backend=seg_boxes())
    done = await rig.run(await rig.image_job([(200, 100)]))
    (result,) = done.results
    assert result.bbox == (0, 0, 200, 100)
    assert sent(rig, "ocsr") == [(200, 100)]
    assert done.structures_found == 1
    assert same_file(rig, done, "page-0.png", "crop-0.png")


async def test_a_pdf_page_with_one_big_box_keeps_the_crop(make_rig: RigFactory) -> None:
    rig = make_rig(backend=seg_boxes((30, 30, 570, 570)))  # 81 % of the 600 x 600 page
    done = await rig.run(await rig.pdf_job(1))

    (result,) = done.results
    assert result.bbox == (30, 30, 570, 570)
    assert sent(rig, "ocsr") == sent(rig, "cls") == [(540, 540)]
    assert png_size(rig.file(done, "page-0.png").read_bytes()) == (600, 600)
    assert not same_file(rig, done, "page-0.png", "crop-0.png")


# --- the setting ---------------------------------------------------------------------------------


def test_the_default_is_sixty_percent_and_the_environment_sets_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SINGLE_IMAGE_WHOLE_FRACTION", raising=False)
    assert WorkerSettings().single_image_whole_fraction == 0.6
    monkeypatch.setenv("SINGLE_IMAGE_WHOLE_FRACTION", "0.75")
    assert WorkerSettings().single_image_whole_fraction == 0.75


@pytest.mark.parametrize("value", [0, -0.1, 1.0001, 2, float("nan")])
def test_a_fraction_outside_zero_to_one_is_refused(value: float) -> None:
    with pytest.raises(ValidationError, match="single_image_whole_fraction"):
        WorkerSettings(single_image_whole_fraction=value)


@pytest.mark.parametrize("value", [0.0001, 0.6, 1])
def test_a_fraction_above_zero_up_to_one_is_accepted(value: float) -> None:
    assert WorkerSettings(single_image_whole_fraction=value).single_image_whole_fraction == value
