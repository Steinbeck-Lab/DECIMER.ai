"""Running `ingest`: in a child process with limits (production), or in a thread (tests).

What the child process is, and is not, kept away from. It starts with a minimal environment (the
API's variables are not passed on) and the API process is made non-dumpable at start-up
(`hardening.make_undumpable`), so the child cannot read the API's environment or memory through
/proc. It still runs as the same user: it can read any file that user can read (a `.env`, for
one) and open network connections (to Redis, for one). Closing that needs the ingest to run
elsewhere: a sidecar container with no route to Redis and only DATA_DIR mounted (README).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import signal
import sys
from pathlib import Path
from typing import Protocol

from .errors import ApiError
from .models import JobWarning
from .settings import MIB, Settings
from .uploads import Ingested, Limits, Upload, classify, processing_limit

log = logging.getLogger("decimer_api.ingest")

# What a child may report, and the HTTP status that goes with each code. The child is trusted
# only this far: it decides the code, never the status.
CHILD_ERRORS = {
    "empty_upload": 400,
    "too_many_files": 400,
    "unsupported": 415,
    "mixed_upload": 400,
    "multiple_pdfs": 400,
    "corrupt_file": 400,
    "encrypted_pdf": 400,
    "too_many_pixels": 413,
    "processing_limit": 413,
}
# A child killed by one of these hit a limit (CPU time, file size) or ran out of memory.
LIMIT_SIGNALS = {signal.SIGKILL, signal.SIGXCPU, signal.SIGXFSZ}
CPU_HEADROOM_SECONDS = 5
# A child killed at the deadline having used less than this share of it in CPU time was waiting,
# not parsing.
LITTLE_CPU_FRACTION = 0.25
STDERR_LOG_BYTES = 2000


class IngestRunner(Protocol):
    """Validate and store the uploads of one job under `job_dir/input/`."""

    async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested: ...


class InProcessIngest:
    """The same code in a thread of this process: no isolation. For tests and development."""

    def __init__(self, limits: Limits) -> None:
        self._limits = limits

    async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
        from .ingest import run_guarded  # imported here: the API process never loads the parsers

        return await asyncio.to_thread(run_guarded, uploads, job_dir / "input", self._limits)


class SubprocessIngest:
    """One short-lived child process per upload, with a deadline and hard resource limits."""

    def __init__(self, settings: Settings, *, argv: list[str] | None = None) -> None:
        self._limits = Limits.from_settings(settings)
        self._timeout = settings.ingest_timeout_seconds
        self._memory_mb = settings.ingest_memory_mb
        self._max_file_bytes = max(
            3 * settings.max_image_pixels + 64 * MIB, settings.max_upload_bytes
        )
        self._argv = argv or [sys.executable, "-I", "-m", "decimer_api.ingest_worker"]
        self._retry_after = settings.queue_retry_after_seconds
        self.last_pid: int | None = None

    async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
        classify(uploads, self._limits)  # refuse garbage without starting a process
        incoming = job_dir / "incoming"
        try:
            await asyncio.to_thread(_stage, uploads, incoming)
            request = {
                "input_dir": str(job_dir / "input"),
                "files": [
                    {"path": str(incoming / f"{n:04d}.bin"), "name": upload.name}
                    for n, upload in enumerate(uploads, start=1)
                ],
                "limits": {
                    "max_pdf_pages": self._limits.max_pdf_pages,
                    "max_image_pixels": self._limits.max_image_pixels,
                    "max_job_pixels": self._limits.max_job_pixels,
                    "max_upload_files": self._limits.max_upload_files,
                },
                "memory_mb": self._memory_mb,
                "cpu_seconds": math.ceil(self._timeout) + CPU_HEADROOM_SECONDS,
                "max_file_bytes": self._max_file_bytes,
            }
            return await self._run(json.dumps(request).encode(), job_dir)
        finally:
            await asyncio.to_thread(shutil.rmtree, incoming, True)

    def _deadline_error(self, cpu_seconds: float | None) -> ApiError:
        """The child hit the wall-clock deadline. If it used little CPU in all that time it was
        starved or slow to start: the host is busy, not the file at fault, so say so (503)."""
        if cpu_seconds is not None and cpu_seconds < self._timeout * LITTLE_CPU_FRACTION:
            return ApiError(
                503,
                "busy",
                "The service is busy processing other uploads. Please try again in a moment.",
                headers={"Retry-After": str(self._retry_after)},
            )
        return processing_limit(f"took too long ({self._timeout:g} s limit)")

    async def _run(self, request: bytes, job_dir: Path) -> Ingested:
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # captured, never forwarded to the server's stderr
            cwd=job_dir,
            # Only what Python needs: the API's own environment is not handed down. (The child
            # could still look at the API's /proc/<pid>/environ; the API is made non-dumpable
            # to prevent that. It can still read files of this user and use the network.)
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
            start_new_session=True,
        )
        self.last_pid = process.pid
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request), timeout=self._timeout
            )
        except TimeoutError as error:
            # Before the kill: how much CPU did the child get to use?
            raise self._deadline_error(_cpu_seconds(process.pid)) from error
        finally:
            await _kill(process)

        if stderr:
            log.warning(
                "ingest child said: %s", stderr[-STDERR_LOG_BYTES:].decode(errors="replace")
            )
        return _interpret(process.returncode, stdout)


def _cpu_seconds(pid: int) -> float | None:
    """CPU time (user + system) a live process has used, from /proc; None if unavailable."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat.rsplit(")", 1)[1].split()  # after the command name, which may have spaces
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return None


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Make sure the child, and anything it started, is gone and reaped."""
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def _stage(uploads: list[Upload], incoming: Path) -> None:
    """Copy the uploads to files the child can open. Pure copying: nothing is parsed."""
    incoming.mkdir(parents=True)
    for number, upload in enumerate(uploads, start=1):
        upload.data.seek(0)
        with open(incoming / f"{number:04d}.bin", "wb") as target:
            shutil.copyfileobj(upload.data, target)


def _interpret(returncode: int | None, stdout: bytes) -> Ingested:
    answer = _parse(stdout)
    if answer is not None:
        return answer
    if returncode is not None and returncode < 0:
        if -returncode in LIMIT_SIGNALS:
            raise processing_limit("needed too many resources")
        raise ApiError(
            400, "corrupt_file", "The file could not be processed: it is damaged or malformed."
        )
    log.error("ingest child exited with %s and no usable answer", returncode)
    raise ApiError(503, "unavailable", "The service is temporarily unavailable. Please retry.")


def _parse(stdout: bytes) -> Ingested | None:
    try:
        payload = json.loads(stdout)
        if "ok" in payload:
            ok = payload["ok"]
            return Ingested(
                pages=int(ok["pages"]),
                pages_processed=int(ok["pages_processed"]),
                truncated=bool(ok["truncated"]),
                warnings=[JobWarning(code=w["code"], message=w["message"]) for w in ok["warnings"]],
            )
        error = payload["error"]
        code, message = str(error["code"]), str(error["message"])[:500]
    except (ValueError, KeyError, TypeError):
        return None
    if code not in CHILD_ERRORS:  # "internal_error" and anything unexpected
        log.error("ingest child reported %s: %s", code, message)
        raise ApiError(503, "unavailable", "The service is temporarily unavailable. Please retry.")
    raise ApiError(CHILD_ERRORS[code], code, message)
