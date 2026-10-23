"""Reading a client's molfile with RDKit, in a child process (TODO, review).

The text gate (`molfile.py`) keeps out what it can recognise as too big, but a gate is a copy of a
parser, and RDKit can cost seconds and gigabytes that no count of the text predicts. So molfiles are
read by `AnalysisPool`: a few long-lived child processes (`analysis_worker`, started ahead of the
first request), each with a hard limit on its address space, one molfile at a time, a wall-clock
timeout per molfile. A child that is slow, runs out of memory, crashes or answers nonsense is
killed and replaced, and the request gets `TooComplex` (422 "too complex to read"); if no child
can be started it is 503. The api process itself never runs RDKit's parser, so a bad molfile cannot
hold its GIL or its memory: every other request carries on.

Places. The pool has `processes` places and no queue: a request takes a place for its molfile (or,
for an export, for all its edits, one after the other) and gets `503 busy` with `Retry-After` when
none is free (`capacity.Capacity`). A place is a child, so what runs at the same time is bounded by
the number of processes.

Memory. A child is about 70 MB resident when idle (RDKit loaded) and may allocate
`ANALYSIS_MEMORY_MB` (384) more before its address-space limit stops it, so the pool's ceiling is
`processes * (70 + 384)`, about 0.9 GB, and about 0.15 GB in normal use (a structure the gate
admits takes under 10 MB). The api container's `mem_limit` (compose, 4608m) is the sum of every
ceiling at once: the api and its spooled bodies 0.5 GB, two ingest children of up to 1.5 GB each,
and these two children at about 0.45 GB each. If something does take a child over a limit, the
kernel stops that child (a crashed child is an ordinary error here), not the api.

A child is replaced after `ANALYSIS_RECYCLE_AFTER` molfiles, because an address-space limit counts
what a long-lived process has mapped, not only what it uses, and fragmentation only grows it.

Environment hygiene is the ingest child's (`ingest_runner`): a minimal environment, its own session,
and the api itself made non-dumpable at start-up (`hardening.make_undumpable`), so a child cannot
read the api's environment or memory through /proc. It runs as the same user.

`InProcessAnalysis` is the same interface in a thread of this process, for tests and development:
no isolation, no timeout.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from . import analysis_protocol as protocol
from .capacity import Capacity
from .chemistry import Chemistry, analyse_molfile
from .errors import ApiError, ErrorCode
from .molfile import MolfileError, TooComplex
from .settings import Settings

log = logging.getLogger("decimer_api.analysis")

TOO_COMPLEX = "it is too complex to read: it took too long or needed too much memory"


class Session(Protocol):
    async def analyse(self, molfile: str) -> Chemistry: ...


class Analysis(Protocol):
    """What the routes use to read client molfiles."""

    capacity: Capacity

    async def analyse(self, molfile: str) -> Chemistry:
        """One molfile, in one place. Raises `MolfileError` (not a molfile, or `TooComplex`) and
        `ApiError` 503 (`busy`: no place free; `unavailable`: no child could be started)."""
        ...

    def session(self) -> contextlib.AbstractAsyncContextManager[Session]:
        """One place for several molfiles one after the other (an export's edits)."""
        ...

    async def start(self) -> None:
        """Get ready ahead of the first request."""
        ...

    async def aclose(self) -> None: ...


class InProcessAnalysis:
    """In a thread of this process. For tests and development: nothing here can be stopped."""

    def __init__(self, places: int) -> None:
        self.capacity = Capacity(places)

    async def analyse(self, molfile: str) -> Chemistry:
        return await self.capacity.run_in_thread(analyse_molfile, molfile)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Session]:
        async with self.capacity.hold():
            yield _ThreadSession()

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _ThreadSession:
    async def analyse(self, molfile: str) -> Chemistry:
        return await asyncio.to_thread(analyse_molfile, molfile)


