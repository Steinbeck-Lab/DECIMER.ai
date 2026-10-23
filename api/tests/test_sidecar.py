"""The ingest sidecar: the parsers run in another container, the api talks to it over
TCP.

Real server, real child processes, real sockets on 127.0.0.1; the only thing missing is the
container. What is checked: the sidecar gives exactly the files the in-process path gives; every
error crosses the wire as the same API error; the api checks what comes back as the untrusted answer
it is; the sidecar keeps nothing after a request; and the api is not ready without it.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from decimer_api import sidecar_client, sidecar_wire
from decimer_api.analysis_pool import InProcessAnalysis
from decimer_api.app import create_app
from decimer_api.chemistry import analyse_molfile, analyse_smiles
from decimer_api.errors import ApiError
from decimer_api.ingest_runner import InProcessIngest
from decimer_api.molfile import MolfileError, TooComplex
from decimer_api.settings import Settings
from decimer_api.sidecar import Sidecar
from decimer_api.sidecar_client import RemoteAnalysis, RemoteIngest
from decimer_api.store import JobStore
from decimer_api.uploads import Ingested, Limits, Upload
from tests.conftest import FakeClock, FakeEnqueue
from tests.factories import (
    make_corrupt_pdf,
    make_encrypted_pdf,
    make_pdf,
    make_png,
    multipart,
    pdf_page_texts,
)
from tests.results import FakeNamer, molfile_of

PAGE = (144.0, 144.0)


@dataclass
class Running:
    port: int
    scratch: Path
    sidecar: Sidecar

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"


async def start_sidecar(settings: Settings, scratch: Path, **parts: Any) -> AsyncIterator[Running]:
    sidecar = Sidecar(settings, scratch=scratch, **parts)
    await sidecar.start()
    server = await sidecar.listen("127.0.0.1", 0)
    yield Running(server.sockets[0].getsockname()[1], scratch, sidecar)
    server.close()
    await server.wait_closed()
    await sidecar.aclose()


@pytest.fixture
async def running(settings: Settings, tmp_path: Path) -> AsyncIterator[Running]:
    """A child process per upload, as in production; molfiles are read in a thread, to keep the
    tests that are about uploads from starting the RDKit children."""
    analysis = InProcessAnalysis(settings.max_concurrent_analyses)
    async for item in start_sidecar(settings, tmp_path / "scratch", analysis=analysis):
        yield item


@pytest.fixture
async def full(settings: Settings, tmp_path: Path) -> AsyncIterator[Running]:
    """The real thing: a child process per upload, a pool of RDKit children for molfiles."""
    async for item in start_sidecar(settings, tmp_path / "scratch"):
        yield item


def uploads(*files: tuple[str, bytes]) -> list[Upload]:
    return [Upload(name=name, data=io.BytesIO(data)) for name, data in files]


def remote(settings: Settings, url: str, **overrides: Any) -> RemoteIngest:
    return RemoteIngest(settings.model_copy(update={"ingest_sidecar_url": url, **overrides}))


def tree(folder: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def leftovers(scratch: Path) -> list[str]:
    return sorted(path.name for path in scratch.iterdir()) if scratch.exists() else []


async def emptied(scratch: Path) -> list[str]:
    """What is left in the scratch once the sidecar has finished its clean-up, which it does after
    it has answered (the client sees the answer first)."""
    for _ in range(100):
        if not leftovers(scratch):
            break
        await asyncio.sleep(0.05)
    return leftovers(scratch)


# --- the same files as in process ------------------------------------------------------------


async def test_the_sidecar_gives_the_files_the_in_process_path_gives(
    settings: Settings, running: Running, tmp_path: Path
) -> None:
    files = [("a.png", make_png((90, 60))), ("b.png", make_png((40, 50)))]
    inside = InProcessIngest(Limits.from_settings(settings))
    expected = await inside(uploads(*files), tmp_path / "inside")
    answer = await remote(settings, running.url)(uploads(*files), tmp_path / "outside")
    assert answer == expected
    assert tree(tmp_path / "outside") == tree(tmp_path / "inside")
    assert sorted(tree(tmp_path / "outside")) == ["input/0001.png", "input/0002.png"]


async def test_a_pdf_longer_than_the_limit_comes_back_trimmed_with_its_warning(
    settings: Settings, tmp_path: Path
) -> None:
    small = settings.model_copy(update={"max_pdf_pages": 2})  # the sidecar and the api agree
    pdf = make_pdf(4, page_size=PAGE)
    async for item in start_sidecar(small, tmp_path / "scratch"):
        answer = await remote(small, item.url)(uploads(("paper.pdf", pdf)), tmp_path / "job")
        assert (answer.pages, answer.pages_processed, answer.truncated) == (4, 2, True)
        assert [w.code for w in answer.warnings] == ["pages_truncated"]
        stored = tmp_path / "job" / "input" / "document.pdf"
        assert pdf_page_texts(stored) == pdf_page_texts(pdf)[:2]


async def test_the_sidecar_keeps_nothing_after_a_request(
    settings: Settings, running: Running, tmp_path: Path
) -> None:
    ingest = remote(settings, running.url)
    await ingest(uploads(("a.png", make_png())), tmp_path / "ok")
    with pytest.raises(ApiError):
        await ingest(uploads(("b.png", b"\x89PNG\r\n\x1a\nbroken")), tmp_path / "broken")
    assert await emptied(running.scratch) == []
    # and the api's side holds only what it was sent: the job folder has the input and no scratch
    assert sorted(path.name for path in (tmp_path / "ok").iterdir()) == ["input"]


# --- errors cross the wire as the same errors ------------------------------------------------


@pytest.mark.parametrize(
    ("files", "status", "code"),
    [
        ([("bad.png", b"\x89PNG\r\n\x1a\nnot really a png")], 400, "corrupt_file"),
        ([("bad.pdf", make_corrupt_pdf())], 400, "corrupt_file"),
        ([("locked.pdf", make_encrypted_pdf())], 400, "encrypted_pdf"),
        ([("huge.png", make_png((5000, 5000)))], 413, "too_many_pixels"),
    ],
)
async def test_an_error_of_the_parser_is_the_same_api_error(
    settings: Settings,
    running: Running,
    tmp_path: Path,
    files: list[tuple[str, bytes]],
    status: int,
    code: str,
) -> None:
    inside = InProcessIngest(Limits.from_settings(settings))
    with pytest.raises(ApiError) as in_process:
        await inside(uploads(*files), tmp_path / "inside")
    with pytest.raises(ApiError) as over_the_wire:
        await remote(settings, running.url)(uploads(*files), tmp_path / "outside")
    assert (over_the_wire.value.status, str(over_the_wire.value.code)) == (status, code)
    assert over_the_wire.value.message == in_process.value.message


async def test_a_refusal_that_needs_no_parser_does_not_even_connect(
    settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(ApiError) as error:  # nothing listens on this port
        await remote(settings, "tcp://127.0.0.1:1")(uploads(("n.txt", b"hello")), tmp_path / "j")
    assert (error.value.status, str(error.value.code)) == (415, "unsupported")


async def test_a_busy_sidecar_answers_busy_with_retry_after_and_loses_nothing(
    settings: Settings, tmp_path: Path
) -> None:
    release = asyncio.Event()
    entered = asyncio.Event()

    class Parked:
        async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
            entered.set()
            await release.wait()
            return await InProcessIngest(Limits.from_settings(settings))(uploads, job_dir)

    one = settings.model_copy(update={"ingest_concurrency": 2})
    async for item in start_sidecar(one, tmp_path / "scratch", ingest=Parked()):
        item.sidecar._places = sidecar_places(1)  # noqa: SLF001  (one place: the second is busy)
        ingest = remote(settings, item.url)
        first = asyncio.create_task(ingest(uploads(("a.png", make_png())), tmp_path / "first"))
        await entered.wait()
        # A body far bigger than the socket buffers: the sidecar must read it and drop it before
        # answering, or closing the connection with data unread resets it, and the api never sees
        # `busy`.
        body = b"\x89PNG\r\n\x1a\n" + os.urandom(900_000)
        with pytest.raises(ApiError) as busy:
            await ingest(uploads(("b.png", body)), tmp_path / "second")
        assert (busy.value.status, str(busy.value.code)) == (503, "busy")
        assert busy.value.headers == {"Retry-After": "2"}
        release.set()
        assert (await first).pages == 1
        assert await emptied(item.scratch) == []


def sidecar_places(places: int) -> Any:
    from decimer_api.capacity import Capacity

    return Capacity(places)


# --- the sidecar is down ---------------------------------------------------------------------


async def test_without_a_sidecar_an_upload_is_unavailable_not_a_crash(
    settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(ApiError) as error:
        await remote(settings, "tcp://127.0.0.1:1")(uploads(("a.png", make_png())), tmp_path / "j")
    assert (error.value.status, str(error.value.code)) == (503, "unavailable")


@pytest.fixture
def api_for(
    settings: Settings,
    redis: fakeredis.FakeAsyncRedis,
    clock: FakeClock,
    enqueue: FakeEnqueue,
    namer: FakeNamer,
) -> Callable[[str], FastAPI]:
    def make(url: str) -> FastAPI:
        # No `ingest_runner` and no `analysis`: the settings choose the sidecar, as in production.
        sidecar_settings = settings.model_copy(update={"ingest_sidecar_url": url})
        return create_app(
            sidecar_settings, JobStore(redis, sidecar_settings, clock=clock), enqueue, namer=namer
        )

    return make


def http(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 5000))
    return httpx.AsyncClient(transport=transport, base_url="http://api.test")


async def test_the_api_is_ready_only_while_its_sidecar_answers(
    running: Running, api_for: Callable[[str], FastAPI]
) -> None:
    async with http(api_for(running.url)) as client:
        assert (await client.get("/readyz")).status_code == 200
    async with http(api_for("tcp://127.0.0.1:1")) as client:
        assert (await client.get("/readyz")).status_code == 503
        assert (await client.get("/healthz")).status_code == 200  # alive, not ready


async def test_an_upload_to_the_api_goes_through_the_sidecar(
    settings: Settings,
    running: Running,
    api_for: Callable[[str], FastAPI],
    enqueue: FakeEnqueue,
) -> None:
    body, headers = multipart([("paper.pdf", make_pdf(2, page_size=PAGE))])
    async with http(api_for(running.url)) as client:
        created = await client.post("/api/jobs", content=body, headers=headers)
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
    assert [p.name for p in (settings.data_dir / job_id).iterdir()] == ["input"]
    assert pdf_page_texts(settings.data_dir / job_id / "input" / "document.pdf") == [
        "page 1",
        "page 2",
    ]
    assert enqueue.job_ids == [job_id]
    assert await emptied(running.scratch) == []


async def test_an_upload_to_the_api_with_the_sidecar_down_is_503_and_leaves_no_folder(
    settings: Settings, api_for: Callable[[str], FastAPI], enqueue: FakeEnqueue
) -> None:
    body, headers = multipart([("a.png", make_png())])
    async with http(api_for("tcp://127.0.0.1:1")) as client:
        response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "unavailable"
    assert enqueue.job_ids == []
    assert not settings.data_dir.exists() or list(settings.data_dir.iterdir()) == []


# --- what comes back is checked --------------------------------------------------------------


class Hostile:
    """A stand-in for a compromised sidecar: reads the request, then does what the test says."""

    def __init__(self, act: Callable[[asyncio.StreamWriter], Awaitable[None]]) -> None:
        self._act = act
        self.port = 0
        self._tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> Hostile:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for task in self._tasks:
            task.cancel()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._tasks.add(asyncio.current_task())  # type: ignore[arg-type]
        try:
            request = await sidecar_wire.read_frame(reader)
            for item in request.get("files", []):
                await reader.readexactly(item["size"])
            await self._act(writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    @property
    def url(self) -> str:
        return f"tcp://127.0.0.1:{self.port}"


def ok_frame(**changes: Any) -> dict[str, Any]:
    ok = {
        "pages": 1,
        "pages_processed": 1,
        "truncated": False,
        "warnings": [],
        "files": [{"name": "0001.png", "size": 4}],
    }
    return {"ok": ok | changes}


async def answer(
    writer: asyncio.StreamWriter, frame: dict[str, Any], body: bytes = b"data"
) -> None:
    await sidecar_wire.write_frame(writer, frame)
    writer.write(body)
    await writer.drain()


async def against(
    settings: Settings,
    act: Callable[[asyncio.StreamWriter], Awaitable[None]],
    tmp_path: Path,
    *files: tuple[str, bytes],
) -> tuple[Ingested | ApiError, Path]:
    """One upload (an image unless `files` say otherwise) to a stand-in sidecar that does `act`."""
    async with Hostile(act) as hostile:
        job = tmp_path / "job"
        try:
            result: Ingested | ApiError = await remote(settings, hostile.url)(
                uploads(*(files or (("a.png", make_png()),))), job
            )
        except ApiError as error:
            result = error
    return result, job


async def test_a_wellformed_answer_from_a_stand_in_is_accepted(
    settings: Settings, tmp_path: Path
) -> None:
    result, job = await against(settings, lambda w: answer(w, ok_frame()), tmp_path)
    assert isinstance(result, Ingested) and result.pages == 1
    assert (job / "input" / "0001.png").read_bytes() == b"data"


BAD_ANSWERS: list[tuple[str, dict[str, Any]]] = [
    ("path out of the folder", ok_frame(files=[{"name": "../../0001.png", "size": 4}])),
    ("absolute path", ok_frame(files=[{"name": "/etc/cron.d/x", "size": 4}])),
    ("a pdf for an image upload", ok_frame(files=[{"name": "document.pdf", "size": 4}])),
    ("an extra file", ok_frame(files=[{"name": "0001.png", "size": 4}, {"name": "x", "size": 1}])),
    (
        "a second image that was never uploaded",
        ok_frame(files=[{"name": "0001.png", "size": 4}] * 2),
    ),
    ("no files", ok_frame(files=[])),
    ("a size that is not a number", ok_frame(files=[{"name": "0001.png", "size": "4"}])),
    ("a negative size", ok_frame(files=[{"name": "0001.png", "size": -1}])),
    ("a size over the bound", ok_frame(files=[{"name": "0001.png", "size": 10**12}])),
    ("pages of an image upload", ok_frame(pages=5)),
    ("processed pages", ok_frame(pages_processed=2)),
    ("truncated images", ok_frame(truncated=True)),
    ("pages that are not an int", ok_frame(pages="1")),
    ("pages that are a bool", ok_frame(pages=True)),
    ("a warning code with capitals", ok_frame(warnings=[{"code": "BAD CODE", "message": "x"}])),
    ("a warning without a message", ok_frame(warnings=[{"code": "fine"}])),
    ("too many warnings", ok_frame(warnings=[{"code": "w", "message": "m"}] * 50)),
    ("no ok at all", {"ok": None}),
    ("neither ok nor error", {"nothing": 1}),
    ("a success status", {"error": {"status": 200, "code": "busy", "message": "m"}}),
    ("a status past 599", {"error": {"status": 999, "code": "busy", "message": "m"}}),
    ("a code that does not exist", {"error": {"status": 400, "code": "nope", "message": "m"}}),
    ("an error that is no object", {"error": "boom"}),
]


@pytest.mark.parametrize(("what", "frame"), BAD_ANSWERS, ids=[what for what, _ in BAD_ANSWERS])
async def test_an_answer_that_cannot_be_true_is_unavailable_and_writes_nothing_outside_input(
    settings: Settings, tmp_path: Path, what: str, frame: dict[str, Any]
) -> None:
    result, job = await against(settings, lambda w: answer(w, frame), tmp_path)
    assert isinstance(result, ApiError), what
    assert (result.status, str(result.code)) == (503, "unavailable")
    # checked before anything is written: not a file, and not even the folder
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == []


def pdf_frame(**changes: Any) -> dict[str, Any]:
    return ok_frame(files=[{"name": "document.pdf", "size": 4}], pages=3, pages_processed=3) | {
        "ok": ok_frame(files=[{"name": "document.pdf", "size": 4}], pages=3, pages_processed=3)[
            "ok"
        ]
        | changes
    }


async def test_a_wellformed_pdf_answer_is_accepted(settings: Settings, tmp_path: Path) -> None:
    pdf = ("paper.pdf", make_pdf(3, page_size=PAGE))
    result, job = await against(settings, lambda w: answer(w, pdf_frame()), tmp_path, pdf)
    assert isinstance(result, Ingested) and (result.pages, result.pages_processed) == (3, 3)
    assert (job / "input" / "document.pdf").read_bytes() == b"data"


BAD_PDF_ANSWERS: list[tuple[str, dict[str, Any]]] = [
    ("an image for a pdf upload", pdf_frame(files=[{"name": "0001.png", "size": 4}])),
    ("a path out of the folder", pdf_frame(files=[{"name": "../document.pdf", "size": 4}])),
    ("a second file", pdf_frame(files=[{"name": "document.pdf", "size": 4}] * 2)),
    ("no file", pdf_frame(files=[])),
    ("no processed page", pdf_frame(pages_processed=0)),
    ("more pages processed than the pdf has", pdf_frame(pages=3, pages_processed=4)),
    (
        "more pages processed than the limit",
        pdf_frame(pages=50, pages_processed=11, truncated=True),
    ),
    ("truncated without a cut", pdf_frame(truncated=True)),
    ("a cut that is not said", pdf_frame(pages=5, pages_processed=3, truncated=False)),
]


@pytest.mark.parametrize(
    ("what", "frame"), BAD_PDF_ANSWERS, ids=[what for what, _ in BAD_PDF_ANSWERS]
)
async def test_a_pdf_answer_that_cannot_be_true_is_unavailable(
    settings: Settings, tmp_path: Path, what: str, frame: dict[str, Any]
) -> None:
    pdf = ("paper.pdf", make_pdf(3, page_size=PAGE))
    result, _ = await against(settings, lambda w: answer(w, {"ok": frame["ok"]}), tmp_path, pdf)
    assert isinstance(result, ApiError), what
    assert (result.status, str(result.code)) == (503, "unavailable")
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == []


async def test_a_file_over_the_bound_of_one_image_is_refused_even_within_the_total(
    settings: Settings, tmp_path: Path
) -> None:
    """The per-file bound is 3 x MAX_IMAGE_PIXELS + 64 MiB (121.2 MiB here), the total 3 x
    MAX_JOB_PIXELS + 64 MiB (350.1 MiB): a file of 200 MiB is inside the total and outside its
    own bound."""
    big = ok_frame(files=[{"name": "0001.png", "size": 200 * 1024 * 1024}])
    result, job = await against(settings, lambda w: answer(w, big, b""), tmp_path)
    assert isinstance(result, ApiError) and str(result.code) == "unavailable"
    assert not (job / "input").exists()  # refused on the announcement, before anything was written


async def test_files_that_are_each_in_bound_but_together_over_the_total_are_refused(
    settings: Settings, tmp_path: Path
) -> None:
    # Bounds here: 121.2 MiB a file (3 x 20 MP + 64 MiB), 350.1 MiB in all (3 x 100 MP + 64 MiB).
    # Three files of 120 MiB are each in bound and 360 MiB together.
    files = [{"name": f"{n:04d}.png", "size": 120 * 1024 * 1024} for n in (1, 2, 3)]
    frame = ok_frame(files=files, pages=3, pages_processed=3)
    three = [(f"{n}.png", make_png()) for n in (1, 2, 3)]
    result, job = await against(settings, lambda w: answer(w, frame, b""), tmp_path, *three)
    assert isinstance(result, ApiError) and str(result.code) == "unavailable"
    assert not (job / "input").exists()


async def test_a_retry_after_that_is_not_a_number_is_dropped(
    settings: Settings, tmp_path: Path
) -> None:
    error = {
        "error": {
            "status": 503,
            "code": "busy",
            "message": "m",
            "headers": {"Retry-After": "7\r\nSet-Cookie: x=1"},
        }
    }
    result, _ = await against(settings, lambda w: answer(w, error, b""), tmp_path)
    assert isinstance(result, ApiError) and result.headers == {}


async def test_the_input_folder_of_a_job_is_never_written_into_twice(
    running: Running, settings: Settings, tmp_path: Path
) -> None:
    (tmp_path / "job" / "input").mkdir(parents=True)
    with pytest.raises(ApiError) as error:
        await remote(settings, running.url)(uploads(("a.png", make_png())), tmp_path / "job")
    assert str(error.value.code) == "unavailable"


async def test_a_real_error_with_a_retry_after_passes_through(
    settings: Settings, tmp_path: Path
) -> None:
    error = {
        "error": {
            "status": 503,
            "code": "busy",
            "message": "The service is busy.",
            "headers": {"Retry-After": "7", "X-Other": "ignored"},
        }
    }
    result, _ = await against(settings, lambda w: answer(w, error, b""), tmp_path)
    assert isinstance(result, ApiError)
    assert (result.status, str(result.code), result.headers) == (503, "busy", {"Retry-After": "7"})


async def test_a_warning_is_cut_to_a_short_message(settings: Settings, tmp_path: Path) -> None:
    warning = {"code": "pages_truncated", "message": "x" * 5000}
    result, _ = await against(settings, lambda w: answer(w, ok_frame(warnings=[warning])), tmp_path)
    assert isinstance(result, Ingested) and len(result.warnings[0].message) == 300


@pytest.mark.parametrize(
    "act",
    [
        pytest.param(lambda w: _nothing(w), id="the sidecar closes without an answer"),
        pytest.param(lambda w: _short_body(w), id="the body is shorter than announced"),
        pytest.param(lambda w: _garbage(w), id="the answer is not a frame"),
        pytest.param(
            lambda w: _giant_frame(w), id="the frame announces more than any frame may be"
        ),
        pytest.param(lambda w: _not_json(w), id="the frame is not JSON"),
    ],
)
async def test_a_sidecar_that_misbehaves_is_unavailable(
    settings: Settings, tmp_path: Path, act: Callable[[asyncio.StreamWriter], Awaitable[None]]
) -> None:
    result, _ = await against(settings, act, tmp_path)
    assert isinstance(result, ApiError)
    assert (result.status, str(result.code)) == (503, "unavailable")


async def _nothing(_writer: asyncio.StreamWriter) -> None:
    return None


async def _short_body(writer: asyncio.StreamWriter) -> None:
    await answer(writer, ok_frame(files=[{"name": "0001.png", "size": 1000}]), b"only a little")


async def _garbage(writer: asyncio.StreamWriter) -> None:
    writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
    await writer.drain()


async def _giant_frame(writer: asyncio.StreamWriter) -> None:
    writer.write(b"\xff\xff\xff\xff")
    await writer.drain()


async def _not_json(writer: asyncio.StreamWriter) -> None:
    writer.write(sidecar_wire.protocol.HEADER.pack(5) + b"{nope")
    await writer.drain()


async def test_a_sidecar_that_never_answers_is_given_up_on(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_client, "INGEST_MARGIN_SECONDS", 0.05)

    async def silent(_writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(30)

    quick = settings.model_copy(update={"ingest_timeout_seconds": 0.2})
    started = time.monotonic()
    result, _ = await against(quick, silent, tmp_path)
    assert isinstance(result, ApiError) and str(result.code) == "unavailable"
    assert time.monotonic() - started < 3


# --- what the sidecar accepts ----------------------------------------------------------------


async def talk(port: int, *frames: dict[str, Any]) -> list[dict[str, Any] | None]:
    """Send frames on one connection; the answers (None when the sidecar closed instead)."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    answers: list[dict[str, Any] | None] = []
    try:
        for frame in frames:
            await sidecar_wire.write_frame(writer, frame)
            try:
                answers.append(await asyncio.wait_for(sidecar_wire.read_frame(reader), 5))
            except (asyncio.IncompleteReadError, ConnectionError):
                answers.append(None)
    finally:
        writer.close()
    return answers


