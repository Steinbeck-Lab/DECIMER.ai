"""Single-image check: images uploaded one at a time as a pasted image, scored per image.

A pasted image that is already a tight crop goes through seg again, and seg's box can clip the
drawing's edge (shentonin A loses its OMe). The worker's rule (`SINGLE_IMAGE_WHOLE_FRACTION`, 0.6):
a job of one uploaded image where seg finds exactly ONE structure covering at least that
fraction of the image sends the whole image to ocsr and cls, and reports the whole image as its box.

This module measures that from outside, through the HTTP API, one job at a time, and deletes every
job it made (`DELETE /api/jobs/{id}`, also when it failed). Two sets of images:

* the crops of a golden run (`--crops`): every image is one seg crop, so the rule should fire on
  all of them. The label of a crop is the one whose `legacy_segment` equals the crop's position in
  the run (the file name's leading number); crops without a label are measured but not scored.
* the negatives (`--negatives`, `single-negatives/`): regions of the golden paper's pages, where
  the rule should NOT fire, or fire on a box that is mostly the structure. `negatives.csv` names
  the labels each image contains.

`--also-without-rule` repeats every image as a job of TWO images (the image and a blank one):
the rule never applies to such a job, so on a stack that still segments multi-image jobs it gives
what seg's crop path reads and seg's own box, to compare with the single-image job. Where a job of
several images never calls seg (every image is read whole), these columns are the whole-image
reading, not seg's: use `multi-image` for that, and this option only to judge an older stack.
Scores are by FULL InChIKey: an image is right when every label it contains is among its results.

Run it before and after a change of the worker and compare with `--before`. The box a job reports
is seg's own box until the rule exists, and the whole image once it fires (or when seg found
nothing at all), so "the rule fired" without `--also-without-rule` is `whole=True` with `found=1`.
`predicted_whole` is the rule applied to seg's box (the `--also-without-rule` job's when there is
one), `--whole-fraction` the threshold it uses (keep it equal to the worker's setting).

Most labels are drafts (`checked=no` in labels.csv): an absolute count is against drafts, only a
before/after difference is solid. The table marks such labels with `*`.
"""

from __future__ import annotations

import csv
import re
import struct
import sys
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .next_stack import JOB_ID, PNG_MAGIC, RETRY_GET, RETRY_POST, retry_after
from .score import to_inchikey

CROP_NAME = re.compile(r"(\d+)_crop-(\d+)\.png")
MAX_BUSY_RETRIES = 12  # 429 and 503 on the upload: refused before a job exists
REQUEST_TIMEOUT = 120.0
BLANK_SIDE = 96  # the companion image of a rule-off job
Box = tuple[int, int, int, int]
COLUMNS = [
    "crop",
    "label",
    "width",
    "height",
    "status",
    "found",
    "results",
    "bbox",
    "fraction",
    "whole",
    "predicted_whole",
    "smiles",
    "inchikey",
    "expected",
    "correct",
    "note",
    "checked",
    "matched",
    "fired",
    "off_status",
    "off_found",
    "off_bbox",
    "off_fraction",
    "off_smiles",
    "off_matched",
    "off_correct",
]


@dataclass(frozen=True)
class Expected:
    """A label an image contains."""

    number: str
    inchikey: str
    checked: bool


@dataclass(frozen=True)
class Item:
    """An image to send and the labels it contains (none: measured, not scored)."""

    path: Path
    expected: tuple[Expected, ...] = ()


@dataclass(frozen=True)
class Outcome:
    """What one job gave for the image."""

    status: str = ""  # done, failed, or the kind of trouble
    found: int = 0  # structuresFound
    results: int = 0  # structures returned for the image
    bbox: Box | None = None  # of the first result
    fraction: float | None = None  # the box's share of the image's area
    whole: bool = False  # the box is the whole image
    smiles: str = ""  # of the first result
    inchikey: str = ""
    matched: int = 0  # labels of the image whose InChIKey is among the results'
    note: str = ""


