"""Pages that seg cannot take as they are, and how crops are stored while pages arrive.

seg refuses images over 40 MP and bodies over 40 MiB. A B2 page at 300 DPI is 49 MP, and the
upload limit is 50 MP, so such pages are legal. The worker sends seg a smaller copy, scales the
boxes it returns back to the full page and cuts the crops from the full-resolution image.
"""

from __future__ import annotations

import base64
import io
import os
from collections.abc import Callable
from pathlib import Path

import httpx
from PIL import Image, ImageChops, ImageDraw

from decimer_api.models import Job, JobStatus
from decimer_api.worker.crops import Scaling, write_parts
from decimer_api.worker.services import Segment
from tests.factories import make_pdf
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, json_response, png_size, seg_json

RigFactory = Callable[..., Rig]

B2 = (1417.3, 2004.2)  # 500 x 707 mm in points: 5906 x 8351 px = 49.3 MP at 300 DPI
BIG = (8000, 6000)  # 48 MP


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def marked_page(size: tuple[int, int], black: tuple[int, int, int, int]) -> bytes:
    """A white page with one black rectangle at a known place of the full-resolution image."""
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).rectangle(black, fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def seg_finding(fraction: tuple[float, float, float, float]) -> Callable[..., httpx.Response]:
    """A seg hook that finds one structure, at `fraction` of the image it was given."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        width, height = png_size(parts["image"])
        x0, y0, x1, y1 = fraction
        box = (int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height))
        return json_response(seg_json(int(parts["page_index"]), width, height, [box]))

    return hook


# --- a page over the limits ---------------------------------------------------------------------


async def test_a_48_megapixel_image_goes_to_seg_as_a_smaller_copy(make_rig: RigFactory) -> None:
    backend = FakeBackend(hooks={"seg": seg_finding((0.5, 0.25, 0.75, 0.5))})
    rig = make_rig(backend=backend)
    job = await rig.image_job([BIG])
    # a black square exactly where seg will say the structure is, in full-resolution pixels
    (rig.file(job, "input") / "0001.png").write_bytes(marked_page(BIG, (4000, 1500, 6000, 3000)))
    done = await rig.run(job)

    sent = png_size(backend.parts_of("seg")[0]["image"])
    assert sent[0] * sent[1] <= rig.worker.seg_input_max_pixels
    assert sent[0] * sent[1] > 10_000_000  # not shrunk more than it had to be
    assert abs(sent[0] / sent[1] - 8000 / 6000) < 0.01
    assert len(backend.parts_of("seg")[0]["image"]) < rig.worker.seg_input_max_bytes

    assert done.status is JobStatus.DONE
    (result,) = done.results
    # the box was in the pixels of the copy; the result is in those of the page
    assert all(
        abs(got - want) <= 3
        for got, want in zip(result.bbox, (4000, 1500, 6000, 3000), strict=True)
    )
    assert png_size(rig.file(done, "page-0.png").read_bytes()) == BIG  # the page keeps its size


async def test_the_crop_is_cut_from_the_full_resolution_page(make_rig: RigFactory) -> None:
    """Not scaled up from what seg saw: the crop has the page's own pixels."""
    backend = FakeBackend(hooks={"seg": seg_finding((0.5, 0.25, 0.75, 0.5))})
    rig = make_rig(backend=backend)
    job = await rig.image_job([BIG])
    # a one-pixel black dot that a downscaled copy would have averaged away
    page = marked_page(BIG, (5000, 2000, 5000, 2000))
    (rig.file(job, "input") / "0001.png").write_bytes(page)
    done = await rig.run(job)

    (result,) = done.results
    crop = Image.open(rig.file(done, "crop-0.png"))
    assert crop.size == (result.bbox[2] - result.bbox[0], result.bbox[3] - result.bbox[1])
    dot = ImageChops.invert(crop.convert("L")).getbbox()  # where the black pixel is in the crop
    assert dot is not None and dot[2] - dot[0] == 1 and dot[3] - dot[1] == 1
    assert (result.bbox[0] + dot[0], result.bbox[1] + dot[1]) == (5000, 2000)  # the page's pixel
    # and ocsr and cls were given that crop, cut down for them
    for service in ("ocsr", "cls"):
        width, height = png_size(backend.parts_of(service)[0]["image"])
        assert width * height <= rig.worker.model_input_max_pixels


async def test_a_b2_pdf_page_is_rendered_whole_and_segmented_from_a_smaller_copy(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(hooks={"seg": seg_finding((0.1, 0.1, 0.3, 0.2))})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(1, data=make_pdf(1, page_size=B2)))

    page_size = png_size(rig.file(job, "page-0.png").read_bytes())
    assert page_size[0] * page_size[1] > 40_000_000  # over seg's limit: it could not take it
    sent = png_size(backend.parts_of("seg")[0]["image"])
    assert sent[0] * sent[1] <= 25_000_000
    assert job.status is JobStatus.DONE
    (result,) = job.results
    x0, y0, x1, y1 = result.bbox
    assert 0 <= x0 < x1 <= page_size[0] and 0 <= y0 < y1 <= page_size[1]
    assert abs(x0 - 0.1 * page_size[0]) < 3 and abs(y1 - 0.2 * page_size[1]) < 3
    assert png_size(rig.file(job, "crop-0.png").read_bytes()) == (x1 - x0, y1 - y0)


async def test_the_single_image_fallback_works_for_an_image_seg_cannot_take_whole(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(layout={})
    rig = make_rig(backend=backend)
    done = await rig.run(await rig.image_job([BIG]))
    sent = png_size(backend.parts_of("seg")[0]["image"])
    assert sent[0] * sent[1] <= rig.worker.seg_input_max_pixels  # seg was asked about a copy
    (result,) = done.results
    assert result.bbox == (0, 0, 8000, 6000)  # the whole image, in its own pixels
    assert png_size(rig.file(done, "crop-0.png").read_bytes()) == BIG
    width, height = png_size(backend.parts_of("ocsr")[0]["image"])
    assert width * height <= rig.worker.model_input_max_pixels  # and the models got a small one
    assert max(width, height) <= rig.worker.model_input_max_side


async def test_a_page_within_the_limits_is_sent_as_it_is(make_rig: RigFactory) -> None:
    backend = FakeBackend(hooks={"seg": seg_finding((0.1, 0.1, 0.3, 0.2))})
    rig = make_rig(backend=backend)
    job = await rig.image_job([(2000, 1500)])
    done = await rig.run(job)
    assert backend.parts_of("seg")[0]["image"] == rig.file(done, "page-0.png").read_bytes()


async def test_seg_crops_are_used_when_the_page_was_not_scaled(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 1})
    rig = make_rig(backend=backend)
    done = await rig.run(await rig.pdf_job(1))
    from tests.worker.fakes import make_crop

    assert rig.file(done, "crop-0.png").read_bytes() == make_crop(0, 0)


async def test_a_page_over_the_byte_limit_is_shrunk_even_below_the_pixel_limit(
    make_rig: RigFactory,
) -> None:
    """40 MiB is a limit of its own: a noisy scan can be over it long before 40 MP."""
    backend = FakeBackend(hooks={"seg": seg_finding((0.1, 0.1, 0.3, 0.3))})
    rig = make_rig(backend=backend, seg_input_max_bytes=400_000)
    job = await rig.image_job([(500, 500)])
    noisy = Image.frombytes("RGB", (500, 500), os.urandom(500 * 500 * 3))
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG", compress_level=1)
    (rig.file(job, "input") / "0001.png").write_bytes(buffer.getvalue())
    done = await rig.run(job)
    assert len(backend.parts_of("seg")[0]["image"]) <= 400_000
    assert png_size(backend.parts_of("seg")[0]["image"])[0] < 500
    (result,) = done.results
    assert result.bbox[2] <= 500 and result.bbox[3] <= 500
    assert abs(result.bbox[0] - 50) <= 2  # scaled back to the 500 px page


async def test_a_page_that_cannot_be_made_small_enough_is_a_failed_page(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(seg_input_max_bytes=50)  # below any PNG
    done = await rig.run(await rig.image_job([(300, 300)]))
    assert done.status is JobStatus.FAILED  # the only page
    assert rig.backend.count("seg") == 0


# --- crops are written as pages arrive, and only the first twenty are kept ------------------------


def segment(index: int, box: tuple[int, int, int, int]) -> Segment:
    buffer = io.BytesIO()
    Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), "white").save(buffer, format="PNG")
    return Segment(index=index, bbox=box, png=base64.b64encode(buffer.getvalue()).decode())  # type: ignore[arg-type]


