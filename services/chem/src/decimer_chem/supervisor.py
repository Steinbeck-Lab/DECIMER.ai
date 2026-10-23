"""Runs the naming engine in a separate process and keeps it on a deadline.

Why a process and not a thread. Orthonym is pure Python (CPU bound, holds the GIL) around an
in-process JVM: a pathological molecule in a thread would starve the event loop that answers
/healthz and the 503s, a thread cannot be interrupted or killed, and a JVM crash would take the
whole service with it. So the engine, and the one JVM it starts, live in a child process:

* the child names one structure at a time on its main thread, where Orthonym's own wall-clock
  limit (SIGALRM, `orthonym.wallclock`) works and puts its internal state back after a timeout;
* the parent waits for each answer with a hard deadline (soft limit plus a grace period). A child
  that misses it, for instance stuck in a JNI call that never returns to Python to run the signal
  handler, is killed (SIGKILL takes the JVM with it);
* a killed, crashed or retired child is replaced at once, in the background, so the next request
  does not pay for the JVM start; a request waits for a replacement only as long as its budget;
* the JVM is started once per child, when the child starts, never per request.

The child does two things, for two kinds of request: it names a structure (Orthonym, `name`) and it
lays a structure out for a molblock (CDK, `cdk`, the D4 fallback). Both run one at a time on the
same pipe, under the same deadlines, with the same failure rules: whatever goes wrong with one
structure is that structure's result, and a killed, crashed or retired child is replaced.

The parent and child talk over a pipe, one message each way per structure. **The messages are
JSON, never pickle**: a pickle imports whatever class it names, and Orthonym's names can be `str`
subclasses that drag Orthonym and RDKit into the web process (42 MB to 104 MB); and a pickle from
the other end is code. The parent also accepts nothing but plain strings and nulls in a result.
The child runs in a scratch directory of its own, so that a JVM crash report (`hs_err_pid*.log`,
which holds memory contents) lands there and is removed when the service stops, not in the app dir.
"""

from __future__ import annotations

import asyncio
import ctypes
import importlib
import json
import logging
import multiprocessing
import os
import shutil
import signal
import tempfile
import time
import uuid
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Protocol, cast

from decimer_service.logs import configure

from .logs import RedactOrthonym, log_failure
from .results import (
    ENGINE_ERROR_REASON,
    ENGINE_UNAVAILABLE_REASON,
    MAX_MOLFILE_BYTES,
    TIMEOUT_REASON,
    CdkResult,
    NameResult,
    looks_like_molfile,
)
from .settings import Settings

log = logging.getLogger("decimer_chem")

DEFAULT_FACTORY = "decimer_chem.engine:OrthonymEngine"

_TIMEOUT = NameResult(None, None, None, TIMEOUT_REASON)
_ENGINE_ERROR = NameResult(None, None, None, ENGINE_ERROR_REASON)
_UNAVAILABLE = NameResult(None, None, None, ENGINE_UNAVAILABLE_REASON)
_CDK_TIMEOUT = CdkResult(None, TIMEOUT_REASON)
_CDK_ENGINE_ERROR = CdkResult(None, ENGINE_ERROR_REASON)
_CDK_UNAVAILABLE = CdkResult(None, ENGINE_UNAVAILABLE_REASON)

#: Relative paths in these are made absolute before the child changes into its scratch directory.
_PATH_ENV = ("ORTHONYM_JAR_DIR", "ORTHONYM_CACHE_DIR", "XDG_CACHE_HOME", "JAVA_HOME")

#: The largest message either end will read. A request is a SMILES of at most 2000 characters; a
#: reply is a name (a few hundred characters) or a molblock (at most `MAX_MOLFILE_BYTES`, 256 KiB,
#: about 28 KB for the 300 atoms CDK is given); anything near a megabyte is a bug or worse, and is
#: refused without reading it.
MAX_MESSAGE_BYTES = 1 << 20