@dataclass(frozen=True)
class Row:
    crop: str  # the file name
    width: int
    height: int
    expected: tuple[Expected, ...]
    on: Outcome  # the image as a job of its own (the rule applies)
    off: Outcome | None  # with a blank companion (it does not), when asked for
    predicted_whole: bool  # found == 1 and seg's box covers the fraction

    @property
    def correct(self) -> bool | None:
        """Every label of the image is among the results; None for an image without labels."""
        return self.on.matched == len(self.expected) if self.expected else None

    @property
    def off_correct(self) -> bool | None:
        if self.off is None or not self.expected:
            return None
        return self.off.matched == len(self.expected)

    @property
    def fired(self) -> bool:
        """The rule made the job read the image whole. With a rule-off job beside it this is
        exact (the single job's box is the image and seg's own was one structure); without, a
        seg that found nothing (the older fallback) looks the same."""
        if self.off is not None:
            return self.on.whole and self.off.found == 1 and not self.off.whole
        return self.on.whole and self.on.found == 1

    def as_csv(self) -> dict[str, str]:
        on, off = self.on, self.off
        checked = "" if not self.expected else str(all(e.checked for e in self.expected))
        return {
            "crop": self.crop,
            "label": ";".join(e.number for e in self.expected),
            "width": str(self.width),
            "height": str(self.height),
            "status": on.status,
            "found": str(on.found),
            "results": str(on.results),
            "bbox": _box(on.bbox),
            "fraction": _share(on.fraction),
            "whole": str(on.whole),
            "predicted_whole": str(self.predicted_whole),
            "smiles": on.smiles,
            "inchikey": on.inchikey,
            "expected": ";".join(e.inchikey for e in self.expected),
            "correct": _tri(self.correct),
            "note": on.note,
            "checked": checked,
            "matched": str(on.matched),
            "fired": str(self.fired),
            "off_status": "" if off is None else off.status,
            "off_found": "" if off is None else str(off.found),
            "off_bbox": "" if off is None else _box(off.bbox),
            "off_fraction": "" if off is None else _share(off.fraction),
            "off_smiles": "" if off is None else off.smiles,
            "off_matched": "" if off is None else str(off.matched),
            "off_correct": _tri(self.off_correct),
        }


def _box(bbox: Box | None) -> str:
    return " ".join(map(str, bbox)) if bbox else ""


def _share(fraction: float | None) -> str:
    return "" if fraction is None else f"{fraction:.4f}"


def _tri(value: bool | None) -> str:
    return "" if value is None else str(value)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[Row]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(row.as_csv() for row in rows)


def png_size(data: bytes) -> tuple[int, int]:
    if not data.startswith(PNG_MAGIC) or len(data) < 24:
        raise ValueError("not a PNG")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def blank_png(side: int = BLANK_SIDE) -> bytes:
    """A white square PNG (8-bit gray), for the companion of a rule-off job."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body)
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + b"\xff" * side for _ in range(side))
    return (
        PNG_MAGIC
        + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


# --- which labels an image contains --------------------------------------------------------------


def read_labels(labels_csv: Path) -> tuple[dict[str, Expected], dict[int, str]]:
    """(label number -> label, legacy_segment -> label number), from labels.csv."""
    by_number: dict[str, Expected] = {}
    by_segment: dict[int, str] = {}
    with labels_csv.open(newline="") as f:
        for row in csv.DictReader(f):
            number = row["number"]
            by_number[number] = Expected(
                number, row["inchikey"], row["checked"].strip().lower() == "yes"
            )
            segment = row.get("legacy_segment", "").strip()
            if segment:
                by_segment[int(segment)] = number
    return by_number, by_segment


def crop_items(folder: Path, labels_csv: Path) -> list[Item]:
    """Every crop of a golden run, in position order, with the label of the same drawing."""
    by_number, by_segment = read_labels(labels_csv)
    found = []
    for path in folder.iterdir():
        match = CROP_NAME.fullmatch(path.name)
        if match is not None and path.is_file():
            found.append((int(match.group(1)), path))
    items = []
    for position, path in sorted(found):
        number = by_segment.get(position)
        items.append(Item(path, (by_number[number],) if number is not None else ()))
    return items


def negative_items(folder: Path, labels_csv: Path) -> list[Item]:
    """The images of `negatives.csv`, in its order, with the labels it names (`;`-separated)."""
    by_number, _ = read_labels(labels_csv)
    items = []
    with (folder / "negatives.csv").open(newline="") as f:
        for row in csv.DictReader(f):
            numbers = [n for n in row["labels"].split(";") if n]
            items.append(Item(folder / row["file"], tuple(by_number[n] for n in numbers)))
    return items


def covers(bbox: Box, size: tuple[int, int], found: int, fraction: float) -> bool:
    """Whether the worker's rule applies to the box: exactly one structure found and a box of at
    least `fraction` of the image's area."""
    x0, y0, x1, y1 = bbox
    return found == 1 and (x1 - x0) * (y1 - y0) >= fraction * size[0] * size[1]


