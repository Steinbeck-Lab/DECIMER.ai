"""The TTL sweep: `python -m decimer_api.sweep [--every SECONDS]`.

Redis expires the job *records* by itself; nothing expires the *files*. The sweep deletes
`DATA_DIR/<job id>/` for every job that is gone or has passed its `expiresAt`:

* the record is gone (Redis expired it)           -> delete, but only when the folder is older
  than SWEEP_GRACE_SECONDS: the API creates the folder before it saves the record, so a young
  folder without a record is an upload in progress;
* the record exists and `expiresAt` has passed    -> delete the folder and the record
  (`expiresAt` is the moment of deletion: at that instant the job is already expired);
* the record exists and is live                   -> keep;
* the record cannot be read                       -> fall back to the age of the folder: delete
  it, and the record, once nothing in it has changed for JOB_TTL_SECONDS.

One folder that cannot be judged or removed is logged and counted as failed; it never stops the
others, and a failed run never stops the loop of `--every`.

Only directories named like a job id (32 hex characters) are considered, symlinks are never
followed, and anything else in DATA_DIR is left alone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from redis.asyncio import Redis

from .models import is_valid_job_id
from .settings import Settings
from .store import CorruptRecord, JobStore

log = logging.getLogger("decimer_api.sweep")


@dataclass
class SweepReport:
    removed: list[str] = field(default_factory=list)
    kept: int = 0
    young: int = 0  # no record, but too recent to judge
    failed: list[str] = field(default_factory=list)


def _newest_mtime(folder: Path) -> float:
    """Latest modification time of the folder, its entries and the entries of its sub-folders."""
    newest = folder.stat().st_mtime
    with os.scandir(folder) as entries:
        for entry in entries:
            newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
            if entry.is_dir(follow_symlinks=False):
                with os.scandir(entry.path) as inner:
                    for item in inner:
                        newest = max(newest, item.stat(follow_symlinks=False).st_mtime)
    return newest


def _job_folders(data_dir: Path) -> list[Path]:
    if not data_dir.is_dir():
        return []
    with os.scandir(data_dir) as entries:
        return [
            Path(entry.path)
            for entry in entries
            if is_valid_job_id(entry.name) and entry.is_dir(follow_symlinks=False)
        ]


async def sweep(settings: Settings, store: JobStore, *, now: datetime | None = None) -> SweepReport:
    moment = now or store.now()
    report = SweepReport()
    for folder in await asyncio.to_thread(_job_folders, settings.data_dir):
        try:
            await _sweep_folder(folder, settings, store, moment, report)
        except Exception:
            log.exception("could not sweep %s", folder.name)
            report.failed.append(folder.name)
    return report


async def _sweep_folder(
    folder: Path, settings: Settings, store: JobStore, moment: datetime, report: SweepReport
) -> None:
    corrupt = False
    try:
        job = await store.read(folder.name)
    except CorruptRecord:
        job, corrupt = None, True
        log.warning("unreadable record for job %s; judging its folder by age", folder.name)
    if job is not None and job.expires_at > moment:
        report.kept += 1
        return
    if job is None:
        # No record to say when the job ends: a folder without one may be an upload in progress
        # (give it the grace period); one with a garbage record is judged by the job TTL.
        limit = settings.job_ttl_seconds if corrupt else settings.sweep_grace_seconds
        try:
            age = moment.timestamp() - await asyncio.to_thread(_newest_mtime, folder)
        except FileNotFoundError:  # vanished while we looked
            return
        if age < limit:
            report.young += 1
            return
    try:
        await asyncio.to_thread(shutil.rmtree, folder)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("could not remove %s", folder.name)
        report.failed.append(folder.name)
        return
    if job is not None:
        await store.discard(job)
    elif corrupt:
        await store.delete_record(folder.name)
    report.removed.append(folder.name)


def _summary(report: SweepReport) -> str:
    return json.dumps(
        {
            "event": "sweep",
            "removed": len(report.removed),
            "kept": report.kept,
            "young": report.young,
            "failed": len(report.failed),
        }
    )


async def run(settings: Settings, redis: Redis, every: float | None) -> None:
    """Sweep once (`every` is None), or forever every `every` seconds."""
    store = JobStore(redis, settings)
    while True:
        if every is None:
            log.info(_summary(await sweep(settings, store)))
            return
        try:
            log.info(_summary(await sweep(settings, store)))
        except Exception:
            log.exception("sweep failed; trying again in %g s", every)
        await asyncio.sleep(every)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m decimer_api.sweep",
        description="Delete the folders of expired jobs under DATA_DIR.",
    )
    parser.add_argument(
        "--every",
        type=float,
        metavar="SECONDS",
        help="keep running and sweep at this interval (default: sweep once and exit)",
    )
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()

    async def go() -> None:
        redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            await run(settings, redis, arguments.every)
        finally:
            await redis.aclose()

    asyncio.run(go())


if __name__ == "__main__":
    main()
