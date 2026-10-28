"""Storing what seg found on one page, as it arrives.

`write_parts` writes the crops of one page to part files in the job folder and returns only
their paths and boxes, so the crops themselves are not held in memory while other pages are
being segmented. Only the first `keep` of a page are written: with pages in reading order,
nothing beyond the first `keep` structures of any page can be among the first `keep` of the job.
The pipeline later renames the ones it keeps to `crop-<n>.png` and deletes the rest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .files import scale_box
from .services import Segment

PART_PATTERN = "seg-*.part"


@dataclass(frozen=True)
class Scaling:
    """seg was sent a smaller copy of `page`: `sent` is its size, `full` that of the page. The
    boxes it returns are scaled to the page and the crops are cut from the page, not from seg's
    answer (which is the copy's pixels, and white outside seg's mask)."""

    page: Path
    sent: tuple[int, int]
    full: tuple[int, int]


@dataclass(frozen=True)
class CropRef:
    """A crop on disk that has not been given its final name yet."""

    page_index: int
    bbox: tuple[int, int, int, int]  # in pixels of the page
    part: Path
    # False: nothing could be written at `part` (an image of a job of several images whose
    # crop cannot be made). It still has its place in the numbering; its recognition fails.
    written: bool = True


@dataclass(frozen=True)
class PageParts:
    found: int  # structures on the page, written or not
    refs: list[CropRef]  # the first `keep` of them, in seg's order


def page_boxes(
    segments: list[Segment], source: Scaling | None
) -> list[tuple[int, tuple[int, int, int, int]]]:
    """(place in seg's answer, box in pixels of the page) of every structure with something left
    on the page: a box scaled back from a smaller copy can fall off it, and is not a structure."""
    boxes = []
    for number, segment in enumerate(segments):
        box = segment.bbox
        if source is not None:
            scaled = scale_box(box, sent=source.sent, full=source.full)
            if scaled is None:
                continue
            box = scaled
        boxes.append((number, box))
    return boxes


def write_parts(
    folder: Path,
    *,
    page_index: int,
    segments: list[Segment],
    keep: int,
    source: Scaling | None,
) -> PageParts:
    """Write the first `keep` crops of a page into `folder` (synchronous: run it in a thread)."""
    from PIL import Image

    boxes = page_boxes(segments, source)
    refs: list[CropRef] = []
    full_page = Image.open(source.page) if source is not None else None
    try:
        for number, box in boxes[:keep]:
            part = folder / f"seg-{page_index}-{number}.part"
            if full_page is not None:
                full_page.crop(box).save(part, format="PNG")
            else:
                part.write_bytes(segments[number].png)
            refs.append(CropRef(page_index=page_index, bbox=box, part=part))
    finally:
        if full_page is not None:
            full_page.close()
    return PageParts(found=len(boxes), refs=refs)
