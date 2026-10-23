"""Multi-image check: the golden crops uploaded together, several images to a job.

Legacy never segments an upload of several images: each image is read whole as one structure, and
the result of image N is structure N. The worker copies that: a job of more than one image
sends every image to ocsr and cls as it is, makes no seg call, numbers the structures in image
order and reports each image as its own box. Before D23 every image of such a job went to seg, so
an image with two structures gave two results and shifted the numbering of the images after it.

This module measures that from outside, through the HTTP API, one job at a time, and deletes every
job it made (`DELETE /api/jobs/{id}`, also when it failed). The images are the crops of a golden
run (`--crops`, the same ones `single-image` sends one by one) in groups of `--sizes` images (pairs
and groups of five by default), taken in position order; a leftover shorter than a group is not
sent. Per image it reports the results the job gave for it (`pageIndex` is the image's place) and
scores them by FULL InChIKey: an image is right when every label it contains is among them.

Per job it reports the signs of the new rule, which a stack that still segments does not show:

* `found` (`structuresFound`) equals the number of images, and so does the number of results;
* the results are in image order: result N is `index` N on `pageIndex` N, one per image;
* every box is its whole image.

`whole` below is all three at once (`ordered` and `boxes whole` are listed apart in the CSV). Run it
before and after a change of the worker and compare with `--before`. Most labels are drafts
(`checked=no` in labels.csv): an absolute count is against drafts, only a before/after difference
is solid; the table marks such labels with `*`.
"""

from __future__ import annotations

import csv
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

from .score import to_inchikey
from .single_image import Box, Item, Unfinished, matched_labels, png_size, run_job

DEFAULT_SIZES = (2, 5)
COLUMNS = [
    "size",
    "group",
    "position",
    "crop",
    "label",
    "width",
    "height",
    "status",
    "seconds",
    "found",
    "job_results",
    "ordered",
    "results",
    "bbox",
    "whole",
    "job_whole",
    "smiles",
    "inchikey",
    "expected",
    "correct",
    "checked",
    "matched",
    "warnings",
    "note",
]


@dataclass(frozen=True)
class Group:
    """The images of one job, in upload order."""

    size: int  # the group size asked for (== len(items))
    number: int  # 1-based, among the jobs of this size
    items: tuple[Item, ...]


@dataclass(frozen=True)
class Seen:
    """What a job's record says about one image."""

    results: int = 0  # results with this image's pageIndex
    bbox: Box | None = None  # of the first of them
    whole: bool = False  # that box is the whole image
    smiles: str = ""
    inchikey: str = ""
    matched: int = 0  # labels of the image whose InChIKey is among its results'


@dataclass(frozen=True)
class JobOutcome:
    """What one job gave, whatever it was."""

    status: str = ""  # done, failed, or the kind of trouble
    seconds: float = 0.0  # from the start of the upload to the poll that saw the end (+/- poll)
    found: int = 0  # structuresFound
    results: int = 0  # all results of the job
    ordered: bool = False  # result N is index N on pageIndex N, one per image
    boxes_whole: bool = False  # every image has exactly one result and its box is the image
    warnings: tuple[str, ...] = ()
    images: tuple[Seen, ...] = ()
    note: str = ""

    def whole(self, size: int) -> bool:
        """The job read like D23: one structure per image, in order, each the whole image."""
        return (
            self.status == "done"
            and self.found == size
            and self.results == size
            and self.ordered
            and self.boxes_whole
        )


@dataclass(frozen=True)
class Row:
    """One image of one job."""

    group: Group
    position: int
    width: int
    height: int
    job: JobOutcome

    @property
    def item(self) -> Item:
        return self.group.items[self.position]

    @property
    def seen(self) -> Seen:
        images = self.job.images
        return images[self.position] if self.position < len(images) else Seen()

    @property
    def correct(self) -> bool | None:
        """Every label of the image is among its results; None for an image without labels."""
        expected = self.item.expected
        return self.seen.matched == len(expected) if expected else None

    def as_csv(self) -> dict[str, str]:
        job, seen, expected = self.job, self.seen, self.item.expected
        checked = "" if not expected else str(all(e.checked for e in expected))
        return {
            "size": str(self.group.size),
            "group": str(self.group.number),
            "position": str(self.position),
            "crop": self.item.path.name,
            "label": ";".join(e.number for e in expected),
            "width": str(self.width),
            "height": str(self.height),
            "status": job.status,
            "seconds": f"{job.seconds:.1f}",
            "found": str(job.found),
            "job_results": str(job.results),
            "ordered": str(job.ordered),
            "results": str(seen.results),
            "bbox": " ".join(map(str, seen.bbox)) if seen.bbox else "",
            "whole": str(seen.whole),
            "job_whole": str(job.whole(self.group.size)),
            "smiles": seen.smiles,
            "inchikey": seen.inchikey,
            "expected": ";".join(e.inchikey for e in expected),
            "correct": "" if self.correct is None else str(self.correct),
            "checked": checked,
            "matched": str(seen.matched),
            "warnings": ";".join(job.warnings),
            "note": job.note,
        }


