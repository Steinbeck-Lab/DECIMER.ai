"""One error shape for every rejection: `{"detail": {"code": ..., "message": ...}}`.

Kept free of web-framework imports: the ingest child process imports this module and must stay
small. The exception handlers that turn these into responses are in `handlers.py`.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = dict(headers or {})


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found. The job may have expired.")
