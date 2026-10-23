"""Peak memory of the containers of one Compose project while a run is in progress.

`docker stats --no-stream` is sampled every `interval` seconds in a background thread, and each
container's highest reading is kept. Docker's own commands are run as argument lists, never
through a shell. A sampling problem (no Docker, no containers) is recorded in the report and
never stops the run it is measuring.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any

MIB = 1024 * 1024
COMMAND_TIMEOUT = 30.0
MAX_ERRORS = 5  # distinct sampling errors kept in the report

# A Compose project name, and a container name as `docker ps` prints it.
PROJECT_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")
CONTAINER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
AMOUNT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)")

Docker = Callable[[list[str]], str]


def run_docker(args: list[str]) -> str:
    """`docker <args>` and its standard output. Raises OSError, TimeoutExpired or
    CalledProcessError."""
    docker = shutil.which("docker")
    if docker is None:
        raise FileNotFoundError("docker is not installed or not on PATH")
    done = subprocess.run(  # noqa: S603  # a fixed program with arguments the caller validated
        [docker, *args], capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=True
    )
    return done.stdout


def to_mib(amount: str) -> float:
    """'1.5GiB' or '512 MiB' or '12.3kB' as MiB."""
    match = AMOUNT.fullmatch(amount.strip())
    if match is None or match.group(2).lower() not in UNITS:
        raise ValueError(f"cannot read a memory amount from {amount!r}")
    return float(match.group(1)) * UNITS[match.group(2).lower()] / MIB


def parse_stats(output: str) -> dict[str, float]:
    """`{{.Name}} {{.MemUsage}}` lines ('name 1.2GiB / 15.5GiB') as {name: MiB in use}."""
    usage: dict[str, float] = {}
    for line in map(str.strip, output.splitlines()):
        if line:
            name, _, memory = line.partition(" ")
            usage[name] = to_mib(memory.split("/")[0])
    return usage


class MemorySampler:
    """Sample on start, every `interval` seconds meanwhile, and once more on stop."""

    def __init__(
        self,
        project: str,
        *,
        interval: float = 2.0,
        docker: Docker | None = None,
    ) -> None:
        if PROJECT_PATTERN.fullmatch(project) is None:
            raise ValueError(f"{project!r} is not a Compose project name")
        self.project = project
        self.interval = interval
        self._docker = docker or run_docker
        self._baseline: dict[str, float] = {}
        self._peak: dict[str, float] = {}
        self._peak_total = 0.0
        self._samples = 0
        self._errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Take the baseline reading (before the load), then sample in the background."""
        self.sample()
        self._baseline = dict(self._peak)
        self._thread = threading.Thread(target=self._loop, name="docker-memory", daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2 * COMMAND_TIMEOUT)
        self.sample()
        return self.report()

    def _loop(self) -> None:
        due = time.monotonic() + self.interval
        while not self._stop.wait(max(0.0, due - time.monotonic())):
            self.sample()
            due = max(due + self.interval, time.monotonic())  # a slow sample does not pile up

    def sample(self) -> None:
        try:
            names = self._docker(
                [
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={self.project}",
                    "--format",
                    "{{.Names}}",
                ]
            ).splitlines()
            names = [name for name in map(str.strip, names) if CONTAINER_PATTERN.fullmatch(name)]
            if not names:
                raise RuntimeError(f"no running containers for project {self.project!r}")
            usage = parse_stats(
                self._docker(
                    ["stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}", *names]
                )
            )
        except Exception as error:  # whatever goes wrong here must not stop the run being measured
            self._note(f"{type(error).__name__}: {error}")
            return
        self._samples += 1
        for name, mib in usage.items():
            self._peak[name] = max(self._peak.get(name, 0.0), mib)
        self._peak_total = max(self._peak_total, sum(usage.values()))

    def _note(self, message: str) -> None:
        if message not in self._errors and len(self._errors) < MAX_ERRORS:
            self._errors.append(message)

    def report(self) -> dict[str, Any]:
        baseline_total = sum(self._baseline.values())
        return {
            "project": self.project,
            "interval_seconds": self.interval,
            "samples": self._samples,
            "baseline_mib": _rounded(self._baseline),
            "peak_mib": _rounded(self._peak),
            "baseline_total_mib": round(baseline_total, 1),
            "peak_total_mib": round(self._peak_total, 1),
            "peak_delta_total_mib": round(max(0.0, self._peak_total - baseline_total), 1),
            "errors": self._errors,
        }


def _rounded(values: dict[str, float]) -> dict[str, float]:
    return {name: round(mib, 1) for name, mib in sorted(values.items())}
