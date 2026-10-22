"""Exception handlers: every failure the API can produce has the same JSON shape."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import ApiError, ErrorDetail, ErrorResponse
from .store import Contended

log = logging.getLogger("decimer_api")

UNAVAILABLE_MESSAGE = "The service is temporarily unavailable. Please retry."
HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def _response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(detail=ErrorDetail(code=code, message=message))
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


async def api_error_handler(_request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ApiError):  # registered for ApiError only
        raise error
    return _response(error.status, error.code, error.message, error.headers)


async def unavailable_handler(_request: Request, _error: Exception) -> JSONResponse:
    """Redis is down, slow or too contended: say so, never leak the exception."""
    return _response(503, "unavailable", UNAVAILABLE_MESSAGE, {"Retry-After": "5"})


async def http_exception_handler(_request: Request, error: Exception) -> JSONResponse:
    """Routing errors (unknown path, wrong method) and anything raised as HTTPException."""
    if not isinstance(error, StarletteHTTPException):
        raise error
    code = HTTP_CODES.get(error.status_code, "http_error")
    message = {404: "Not found.", 405: "Method not allowed."}.get(
        error.status_code, "Request failed."
    )
    return _response(error.status_code, code, message, dict(error.headers or {}))


async def validation_error_handler(_request: Request, _error: Exception) -> JSONResponse:
    return _response(422, "invalid_request", "The request is not valid.")


async def internal_error_handler(_request: Request, error: Exception) -> JSONResponse:
    log.error("unhandled error", exc_info=error)
    return _response(500, "internal_error", "Something went wrong on our side.")


def register(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RedisError, unavailable_handler)
    app.add_exception_handler(Contended, unavailable_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, internal_error_handler)
