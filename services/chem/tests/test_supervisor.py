"""The supervisor with real child processes running a fake engine: deadlines, kills, restarts."""

from __future__ import annotations

import asyncio
import glob
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from decimer_chem import supervisor
from decimer_chem.results import TIMEOUT_REASON, NameResult
from decimer_chem.settings import Settings
from decimer_chem.supervisor import EngineUnavailable, ProcessNamer

FAST = Settings(
    name_timeout_s=0.5,
    batch_timeout_s=10.0,
    kill_grace_s=0.5,
    startup_timeout_s=30.0,
    respawn_backoff_s=0.0,
)


def make(settings: Settings = FAST) -> ProcessNamer:
    return ProcessNamer(settings, factory="fakes:make_engine")


@pytest.fixture
async def namer() -> AsyncIterator[ProcessNamer]:
    n = make()
    await n.start()
    try:
        yield n
    finally:
        await n.stop()


async def pid_of(namer: ProcessNamer) -> int:
    [r] = await namer.name_many(["PID"])
    assert r.name is not None
    return int(r.name)


async def bounded[T](coro: Any, seconds: float = 20.0) -> T:
    """Every wait is bounded, so code without a deadline fails these tests instead of hanging."""
    return await asyncio.wait_for(coro, seconds)  # type: ignore[no-any-return]


