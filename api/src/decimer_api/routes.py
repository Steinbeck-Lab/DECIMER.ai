"""The job endpoints: POST /api/jobs, GET /api/jobs/{id}, GET /api/jobs/{id}/media/{name}."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shutil
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from .clientip import fairness_key, real_client_ip
from .errors import ApiError, ErrorResponse, not_found
from .ingest_gate import IngestBusy
from .media import resolve_media
from .models import Job, JobStatus, JobView, is_valid_job_id, new_job_id
from .settings import Settings
from .store import Admission, Reservation
from .uploads import Upload, classify

log = logging.getLogger("decimer_api")

router = APIRouter(prefix="/api")

# Multipart framing (boundaries, headers) on top of the files themselves.
FRAMING_SLACK_BYTES = 64 * 1024
FILE_FIELD = "file"
MAX_FORM_FIELDS = 2  # the client sends files only; a couple of stray fields are tolerated
IMAGE_PNG = "image/png"
# Peers already reported for sending X-Forwarded-For without being trusted (bounded).
WARNED_PEERS_MAX = 32

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse, "description": text}
    for status, text in {
        400: "Mixed PDF and images, several PDFs, a corrupt or encrypted file, or no file",
        413: "The upload, an image or a PDF page is too large",
        415: "A file is not a PDF, PNG, JPEG, WebP or HEIC/HEIF image",
        429: "The client already has the maximum number of active jobs",
        503: "The queue is full, or the service is unavailable. See Retry-After",
    }.items()
}

CREATE_JOB_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "array",
                            "minItems": 1,
                            "items": {"type": "string", "format": "binary"},
                            "description": "One PDF, or one or more PNG, JPEG, WebP or HEIC "
                            "images. A pasted image is one part.",
                        }
                    },
                }
            }
        },
    }
}


def _admission_error(verdict: Admission, settings: Settings) -> ApiError:
    if verdict is Admission.RATE_LIMITED:
        return ApiError(
            429,
            "rate_limited",
            f"You already have {settings.max_active_jobs_per_ip} jobs in progress. "
            "Please wait for one to finish.",
        )
    return ApiError(
        503,
        "queue_full",
        "The service is busy. Please try again in a few minutes.",
        headers={"Retry-After": str(settings.queue_retry_after_seconds)},
    )


async def _limited(stream: AsyncGenerator[bytes, None], limit: int) -> AsyncGenerator[bytes, None]:
    """The request body, cut off with 413 once it passes `limit`; needs no Content-Length."""
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > limit:
            raise ApiError(413, "too_large", _too_large_message(limit))
        yield chunk


def _too_large_message(limit: int) -> str:
    return f"The upload is too large. The limit is {limit // (1024 * 1024)} MB in total."


async def _read_form(request: Request, settings: Settings) -> FormData:
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(415, "unsupported", "Send the files as multipart/form-data.")
    limit = settings.max_upload_bytes + FRAMING_SLACK_BYTES
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "too_large", _too_large_message(settings.max_upload_bytes))
    parser = MultiPartParser(
        request.headers,
        _limited(request.stream(), limit),
        max_files=settings.max_upload_files,
        max_fields=MAX_FORM_FIELDS,
    )
    try:
        return await parser.parse()
    except MultiPartException as error:
        if error.message.startswith("Too many files"):
            raise ApiError(
                400,
                "too_many_files",
                f"At most {settings.max_upload_files} files can be uploaded at once.",
            ) from error
        raise ApiError(400, "invalid_request", "The upload could not be read.") from error


def _client_hash(request: Request) -> str:
    deps = request.app.state.deps
    peer = request.client.host if request.client else None
    forwarded = request.headers.getlist("x-forwarded-for")
    address = real_client_ip(peer, forwarded, deps.settings.trusted_proxy_cidr)
    if forwarded and peer is not None and address == fairness_key(peer):
        _warn_if_proxy_is_not_trusted(deps, peer)
    return str(deps.hash_client(fairness_key(address)))


def _warn_if_proxy_is_not_trusted(deps: Any, peer: str) -> None:
    """A peer on a private or otherwise non-global address sending X-Forwarded-For is almost
    certainly our proxy, missing from TRUSTED_PROXY_CIDR: every client behind it is then counted
    as the proxy. Said once per peer."""
    try:
        internal = not ipaddress.ip_address(peer).is_global
    except ValueError:
        return
    if internal and peer not in deps.warned_peers and len(deps.warned_peers) < WARNED_PEERS_MAX:
        deps.warned_peers.add(peer)
        log.warning(
            "%s sent X-Forwarded-For but is not in TRUSTED_PROXY_CIDR, so the header is ignored "
            "and all its clients share one limit. If %s is your reverse proxy, add it.",
            peer,
            peer,
        )


@router.post(
    "/jobs",
    status_code=202,
    response_model=JobView,
    responses=ERROR_RESPONSES,
    openapi_extra=CREATE_JOB_BODY,
    summary="Create a job from an upload or a pasted image",
)
async def create_job(request: Request, response: Response) -> JobView:
    deps = request.app.state.deps
    settings: Settings = deps.settings
    client_hash = _client_hash(request)

    # Take the client's slot first, before a byte of the body is read: a client over its limit
    # costs one Redis round trip, however many uploads it starts at once.
    reservation = await deps.store.reserve(client_hash)
    if not isinstance(reservation, Reservation):
        raise _admission_error(reservation, settings)

    job_dir = settings.data_dir / new_job_id()
    form: FormData | None = None
    job: Job | None = None
    enqueued = False
    try:
        form = await _read_form(request, settings)
        parts = [
            value
            for key, value in form.multi_items()
            if key == FILE_FIELD and isinstance(value, UploadFile)
        ]
        if sum(part.size or 0 for part in parts) > settings.max_upload_bytes:
            raise ApiError(413, "too_large", _too_large_message(settings.max_upload_bytes))
        uploads = [Upload(name=part.filename or "", data=part.file) for part in parts]
        classify(uploads, deps.limits)  # cheap refusals first, before waiting for a slot

        try:
            async with deps.gate.slot(client_hash, settings.ingest_wait_seconds):
                ingested = await deps.ingest(uploads, job_dir)
        except IngestBusy as error:
            raise ApiError(
                503,
                "busy",
                "The service is busy processing other uploads. Please try again in a moment.",
                headers={"Retry-After": str(settings.queue_retry_after_seconds)},
            ) from error

        job = Job.create(
            job_id=job_dir.name,
            client_ip_hash=client_hash,
            pages=ingested.pages,
            pages_processed=ingested.pages_processed,
            truncated=ingested.truncated,
            warnings=ingested.warnings,
            now=deps.store.now(),
        )
        verdict = await deps.store.admit(job, reservation)
        if verdict is not Admission.OK:
            raise _admission_error(verdict, settings)
        try:
            await deps.enqueue(job.id)
        except Exception as error:
            log.exception("could not enqueue job %s", job.id)
            raise ApiError(
                503, "unavailable", "The service is temporarily unavailable. Please retry."
            ) from error
        enqueued = True
    except BaseException:
        if not enqueued:
            # Everything this request took is given back, even if the request is cancelled.
            await asyncio.shield(_give_back(deps.store, reservation, job, job_dir))
        raise
    finally:
        if form is not None:
            await form.close()

    try:
        position = await deps.store.queue_position(job.id)
    except Exception:
        position = None
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return JobView.from_job(job, position)


async def _give_back(store: Any, reservation: Reservation, job: Job | None, job_dir: Any) -> None:
    await asyncio.to_thread(shutil.rmtree, job_dir, True)
    if job is not None:
        await store.discard(job)
    await store.release(reservation)


@router.get(
    "/jobs/{job_id}",
    response_model=JobView,
    responses={404: {"model": ErrorResponse, "description": "Unknown or expired job"}},
    summary="Status, progress and results of a job",
)
async def get_job(job_id: str, request: Request, response: Response) -> JobView:
    store = request.app.state.deps.store
    if not is_valid_job_id(job_id):
        raise not_found()
    job = await store.get(job_id)
    if job is None:
        raise not_found()
    position = await store.queue_position(job_id) if job.status is JobStatus.QUEUED else None
    response.headers["Cache-Control"] = "no-store"
    return JobView.from_job(job, position)


@router.get(
    "/jobs/{job_id}/media/{name}",
    response_class=Response,
    responses={
        200: {"content": {IMAGE_PNG: {}}, "description": "The image (crop-N.png or page-N.png)"},
        404: {"model": ErrorResponse, "description": "Unknown job or file"},
    },
    summary="A crop or page image of a job",
)
async def get_media(job_id: str, name: str, request: Request) -> Response:
    deps = request.app.state.deps
    settings: Settings = deps.settings
    if not is_valid_job_id(job_id) or not await deps.store.is_live(job_id):
        raise not_found()
    path = await asyncio.to_thread(resolve_media, settings.data_dir, job_id, name)
    if path is None:
        raise not_found()
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if settings.media_stream_direct:
        return FileResponse(path, media_type=IMAGE_PNG, headers=headers)
    # nginx replaces this response with the file; the body here stays empty.
    headers["X-Accel-Redirect"] = f"{settings.media_accel_prefix}/{job_id}/{name}"
    return Response(content=b"", media_type=IMAGE_PNG, headers=headers)
