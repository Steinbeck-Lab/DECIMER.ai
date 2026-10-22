import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import pytest

from decimer_service import Limits, create_app
from decimer_service.logs import JsonFormatter


class StubModel:
    name = "stub"

    def __init__(self) -> None:
        self.loaded = False
        self.selftest_error: Exception | None = None
        self.infer_error: Exception | None = None
        self.gate: threading.Event | None = None

    def load(self) -> None:
        self.loaded = True

    def selftest(self) -> None:
        if self.selftest_error:
            raise self.selftest_error

    def infer(self, image: bytes, params: Mapping[str, str]) -> dict[str, Any]:
        if self.gate is not None:
            self.gate.wait(timeout=5)
        if self.infer_error:
            raise self.infer_error
        return {"bytes": len(image), "params": dict(params)}


@pytest.fixture
def model() -> StubModel:
    return StubModel()


async def client_for(
    model: StubModel, limits: Limits | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(model, limits or Limits())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://svc") as client:
            yield client


@pytest.fixture
async def client(model: StubModel) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(model):
        yield c


async def test_model_is_loaded_at_startup_and_healthz_ok(
    model: StubModel, client: httpx.AsyncClient
) -> None:
    assert model.loaded
    r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "model": "stub"}


async def test_readyz_ok_when_selftest_passes(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_readyz_503_when_selftest_fails(model: StubModel) -> None:
    model.selftest_error = RuntimeError("weights missing")
    async for client in client_for(model):
        r = await client.get("/readyz")
        assert r.status_code == 503
        assert "weights missing" in r.json()["detail"]


async def test_infer_returns_model_output_and_passes_params(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/infer", files={"image": ("p.png", b"12345", "image/png")}, data={"page_index": "3"}
    )
    assert r.status_code == 200
    assert r.json() == {"bytes": 5, "params": {"page_index": "3"}}


async def test_infer_rejects_oversized_image(model: StubModel) -> None:
    async for client in client_for(model, Limits(max_image_bytes=4)):
        r = await client.post("/infer", files={"image": ("p.png", b"12345", "image/png")})
        assert r.status_code == 413


async def test_infer_rejects_missing_image(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", data={"page_index": "3"})
    assert r.status_code == 422


async def test_bad_input_from_model_is_422(model: StubModel, client: httpx.AsyncClient) -> None:
    model.infer_error = ValueError("not an image")
    r = await client.post("/infer", files={"image": ("p.png", b"x", "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"] == "not an image"


async def test_model_crash_is_500_and_service_stays_up(
    model: StubModel, client: httpx.AsyncClient
) -> None:
    # The legacy socket servers died on the first bad request; one failure must not take the
    # service down.
    model.infer_error = RuntimeError("tensor shape mismatch")
    r = await client.post("/infer", files={"image": ("p.png", b"x", "image/png")})
    assert r.status_code == 500
    assert "tensor shape mismatch" not in r.text  # internals are logged, not returned
    model.infer_error = None
    r = await client.post("/infer", files={"image": ("p.png", b"x", "image/png")})
    assert r.status_code == 200


async def test_busy_service_answers_503_instead_of_queueing_forever(model: StubModel) -> None:
    model.gate = threading.Event()
    async for client in client_for(model, Limits(max_concurrency=1, max_waiting=0)):
        files = {"image": ("p.png", b"x", "image/png")}
        first = asyncio.create_task(client.post("/infer", files=files))
        await asyncio.sleep(0.1)  # let the first request take the only slot
        second = await client.post("/infer", files=files)
        assert second.status_code == 503
        assert second.headers["retry-after"] == "5"
        model.gate.set()
        assert (await first).status_code == 200


async def test_waiting_requests_run_once_a_slot_frees(model: StubModel) -> None:
    model.gate = threading.Event()
    async for client in client_for(model, Limits(max_concurrency=1, max_waiting=2)):
        files = {"image": ("p.png", b"x", "image/png")}
        tasks = [asyncio.create_task(client.post("/infer", files=files)) for _ in range(3)]
        await asyncio.sleep(0.1)
        model.gate.set()
        assert [r.status_code for r in await asyncio.gather(*tasks)] == [200, 200, 200]


def test_json_formatter_writes_one_json_object_per_line() -> None:
    record = logging.LogRecord("svc", logging.INFO, __file__, 1, "inferred %s", ("p.png",), None)
    record.duration_ms = 12
    line = JsonFormatter().format(record)
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["message"] == "inferred p.png"
    assert payload["level"] == "INFO"
    assert payload["duration_ms"] == 12
