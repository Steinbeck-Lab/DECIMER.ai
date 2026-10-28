"""The endpoints that work on the results of a finished job.

    POST /api/jobs/{id}/results/{n}/validate   an edited molfile in; SMILES, InChIKey, SVG, name out
    POST /api/jobs/{id}/export                 the job's structures as a ZIP, an SDF or a CSV
    POST /api/jobs/{id}/report                 save a problem report for one result
    GET  /api/engines                          the versions of what produced the results

The first three are `@guarded`: a rate limit per client and a bound on the body, both before the
body is read (`guards.py`). Everything a client sends is checked against the job's own record: the
result index must exist, a molfile must pass `clean_molfile` and be small, and no name, path or
order comes from the request.

Nothing here fails because of the chem service. A name is a bonus (`names.py`): without one the
answer says "no name", it never says "error".
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any, BinaryIO

from fastapi import APIRouter, Request, Response
from fastapi import Path as PathParam
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from .capacity import Place, Share
from .engines import engines
from .errors import ApiError, ErrorCode, ErrorResponse, not_found
from .exports import (
    Entry,
    StructureError,
    build_rows,
    csv_text,
    sdf_text,
    zip_entries,
    zip_stream,
)
from .guards import Guard, GuardedRoute, guarded
from .media import resolve_media
from .molfile import MolfileError, clean_molfile
from .names import NO_NAME, nameable
from .reports import ReportsFull, save_report
from .result_models import (
    EnginesView,
    EngineView,
    ExportFormat,
    ExportRequest,
    ReportRequest,
    ValidateRequest,
    ValidateView,
)
from .routes import client_key, find_job
from .settings import Settings

log = logging.getLogger("decimer_api")

router = APIRouter(prefix="/api", route_class=GuardedRoute)

# Matches nginx's `client_max_body_size 1m` for everything but the upload (deploy/nginx/edge.conf).
EXPORT_BODY_BYTES = 1024 * 1024
REPORT_BODY_BYTES = 4 * 1024
# A result index as a JSON key: decimal digits, no sign, no padding, no whitespace.
INDEX_KEY = re.compile(r"0|[1-9][0-9]{0,5}")

# Ties the limits to the settings; the bodies are JSON, so an escaped molfile can be twice its size.
VALIDATE = Guard(
    "validate",
    rate=lambda s: (s.validate_rate_per_minute, 60),
    body=lambda s: 2 * s.max_molfile_bytes + 1024,
)
EXPORT = Guard(
    "export", rate=lambda s: (s.export_rate_per_minute, 60), body=lambda _s: EXPORT_BODY_BYTES
)
REPORT = Guard(
    "report", rate=lambda s: (s.report_rate_per_hour, 3600), body=lambda _s: REPORT_BODY_BYTES
)

NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MEDIA_TYPES = {
    ExportFormat.ZIP: "application/zip",
    ExportFormat.SDF: "chemical/x-mdl-sdfile",
    ExportFormat.CSV: "text/csv; charset=utf-8",
}


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    texts = {
        404: "Unknown or expired job, or no such result",
        409: "The job has no structures to export",
        413: "The request is too large",
        422: "The request is not valid, or a molfile cannot be read",
        429: "Too many requests from this client. See Retry-After",
        503: "The service is unavailable. See Retry-After",
    }
    return {status: {"model": ErrorResponse, "description": texts[status]} for status in statuses}


def _unusable(error: Exception) -> ApiError:
    return ApiError(422, ErrorCode.INVALID_REQUEST, f"The molfile cannot be used: {error}.")


def client_molfile(text: str, settings: Settings) -> str:
    """A molfile from the client, checked: not over `MAX_MOLFILE_BYTES` (413), and one that
    `clean_molfile` accepts (422). Returns the cleaned text."""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError as error:  # a lone surrogate, which JSON lets through
        raise ApiError(422, ErrorCode.INVALID_REQUEST, "The molfile is not valid text.") from error
    if size > settings.max_molfile_bytes:
        raise ApiError(
            413,
            ErrorCode.TOO_LARGE,
            f"The molfile is too large. The limit is {settings.max_molfile_bytes // 1024} KB.",
        )
    try:
        return clean_molfile(text)
    except MolfileError as error:
        raise _unusable(error) from error


# --- validate ------------------------------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/results/{n}/validate",
    response_model=ValidateView,
    responses=_errors(404, 413, 422, 429, 503),
    summary="Recompute SMILES, InChIKey, drawing and IUPAC name for an edited molfile",
)
@guarded(VALIDATE)
async def validate(
    job_id: str,
    n: Annotated[int, PathParam(ge=0, description="0-based index of the result.")],
    body: ValidateRequest,
    request: Request,
    response: Response,
) -> ValidateView:
    """The structure the user drew in place of result `n`. The values are computed as the worker
    computes them for a recognised structure. The name is null when chem is off, slow, or
    declines. A molfile that is a molfile but not a valid structure answers `valid: false`."""
    deps = request.app.state.deps
    job = await find_job(request, job_id)
    if n >= len(job.results):
        raise not_found()
    molfile = client_molfile(body.molfile, deps.settings)
    try:
        chem = await deps.analysis.analyse(molfile)
    except MolfileError as error:
        raise _unusable(error) from error
    # No CDK fallback here: CDK reads SMILES, and DECIMER's SMILES are what the worker
    # tries with it (`Result.valid_by`). This molfile came from the editor, which wrote its own
    # (also when the user opened a structure that only CDK had accepted); if RDKit refuses it, it
    # is invalid, and the user sees that and fixes the drawing.
    name = NO_NAME
    if chem.valid and nameable(chem.smiles):
        name = (await deps.namer([chem.smiles]))[0]
    response.headers["Cache-Control"] = "no-store"
    return ValidateView(
        smiles=chem.smiles,
        inchikey=chem.inchikey,
        svg=chem.svg,
        valid=chem.valid,
        name=name.name,
        name_tier=name.tier,
        name_verified=name.verified,
    )


# --- export --------------------------------------------------------------------------------------


def _edits(molfiles: dict[str, str], count: int, settings: Settings) -> dict[int, str]:
    """The client's molfiles by result index: every key a result of this job, every molfile
    clean and small. Anything else refuses the whole export (it is a client bug or an attack,
    and an export that silently drops an edit would be wrong)."""
    edits: dict[int, str] = {}
    for key, text in molfiles.items():
        if not INDEX_KEY.fullmatch(key) or int(key) >= count:
            raise ApiError(
                422, ErrorCode.INVALID_REQUEST, f"{key[:20]!r} is not a result of this job."
            )
        edits[int(key)] = client_molfile(text, settings)
    return edits


def _open_crops(data_dir: Path, job_id: str, indexes: list[int]) -> list[BinaryIO | None] | None:
    """The crop images, open, in order; None for a result that has no crop file (an image that
    could not be read has none: the archive goes without that image, as does a symlink,
    which is never followed). None for the whole list, and nothing left open, when a file that
    is there cannot be opened. They are opened before the first byte is sent, so a job that
    expires or is deleted during the download cannot cut the archive short: an open file stays
    readable."""
    opened: list[BinaryIO | None] = []
    try:
        for index in indexes:
            path = resolve_media(data_dir, job_id, f"crop-{index}.png")
            opened.append(None if path is None else path.open("rb"))
    except OSError:
        for file in opened:
            if file is not None:
                file.close()
        return None
    return opened


class DownloadResponse(StreamingResponse):
    """A streamed ZIP that gives its places back and closes its files when the response ends,
    however it ends: finished, the client gone (Starlette cancels the stream), a failed send, or
    `timeout` seconds after it started, when a client that reads too slowly (or not at all) is cut
    off: the stream is aborted without its last piece, so the client sees an incomplete download,
    and the places and the files come back. A background task would not run after an error, and
    a suspended generator is only closed when it is garbage collected, which is not a promise
    about when."""

    def __init__(
        self,
        content: Iterator[bytes],
        entries: list[Entry],
        places: list[Place | Share],
        timeout: float,
        **kwargs: Any,
    ) -> None:
        super().__init__(content, **kwargs)
        self._entries = entries
        self._places = places
        self._timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            try:
                async with asyncio.timeout(self._timeout):
                    await super().__call__(scope, receive, send)
            except TimeoutError:
                log.warning(
                    "a download was cut off after %g s (the client reads too slowly)",
                    self._timeout,
                )
        finally:
            for entry in self._entries:
                entry.close()
            for place in self._places:
                place.release()


@router.post(
    "/jobs/{job_id}/export",
    response_class=Response,
    responses={
        200: {
            "description": "The file. A ZIP has each structure's crop image and `.mol` file, and "
            "`results.smiles.txt` (image, SMILES, IUPAC name), in result order.",
            "content": {media: {} for media in MEDIA_TYPES.values()},
        },
        **_errors(404, 409, 413, 422, 429, 503),
    },
    summary="Download a job's structures as a ZIP, an SDF or a CSV, with the user's edits",
)
@guarded(EXPORT)
async def export_job(job_id: str, body: ExportRequest, request: Request) -> Response:
    deps = request.app.state.deps
    settings: Settings = deps.settings
    job = await find_job(request, job_id)
    if not job.results:
        raise ApiError(409, ErrorCode.NO_RESULTS, "This job has no structures to export.")
    edits = _edits(body.molfiles, len(job.results), settings)
    try:
        rows = await build_rows(job.results, edits, deps.namer, deps.analysis)
    except StructureError as error:
        raise _unusable(error) from error

    extension = body.format.value
    headers = {
        **NO_STORE,
        "Content-Disposition": f'attachment; filename="decimer-results-{job_id[:8]}.{extension}"',
    }
    media_type = MEDIA_TYPES[body.format]
    if body.format is ExportFormat.SDF:
        return Response(
            sdf_text(rows).encode("utf-8", "replace"), media_type=media_type, headers=headers
        )
    if body.format is ExportFormat.CSV:
        return Response(
            csv_text(rows).encode("utf-8", "replace"), media_type=media_type, headers=headers
        )

    # A download holds a file open per crop for as long as the client takes to read: so many per
    # client (429 beyond), so many in all (503 busy beyond), and a time limit, so that slow readers
    # cannot use up the process's file handles, nor one address all the places.
    share = deps.client_streams.take(client_key(request))
    places: list[Place | Share] = [share]
    try:
        places.append(deps.streams.take())
        crops = await asyncio.to_thread(
            _open_crops, settings.data_dir, job_id, [row.index for row in rows]
        )
        if crops is None:
            raise not_found()
    except BaseException:
        for place in places:
            place.release()
        raise
    entries = zip_entries(rows, crops)
    return DownloadResponse(
        zip_stream(entries, deps.store.now()),
        entries,
        places,
        settings.download_timeout_seconds,
        media_type=media_type,
        headers=headers,
    )


# --- report --------------------------------------------------------------------------------------


@router.post(
    "/jobs/{job_id}/report",
    status_code=204,
    response_class=Response,
    responses=_errors(404, 413, 422, 429, 503),
    summary="Report a problem with one result",
)
@guarded(REPORT)
async def report_problem(job_id: str, body: ReportRequest, request: Request) -> Response:
    """Keeps the result and its crop image on the private reports volume until someone has looked
    at them. Only the index comes from the client. A result reported twice is kept once."""
    deps = request.app.state.deps
    settings: Settings = deps.settings
    job = await find_job(request, job_id)
    if body.index >= len(job.results):
        raise not_found()
    result = job.results[body.index]
    crop = await asyncio.to_thread(
        resolve_media, settings.data_dir, job_id, f"crop-{result.index}.png"
    )
    try:
        await asyncio.to_thread(
            save_report,
            settings.reports_dir,
            job,
            result,
            crop,
            now=deps.store.now(),
            max_reports=settings.max_reports,
            max_image_bytes=settings.max_report_image_bytes,
            max_bytes=settings.reports_max_bytes,
        )
    except ReportsFull as error:
        log.error("REPORTS_DIR is full (%s): no more reports are taken", error.what)
        raise ApiError(
            503, ErrorCode.UNAVAILABLE, "Reports cannot be saved right now. Please try later."
        ) from error
    except OSError as error:
        log.exception("could not save a report")
        raise ApiError(
            503, ErrorCode.UNAVAILABLE, "Reports cannot be saved right now. Please try later."
        ) from error
    return Response(status_code=204)


# --- engines -------------------------------------------------------------------------------------


@router.get(
    "/engines",
    response_model=EnginesView,
    summary="The versions of the models and libraries behind the results",
)
async def get_engines(response: Response) -> EnginesView:
    response.headers["Cache-Control"] = "public, max-age=300"
    return EnginesView(
        engines=[
            EngineView(id=e.id, name=e.name, role=e.role, version=e.version, revision=e.revision)
            for e in engines()
        ]
    )
