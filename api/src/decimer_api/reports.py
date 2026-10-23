"""Problem reports: what is kept when a user says a result is wrong.

A report is one folder in `REPORTS_DIR`, a private volume that only the api mounts (the edge never
sees it, and the job sweep never touches it): `<job id>-<index>/` with

* `crop.png`     the image of the structure, copied from the job (left out when it is bigger than
                 `MAX_REPORT_IMAGE_BYTES`; the record then says so),
* `result.json`  the result as recognised (SMILES, molfile, name, confidence, ...), the job's
                 page counts and warnings, the engines' versions and the time.

Nothing comes from the client but the index, and that is checked against the job's own results.
The folder name is built here from a validated job id and an integer: no client text reaches a
path. Folders are `0700` and files `0600`. A folder is written under a temporary name (`.tmp-*`)
and renamed into place, so a reader never finds half a report, and a report is made once per
result: a second one for the same result is a no-op (a double click, a retry).

The number of reports is capped. Only finished reports count (a `.tmp-*` folder is work in
progress, or what a crash left: `tidy`, at start-up, removes those older than an hour). The check
and the rename are done together under a lock, so requests running at the same time cannot all find
room for one more; there is one api process, which is also why a process lock is enough. A warning
is logged when the count reaches 80 % of the cap (and by `tidy` if it already is at start-up).

The privacy page promises two things. A report is deleted once it has been analysed: that is done
by hand, on the volume. And no report is kept longer than 12 months: `expire` deletes a report older
than `REPORTS_MAX_AGE_DAYS` (364), from `tidy` at start-up and then every hour from `tidy_every`
(`app.py` starts it). That is a day short of a year on purpose: the shortest twelve months are 365
days, and a report can wait up to an hour (the interval) past the limit before a run finds it, so
none is kept past 364 days and an hour. The age is the older of the folder's own time and
`reportedAt` in its `result.json`; only folders named like a report are ever deleted. The volume is
in no backup (deploy/README.md), so deleting it here deletes it everywhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

from .engines import engines
from .models import Job, Result

log = logging.getLogger("decimer_api.reports")

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600
COPY_CHUNK = 64 * 1024
TEMP_PREFIX = ".tmp-"
STALE_TEMP_SECONDS = 3600
WARN_FRACTION = 0.8
SECONDS_PER_DAY = 86400
# How often `tidy_every` looks for reports past their age. The privacy page says 12 months at the
# latest (365 days at the shortest): the age limit (364 days) plus this interval stays within it,
# and test_a_report_never_outlives_the_twelve_months_the_page_promises holds the two together.
TIDY_INTERVAL_SECONDS = 3600
# What `report_name` makes: a job id (32 lowercase hex characters), a dash and a result index.
REPORT_NAME = re.compile(r"[0-9a-f]{32}-[0-9]{1,6}")

# Check the cap and create the folder as one step. One api process (compose runs exactly one).
_cap_lock = threading.Lock()


class ReportsFull(Exception):  # noqa: N818  (a condition, not a failure of the code)
    """`REPORTS_DIR` already holds the most reports (`what` is "reports") or bytes ("bytes")
    it may."""

    def __init__(self, what: str = "reports") -> None:
        super().__init__(what)
        self.what = what


def report_name(job_id: str, index: int) -> str:
    return f"{job_id}-{index}"


def _record(
    job: Job, result: Result, now: datetime, image: str | None, reason: str | None
) -> bytes:
    document = {
        "reportedAt": now.isoformat(),
        "jobId": job.id,
        "job": {
            "pages": job.pages,
            "pagesProcessed": job.pages_processed,
            "truncated": job.truncated,
            "warnings": [warning.code for warning in job.warnings],
        },
        "result": result.model_dump(mode="json", by_alias=True, exclude={"svg"}),
        "image": image,
        "imageOmitted": reason,
        "engines": [{"id": e.id, "version": e.version, "revision": e.revision} for e in engines()],
    }
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")


def count_reports(reports_dir: Path) -> int:
    """Finished reports: the folders that are not work in progress."""
    with os.scandir(reports_dir) as entries:
        return sum(1 for entry in entries if not entry.name.startswith(TEMP_PREFIX))


def folder_bytes(reports_dir: Path) -> int:
    """The bytes the finished reports hold (a `.tmp-*` folder is not one)."""
    total = 0
    with os.scandir(reports_dir) as entries:
        for entry in entries:
            if entry.name.startswith(TEMP_PREFIX) or not entry.is_dir(follow_symlinks=False):
                continue
            with os.scandir(entry.path) as files:
                total += sum(
                    item.stat(follow_symlinks=False).st_size
                    for item in files
                    if item.is_file(follow_symlinks=False)
                )
    return total


def _warn_when_nearly_full(used: int, limit: int, unit: str) -> None:
    """`unit` is what is counted: "reports" or "bytes"."""
    log.warning(
        "REPORTS_DIR holds %d of at most %d %s (%d%%): analyse and delete some",
        used,
        limit,
        unit,
        round(100 * used / limit),
    )


def _made_at(folder: Path) -> float:
    """When a report was made, in seconds since the epoch: the older of the folder's own time and
    the `reportedAt` it records. The folder's time alone could be reset by a copy of the volume;
    `reportedAt` alone by a folder without a readable `result.json`. Taking the older of the two
    can only delete earlier, never keep longer."""
    made = folder.stat().st_mtime
    try:
        with (folder / "result.json").open("rb") as record:
            reported = datetime.fromisoformat(json.load(record)["reportedAt"]).timestamp()
    except (OSError, ValueError, OverflowError, KeyError, TypeError, RecursionError):
        # (JSON errors are ValueErrors; a record nested too deep is a RecursionError, which is not.)
        return made
    return min(made, reported)


def expire(reports_dir: Path, max_age_days: int, *, now: float | None = None) -> int:
    """Delete the reports older than `max_age_days` days. Returns how many it removed. Only folders
    named like a report (`report_name`) are looked at: anything else on the volume, and a `.tmp-*`
    folder, is left alone. A folder that cannot be judged or removed is logged and skipped; the
    next run tries again."""
    if not reports_dir.is_dir():
        return 0
    moment = time.time() if now is None else now
    limit = max_age_days * SECONDS_PER_DAY
    with os.scandir(reports_dir) as entries:
        candidates = [
            Path(entry.path)
            for entry in entries
            if REPORT_NAME.fullmatch(entry.name) and entry.is_dir(follow_symlinks=False)
        ]
    removed = 0
    for folder in candidates:
        try:
            if moment - _made_at(folder) <= limit:
                continue
            shutil.rmtree(folder)
        except FileNotFoundError:  # taken away since it was listed
            continue
        except OSError:
            log.exception("could not delete the expired report %s", folder.name)
            continue
        removed += 1
    if removed:
        log.info("deleted %d reports older than %d days", removed, max_age_days)
    return removed


def tidy(
    reports_dir: Path,
    max_reports: int,
    max_bytes: int | None = None,
    max_age_days: int | None = None,
    *,
    now: float | None = None,
) -> int:
    """Housekeeping, at start-up and then hourly (`tidy_every`): remove `.tmp-*` folders older than
    an hour (a request that died halfway), delete the reports older than `max_age_days` (none is
    deleted for age when it is None), and warn if what is left is at 80 % of the cap, in number
    or in bytes (counted from the disk). Returns how many folders it removed. Nothing to do when
    the directory does not exist yet."""
    if not reports_dir.is_dir():
        return 0
    moment = time.time() if now is None else now
    removed = 0
    with os.scandir(reports_dir) as entries:
        stale = [
            Path(entry.path)
            for entry in entries
            if entry.name.startswith(TEMP_PREFIX)
            and entry.is_dir(follow_symlinks=False)
            and moment - entry.stat(follow_symlinks=False).st_mtime > STALE_TEMP_SECONDS
        ]
    for folder in stale:
        shutil.rmtree(folder, ignore_errors=True)
        removed += 1
    if max_age_days is not None:
        removed += expire(reports_dir, max_age_days, now=moment)
    count = count_reports(reports_dir)
    if count >= math.ceil(WARN_FRACTION * max_reports):
        _warn_when_nearly_full(count, max_reports, "reports")
    if max_bytes is not None:
        used = folder_bytes(reports_dir)
        if used >= WARN_FRACTION * max_bytes:
            _warn_when_nearly_full(used, max_bytes, "bytes")
    return removed


async def tidy_every(
    interval_seconds: float,
    reports_dir: Path,
    max_reports: int,
    max_bytes: int | None,
    max_age_days: int | None,
) -> None:
    """Run `tidy` every `interval_seconds`, forever (the first run is one interval from now: the api
    tidies once at start-up itself). A failed run is logged and does not stop the loop. Meant to
    be a task of the api's lifespan, cancelled at shutdown."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(tidy, reports_dir, max_reports, max_bytes, max_age_days)
        except Exception:
            log.exception("could not tidy REPORTS_DIR %s", reports_dir)


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    with os.fdopen(fd, "wb") as out:
        out.write(data)


