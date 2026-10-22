"""PDF pages to page-N.png at exactly 300 DPI, off the event loop, in a child process."""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from decimer_api import ingest
from decimer_api.worker import render
from decimer_api.worker.errors import RenderError
from decimer_api.worker.render import RENDER_DPI, SubprocessRenderer, ThreadRenderer, render_page
from tests.factories import make_corrupt_pdf, make_pdf

LETTER = (612.0, 792.0)  # 8.5 x 11 in
A5 = (419.53, 595.28)


def write_pdf(directory: Path, data: bytes) -> Path:
    path = directory / "document.pdf"
    path.write_bytes(data)
    return path


def test_the_dpi_is_the_one_the_ingest_checks_page_sizes_against() -> None:
    assert RENDER_DPI == ingest.RENDER_DPI == 300


def test_a_letter_page_is_exactly_300_dpi(tmp_path: Path) -> None:
    """11 in x 300 = 3300 px. 792 pt x (300 / 72) is 3300.0000000000005 in floating point, and a
    renderer that rounds up would produce 3301 rows."""
    pdf = write_pdf(tmp_path, make_pdf(1, page_size=LETTER))
    out = tmp_path / "page-0.png"
    assert render_page(pdf, 0, out) == (2550, 3300)
    with Image.open(out) as image:
        assert image.size == (2550, 3300)
        assert image.mode == "RGB"
        dpi = image.info["dpi"]
        assert (round(dpi[0]), round(dpi[1])) == (300, 300)


@pytest.mark.parametrize("size", [LETTER, A5, (595.276, 841.89), (200.0, 144.0), (1000.0, 36.0)])
def test_every_page_size_is_within_a_pixel_of_300_dpi(
    tmp_path: Path, size: tuple[float, float]
) -> None:
    pdf = write_pdf(tmp_path, make_pdf(1, page_size=size))
    width, height = render_page(pdf, 0, tmp_path / "page-0.png")
    assert abs(width - size[0] * 300 / 72) < 1
    assert abs(height - size[1] * 300 / 72) < 1


def test_the_page_is_drawn_on_white_and_has_content(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_pdf(1))
    out = tmp_path / "page-0.png"
    render_page(pdf, 0, out)
    with Image.open(out) as image:
        extrema = image.convert("L").getextrema()
        assert extrema[1] == 255  # white paper
        assert extrema[0] < 100  # the text "page 1"
        assert image.getpixel((0, 0)) == (255, 255, 255)


def test_the_index_selects_the_page(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_pdf(page_sizes=[LETTER, A5, (300.0, 300.0)]))
    sizes = [render_page(pdf, index, tmp_path / f"page-{index}.png") for index in range(3)]
    assert sizes[0] == (2550, 3300)
    assert sizes[2] == (1250, 1250)
    assert sizes[1][0] < sizes[0][0]


def test_no_temporary_file_is_left_and_the_target_is_written_whole(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_pdf(1))
    render_page(pdf, 0, tmp_path / "page-0.png")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["document.pdf", "page-0.png"]


def test_a_damaged_pdf_or_a_missing_page_is_a_render_error(tmp_path: Path) -> None:
    broken = write_pdf(tmp_path, make_corrupt_pdf())
    with pytest.raises(RenderError):
        render_page(broken, 0, tmp_path / "page-0.png")
    fine = write_pdf(tmp_path, make_pdf(2))
    with pytest.raises(RenderError):
        render_page(fine, 5, tmp_path / "page-5.png")
    assert not (tmp_path / "page-5.png").exists()


# --- the thread renderer ---------------------------------------------------------------------


async def test_the_thread_renderer_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def slow_render(pdf: Path, index: int, out: Path, dpi: int = RENDER_DPI) -> tuple[int, int]:
        time.sleep(0.3)  # a blocking call, as pdfium is
        return (10, 10)

    monkeypatch.setattr(render, "render_page", slow_render)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        assert await ThreadRenderer()(tmp_path / "x.pdf", 0, tmp_path / "x.png") == (10, 10)
    finally:
        task.cancel()
    assert ticks >= 15  # about 30 ticks fit in 0.3 s; a blocked loop would manage 0 or 1


