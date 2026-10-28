import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator

import httpx
import numpy as np
import pytest
import uvicorn
from helpers import DAIDZEIN, FakePredictor, cut_off, image_bytes

from decimer_ocsr import __main__ as entry
from decimer_ocsr import app as ocsr_app
from decimer_ocsr.model import OcsrModel

CROP = image_bytes(30, 20)


@pytest.fixture(autouse=True)
def restore_root_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:], root.level = handlers, level


@pytest.fixture
def fake() -> FakePredictor:
    return FakePredictor()  # answers daidzein for everything, so /readyz's selftest passes too


@pytest.fixture
async def client(fake: FakePredictor) -> AsyncIterator[httpx.AsyncClient]:
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            yield c


def test_production_limits_are_pinned() -> None:
    assert (
        ocsr_app.LIMITS.max_concurrency == 1
    )  # one transformer, called from one request at a time
    assert ocsr_app.LIMITS.max_waiting == 4
    assert ocsr_app.LIMITS.max_image_bytes == 20 * 1024 * 1024
    # The skeleton refreshes its readiness cache on every 200 /infer, so with a cache /readyz would
    # never reach the model's own policy under steady traffic. 0: every /readyz asks the model.
    assert ocsr_app.LIMITS.ready_cache_seconds == 0


def test_build_app_gives_create_app_the_pinned_limits_and_the_ocsr_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def spy(model: object, limits: object = None) -> str:
        seen.update(model=model, limits=limits)
        return "app"

    monkeypatch.setattr(ocsr_app, "create_app", spy)

    assert ocsr_app.build_app() == "app"
    assert seen["limits"] is ocsr_app.LIMITS
    assert isinstance(seen["model"], OcsrModel)


async def test_the_pinned_upload_limit_is_enforced_at_exactly_20_mib(fake: FakePredictor) -> None:
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            limit = 20 * 1024 * 1024
            over = await c.post(
                "/infer", files={"image": ("x.png", b"\0" * (limit + 1), "image/png")}
            )
            at = await c.post("/infer", files={"image": ("x.png", b"\0" * limit, "image/png")})
    assert over.status_code == 413
    assert at.status_code == 422  # passes the size gate, then is not an image


async def test_infer_returns_the_documented_shape(client: httpx.AsyncClient) -> None:
    fake_pairs = [("C", np.float32(0.5)), ("O", np.float32(0.25))]
    app = ocsr_app.build_app(OcsrModel(FakePredictor(("CO", fake_pairs))))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            r = await c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})

    assert r.status_code == 200
    assert r.json() == {
        "smiles": "CO",
        "token_confidence": [["C", 0.5], ["O", 0.25]],
        "mean_confidence": 0.375,
        "incomplete": False,
    }