async def until(condition: Any, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        await asyncio.sleep(0.05)


def reasons(results: list[Any]) -> list[str | None]:
    return [r.reason for r in results]


async def test_names_come_back_in_order_from_a_separate_process(namer: ProcessNamer) -> None:
    results = await namer.name_many(["a", "b", "c"])
    assert [r.name.split("@")[0] for r in results if r.name] == ["a", "b", "c"]
    assert await pid_of(namer) != os.getpid()


async def test_the_same_process_serves_every_call(namer: ProcessNamer) -> None:
    first = await pid_of(namer)
    await namer.name_many(["x", "DECLINE"])
    assert await pid_of(namer) == first  # the JVM is started once, never per request


async def test_a_decline_passes_through(namer: ProcessNamer) -> None:
    [r] = await namer.name_many(["DECLINE"])
    assert (r.name, r.tier, r.reason) == (None, "abstain", "UNNAMEABLE")


async def test_the_soft_limit_given_to_the_engine_is_the_per_structure_limit(
    namer: ProcessNamer,
) -> None:
    [r] = await namer.name_many(["x"])
    assert r.name == "x@0.50"


# --- deadlines ---------------------------------------------------------------------------------


async def test_a_call_that_ignores_the_soft_limit_is_killed_at_the_hard_deadline(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    start = time.monotonic()
    [r] = await bounded(namer.name_many(["HANG"]))
    elapsed = time.monotonic() - start
    assert (r.name, r.tier, r.reason) == (None, None, TIMEOUT_REASON)
    assert 0.9 <= elapsed < 5  # soft limit 0.5 s + grace 0.5 s
    assert namer.hard_kills == 1
    assert await pid_of(namer) != before  # the next call is served by a fresh process


async def test_a_hang_in_the_middle_of_a_batch_costs_one_result_not_the_batch(
    namer: ProcessNamer,
) -> None:
    results = await bounded(namer.name_many(["a", "HANG", "c", "DECLINE"]))
    assert reasons(results) == [None, TIMEOUT_REASON, None, "UNNAMEABLE"]
    assert results[2].name is not None


async def test_the_process_is_replaced_in_the_background_after_a_kill(
    namer: ProcessNamer,
) -> None:
    await bounded(namer.name_many(["HANG"]))
    assert not namer.busy  # a replacement being started is not a call in flight
    await until(lambda: namer.pid is not None)  # without another call to ask for it
    assert (await pid_of(namer)) == namer.pid


async def test_when_the_batch_budget_is_spent_the_rest_is_declined_as_timeout() -> None:
    n = make(replace(FAST, batch_timeout_s=0.5, name_timeout_s=5.0))
    await n.start()
    try:
        results = await bounded(n.name_many(["SLOW1", "SLOW2", "SLOW3", "after"]))
        assert reasons(results[:2]) == [None, None]
        assert results[3].reason == TIMEOUT_REASON  # never sent: the budget was gone
        # the per-structure limit shrinks to what is left of the budget
        assert float(results[2].name.split("@")[1]) < 0.5  # type: ignore[union-attr]
    finally:
        await n.stop()


async def test_a_replacement_that_is_slow_to_start_is_waited_for_only_as_long_as_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = make(replace(FAST, batch_timeout_s=2.0))
    await n.start()
    try:
        monkeypatch.setenv("FAKE_ENGINE_START_DELAY", "4")  # the replacement will be slow
        started = time.monotonic()
        results = await bounded(n.name_many(["HANG", "b", "c"]), 15)
        assert time.monotonic() - started < 3.5  # not the 4 s the replacement needs
        assert reasons(results) == [TIMEOUT_REASON] * 3
        monkeypatch.delenv("FAKE_ENGINE_START_DELAY")
        # the slow replacement was not abandoned: it comes up, and the next call uses it
        await until(lambda: n.pid is not None, 15)
        [r] = await bounded(n.name_many(["after"]), 15)
        assert r.name == "after@0.50"
    finally:
        await n.stop()


# --- errors per structure ---------------------------------------------------------------------


async def test_an_error_inside_the_engine_costs_that_structure_only(namer: ProcessNamer) -> None:
    before = await pid_of(namer)
    results = await namer.name_many(["a", "BOOM", "BAD", "d"])
    assert reasons(results) == [None, "engine_error", "engine_error", None]
    assert [r.name for r in results[1:3]] == [None, None]
    assert results[3].name == "d@0.50"
    assert await pid_of(namer) == before  # and the process is the same


async def test_an_error_message_from_the_engine_never_reaches_the_parent(
    namer: ProcessNamer, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        await namer.name_many(["BOOM"])
    assert "engine bug" not in " ".join(str(r.__dict__) for r in caplog.records)


async def test_a_crashed_process_costs_that_structure_and_the_batch_carries_on(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    results = await bounded(namer.name_many(["a", "CRASH", "c"]))
    assert reasons(results) == [None, "engine_error", None]
    assert await pid_of(namer) != before


async def test_two_crashes_in_a_row_stop_the_batch_and_the_rest_is_unavailable(
    namer: ProcessNamer,
) -> None:
    results = await bounded(namer.name_many(["a", "CRASH", "CRASH", "d", "e"]))
    assert reasons(results) == [None, "engine_error", "engine_error"] + ["engine_unavailable"] * 2


async def test_a_replacement_that_fails_to_start_marks_the_rest_engine_unavailable(
    namer: ProcessNamer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")  # the next process will not start
    results = await bounded(namer.name_many(["a", "HANG", "c", "d"]))
    assert reasons(results) == [None, TIMEOUT_REASON, "engine_unavailable", "engine_unavailable"]
    assert results[0].name == "a@0.50"  # what was done before the trouble is kept


async def test_when_not_even_the_first_structure_can_be_tried_the_engine_is_unavailable(
    namer: ProcessNamer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")
    await bounded(namer.name_many(["HANG"]))  # killed; its replacement will fail
    await until(lambda: namer.pid is None and not namer.starting)
    with pytest.raises(EngineUnavailable, match="/nowhere/opsin.jar"):
        await bounded(namer.name_many(["a", "b"]))


async def test_a_failed_restart_is_reported_not_retried_in_a_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = make(replace(FAST, respawn_backoff_s=30.0))
    await n.start()
    try:
        monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")
        await n.name_many(["HANG"])
        await until(lambda: not n.starting)
        with pytest.raises(EngineUnavailable, match="/nowhere/opsin.jar"):
            await n.name_many(["a"])
        monkeypatch.delenv("FAKE_ENGINE_FAIL_START")
        with pytest.raises(EngineUnavailable, match="/nowhere/opsin.jar"):  # inside the backoff
            await n.name_many(["a"])
    finally:
        await n.stop()


async def test_a_process_that_asks_to_be_replaced_is_replaced_after_its_answer_is_delivered(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    [r] = await namer.name_many(["LEAK"])
    assert r.name == str(before)  # the answer came from the old process, and was delivered
    await until(lambda: namer.pid not in (None, before))


async def test_a_call_cancelled_mid_flight_cannot_leave_a_stale_reply_for_the_next_call(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    task = asyncio.create_task(namer.name_many(["SLOW"]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    [r] = await bounded(namer.name_many(["fresh"]))
    assert r.name == "fresh@0.50"  # not the cancelled call's answer
    # The process that was mid-call is gone: it still had an answer to send, and a pipe that is
    # shared with a call nobody awaits any more cannot be trusted.
    assert await pid_of(namer) != before


async def test_busy_is_true_only_while_a_call_is_in_flight(namer: ProcessNamer) -> None:
    assert not namer.busy
    task = asyncio.create_task(namer.name_many(["SLOW"]))
    await asyncio.sleep(0.05)
    assert namer.busy
    await task
    assert not namer.busy


async def test_calls_take_turns(namer: ProcessNamer) -> None:
    results = await asyncio.gather(*(namer.name_many([f"s{i}"]) for i in range(5)))
    assert [r[0].name.split("@")[0] for r in results if r[0].name] == [f"s{i}" for i in range(5)]


# --- start and stop --------------------------------------------------------------------------


async def test_start_fails_with_the_engines_own_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")
    n = make()
    with pytest.raises(RuntimeError, match="jar missing at /nowhere/opsin.jar"):
        await bounded(n.start())
    assert not n.busy


async def test_start_fails_when_the_process_never_says_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_HANG_START", "1")
    n = ProcessNamer(replace(FAST, startup_timeout_s=1.0), factory="fakes:make_engine")
    with pytest.raises(RuntimeError, match="not ready after 1 s"):
        await bounded(n.start())
    assert n.pid is None  # the stuck process was killed, not left behind


async def test_start_fails_when_the_factory_cannot_be_loaded() -> None:
    n = ProcessNamer(FAST, factory="fakes:no_such_factory")
    with pytest.raises(RuntimeError, match="no_such_factory"):
        await bounded(n.start())


async def test_start_fails_when_the_process_exits_without_a_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_EXIT_START", "1")
    n = make()
    with pytest.raises(RuntimeError, match=r"exited during startup \(exit code 3\)"):
        await bounded(n.start())


async def test_stop_ends_the_process_and_is_safe_to_repeat() -> None:
    n = make()
    await n.start()
    pid = await pid_of(n)
    await n.stop()
    await n.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_stop_during_a_background_replacement_leaves_no_process_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    n = make()
    await n.start()
    monkeypatch.setenv("FAKE_ENGINE_START_DELAY", "5")
    await n.name_many(["HANG"])  # killed; a slow replacement is now starting
    await until(lambda: n.starting)
    await bounded(n.stop())
    assert n.pid is None and not n.starting


async def test_orthonyms_per_molecule_info_logs_are_redacted_in_the_service_logs(
    capfd: pytest.CaptureFixture[str],
) -> None:
    n = make()  # spawned here, after capfd has taken over stderr, so the child inherits it
    await n.start()
    try:
        capfd.readouterr()
        await n.name_many(["LOG"])
        err = capfd.readouterr().err
    finally:
        await n.stop()
    assert "orthonym-warning-visible" not in err
    assert "secret-structure-name" not in err  # the name of a user's structure is not logged
    records = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    from_orthonym = [r for r in records if r["logger"].startswith("orthonym")]
    # the INFO line is not logged at all; the warning is, as logger, level and one fixed sentence
    assert [(r["level"], r["message"]) for r in from_orthonym] == [
        ("WARNING", "orthonym log message withheld")
    ]


# --- reaping, parent-death -----------------------------------------------------------------------


class _Process:
    def __init__(self, close_error: Exception | None = None) -> None:
        self.close_error = close_error
        self.killed = False
        self.joined: float | None = None
        self.pid = 1

    def kill(self) -> None:
        self.killed = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = timeout

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error


class _Conn:
    closed = False

    def close(self) -> None:
        self.closed = True


def test_a_process_that_cannot_be_closed_yet_does_not_break_the_reaper(
    caplog: pytest.LogCaptureFixture,
) -> None:
    process = _Process(ValueError("Cannot close a process while it is still running"))
    conn = _Conn()
    supervisor.reap(supervisor._Worker(process, conn, {}))  # type: ignore[arg-type]
    assert process.joined == 5 and conn.closed  # joined and its pipe closed all the same
    assert any("could not be closed" in r.getMessage() for r in caplog.records)


async def test_killing_a_process_does_not_block_the_event_loop() -> None:
    class SlowToJoin(_Process):
        def join(self, timeout: float | None = None) -> None:
            time.sleep(0.4)  # a process that takes its time to go
            super().join(timeout)

    namer = make()
    worker = supervisor._Worker(SlowToJoin(), _Conn(), {})  # type: ignore[arg-type]
    started = time.monotonic()
    namer._kill(worker)
    assert (
        time.monotonic() - started < 0.15
    )  # the SIGKILL is sent at once, the join is not waited for
    assert worker.process.killed  # type: ignore[attr-defined]
    await namer.stop()  # which does wait for the reaper
    assert worker.process.joined == 5 and worker.conn.closed  # type: ignore[attr-defined]


def test_parent_death_signal_failure_is_logged_not_ignored(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[tuple[int, int]] = []

    class Libc:
        def prctl(self, option: int, arg: int) -> int:
            calls.append((option, arg))
            return -1

    monkeypatch.setattr(supervisor.ctypes, "CDLL", lambda name: Libc())
    supervisor._die_with_parent()
    assert calls == [(1, int(supervisor.signal.SIGKILL))]  # PR_SET_PDEATHSIG, SIGKILL
    assert any("parent-death signal" in r.getMessage() for r in caplog.records)


PARENT_SCRIPT = """
import asyncio
from decimer_chem.settings import Settings
from decimer_chem.supervisor import ProcessNamer

async def main():
    namer = ProcessNamer(Settings(startup_timeout_s=30), factory="fakes:make_engine")
    await namer.start()
    print(namer.pid, flush=True)
    print(namer._scratch, flush=True)
    await namer.name_many(["HANG"])  # the test kills this process while the child is stuck

asyncio.run(main())
"""


def alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):  # the process may vanish while it is read
        return False


def test_alive_treats_a_process_that_vanishes_while_being_read_as_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def vanish(*args: Any, **kwargs: Any) -> None:
        raise ProcessLookupError  # what reading /proc/<pid>/stat raises when the process just left

    monkeypatch.setattr("builtins.open", vanish)
    assert alive(1) is False


def test_the_naming_process_dies_with_its_parent_even_when_stuck() -> None:
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    parent = subprocess.Popen(
        [sys.executable, "-c", PARENT_SCRIPT],
        env={**os.environ, "PYTHONPATH": tests_dir},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert parent.stdout is not None
        child = int(parent.stdout.readline())
        scratch = parent.stdout.readline().strip()
        assert alive(child)
        time.sleep(0.3)  # the child is now inside the HANG call
        parent.kill()  # SIGKILL: no shutdown code runs in the parent
        parent.wait(timeout=10)
        deadline = time.monotonic() + 5
        while alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not alive(child), "the naming process outlived its parent"
    finally:
        parent.kill()
        # A parent that is killed cannot clean up after itself: its scratch directory stays.
        shutil.rmtree(scratch, ignore_errors=True)


# --- the wire: JSON only, never pickle ---------------------------------------------------


class Tagged(str):
    derivation = object()


def test_both_directions_of_the_pipe_carry_json_not_pickle() -> None:
    parent, child = multiprocessing.Pipe()
    supervisor._send(
        child, ("result", 7, NameResult(Tagged("n"), None, None, "r").as_dict(), False)
    )
    assert json.loads(parent.recv_bytes()) == [
        "result",
        7,
        {"name": "n", "tier": None, "verified": None, "reason": "r"},
        False,
    ]
    assert supervisor._exchange(parent, ("name", 8, "CCO", 1.5), 0.05) is None  # nothing came back
    assert json.loads(child.recv_bytes()) == ["name", 8, "CCO", 1.5]


def test_a_pickle_from_the_other_end_is_refused_and_never_loaded(tmp_path: Path) -> None:
    marker = tmp_path / "unpickled"

    class Hostile:
        def __reduce__(self) -> tuple[Any, ...]:
            return (os.mkdir, (str(marker),))  # what loading it would do

    parent, child = multiprocessing.Pipe()
    child.send(Hostile())  # a pickle, which is what Connection.send writes
    with pytest.raises(supervisor.EngineFailure):
        supervisor._exchange(parent, None, 2)
    assert not marker.exists()


async def test_a_str_subclass_from_the_engine_arrives_as_plain_str_and_its_module_stays_out(
    namer: ProcessNamer,
) -> None:
    [r] = await namer.name_many(["SUBCLASS"])
    assert (r.name, r.tier, r.verified) == ("n", "pin_verified", "opsin")
    assert type(r.name) is str and type(r.tier) is str and type(r.verified) is str
    assert "child_only_types" not in sys.modules  # the web process never imported the class


async def test_a_field_that_is_not_a_string_is_refused_as_an_engine_error(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    [r] = await namer.name_many(["BADFIELD"])
    assert (r.name, r.reason) == (None, "engine_error")
    await until(lambda: namer.pid not in (None, before))  # and the process is not trusted again


@pytest.mark.parametrize(
    "payload",
    [
        {"name": 5, "tier": None, "verified": None, "reason": None},
        {"name": "x", "tier": ["a"], "verified": None, "reason": None},
        {"name": "x", "tier": None, "verified": None},  # a key missing
        {"name": "x", "tier": None, "verified": None, "reason": None, "extra": 1},
        ["not", "a", "dict"],
        None,
    ],
)
def test_the_parent_accepts_only_exactly_four_string_or_null_fields(payload: Any) -> None:
    assert supervisor.parse_result(payload) is None


def test_the_parent_accepts_a_plain_result() -> None:
    payload = {"name": "x", "tier": "pin_verified", "verified": "opsin", "reason": None}
    assert supervisor.parse_result(payload) == NameResult("x", "pin_verified", "opsin", None)


# --- the working directory, and stopping -------------------------------------------------


async def test_the_naming_process_runs_in_a_scratch_directory_that_stop_removes() -> None:
    n = make()
    await n.start()
    [r] = await n.name_many(["CWD"])
    scratch = Path(str(r.name))
    assert scratch != Path.cwd() and scratch.name.startswith("decimer-chem-")
    assert scratch.is_dir()  # an `hs_err_pid*.log` from the JVM would land here, not in the app dir
    await n.stop()
    assert not scratch.exists()


async def test_the_scratch_directory_survives_a_replacement_of_the_process(
    namer: ProcessNamer,
) -> None:
    [first] = await namer.name_many(["CWD"])
    await namer.name_many(["HANG"])  # killed and replaced
    await until(lambda: namer.pid is not None)
    [second] = await namer.name_many(["CWD"])
    assert first.name == second.name


def test_relative_path_settings_become_absolute_before_the_process_changes_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    env = {"ORTHONYM_JAR_DIR": "jars", "JAVA_HOME": "jre", "XDG_CACHE_HOME": "/abs", "OTHER": "x"}
    supervisor.absolutize_path_env(env)
    assert env == {
        "ORTHONYM_JAR_DIR": str(tmp_path / "jars"),
        "JAVA_HOME": str(tmp_path / "jre"),
        "XDG_CACHE_HOME": "/abs",
        "OTHER": "x",
    }


async def test_no_replacement_starts_once_the_namer_is_stopped() -> None:
    n = make()
    await n.start()
    spawned: list[int] = []
    real_spawn = n._spawn

    async def spy() -> Any:
        spawned.append(1)
        return await real_spawn()

    n._spawn = spy  # type: ignore[method-assign]
    task = asyncio.create_task(n.name_many(["HANG"]))
    await asyncio.sleep(0.3)  # the child is stuck inside the call
    await n.stop()  # the call then ends, and would normally start a replacement
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(1.0)
    assert spawned == []  # not even an attempt: a process started now would outlive the service
    assert n.pid is None and not n.starting
    assert not [p for p in multiprocessing.active_children() if p.name == "decimer-chem-engine"]


async def test_a_namer_can_be_started_again_after_it_was_stopped() -> None:
    n = make()
    await n.start()
    await n.stop()
    await n.start()
    try:
        assert await pid_of(n) > 0
    finally:
        await n.stop()


# --- a reply the parent cannot make sense of: that structure's engine_error -----------------


class _AliveProcess:
    """Stands in for the child process of a scripted worker."""

    pid = 999_999
    exitcode = None

    def is_alive(self) -> bool:
        return True

    def kill(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        pass

    def close(self) -> None:
        pass


Reply = Callable[[list[Any]], bytes]


def good(request: list[Any]) -> bytes:
    named = {
        "name": f"{request[2]}-ok",
        "tier": "pin_verified",
        "verified": "opsin",
        "reason": None,
    }
    return json.dumps(["result", request[1], named, False]).encode()


def valid_but(**fields: Any) -> Reply:
    """A reply that is well formed except for what `fields` overrides."""

    def reply(request: list[Any]) -> bytes:
        payload = {"name": "n", "tier": "pin_verified", "verified": "opsin", "reason": None}
        return json.dumps(["result", request[1], {**payload, **fields}, False]).encode()

    return reply


def raw(data: bytes) -> Reply:
    return lambda request: data


def scripted_namer(*replies: Reply) -> ProcessNamer:
    """A namer whose first worker is a script: it answers each request with the next reply.

    Whatever follows the script is served by a real child process running the fake engine."""
    namer = make()
    parent, child = multiprocessing.Pipe()
    namer._worker = supervisor._Worker(_AliveProcess(), parent, {})  # type: ignore[arg-type]

    def play() -> None:
        try:
            for reply in replies:
                child.send_bytes(reply(json.loads(child.recv_bytes())))
        except OSError:  # the parent closed its end: it has stopped trusting this "process"
            pass

    threading.Thread(target=play, daemon=True).start()
    return namer


BROKEN_REPLIES = {
    "a number": raw(b"5"),
    "an object": raw(b'{"result": 1}'),
    "a string": raw(b'"result"'),
    "null": raw(b"null"),
    "an empty array": raw(b"[]"),
    "an array that does not start with a word": raw(b"[1, 2, 3, 4]"),
    "an array of the wrong length": raw(b'["result", 1]'),
    "nesting deep enough to overflow the parser": raw(b"[" * 100_000 + b"]" * 100_000),
    "bytes that are not JSON": raw(b"\xff\xfe\x00"),
    "a result larger than anything the engine says": valid_but(name="x" * (1 << 20)),
    "a lone surrogate (not UTF-8)": valid_but(name="\ud800"),
    "a lone surrogate in the tier": valid_but(tier="\udfff"),
    "a number as the name": valid_but(name=5),
}


@pytest.mark.parametrize("reply", BROKEN_REPLIES.values(), ids=BROKEN_REPLIES.keys())
async def test_a_reply_that_makes_no_sense_costs_that_structure_only_and_the_process_is_replaced(
    reply: Reply,
) -> None:
    n = scripted_namer(good, reply)
    try:
        results = await bounded(n.name_many(["a", "b", "c"]))
        # the name already made is kept, the broken answer is that structure's engine_error, and
        # the next structure is served by the replacement: never a failed request
        assert [r.name for r in results] == ["a-ok", None, "c@0.50"]
        assert results[1].reason == "engine_error"
        assert n.pid != _AliveProcess.pid  # the process that said it was dropped
    finally:
        await n.stop()


@pytest.mark.parametrize(
    "data",
    [
        b"5",
        b'{"a": 1}',
        b"[]",
        b"[1]",
        b"[" * 100_000 + b"]" * 100_000,
        b"\xff\xfe",
        b'["result"' + b" " * (1 << 20) + b"]",
    ],
    ids=[
        "number",
        "object",
        "empty array",
        "no leading word",
        "deep nesting",
        "bytes",
        "oversized",
    ],
)
def test_exchange_raises_engine_failure_for_anything_but_a_modest_json_array(data: bytes) -> None:
    parent, child = multiprocessing.Pipe()
    sender = threading.Thread(target=lambda: _quiet_send(child, data), daemon=True)
    sender.start()
    with pytest.raises(supervisor.EngineFailure):
        supervisor._exchange(parent, None, 5)


def _quiet_send(conn: Any, data: bytes) -> None:
    try:
        conn.send_bytes(data)
    except OSError:
        pass


def test_exchange_still_returns_an_ordinary_reply() -> None:
    parent, child = multiprocessing.Pipe()
    child.send_bytes(b'["result", 3, {"name": null}, false]')
    assert supervisor._exchange(parent, None, 5) == ["result", 3, {"name": None}, False]


async def test_a_reply_too_large_to_read_from_a_real_process_is_an_engine_error(
    namer: ProcessNamer,
) -> None:
    before = await pid_of(namer)
    [r] = await bounded(namer.name_many(["HUGE"]))  # the child is stuck sending 2 MiB
    assert (r.name, r.reason) == (None, "engine_error")
    await until(lambda: namer.pid not in (None, before))


async def test_a_lone_surrogate_from_a_real_process_is_an_engine_error(
    namer: ProcessNamer,
) -> None:
    [r] = await bounded(namer.name_many(["SURROGATE"]))
    assert (r.name, r.reason) == (None, "engine_error")


@pytest.mark.parametrize("text", ["\ud800", "ok\udfff", "\udc00x"])
@pytest.mark.parametrize("field", ["name", "tier", "verified", "reason"])
def test_parse_result_refuses_text_that_is_not_utf8(field: str, text: str) -> None:
    payload = {"name": "n", "tier": "t", "verified": "v", "reason": "r", field: text}
    assert supervisor.parse_result(payload) is None


def test_parse_result_accepts_any_text_that_encodes() -> None:
    payload = {
        "name": "2-(acetyloxy)benzoic acid",
        "tier": "ünï ⁶",
        "verified": None,
        "reason": "",
    }
    assert supervisor.parse_result(payload) == NameResult(
        "2-(acetyloxy)benzoic acid", "ünï ⁶", None, ""
    )


# --- no scratch directory is left behind ---------------------------------------------------


def scratch_dirs() -> set[str]:
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "decimer-chem-*")))


@pytest.mark.parametrize("how", ["engine fails", "never ready", "exits", "no factory"])
async def test_a_failed_start_removes_the_scratch_directory(
    how: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = FAST
    factory = "fakes:make_engine"
    if how == "engine fails":
        monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")
    elif how == "never ready":
        monkeypatch.setenv("FAKE_ENGINE_HANG_START", "1")
        settings = replace(FAST, startup_timeout_s=1.0)
    elif how == "exits":
        monkeypatch.setenv("FAKE_ENGINE_EXIT_START", "1")
    else:
        factory = "fakes:no_such_factory"
    before = scratch_dirs()
    n = ProcessNamer(settings, factory=factory)
    with pytest.raises(RuntimeError):
        await bounded(n.start())
    assert scratch_dirs() == before  # the failed start did not leave its directory


async def test_a_namer_whose_start_failed_can_be_started_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_ENGINE_FAIL_START", "1")
    n = make()
    with pytest.raises(RuntimeError):
        await bounded(n.start())
    monkeypatch.delenv("FAKE_ENGINE_FAIL_START")
    await bounded(n.start())
    try:
        assert await pid_of(n) > 0
    finally:
        await n.stop()