#: Two crashes in a row inside one request: the engine is not coming back for this request.
MAX_CONSECUTIVE_CRASHES = 2


class EngineUnavailable(RuntimeError):
    """No naming process could be started (or none was usable for any structure of a request)."""


class EngineFailure(RuntimeError):
    """The naming process died, or broke the protocol, in the middle of a call."""


class Engine(Protocol):
    """What runs inside the naming process."""

    def start(self) -> dict[str, object]:
        """Check the jars, start the JVM and warm up; raise if the engine cannot work."""

    def name(self, smiles: str, timeout_s: float) -> NameResult:
        """Name one structure within `timeout_s` (a soft limit). Any exception is that structure's
        `engine_error`. An engine may set `recycle_requested` to have its process replaced."""

    def cdk(self, smiles: str, timeout_s: float) -> CdkResult:
        """Lay one structure out as a V2000 molblock within `timeout_s`, or decline. The same
        rules as `name`."""


class _Result(Protocol):
    @property
    def reason(self) -> str | None: ...

    def as_dict(self) -> dict[str, str | None]: ...


@dataclass(frozen=True)
class _Operation[R: _Result]:
    """One kind of request. `request` is its tag on the pipe and the name of the engine method that
    serves it; `reply` is the tag of its answer; `parse` accepts a reply's payload or refuses it;
    `limit` is the soft limit for one structure; the three results are what a structure that timed
    out, hit an engine error or could not be tried with no process available gets."""

    request: str
    reply: str
    parse: Callable[[object], R | None]
    limit: Callable[[Settings], float]
    timeout: R
    engine_error: R
    unavailable: R
    #: After the first `timeout` in a batch, answer the rest of it with `timeout` without calling
    #: the engine. For CDK a timeout means a runaway Java thread and a replaced process (about 3.5 s
    #: for the next JVM), so each further structure of a slow batch would cost another replacement
    #: and hold the one process, and every name waiting for it, for the whole request budget.
    give_up_after_timeout: bool = False


# --- the child ---------------------------------------------------------------------------


def _load_factory(spec: str) -> Callable[[Settings], Engine]:
    module, _, attribute = spec.partition(":")
    return cast(Callable[[Settings], Engine], getattr(importlib.import_module(module), attribute))


def _die_with_parent() -> None:
    """Linux: have the kernel SIGKILL this process when its parent dies.

    Without it, a `kill -9` or an OOM kill of the service would leave the child (and its JVM)
    running, possibly stuck in a hang the parent was about to kill. Best effort: no-op where
    `prctl` does not exist; a failing call is logged.
    """
    try:
        libc = ctypes.CDLL(None)
        result = libc.prctl(1, int(signal.SIGKILL))  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        return
    if result != 0:
        log.warning("could not set the parent-death signal; this process may outlive its parent")
        return
    parent = multiprocessing.parent_process()
    if parent is not None and os.getppid() != parent.pid:
        os._exit(0)  # the parent died before the call above took effect


def absolutize_path_env(environ: MutableMapping[str, str]) -> None:
    """Make relative paths among the settings absolute (the child is about to change directory)."""
    for name in _PATH_ENV:
        value = environ.get(name)
        if value and not os.path.isabs(value):
            environ[name] = os.path.abspath(value)


