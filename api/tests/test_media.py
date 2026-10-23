"""GET /api/jobs/{id}/media/{name}: job-scoped, X-Accel-Redirect, no traversal."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from decimer_api.ingest_runner import IngestRunner
from decimer_api.models import Job, JobStatus, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock, FakeEnqueue
from tests.factories import make_png

ClientFactory = Callable[..., httpx.AsyncClient]


async def make_job(
    store: JobStore, settings: Settings, clock: FakeClock, files: dict[str, bytes]
) -> Job:
    job = Job.create(
        job_id=new_job_id(), client_ip_hash="h" * 32, pages=1, pages_processed=1, now=clock()
    )
    job.status = JobStatus.DONE
    await store.save(job)
    directory = settings.data_dir / job.id
    (directory / "input").mkdir(parents=True)
    (directory / "input" / "0001.png").write_bytes(make_png())
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return job


async def test_media_is_handed_to_nginx(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    response = await client.get(f"/api/jobs/{job.id}/media/crop-0.png")
    assert response.status_code == 200
    assert response.headers["x-accel-redirect"] == f"/_protected/jobs/{job.id}/crop-0.png"
    assert response.content == b""
    assert response.headers["content-type"] == "image/png"
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["crop-0.png", "crop-19.png", "page-0.png", "page-12.png"])
async def test_allowed_names(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock, name: str
) -> None:
    job = await make_job(store, settings, clock, {name: make_png()})
    assert (await client.get(f"/api/jobs/{job.id}/media/{name}")).status_code == 200


async def test_every_page_url_of_the_job_is_served_and_a_skipped_page_is_404(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = await make_job(
        store, settings, clock, {"page-0.png": make_png(), "page-2.png": make_png()}
    )
    job.pages = job.pages_processed = 3
    await store.save(job)
    urls = (await client.get(f"/api/jobs/{job.id}")).json()["pageUrls"]
    assert urls == [f"/api/jobs/{job.id}/media/page-{i}.png" for i in range(3)]
    # The URLs are what the page loads: same origin, answered by the media route.
    assert [(await client.get(url)).status_code for url in urls] == [
        200,
        404,
        200,
    ]  # page 1 failed to render


async def test_a_media_file_of_job_a_is_404_under_job_b(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job_a = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    job_b = await make_job(store, settings, clock, {})
    assert (await client.get(f"/api/jobs/{job_a.id}/media/crop-0.png")).status_code == 200
    response = await client.get(f"/api/jobs/{job_b.id}/media/crop-0.png")
    assert response.status_code == 404
    assert "x-accel-redirect" not in response.headers


async def test_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/jobs/{new_job_id()}/media/crop-0.png")
    assert response.status_code == 404


async def test_files_of_an_expired_job_are_404_even_before_the_sweep(
    client: httpx.AsyncClient,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    job = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    assert (await client.get(f"/api/jobs/{job.id}/media/crop-0.png")).status_code == 200
    await redis.delete(store.job_key(job.id))  # Redis let the record expire
    assert (settings.data_dir / job.id / "crop-0.png").exists()  # the sweep has not run yet
    assert (await client.get(f"/api/jobs/{job.id}/media/crop-0.png")).status_code == 404


async def test_a_media_request_does_not_parse_the_record(
    client: httpx.AsyncClient,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    job = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    await redis.set(store.job_key(job.id), "not json, never parsed", ex=60)
    assert (await client.get(f"/api/jobs/{job.id}/media/crop-0.png")).status_code == 200


async def test_a_folder_without_a_record_is_not_served(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    orphan = new_job_id()
    (settings.data_dir / orphan).mkdir(parents=True)
    (settings.data_dir / orphan / "crop-0.png").write_bytes(make_png())
    assert (await client.get(f"/api/jobs/{orphan}/media/crop-0.png")).status_code == 404


@pytest.mark.parametrize(
    "name",
    [
        "crop-0.PNG",
        "crop-.png",
        "crop-a.png",
        "crop-0.png.exe",
        "crop-0.jpg",
        "crop--1.png",
        "crop-0000000000.png",
        "crop-0.png%00",
        "crop 0.png",
        "0001.png",
        "document.pdf",
        "input",
        "input%2F0001.png",
        "%2e%2e",
        "%2e%2e%2fcrop-0.png",
        "..%2fcrop-0.png",
        "..%2F..%2F..%2Fetc%2Fpasswd",
        "%252e%252e%252fcrop-0.png",
        "crop-0.png%2f..%2fcrop-0.png",
        "....//crop-0.png",
        "%2fetc%2fpasswd",
        "crop-0.png/",
    ],
)
async def test_bad_names_and_traversal_are_404(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock, name: str
) -> None:
    job = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    for url in (f"/api/jobs/{job.id}/media/{name}", f"/api/jobs/{job.id}/media/{name}/x"):
        response = await client.get(url)
        assert response.status_code == 404, (url, response.status_code)
        assert "x-accel-redirect" not in response.headers


async def test_only_media_names_are_served_even_if_the_file_exists(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    """Whatever else the worker keeps in the job folder stays private."""
    decoys = ["internal.json", "crop-0.png.bak", "page-1.PNG", "crop-.png", ".env", "document.pdf"]
    job = await make_job(store, settings, clock, dict.fromkeys(decoys, b"private"))
    for name in decoys + ["input", "0001.png"]:
        response = await client.get(f"/api/jobs/{job.id}/media/{name}")
        assert response.status_code == 404, name
        assert b"private" not in response.content


@pytest.mark.parametrize("name", ["..", ".", "../crop-0.png", "crop-0.png/..", "a/../crop-0.png"])
async def test_dot_segments_that_reach_the_app_are_404(
    app: FastAPI, store: JobStore, settings: Settings, clock: FakeClock, name: str
) -> None:
    """httpx collapses `..` before sending, so this speaks ASGI directly."""
    job = await make_job(store, settings, clock, {"crop-0.png": make_png()})
    path = f"/api/jobs/{job.id}/media/{name}"
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.1", 1),
        "server": ("api.test", 80),
    }
    await app(scope, receive, send)  # type: ignore[arg-type]
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 404


@pytest.mark.parametrize("job_id", ["..", "%2e%2e", "..%2f..", "x" * 32, "0" * 32 + "%2f.."])
async def test_traversal_through_the_job_id_is_404(client: httpx.AsyncClient, job_id: str) -> None:
    assert (await client.get(f"/api/jobs/{job_id}/media/crop-0.png")).status_code == 404


@pytest.fixture
def direct_client(
    settings: Settings, store: JobStore, enqueue: FakeEnqueue, ingest_runner: IngestRunner
) -> httpx.AsyncClient:
    """A client for an app that streams files itself (development mode)."""
    from decimer_api.app import create_app

    direct = settings.model_copy(update={"media_stream_direct": True})
    transport = httpx.ASGITransport(
        app=create_app(direct, store, enqueue, ingest_runner=ingest_runner)
    )
    return httpx.AsyncClient(transport=transport, base_url="http://api.test")


async def test_a_symlink_inside_the_job_folder_is_not_followed(
    direct_client: httpx.AsyncClient,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"top secret")
    job = await make_job(store, settings, clock, {})
    os.symlink(secret, settings.data_dir / job.id / "crop-0.png")
    response = await direct_client.get(f"/api/jobs/{job.id}/media/crop-0.png")
    assert response.status_code == 404
    assert b"top secret" not in response.content


async def test_a_symlinked_job_folder_is_not_followed(
    direct_client: httpx.AsyncClient,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    job = await make_job(store, settings, clock, {})
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "crop-0.png").write_bytes(b"top secret")
    shutil.rmtree(settings.data_dir / job.id)
    os.symlink(outside, settings.data_dir / job.id)
    assert (await direct_client.get(f"/api/jobs/{job.id}/media/crop-0.png")).status_code == 404


async def test_direct_streaming_mode_serves_the_bytes(
    direct_client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    png = make_png()
    job = await make_job(store, settings, clock, {"crop-0.png": png})
    response = await direct_client.get(f"/api/jobs/{job.id}/media/crop-0.png")
    assert response.status_code == 200
    assert response.content == png
    assert response.headers["content-type"] == "image/png"
    assert "x-accel-redirect" not in response.headers
    assert (await direct_client.get(f"/api/jobs/{job.id}/media/crop-1.png")).status_code == 404
