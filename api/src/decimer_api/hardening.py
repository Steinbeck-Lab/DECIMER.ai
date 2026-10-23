"""Process hardening for the API process, and what its child processes (ingest, analysis, render)
share. Standard library only: the children import it before they apply their limits."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio

log = logging.getLogger("decimer_api.hardening")

PR_SET_DUMPABLE = 4
# The whole environment of a child process: only what Python needs, none of the parent's variables
# (REDIS_URL, IP_HASH_SECRET, ...).
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}


async def kill_child(process: asyncio.subprocess.Process) -> None:
    """Make sure the child, and anything it started, is gone and reaped."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


def current_address_space() -> int:
    """Bytes of address space in use now (Linux), or 0 when unknown."""
    try:
        with open("/proc/self/statm") as statm:
            return int(statm.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def _is_linux() -> bool:
    return sys.platform == "linux"


def make_undumpable() -> bool:
    """Mark this process non-dumpable (`prctl(PR_SET_DUMPABLE, 0)`). Linux only.

    The ingest children run as the same user as the API. A dumpable process lets a same-user
    process read `/proc/<pid>/environ` (where IP_HASH_SECRET and REDIS_URL live) and
    `/proc/<pid>/mem`; a non-dumpable one does not. It also disables core dumps, so a crash
    cannot write the secrets to disk. The cost: debuggers (py-spy, gdb) cannot attach to the API
    unless run as root.

    This closes that one door. It does not stop a child from reading files the user can read
    (such as a `.env`) or from opening network connections (such as to Redis); that needs the
    child to run elsewhere, see the README.

    Returns whether the flag was set; elsewhere it is a logged no-op.
    """
    if not _is_linux():
        log.info(
            "PR_SET_DUMPABLE skipped: not Linux. The ingest children can read this process's "
            "environment."
        )
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError) as error:
        log.warning("PR_SET_DUMPABLE could not be set: %s", error)
        return False
    if result != 0:
        log.warning("prctl(PR_SET_DUMPABLE) failed with errno %d", ctypes.get_errno())
        return False
    return True
