"""GET /api/jobs/{id}, /healthz, /readyz and the OpenAPI document."""

from __future__ import annotations

import os
import stat

import httpx
import pytest
from fastapi import FastAPI

from decimer_api.models import JobStatus, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock
from tests.factories import make_png, multipart


async def create(client: httpx.AsyncClient) -> dict[str, object]:
    body, headers = multipart([("a.png", make_png())])
    response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == 202
    return dict(response.json())


async def test_get_returns_the_job(client: httpx.AsyncClient) -> None:
    created = await create(client)
    response = await client.get(f"/api/jobs/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created
    assert response.headers["cache-control"] == "no-store"


async def test_queue_position_disappears_once_the_job_runs(
    client: httpx.AsyncClient, store: JobStore
) -> None:
    created = await create(client)
    await store.mark_running(str(created["id"]))
    job = (await client.get(f"/api/jobs/{created['id']}")).json()
    assert job["status"] == "running"
    assert job["queuePosition"] is None


async def test_a_finished_job_is_returned_until_it_expires(
    client: httpx.AsyncClient, store: JobStore, clock: FakeClock
) -> None:
    created = await create(client)
    job = await store.get(str(created["id"]))
    assert job is not None
    job.status = JobStatus.DONE
    await store.finish(job)
    assert (await client.get(f"/api/jobs/{job.id}")).json()["status"] == "done"
    clock.advance(3601)
    assert (await client.get(f"/api/jobs/{job.id}")).status_code == 404


async def test_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/jobs/{new_job_id()}")
    assert response.status_code == 404
    assert response.json() == {
        "detail": {"code": "not_found", "message": "Not found. The job may have expired."}
    }


@pytest.mark.parametrize(
    "job_id",
    ["..", "x", "0" * 31, "0" * 33, "A" * 32, "g" * 32, "%2e%2e", "0" * 32 + "%2f..", "a b"],
)
async def test_malformed_ids_are_404_not_422(client: httpx.AsyncClient, job_id: str) -> None:
    response = await client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 404


async def test_the_response_has_no_client_address_hash(
    client: httpx.AsyncClient,
) -> None:
    created = await create(client)
    text = (await client.get(f"/api/jobs/{created['id']}")).text
    assert "clientIp" not in text and "client_ip" not in text


# --- health ---------------------------------------------------------------------------------


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200 and response.json() == {"status": "ok"}


async def test_readyz_pings_redis(client: httpx.AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200 and response.json() == {"status": "ready"}


async def test_readyz_is_503_when_redis_is_down(
    client: httpx.AsyncClient, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down() -> bool:
        raise ConnectionError("down")

    monkeypatch.setattr(store.redis, "ping", down)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "not ready"
    assert "down" not in response.text  # no internals in the body


async def test_readyz_is_503_when_the_data_dir_is_not_writable(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.data_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(settings.data_dir, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        assert (await client.get("/readyz")).status_code == 503
    finally:
        os.chmod(settings.data_dir, stat.S_IRWXU)


# --- OpenAPI --------------------------------------------------------------------------------


async def test_openapi_describes_the_endpoints_and_camel_case_fields(
    client: httpx.AsyncClient,
) -> None:
    schema = (await client.get("/api/openapi.json")).json()
    assert {
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/jobs/{job_id}/media/{name}",
        "/healthz",
        "/readyz",
    } <= set(schema["paths"])
    post = schema["paths"]["/api/jobs"]["post"]
    assert "202" in post["responses"] and "429" in post["responses"] and "503" in post["responses"]
    assert "multipart/form-data" in post["requestBody"]["content"]
    job = schema["components"]["schemas"]["JobView"]["properties"]
    assert {
        "queuePosition",
        "pagesProcessed",
        "structuresDropped",
        "createdAt",
        "expiresAt",
    } <= set(job)
    result = schema["components"]["schemas"]["ResultView"]["properties"]
    assert {"pageIndex", "isStructure", "nameTier", "cropUrl", "number"} <= set(result)
    assert "clientIpHash" not in job


async def test_a_redis_outage_is_a_503_not_a_stack_trace(
    client: httpx.AsyncClient, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def down(*_args: object, **_kwargs: object) -> None:
        raise RedisConnectionError("connection refused: redis:6379")

    monkeypatch.setattr(store.redis, "get", down)
    response = await client.get(f"/api/jobs/{new_job_id()}")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "unavailable"
    assert response.headers["retry-after"] == "5"
    assert "6379" not in response.text


# --- one error shape for everything --------------------------------------------------------


def assert_standard_shape(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"detail"}
    assert set(body["detail"]) == {"code", "message"}
    assert body["detail"]["code"] == code


async def test_unknown_routes_and_wrong_methods_use_the_standard_shape(
    client: httpx.AsyncClient,
) -> None:
    assert_standard_shape(await client.get("/nope"), 404, "not_found")
    assert_standard_shape(await client.get("/api/jobs"), 405, "method_not_allowed")
    wrong = await client.delete(f"/api/jobs/{new_job_id()}")
    assert_standard_shape(wrong, 405, "method_not_allowed")
    assert "GET" in wrong.headers["allow"]  # the standard header survives


async def test_validation_errors_use_the_standard_shape(app: FastAPI) -> None:
    @app.get("/api/_probe/{number}")
    async def probe(number: int) -> int:  # a route whose parameter can fail validation
        return number

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")
    assert_standard_shape(await client.get("/api/_probe/abc"), 422, "invalid_request")


async def test_running_out_of_admission_retries_is_a_503_with_retry_after(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimer_api import store as store_module

    monkeypatch.setattr(store_module, "ADMIT_ATTEMPTS", 0)
    response = await create_response(client)
    assert_standard_shape(response, 503, "unavailable")
    assert response.headers["retry-after"] == "5"


async def create_response(client: httpx.AsyncClient) -> httpx.Response:
    body, headers = multipart([("a.png", make_png())])
    return await client.post("/api/jobs", content=body, headers=headers)