def test_only_the_crops_that_can_matter_are_written_for_a_page(tmp_path: Path) -> None:
    segments = [segment(i, (i * 10, 0, i * 10 + 8, 8)) for i in range(30)]
    parts = write_parts(tmp_path, page_index=2, segments=segments, keep=20, source=None)
    refs = parts.refs
    assert parts.found == 30  # all are counted, only the first twenty are written
    assert len(refs) == 20
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(r.part.name for r in refs)
    assert [r.bbox for r in refs] == [s.bbox for s in segments[:20]]
    assert all(r.page_index == 2 for r in refs)


def test_parts_are_cut_from_the_full_page_when_a_source_is_given(tmp_path: Path) -> None:
    page = tmp_path / "page-0.png"
    Image.new("RGB", (400, 400), "white").save(page)
    (ref,) = write_parts(
        tmp_path,
        page_index=0,
        segments=[segment(0, (10, 10, 20, 20))],  # in the pixels of the copy: half the page
        keep=20,
        source=Scaling(page=page, sent=(200, 200), full=(400, 400)),
    ).refs
    assert ref.bbox == (20, 20, 40, 40)
    assert png_size(ref.part.read_bytes()) == (20, 20)


async def test_a_page_with_thirty_structures_costs_twenty_files(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 30}))
    job = await rig.run(await rig.pdf_job(1))
    assert job.structures_found == 30 and job.structures_dropped == 10
    names = sorted(
        p.name for p in rig.file(job, ".").iterdir() if p.name.startswith(("crop", "seg"))
    )
    assert names == sorted(f"crop-{i}.png" for i in range(20))  # no part files left behind
    assert len(job.results) == 20


