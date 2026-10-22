"""Parsing untrusted files happens in a child process with resource limits and a deadline."""

from __future__ import annotations

import asyncio
import ctypes
import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from decimer_api.errors import ApiError
from decimer_api.hardening import make_undumpable
from decimer_api.ingest_runner import SubprocessIngest
from decimer_api.settings import Settings
from decimer_api.uploads import Upload
from tests.factories import (
    make_corrupt_pdf,
    make_encrypted_pdf,
    make_pdf,
    make_png,
    make_self_referencing_pdf,
    pdf_page_texts,
)

OK_JSON = '{"ok": {"pages": 1, "pages_processed": 1, "truncated": false, "warnings": []}}'


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(data_dir=tmp_path / "jobs", ip_hash_secret="x", **overrides)  # type: ignore[arg-type]


def uploads(*files: tuple[str, bytes]) -> list[Upload]:
    return [Upload(name=name, data=io.BytesIO(data)) for name, data in files]


async def run(
    tmp_path: Path, *files: tuple[str, bytes], argv: list[str] | None = None, **overrides: object
) -> Path:
    settings = settings_for(tmp_path, **overrides)
    runner = SubprocessIngest(settings, argv=argv)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    await runner(uploads(*files), job_dir)
    return job_dir


async def refusal(
    tmp_path: Path, *files: tuple[str, bytes], argv: list[str] | None = None, **overrides: object
) -> ApiError:
    with pytest.raises(ApiError) as caught:
        await run(tmp_path, *files, argv=argv, **overrides)
    return caught.value


def child(code: str) -> list[str]:
    """A stand-in for the ingest worker that runs `code` instead."""
    return [sys.executable, "-I", "-c", code]


# --- the real worker ------------------------------------------------------------------------


async def test_the_worker_ingests_images_and_pdfs(tmp_path: Path) -> None:
    job_dir = await run(
        tmp_path / "a", ("a.png", make_png((30, 20))), ("b.png", make_png((10, 40)))
    )
    assert [Image.open(job_dir / "input" / n).size for n in ("0001.png", "0002.png")] == [
        (30, 20),
        (10, 40),
    ]
    assert not (job_dir / "incoming").exists()  # the raw uploads are not kept

    job_dir = await run(tmp_path / "b", ("doc.pdf", make_pdf(12)))
    assert pdf_page_texts(job_dir / "input" / "document.pdf")[-1] == "page 10"


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (make_corrupt_pdf(), 400, "corrupt_file"),
        (make_self_referencing_pdf(), 400, "corrupt_file"),
        (make_encrypted_pdf(), 400, "encrypted_pdf"),
        (make_png()[:40], 400, "corrupt_file"),
        (b"plain text", 415, "unsupported"),
    ],
)
async def test_errors_come_back_as_api_errors(
    tmp_path: Path, data: bytes, status: int, code: str
) -> None:
    error = await refusal(tmp_path, ("x.bin", data))
    assert (error.status, error.code) == (status, code)


async def test_the_open_files_limit_leaves_room_for_every_file_of_the_upload(
    tmp_path: Path,
) -> None:
    files = [(f"{n}.png", make_png((4, 4))) for n in range(100)]
    job_dir = await run(tmp_path, *files, max_upload_files=120)
    assert len(list((job_dir / "input").glob("*.png"))) == 100


async def test_limits_are_passed_to_the_worker(tmp_path: Path) -> None:
    error = await refusal(tmp_path, ("a.png", make_png((100, 100))), max_image_pixels=9_999)
    assert (error.status, error.code) == (413, "too_many_pixels")


# --- limits and deadlines -------------------------------------------------------------------


async def test_a_child_that_used_almost_no_cpu_before_the_deadline_is_our_load_not_the_files(
    tmp_path: Path,
) -> None:
    """A child that waits (starved of CPU by a busy host, or stuck) and is killed at the deadline
    with next to no CPU time used is not a file that is too hard: the client should retry
    (503 busy), not be told its file is too big (413)."""
    started = time.monotonic()
    error = await refusal(
        tmp_path,
        ("a.png", make_png()),
        argv=child("import time; time.sleep(60)"),
        ingest_timeout_seconds=1.0,
    )
    assert (error.status, error.code) == (503, "busy")
    assert error.headers["Retry-After"] == "30"
    assert time.monotonic() - started < 10


async def test_a_file_that_burns_cpu_until_the_deadline_is_the_files_fault(tmp_path: Path) -> None:
    error = await refusal(
        tmp_path,
        ("a.png", make_png()),
        argv=child("while True: pass"),
        ingest_timeout_seconds=1.0,
    )
    assert (error.status, error.code) == (413, "processing_limit")
    assert "too long" in error.message


