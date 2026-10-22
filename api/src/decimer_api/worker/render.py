"""Render one PDF page to a PNG at exactly 300 DPI (D10: pypdfium2).

Two ways to run `render_page`, both behind the `Renderer` protocol:

* `SubprocessRenderer` (production): `python -I -m decimer_api.worker.render`, one short-lived
  child per page, with a deadline and hard limits on memory, CPU time and file size. A PDF that
  defeats pdfium kills the child, never the worker, and the page's memory (up to 0.5 GB at the
  50 MP cap) goes back to the system. The worker process itself never imports pdfium.
* `ThreadRenderer` (tests, development): the same function in a thread, one page at a time,
  because pdfium is not thread-safe.

This module imports neither pypdfium2 nor Pillow at the top: the worker imports it, the child
imports them (before it applies its limits, so they do not eat into the memory budget).

Exact 300 DPI: a page of 792 pt is 11 in, so 3300 px. pypdfium2 sizes the bitmap as
`ceil(points * scale)`, and `792 * (300 / 72)` is `3300.0000000000005`, which would give 3301
rows. The scale is lowered by one part in a billion (a change of at most 0.00003 px on the
largest page), which only matters where the exact value is within rounding noise of a whole
number of pixels.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Protocol

from .errors import RenderError

RENDER_DPI = 300  # the same as ingest.RENDER_DPI, which sizes the pixel limits (a test checks)
POINTS_PER_INCH = 72
SCALE_NOISE = 1e-9
PNG_COMPRESS_LEVEL = 4
CPU_HEADROOM_SECONDS = 5
OPEN_FILES = 64
STDERR_LOG_BYTES = 2000

_PDFIUM_LOCK = threading.Lock()  # pdfium may be used by one thread at a time


class Renderer(Protocol):
    """Render page `index` (0-based) of `pdf` into the PNG `out`; returns (width, height)."""

    async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]: ...


def render_page(pdf: Path, index: int, out: Path, dpi: int = RENDER_DPI) -> tuple[int, int]:
    """Synchronous. Writes `out` through a temporary name, so it is whole or absent."""
    import pypdfium2 as pdfium

    try:
        document = pdfium.PdfDocument(pdf)
    except MemoryError:
        raise
    except Exception as error:
        raise RenderError(f"cannot open the PDF: {error}") from error
    try:
        page = document[index]
        try:
            bitmap = page.render(scale=dpi / POINTS_PER_INCH * (1 - SCALE_NOISE))
            image = bitmap.to_pil()
        finally:
            page.close()
        temporary = out.with_name(out.name + ".tmp")
        try:
            image.save(temporary, format="PNG", dpi=(dpi, dpi), compress_level=PNG_COMPRESS_LEVEL)
            os.replace(temporary, out)
        finally:
            temporary.unlink(missing_ok=True)
        width, height = image.size
        return int(width), int(height)
    except MemoryError:
        raise
    except RenderError:
        raise
    except Exception as error:
        raise RenderError(f"cannot render page {index}: {error}") from error
    finally:
        document.close()


class ThreadRenderer:
    """`render_page` in a worker thread: the event loop keeps running, but there is no isolation."""

    async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]:
        return await asyncio.to_thread(self._locked, pdf, index, out)

    @staticmethod
    def _locked(pdf: Path, index: int, out: Path) -> tuple[int, int]:
        with _PDFIUM_LOCK:
            return render_page(pdf, index, out)  # looked up at call time, so tests can patch it


class SubprocessRenderer:
    """One child process per page, with a deadline and resource limits."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        memory_mb: int,
        max_file_bytes: int,
        argv: list[str] | None = None,
    ) -> None:
        self._timeout = timeout_seconds
        self._memory_mb = memory_mb
        self._max_file_bytes = max_file_bytes
        self._argv = argv or [sys.executable, "-I", "-m", "decimer_api.worker.render"]
        self.last_pid: int | None = None

    async def __call__(self, pdf: Path, index: int, out: Path) -> tuple[int, int]:
        request = json.dumps(
            {
                "pdf": str(pdf),
                "index": index,
                "out": str(out),
                "dpi": RENDER_DPI,
                "memory_mb": self._memory_mb,
                "cpu_seconds": math.ceil(self._timeout) + CPU_HEADROOM_SECONDS,
                "max_file_bytes": self._max_file_bytes,
            }
        ).encode()
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # captured; pdfium may be chatty
            cwd=out.parent,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},  # none of the worker's secrets
            start_new_session=True,
        )
        self.last_pid = process.pid
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request), timeout=self._timeout
            )
        except TimeoutError as error:
            raise RenderError(f"took too long ({self._timeout:g} s limit)") from error
        finally:
            await _kill(process)
        return _interpret(process.returncode, stdout, stderr)


async def _kill(process: asyncio.subprocess.Process) -> None:
    """Make sure the child, and anything it started, is gone and reaped."""
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def _interpret(returncode: int | None, stdout: bytes, stderr: bytes) -> tuple[int, int]:
    try:
        answer = json.loads(stdout)
        if "ok" in answer:
            return int(answer["ok"]["width"]), int(answer["ok"]["height"])
        raise RenderError(str(answer["error"]["message"])[:300])
    except (ValueError, KeyError, TypeError):
        pass
    tail = stderr[-STDERR_LOG_BYTES:].decode(errors="replace").strip()
    if returncode is not None and returncode < 0:
        raise RenderError(f"the render process was killed by signal {-returncode}: {tail}")
    raise RenderError(f"the render process crashed (exit {returncode}) without an answer: {tail}")


# --- the child ---------------------------------------------------------------------------------


def main() -> int:
    """`python -I -m decimer_api.worker.render`: one JSON request on stdin, one JSON answer."""
    request = json.load(sys.stdin)
    # Imported before the limits apply: their address space is not part of the budget.
    import PIL.Image  # noqa: F401
    import pypdfium2  # noqa: F401

    from ..ingest_worker import apply_limits

    out_of_memory = json.dumps({"error": {"message": "the page needs too much memory"}})
    apply_limits(
        memory_mb=request["memory_mb"],
        cpu_seconds=request["cpu_seconds"],
        max_file_bytes=request["max_file_bytes"],
        open_files=OPEN_FILES,
    )
    try:
        width, height = render_page(
            Path(request["pdf"]), request["index"], Path(request["out"]), request["dpi"]
        )
        answer = json.dumps({"ok": {"width": width, "height": height}})
    except RenderError as error:
        answer = json.dumps({"error": {"message": str(error)[:300]}})
    except MemoryError:
        answer = out_of_memory
    sys.stdout.write(answer)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