def worker_main(conn: Connection, factory: str, settings: Settings, scratch: str) -> None:
    """Entry point of the naming process (module level so a spawned child can import it)."""
    configure()
    _die_with_parent()
    # Orthonym logs at INFO for every molecule, including the name it picked ("winner='...'"):
    # noise, and the structures people draw are not for the logs. Its warnings and errors still
    # show, reduced to logger, level and one fixed sentence (RedactOrthonym).
    logging.getLogger("orthonym").setLevel(logging.WARNING)
    for handler in logging.getLogger().handlers:
        handler.addFilter(RedactOrthonym())
    # The parent owns this process's life; a Ctrl-C meant for the service must not traceback here.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        absolutize_path_env(os.environ)
        os.chdir(scratch)
        engine = _load_factory(factory)(settings)
        info = engine.start()
    except Exception as exc:
        log.exception("naming process failed to start")
        _send(conn, ("startup_error", f"{type(exc).__name__}: {exc}"))
        return
    if not _send(conn, ("ready", info)):
        return
    while True:
        try:
            message = json.loads(conn.recv_bytes(MAX_MESSAGE_BYTES))
        except (EOFError, OSError, ValueError, RecursionError):
            return  # the parent is gone, or no longer speaks to us
        if message[0] == "stop":
            return
        operation = _OPERATIONS.get(message[0])
        if operation is None:
            return  # the parent no longer speaks our protocol
        _, rid, smiles, timeout_s = message
        try:
            result = getattr(engine, operation.request)(smiles, timeout_s)
        except Exception as exc:
            # One structure's failure (a bug in Orthonym or CDK, say) is that structure's answer.
            # Only the exception's type and frames are logged: its message can carry the SMILES.
            log_failure(log, "engine raised", exc, error_id=uuid.uuid4().hex[:12])
            result = operation.engine_error
        recycle = bool(getattr(engine, "recycle_requested", False))
        if not _send(conn, (operation.reply, rid, result.as_dict(), recycle)) or recycle:
            return  # a recycled process leaves after its answer; the parent starts the next one


def _send(conn: Connection, message: tuple[Any, ...]) -> bool:
    try:
        conn.send_bytes(json.dumps(message, default=str).encode())
    except (BrokenPipeError, EOFError, OSError):
        return False
    return True


# --- the parent --------------------------------------------------------------------------


def _exchange(conn: Connection, message: tuple[Any, ...] | None, wait_s: float) -> list[Any] | None:
    """Send `message` (if any) and wait up to `wait_s` for the reply; None if none came in time.

    Runs in a thread: the pipe calls block. Raises EngineFailure if the other end is gone or sent
    anything but a modest JSON array that starts with a word: a pickle (never loaded), a message
    over `MAX_MESSAGE_BYTES` (not read), bytes that are not JSON, nesting deep enough to overflow
    the parser, a number or an object. Whatever the break, the caller treats it like a crash.
    """
    try:
        if message is not None:
            conn.send_bytes(json.dumps(message).encode())
        if not conn.poll(wait_s):
            return None
        reply = json.loads(conn.recv_bytes(MAX_MESSAGE_BYTES))
    except EOFError as exc:
        raise EngineFailure("naming process died") from exc
    except OSError as exc:  # a broken pipe, or a message over the limit ("bad message length")
        raise EngineFailure(f"naming process pipe failed: {exc}") from exc
    except (ValueError, RecursionError) as exc:  # not JSON (or not UTF-8), or nested too deeply
        raise EngineFailure("naming process sent something that is not valid JSON") from exc
    if not isinstance(reply, list) or not reply or not isinstance(reply[0], str):
        raise EngineFailure("naming process sent a reply that is not a JSON array with a tag")
    return reply


