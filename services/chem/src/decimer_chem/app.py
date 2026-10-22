"""The chem service's HTTP layer: /healthz, /readyz, POST /name and POST /names.

The naming itself happens in a separate process (see `supervisor`), which owns Orthonym and its
JVM. This module only validates requests, bounds how many wait, maps errors to status codes and
keeps the readiness check honest. It takes JSON, so it does not use the image-upload skeleton in
`decimer_service.create_app`; it follows the same conventions (a gate that answers 503 with
Retry-After instead of queueing forever, ValueError as 422, any other error as 500 with an
error_id, a readiness check that really runs the model).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .logs import log_failure
from .results import INVALID_SMILES_REASON, TIMEOUT_REASON, NameResult
from .settings import Settings
from .supervisor import EngineUnavailable, ProcessNamer

log = logging.getLogger("decimer_chem")

#: The selftest: Orthonym's README names caffeine exactly this, and only a working JVM, both jars
#: and the OPSIN round trip produce it.
CAFFEINE_SMILES = "Cn1cnc2c1c(=O)n(C)c(=O)n2C"
CAFFEINE_NAME = "1,3,7-trimethyl-3,7-dihydro-1H-purine-2,6-dione"

MAX_SMILES_CHARS = 2000
MAX_BATCH = 20

#: A SMILES is printable ASCII without spaces. RDKit would read anything after a space as the
#: molecule's name, and control characters have no business in one.
Smiles = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_SMILES_CHARS,
        pattern=r"^[\x21-\x7e]+$",
    ),
]


class NameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: Smiles


class NamesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    smiles: Annotated[list[Smiles], Field(min_length=1, max_length=MAX_BATCH)]


class NameBody(BaseModel):
    name: str | None
    tier: str | None
    verified: str | None
    reason: str | None


class NamesBody(BaseModel):
    results: list[NameBody]


class Namer(Protocol):
    """What the app needs from the naming machinery (the supervisor, or a fake in tests)."""

    @property
    def busy(self) -> bool:
        """True while a naming call is in flight."""

    async def start(self) -> None:
        """Make the engine ready (JVM up, jars checked); raise if it cannot be."""

    async def stop(self) -> None: ...

    async def name_many(self, smiles: Sequence[str]) -> list[NameResult]:
        """One result per SMILES, in order. Everything that goes wrong with one structure (an
        unreadable SMILES, a timeout, an engine error) is that structure's result with a reason.
        Raise EngineUnavailable when no structure at all could be tried."""


@dataclass(frozen=True)
class Limits:
    #: One naming process, one structure at a time. A request holds the slot for its whole batch.
    max_concurrency: int = 1
    #: The worker sends one /names batch per job and runs at most two jobs at once, so one request
    #: waits at a time; the rest is headroom for single /name calls.
    max_waiting: int = 4
    #: A waiting request gives up after this long and is answered 503 with Retry-After (a batch
    #: ahead of it can run for CHEM_BATCH_TIMEOUT_S), so no request waits for ever.
    queue_timeout_s: float = 15.0
    max_body_bytes: int = 256 * 1024
    ready_cache_seconds: float = 30.0


LIMITS = Limits()


class _Gate:
    """A semaphore that refuses new work once `slots + waiting` callers are inside or waiting."""

    def __init__(self, slots: int, waiting: int) -> None:
        self.semaphore = asyncio.Semaphore(slots)
        self._capacity = slots + waiting
        self._inside = 0

    def try_enter(self) -> bool:
        if self._inside >= self._capacity:
            return False
        self._inside += 1
        return True

    def leave(self) -> None:
        self._inside -= 1


async def selftest(namer: Namer) -> None:
    """Name caffeine through the whole stack and require Orthonym's documented answer."""
    [result] = await namer.name_many([CAFFEINE_SMILES])
    if result.name != CAFFEINE_NAME or result.verified != "opsin":
        raise RuntimeError(
            f"selftest failed: caffeine should be named {CAFFEINE_NAME!r} (checked by opsin), "
            f"got name={result.name!r}, tier={result.tier!r}, verified={result.verified!r}, "
            f"reason={result.reason!r}"
        )