def matched_labels(results: list[dict[str, Any]], expected: tuple[Expected, ...]) -> int:
    """How many of the labels have their FULL InChIKey among the results' (RDKit, from SMILES)."""
    keys = {to_inchikey(str(r.get("smiles", ""))) for r in results}
    return sum(e.inchikey in keys for e in expected)


# --- one job -------------------------------------------------------------------------------------


class Unfinished(Exception):
    def __init__(self, status: str, note: str) -> None:
        super().__init__(note)
        self.status = status
        self.note = note


def _send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    sleep: Callable[[float], None],
    busy: frozenset[int] = RETRY_POST,
    **kwargs: Any,
) -> httpx.Response:
    """One request; 429 and 503 (refused before anything happened) are waited out, Retry-After."""
    for _ in range(MAX_BUSY_RETRIES):
        try:
            response = client.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except httpx.TransportError as error:
            raise Unfinished("unreachable", f"{method} {url}: {error!r}") from error
        if response.status_code not in busy:
            return response
        sleep(max(1.0, retry_after(response)))
    raise Unfinished(
        "busy", f"{method} {url}: still {response.status_code} after {MAX_BUSY_RETRIES} tries"
    )


def _record(response: httpx.Response, what: str) -> dict[str, Any]:
    """The JSON object of an answer, or `Unfinished("bad_response")`."""
    try:
        body = response.json()
    except ValueError as error:
        raise Unfinished("bad_response", f"{what}: the body is not JSON") from error
    if not isinstance(body, dict):
        raise Unfinished("bad_response", f"{what}: the body is not a JSON object")
    return body


def _job_id(response: httpx.Response) -> str:
    """The id of the job an accepted upload made: from its body, else from its Location header
    (a body that cannot be read must not leave the job behind). Empty when neither has one."""
    try:
        body = response.json()
        candidate = body.get("id", "") if isinstance(body, dict) else ""
    except ValueError:
        candidate = ""
    if not isinstance(candidate, str) or JOB_ID.fullmatch(candidate) is None:
        candidate = response.headers.get("location", "").rsplit("/", 1)[-1]
    return candidate if JOB_ID.fullmatch(candidate) is not None else ""