def _encodes(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def parse_result(payload: object) -> NameResult | None:
    """A `NameResult` from a reply, or None unless it is exactly four plain strings or nulls."""
    keys = {"name", "tier", "verified", "reason"}
    if not isinstance(payload, dict) or set(payload) != keys:
        return None
    if any(value is not None and type(value) is not str for value in payload.values()):
        return None
    if any(value is not None and not _encodes(value) for value in payload.values()):
        return None  # a lone surrogate ("\\ud800") is valid JSON but cannot be written as UTF-8
    return NameResult(payload["name"], payload["tier"], payload["verified"], payload["reason"])


def parse_cdk_result(payload: object) -> CdkResult | None:
    """A `CdkResult` from a reply, or None unless it is exactly two plain strings or nulls, exactly
    one of them set, and a molblock (if there is one) that has the shape of a V2000 file, encodes as
    UTF-8 and is within `MAX_MOLFILE_BYTES`."""
    if not isinstance(payload, dict) or set(payload) != {"molfile", "reason"}:
        return None
    molfile, reason = payload["molfile"], payload["reason"]
    if any(value is not None and type(value) is not str for value in (molfile, reason)):
        return None
    if (molfile is None) == (reason is None):
        return None  # a molblock and a reason, or neither: not an answer
    if reason is not None and not _encodes(reason):
        return None
    if molfile is not None and (
        not _encodes(molfile)
        or len(molfile.encode("utf-8")) > MAX_MOLFILE_BYTES
        or not looks_like_molfile(molfile)
    ):
        return None
    return CdkResult(molfile, reason)


_NAME = _Operation(
    "name",
    "result",
    parse_result,
    lambda s: s.name_timeout_s,
    _TIMEOUT,
    _ENGINE_ERROR,
    _UNAVAILABLE,
)
_CDK = _Operation(
    "cdk",
    "cdk_result",
    parse_cdk_result,
    lambda s: s.cdk_timeout_s,
    _CDK_TIMEOUT,
    _CDK_ENGINE_ERROR,
    _CDK_UNAVAILABLE,
    give_up_after_timeout=True,
)
_OPERATIONS: dict[str, _Operation[Any]] = {op.request: op for op in (_NAME, _CDK)}


@dataclass
class _Worker:
    process: BaseProcess
    conn: Connection
    info: dict[str, object]


def reap(worker: _Worker) -> None:
    """Wait for a killed process, close its pipe and release it. Blocking: run it in a thread."""
    worker.process.join(5)
    worker.conn.close()
    try:
        worker.process.close()
    except ValueError:  # "Cannot close a process while it is still running": the join timed out
        log.warning("naming process could not be closed", extra={"pid": worker.process.pid})


class ProcessNamer:
    """The production `Namer`: names structures one at a time in a supervised child process."""

    def __init__(self, settings: Settings, factory: str = DEFAULT_FACTORY) -> None:
        self._settings = settings
        self._factory = factory
        self._ctx = multiprocessing.get_context("spawn")  # never fork a process holding threads
        self._worker: _Worker | None = None
        self._spawning: asyncio.Task[None] | None = None
        self._reapers: set[asyncio.Future[None]] = set()
        self._lock = asyncio.Lock()
        self._active = 0
        self._rid = 0
        self._failed: tuple[float, str] | None = None
        self._scratch: str | None = None
        self._stopped = False
        self.hard_kills = 0

    @property
    def busy(self) -> bool:
        """True while a naming call is in flight (a replacement being started is not one)."""
        return self._active > 0

    @property
    def starting(self) -> bool:
        return self._spawning is not None

    @property
    def pid(self) -> int | None:
        return self._worker.process.pid if self._worker else None

    @property
    def info(self) -> dict[str, object]:
        return dict(self._worker.info) if self._worker else {}

    async def start(self) -> None:
        self._failed = None
        self._stopped = False
        try:
            worker = await self._acquire(
                self._settings.startup_timeout_s + self._settings.kill_grace_s
            )
            if worker is None:
                raise RuntimeError("naming process did not become ready")
        except BaseException:
            await self.stop()  # a start that failed leaves nothing behind (its scratch directory)
            raise

    async def stop(self) -> None:
        self._stopped = True  # first: nothing started from here on, whatever is still in flight
        spawning = self._spawning
        if spawning is not None:
            spawning.cancel()
            await asyncio.gather(spawning, return_exceptions=True)
        self._discard()
        if self._reapers:
            await asyncio.gather(*self._reapers, return_exceptions=True)
        scratch, self._scratch = self._scratch, None
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)

    async def name_many(self, smiles: Sequence[str]) -> list[NameResult]:
        """One result per SMILES, in order; see `_call_one` for what each failure becomes.

        Raises EngineUnavailable only when no structure at all could be tried.
        """
        return await self._batch(_NAME, smiles)

    async def cdk_many(self, smiles: Sequence[str]) -> list[CdkResult]:
        """One CDK result per SMILES, in order (a molblock or a decline), under the same rules as
        `name_many`; the two take turns for the one process."""
        return await self._batch(_CDK, smiles)

    async def _batch[R: _Result](self, op: _Operation[R], smiles: Sequence[str]) -> list[R]:
        async with self._lock:
            self._active += 1
            try:
                return await self._run_batch(op, smiles)
            finally:
                self._active -= 1

    async def _run_batch[R: _Result](self, op: _Operation[R], smiles: Sequence[str]) -> list[R]:
        budget_ends = time.monotonic() + self._settings.batch_timeout_s
        results: list[R] = []
        crashes = 0
        unavailable: str | None = None
        gave_up = False
        for item in smiles:
            if unavailable is not None:
                results.append(op.unavailable)
                continue
            if gave_up:
                results.append(op.timeout)  # an earlier structure timed out: not even tried
                continue
            remaining = budget_ends - time.monotonic()
            if remaining <= 0:
                results.append(op.timeout)  # the budget is spent: not even tried
                continue
            try:
                result, crashed = await self._call_one(
                    op, item, min(op.limit(self._settings), remaining), remaining
                )
            except EngineUnavailable as exc:
                unavailable = str(exc)
                results.append(op.unavailable)
                continue
            results.append(result)
            gave_up = op.give_up_after_timeout and result.reason == TIMEOUT_REASON
            crashes = crashes + 1 if crashed else 0
            if crashes >= MAX_CONSECUTIVE_CRASHES:
                unavailable = "the naming process crashed twice in a row"
        if results and all(r.reason == ENGINE_UNAVAILABLE_REASON for r in results):
            raise EngineUnavailable(unavailable or "naming engine unavailable")
        return results

    # -- one structure --

    async def _call_one[R: _Result](
        self, op: _Operation[R], smiles: str, limit_s: float, remaining_s: float
    ) -> tuple[R, bool]:
        """(result, the process crashed). A timeout, a crash and an engine error are each that
        structure's result; the process is replaced in the background after a kill or a crash."""
        worker = await self._acquire(remaining_s)
        if worker is None:
            return op.timeout, False  # the replacement is still starting and the budget is gone
        self._rid += 1
        rid = self._rid
        wait_s = limit_s + self._settings.kill_grace_s
        try:
            reply = await asyncio.to_thread(
                _exchange, worker.conn, (op.request, rid, smiles, limit_s), wait_s
            )
        except EngineFailure as exc:
            # The reason is one of the fixed texts above: no structure, no engine message.
            log.error(
                "naming process failed while naming a structure",
                extra={"pid": worker.process.pid, "reason": str(exc)},
            )
            self._replace()
            return op.engine_error, True
        except BaseException:
            # Cancelled mid-flight: never leave a reply in the pipe for the next call to mistake
            # for its own.
            self._discard()
            raise
        if reply is None:
            self.hard_kills += 1
            log.warning(
                "naming process missed its deadline and was killed",
                extra={"pid": worker.process.pid, "deadline_s": round(wait_s, 2)},
            )
            self._replace()
            return op.timeout, False
        result = op.parse(reply[2]) if len(reply) == 4 and reply[0] == op.reply else None
        if result is None or reply[1] != rid:
            log.error("naming process broke the protocol", extra={"pid": worker.process.pid})
            self._replace()
            return op.engine_error, True
        if reply[3]:  # the engine asked to be replaced (its state could not be restored)
            log.warning("naming process asked to be replaced", extra={"pid": worker.process.pid})
            self._replace()
        return result, False

    # -- the process --

    def _replace(self) -> None:
        """Drop the current process and start its successor in the background."""
        self._discard()
        self._start_spawn()

    async def _acquire(self, budget_s: float) -> _Worker | None:
        """A ready worker, starting one if there is none.

        None: a replacement is still starting when `budget_s` ran out (it keeps starting).
        Raises EngineUnavailable if there is none and none can be started.
        """
        worker = self._worker
        if worker is not None:
            if worker.process.is_alive():
                return worker
            log.warning("naming process had died; starting a new one")
            self._discard()
        if self._stopped:
            raise EngineUnavailable("the naming service has been stopped")
        if self._spawning is None:
            if self._failed is not None and time.monotonic() < self._failed[0]:
                raise EngineUnavailable(self._failed[1])
            self._start_spawn()
        spawning = self._spawning
        assert spawning is not None  # noqa: S101  # _start_spawn just set it
        try:
            await asyncio.wait_for(asyncio.shield(spawning), max(budget_s, 0.0))
        except TimeoutError:
            return None
        except Exception as exc:
            raise EngineUnavailable(str(exc)) from exc
        return self._worker

    def _start_spawn(self) -> None:
        if self._stopped or self._spawning is not None:
            return
        task = asyncio.get_running_loop().create_task(self._spawn_and_keep(), name="chem-spawn")
        task.add_done_callback(self._spawn_done)
        self._spawning = task

    def _spawn_done(self, task: asyncio.Task[None]) -> None:
        # A done callback, not a `finally` in the task: a task cancelled before its first step never
        # runs its body, and `starting` would stay true for ever.
        if self._spawning is task:
            self._spawning = None
        if not task.cancelled():
            task.exception()  # seen: it is reported through `_failed` and to whoever awaits it

    async def _spawn_and_keep(self) -> None:
        try:
            self._worker = await self._spawn()
            self._failed = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._failed = (time.monotonic() + self._settings.respawn_backoff_s, str(exc))
            log.error("naming process could not be started", extra={"reason": str(exc)})
            raise

    async def _spawn(self) -> _Worker:
        start = time.monotonic()
        if self._scratch is None:
            self._scratch = tempfile.mkdtemp(prefix="decimer-chem-")
        parent_conn, child_conn = self._ctx.Pipe()
        process = self._ctx.Process(
            target=worker_main,
            args=(child_conn, self._factory, self._settings, self._scratch),
            name="decimer-chem-engine",
            daemon=True,
        )
        # Started on the event loop's thread, not a pool thread: the kernel's parent-death signal
        # follows the thread that created the child, and that must live as long as the service.
        process.start()
        child_conn.close()
        worker = _Worker(process, parent_conn, {})
        try:
            reply = await asyncio.to_thread(
                _exchange, parent_conn, None, self._settings.startup_timeout_s
            )
        except EngineFailure as exc:
            await asyncio.to_thread(process.join, 5)
            code = process.exitcode
            self._kill(worker)
            raise RuntimeError(
                f"naming process exited during startup (exit code {code}); see its log above"
            ) from exc
        except BaseException:
            self._kill(worker)
            raise
        if reply is None:
            self._kill(worker)
            raise RuntimeError(
                f"naming process was not ready after {self._settings.startup_timeout_s:g} s"
            )
        if reply[0] != "ready":
            self._kill(worker)
            raise RuntimeError(f"naming process could not start: {reply[1]}")
        worker.info = dict(reply[1]) if isinstance(reply[1], dict) else {}
        log.info(
            "naming process ready",
            extra={
                "pid": process.pid,
                "seconds": round(time.monotonic() - start, 2),
                **worker.info,
            },
        )
        return worker

    def _kill(self, worker: _Worker) -> None:
        """SIGKILL now (cheap, on the loop); the wait, the pipe and the release run in a thread."""
        worker.process.kill()
        future = asyncio.get_running_loop().run_in_executor(None, reap, worker)
        self._reapers.add(future)
        future.add_done_callback(self._reapers.discard)

    def _discard(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            self._kill(worker)