async def test_a_child_that_is_unreadable_to_proc_is_judged_by_the_deadline_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without /proc there is no CPU figure: keep the old answer (413)."""
    monkeypatch.setattr("decimer_api.ingest_runner._cpu_seconds", lambda _pid: None)
    error = await refusal(tmp_path, ("a.png", make_png()), ingest_timeout_seconds=0.05)
    assert (error.status, error.code) == (413, "processing_limit")


async def test_a_memory_hungry_file_is_stopped_by_the_address_space_limit(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("1", (6000, 6000), 1).save(buffer, format="PNG")  # 36 MP: ~108 MB as RGB
    big = buffer.getvalue()
    error = await refusal(
        tmp_path / "limited", ("big.png", big), max_image_pixels=50_000_000, ingest_memory_mb=64
    )
    assert (error.status, error.code) == (413, "processing_limit")
    # The same file passes with room to work: the limit, not the file, caused the refusal.
    job_dir = await run(
        tmp_path / "roomy", ("big.png", big), max_image_pixels=50_000_000, ingest_memory_mb=1536
    )
    assert (job_dir / "input" / "0001.png").exists()


def test_the_worker_applies_its_limits_to_itself() -> None:
    script = (
        "import json, resource\n"
        "from decimer_api.ingest_worker import apply_limits\n"
        "apply_limits(memory_mb=100, cpu_seconds=7, max_file_bytes=12345)\n"
        "names = ['RLIMIT_AS', 'RLIMIT_CPU', 'RLIMIT_FSIZE', 'RLIMIT_NOFILE', 'RLIMIT_CORE']\n"
        "print(json.dumps({n: resource.getrlimit(getattr(resource, n)) for n in names}))\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True, timeout=60
    )
    limits = json.loads(result.stdout)
    assert limits["RLIMIT_CPU"] == [7, 7]  # soft and hard: it cannot raise them again
    assert limits["RLIMIT_FSIZE"] == [12345, 12345]
    assert limits["RLIMIT_CORE"] == [0, 0]
    assert limits["RLIMIT_NOFILE"][0] == limits["RLIMIT_NOFILE"][1] <= 128
    soft, hard = limits["RLIMIT_AS"]
    assert soft == hard and 0 < soft < 100 * 1024 * 1024 + 2**30  # a limit, close to usage + 100 MB


# --- how the child's exit is read -----------------------------------------------------------


async def test_a_crash_of_the_parser_is_a_corrupt_file(tmp_path: Path) -> None:
    error = await refusal(
        tmp_path,
        ("a.png", make_png()),
        argv=child("import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"),
    )
    assert (error.status, error.code) == (400, "corrupt_file")


@pytest.mark.parametrize("signal_name", ["SIGXCPU", "SIGKILL"])
async def test_running_into_a_resource_limit_is_reported_as_one(
    tmp_path: Path, signal_name: str
) -> None:
    error = await refusal(
        tmp_path,
        ("a.png", make_png()),
        argv=child(f"import os, signal; os.kill(os.getpid(), signal.{signal_name})"),
    )
    assert (error.status, error.code) == (413, "processing_limit")


async def test_a_worker_that_prints_nonsense_is_a_server_problem_not_a_bad_file(
    tmp_path: Path,
) -> None:
    error = await refusal(tmp_path, ("a.png", make_png()), argv=child("print('hello')"))
    assert (error.status, error.code) == (503, "unavailable")


async def test_the_children_environment_holds_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IP_HASH_SECRET", "super-secret")
    monkeypatch.setenv("REDIS_URL", "redis://:password@redis:6379/0")
    code = (
        "import os, json\n"
        "open('env.json', 'w').write(json.dumps(dict(os.environ)))\n"
        f"print('{OK_JSON}')"
    )
    job_dir = await run(tmp_path, ("a.png", make_png()), argv=child(code))
    seen = (job_dir / "env.json").read_text()
    assert "super-secret" not in seen and "password" not in seen


async def test_the_childs_stderr_is_captured_not_forwarded(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    code = f"import sys; sys.stderr.write('pdfium says something\\n'); print('{OK_JSON}')"
    await run(tmp_path, ("a.png", make_png()), argv=child(code))
    captured = capfd.readouterr()
    assert "pdfium says something" not in captured.err
    assert "pdfium says something" not in captured.out


async def test_no_parser_error_prints_to_the_servers_stderr(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    for data in (make_corrupt_pdf(), make_self_referencing_pdf(), make_encrypted_pdf()):
        await refusal(tmp_path / str(len(data)), ("a.pdf", data))
    assert capfd.readouterr().err == ""


def test_the_api_process_never_loads_the_parsers() -> None:
    """Pillow, pillow-heif and pdfium are imported by the ingest child only."""
    script = (
        "import sys\n"
        "import decimer_api.app, decimer_api.routes, decimer_api.ingest_runner\n"
        "loaded = [m for m in ('PIL', 'pillow_heif', 'pypdfium2') if m in sys.modules]\n"
        "print(loaded)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True, timeout=60
    )
    assert result.stdout.strip() == "[]"


async def test_garbage_is_refused_before_any_process_is_started(tmp_path: Path) -> None:
    runner = SubprocessIngest(settings_for(tmp_path))
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    for files in ([("a.png", b"not an image")], [("a.pdf", make_pdf()), ("b.png", make_png())]):
        with pytest.raises(ApiError):
            await runner(uploads(*files), job_dir)
    assert runner.last_pid is None
    assert not (job_dir / "incoming").exists()


async def test_the_raw_uploads_are_not_kept_after_the_child_is_done(tmp_path: Path) -> None:
    job_dir = await run(tmp_path, ("a.png", make_png()))
    assert not (job_dir / "incoming").exists()
    with pytest.raises(ApiError):
        await run(tmp_path / "bad", ("a.png", make_png()[:40]))
    assert not (tmp_path / "bad" / "job" / "incoming").exists()


async def test_the_child_decides_the_error_code_but_never_the_status(tmp_path: Path) -> None:
    lie = '{"error": {"status": 200, "code": "corrupt_file", "message": "x"}}'
    error = await refusal(tmp_path / "a", ("a.png", make_png()), argv=child(f"print('{lie}')"))
    assert (error.status, error.code) == (400, "corrupt_file")
    other = '{"error": {"status": 200, "code": "something_else", "message": "x"}}'
    error = await refusal(tmp_path / "b", ("a.png", make_png()), argv=child(f"print('{other}')"))
    assert (error.status, error.code) == (503, "unavailable")
    internal = '{"error": {"code": "internal_error", "message": "boom"}}'
    error = await refusal(tmp_path / "c", ("a.png", make_png()), argv=child(f"print('{internal}')"))
    assert (error.status, error.code) == (503, "unavailable")


# --- the whole process group is killed ------------------------------------------------------


def alive(pid: int) -> bool:
    """Whether the process exists and is not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