async def test_infer_reads_the_image_it_is_sent(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    r = await client.post("/infer", files={"image": ("crop.png", image_bytes(12, 9), "image/png")})
    assert r.status_code == 200
    (array,) = fake.calls
    assert array.shape == (9, 12, 4)


async def test_form_fields_cannot_switch_on_the_hand_drawn_model(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    r = await client.post(
        "/infer",
        files={"image": ("crop.png", CROP, "image/png")},
        data={"hand_drawn": "true"},
    )
    assert r.status_code == 200
    assert len(fake.calls) == 1


async def test_garbage_image_is_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/infer", files={"image": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"] == "could not decode image"


async def test_an_image_over_the_pixel_cap_is_422(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimer_ocsr import model as model_module

    monkeypatch.setattr(model_module, "MAX_PIXELS", 10)
    r = await client.post("/infer", files={"image": ("x.png", image_bytes(4, 4), "image/png")})
    assert r.status_code == 422
    assert "too large" in r.json()["detail"]


async def test_model_crash_is_500_and_the_service_stays_up(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    files = {"image": ("crop.png", CROP, "image/png")}
    fake.error = RuntimeError("tensor shape mismatch")
    r = await client.post("/infer", files=files)
    assert r.status_code == 500
    assert "error_id" in r.json()
    fake.error = None
    assert (await client.post("/infer", files=files)).status_code == 200


async def test_a_value_error_inside_the_library_is_500_not_422(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    fake.error = ValueError("zero-size array")
    r = await client.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
    assert r.status_code == 500


async def test_readyz_runs_the_selftest(client: httpx.AsyncClient, fake: FakePredictor) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert len(fake.calls) == 1


async def test_readyz_is_503_when_the_selftest_crashes(fake: FakePredictor) -> None:
    fake.error = RuntimeError("weights missing")
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 503


async def test_readyz_is_503_when_the_model_answers_wrongly() -> None:
    wrong = FakePredictor(("CCO", [("C", 0.99), ("C", 0.99), ("O", 0.99)]))
    app = ocsr_app.build_app(OcsrModel(wrong))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            r = await c.get("/readyz")
    assert r.status_code == 503
    assert DAIDZEIN in r.json()["detail"]


async def test_readyz_answers_promptly_while_a_long_decode_is_in_flight() -> None:
    # A token-limit decode holds the model for 38-55 s and its 422 does not refresh the
    # skeleton's readiness cache; /readyz used to queue behind it and time out, and the container
    # went unhealthy.
    gate = threading.Event()
    fake = FakePredictor()
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 200  # the first, real, selftest
            fake.calls.clear()
            fake.entered.clear()
            fake.gate = gate
            long_decode = asyncio.create_task(
                c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
            )
            try:
                assert await asyncio.to_thread(fake.entered.wait, 10)

                ready = await asyncio.wait_for(c.get("/readyz"), timeout=5)

                assert ready.status_code == 200
                assert len(fake.calls) == 1  # no second decode was started for the check
            finally:
                gate.set()
            assert (await long_decode).status_code == 200


async def test_readyz_does_not_rerun_the_selftest_after_a_token_limit_answer() -> None:
    fake = FakePredictor()
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 200
            fake.calls.clear()
            fake.answer = cut_off()  # decoding stopped at the token limit

            r = await c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
            assert r.status_code == 200  # answered, flagged incomplete (was a 422)
            assert r.json()["incomplete"] is True

            ready = await c.get("/readyz")

    assert ready.status_code == 200  # the model answered, so it is alive
    assert len(fake.calls) == 1  # and the selftest (which this answer would fail) did not run


async def test_readyz_keeps_answering_503_for_a_model_that_reads_wrongly() -> None:
    # Reproduced before the fix: 503, then ready, ready, ready (what a 5 s healthcheck sees).
    wrong = FakePredictor(("CCO", [("C", 0.99), ("C", 0.99), ("O", 0.99)]))
    app = ocsr_app.build_app(OcsrModel(wrong))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            statuses = [(await c.get("/readyz")).status_code for _ in range(4)]
    assert statuses == [503, 503, 503, 503]


async def test_readyz_is_503_for_a_decode_that_has_hung() -> None:
    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    gate = threading.Event()
    fake = FakePredictor()
    app = ocsr_app.build_app(OcsrModel(fake, clock=clock))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 200
            fake.entered.clear()
            fake.gate = gate
            stuck = asyncio.create_task(
                c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
            )
            try:
                assert await asyncio.to_thread(fake.entered.wait, 10)
                assert (await c.get("/readyz")).status_code == 200  # busy, not hung
                clock.now += 91.0

                hung = await asyncio.wait_for(c.get("/readyz"), timeout=5)

                assert hung.status_code == 503
                assert "decode running for 91 s" in hung.json()["detail"]
            finally:
                gate.set()
            await stuck


async def test_readyz_stays_503_for_a_wrong_model_after_a_successful_infer() -> None:
    # The skeleton used to refresh its readiness cache on every 200 /infer, so a model that answers
    # wrongly but completely (wrong weights still decode) went 503, then ready under traffic.
    wrong = FakePredictor(("CCO", [("C", 0.99), ("C", 0.99), ("O", 0.99)]))
    app = ocsr_app.build_app(OcsrModel(wrong))  # the production wiring, with its LIMITS
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 503
            r = await c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
            assert r.status_code == 200  # it answers, with the wrong structure
            assert r.json()["smiles"] == "CCO"

            after = await c.get("/readyz")
            again = await c.get("/readyz")

    assert after.status_code == 503
    assert again.status_code == 503
    assert "selftest failed" in after.json()["detail"]


async def test_readyz_reaches_the_model_after_every_infer_but_a_correct_one_stays_ready(
    fake: FakePredictor,
) -> None:
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            assert (await c.get("/readyz")).status_code == 200
            for _ in range(3):
                r = await c.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
                assert r.status_code == 200
                assert (await c.get("/readyz")).status_code == 200
    assert len(fake.calls) == 1 + 3  # the first selftest and the three requests; no more selftests


async def test_an_idle_model_is_not_decoding_on_every_probe(fake: FakePredictor) -> None:
    # With the skeleton's cache off, the model has to cache its own pass or a 5 s healthcheck would
    # keep it decoding about half of the time.
    app = ocsr_app.build_app(OcsrModel(fake))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr") as c:
            statuses = [(await c.get("/readyz")).status_code for _ in range(6)]
    assert statuses == [200] * 6
    assert len(fake.calls) == 1


async def test_a_decode_at_the_token_limit_is_a_200_with_the_cut_off_smiles_flagged_incomplete(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    # Like the earlier Laravel version of decimer.ai, the cut-off SMILES is passed on; the answer
    # says so.
    # The library's pairs lack the last token; it comes back with confidence 0.0 ("not reported").
    fake.answer = cut_off("Cl", head=[("C", 0.5), ("C", 0.25)])

    r = await client.post("/infer", files={"image": ("crop.png", CROP, "image/png")})

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"smiles", "token_confidence", "mean_confidence", "incomplete"}
    assert body["incomplete"] is True
    assert body["smiles"] == "C" * 301 + "Cl"  # 302 tokens, the last one `Cl`
    assert body["token_confidence"][:2] == [["C", 0.5], ["C", 0.25]]
    assert body["token_confidence"][-2:] == [["C", 0.9], ["Cl", 0.0]]
    assert len(body["token_confidence"]) == 302
    assert body["mean_confidence"] == pytest.approx((0.5 + 0.25 + 299 * 0.9) / 302)


async def test_a_special_token_in_a_cut_off_answer_is_still_a_422(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    fake.answer = cut_off("4unk5")  # the dropped last token is the special one

    r = await client.post("/infer", files={"image": ("crop.png", CROP, "image/png")})

    assert r.status_code == 422
    assert r.json()["detail"] == "no complete structure: the model wrote a special token (4unk5)"


async def test_a_special_token_is_a_422_with_the_stable_prefix(
    client: httpx.AsyncClient, fake: FakePredictor
) -> None:
    fake.answer = ("C4unk5C", [("C", 0.9), ("4unk5", 0.9), ("C", 0.9)])
    r = await client.post("/infer", files={"image": ("crop.png", CROP, "image/png")})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("no complete structure")


async def test_healthz_names_the_model(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.json() == {"status": "ok", "model": "ocsr"}


def test_main_serves_the_ocsr_app_on_all_interfaces_and_port_from_env(
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
    assert getattr(started["app"], "title", None) == "decimer-ocsr"


def test_main_defaults_to_port_8002(monkeypatch: pytest.MonkeyPatch) -> None:
    started: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.update(kw))
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert started["port"] == 8002


def test_main_logs_are_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: logging.getLogger("t").warning("hello"))
    entry.main()
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert json.loads(line)["message"] == "hello"
