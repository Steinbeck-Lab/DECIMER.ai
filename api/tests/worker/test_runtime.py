"""`build_deps`: the production wiring, with fake services but the real renderer."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from PIL import Image

from decimer_api.models import JobStatus
from decimer_api.settings import Settings
from decimer_api.worker.pipeline import run_pipeline
from decimer_api.worker.render import SubprocessRenderer
from decimer_api.worker.runtime import build_deps
from decimer_api.worker.services import make_http_client
from decimer_api.worker.settings import WorkerSettings
from tests.worker.conftest import Rig, SpyStore
from tests.worker.fakes import FakeBackend, FakeSleep, no_dns


async def test_a_job_through_the_production_wiring_renders_in_a_child_process(
    make_rig: Callable[..., Rig], settings: Settings, spy_store: SpyStore
) -> None:
    rig = make_rig()  # only for its job helpers; the pipeline below runs on `build_deps`
    backend = FakeBackend(layout={0: 1, 1: 2})
    worker = WorkerSettings(
        seg_url="http://seg.test",
        ocsr_url="http://ocsr.test",
        cls_url="http://cls.test",
        chem_url="",
    )
    http = httpx.AsyncClient(transport=backend.transport)
    deps = build_deps(settings, worker, spy_store, http=http, sleep=FakeSleep(), resolver=no_dns)
    try:
        assert isinstance(deps.renderer, SubprocessRenderer)
        job = await rig.pdf_job(2)
        await run_pipeline(job.id, deps)
    finally:
        await deps.aclose()
    stored = await spy_store.get(job.id, include_expired=True)
    assert stored is not None and stored.status is JobStatus.DONE, stored and stored.error
    assert len(stored.results) == 3
    assert deps.renderer.last_pid is not None
    with Image.open(settings.data_dir / job.id / "page-1.png") as image:
        assert image.size == (600, 600)
    assert http.is_closed


def test_the_http_client_ignores_proxy_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.campus.example:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.campus.example:3128")
    client = make_http_client(WorkerSettings())
    assert client._trust_env is False
    assert client.follow_redirects is False


def test_the_client_lets_go_of_an_idle_connection_before_a_uvicorn_server_does() -> None:
    """The services are uvicorn servers, which close an idle connection after 5 s. A client that
    keeps one for 5 s too can reuse it just as it is closed and get a reset, which the worker takes
    for a dead replica (and cools it). Two seconds leaves the client the first to let go."""
    transport: Any = make_http_client(WorkerSettings())._transport
    assert transport._pool._keepalive_expiry < 5


def test_the_client_has_no_connection_cap_below_what_the_replica_pools_allow() -> None:
    """The replica pools bound what is in flight, exactly: replicas x concurrency per service. A
    fixed cap of 32 under that sum would turn a wait into a PoolTimeout (read as a slow service)."""
    transport: Any = make_http_client(WorkerSettings())._transport
    assert transport._pool._max_connections >= 100_000


async def test_the_cdk_flag_without_chem_is_noted_and_harmless(
    settings: Settings, spy_store: SpyStore, caplog: pytest.LogCaptureFixture
) -> None:
    """CDK runs in chem: with no CHEM_URL there is nothing to call, and the worker says so once."""
    with caplog.at_level(logging.INFO, logger="decimer_api.worker"):
        deps = build_deps(settings, WorkerSettings(cdk_fallback=True, chem_url=None), spy_store)
    await deps.aclose()
    assert "CDK_FALLBACK is on, but CHEM_URL is not set" in caplog.text


async def test_the_cdk_flag_with_chem_says_nothing(
    settings: Settings, spy_store: SpyStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="decimer_api.worker"):
        deps = build_deps(
            settings, WorkerSettings(cdk_fallback=True, chem_url="http://chem:8004"), spy_store
        )
    await deps.aclose()
    assert "CDK_FALLBACK" not in caplog.text


def test_the_cdk_fallback_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """On unless CDK_FALLBACK says otherwise."""
    monkeypatch.delenv("CDK_FALLBACK", raising=False)
    assert WorkerSettings().cdk_fallback is True
    monkeypatch.setenv("CDK_FALLBACK", "false")
    assert WorkerSettings().cdk_fallback is False


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("CHEM_URL", "", None),
        ("CHEM_URL", "  ", None),
        ("CHEM_URL", "http://chem:8004", "http://chem:8004"),
    ],
)
def test_chem_is_optional_and_an_empty_value_means_off(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, expected: str | None
) -> None:
    monkeypatch.setenv(variable, value)
    assert WorkerSettings().chem_url == expected


def test_the_settings_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEG_URL", "http://seg:8001/")
    monkeypatch.setenv("OCSR_CONCURRENCY", "3")
    monkeypatch.setenv("BUSY_RETRIES", "7")
    worker = WorkerSettings()
    assert worker.url("seg") == "http://seg:8001"
    assert worker.concurrency("ocsr") == 3
    assert worker.busy_retries == 7
