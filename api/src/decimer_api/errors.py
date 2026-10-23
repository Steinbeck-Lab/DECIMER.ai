"""One error shape for every rejection: `{"detail": {"code": ..., "message": ...}}`.

Kept free of web-framework imports: the ingest child process imports this module and must stay
small. The exception handlers that turn these into responses are in `handlers.py`.

`ErrorCode` lists every code the API can answer with. It is the type of `ErrorDetail.code`, so the
OpenAPI schema carries the list and the frontend's own list of codes is generated from it (it
fails to compile when the API gains one the UI has no sentence for).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel


class ErrorCode(StrEnum):
    # upload (POST /api/jobs)
    EMPTY_UPLOAD = "empty_upload"
    TOO_MANY_FILES = "too_many_files"
    UNSUPPORTED = "unsupported"
    MIXED_UPLOAD = "mixed_upload"
    MULTIPLE_PDFS = "multiple_pdfs"
    CORRUPT_FILE = "corrupt_file"
    ENCRYPTED_PDF = "encrypted_pdf"
    TOO_MANY_PIXELS = "too_many_pixels"
    PROCESSING_LIMIT = "processing_limit"
    # any request
    TOO_LARGE = "too_large"
    INVALID_REQUEST = "invalid_request"
    RATE_LIMITED = "rate_limited"
    QUEUE_FULL = "queue_full"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    # export of a job that has nothing to export
    NO_RESULTS = "no_results"
    # the framework's own answers, and a bug
    METHOD_NOT_ALLOWED = "method_not_allowed"
    HTTP_ERROR = "http_error"
    INTERNAL_ERROR = "internal_error"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class ApiError(Exception):
    """`code` is an `ErrorCode` or its string; any other string is a bug and fails here, where
    the error is raised, not later when the answer is built."""

    def __init__(
        self,
        status: int,
        code: ErrorCode | str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = ErrorCode(code)
        self.message = message
        self.headers = dict(headers or {})


UNAVAILABLE_MESSAGE = "The service is temporarily unavailable. Please retry."


def not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "Not found. The job may have expired.")


def unavailable() -> ApiError:
    return ApiError(503, ErrorCode.UNAVAILABLE, UNAVAILABLE_MESSAGE)