async def test_the_sidecar_answers_a_ping_and_refuses_what_it_does_not_know(
    running: Running,
) -> None:
    assert await sidecar_wire.ping("127.0.0.1", running.port) is True
    (unknown,) = await talk(running.port, {"op": "shell", "cmd": "id"})
    assert unknown is not None and unknown["error"]["code"] == "invalid_request"
    assert await sidecar_wire.ping("127.0.0.1", running.port) is True  # still serving


@pytest.mark.parametrize(
    "announcement",
    [
        {"op": "ingest"},
        {"op": "ingest", "files": []},
        {"op": "ingest", "files": "a.png"},
        {"op": "ingest", "files": [{"name": "a", "size": -5}]},
        {"op": "ingest", "files": [{"name": "a", "size": "5"}]},
        {"op": "ingest", "files": [{"name": 7, "size": 5}]},
        {"op": "ingest", "files": [{"name": "a", "size": 10**12}]},
        {"op": "ingest", "files": [{"name": "a", "size": 1}] * 500},
        {"op": "ingest", "files": ["a"]},
    ],
)
async def test_the_sidecar_refuses_a_bad_announcement_before_reading_a_byte(
    running: Running, announcement: dict[str, Any]
) -> None:
    (answered,) = await talk(running.port, announcement)
    assert answered is not None and answered["error"]["code"] == "invalid_request"
    assert await emptied(running.scratch) == []