async def test_the_thread_renderer_renders_one_page_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pdfium is not thread-safe: two threads must never be inside it together."""
    inside = 0
    peak = 0

    def counting_render(pdf: Path, index: int, out: Path, dpi: int = RENDER_DPI) -> tuple[int, int]:
        nonlocal inside, peak
        inside += 1
        peak = max(peak, inside)
        time.sleep(0.02)
        inside -= 1
        return (1, 1)

    monkeypatch.setattr(render, "render_page", counting_render)
    renderer = ThreadRenderer()
    await asyncio.gather(
        *(renderer(tmp_path / "x.pdf", i, tmp_path / f"{i}.png") for i in range(5))
    )
    assert peak == 1


# --- the subprocess renderer -------------------------------------------------------------------


def child(settings_timeout: float = 30.0, **kwargs: object) -> SubprocessRenderer:
    return SubprocessRenderer(
        timeout_seconds=settings_timeout,
        memory_mb=2048,
        max_file_bytes=300 * 1024 * 1024,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_the_subprocess_renderer_renders_in_another_process(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_pdf(2, page_size=LETTER))
    renderer = child()
    assert await renderer(pdf, 1, tmp_path / "page-1.png") == (2550, 3300)
    assert renderer.last_pid not in (None, __import__("os").getpid())
    with Image.open(tmp_path / "page-1.png") as image:
        assert image.size == (2550, 3300)


async def test_the_subprocess_renderer_reports_a_damaged_pdf(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_corrupt_pdf())
    with pytest.raises(RenderError):
        await child()(pdf, 0, tmp_path / "page-0.png")
    assert not (tmp_path / "page-0.png").exists()


async def test_a_child_that_runs_too_long_is_killed(tmp_path: Path) -> None:
    renderer = child(0.5, argv=[sys.executable, "-c", "import time; time.sleep(60)"])
    started = time.monotonic()
    with pytest.raises(RenderError, match="took too long"):
        await renderer(tmp_path / "x.pdf", 0, tmp_path / "x.png")
    assert time.monotonic() - started < 5
    assert renderer.last_pid is not None
    with pytest.raises(ProcessLookupError):
        __import__("os").kill(renderer.last_pid, 0)  # gone, and reaped


async def test_a_child_that_crashes_is_a_render_error(tmp_path: Path) -> None:
    renderer = child(
        argv=[sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"]
    )
    with pytest.raises(RenderError, match="crashed|killed|signal"):
        await renderer(tmp_path / "x.pdf", 0, tmp_path / "x.png")


async def test_a_child_that_says_nothing_useful_is_a_render_error(tmp_path: Path) -> None:
    renderer = child(argv=[sys.executable, "-c", "print('hello')"])
    with pytest.raises(RenderError):
        await renderer(tmp_path / "x.pdf", 0, tmp_path / "x.png")


async def test_the_child_gets_none_of_the_workers_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "hunter2")
    script = (
        "import json, os, sys; "
        "leaked = 'SECRET_TOKEN' in os.environ; "
        "json.dump({'ok': {'width': 1 if leaked else 0, 'height': 1}}, sys.stdout)"
    )
    renderer = child(argv=[sys.executable, "-c", script])
    assert await renderer(tmp_path / "x.pdf", 0, tmp_path / "x.png") == (0, 1)


async def test_the_child_cannot_use_more_memory_than_it_is_given(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path, make_pdf(1, page_size=LETTER))
    tight = SubprocessRenderer(timeout_seconds=30, memory_mb=8, max_file_bytes=300 * 1024 * 1024)
    with pytest.raises(RenderError):
        await tight(pdf, 0, tmp_path / "page-0.png")
    # and the same child with room to work succeeds
    assert await child()(pdf, 0, tmp_path / "page-0.png") == (2550, 3300)


async def test_the_worker_process_never_loads_the_pdf_parser() -> None:
    """The parent imports no pdfium: a PDF that crashes it can only crash a child."""
    code = (
        "import sys\n"
        "from decimer_api.worker import render\n"
        "render.SubprocessRenderer(timeout_seconds=1, memory_mb=1, max_file_bytes=1)\n"
        "print([m for m in ('pypdfium2', 'PIL', 'pillow_heif') if m in sys.modules])\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", code, stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await process.communicate()
    assert stdout.decode().strip() == "[]"
