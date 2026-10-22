"""HTTP skeleton shared by the seg, ocsr and cls model services.

Each service supplies a Model; this module gives it /healthz (process alive), /readyz (a tiny
real inference, cached briefly) and /infer, which runs at most `max_concurrency` inferences at
once, lets `max_waiting` more wait, and answers 503 beyond that instead of queueing forever.
/readyz only runs the selftest when no selftest or inference has succeeded within the last
`ready_cache_seconds`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

log = logging.getLogger("decimer_service")


class Model(Protocol):
    name: str

    def load(self) -> None: ...

    def selftest(self) -> None:
        """Run one tiny real inference; raise if the model cannot answer."""

    def infer(self, image: bytes, params: Mapping[str, str]) -> dict[str, Any]:
        """Raise ValueError for bad input (answered 422); anything else is a 500."""


@dataclass(frozen=True)
class Limits:
    max_concurrency: int = 1
    max_waiting: int = 8
    max_image_bytes: int = 20 * 1024 * 1024
    ready_cache_seconds: float = 30.0


class _Gate:
    """A semaphore that refuses new work once `capacity` callers are inside or waiting."""

    def __init__(self, slots: int, waiting: int) -> None:
        self._sem = asyncio.Semaphore(slots)
        self._capacity = slots + waiting
        self._inside = 0

    def try_enter(self) -> bool:
        if self._inside >= self._capacity:
            return False
        self._inside += 1
        return True

    def leave(self) -> None:
        self._inside -= 1

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._sem


def create_app(model: Model, limits: Limits | None = None) -> FastAPI:
    limits = limits or Limits()
    ready = {"at": 0.0}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        start = time.monotonic()
        await run_in_threadpool(model.load)
        log.info("model loaded", extra={"model": model.name, "seconds": time.monotonic() - start})
        yield

    app = FastAPI(title=f"decimer-{model.name}", lifespan=lifespan)
    gate = _Gate(limits.max_concurrency, limits.max_waiting)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "model": model.name}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if time.monotonic() - ready["at"] > limits.ready_cache_seconds:
            try:
                await run_in_threadpool(model.selftest)
            except Exception as exc:
                log.warning("selftest failed", extra={"model": model.name, "error": str(exc)})
                raise HTTPException(status_code=503, detail=f"selftest failed: {exc}") from exc
            ready["at"] = time.monotonic()
        return {"status": "ready", "model": model.name}

    @app.post("/infer")
    async def infer(request: Request, image: UploadFile) -> Any:
        data = await image.read(limits.max_image_bytes + 1)
        if len(data) > limits.max_image_bytes:
            raise HTTPException(status_code=413, detail="image too large")
        form = await request.form()
        params = {k: v for k, v in form.items() if k != "image" and isinstance(v, str)}
        if not gate.try_enter():
            return JSONResponse({"detail": "busy"}, status_code=503, headers={"Retry-After": "5"})
        try:
            async with gate.semaphore:
                start = time.monotonic()
                try:
                    result = await run_in_threadpool(model.infer, data, params)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                except Exception:
                    error_id = uuid.uuid4().hex[:12]
                    log.exception(
                        "inference failed", extra={"model": model.name, "error_id": error_id}
                    )
                    return JSONResponse(
                        {"detail": "inference failed", "error_id": error_id}, status_code=500
                    )
                # A successful inference proves the model works as well as the selftest does.
                ready["at"] = time.monotonic()
                log.info(
                    "inferred",
                    extra={"model": model.name, "ms": round((time.monotonic() - start) * 1000)},
                )
                return result
        finally:
            gate.leave()

    return app
