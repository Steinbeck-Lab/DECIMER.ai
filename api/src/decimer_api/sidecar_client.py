"""The api's side of the ingest sidecar (TODO, `decimer_api.sidecar`): an `IngestRunner` and
an `Analysis` that send their work over a TCP connection instead of starting child processes.

What comes back is not trusted beyond what the api can check, because the sidecar is where untrusted
files are parsed and is the first container to be compromised if a parser has a hole:

* the files of an ingest must be exactly `document.pdf`, or `0001.png` to `NNNN.png` for N uploaded
  images; sizes are bounded per file and in all; they are written under names the api chose;
* page counts are whole numbers in the ranges the limits allow, and warnings have a short code of
  lower-case letters and a short message (the page shows them to the user);
* an error carries a status between 400 and 599 and a code from `ErrorCode`; anything else, a closed
  connection or silence past the deadline is `503 unavailable`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import sidecar_wire as wire
from .analysis_pool import TOO_COMPLEX, Session
from .capacity import Capacity
from .chemistry import Chemistry
from .errors import ApiError, ErrorCode, unavailable
from .models import JobWarning
from .molfile import MolfileError, TooComplex
from .settings import INGEST_MARGIN_SECONDS, MIB, Settings
from .uploads import FileKind, Ingested, Limits, Upload, classify

log = logging.getLogger("decimer_api.sidecar_client")

CONNECT_TIMEOUT_SECONDS = 3.0
# Round trip and framing around the sidecar's own deadlines, which come first.
MARGIN_SECONDS = 2.0
NAME_CHARS = 255
CODE = re.compile(r"[a-z0-9_]{1,64}")
MAX_WARNINGS = 8
MAX_WARNING_CHARS = 300


def _unavailable(why: str) -> ApiError:
    log.error("the ingest sidecar: %s", why)
    return unavailable()


def _endpoint(settings: Settings) -> tuple[str, int]:
    url = urlsplit(settings.ingest_sidecar_url or "")
    if not url.hostname or url.port is None:
        raise ValueError("INGEST_SIDECAR_URL is not set")
    return url.hostname, url.port


async def _connect(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        return await asyncio.wait_for(
            asyncio.open_connection(host, port, limit=2 * wire.CHUNK_BYTES), CONNECT_TIMEOUT_SECONDS
        )
    except (TimeoutError, OSError) as error:
        raise _unavailable(f"cannot connect ({type(error).__name__})") from error


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


def api_error(payload: Any) -> ApiError:
    """The `ApiError` an error frame describes; anything malformed is `503 unavailable`."""
    try:
        status = int(payload["status"])
        code = ErrorCode(str(payload["code"]))
        message = str(payload["message"])[:500]
        retry = str((payload.get("headers") or {}).get("Retry-After", ""))
        if not 400 <= status <= 599:
            raise ValueError("status")
    except (KeyError, TypeError, ValueError, AttributeError):
        return _unavailable("sent an error that is not an error")
    return ApiError(
        status, code, message, headers={"Retry-After": retry} if retry.isdigit() else {}
    )


class RemoteIngest:
    """`IngestRunner` that parses the uploads in the sidecar and writes the result under
    `job_dir/input/`, as `SubprocessIngest` does."""

    def __init__(self, settings: Settings) -> None:
        self._host, self._port = _endpoint(settings)
        self._limits = Limits.from_settings(settings)
        self._deadline = settings.ingest_timeout_seconds + INGEST_MARGIN_SECONDS
        # A normalised page is a PNG of at most the pixel bound; a PDF is the upload itself.
        self._max_file_bytes = max(settings.max_page_png_bytes, settings.max_upload_bytes)
        self._max_total_bytes = (
            max(settings.max_upload_bytes, 3 * settings.max_job_pixels) + 64 * MIB
        )

    async def ping(self) -> bool:
        return await wire.ping(self._host, self._port)

    async def __call__(self, uploads: list[Upload], job_dir: Path) -> Ingested:
        kinds = classify(uploads, self._limits)  # refuse garbage without a connection
        try:
            async with asyncio.timeout(self._deadline):
                return await self._exchange(uploads, kinds[0] is FileKind.PDF, job_dir / "input")
        except TimeoutError as error:
            raise _unavailable("did not answer in time") from error
        except (OSError, wire.FrameError, asyncio.IncompleteReadError) as error:
            raise _unavailable(f"the connection failed ({type(error).__name__})") from error

    async def _exchange(self, uploads: list[Upload], is_pdf: bool, input_dir: Path) -> Ingested:
        reader, writer = await _connect(self._host, self._port)
        try:
            sizes = [await asyncio.to_thread(_size, upload.data) for upload in uploads]
            announce = [
                {"name": upload.name[:NAME_CHARS], "size": size}
                for upload, size in zip(uploads, sizes, strict=True)
            ]
            await wire.write_frame(writer, {"op": "ingest", "files": announce})
            # If the sidecar answers before it has the whole body (it is busy and discards it), the
            # answer is already waiting: a reset while sending is not the end of the exchange.
            with contextlib.suppress(ConnectionError):
                for upload, size in zip(uploads, sizes, strict=True):
                    await wire.send_file(writer, upload.data, size)
            reply = await wire.read_frame(reader)
            if "error" in reply:
                raise api_error(reply["error"])
            return await self._receive(reply.get("ok"), len(uploads), is_pdf, reader, input_dir)
        finally:
            await _close(writer)

    async def _receive(
        self,
        ok: Any,
        uploaded: int,
        is_pdf: bool,
        reader: asyncio.StreamReader,
        input_dir: Path,
    ) -> Ingested:
        try:
            ingested, files = self._checked(ok, uploaded, is_pdf)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise _unavailable(f"sent an answer that does not fit ({error!r})") from error
        await asyncio.to_thread(input_dir.mkdir, parents=True, exist_ok=False)
        for name, size in files:
            with open(input_dir / name, "wb") as target:
                await wire.receive_file(reader, target, size)
        return ingested

    def _checked(
        self, ok: Any, uploaded: int, is_pdf: bool
    ) -> tuple[Ingested, list[tuple[str, int]]]:
        """The answer as the api will use it, or `ValueError` if it cannot be true."""
        pages, processed, truncated = ok["pages"], ok["pages_processed"], ok["truncated"]
        if type(pages) is not int or type(processed) is not int or type(truncated) is not bool:
            raise ValueError("counts")
        files = [(str(item["name"]), item["size"]) for item in ok["files"]]
        names = [name for name, _ in files]
        if is_pdf:
            if names != ["document.pdf"]:
                raise ValueError("file names")
            limit = min(pages, self._limits.max_pdf_pages)
            if not 1 <= processed <= limit or truncated != (processed < pages):
                raise ValueError("pages")
        elif names != [f"{n:04d}.png" for n in range(1, uploaded + 1)]:
            raise ValueError("file names")
        elif pages != uploaded or processed != uploaded or truncated:
            raise ValueError("pages")
        sizes = [size for _, size in files]
        if any(type(size) is not int or not 0 <= size <= self._max_file_bytes for size in sizes):
            raise ValueError("file sizes")
        if sum(sizes) > self._max_total_bytes:
            raise ValueError("total size")
        warnings = ok["warnings"]
        if not isinstance(warnings, list) or len(warnings) > MAX_WARNINGS:
            raise ValueError("warnings")
        kept = []
        for item in warnings:
            code, message = item["code"], item["message"]
            if (
                not isinstance(code, str)
                or CODE.fullmatch(code) is None
                or not isinstance(message, str)
            ):
                raise ValueError("warning")
            kept.append(JobWarning(code=code, message=message[:MAX_WARNING_CHARS]))
        return Ingested(pages, processed, truncated, kept), files


def _size(data: Any) -> int:
    data.seek(0, 2)
    size = int(data.tell())
    data.seek(0)
    return size


# What a structure may look like when it comes back from the sidecar. RDKit made it there, but the
# process that ran RDKit is the one that parses what clients send: what it says is checked like any
# other input, because the api puts the SVG into a page, the molfile into a download and the SMILES
# into both.
MAX_SMILES_CHARS = 64 * 1024
MAX_MOLFILE_CHARS = 1024 * 1024
MAX_SVG_CHARS = 4 * 1024 * 1024
CHEMISTRY_FIELDS = frozenset({"smiles", "inchikey", "valid", "molfile", "svg"})
INCHIKEY = re.compile(r"(?:[A-Z]{14}-[A-Z]{10}-[A-Z])?")
# What RDKit draws with: shapes and text, no script, no link, no style sheet, no embedded document.
SVG_TAGS = frozenset(
    {
        "svg",
        "path",
        "rect",
        "ellipse",
        "circle",
        "line",
        "polyline",
        "polygon",
        "g",
        "text",
        "tspan",
    }
)
SVG_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][\w:.-]*)")
SVG_FORBIDDEN = re.compile(
    r"<\s*[!?]|\bon[a-z]+\s*=|href|javascript:|url\s*\(|data:|&#", re.IGNORECASE
)
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def checked_chemistry(ok: Any) -> Chemistry:
    """The structure an `analysis` answer carries, or `ValueError` naming what is wrong with it."""
    if not isinstance(ok, dict) or set(ok) != CHEMISTRY_FIELDS:
        raise ValueError("fields")
    smiles, inchikey, valid = ok["smiles"], ok["inchikey"], ok["valid"]
    molfile, svg = ok["molfile"], ok["svg"]
    if not all(isinstance(value, str) for value in (smiles, inchikey, molfile, svg)):
        raise ValueError("types")
    if type(valid) is not bool:
        raise ValueError("valid")
    if len(smiles) > MAX_SMILES_CHARS or CONTROL.search(smiles) or "\n" in smiles:
        raise ValueError("smiles")
    if INCHIKEY.fullmatch(inchikey) is None:
        raise ValueError("inchikey")
    if len(molfile) > MAX_MOLFILE_CHARS or "\x00" in molfile or "$$$$" in molfile:
        raise ValueError("molfile")
    if molfile and not molfile.rstrip().endswith("M  END"):
        raise ValueError("molfile")
    if svg and not _drawing_only(svg):
        raise ValueError("svg")
    return Chemistry(smiles=smiles, inchikey=inchikey, valid=valid, molfile=molfile, svg=svg)


# The one comment RDKit writes. Any other `<!` (a comment, a CDATA section, a DOCTYPE) is refused: a
# comment is no place to look for what is not there, and a pattern that skipped comments could be
# made to skip a script by putting `<!--` inside an attribute.
RDKIT_COMMENT = "<!-- END OF HEADER -->"


def _drawing_only(svg: str) -> bool:
    """Whether `svg` is a drawing and nothing else: bounded, starting with `<svg` (the analysis
    drops RDKit's XML declaration), made of the tags RDKit draws with, and with no script, event
    handler, link, style sheet, entity or embedded document."""
    svg = svg.replace(RDKIT_COMMENT, "", 1)
    if len(svg) > MAX_SVG_CHARS or not svg.lstrip().startswith("<svg"):
        return False
    if SVG_FORBIDDEN.search(svg):
        return False
    return all(name in SVG_TAGS for _closing, name in SVG_TAG.findall(svg))


# --- molfiles --------------------------------------------------------------------------------


class RemoteAnalysis:
    """`Analysis` that reads molfiles in the sidecar's pool of RDKit children. The places are kept
    here too (`capacity`), so a request that finds none is refused as `503 busy` without a
    connection."""

    def __init__(self, settings: Settings) -> None:
        self._host, self._port = _endpoint(settings)
        self.capacity = Capacity(settings.max_concurrent_analyses)
        self._timeout = settings.analysis_timeout_seconds
        self._batch = settings.analysis_batch_seconds
        self._startup = settings.analysis_startup_seconds

    async def analyse(self, molfile: str) -> Chemistry:
        async with self.session() as session:
            return await session.analyse(molfile)

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[Session]:
        place = self.capacity.take()
        try:
            reader, writer = await self._open()
            try:
                yield _RemoteSession(self, reader, writer)
            finally:
                await _close(writer)
        finally:
            place.release()

    async def start(self) -> None:
        return None  # the children are the sidecar's business

    async def aclose(self) -> None:
        return None

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await _connect(self._host, self._port)
        try:
            async with asyncio.timeout(self._startup + MARGIN_SECONDS):
                await wire.write_frame(writer, {"op": "analysis"})
                reply = await wire.read_frame(reader)
        except (TimeoutError, OSError, wire.FrameError, asyncio.IncompleteReadError) as error:
            await _close(writer)
            raise _unavailable(f"did not take the session ({type(error).__name__})") from error
        if reply.get("ready") is not True:
            await _close(writer)
            raise api_error(reply.get("error"))
        return reader, writer


class _RemoteSession:
    def __init__(
        self, owner: RemoteAnalysis, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._owner = owner
        self._reader = reader
        self._writer: asyncio.StreamWriter | None = writer
        self._began: float | None = None

    async def analyse(self, molfile: str) -> Chemistry:
        owner = self._owner
        if self._writer is None:
            raise _unavailable("the session is over")
        now = asyncio.get_running_loop().time()
        self._began = now if self._began is None else self._began
        # One molfile gets ANALYSIS_TIMEOUT_SECONDS, all of an export's ANALYSIS_BATCH_SECONDS.
        budget = min(owner._timeout, self._began + owner._batch - now)  # noqa: SLF001
        if budget <= 0:
            raise TooComplex(TOO_COMPLEX)
        try:
            async with asyncio.timeout(budget + MARGIN_SECONDS):
                await wire.write_frame(self._writer, {"molfile": molfile})
                reply = await wire.read_frame(self._reader)
        except TimeoutError:
            self._writer = None  # an answer may still be coming: this connection is not used again
            raise TooComplex(TOO_COMPLEX) from None
        except (OSError, wire.FrameError, asyncio.IncompleteReadError) as error:
            self._writer = None
            raise _unavailable(f"the session broke ({type(error).__name__})") from error
        return self._chemistry(reply)

    @staticmethod
    def _chemistry(reply: dict[str, Any]) -> Chemistry:
        if "ok" in reply:
            try:
                return checked_chemistry(reply["ok"])
            except ValueError as error:
                raise _unavailable(f"sent a structure that does not fit ({error})") from None
        kind = reply.get("error")
        if kind == "molfile":
            raise MolfileError(str(reply.get("message", "it cannot be read as a molfile"))[:200])
        if isinstance(kind, dict):
            raise api_error(kind)
        raise TooComplex(TOO_COMPLEX)  # "too_complex", "internal", anything else
