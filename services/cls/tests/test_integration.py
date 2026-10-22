"""The real EfficientNet classifier on real crops. Slow (TensorFlow, CPU): `pytest -m slow`.

Done-when for P2-04: the flags agree with the legacy site when both are given the SAME crops. The
crops and legacy's flags are the committed run `golden/runs/legacy-20261022-140954` (rows 0-19 have
a flag; rows 20-31 were not classified because legacy caps a job at 20 structures).
"""

import csv
import inspect
import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers import image_bytes

from decimer_cls import model as model_module
from decimer_cls.app import build_app
from decimer_cls.model import THRESHOLD, ClsModel

pytestmark = pytest.mark.slow
# One event loop for the whole module, so the real model is loaded once and shared by every test.
module_loop = pytest.mark.asyncio(loop_scope="module")

REPO = Path(__file__).resolve().parents[3]
RUN = REPO / "golden" / "runs" / "legacy-20261022-140954"


@dataclass(frozen=True)
class LegacyCrop:
    index: int
    path: Path
    legacy_is_structure: bool

    def __str__(self) -> str:
        return self.path.name


def _legacy_crops() -> list[LegacyCrop]:
    crops = []
    with (RUN / "predictions.csv").open(newline="") as handle:
        for row in csv.DictReader(handle):
            flag = row["classifier"]
            if flag == "":
                continue  # not classified by legacy
            assert flag in ("True", "False"), row
            crops.append(LegacyCrop(int(row["index"]), RUN / row["segment"], flag == "True"))
    return crops


CROPS = _legacy_crops()


@pytest.fixture(scope="module")
def model() -> ClsModel:
    return ClsModel()  # loaded by the lifespan of the `client` fixture, then shared


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(model: ClsModel) -> AsyncIterator[httpx.AsyncClient]:
    # The whole service path with the production limits: the lifespan loads the real model.
    app = build_app(model)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://cls", timeout=300) as c:
            yield c


def test_the_fixture_is_the_20_legacy_crops_with_both_answers_present() -> None:
    # Without both answers present, a classifier that always says one thing could pass.
    assert [c.index for c in CROPS] == list(range(20))
    assert all(c.path.is_file() for c in CROPS)
    assert {c.legacy_is_structure for c in CROPS} == {True, False}


@pytest.mark.usefixtures("client")  # the lifespan is what loads the model
def test_real_model_passes_its_own_selftest(model: ClsModel) -> None:
    # Called directly: the service counts a successful /infer as a readiness pass, so going through
    # /readyz after any request would not run the selftest at all.
    model.selftest()


@module_loop
@pytest.mark.parametrize("crop", CROPS, ids=str)
async def test_flag_agrees_with_legacy_on_the_same_crop(
    client: httpx.AsyncClient, crop: LegacyCrop
) -> None:
    r = await client.post(
        "/infer", files={"image": (crop.path.name, crop.path.read_bytes(), "image/png")}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"is_structure", "score"}
    assert body["is_structure"] is crop.legacy_is_structure, (
        f"{crop.path.name}: legacy says {crop.legacy_is_structure}, this service says "
        f"{body['is_structure']} (score {body['score']!r}, threshold {THRESHOLD})"
    )


def test_threshold_is_still_the_librarys_default() -> None:
    from decimer_image_classifier import DecimerImageClassifier

    default = inspect.signature(DecimerImageClassifier.is_chemical_structure).parameters[
        "threshold"
    ]
    assert default.default == THRESHOLD


@module_loop
async def test_a_request_adds_no_keras_progress_output(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # predict() draws a progress bar on stdout, or logs it via absl; neither belongs in the logs.
    crop = CROPS[0].path
    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/infer", files={"image": (crop.name, crop.read_bytes(), "image/png")}
        )
    assert r.status_code == 200
    assert "1/1" not in capsys.readouterr().out
    assert [rec.getMessage() for rec in caplog.records if rec.name == "absl"] == []


@module_loop
async def test_classifying_an_rgba_crop_writes_nothing_to_the_working_directory(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # decimer_image_classifier saves "caffeine_mod.png" into the cwd for every RGBA input.
    monkeypatch.chdir(tmp_path)
    crop = CROPS[0].path
    r = await client.post("/infer", files={"image": (crop.name, crop.read_bytes(), "image/png")})
    assert r.status_code == 200
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("client")
async def test_progress_output_is_silenced_on_a_thread_that_did_not_load_the_model(
    model: ClsModel, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    # Keras' interactive-logging switch is thread-local. The model was loaded on a worker thread of
    # the lifespan; a request can land on any other thread, so check one that is brand new.
    data = CROPS[0].path.read_bytes()
    results: list[dict[str, object]] = []
    thread = threading.Thread(target=lambda: results.append(model.infer(data, {})))
    with caplog.at_level(logging.INFO):
        thread.start()
        thread.join(timeout=120)

    assert len(results) == 1
    assert "1/1" not in capsys.readouterr().out
    assert [rec.getMessage() for rec in caplog.records if rec.name == "absl"] == []


@module_loop
async def test_a_pixel_mode_the_library_cannot_convert_is_a_422_on_the_real_service(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before the mode check, this reached the real library and came back as a 500.
    monkeypatch.setattr(model_module, "FORMATS", [*model_module.FORMATS, "TIFF"])
    lab = image_bytes(40, 40, fmt="TIFF", mode="LAB", color=(50, 0, 0))
    r = await client.post("/infer", files={"image": ("x.tif", lab, "image/tiff")})
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == "unsupported pixel mode: LAB"