class _Child:
    """One slot of the pool: at most one live process, used by one request at a time."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.served = 0
        self.lock = asyncio.Lock()  # a start or restart of the process, and its use


class AnalysisPool:
    def __init__(
        self,
        settings: Settings,
        *,
        argv: list[str] | None = None,
        spawn: Callable[..., Any] = asyncio.create_subprocess_exec,
    ) -> None:
        self.capacity = Capacity(settings.max_concurrent_analyses)
        self._memory_mb = settings.analysis_memory_mb
        self._timeout = settings.analysis_timeout_seconds
        self._batch = settings.analysis_batch_seconds
        self._startup = settings.analysis_startup_seconds
        self._recycle_after = settings.analysis_recycle_after
        self._argv = argv or [sys.executable, "-I", "-m", "decimer_api.analysis_worker"]
        self._spawn = spawn
        self._children = [_Child() for _ in range(settings.max_concurrent_analyses)]
        # Last in, first out: the child that has just been used (and is warm) is used again.
        self._idle: asyncio.LifoQueue[_Child] = asyncio.LifoQueue()
        for child in self._children:
            self._idle.put_nowait(child)
        self._background: set[asyncio.Task[None]] = set()
        self.started = 0  # processes started, for the logs and the tests

    # --- the interface -------------------------------------------------------------------------

    async def analyse(self, molfile: str) -> Chemistry:
        async with self.session() as session:
            return await session.analyse(molfile)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Session]:
        place = self.capacity.take()
        child = self._idle.get_nowait()  # a place is a child: there is one
        try:
            yield _PoolSession(self, child)
        finally:
            self._idle.put_nowait(child)
            place.release()

    async def start(self) -> None:
        """Start every child in the background; a request that comes first waits for its own."""
        for child in self._children:
            self._later(self._warm(child))

    async def aclose(self) -> None:
        for task in list(self._background):
            task.cancel()
        for task in list(self._background):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for child in self._children:
            await self._kill(child)

    # --- children --------------------------------------------------------------------------------

    def _later(self, work: Any) -> None:
        task = asyncio.ensure_future(work)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _warm(self, child: _Child) -> None:
        """Have the child's process running; a failure is met again by the request that needs it."""
        try:
            async with child.lock:
                await self._ensure_running(child)
        except ApiError:
            log.warning("an analysis process could not be started", exc_info=True)

    async def _ensure_running(self, child: _Child) -> asyncio.subprocess.Process:
        """The child's live process, started if there is none (call with `child.lock` held)."""
        process = child.process
        if process is not None and process.returncode is None:
            return process
        process = await self._start(child)
        child.process, child.served = process, 0
        return process

    async def _start(self, child: _Child) -> asyncio.subprocess.Process:
        try:
            process: asyncio.subprocess.Process = await self._spawn(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # Only what Python needs, as for the ingest child.
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
                start_new_session=True,
            )
        except OSError as error:
            raise self._unavailable("could not start") from error
        self.started += 1
        assert process.stdin is not None  # noqa: S101  (we asked for the pipe)
        try:
            process.stdin.write(protocol.encode({"memory_mb": self._memory_mb}))
            await process.stdin.drain()
            ready = await asyncio.wait_for(self._read(process), self._startup)
        except (TimeoutError, OSError, protocol.FrameError, asyncio.IncompleteReadError) as error:
            await _kill(process)
            raise self._unavailable("did not become ready") from error
        if ready.get("ready") is not True:
            await _kill(process)
            raise self._unavailable("answered nonsense at start-up")
        return process

    @staticmethod
    def _unavailable(why: str) -> ApiError:
        log.error("the analysis process %s", why)
        return ApiError(
            503, ErrorCode.UNAVAILABLE, "The service is temporarily unavailable. Please retry."
        )

    @staticmethod
    async def _read(process: asyncio.subprocess.Process) -> dict[str, Any]:
        assert process.stdout is not None  # noqa: S101  (we asked for the pipe)
        (length,) = protocol.HEADER.unpack(await process.stdout.readexactly(protocol.HEADER.size))
        if length > protocol.MAX_FRAME_BYTES:
            raise protocol.FrameError("the answer is too large")
        return protocol.decode(await process.stdout.readexactly(length))

    @staticmethod
    def _detach(child: _Child) -> asyncio.subprocess.Process | None:
        """Take the process away from the child at once (no await), so that nothing can use it
        again from this moment, whatever happens to whoever is about to kill it."""
        process, child.process = child.process, None
        return process

    async def _kill(self, child: _Child) -> None:
        process = self._detach(child)
        if process is not None:
            await _kill(process)

    async def _replace(self, child: _Child) -> None:
        """Kill the child's process and start another ahead of the next request."""
        await self._dispose(self._detach(child), child)

    async def _dispose(self, process: asyncio.subprocess.Process | None, child: _Child) -> None:
        if process is not None:
            await _kill(process)
        self._later(self._warm(child))


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Make sure the process, and anything it started, is gone and reaped."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()