async def test_a_connection_that_sends_garbage_is_dropped_and_the_sidecar_lives(
    running: Running,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", running.port)
    writer.write(b"\xff\xff\xff\xff" + b"GET / HTTP/1.1\r\n\r\n")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 5) == b""  # closed without an answer
    writer.close()
    assert await sidecar_wire.ping("127.0.0.1", running.port) is True


async def test_an_upload_cut_off_halfway_leaves_nothing_in_the_scratch(
    running: Running,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", running.port)
    await sidecar_wire.write_frame(
        writer, {"op": "ingest", "files": [{"name": "a.png", "size": 100_000}]}
    )
    writer.write(b"x" * 1000)  # and then the api goes away
    await writer.drain()
    writer.close()
    assert await emptied(running.scratch) == []
    assert await sidecar_wire.ping("127.0.0.1", running.port) is True


async def test_a_symlink_a_parser_leaves_in_its_output_is_not_sent(
    settings: Settings, tmp_path: Path
) -> None:
    """A compromised child could plant `0001.png` as a link to a file of the sidecar: only regular
    files are sent."""
    secret = tmp_path / "secret.txt"
    secret.write_text("the sidecar's own file")

    class Planting:
        async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
            (job_dir / "input").mkdir(parents=True)
            (job_dir / "input" / "0001.png").symlink_to(secret)
            return Ingested(pages=1, pages_processed=1)

    async for item in start_sidecar(settings, tmp_path / "scratch", ingest=Planting()):
        with pytest.raises(ApiError) as error:
            await remote(settings, item.url)(uploads(("a.png", make_png())), tmp_path / "job")
    assert str(error.value.code) == "unavailable"  # no 0001.png came back
    assert not any(
        "sidecar's own file" in p.read_text()
        for p in tmp_path.rglob("*")
        if p.is_file() and p != secret
    )


# --- molfiles --------------------------------------------------------------------------------


async def test_a_molfile_is_read_in_the_sidecar_as_it_is_in_process(
    settings: Settings, full: Running
) -> None:
    analysis = RemoteAnalysis(settings.model_copy(update={"ingest_sidecar_url": full.url}))
    molfile = molfile_of("CC(=O)Oc1ccccc1C(=O)O")
    assert await analysis.analyse(molfile) == analyse_molfile(molfile)


async def test_one_session_reads_several_molfiles_on_one_connection(
    settings: Settings, full: Running
) -> None:
    analysis = RemoteAnalysis(settings.model_copy(update={"ingest_sidecar_url": full.url}))
    async with analysis.session() as session:
        first = await session.analyse(molfile_of("CCO"))
        second = await session.analyse(molfile_of("c1ccccc1"))
    assert (first.smiles, second.smiles) == ("CCO", "c1ccccc1")
    assert analysis.capacity.in_use == 0  # the place is back


async def test_a_text_that_is_not_a_molfile_is_a_molfile_error(
    settings: Settings, full: Running
) -> None:
    analysis = RemoteAnalysis(settings.model_copy(update={"ingest_sidecar_url": full.url}))
    with pytest.raises(MolfileError):
        await analysis.analyse("this is not a molfile\nat all\n")


async def test_the_places_of_the_api_are_kept_without_a_connection(
    settings: Settings, full: Running
) -> None:
    one = settings.model_copy(update={"ingest_sidecar_url": full.url, "max_concurrent_analyses": 1})
    analysis = RemoteAnalysis(one)
    async with analysis.session():
        with pytest.raises(ApiError) as busy:
            await analysis.analyse(molfile_of("CCO"))
        assert (busy.value.status, str(busy.value.code)) == (503, "busy")
    assert (await analysis.analyse(molfile_of("CCO"))).valid is True


async def test_the_analysis_places_of_the_sidecar_are_busy_when_the_api_has_more(
    settings: Settings, tmp_path: Path
) -> None:
    """The sidecar enforces its own number of places and says `busy` as the api's pool would."""
    small = settings.model_copy(update={"max_concurrent_analyses": 1})
    async for item in start_sidecar(small, tmp_path / "scratch"):
        big = settings.model_copy(
            update={"ingest_sidecar_url": item.url, "max_concurrent_analyses": 3}
        )
        analysis = RemoteAnalysis(big)
        async with analysis.session():
            with pytest.raises(ApiError) as busy:
                await analysis.analyse(molfile_of("CCO"))
            assert (busy.value.status, str(busy.value.code)) == (503, "busy")


async def test_a_molfile_over_the_size_the_api_allows_is_refused_by_the_sidecar_too(
    full: Running,
) -> None:
    ready, refused = await talk(full.port, {"op": "analysis"}, {"molfile": "x" * 100_000})
    assert ready == {"ready": True}
    assert refused == {"error": "molfile", "message": "it is not a molfile that may be read"}


async def test_all_the_edits_of_one_session_share_the_batch_budget(
    settings: Settings, full: Running
) -> None:
    short = settings.model_copy(
        update={"ingest_sidecar_url": full.url, "analysis_batch_seconds": 0.3}
    )
    async with RemoteAnalysis(short).session() as session:
        await session.analyse(molfile_of("CCO"))
        await asyncio.sleep(0.4)  # the export's edits have used up their budget
        with pytest.raises(TooComplex):
            await session.analyse(molfile_of("CCO"))


class ScriptedSession:
    """A stand-in sidecar for the analysis side: says `ready`, then gives every frame `reply`."""

    def __init__(self, reply: Any) -> None:
        self._reply = reply
        self._tasks: set[asyncio.Task[Any]] = set()

    async def __aenter__(self) -> ScriptedSession:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: object) -> None:
        for task in self._tasks:
            task.cancel()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._tasks.add(asyncio.current_task())  # type: ignore[arg-type]
        try:
            await sidecar_wire.read_frame(reader)
            await sidecar_wire.write_frame(writer, {"ready": True})
            await sidecar_wire.read_frame(reader)
            if self._reply is None:
                return  # drop the connection mid-session
            if self._reply == "silence":
                await asyncio.sleep(30)
            await sidecar_wire.write_frame(writer, self._reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@pytest.mark.parametrize(
    ("reply", "raises", "status"),
    [
        ({"error": "too_complex"}, TooComplex, None),
        ({"error": "internal"}, TooComplex, None),
        ({"error": "something else"}, TooComplex, None),
        ({"ok": {"smiles": "x"}}, ApiError, 503),  # not a Chemistry: a sidecar that lies is down
        ({"error": "molfile", "message": "no good"}, MolfileError, None),
        ({"error": {"status": 503, "code": "busy", "message": "m", "headers": {}}}, ApiError, 503),
        ({"error": {"status": 200, "code": "busy", "message": "m"}}, ApiError, 503),
        (None, ApiError, 503),  # the connection drops
    ],
)
async def test_an_analysis_answer_becomes_the_error_the_routes_know(
    settings: Settings, reply: Any, raises: type[Exception], status: int | None
) -> None:
    async with ScriptedSession(reply) as stand_in:
        analysis = RemoteAnalysis(
            settings.model_copy(update={"ingest_sidecar_url": f"tcp://127.0.0.1:{stand_in.port}"})
        )
        with pytest.raises(raises) as error:
            await analysis.analyse(molfile_of("CCO"))
    if status is not None:
        assert isinstance(error.value, ApiError) and error.value.status == status


STRUCTURES = [
    "CC(=O)Oc1ccccc1C(=O)O",
    "[Na+].[Cl-]",
    "C[C@H](N)C(=O)O",
    "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
    "OC[C@H]1O[C@@H](O)[C@@H](O)[C@H](O)[C@H]1O",
    "[13CH3]C(=O)[O-]",
    "C(F)(F)(F)Br",
    "*C",
    "O",
    "not a structure",  # valid=False, everything else empty
]


@pytest.mark.parametrize("smiles", STRUCTURES)
def test_what_rdkit_draws_is_accepted_as_it_is(smiles: str) -> None:
    chemistry = analyse_smiles(smiles)
    assert sidecar_client.checked_chemistry(asdict(chemistry)) == chemistry


def _good() -> dict[str, Any]:
    return asdict(analyse_smiles("CCO"))


def _svg(*, inside: str = "", head: str = "") -> str:
    good = _good()["svg"]
    assert good.count("</svg>") == 1
    return head + good.replace("</svg>", inside + "</svg>")


HOSTILE: dict[str, Any] = {
    "not a mapping": ["smiles"],
    "an empty mapping": {},
    "a field is missing": {k: v for k, v in _good().items() if k != "svg"},
    "a field too many": {**_good(), "extra": "x"},
    "smiles is not text": {**_good(), "smiles": 5},
    "svg is not text": {**_good(), "svg": None},
    "valid is 1, not a bool": {**_good(), "valid": 1},
    "valid is a string": {**_good(), "valid": "true"},
    "smiles is a megabyte": {**_good(), "smiles": "C" * 70_000},
    "smiles has a line break": {**_good(), "smiles": "CCO\nCCO"},
    "smiles has an escape": {**_good(), "smiles": "CCO\x1b[31m"},
    "inchikey is short": {**_good(), "inchikey": "LFQSCWFLJHTTHZ"},
    "inchikey has lower case": {**_good(), "inchikey": "lfqscwfljhtthz-uhfffaoysa-n"},
    "inchikey has a tail": {**_good(), "inchikey": _good()["inchikey"] + "<script>"},
    "molfile with a second record": {
        **_good(),
        "molfile": _good()["molfile"].replace("M  END", "M  END\n$$$$\nM  END"),
    },
    "molfile with a NUL": {**_good(), "molfile": _good()["molfile"].replace("RDKit", "RD\x00Kit")},
    "molfile that does not end": {**_good(), "molfile": _good()["molfile"].split("M  END")[0]},
    "molfile of two megabytes": {**_good(), "molfile": "x" * 2_100_000 + "M  END"},
    "svg of five megabytes": {**_good(), "svg": _svg(inside="<g>" + "x" * 5_000_000 + "</g>")},
    "svg that is html": {**_good(), "svg": "<html><body>hi</body></html>"},
    "svg with a script": {**_good(), "svg": _svg(inside="<script>alert(1)</script>")},
    "svg with an event handler": {**_good(), "svg": _svg().replace("<svg", "<svg onload='x()'", 1)},
    "svg with a foreign object": {
        **_good(),
        "svg": _svg(inside="<foreignObject><iframe/></foreignObject>"),
    },
    "svg with a link": {
        **_good(),
        "svg": _svg(inside="<a href='https://x.example'><rect/></a>"),
    },
    "svg with a style sheet": {**_good(), "svg": _svg(inside="<style>@import 'x';</style>")},
    "svg with a url in a style": {
        **_good(),
        "svg": _svg(inside="<rect style='fill:url(https://x.example/a)'/>"),
    },
    "svg with a doctype": {**_good(), "svg": _svg(head="<!DOCTYPE svg [<!ENTITY x 'y'>]>")},
    "svg with a CDATA section": {**_good(), "svg": _svg(inside="<![CDATA[<script/>]]>")},
    "svg with a comment": {**_good(), "svg": _svg(inside="<!-- <script> -->")},
    "svg with an entity": {**_good(), "svg": _svg(inside="<text>&#60;script&#62;</text>")},
    "svg with a data uri": {**_good(), "svg": _svg(inside="<rect fill='data:text/html,x'/>")},
    "svg with an image": {**_good(), "svg": _svg(inside="<image width='1'/>")},
    "svg that starts with the wrong element": {**_good(), "svg": "<g/>"},
    "svg with an xml declaration": {**_good(), "svg": "<?xml version='1.0'?>" + _svg()},
    "svg with text before it": {**_good(), "svg": "x" + _svg()},
    "svg with a stray closing tag": {**_good(), "svg": _svg(inside="</div>")},
    "svg with a processing instruction": {**_good(), "svg": _svg(inside="<?pi x?>")},
    "svg with a plain comment": {**_good(), "svg": _svg(inside="<!-- hello -->")},
    "svg with the header comment twice": {
        **_good(),
        "svg": _svg(inside="<!-- END OF HEADER -->"),
    },
    "svg with a plain CDATA section": {**_good(), "svg": _svg(inside="<![CDATA[hello]]>")},
    "svg with a link on a shape": {
        **_good(),
        "svg": _svg(inside="<rect xlink:href='https://x.example'/>"),
    },
    "svg with a script url as text": {
        **_good(),
        "svg": _svg(inside="<text>javascript:alert(1)</text>"),
    },
    "svg with a script after the end": {**_good(), "svg": _svg() + "<script/>"},
}


@pytest.mark.parametrize("name", HOSTILE)
def test_a_structure_that_cannot_be_what_rdkit_made_is_refused(name: str) -> None:
    with pytest.raises(ValueError):
        sidecar_client.checked_chemistry(HOSTILE[name])


def test_the_empty_structure_of_an_invalid_input_is_accepted() -> None:
    nothing = replace(analyse_smiles("not a structure"), valid=False)
    assert sidecar_client.checked_chemistry(asdict(nothing)) == nothing


@pytest.mark.parametrize(
    "name", ["svg with a script", "valid is 1, not a bool", "smiles has a line break"]
)
async def test_a_structure_that_does_not_fit_is_unavailable_not_shown(
    settings: Settings, name: str
) -> None:
    async with ScriptedSession({"ok": HOSTILE[name]}) as stand_in:
        analysis = RemoteAnalysis(
            settings.model_copy(update={"ingest_sidecar_url": f"tcp://127.0.0.1:{stand_in.port}"})
        )
        with pytest.raises(ApiError) as error:
            await analysis.analyse(molfile_of("CCO"))
    assert error.value.status == 503


async def test_a_silent_sidecar_is_too_complex_after_the_molfile_timeout(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar_client, "MARGIN_SECONDS", 0.05)
    async with ScriptedSession("silence") as stand_in:
        analysis = RemoteAnalysis(
            settings.model_copy(
                update={
                    "ingest_sidecar_url": f"tcp://127.0.0.1:{stand_in.port}",
                    "analysis_timeout_seconds": 0.2,
                }
            )
        )
        started = time.monotonic()
        with pytest.raises(TooComplex):
            await analysis.analyse(molfile_of("CCO"))
    assert time.monotonic() - started < 3


async def test_a_validate_request_through_the_api_uses_the_sidecar(
    settings: Settings,
    full: Running,
    api_for: Callable[[str], FastAPI],
    redis: fakeredis.FakeAsyncRedis,
    clock: FakeClock,
) -> None:
    from tests.results import make_done_job

    sidecar_settings = settings.model_copy(update={"ingest_sidecar_url": full.url})
    store = JobStore(redis, sidecar_settings, clock=clock)
    job = await make_done_job(store, sidecar_settings, clock)
    app = create_app(sidecar_settings, store, FakeEnqueue(), namer=FakeNamer())
    async with http(app) as client:
        response = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": molfile_of("CCO")}
        )
        assert response.status_code == 200, response.text
        assert response.json()["smiles"] == "CCO" and response.json()["valid"] is True
        broken = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": "junk\nmore junk\n"}
        )
        assert broken.status_code == 422


