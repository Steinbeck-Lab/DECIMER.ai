"""What a request must pass before its body is read: a rate limit per client, then a size limit.

The validate, export and report endpoints take small JSON bodies. FastAPI reads a body in full
before it calls anything, so the limits have to sit in front of that: `GuardedRoute` runs them
for every endpoint decorated with `@guarded(...)`.

1. **Rate limit.** A fixed window per client (the same keyed hash of the address that the upload
   slots use, `routes.client_key`) and per kind of request, counted in Redis, so every api
   process and every restart shares it. Over the limit is 429 with `Retry-After` set to the end of
   the window. nginx has its own, coarser limit in front (`api_general`); this one knows what a
   request costs (validate asks the chem service, export builds an archive).
2. **Body size.** A declared `Content-Length` over the limit is 413 at once; a body without one,
   or one that lies, is cut off at the limit (413), as the upload is (`routes._limited`). Nothing
   is buffered beyond the limit. The body is read here and handed to FastAPI as a one-piece
   request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Request, Response
from fastapi.routing import APIRoute
from starlette.requests import ClientDisconnect
from starlette.types import Message

from .errors import ApiError, ErrorCode
from .routes import client_key
from .settings import Settings

log = logging.getLogger("decimer_api")

GUARD_ATTRIBUTE = "__decimer_guard__"
F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class Guard:
    kind: str  # the rate-limit bucket: one counter per client and kind
    # (requests, window in seconds) and the largest body in bytes, from the settings.
    rate: Callable[[Settings], tuple[int, int]]
    body: Callable[[Settings], int]


def guarded(guard: Guard) -> Callable[[F], F]:
    """Mark an endpoint function for `GuardedRoute`. Put it below the route decorator."""

    def mark(function: F) -> F:
        setattr(function, GUARD_ATTRIBUTE, guard)
        return function

    return mark


class RateLimiter:
    """Fixed-window counters in Redis. `store` is the job store: its Redis client and clock."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def check(self, kind: str, client: str, limit: int, window_seconds: int) -> None:
        """Count one request; raise 429 when the client has used up its window."""
        now = self._store.now().timestamp()
        window = int(now // window_seconds)
        key = f"decimer:rate:{kind}:{client}:{window}"
        async with self._store.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds + 1)
            used = int((await pipe.execute())[0])
        if used > limit:
            retry_after = max(1, int((window + 1) * window_seconds - now) + 1)
            raise ApiError(
                429,
                ErrorCode.RATE_LIMITED,
                "Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(retry_after)},
            )


def _too_large(limit: int) -> ApiError:
    return ApiError(
        413, ErrorCode.TOO_LARGE, f"The request is too large. The limit is {limit // 1024} KB."
    )


async def read_bounded(request: Request, limit: int) -> bytes:
    """The whole body, or 413 as soon as it passes `limit`. Done here, ahead of FastAPI, which
    turns any error raised while it reads a body into a 400 of its own."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise _too_large(limit)
    body = bytearray()
    try:
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                raise _too_large(limit)
    except ClientDisconnect as error:  # nobody is left to read the answer; do not log a failure
        raise ApiError(400, ErrorCode.INVALID_REQUEST, "The request was interrupted.") from error
    return bytes(body)


class GuardedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        guard: Guard | None = getattr(self.endpoint, GUARD_ATTRIBUTE, None)
        if guard is None:
            return handler

        async def guarded_handler(request: Request) -> Response:
            deps = request.app.state.deps
            settings: Settings = deps.settings
            limit, window = guard.rate(settings)
            await deps.limiter.check(guard.kind, client_key(request), limit, window)
            body = await read_bounded(request, guard.body(settings))
            sent = False

            async def replay() -> Message:
                nonlocal sent
                if sent:  # FastAPI reads the body once; after that the client is gone
                    return {"type": "http.disconnect"}
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}

            return await handler(Request(request.scope, replay))

        return guarded_handler