def _copy(source: Path, target: Path) -> None:
    src = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    dst = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, FILE_MODE)
    with os.fdopen(src, "rb") as reader, os.fdopen(dst, "wb") as writer:
        shutil.copyfileobj(reader, writer, COPY_CHUNK)


def save_report(
    reports_dir: Path,
    job: Job,
    result: Result,
    crop: Path | None,
    *,
    now: datetime,
    max_reports: int,
    max_image_bytes: int,
    max_bytes: int = 2 * 1024**3,
) -> bool:
    """Write the report. True when it was made, False when this result had one already. Raises
    `ReportsFull` at the cap of reports or of bytes and `OSError` when the volume cannot be
    written."""
    reports_dir.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    final = reports_dir / report_name(job.id, result.index)
    if final.exists():
        return False
    # A first look, before any copying.
    if count_reports(reports_dir) >= max_reports:
        raise ReportsFull("reports")
    if folder_bytes(reports_dir) >= max_bytes:
        raise ReportsFull("bytes")

    image: str | None = None
    reason: str | None = None
    try:
        size = None if crop is None else crop.stat().st_size
    except FileNotFoundError:  # a DELETE came between the lookup and here
        size = None
    if size is None:
        reason = "missing"
    elif size > max_image_bytes:
        reason = "too_large"
    else:
        image = "crop.png"

    staging = reports_dir / f"{TEMP_PREFIX}{secrets.token_hex(8)}"
    staging.mkdir(mode=DIRECTORY_MODE)
    try:
        if image is not None and crop is not None:
            try:
                _copy(crop, staging / image)
            except FileNotFoundError:  # taken away since it was looked up
                image, reason = None, "missing"
        _write(staging / "result.json", _record(job, result, now, image, reason))
        new_bytes = sum(item.stat().st_size for item in staging.iterdir())
        with _cap_lock:  # the caps are checked and the folder made in one step
            if final.exists():  # made by a request at the same time
                shutil.rmtree(staging, ignore_errors=True)
                return False
            count = count_reports(reports_dir)
            used = folder_bytes(reports_dir)
            if count >= max_reports:
                raise ReportsFull("reports")
            if used + new_bytes > max_bytes:
                raise ReportsFull("bytes")
            os.rename(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if count + 1 == math.ceil(WARN_FRACTION * max_reports):
        _warn_when_nearly_full(count + 1, max_reports, "reports")
    if used < WARN_FRACTION * max_bytes <= used + new_bytes:
        _warn_when_nearly_full(used + new_bytes, max_bytes, "bytes")
    return True