class _PoolSession:
    def __init__(self, pool: AnalysisPool, child: _Child) -> None:
        self._pool = pool
        self._child = child
        self._began: float | None = None  # when the first molfile of this session was sent

    async def analyse(self, molfile: str) -> Chemistry:
        pool, child = self._pool, self._child
        async with child.lock:
            process = await pool._ensure_running(child)  # noqa: SLF001
            now = asyncio.get_running_loop().time()
            self._began = now if self._began is None else self._began
            # One molfile gets ANALYSIS_TIMEOUT_SECONDS, all of an export's ANALYSIS_BATCH_SECONDS.
            budget = min(pool._timeout, self._began + pool._batch - now)  # noqa: SLF001
            if budget <= 0:
                raise TooComplex(TOO_COMPLEX)
            try:
                answer = await asyncio.wait_for(self._exchange(process, molfile), budget)
            except (TimeoutError, OSError, protocol.FrameError, asyncio.IncompleteReadError):
                # Slow, dead or talking nonsense: it is stopped, and replaced for the next request.
                log.warning("an analysis process was stopped (timeout, crash or bad answer)")
                await pool._replace(child)  # noqa: SLF001
                raise TooComplex(TOO_COMPLEX) from None
            except BaseException:
                # The caller went away (cancelled) with the molfile on the pipe: the child's answer
                # is still coming and the next request on this child would read it as its own. The
                # process is taken away at once, and its killing is shielded: it is finished even
                # if this task is cancelled again.
                taken = pool._detach(child)  # noqa: SLF001
                await asyncio.shield(pool._dispose(taken, child))  # noqa: SLF001
                raise
            child.served += 1
            return await self._chemistry(answer)

    async def _exchange(self, process: asyncio.subprocess.Process, molfile: str) -> dict[str, Any]:
        assert process.stdin is not None  # noqa: S101
        process.stdin.write(protocol.encode({"molfile": molfile}))
        await process.stdin.drain()
        return await AnalysisPool._read(process)  # noqa: SLF001

    async def _chemistry(self, answer: dict[str, Any]) -> Chemistry:
        pool, child = self._pool, self._child
        if "ok" in answer:
            try:
                chemistry = Chemistry(**answer["ok"])
            except TypeError:
                await pool._replace(child)  # noqa: SLF001
                raise TooComplex(TOO_COMPLEX) from None
            if child.served >= pool._recycle_after:  # noqa: SLF001
                await pool._replace(child)  # noqa: SLF001
            return chemistry
        kind = answer.get("error")
        if kind == "molfile":
            raise MolfileError(str(answer.get("message", "it cannot be read as a molfile"))[:200])
        if kind == "internal":
            log.error("an analysis process failed: %s", answer.get("message"))
        await pool._replace(child)  # noqa: SLF001  (out of memory, or a bug: start afresh)
        raise TooComplex(TOO_COMPLEX)


def build_analysis(settings: Settings) -> AnalysisPool:
    return AnalysisPool(settings)