async def test_a_grandchild_of_the_worker_dies_with_it(tmp_path: Path) -> None:
    """Killing only the child would leave whatever it started running. The child runs in its
    own session and the whole process group is killed."""
    code = (
        "import subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open('grandchild.pid', 'w').write(str(grandchild.pid))\n"
        "time.sleep(60)\n"
    )
    settings = settings_for(tmp_path, ingest_timeout_seconds=1.5)
    runner = SubprocessIngest(settings, argv=child(code))
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    with pytest.raises(ApiError):
        # asyncio waits for the child's pipes to close, and the grandchild holds them: if only
        # the child were killed this call would last as long as the grandchild's 60 s sleep.
        await asyncio.wait_for(runner(uploads(("a.png", make_png())), job_dir), timeout=15)
    grandchild = int((job_dir / "grandchild.pid").read_text())
    for _ in range(50):  # init reaps it a moment after the kill
        if not alive(grandchild):
            break
        await asyncio.sleep(0.1)
    assert not alive(grandchild), "the grandchild outlived its parent"


# --- the API's secrets are out of the child's reach -----------------------------------------


READ_PARENT_ENVIRON = (
    "import os, json\n"
    "try:\n"
    "    open(f'/proc/{os.getppid()}/environ', 'rb').read()\n"
    "    result = 'readable'\n"
    "except OSError as error:\n"
    "    result = type(error).__name__\n"
    "open('environ.result', 'w').write(result)\n"
    f"print('{OK_JSON}')"
)


async def read_parent_environ(tmp_path: Path) -> str:
    job_dir = await run(tmp_path, ("a.png", make_png()), argv=child(READ_PARENT_ENVIRON))
    return (job_dir / "environ.result").read_text()


async def test_the_child_cannot_read_the_parents_environment(tmp_path: Path) -> None:
    """The child gets a scrubbed environment, but it runs as the same user, so it could read
    the API's /proc/<pid>/environ (IP_HASH_SECRET, REDIS_URL) unless the API makes itself
    non-dumpable."""
    if await read_parent_environ(tmp_path / "control") != "readable":
        pytest.skip("this host already hides a parent's environ from its children")
    assert make_undumpable() is True
    try:
        assert await read_parent_environ(tmp_path / "hardened") == "PermissionError"
    finally:
        libc = ctypes.CDLL(None)
        libc.prctl(4, 1, 0, 0, 0)  # PR_SET_DUMPABLE back to 1 for the rest of the test run
