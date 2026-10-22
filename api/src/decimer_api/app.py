"""The FastAPI application. `create_app(settings, store, enqueue)` takes everything it uses, so
tests inject fakes; `create_app_from_env()` wires the production pieces."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from . import handlers
from .hardening import make_undumpable
from .ingest_gate import IngestGate
from .ingest_runner import IngestRunner, SubprocessIngest
from .queue import ArqEnqueuer, Enqueue
from .routes import router
from .settings import Settings
from .store import JobStore
from .uploads import Limits

log = logging.getLogger("decimer_api")


@dataclass
class Deps:
    settings: Settings
    store: JobStore
    enqueue: Enqueue
    ingest: IngestRunner
    limits: Limits
    hash_client: Callable[[str], str]
    gate: IngestGate
    warned_peers: set[str] = field(default_factory=set)


def _client_hasher(secret: str) -> Callable[[str], str]:
    if not secret:
        log.warning(
            "IP_HASH_SECRET is not set: using a random key. Per-IP counters reset when this "
            "process restarts and are not shared between processes."
        )
        secret = secrets.token_hex(32)
    key = secret.encode()

    def hash_client(address_key: str) -> str:
        return hmac.new(key, address_key.encode(), hashlib.sha256).hexdigest()[:32]

    return hash_client


def create_app(
    settings: Settings,
    store: JobStore,
    enqueue: Enqueue,
    *,
    ingest_runner: IngestRunner | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if on_shutdown is not None:
            await on_shutdown()

    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("cannot create DATA_DIR %s; /readyz will report not ready", settings.data_dir)

    app = FastAPI(
        title="DECIMER.ai job API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        # /api/jobs/x/media/crop-0.png/ is a 404, not a redirect to the real file.
        redirect_slashes=False,
    )
    app.state.deps = Deps(
        settings=settings,
        store=store,
        enqueue=enqueue,
        ingest=ingest_runner or SubprocessIngest(settings),
        limits=Limits.from_settings(settings),
        hash_client=_client_hasher(settings.ip_hash_secret),
        gate=IngestGate(settings.ingest_concurrency),
    )
    handlers.register(app)
    app.include_router(router)

    @app.get("/healthz", summary="Liveness: the process answers")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/readyz",
        summary="Readiness: Redis answers and the data directory is writable",
        responses={503: {"description": "Not ready"}},
    )
    async def readyz() -> Response:
        ready = await store.ping() and _writable(settings)
        if ready:
            return JSONResponse({"status": "ready"})
        return JSONResponse({"status": "not ready"}, status_code=503)

    return app


def _writable(settings: Settings) -> bool:
    return settings.data_dir.is_dir() and os.access(settings.data_dir, os.W_OK | os.X_OK)


def create_app_from_env() -> FastAPI:
    """The production app: settings from the environment, real Redis, arq enqueue."""
    make_undumpable()  # before anything that parses an upload can run: see hardening.py
    settings = Settings()
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    enqueuer = ArqEnqueuer(settings.redis_url)

    async def shutdown() -> None:
        await enqueuer.aclose()
        await redis.aclose()

    return create_app(settings, JobStore(redis, settings), enqueuer, on_shutdown=shutdown)
