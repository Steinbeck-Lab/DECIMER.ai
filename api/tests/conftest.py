from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from decimer_api.ingest_runner import IngestRunner, InProcessIngest
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from decimer_api.uploads import Limits


class FakeClock:
    """A settable clock shared by the store and the sweep."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeEnqueue:
    """Records enqueued job ids and their size class; can be made to fail."""

    def __init__(self) -> None:
        self.job_ids: list[str] = []
        self.small: dict[str, bool] = {}
        self.error: Exception | None = None

    async def __call__(self, job_id: str, *, small: bool = False) -> None:
        if self.error is not None:
            raise self.error
        self.job_ids.append(job_id)
        self.small[job_id] = small


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        redis_url="redis://unused:6379/0",
        data_dir=tmp_path / "jobs",
        job_ttl_seconds=3600,
        max_pdf_pages=10,
        max_upload_mb=1,
        max_active_jobs_per_ip=2,
        queue_max=50,
        max_image_pixels=20_000_000,
        max_job_pixels=100_000_000,
        trusted_proxy_cidr=[],
        ip_hash_secret="test-secret",
        media_stream_direct=False,
    )


@pytest.fixture
async def redis() -> AsyncIterator[fakeredis.FakeAsyncRedis]:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
def store(redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock) -> JobStore:
    return JobStore(redis, settings, clock=clock)


@pytest.fixture
def enqueue() -> FakeEnqueue:
    return FakeEnqueue()


@pytest.fixture
def ingest_runner(settings: Settings) -> IngestRunner:
    """Most tests run the ingest code in a thread: same code, no process start-up. The tests
    that are about isolation use the subprocess runner explicitly."""
    return InProcessIngest(Limits.from_settings(settings))


@pytest.fixture
def app(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue, ingest_runner: IngestRunner
) -> FastAPI:
    from decimer_api.app import create_app

    return create_app(settings, store, enqueue, ingest_runner=ingest_runner)


@pytest.fixture
def make_client(app: FastAPI) -> Callable[..., httpx.AsyncClient]:
    """Clients for one app; `peer` is the direct TCP peer the app sees."""

    def factory(peer: str = "203.0.113.10", **headers: str) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=app, client=(peer, 50000))
        return httpx.AsyncClient(transport=transport, base_url="http://api.test", headers=headers)

    return factory


@pytest.fixture
def client(make_client: Callable[..., httpx.AsyncClient]) -> httpx.AsyncClient:
    return make_client()
