"""Stopping a job that the user deleted (TODO: `DELETE /api/jobs/{id}`).

The api removes the record and sets a marker (`JobStore.cancel`). A job that is only queued needs
nothing more: the worker finds no record and skips it. A job that is *running* has to be stopped:
`run_cancellable` runs the pipeline next to a watcher that looks for the marker every
`CANCEL_POLL_SECONDS` (one Redis `EXISTS`), and when it appears cancels the run (the same
`CancelledError` a restart sends: the pipeline leaves the job unfinished and does not write it).
Then it deletes the record, should one have been written in the meantime, and the job's folder, so
that nothing of a deleted job stays behind. It does NOT give the client's slot back: the delete
kept it for a running job (`JobStore.cancel`, `cancelling_slot_seconds`) because the model services
go on with the calls this worker abandoned, and the slot is what limits how much work a client can
have going. Until the run stops, `JobStore.save` and `finish`
write nothing for the job, so it cannot reappear.

It wraps the pipeline from outside, on purpose: the pipeline does not know about cancelling.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from ..models import is_valid_job_id
from ..store import JobStore

log = logging.getLogger("decimer_api.worker")


async def _until_cancelled(job_id: str, store: JobStore, poll_seconds: float) -> None:
    """Returns when the job's marker exists. A failing Redis is no news: ask again."""
    while True:
        try:
            if await store.is_cancelled(job_id):
                return
        except Exception:
            log.warning("job %s: could not look for a cancellation", job_id, exc_info=True)
        await asyncio.sleep(poll_seconds)


async def _stop(*tasks: asyncio.Future[Any]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def run_cancellable(
    job_id: str,
    store: JobStore,
    run: Coroutine[Any, Any, None],
    *,
    poll_seconds: float,
    folder: Path,
) -> bool:
    """Run `run` (the pipeline of the job) and stop it if the job is deleted meanwhile. True when
    the job was cancelled. What `run` raises is raised, and so is a cancellation of this call
    (a restart), after the run was cancelled too."""
    pipeline = asyncio.ensure_future(run)
    watcher = asyncio.ensure_future(_until_cancelled(job_id, store, poll_seconds))
    try:
        await asyncio.wait({pipeline, watcher}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        await _stop(pipeline, watcher)
        raise
    if pipeline.done():
        await _stop(watcher)
        pipeline.result()  # raises what the pipeline raised
        # A deletion that came while the last save was on its way: nothing is kept either.
        cancelled = await store.is_cancelled(job_id)
    else:
        await _stop(pipeline)
        cancelled = True
    if cancelled:
        log.info("job %s: deleted by its owner; stopped and removed", job_id)
        await remove(job_id, store, folder)
    return cancelled


async def remove(job_id: str, store: JobStore, folder: Path) -> None:
    try:
        await store.delete_record(job_id)
        if is_valid_job_id(job_id):
            await asyncio.to_thread(shutil.rmtree, folder, True)
    except Exception:
        log.warning("job %s: cleaning up after a deletion failed", job_id, exc_info=True)
