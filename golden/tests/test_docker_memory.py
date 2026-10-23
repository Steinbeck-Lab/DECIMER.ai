"""The memory sampler: what it asks Docker, how it reads the answer, what it keeps."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from typing import Any

import pytest

from decimer_golden import docker_memory
from decimer_golden.docker_memory import MemorySampler, parse_stats, to_mib


@pytest.mark.parametrize(
    ("amount", "mib"),
    [
        ("512MiB", 512.0),
        ("1.5GiB", 1536.0),
        ("1GiB", 1024.0),
        ("2048KiB", 2.0),
        ("1048576B", 1.0),
        ("1GB", 1e9 / 2**20),  # decimal units, as older Docker versions print them
        ("12.5MB", 12.5e6 / 2**20),
        (" 100 MiB ", 100.0),
    ],
)
def test_amounts_become_mib(amount: str, mib: float) -> None:
    assert to_mib(amount) == pytest.approx(mib)


@pytest.mark.parametrize("amount", ["", "lots", "5 parsecs", "-1MiB"])
def test_unreadable_amounts_are_refused(amount: str) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        to_mib(amount)


def test_stats_lines_are_read_by_container_name() -> None:
    out = "decimer-next-seg-1 1.5GiB / 15.5GiB\n\ndecimer-next-cls-2 300MiB / 15.5GiB\n"
    assert parse_stats(out) == {"decimer-next-seg-1": 1536.0, "decimer-next-cls-2": 300.0}


class Docker:
    """A fake `docker`: records its commands, answers `ps` with `names` and `stats` in turn."""

    def __init__(self, names: str, readings: list[str]) -> None:
        self.names = names
        self.readings = readings
        self.commands: list[list[str]] = []
        self.calls = 0

    def __call__(self, args: list[str]) -> str:
        self.commands.append(args)
        if args[0] == "ps":
            return self.names
        self.calls += 1
        return self.readings[min(self.calls, len(self.readings)) - 1]


def test_peak_is_the_highest_reading_per_container_and_total_is_of_one_moment() -> None:
    docker = Docker(
        "p-seg-1\np-ocsr-1\n",
        [
            "p-seg-1 1GiB / 9GiB\np-ocsr-1 1GiB / 9GiB",  # baseline: 2 GiB
            "p-seg-1 4GiB / 9GiB\np-ocsr-1 1GiB / 9GiB",  # 5 GiB
            "p-seg-1 1GiB / 9GiB\np-ocsr-1 3GiB / 9GiB",  # 4 GiB
            "p-seg-1 2GiB / 9GiB\np-ocsr-1 1GiB / 9GiB",
        ],
    )
    sampler = MemorySampler("p", docker=docker)
    for _ in range(4):
        sampler.sample()
    sampler._baseline = {"p-seg-1": 1024.0, "p-ocsr-1": 1024.0}
    report = sampler.report()
    assert report["peak_mib"] == {"p-ocsr-1": 3072.0, "p-seg-1": 4096.0}
    # 7 GiB would be the sum of the two peaks; at no moment were both that high.
    assert report["peak_total_mib"] == 5120.0
    assert report["peak_delta_total_mib"] == 3072.0
    assert report["samples"] == 4


def test_docker_is_asked_for_this_projects_containers_only_with_no_shell() -> None:
    docker = Docker("p-seg-1\nweird name\n--all\np-ocsr-1\n", ["p-seg-1 1GiB / 9GiB"])
    MemorySampler("p", docker=docker).sample()
    ps, stats = docker.commands
    assert ps == [
        "ps",
        "--filter",
        "label=com.docker.compose.project=p",
        "--format",
        "{{.Names}}",
    ]
    # Names that are not plain container names are not passed on (they could be options).
    assert stats == [
        "stats",
        "--no-stream",
        "--format",
        "{{.Name}} {{.MemUsage}}",
        "p-seg-1",
        "p-ocsr-1",
    ]


@pytest.mark.parametrize("project", ["", "Up", "a b", "x;y", "-x", "a/b", "$(id)"])
def test_project_names_that_compose_would_not_accept_are_refused(project: str) -> None:
    with pytest.raises(ValueError, match="Compose project"):
        MemorySampler(project, docker=Docker("", []))


def test_problems_are_recorded_once_and_never_raised() -> None:
    def broken(args: list[str]) -> str:
        if args[0] == "ps":
            return ""
        raise AssertionError("not reached without containers")

    sampler = MemorySampler("p", docker=broken)
    sampler.sample()
    sampler.sample()
    report = sampler.report()
    assert report["samples"] == 0
    assert report["errors"] == ["RuntimeError: no running containers for project 'p'"]

    def garbled(args: list[str]) -> str:
        return "p-seg-1" if args[0] == "ps" else "p-seg-1 lots / more"

    sampler = MemorySampler("p", docker=garbled)
    sampler.sample()
    assert sampler.report()["errors"] == ["ValueError: cannot read a memory amount from 'lots '"]


def test_the_background_thread_samples_until_stopped() -> None:
    docker = Docker(
        "p-seg-1", ["p-seg-1 1GiB / 9GiB", "p-seg-1 2GiB / 9GiB", "p-seg-1 8GiB / 9GiB"]
    )
    sampler = MemorySampler("p", interval=0.01, docker=docker)
    sampler.start()
    deadline = time.monotonic() + 5
    while docker.calls < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    report = sampler.stop()
    assert report["baseline_mib"] == {"p-seg-1": 1024.0}
    assert report["peak_mib"] == {"p-seg-1": 8192.0}
    assert report["samples"] >= 3
    calls = docker.calls
    time.sleep(0.05)
    assert docker.calls == calls  # stopped means stopped
    assert [t for t in threading.enumerate() if t.name == "docker-memory"] == []


def test_the_real_command_is_an_argument_list_without_a_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0, stdout="out", stderr="")

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert docker_memory.run_docker(["stats", "--no-stream"]) == "out"
    assert seen["command"] == ["/usr/bin/docker", "stats", "--no-stream"]
    assert not seen["kwargs"].get("shell")
    assert seen["kwargs"]["check"] is True and seen["kwargs"]["timeout"] > 0


def test_without_docker_installed_the_error_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match="docker"):
        docker_memory.run_docker(["ps"])
