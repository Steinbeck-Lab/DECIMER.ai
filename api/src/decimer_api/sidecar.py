"""The ingest sidecar: `python -m decimer_api.sidecar`.

The api used to parse every upload (pdfium, libheif, Pillow: the most exploitable code in the stack)
and read every client molfile (RDKit) in child processes of its own container. Those children run as
the api's user: a compromised one could read and write all of `DATA_DIR`, reach Redis on the
`backend` network, and reach chem and the edge with the api's identity. This process runs the same
children in a container of their own, which has

* **no volume with job data**: a request's bytes arrive on the connection, are kept in a private
  scratch directory (`SIDECAR_SCRATCH_DIR`, its own volume) for as long as the request takes, and
  the normalised result goes back on the same connection. The api writes it into
  `DATA_DIR/<job>/input/` itself;
* **no route to Redis, chem, the edge or the internet**: its one network, `ingest`, holds the api
  and nothing else;
* **no secret in its environment** (compose blanks `REDIS_PASSWORD` and `IP_HASH_SECRET`, and it has
  no `SENTRY_DSN`), and it is made non-dumpable like the api.

What a compromised child can still reach: the other upload being parsed at the same moment (at most
`INGEST_CONCURRENCY`, same user, same scratch volume) and the molfiles being read by the analysis
children. That is the design's price: one upload, not all of `DATA_DIR`.

Two operations, both over `sidecar_wire` frames (see there): `ingest` (the same `SubprocessIngest`
the api used in-process: a child per upload with a deadline and hard limits) and `analysis` (the
same `AnalysisPool` of long-lived RDKit children, one place per connection). The limits come from
the same settings as the api's (compose gives both the same `.env`), so the two cannot drift apart.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import re
import shutil
import signal
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import sidecar_wire as wire
from .analysis_pool import Analysis, AnalysisPool
from .capacity import Capacity
from .errors import ApiError, ErrorCode
from .hardening import make_undumpable
from .ingest_runner import IngestRunner, SubprocessIngest
from .logs import configure_logging
from .molfile import MolfileError, TooComplex
from .settings import Settings
from .uploads import Upload

log = logging.getLogger("decimer_api.sidecar")

HEADER_TIMEOUT_SECONDS = 10.0
RECEIVE_TIMEOUT_SECONDS = 60.0
# Between two molfiles of one analysis session the api may be busy with other work (an export reads
# its edits one after the other); longer than that and the session is over.
ANALYSIS_IDLE_SECONDS = 60.0
DISCARD_CHUNK = 1024 * 1024
MAX_NAME_CHARS = 255
OUTPUT_NAME = re.compile(r"document\.pdf|\d{4}\.png")
SHUTDOWN_WAIT_SECONDS = 10.0


def error_frame(error: ApiError) -> dict[str, Any]:
    return {
        "error": {
            "status": error.status,
            "code": str(error.code),
            "message": error.message,
            "headers": error.headers,
        }
    }


class Sidecar:
    def __init__(
        self,
        settings: Settings,
        *,
        scratch: Path,
        ingest: IngestRunner | None = None,
        analysis: Analysis | None = None,
    ) -> None:
        self._settings = settings
        self._scratch = scratch
        self._ingest = ingest or SubprocessIngest(settings)
        self._analysis = analysis or AnalysisPool(settings)
        self._places = Capacity(settings.ingest_concurrency)
        # The api sends the bytes the client sent, plus multipart framing nobody sees: the same
        # bound the api applies to the request.
        self._max_total = settings.max_upload_bytes
        self._connections: set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        self._scratch.mkdir(parents=True, exist_ok=True)
        await self._analysis.start()  # the molfile-reading children, ready before the first request

    async def aclose(self) -> None:
        await self._analysis.aclose()

    async def listen(self, host: str, port: int) -> asyncio.Server:
        return await asyncio.start_server(self.handle, host, port, limit=2 * wire.CHUNK_BYTES)

    # --- one connection -------------------------------------------------------------------------

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        try:
            request = await asyncio.wait_for(wire.read_frame(reader), HEADER_TIMEOUT_SECONDS)
            operation = request.get("op")
            if operation == "ping":
                await wire.write_frame(writer, {"ok": True})
            elif operation == "ingest":
                await self._serve_ingest(request, reader, writer)
            elif operation == "analysis":
                await self._serve_analysis(reader, writer)
            else:
                await wire.write_frame(
                    writer, error_frame(ApiError(400, ErrorCode.INVALID_REQUEST, "unknown request"))
                )
        except (TimeoutError, wire.FrameError, asyncio.IncompleteReadError, ConnectionError):
            log.warning("a connection ended early or sent nonsense")
        except Exception:
            log.exception("a request failed")
        finally:
            if task is not None:
                self._connections.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def wait_for_connections(self) -> None:
        if self._connections:
            await asyncio.wait(self._connections, timeout=SHUTDOWN_WAIT_SECONDS)

    # --- ingest ---------------------------------------------------------------------------------

    def _announced(self, request: dict[str, Any]) -> list[tuple[str, int]]:
        """The files the api says it will send, checked: a bad announcement is a bug or worse."""
        files = request.get("files")
        bad = ApiError(400, ErrorCode.INVALID_REQUEST, "the announcement is not valid")
        if not isinstance(files, list) or not 1 <= len(files) <= self._settings.max_upload_files:
            raise bad
        announced: list[tuple[str, int]] = []
        for item in files:
            name, size = (item.get("name"), item.get("size")) if isinstance(item, dict) else (0, 0)
            if not isinstance(name, str) or type(size) is not int or size < 0:
                raise bad
            announced.append((name[:MAX_NAME_CHARS], size))
        if sum(size for _, size in announced) > self._max_total:
            raise bad
        return announced

    async def _serve_ingest(
        self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            announced = self._announced(request)
        except ApiError as error:
            await wire.write_frame(writer, error_frame(error))
            return
        try:
            place = self._places.take()
        except ApiError as busy:
            # The body is already on its way: read it and drop it before answering. (Closing a
            # connection with data unread resets it, and a reset can take the answer with it.)
            await self._discard(reader, sum(size for _, size in announced))
            await wire.write_frame(writer, error_frame(busy))
            return
        folder = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="req-", dir=self._scratch))
        try:
            await self._ingest_in(folder, announced, reader, writer)
        finally:
            place.release()
            await asyncio.to_thread(shutil.rmtree, folder, True)

    async def _ingest_in(
        self,
        folder: Path,
        announced: list[tuple[str, int]],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        handles = []
        try:
            uploads = []
            async with asyncio.timeout(RECEIVE_TIMEOUT_SECONDS):
                for number, (name, size) in enumerate(announced, start=1):
                    handle = open(folder / f"in-{number:04d}", "w+b")  # noqa: SIM115  (closed below)
                    handles.append(handle)
                    await wire.receive_file(reader, handle, size)
                    handle.seek(0)
                    uploads.append(Upload(name=name, data=handle))
            try:
                ingested = await self._ingest(uploads, folder / "job")
            except ApiError as error:
                await wire.write_frame(writer, error_frame(error))
                return
            outputs = self._outputs(folder / "job" / "input")
            await wire.write_frame(
                writer,
                {
                    "ok": {
                        "pages": ingested.pages,
                        "pages_processed": ingested.pages_processed,
                        "truncated": ingested.truncated,
                        "warnings": [
                            {"code": w.code, "message": w.message} for w in ingested.warnings
                        ],
                        "files": [{"name": path.name, "size": size} for path, size in outputs],
                    }
                },
            )
            for path, size in outputs:
                with open(path, "rb") as source:
                    await wire.send_file(writer, source, size)
        finally:
            for handle in handles:
                handle.close()

    @staticmethod
    def _outputs(input_dir: Path) -> list[tuple[Path, int]]:
        """The normalised files, regular files only (a child cannot send a symlink out)."""
        found = []
        for path in sorted(input_dir.iterdir()):
            if path.is_file() and not path.is_symlink() and OUTPUT_NAME.fullmatch(path.name):
                found.append((path, path.stat().st_size))
        return found

    @staticmethod
    async def _discard(reader: asyncio.StreamReader, size: int) -> None:
        with contextlib.suppress(TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            async with asyncio.timeout(RECEIVE_TIMEOUT_SECONDS):
                remaining = size
                while remaining > 0:
                    remaining -= len(await reader.readexactly(min(DISCARD_CHUNK, remaining)))

    # --- molfiles -------------------------------------------------------------------------------

    async def _serve_analysis(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            async with self._analysis.session() as session:
                await wire.write_frame(writer, {"ready": True})
                while True:
                    try:
                        async with asyncio.timeout(ANALYSIS_IDLE_SECONDS):
                            request = await wire.read_frame(reader)
                    except asyncio.IncompleteReadError:
                        return  # the api ended the session
                    await wire.write_frame(writer, await self._analyse(session, request))
        except ApiError as error:  # no place free, or no child could be started
            await wire.write_frame(writer, error_frame(error))

    async def _analyse(self, session: Any, request: dict[str, Any]) -> dict[str, Any]:
        molfile = request.get("molfile")
        if not isinstance(molfile, str) or len(molfile) > self._settings.max_molfile_bytes:
            return {"error": "molfile", "message": "it is not a molfile that may be read"}
        try:
            chemistry = await session.analyse(molfile)
        except TooComplex:
            return {"error": "too_complex"}
        except MolfileError as error:
            return {"error": "molfile", "message": str(error)[:200]}
        except ApiError as error:
            return error_frame(error)
        except Exception:
            log.exception("reading a molfile failed")
            return {"error": "internal"}
        return {"ok": asdict(chemistry)}


# --- the process -----------------------------------------------------------------------------


async def serve(settings: Settings, *, stop: asyncio.Event | None = None) -> None:
    sidecar = Sidecar(settings, scratch=settings.sidecar_scratch_dir)
    await sidecar.start()
    server = await sidecar.listen(settings.sidecar_host, settings.sidecar_port)
    stopping = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not on every platform
            loop.add_signal_handler(number, stopping.set)
    log.info("ingest sidecar listening on %s:%d", settings.sidecar_host, settings.sidecar_port)
    await stopping.wait()
    server.close()
    await sidecar.wait_for_connections()
    await sidecar.aclose()
    log.info("ingest sidecar stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m decimer_api.sidecar",
        description="The ingest sidecar: parses uploads and reads molfiles for the api.",
    )
    parser.add_argument(
        "--ping",
        action="store_true",
        help="exit 0 if a sidecar answers on SIDECAR_PORT (health check)",
    )
    arguments = parser.parse_args(argv)
    settings = Settings()
    if arguments.ping:
        return 0 if asyncio.run(wire.ping("127.0.0.1", settings.sidecar_port)) else 1
    configure_logging(fmt=settings.log_format, level=settings.log_level)
    # The children run as the same user: without this they could read this process's environment.
    make_undumpable()
    asyncio.run(serve(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