async def test_the_crops_of_a_page_are_on_disk_before_the_next_page_is_asked_about(
    make_rig: RigFactory,
) -> None:
    """Pages are not collected in memory: what a page found is written when it arrives. The
    crops of page 0 are part files, or under their final names when the page has already been
    given its place (the stages overlap), and never a count short."""
    seen: list[list[str]] = []
    folder_of: dict[str, Path] = {}

    def second_page(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        if parts["page_index"] == b"1":
            names = (p.name for p in folder_of["job"].iterdir())
            seen.append(sorted(n for n in names if n.startswith("seg-0-") or n.startswith("crop-")))
        return None

    rig = make_rig(
        backend=FakeBackend(layout={0: 3, 1: 1}, hooks={"seg": second_page}), seg_concurrency=1
    )
    job = await rig.pdf_job(2)
    folder_of["job"] = rig.file(job, ".")
    await rig.run(job)
    assert len(seen) == 1 and len(seen[0]) == 3


async def test_a_run_that_is_repeated_does_not_trip_over_parts_left_by_the_first(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}))
    job = await rig.pdf_job(1)
    (rig.file(job, ".") / "seg-9-9.part").write_bytes(b"left over from a cancelled run")
    done = await rig.run(job)
    assert done.status is JobStatus.DONE
    assert not list(rig.file(done, ".").glob("*.part"))


async def test_the_fallback_crop_is_a_link_to_the_page_not_a_second_copy(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={}))
    done = await rig.run(await rig.image_job([(123, 77)]))
    page, crop = rig.file(done, "page-0.png").stat(), rig.file(done, "crop-0.png").stat()
    assert page.st_ino == crop.st_ino
    assert page.st_nlink >= 2
    assert not rig.file(done, "crop-0.png").is_symlink()  # the media endpoint refuses symlinks


# --- the size of seg's answer ---------------------------------------------------------------------


async def test_an_oversized_answer_from_seg_loses_that_page_not_the_worker(
    make_rig: RigFactory,
) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 3, 1: 0}), seg_response_limit_mb=0.0005)
    job = await rig.run(await rig.pdf_job(2))
    # page 0 answered with more than 1 KB (three crops), page 1 with nothing much
    assert job.status is JobStatus.DONE
    assert codes(job) == ["page_segmentation_failed"]
    assert job.results == []
