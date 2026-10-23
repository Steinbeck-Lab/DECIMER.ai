"""deploy/nginx/edge-logs.sh: the edge's error log is deleted after seven days.

nginx's error log is the one place the edge writes a client's IP address; the privacy page says it
is kept seven days at most. The script that makes that true is a shell script (it runs in the nginx
image, which has BusyBox and no Python), so these tests run it, with the time set by EDGE_LOG_NOW,
under every shell found here: `sh` (dash on Debian) and BusyBox's ash, the one the image has.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "nginx" / "edge-logs.sh"
DAY = 86400

# Inside the monorepo a missing script FAILS (see test_engines.py): a skip would hide a moved file.
IN_MONOREPO = (REPO / "api" / "pyproject.toml").is_file() and (REPO / "deploy").is_dir()
pytestmark = pytest.mark.skipif(
    not IN_MONOREPO, reason="the api is not run from inside the monorepo"
)

SHELLS = [["sh"]] + ([[busybox, "sh"]] if (busybox := shutil.which("busybox")) else [])


@pytest.fixture(
    params=SHELLS,
    ids=lambda shell: (
        " ".join(Path(shell[0]).name for _ in shell[:1]) + (" sh" if len(shell) > 1 else "")
    ),
)
def shell(request: pytest.FixtureRequest) -> list[str]:
    assert isinstance(request.param, list)
    return request.param


def epoch(text: str) -> int:
    """A UTC time such as '2026-10-23 12:00' as seconds since the epoch."""
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def date_of(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%d")


class Logs:
    """A log directory and a way to run the script on it at a chosen time."""

    def __init__(self, shell: list[str], directory: Path) -> None:
        self.shell = shell
        self.dir = directory
        self.dir.mkdir(exist_ok=True)
        self.pid_file = directory.parent / "nginx.pid"

    def run(
        self, command: str, at: str | int | None = None, **settings: str
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "PATH": os.environ["PATH"],
            "EDGE_LOG_DIR": str(self.dir),
            "EDGE_LOG_PID_FILE": str(self.pid_file),
            **{key: value for key, value in settings.items()},
        }
        if at is not None:
            environment["EDGE_LOG_NOW"] = str(epoch(at) if isinstance(at, str) else at)
        return subprocess.run(  # noqa: S603
            [*self.shell, str(SCRIPT), command],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def write(self, name: str, text: str = "a line\n", modified: str | None = None) -> Path:
        path = self.dir / name
        path.write_text(text)
        if modified is not None:
            stamp = epoch(modified)
            os.utime(path, (stamp, stamp))
        return path

    def names(self) -> list[str]:
        return sorted(path.name for path in self.dir.iterdir())

    @property
    def began(self) -> str:
        return (self.dir / ".error.log.began").read_text().strip()


@pytest.fixture
def logs(shell: list[str], tmp_path: Path) -> Logs:
    return Logs(shell, tmp_path / "log")


# --- rotating -------------------------------------------------------------------------------------


def test_the_file_of_an_earlier_day_is_renamed_to_that_day_and_a_new_day_begins(logs: Logs) -> None:
    logs.write("error.log", "yesterday's lines\n")
    logs.write(".error.log.began", "2026-10-22\n")
    assert logs.run("rotate", "2026-10-23 00:05").returncode == 0
    assert logs.names() == [".error.log.began", "error.log.2026-10-22"]
    assert (logs.dir / "error.log.2026-10-22").read_text() == "yesterday's lines\n"
    assert logs.began == "2026-10-23"


def test_a_file_that_is_still_in_its_day_is_left_alone(logs: Logs) -> None:
    logs.write("error.log", "today\n")
    logs.write(".error.log.began", "2026-10-23\n")
    logs.run("rotate", "2026-10-23 23:59")
    assert logs.names() == [".error.log.began", "error.log"]
    assert (logs.dir / "error.log").read_text() == "today\n"


def test_a_file_without_a_day_marker_is_named_for_the_day_of_its_last_line(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-20 18:30")
    logs.run("rotate", "2026-10-23 12:00")
    assert "error.log.2026-10-20" in logs.names()
    assert not (logs.dir / "error.log").exists()
    assert logs.began == "2026-10-23"


def test_an_empty_or_missing_file_only_starts_the_day(logs: Logs) -> None:
    logs.run("rotate", "2026-10-23 12:00")
    assert logs.names() == [".error.log.began"]
    assert logs.began == "2026-10-23"
    logs.write("error.log", "")
    logs.write(".error.log.began", "2026-10-01\n")
    logs.run("rotate", "2026-10-23 12:00")
    assert logs.names() == [".error.log.began", "error.log"]  # nothing to keep: not renamed
    assert logs.began == "2026-10-23"


def test_a_day_that_has_a_file_already_gets_the_next_number_and_nothing_is_overwritten(
    logs: Logs,
) -> None:
    logs.write("error.log.2026-10-22", "first\n")
    logs.write("error.log", "second\n")
    logs.write(".error.log.began", "2026-10-22\n")
    logs.run("rotate", "2026-10-23 00:05")
    assert (logs.dir / "error.log.2026-10-22").read_text() == "first\n"
    assert (logs.dir / "error.log.2026-10-22.1").read_text() == "second\n"


def test_rotating_twice_in_a_row_changes_nothing_the_second_time(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    logs.run("rotate", "2026-10-23 00:01")
    before = logs.names()
    logs.run("rotate", "2026-10-23 00:11")
    assert logs.names() == before


def test_nginx_is_told_to_open_a_new_file_when_the_old_one_is_renamed(
    logs: Logs, tmp_path: Path
) -> None:
    reopened = tmp_path / "reopened"
    # A stand-in for nginx: reacts to USR1 as nginx does, by "reopening".
    nginx = subprocess.Popen(  # noqa: S603
        ["/bin/sh", "-c", f"trap 'echo yes > {reopened}' USR1; while :; do sleep 0.05; done"]
    )
    try:
        logs.pid_file.write_text(f"{nginx.pid}\n")
        time.sleep(0.3)  # let the shell install its trap
        logs.write("error.log", modified="2026-10-22 10:00")
        logs.write(".error.log.began", "2026-10-22\n")
        logs.run("rotate", "2026-10-22 12:00")  # the same day: no signal
        time.sleep(0.3)
        assert not reopened.exists()
        logs.run("rotate", "2026-10-23 00:05")
        deadline = time.monotonic() + 10
        while not reopened.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reopened.exists()
    finally:
        nginx.send_signal(signal.SIGKILL)
        nginx.wait()


@pytest.mark.parametrize("pid", ["", "not a pid", "-1", "999999999"])
def test_a_missing_or_stale_pid_does_not_stop_the_rotation(logs: Logs, pid: str) -> None:
    logs.pid_file.write_text(pid)
    logs.write("error.log", modified="2026-10-22 10:00")
    assert logs.run("rotate", "2026-10-23 00:05").returncode == 0
    assert "error.log.2026-10-22" in logs.names()


# --- deleting -------------------------------------------------------------------------------------


def test_a_file_goes_when_its_day_is_a_week_behind_and_not_before(logs: Logs) -> None:
    # The file of 2026-10-16 starts at 2026-10-16 00:00; a week later is 2026-10-23 00:00. A run is
    # every 10 minutes: the last one before that moment (23:50 on the 22nd) is the one that deletes.
    for day in range(14, 24):
        logs.write(f"error.log.2026-10-{day}", "x\n")
    logs.run("rotate", "2026-10-22 23:49:59")
    assert "error.log.2026-10-15" not in logs.names()  # a week behind since the 22nd, 00:00
    assert "error.log.2026-10-16" in logs.names()  # 10 minutes to go
    logs.run("rotate", "2026-10-22 23:50:00")
    assert "error.log.2026-10-16" not in logs.names()
    assert "error.log.2026-10-17" in logs.names()


def test_the_current_file_and_the_files_inside_the_week_are_never_deleted(logs: Logs) -> None:
    logs.write("error.log", "today\n", modified="2000-01-01 00:00")  # however old its time says
    logs.write(".error.log.began", "2026-10-23\n")
    for day in range(17, 23):
        logs.write(f"error.log.2026-10-{day}")
    logs.run("rotate", "2026-10-23 12:00")
    assert "error.log" in logs.names()
    assert [n for n in logs.names() if n.startswith("error.log.2026")] == [
        f"error.log.2026-10-{day}" for day in range(17, 23)
    ]


def test_a_numbered_file_of_an_old_day_goes_with_its_day(logs: Logs) -> None:
    logs.write("error.log.2026-10-10")
    logs.write("error.log.2026-10-10.1")
    logs.write("error.log.2026-10-10.2")
    logs.run("rotate", "2026-10-23 12:00")
    assert logs.names() == [".error.log.began"]


def test_only_files_named_for_a_day_are_ever_deleted(logs: Logs) -> None:
    other = [
        "access.log",
        "error.log.old",
        "error.log.2026-1-1",
        "error.log.",
        "notes.txt",
        "error.log.20261001",
        ".hidden",
    ]
    for name in other:
        logs.write(name, modified="2020-01-01 00:00")
    (logs.dir / "error.log.2026-10-01").mkdir()  # a directory named for a day is not a log file
    logs.run("rotate", "2026-10-23 12:00")
    assert sorted(other + ["error.log.2026-10-01", ".error.log.began"]) == logs.names()


def test_no_line_in_the_logs_is_older_than_seven_days_over_eleven_days_of_running(
    logs: Logs,
) -> None:
    """The promise itself, from outside: a fake nginx writes a line with its time every 97 minutes,
    the script runs every hour (set as its interval), and after every run no line is older than
    seven days, while every line younger than six days less that hour is still there."""
    interval = 3600
    start = epoch("2026-09-01 00:03")
    end = start + 11 * DAY
    written: list[int] = []
    next_line = start
    logs.run("rotate", start - 60)  # what the entrypoint does before it starts nginx
    for moment in range(start, end, interval):
        while next_line <= moment:  # nginx appends to error.log, creating it when it is not there
            with (logs.dir / "error.log").open("a") as out:
                out.write(f"{next_line}\n")
            written.append(next_line)
            next_line += 97 * 60
        result = logs.run("rotate", moment, EDGE_LOG_INTERVAL_SECONDS=str(interval))
        assert result.returncode == 0, result.stderr
        kept = {
            int(line)
            for path in logs.dir.iterdir()
            if path.name.startswith("error.log")
            for line in path.read_text().split()
        }
        assert all(moment - line <= 7 * DAY for line in kept), (date_of(moment), min(kept))
        young = {line for line in written if moment - line < 6 * DAY - interval}
        assert young <= kept, (date_of(moment), min(young - kept))
    assert len(written) > 150  # it did run for eleven days, and old lines did get deleted
    assert min(kept) > start + 3 * DAY


def test_a_restart_after_days_down_rotates_and_deletes_the_stale_file(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-01 10:00")  # the edge was down since the 1st
    logs.write(".error.log.began", "2026-10-01\n")
    logs.run("rotate", "2026-10-23 12:00")
    assert logs.names() == [".error.log.began"]  # rotated to the 1st, which is a fortnight behind


# --- checking -------------------------------------------------------------------------------------


def test_check_passes_after_a_rotation(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    logs.run("rotate", "2026-10-23 00:05")
    assert logs.run("check", "2026-10-23 00:10").returncode == 0
    assert logs.run("check", "2026-10-23 23:59").returncode == 0  # the day is not over


def test_check_allows_the_hour_after_midnight_for_the_rotation_to_run(logs: Logs) -> None:
    logs.write(".error.log.began", "2026-10-22\n")
    assert logs.run("check", "2026-10-23 00:30").returncode == 0
    late = logs.run("check", "2026-10-23 01:30")
    assert late.returncode == 1
    assert "has been open since 2026-10-22 and was not rotated" in late.stderr


def test_check_fails_when_the_rotation_has_never_run(logs: Logs) -> None:
    result = logs.run("check", "2026-10-23 12:00")
    assert result.returncode == 1
    assert "no record of when error.log was started" in result.stderr


def test_check_fails_for_a_file_past_its_week_but_not_in_the_hour_of_grace(logs: Logs) -> None:
    logs.write(".error.log.began", "2026-10-23\n")
    logs.write("error.log.2026-10-16")  # a week behind at 2026-10-23 00:00
    assert logs.run("check", "2026-10-23 00:30").returncode == 0  # the run is due, not late
    late = logs.run("check", "2026-10-23 01:30")
    assert late.returncode == 1
    assert "error.log.2026-10-16 is older than 7 days and was not deleted" in late.stderr
    logs.run("rotate", "2026-10-23 01:30")
    assert logs.run("check", "2026-10-23 01:31").returncode == 0


# --- enforcing: the health check does the work itself ------------------------------------------


def test_enforce_rotates_and_deletes_by_itself_so_a_dead_loop_changes_nothing(logs: Logs) -> None:
    """`check` alone only notices; the image's HEALTHCHECK runs `enforce`, which does the work
    first, every fifteen seconds, whether or not the loop beside nginx is alive."""
    logs.write("error.log", "yesterday\n", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    logs.write("error.log.2026-10-10")  # a fortnight old: the dead loop has not deleted it
    assert logs.run("check", "2026-10-23 02:00").returncode == 1
    result = logs.run("enforce", "2026-10-23 02:00")
    assert result.returncode == 0, result.stderr
    assert logs.names() == [".error.log.began", "error.log.2026-10-22"]
    assert logs.began == "2026-10-23"
    assert logs.run("check", "2026-10-23 02:00").returncode == 0


def test_enforce_still_fails_for_what_rotating_cannot_mend(logs: Logs) -> None:
    (logs.dir / "error.log").symlink_to("/dev/stderr")  # a volume seeded by the stock nginx image
    result = logs.run("enforce", "2026-10-23 12:00")
    assert result.returncode == 1
    assert "error.log is not a regular file" in result.stderr


def test_a_log_directory_that_cannot_be_used_fails_the_rotation_and_the_enforcement(
    tmp_path: Path, shell: list[str]
) -> None:
    missing = Logs(shell, tmp_path / "log")
    missing.dir.rmdir()
    for command in ("rotate", "enforce"):
        result = missing.run(command, "2026-10-23 12:00")
        assert result.returncode == 1, command
        assert "cannot take the lock" in result.stderr


def test_a_run_that_finds_the_lock_taken_leaves_everything_to_the_one_that_holds_it(
    logs: Logs,
) -> None:
    logs.write("error.log", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    (logs.dir / ".error.log.lock").mkdir()  # the loop, or an admin's one-liner, is mid-rotation
    result = logs.run("rotate", "2026-10-23 00:05")
    assert result.returncode == 0, result.stderr
    assert logs.names() == [".error.log.began", ".error.log.lock", "error.log"]  # nothing done
    assert logs.began == "2026-10-22"


def test_a_lock_that_nobody_holds_any_more_is_broken(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    lock = logs.dir / ".error.log.lock"
    lock.mkdir()
    long_ago = time.time() - 600  # a run that was killed with the container, ten minutes ago
    os.utime(lock, (long_ago, long_ago))
    assert logs.run("rotate", "2026-10-23 00:05").returncode == 0
    assert logs.names() == [".error.log.began", "error.log.2026-10-22"]  # rotated, lock gone


def test_the_lock_is_given_back_after_every_run_that_took_it(logs: Logs) -> None:
    logs.write("error.log", modified="2026-10-22 10:00")
    logs.write(".error.log.began", "2026-10-22\n")
    logs.run("rotate", "2026-10-23 00:05")
    logs.run("enforce", "2026-10-23 00:06")
    assert ".error.log.lock" not in logs.names()
    (logs.dir / "error.log").mkdir()  # a run that fails half way gives it back too
    assert logs.run("enforce", "2026-10-23 00:07").returncode == 1
    assert ".error.log.lock" not in logs.names()


def test_runs_at_the_same_time_rotate_once_and_none_fails(logs: Logs) -> None:
    """Twelve health checks and loops at the same moment, many times over: one rotates, the rest
    see the lock and leave it to that one, and none reports a failure (the lock, and a marker
    written whole, are what this holds together)."""
    environment = {
        "PATH": os.environ["PATH"],
        "EDGE_LOG_DIR": str(logs.dir),
        "EDGE_LOG_PID_FILE": str(logs.pid_file),
        "EDGE_LOG_NOW": str(epoch("2026-10-23 03:00")),  # long after the hour of grace
    }
    for round_number in range(15):
        for leftover in logs.dir.iterdir():
            leftover.unlink()
        logs.write("error.log", "yesterday\n", modified="2026-10-22 10:00")
        logs.write(".error.log.began", "2026-10-22\n")
        processes = [
            subprocess.Popen(  # noqa: S603
                [*logs.shell, str(SCRIPT), "enforce"],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for _ in range(12)
        ]
        results = [
            (process.wait(timeout=30), process.stderr.read() if process.stderr else b"")
            for process in processes
        ]
        assert [code for code, _ in results] == [0] * 12, (round_number, results)
        assert logs.names() == [".error.log.began", "error.log.2026-10-22"], round_number
        assert (logs.dir / "error.log.2026-10-22").read_text() == "yesterday\n"


def test_enforce_leaves_the_check_to_the_next_time_when_another_run_holds_the_lock(
    logs: Logs,
) -> None:
    (logs.dir / ".error.log.lock").mkdir()  # a rotation is going on: its result is not in yet
    assert logs.run("check", "2026-10-23 12:00").returncode == 1  # no marker yet
    assert logs.run("enforce", "2026-10-23 12:00").returncode == 0


# --- a file that is not a plain file, and a clock that goes back ---------------------------------


@pytest.mark.parametrize("kind", ["symlink to stderr", "symlink to a file", "directory"])
def test_check_fails_when_error_log_is_not_a_regular_file(
    logs: Logs, kind: str, tmp_path: Path
) -> None:
    logs.write(".error.log.began", "2026-10-23\n")
    target = logs.dir / "error.log"
    if kind == "symlink to stderr":
        target.symlink_to("/dev/stderr")  # docker's log driver keeps stderr by size, not by age
    elif kind == "symlink to a file":
        (tmp_path / "elsewhere.log").write_text("x")
        target.symlink_to(tmp_path / "elsewhere.log")
    else:
        target.mkdir()
    result = logs.run("check", "2026-10-23 12:00")
    assert result.returncode == 1
    assert "error.log is not a regular file" in result.stderr


def test_check_accepts_a_regular_file_and_no_file_at_all(logs: Logs) -> None:
    logs.write(".error.log.began", "2026-10-23\n")
    assert logs.run("check", "2026-10-23 12:00").returncode == 0  # nginx has not made it yet
    logs.write("error.log")
    assert logs.run("check", "2026-10-23 12:00").returncode == 0


def test_a_clock_that_went_back_does_not_date_a_file_in_the_future(logs: Logs) -> None:
    logs.write("error.log", "lines\n")
    logs.write(".error.log.began", "2026-10-25\n")  # the clock was ahead; it is now the 24th
    logs.run("rotate", "2026-10-24 12:00")
    assert logs.names() == [".error.log.began", "error.log.2026-10-24"]
    assert logs.began == "2026-10-24"


def test_a_file_dated_in_the_future_is_dated_today_so_that_it_is_gone_a_week_from_today(
    logs: Logs,
) -> None:
    logs.write("error.log.2026-10-25", "written while the clock was ahead\n")
    logs.write(".error.log.began", "2026-10-24\n")
    logs.run("rotate", "2026-10-24 12:00")
    assert logs.names() == [".error.log.began", "error.log.2026-10-24"]
    # Once the clock has caught up it still counts from the 24th: gone by the 31st, not the 1st.
    logs.run("rotate", "2026-10-30 23:49")
    assert "error.log.2026-10-24" in logs.names()
    logs.run("rotate", "2026-10-30 23:50")
    assert "error.log.2026-10-24" not in logs.names()


def test_a_future_date_does_not_overwrite_a_file_of_today(logs: Logs) -> None:
    logs.write("error.log.2026-10-24", "today's\n")
    logs.write("error.log.2026-10-26", "ahead\n")
    logs.write(".error.log.began", "2026-10-24\n")
    logs.run("rotate", "2026-10-24 12:00")
    assert (logs.dir / "error.log.2026-10-24").read_text() == "today's\n"
    assert (logs.dir / "error.log.2026-10-24.1").read_text() == "ahead\n"


def test_check_fails_while_the_clock_is_behind_a_date_in_the_logs(logs: Logs) -> None:
    logs.write(".error.log.began", "2026-10-25\n")
    result = logs.run("check", "2026-10-24 12:00")
    assert result.returncode == 1
    assert "clock" in result.stderr
    logs.write(".error.log.began", "2026-10-24\n")
    logs.write("error.log.2026-10-26")
    assert logs.run("check", "2026-10-24 12:00").returncode == 1
    logs.run("enforce", "2026-10-24 12:00")
    assert logs.run("check", "2026-10-24 12:00").returncode == 0


# --- the loop, and bad input ----------------------------------------------------------------------


def test_the_loop_rotates_at_once_and_then_again_and_again(logs: Logs) -> None:
    logs.write("error.log.2000-01-01")
    process = subprocess.Popen(  # noqa: S603
        [*logs.shell, str(SCRIPT), "loop"],
        env={
            "PATH": os.environ["PATH"],
            "EDGE_LOG_DIR": str(logs.dir),
            "EDGE_LOG_PID_FILE": str(logs.pid_file),
            "EDGE_LOG_INTERVAL_SECONDS": "1",
        },
    )

    def wait_until(condition: object, what: str) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if callable(condition) and condition():
                return
            time.sleep(0.05)
        raise AssertionError(what)

    try:
        wait_until(lambda: logs.names() == [".error.log.began"], "the first run did not delete")
        logs.write("error.log.2001-01-01")  # a file that got too old after the loop began
        wait_until(lambda: logs.names() == [".error.log.began"], "the loop did not run again")
        assert process.poll() is None
    finally:
        process.kill()
        process.wait()


@pytest.mark.parametrize(
    "settings",
    [
        {"EDGE_LOG_KEEP_DAYS": "0"},
        {"EDGE_LOG_KEEP_DAYS": "seven"},
        {"EDGE_LOG_INTERVAL_SECONDS": "-5"},
        {"EDGE_LOG_GRACE_SECONDS": "1.5"},
    ],
)
def test_a_setting_that_is_not_a_positive_whole_number_is_refused_and_nothing_is_deleted(
    logs: Logs, settings: dict[str, str]
) -> None:
    logs.write("error.log.2000-01-01")
    result = logs.run("rotate", "2026-10-23 12:00", **settings)
    assert result.returncode == 2
    assert "not a positive whole number" in result.stderr
    assert "error.log.2000-01-01" in logs.names()


@pytest.mark.parametrize("command", ["", "delete", "rotate now"])
def test_anything_but_rotate_loop_or_check_is_a_usage_error(logs: Logs, command: str) -> None:
    result = logs.run(command, "2026-10-23 12:00")
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_the_keep_days_setting_changes_the_week(logs: Logs) -> None:
    for day in (18, 19, 20, 21):
        logs.write(f"error.log.2026-10-{day}")
    logs.run("rotate", "2026-10-23 12:00", EDGE_LOG_KEEP_DAYS="3")
    assert [n for n in logs.names() if n.startswith("error.log.2026")] == ["error.log.2026-10-21"]


def test_the_script_is_executable_and_the_clock_is_the_systems_without_the_override(
    logs: Logs,
) -> None:
    assert os.access(SCRIPT, os.X_OK)
    today = datetime.now(UTC)
    logs.write("error.log", modified=(today - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
    logs.run("rotate")
    assert logs.began == date_of(today.timestamp())