# --- the process -----------------------------------------------------------------------------


async def test_the_sidecar_runs_as_a_process_answers_its_health_check_and_stops_on_sigterm(
    settings: Settings, tmp_path: Path
) -> None:
    probe = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = probe.sockets[0].getsockname()[1]
    probe.close()
    await probe.wait_closed()
    env = {
        **os.environ,
        "SIDECAR_HOST": "127.0.0.1",
        "SIDECAR_PORT": str(port),
        "SIDECAR_SCRATCH_DIR": str(tmp_path / "scratch"),
        "LOG_FORMAT": "json",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "decimer_api.sidecar", env=env, stderr=asyncio.subprocess.PIPE
    )
    try:
        for _ in range(100):
            if await sidecar_wire.ping("127.0.0.1", port):
                break
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("the sidecar did not start")
        health = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "decimer_api.sidecar", "--ping", env=env
        )
        assert await health.wait() == 0
        url = f"tcp://127.0.0.1:{port}"
        answer = await remote(settings, url)(uploads(("a.png", make_png())), tmp_path / "job")
        assert answer.pages == 1
        process.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(process.wait(), 20) == 0
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    health_after = subprocess.run(  # noqa: S603  (our own interpreter)
        [sys.executable, "-m", "decimer_api.sidecar", "--ping"], env=env, check=False
    )
    assert health_after.returncode == 1  # nothing answers any more
    assert await emptied(tmp_path / "scratch") == []
