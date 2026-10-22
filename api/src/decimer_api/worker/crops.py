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


@dataclass(frozen=True)
class PageParts:
    found: int  # structures on the page, written or not
    refs: list[CropRef]  # the first `keep` of them, in seg's order


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

    found = 0
    refs: list[CropRef] = []
    full_page = Image.open(source.page) if source is not None else None
    try:
        for number, segment in enumerate(segments):
            box = segment.bbox
            if source is not None:
                scaled = scale_box(box, sent=source.sent, full=source.full)
                if scaled is None:
                    continue
                box = scaled
            found += 1
            if len(refs) >= keep:
                continue
            part = folder / f"seg-{page_index}-{number}.part"
            if full_page is not None:
                full_page.crop(box).save(part, format="PNG")
            else:
                part.write_bytes(segment.png)
            refs.append(CropRef(page_index=page_index, bbox=box, part=part))
    finally:
        if full_page is not None:
            full_page.close()
    return PageParts(found=found, refs=refs)