# --- the groups ----------------------------------------------------------------------------------


def make_groups(items: Sequence[Item], sizes: Sequence[int], *, limit: int = 0) -> list[Group]:
    """Consecutive groups of each size, in position order (a leftover shorter than the size is
    left out); at most `limit` jobs per size when it is above zero. Sizes below 2 are refused: a
    job of one image is the single-image check's."""
    if limit < 0:
        raise ValueError(f"the limit of jobs per size cannot be negative: {limit}")
    groups = []
    for size in sizes:
        if size < 2:
            raise ValueError(f"a group needs at least 2 images, not {size}")
        jobs = [
            tuple(items[start : start + size]) for start in range(0, len(items) - size + 1, size)
        ]
        for number, chunk in enumerate(jobs[: limit or None], start=1):
            groups.append(Group(size, number, chunk))
    return groups


# --- one job -------------------------------------------------------------------------------------


def _seen(results: list[dict[str, Any]], place: int, size: tuple[int, int], item: Item) -> Seen:
    own = [r for r in results if int(r.get("pageIndex", -1)) == place]
    if not own:
        return Seen()
    x0, y0, x1, y1 = (int(v) for v in own[0]["bbox"])
    first = str(own[0].get("smiles", ""))
    return Seen(
        results=len(own),
        bbox=(x0, y0, x1, y1),
        whole=(x0, y0, x1, y1) == (0, 0, size[0], size[1]),
        smiles=first,
        inchikey=to_inchikey(first) or "",
        matched=matched_labels(own, item.expected),
    )


def _outcome(
    job: dict[str, Any], sizes: list[tuple[int, int]], items: tuple[Item, ...]
) -> JobOutcome:
    """A finished job's record, read against the images that were sent. Raises KeyError,
    TypeError or ValueError for a record that is not the documented shape."""
    results = list(job.get("results") or [])
    warnings = tuple(str(w.get("code", "")) for w in job.get("warnings") or [])
    base: dict[str, Any] = {
        "found": int(job.get("structuresFound", 0)),
        "results": len(results),
        "warnings": warnings,
    }
    if job.get("status") != "done":
        return JobOutcome(
            status=str(job.get("status")), note=str(job.get("error") or "no result"), **base
        )
    images = tuple(
        _seen(results, place, size, item)
        for place, (size, item) in enumerate(zip(sizes, items, strict=True))
    )
    ordered = [(int(r["index"]), int(r["pageIndex"])) for r in results] == [
        (place, place) for place in range(len(items))
    ]
    return JobOutcome(
        status="done",
        ordered=ordered,
        boxes_whole=all(seen.results == 1 and seen.whole for seen in images),
        images=images,
        **base,
    )


def measure_group(
    client: httpx.Client,
    group: Group,
    *,
    poll: float = 1.0,
    timeout: float = 300.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Row]:
    """Upload the group as one job and read it. Never raises for what a job or the API answered:
    trouble is the job's status, on every row of the group."""
    datas = [item.path.read_bytes() for item in group.items]
    sizes = [png_size(data) for data in datas]
    uploads = [
        (f"{n:02d}_{item.path.name}", data)
        for n, (item, data) in enumerate(zip(group.items, datas, strict=True))
    ]
    started = clock()
    try:
        record = run_job(client, uploads, poll=poll, timeout=timeout, clock=clock, sleep=sleep)
        job = _outcome(record, sizes, group.items)
    except Unfinished as error:
        job = JobOutcome(status=error.status, note=error.note)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        job = JobOutcome(status="bad_response", note=f"the record is not readable: {error!r}")
    job = replace(job, seconds=clock() - started)
    return [Row(group, place, w, h, job) for place, (w, h) in enumerate(sizes)]


