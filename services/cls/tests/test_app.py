import json
import logging
from collections.abc import AsyncIterator, Iterator

import httpx
import numpy as np
import pytest
import uvicorn
from helpers import FakeScorer, image_bytes, structure_if_inked

from decimer_cls import __main__ as entry
from decimer_cls import app as cls_app
from decimer_cls import model as model_module
from decimer_cls.model import ClsModel

INKED = image_bytes(30, 20, color=0)  # something drawn: the fake calls it a structure
BLANK = image_bytes(30, 20)


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level


@pytest.fixture
def fake() -> FakeScorer:
    # Answers like the real model on drawn versus blank images, so /readyz's selftest passes too.
    return FakeScorer(structure_if_inked(structure=np.float32(2.5e-10), blank=np.float32(0.97)))


@pytest.fixture
async def client(fake: FakeScorer) -> AsyncIterator[httpx.AsyncClient]:
    app = cls_app.build_app(ClsModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cls") as c:
            yield c


def test_production_limits_are_pinned() -> None:
    assert cls_app.LIMITS.max_concurrency == 1  # one Keras model, called from one request at a time
    assert cls_app.LIMITS.max_waiting == 16
    assert cls_app.LIMITS.max_image_bytes == 20 * 1024 * 1024


def test_build_app_gives_create_app_the_pinned_limits_and_the_cls_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def spy(model: object, limits: object = None) -> str:
        seen.update(model=model, limits=limits)
        return "app"

    monkeypatch.setattr(cls_app, "create_app", spy)

    assert cls_app.build_app() == "app"
    assert seen["limits"] is cls_app.LIMITS
    assert isinstance(seen["model"], ClsModel)


async def test_the_pinned_upload_limit_is_enforced_at_exactly_20_mib(fake: FakeScorer) -> None:
    app = cls_app.build_app(ClsModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cls") as c:
            limit = 20 * 1024 * 1024
            over = await c.post(
                "/infer", files={"image": ("x.png", b"\0" * (limit + 1), "image/png")}
            )
            at = await c.post("/infer", files={"image": ("x.png", b"\0" * limit, "image/png")})
    assert over.status_code == 413
    assert at.status_code == 422  # passes the size gate, then is not an image


async def test_infer_returns_the_documented_shape(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", files={"image": ("crop.png", INKED, "image/png")})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"is_structure", "score"}
    assert body["is_structure"] is True
    assert body["score"] == pytest.approx(2.5e-10, rel=1e-6)


async def test_a_high_score_is_reported_as_not_a_structure(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", files={"image": ("crop.png", BLANK, "image/png")})
    body = r.json()
    assert body["is_structure"] is False
    assert body["score"] == pytest.approx(0.97, rel=1e-6)


async def test_garbage_image_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", files={"image": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"] == "could not decode image"


async def test_an_unsupported_pixel_mode_is_422_not_500(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_module, "FORMATS", [*model_module.FORMATS, "TIFF"])
    lab = image_bytes(6, 6, fmt="TIFF", mode="LAB", color=(50, 0, 0))
    r = await client.post("/infer", files={"image": ("x.tif", lab, "image/tiff")})
    assert r.status_code == 422
    assert r.json()["detail"] == "unsupported pixel mode: LAB"


async def test_classifier_crash_is_500_and_the_service_stays_up(
    client: httpx.AsyncClient, fake: FakeScorer
) -> None:
    files = {"image": ("crop.png", INKED, "image/png")}
    fake.error = RuntimeError("tensor shape mismatch")
    r = await client.post("/infer", files=files)
    assert r.status_code == 500
    assert "error_id" in r.json()
    fake.error = None
    assert (await client.post("/infer", files=files)).status_code == 200


async def test_readyz_runs_the_selftest(client: httpx.AsyncClient, fake: FakeScorer) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert len(fake.calls) == 2  # the hexagon and the blank page


async def test_readyz_is_503_when_the_selftest_fails(fake: FakeScorer) -> None:
    fake.error = RuntimeError("weights missing")
    app = cls_app.build_app(ClsModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cls") as c:
            assert (await c.get("/readyz")).status_code == 503


async def test_readyz_is_503_when_the_model_answers_wrongly(fake: FakeScorer) -> None:
    fake.score = np.float32(0.5)  # "not a structure" for the hexagon too
    app = cls_app.build_app(ClsModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cls") as c:
            r = await c.get("/readyz")
    assert r.status_code == 503
    assert "hexagon" in r.json()["detail"]


async def test_healthz_names_the_model(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.json() == {"status": "ok", "model": "cls"}


def test_main_serves_the_cls_app_on_all_interfaces_and_port_from_env(
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
    assert getattr(started["app"], "title", None) == "decimer-cls"


def test_main_defaults_to_port_8003(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert started["port"] == 8003


def test_main_logs_are_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: logging.getLogger("t").warning("hello"))
    entry.main()
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(line)["message"] == "hello"
