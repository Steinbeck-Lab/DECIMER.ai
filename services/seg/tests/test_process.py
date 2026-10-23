"""The real service in its own process, hit by concurrent requests. Slow: `pytest -m slow`.

decimer_segmentation is not thread-safe: two inferences in one process corrupt the heap and the
process aborts ("free(): invalid pointer", "corrupted double-linked list"). The service must never
run two at once, whoever asks. This is the test that sees it end to end, on the real model, over
real sockets, in a separate process, so a crash shows up as dead connections and an exit code and
cannot take pytest down with it.

Tip on a shared machine: `TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 nice pytest -m slow`.
"""

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pytest
from helpers import FIXTURES, assert_matches_fixture, load_fixture, render_page

from decimer_seg.model import weights_file

pytestmark = [pytest.mark.slow, pytest.mark.asyncio(loop_scope="module")]

STARTUP_SECONDS = 180


class Server(NamedTuple):
    base_url: str
    proc: subprocess.Popen[bytes]
    log: Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def log_tail(path: Path, lines: int = 25) -> str:
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Server]:
    """`python -m decimer_seg` as production starts it, with its stdout and stderr in a file."""
    if not weights_file().is_file() and os.environ.get("SEG_ALLOW_WEIGHT_DOWNLOAD") != "1":
        pytest.fail(f"segmentation weights are missing: expected {weights_file()}", pytrace=False)
    port = free_port()
    log_path = tmp_path_factory.mktemp("seg-process") / "server.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(  # noqa: S603  # our own interpreter and module
            [sys.executable, "-m", "decimer_seg"],
            env={**os.environ, "PORT": str(port), "PYTHONFAULTHANDLER": "1"},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + STARTUP_SECONDS
            while True:
                if proc.poll() is not None:
                    pytest.fail(f"seg exited at start ({proc.returncode}):\n{log_tail(log_path)}")
                try:
                    if httpx.get(f"{base_url}/healthz", timeout=2).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"seg not up after {STARTUP_SECONDS} s:\n{log_tail(log_path)}")
                time.sleep(0.5)
            yield Server(base_url, proc, log_path)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()


async def test_concurrent_requests_and_probes_neither_crash_nor_change_the_answers(
    server: Server,
) -> None:
    # Must be the first test of this module: the process has just started, so its readiness cache
    # is empty and the first /readyz runs a real selftest, as Docker's start-up probe does in
    # production. Eight pages (the most the production queue admits beside the running one) and
    # three probes arrive together.
    base_url, proc, log = server
    expected = [load_fixture(p) for p in FIXTURES]
    pages = [render_page(e["page_index"], e["dpi"]) for e in expected]
    order = [i % len(pages) for i in range(8)]

    async with httpx.AsyncClient(base_url=base_url, timeout=900) as client:

        async def infer(i: int) -> httpx.Response:
            return await client.post(
                "/infer",
                files={"image": ("page.png", pages[i], "image/png")},
                data={"page_index": str(expected[i]["page_index"])},
            )

        calls: list[Any] = [infer(i) for i in order] + [client.get("/readyz") for _ in range(3)]
        try:
            responses = await asyncio.gather(*calls)
        except httpx.TransportError as exc:  # a crashed process drops every open connection
            await asyncio.sleep(1)
            pytest.fail(
                f"{type(exc).__name__}: seg exit code {proc.poll()}\n{log_tail(log)}",
                pytrace=False,
            )
        assert proc.poll() is None, f"seg died (exit {proc.returncode})"

        answers, probes = responses[: len(order)], responses[len(order) :]
        assert [r.status_code for r in answers] == [200] * len(order), [r.text for r in answers]
        assert [r.status_code for r in probes] == [200] * 3
        for i, response in zip(order, answers, strict=True):
            assert_matches_fixture(response.json(), expected[i])

        # Still the same live process, and still answering.
        assert (await client.get("/healthz")).status_code == 200
    assert proc.poll() is None