async def _read_json[M: BaseModel](request: Request, model: type[M], limit: int) -> M:
    """Read at most `limit` bytes of body, parse and validate it; 413 / 422 otherwise."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="request body too large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="request body too large")
        chunks.append(chunk)
    try:
        return model.model_validate_json(b"".join(chunks))
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        raise RequestValidationError([{**e, "loc": ("body", *e["loc"])} for e in errors]) from exc


def create_app(namer: Namer, limits: Limits | None = None) -> FastAPI:
    limits = limits or Limits()
    ready = {"at": 0.0}
    gate = _Gate(limits.max_concurrency, limits.max_waiting)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        start = time.monotonic()
        try:
            await namer.start()
            await selftest(namer)
        except BaseException:
            await namer.stop()
            raise
        ready["at"] = time.monotonic()
        log.info("chem ready", extra={"seconds": round(time.monotonic() - start, 2)})
        try:
            yield
        finally:
            await namer.stop()

    app = FastAPI(title="decimer-chem", lifespan=lifespan)
    app.state.namer = namer

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "chem"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        stale = time.monotonic() - ready["at"] > limits.ready_cache_seconds
        # A call in flight is the engine working (deadlines bound it), and a selftest would only
        # queue behind it, so a busy service reports ready instead of timing out its health check.
        if stale and not namer.busy:
            try:
                await selftest(namer)
            except Exception as exc:
                log.warning("selftest failed", extra={"error": str(exc)})
                raise HTTPException(status_code=503, detail=f"selftest failed: {exc}") from exc
            ready["at"] = time.monotonic()
        return {"status": "ready", "service": "chem"}

    def busy_response() -> JSONResponse:
        return JSONResponse({"detail": "busy"}, status_code=503, headers={"Retry-After": "5"})

    async def run(request: Request, smiles: list[str]) -> list[NameResult] | JSONResponse:
        if not gate.try_enter():
            return busy_response()
        try:
            try:
                await asyncio.wait_for(gate.semaphore.acquire(), limits.queue_timeout_s)
            except TimeoutError:
                return busy_response()
            try:
                if await request.is_disconnected():
                    # It waited for the slot and hung up meanwhile: nobody will read the answer.
                    return JSONResponse({"detail": "client disconnected"}, status_code=499)
                start = time.monotonic()
                try:
                    results = await namer.name_many(smiles)
                except EngineUnavailable:
                    log.error("naming engine unavailable")
                    return JSONResponse(
                        {"detail": "naming engine unavailable"},
                        status_code=503,
                        headers={"Retry-After": "5"},
                    )
                except Exception as exc:
                    error_id = uuid.uuid4().hex[:12]
                    log_failure(log, "naming failed", exc, error_id=error_id)
                    return JSONResponse(
                        {"detail": "naming failed", "error_id": error_id}, status_code=500
                    )
                if any(r.name is not None and r.verified == "opsin" for r in results):
                    # A name that OPSIN read back in full shows the engine works, as the selftest
                    # does. A decline, a timeout or a name checked another way does not: they
                    # would keep readiness green while the selftest fails.
                    ready["at"] = time.monotonic()
                log.info(
                    "named",
                    extra={
                        "structures": len(results),
                        "named": sum(r.name is not None for r in results),
                        "declined": sum(r.name is None for r in results),
                        "timeouts": sum(r.reason == TIMEOUT_REASON for r in results),
                        "ms": round((time.monotonic() - start) * 1000),
                    },
                )
                return results
            finally:
                gate.semaphore.release()
        finally:
            gate.leave()

    @app.post("/name", response_model=NameBody)
    async def name(request: Request) -> Any:
        body = await _read_json(request, NameRequest, limits.max_body_bytes)
        outcome = await run(request, [body.smiles])
        if isinstance(outcome, JSONResponse):
            return outcome
        if outcome[0].reason == INVALID_SMILES_REASON:
            # One structure that cannot be read is the caller's mistake. (In a batch it is that
            # entry's result instead, so the other names still come back.)
            raise RequestValidationError(
                [
                    {
                        "loc": ("body", "smiles"),
                        "msg": "not a valid SMILES",
                        "type": "value_error",
                    }
                ]
            )
        return outcome[0].as_dict()

    @app.post("/names", response_model=NamesBody)
    async def names(request: Request) -> Any:
        body = await _read_json(request, NamesRequest, limits.max_body_bytes)
        outcome = await run(request, body.smiles)
        if isinstance(outcome, JSONResponse):
            return outcome
        return {"results": [r.as_dict() for r in outcome]}

    return app


def build_app(namer: Namer | None = None, settings: Settings | None = None) -> FastAPI:
    """The app as `python -m decimer_chem` serves it."""
    return create_app(
        ProcessNamer(settings or Settings.from_env()) if namer is None else namer, LIMITS
    )
