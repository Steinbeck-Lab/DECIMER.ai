import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator, Iterator

import cv2
import httpx
import numpy as np
import pytest
import uvicorn
from decimer_service import Limits, create_app
from helpers import FakeSegmenter, OverlapProbe, png_bytes, rgba_crop

from decimer_seg import __main__ as entry
from decimer_seg import app as seg_app
from decimer_seg.model import SegModel


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level


@pytest.fixture
def fake() -> FakeSegmenter:
    # y0, x0, y1, x1 as the library reports them
    return FakeSegmenter([rgba_crop(30, 20, seed=5)], [(10, 20, 40, 40)])


@pytest.fixture
async def client(fake: FakeSegmenter) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(SegModel(fake), Limits())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg") as c:
            yield c


def test_production_limits_are_pinned() -> None:
    assert seg_app.LIMITS.max_concurrency == 1  # one Mask R-CNN at a time: it needs ~2 GB
    assert seg_app.LIMITS.max_waiting == 8
    assert seg_app.LIMITS.max_image_bytes == 40 * 1024 * 1024


def test_build_app_gives_create_app_the_pinned_limits_and_the_seg_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def spy(model: object, limits: object = None) -> str:
        seen.update(model=model, limits=limits)
        return "app"

    monkeypatch.setattr(seg_app, "create_app", spy)

    assert seg_app.build_app() == "app"
    assert seen["limits"] is seg_app.LIMITS
    assert isinstance(seen["model"], SegModel)


async def test_the_pinned_upload_limit_is_enforced_at_exactly_40_mib(fake: FakeSegmenter) -> None:
    app = seg_app.build_app(SegModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg") as c:
            limit = 40 * 1024 * 1024
            over = await c.post(
                "/infer", files={"image": ("x.png", b"\0" * (limit + 1), "image/png")}
            )
            at = await c.post("/infer", files={"image": ("x.png", b"\0" * limit, "image/png")})
    assert over.status_code == 413
    assert at.status_code == 422  # passes the size gate, then is not an image


async def test_infer_returns_the_documented_shape(
    client: httpx.AsyncClient, fake: FakeSegmenter
) -> None:
    files = {"image": ("page.png", png_bytes(300, 200), "image/png")}
    r = await client.post("/infer", files=files, data={"page_index": "2"})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"page_index", "width", "height", "segments"}
    assert (body["page_index"], body["width"], body["height"]) == (2, 300, 200)
    (segment,) = body["segments"]
    assert set(segment) == {"index", "bbox", "png"}
    assert segment["index"] == 0
    assert segment["bbox"] == [20, 10, 40, 40]
    crop = cv2.imdecode(
        np.frombuffer(base64.b64decode(segment["png"]), np.uint8), cv2.IMREAD_UNCHANGED
    )
    assert np.array_equal(crop, fake.segments[0])


async def test_garbage_image_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", files={"image": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"] == "could not decode image"


async def test_bad_page_index_is_422(client: httpx.AsyncClient) -> None:
    files = {"image": ("page.png", png_bytes(10, 10), "image/png")}
    r = await client.post("/infer", files=files, data={"page_index": "first"})
    assert r.status_code == 422
    assert "page_index" in r.json()["detail"]


async def test_segmentation_crash_is_500_and_the_service_stays_up(
    client: httpx.AsyncClient, fake: FakeSegmenter
) -> None:
    files = {"image": ("page.png", png_bytes(10, 10), "image/png")}
    fake.error = ValueError("inside mrcnn")
    assert (await client.post("/infer", files=files)).status_code == 500
    fake.error = None
    assert (await client.post("/infer", files=files)).status_code == 200


async def test_readyz_runs_the_selftest(client: httpx.AsyncClient, fake: FakeSegmenter) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert len(fake.calls) == 1


async def test_readyz_is_503_when_the_selftest_fails(fake: FakeSegmenter) -> None:
    fake.error = ValueError("weights missing")
    app = create_app(SegModel(fake), Limits())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg") as c:
            assert (await c.get("/readyz")).status_code == 503


async def test_readyz_is_503_when_the_model_finds_nothing_in_its_test_image() -> None:
    app = create_app(SegModel(FakeSegmenter()), Limits())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg") as c:
            r = await c.get("/readyz")
    assert r.status_code == 503
    assert "no structure" in r.json()["detail"]


async def test_a_probe_during_a_request_never_runs_a_second_inference() -> None:
    # A crash loop seen once: Docker's health check called /readyz (a selftest on the model)
    # while the worker's /infer was running, two inferences in one process, heap corruption.
    # Production wiring (build_app: real SegModel, production limits), cold readiness cache.
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    app = seg_app.build_app(SegModel(probe))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg") as c:
            files = {"image": ("page.png", png_bytes(300, 200), "image/png")}
            infer = asyncio.create_task(c.post("/infer", files=files))
            await asyncio.to_thread(probe.wait_until_inside)
            probes = [asyncio.create_task(c.get("/readyz")) for _ in range(2)]
            await asyncio.sleep(0.2)  # long enough for an unguarded selftest to get in

            assert probe.inside == 1
            probe.release.set()
            assert (await infer).status_code == 200
            assert [(await p).status_code for p in probes] == [200, 200]
    assert probe.peak == 1


def test_main_serves_the_seg_app_on_all_interfaces_and_port_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, object] = {}

    def fake_run(app: object, **kwargs: object) -> None:
        started["app"] = app
        started.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("PORT", "9123")
    entry.main()

    assert started["host"] == "0.0.0.0"  # noqa: S104
    assert started["port"] == 9123
    assert started["log_config"] is None  # keep the JSON logging configured by the service
    assert getattr(started["app"], "title", None) == "decimer-seg"


def test_main_defaults_to_port_8001(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert started["port"] == 8001


def test_main_logs_are_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: logging.getLogger("t").warning("hello"))
    entry.main()
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(line)["message"] == "hello"
