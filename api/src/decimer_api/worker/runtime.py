"""Building the pieces a worker process needs, and closing them. Shared by the arq worker
(`arq_app`) and the in-process development mode (`inprocess`)."""

from __future__ import annotations

import logging
import time

import httpx

from ..settings import MIB, Settings
from ..store import JobStore
from .pipeline import CdkFallback, Deps
from .render import Renderer, SubprocessRenderer
from .replicas import Clock, Resolver, system_resolver
from .services import Sleep, make_gates, make_http_client, make_services
from .settings import WorkerSettings

log = logging.getLogger("decimer_api.worker")


def build_deps(
    settings: Settings,
    worker: WorkerSettings,
    store: JobStore,
    *,
    http: httpx.AsyncClient | None = None,
    renderer: Renderer | None = None,
    cdk_fallback: CdkFallback | None = None,
    sleep: Sleep | None = None,
    resolver: Resolver = system_resolver,
    clock: Clock = time.monotonic,
) -> Deps:
    """The production wiring: one shared HTTP client, a child process per rendered page, one pool
    of replicas per model service. `Deps.aclose()` closes the client."""
    worker.check_against(settings)
    client = http or make_http_client(worker)
    gates = make_gates(worker)
    services = (
        make_services(client, worker, resolver=resolver, clock=clock)
        if sleep is None
        else make_services(client, worker, sleep=sleep, resolver=resolver, clock=clock)
    )
    if renderer is None:
        renderer = SubprocessRenderer(
            timeout_seconds=worker.render_timeout_seconds,
            memory_mb=worker.render_memory_mb,
            # Same bound as the ingest child: a worst-case PNG of the biggest allowed page.
            max_file_bytes=3 * settings.max_image_pixels + 64 * MIB,
        )
    if worker.cdk_fallback and cdk_fallback is None:
        log.warning(
            "CDK_FALLBACK is on, but no CDK backend is plugged in (D4 is open): RDKit decides alone"
        )
    return Deps(
        settings=settings,
        worker=worker,
        store=store,
        services=services,
        renderer=renderer,
        gates=gates,
        cdk_fallback=cdk_fallback,
        http=client,
    )