def measure(
    client: httpx.Client,
    groups: list[Group],
    *,
    poll: float = 1.0,
    timeout: float = 300.0,
    on_group: Callable[[Group, list[Row]], None] = lambda group, rows: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Row]:
    rows: list[Row] = []
    for group in groups:
        done = measure_group(client, group, poll=poll, timeout=timeout, clock=clock, sleep=sleep)
        on_group(group, done)
        rows += done
    return rows


# --- reading the result --------------------------------------------------------------------------


def write_rows(path: Path, rows: list[Row]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(row.as_csv() for row in rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def unchecked(rows: list[Row]) -> list[str]:
    """The numbers of the labels the rows are scored against that no human has checked."""
    numbers: list[str] = []
    for row in rows:
        for e in row.item.expected:
            if not e.checked and e.number not in numbers:
                numbers.append(e.number)
    return numbers


def _jobs(rows: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    """The rows of the CSV by job (size, group), in file order."""
    by_job: dict[tuple[str, str], list[dict[str, str]]] = {}
    for r in rows:
        by_job.setdefault((r["size"], r["group"]), []).append(r)
    return by_job


def table(rows: list[dict[str, str]]) -> str:
    """One line per job (found / results / in order / boxes), then one per image, then totals per
    group size."""
    lines = [
        f"{'job':<8}{'status':<8}{'sec':>5}{'found':>6}{'res':>5} {'ordered':>8} {'whole':>6}"
        f" {'right':>7}  images"
    ]
    for (size, group), job in _jobs(rows).items():
        first = job[0]
        scored = [r for r in job if r["correct"] != ""]
        right = sum(r["correct"] == "True" for r in scored)
        marks = " ".join(
            r["label"]
            + ("*" if r["checked"] == "False" else "")
            + ("" if r["correct"] != "False" else "!")
            if r["label"]
            else "-"
            for r in job
        )
        lines.append(
            f"{size + 'x' + group:<8}{first['status']:<8}{float(first['seconds']):>5.1f}"
            f"{first['found']:>6}{first['job_results']:>5} {_yes(first['ordered']):>8} "
            f"{_yes(first['job_whole']):>6} {f'{right}/{len(scored)}':>7}  {marks}"
        )
    lines.append("")
    for size in sorted({r["size"] for r in rows}, key=int):
        part = [r for r in rows if r["size"] == size]
        jobs = {r["group"] for r in part}
        scored = [r for r in part if r["correct"] != ""]
        whole = {r["group"] for r in part if r["job_whole"] == "True"}
        lines.append(
            f"groups of {size}: {len(jobs)} jobs, {len(part)} images, "
            f"right {sum(r['correct'] == 'True' for r in scored)}/{len(scored)} scored, "
            f"D23-shaped {len(whole)}/{len(jobs)} jobs, "
            f"{sum(r['status'] != 'done' for r in part)} images in jobs that did not finish"
        )
    lines.append("(marks: label number, * unchecked label, ! wrong; - not scored)")
    return "\n".join(lines)


def _yes(value: str) -> str:
    return {"True": "yes", "False": "no", "": "-"}.get(value, value)


def compare(before: list[dict[str, str]], after: list[dict[str, str]]) -> str:
    """Per image (size, job, crop): right before and after, and what the first result changed to."""
    earlier = {(r["size"], r["group"], r["crop"]): r for r in before}
    lines = [f"{'image':<26}{'job':>6}{'before':>8}{'after':>7}  change"]
    gained: list[str] = []
    lost: list[str] = []
    for r in after:
        b = earlier.get((r["size"], r["group"], r["crop"]))
        if b is None:
            continue
        name = f"{r['crop']} (x{r['size']})"
        change = "" if b["smiles"] == r["smiles"] else f"{b['smiles']}  ->  {r['smiles']}"
        lines.append(
            f"{name:<26}{r['group']:>6}{_yes(b['correct']):>8}{_yes(r['correct']):>7}  {change}"
        )
        if b["correct"] == "False" and r["correct"] == "True":
            gained.append(name)
        if b["correct"] == "True" and r["correct"] == "False":
            lost.append(name)
    lines.append(f"now right: {', '.join(gained) or '-'}; now wrong: {', '.join(lost) or '-'}")
    for size in sorted({r["size"] for r in after}, key=int):
        was = sum(
            r["job_whole"] == "True" for r in before if r["size"] == size and r["position"] == "0"
        )
        now = sum(
            r["job_whole"] == "True" for r in after if r["size"] == size and r["position"] == "0"
        )
        lines.append(f"groups of {size} D23-shaped: {was} jobs before, {now} after")
    return "\n".join(lines)
