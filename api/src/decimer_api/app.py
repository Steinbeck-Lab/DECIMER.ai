"""The FastAPI application. `create_app(settings, store, enqueue)` takes everything it uses, so
tests inject fakes; `create_app_from_env()` wires the production pieces."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from . import handlers
from .analysis_pool import Analysis, build_analysis
from .capacity import Capacity, ClientShares
from .guards import RateLimiter
from .hardening import make_undumpable
from .ingest_gate import IngestGate
from .ingest_runner import IngestRunner, SubprocessIngest
from .names import Namer, build_namer
from .observability import close_error_reporting, init_error_reporting
from .queue import ArqEnqueuer, Enqueue
from .reports import TIDY_INTERVAL_SECONDS, tidy, tidy_every
from .result_routes import router as result_router
from .routes import router
from .settings import Settings
from .sidecar_client import RemoteAnalysis, RemoteIngest
from .store import JobStore
from .uploads import Limits
from .usage import NullUsage, RedisUsage, Usage, build_usage

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
    namer: Namer
    limiter: RateLimiter
    # Reads client molfiles in child processes (`analysis_pool`), a few at once; and the ZIP
    # downloads being streamed at once. No queue for either: the next request is 503 busy.
    analysis: Analysis
    streams: Capacity
    client_streams: ClientShares  # of those, what one client may hold
    usage: Usage  # the per-day counts (`usage`); counts nothing in tests
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
    namer: Namer | None = None,
    analysis: Analysis | None = None,
    usage: Usage | None = None,
    on_shutdown: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    deps_namer = namer or build_namer(settings)
    # With INGEST_SIDECAR_URL the parsers run in the ingest sidecar's container, not in children of
    # this one (`sidecar.py`); without it, in children of this process.
    sidecar = settings.ingest_sidecar_url is not None
    deps_analysis = analysis or (RemoteAnalysis(settings) if sidecar else build_analysis(settings))
    deps_ingest = ingest_runner or (
        RemoteIngest(settings) if sidecar else SubprocessIngest(settings)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # `.tmp-*` folders a crash left in the reports volume, reports past their age (the privacy
        # page promises 12 months at the latest); a warning if it is nearly full. Then again every
        # hour. Whatever goes wrong here (a full or read-only volume, a tampered record) is logged:
        # housekeeping never keeps the api from starting.
        tidy_arguments = (
            settings.reports_dir,
            settings.max_reports,
            settings.reports_max_bytes,
            settings.reports_max_age_days,
        )
        try:
            await asyncio.to_thread(tidy, *tidy_arguments)
        except Exception:
            log.warning("could not tidy REPORTS_DIR %s", settings.reports_dir, exc_info=True)
        await deps_analysis.start()  # the molfile-reading children, ready before the first request
        hourly_tidy = asyncio.create_task(tidy_every(TIDY_INTERVAL_SECONDS, *tidy_arguments))
        yield
        hourly_tidy.cancel()
        with suppress(asyncio.CancelledError):
            await hourly_tidy
        await deps_analysis.aclose()
        await deps_namer.aclose()
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
        ingest=deps_ingest,
        limits=Limits.from_settings(settings),
        hash_client=_client_hasher(settings.ip_hash_secret),
        gate=IngestGate(settings.ingest_concurrency),
        namer=deps_namer,
        limiter=RateLimiter(store),
        analysis=deps_analysis,
        streams=Capacity(settings.max_concurrent_downloads),
        client_streams=ClientShares(settings.max_downloads_per_client),
        usage=usage or NullUsage(),
    )
    handlers.register(app)
    app.include_router(router)
    app.include_router(result_router)

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
        # With the ingest sidecar (INGEST_SIDECAR_URL) the api is not ready while it does not
        # answer: uploads cannot work without it. (Said here and not in `summary`, which is part
        # of the OpenAPI schema.)
        sidecar_ping = getattr(deps_ingest, "ping", None)
        if ready and sidecar_ping is not None:
            ready = bool(await sidecar_ping())
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
    # Off without SENTRY_DSN. The uvicorn process is the API: the integrations patch Starlette once.
    init_error_reporting(
        settings.sentry_dsn,
        service="api",
        environment=settings.sentry_environment,
        release=settings.sentry_release,
    )
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    enqueuer = ArqEnqueuer(
        settings.redis_url,
        queue_name=settings.worker_queue_name,
        fast_queue_name=settings.worker_fast_queue_name,
        fast_lane_check_seconds=settings.fast_lane_check_seconds,
    )

    usage: RedisUsage = build_usage(settings)

    async def shutdown() -> None:
        await enqueuer.aclose()
        await redis.aclose()
        await usage.aclose()
        close_error_reporting()

    return create_app(
        settings, JobStore(redis, settings), enqueuer, usage=usage, on_shutdown=shutdown
    )