def run_job(
    client: httpx.Client,
    uploads: list[tuple[str, bytes]],
    *,
    poll: float,
    timeout: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    """Upload the images as pasted PNGs, wait for the job, return its record; the job is deleted
    before this returns (or raises `Unfinished`), whatever its answers looked like."""
    response = _send(
        client,
        "POST",
        "/api/jobs",
        sleep=sleep,
        files=[("file", (name, data, "image/png")) for name, data in uploads],
    )
    if response.status_code != 202:
        raise Unfinished("rejected", f"upload: HTTP {response.status_code}: {response.text[:200]}")
    job_id = _job_id(response)
    try:
        if not job_id:
            raise Unfinished("bad_response", "upload: the answer has no job id")
        job = _record(response, "upload")
        deadline = clock() + timeout
        while job.get("status") not in ("done", "failed"):
            if clock() >= deadline:
                raise Unfinished("timeout", f"not done after {timeout:g} s")
            sleep(poll)
            reply = _send(client, "GET", f"/api/jobs/{job_id}", sleep=sleep, busy=RETRY_GET)
            if not reply.is_success:
                raise Unfinished("http_error", f"poll: HTTP {reply.status_code}")
            job = _record(reply, "poll")
        return job
    finally:
        if job_id:
            _delete(client, job_id, sleep=sleep)


def _delete(client: httpx.Client, job_id: str, *, sleep: Callable[[float], None]) -> None:
    """Never raises: a job that cannot be deleted is reported on stderr and left to expire."""
    try:
        response = _send(client, "DELETE", f"/api/jobs/{job_id}", sleep=sleep)
    except Unfinished as error:
        print(f"warning: job {job_id} was not deleted: {error.note}", file=sys.stderr)
        return
    if response.status_code not in (204, 404):
        print(
            f"warning: job {job_id}: DELETE answered HTTP {response.status_code}", file=sys.stderr
        )


def _outcome(job: dict[str, Any], size: tuple[int, int], expected: tuple[Expected, ...]) -> Outcome:
    """The part of a finished job's record that is about the first image. Raises KeyError,
    TypeError or ValueError for a record that is not the documented shape."""
    results = [r for r in job.get("results") or [] if int(r.get("pageIndex", 0)) == 0]
    found = int(job.get("structuresFound", 0))
    if job.get("status") != "done" or not results:
        return Outcome(
            status=str(job.get("status")),
            found=found,
            results=len(results),
            note=str(job.get("error") or "no result"),
        )
    first = results[0]
    x0, y0, x1, y1 = (int(v) for v in first["bbox"])
    box = (x0, y0, x1, y1)
    smiles = str(first.get("smiles", ""))
    return Outcome(
        status="done",
        found=found,
        results=len(results),
        bbox=box,
        fraction=(x1 - x0) * (y1 - y0) / (size[0] * size[1]),
        whole=box == (0, 0, size[0], size[1]),
        smiles=smiles,
        inchikey=to_inchikey(smiles) or "",
        matched=matched_labels(results, expected),
    )


def run_outcome(
    client: httpx.Client,
    uploads: list[tuple[str, bytes]],
    size: tuple[int, int],
    expected: tuple[Expected, ...],
    *,
    poll: float,
    timeout: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> Outcome:
    """One job's outcome. Never raises for what a job or the API answered: trouble is a status."""
    try:
        job = run_job(client, uploads, poll=poll, timeout=timeout, clock=clock, sleep=sleep)
        return _outcome(job, size, expected)
    except Unfinished as error:
        return Outcome(status=error.status, note=error.note)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        return Outcome(status="bad_response", note=f"the record is not readable: {error!r}")


def measure_item(
    client: httpx.Client,
    item: Item,
    *,
    whole_fraction: float,
    also_without_rule: bool = False,
    poll: float = 1.0,
    timeout: float = 300.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Row:
    data = item.path.read_bytes()
    size = png_size(data)
    common: dict[str, Any] = {"poll": poll, "timeout": timeout, "clock": clock, "sleep": sleep}
    on = run_outcome(client, [("pasted.png", data)], size, item.expected, **common)
    off = None
    if also_without_rule:
        both = [("pasted.png", data), ("blank.png", blank_png())]
        off = run_outcome(client, both, size, item.expected, **common)
    seen = off if off is not None else on  # seg's own box, when there is a job that shows it
    predicted = seen.bbox is not None and covers(seen.bbox, size, seen.found, whole_fraction)
    return Row(item.path.name, size[0], size[1], item.expected, on, off, predicted)


def measure(
    client: httpx.Client,
    items: list[Item],
    *,
    whole_fraction: float,
    also_without_rule: bool = False,
    poll: float = 1.0,
    timeout: float = 300.0,
    on_row: Callable[[Row], None] = lambda row: None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Row]:
    rows = []
    for item in items:
        row = measure_item(
            client,
            item,
            whole_fraction=whole_fraction,
            also_without_rule=also_without_rule,
            poll=poll,
            timeout=timeout,
            clock=clock,
            sleep=sleep,
        )
        on_row(row)
        rows.append(row)
    return rows


# --- reading the table ---------------------------------------------------------------------------


def _yes(value: str) -> str:
    return {"True": "yes", "False": "no", "": "-"}.get(value, value)


def unchecked(rows: list[Row]) -> list[str]:
    """The numbers of the labels the rows are scored against that no human has checked."""
    numbers: list[str] = []
    for row in rows:
        for e in row.expected:
            if not e.checked and e.number not in numbers:
                numbers.append(e.number)
    return numbers


def table(rows: list[dict[str, str]]) -> str:
    """The rows of a CSV as a table. With rule-off jobs (`off_status` filled) it shows seg's own
    box and what the crop path read beside what the single-image job read."""
    paired = any(r.get("off_status") for r in rows)
    head = (
        f"{'image':<32}{'label':<10}{'size':<11}{'found':>5} {'seg box':>8} {'whole':>6} "
        f"{'rule':>5} {'fired':>6} {'right':>6}"
        + (f" {'crop path':>10}" if paired else "")
        + "  smiles"
    )
    lines = [head]
    for r in rows:
        seen = r["off_fraction"] if paired and r.get("off_fraction") else r["fraction"]
        found = r["off_found"] if paired and r.get("off_found") else r["found"]
        share = f"{float(seen):.0%}" if seen else "-"
        smiles = r["smiles"] if r["status"] == "done" else f"({r['status']}: {r['note']})"
        label = r["label"] + ("*" if r.get("checked") == "False" else "")
        correct = r["correct"]
        if r.get("matched") and ";" in r["label"]:
            correct = f"{r['matched']}/{len(r['label'].split(';'))}"
        lines.append(
            f"{r['crop']:<32}{label:<10}{r['width'] + 'x' + r['height']:<11}{found:>5} "
            f"{share:>8} {_yes(r['whole']):>6} {_yes(r['predicted_whole']):>5} "
            f"{_yes(r.get('fired', '')):>6} {_yes(correct):>6}"
            + (f" {_yes(r.get('off_correct', '')):>10}" if paired else "")
            + f"  {smiles}"
        )
    scored = [r for r in rows if r["correct"] != ""]
    correct_n = sum(r["correct"] == "True" for r in scored)
    rule = [r["crop"] for r in rows if r["predicted_whole"] == "True"]
    lines.append(
        f"right {correct_n}/{len(scored)} scored; "
        f"the rule applies to {len(rule)}: {', '.join(rule) or '-'}"
    )
    return "\n".join(lines)


def compare(before: list[dict[str, str]], after: list[dict[str, str]]) -> str:
    """Per crop: was it right before and after, did the rule fire, was it predicted to."""
    earlier = {r["crop"]: r for r in before}
    lines = [f"{'crop':<18}{'predicted':>10}{'fired':>7}{'before':>8}{'after':>7}  change"]
    predicted_set: set[str] = set()
    fired_set: set[str] = set()
    gained: list[str] = []
    lost: list[str] = []
    for r in after:
        b = earlier.get(r["crop"])
        if b is None:
            continue
        predicted = b["predicted_whole"] == "True"
        # a crop seg found nothing on is "whole" before too: it is not the rule's doing
        fired = r["whole"] == "True" and r["found"] == "1" and b["whole"] != "True"
        if predicted:
            predicted_set.add(r["crop"])
        if fired:
            fired_set.add(r["crop"])
        if b["correct"] == "False" and r["correct"] == "True":
            gained.append(r["crop"])
        if b["correct"] == "True" and r["correct"] == "False":
            lost.append(r["crop"])
        change = "" if b["smiles"] == r["smiles"] else f"{b['smiles']}  ->  {r['smiles']}"
        lines.append(
            f"{r['crop']:<18}{_yes(str(predicted)):>10}{_yes(str(fired)):>7}"
            f"{_yes(b['correct']):>8}{_yes(r['correct']):>7}  {change}"
        )
    kept = sorted(predicted_set ^ fired_set)
    lines.append(
        f"predicted {len(predicted_set)}, fired {len(fired_set)}, "
        f"{'the same crops' if not kept else 'DIFFERENT: ' + ', '.join(kept)}"
    )
    lines.append(f"now right: {', '.join(gained) or '-'}; now wrong: {', '.join(lost) or '-'}")
    return "\n".join(lines)
