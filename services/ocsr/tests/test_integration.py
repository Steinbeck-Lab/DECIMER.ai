"""The real DECIMER Image Transformer on real crops. Slow (TensorFlow, CPU): `pytest -m slow`.

Done-when for P2-03: the crops of the golden-set paper give the labelled InChIKeys. The crops are
the ones of the committed legacy run `golden/runs/legacy-20261022-140954` (rows 0-19 are the ones
legacy processed, because it caps a job at 20 structures); `labels.csv` points at them through
`legacy_segment`. Legacy's output is NOT the reference (its model code is from 2023); it is only
reported beside ours.

Tip on a shared machine: `TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 nice pytest -m slow`.
Run with `-s` to see the comparison table.
"""

import io
import logging
import math
import os
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from golden_set import Crop, Label, crops, inchikey, labels_on_the_first_crops
from PIL import Image

from decimer_ocsr import model as model_module
from decimer_ocsr.app import build_app
from decimer_ocsr.model import OcsrModel, weights_dir

pytestmark = pytest.mark.slow
# One event loop for the whole module, so the real model is loaded once and shared by every test.
module_loop = pytest.mark.asyncio(loop_scope="module")

CROPS = crops()
LABELS = labels_on_the_first_crops()


@pytest.fixture(scope="module", autouse=True)
def weights_are_on_disk() -> None:
    """Fail with the path instead of silently downloading 600 MB into someone's home directory."""
    path = weights_dir()
    if (
        os.environ.get("OCSR_ALLOW_WEIGHT_DOWNLOAD") != "1"
        and not (path / "DECIMER_model" / "saved_model.pb").is_file()
    ):
        pytest.fail(
            f"DECIMER weights are missing: expected DECIMER_model/ and DECIMER_HandDrawn_model/ "
            f"in {path} (the directory comes from PYSTOW_HOME), or set "
            "OCSR_ALLOW_WEIGHT_DOWNLOAD=1 to let DECIMER download them.",
            pytrace=False,
        )


@pytest.fixture(scope="module")
def model() -> OcsrModel:
    return OcsrModel()  # loaded by the lifespan of the `client` fixture, then shared


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client(model: OcsrModel) -> AsyncIterator[httpx.AsyncClient]:
    # The whole service path with the production limits: the lifespan loads the real model.
    app = build_app(model)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocsr", timeout=600) as c:
            yield c


@dataclass(frozen=True)
class Read:
    """One crop through the service: the status, and the body (a prediction or an error)."""

    crop: Crop
    status: int
    body: dict[str, Any]
    seconds: float

    @property
    def smiles(self) -> str:
        return str(self.body.get("smiles", ""))

    @property
    def inchikey(self) -> str | None:
        return inchikey(self.smiles)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def reads(client: httpx.AsyncClient) -> list[Read]:
    """Every legacy crop through the real service, once."""
    out = []
    for crop in CROPS:
        start = time.perf_counter()
        r = await client.post(
            "/infer", files={"image": (crop.path.name, crop.path.read_bytes(), "image/png")}
        )
        out.append(Read(crop, r.status_code, r.json(), time.perf_counter() - start))
    return out


def predictions(reads: list[Read]) -> list[Read]:
    return [r for r in reads if r.status == 200]


def verdict(label: Label | None, key: str | None) -> str:
    if label is None:
        return "no label"
    return "correct" if key == label.inchikey else "WRONG"


def table(reads: list[Read]) -> str:
    cols = "{:>4} {:>6} {:>8} {:>8} {:>6} {:>6}  {}"
    lines = [cols.format("crop", "label", "legacy", "new", "mean", "sec", "new SMILES or error")]
    for read in reads:
        label = LABELS.get(read.crop.index)
        shown = "-" if label is None else label.number + (" *" if label.checked else "")
        ok = read.status == 200
        lines.append(
            cols.format(
                read.crop.index,
                shown,
                verdict(label, read.crop.legacy_inchikey),
                verdict(label, read.inchikey),
                f"{read.body['mean_confidence']:.3f}" if ok else "-",
                f"{read.seconds:.1f}",
                read.smiles[:60] if ok else f"{read.status} {read.body.get('detail')}",
            )
        )
    return "\n".join(lines)


def test_the_fixture_is_the_20_legacy_crops_and_the_labels_that_point_at_them() -> None:
    assert [c.index for c in CROPS] == list(range(20))
    assert all(c.path.is_file() for c in CROPS)
    # 26 labels in all; 19 of them sit on one of the first 20 crops (7 are on crops legacy never
    # processed), and 6 of those 19 are the PubChem-confirmed ones.
    assert len(LABELS) == 19
    assert sum(label.checked for label in LABELS.values()) == 6


@pytest.mark.usefixtures("client")  # the lifespan is what loads the model
def test_real_model_passes_its_own_selftest(model: OcsrModel) -> None:
    # Called directly: the service counts a successful /infer as a readiness pass, so going through
    # /readyz after any request would not run the selftest at all.
    model.selftest()


def test_checked_labels_get_their_inchikey_from_the_crops(
    reads: list[Read], capsys: pytest.CaptureFixture[str]
) -> None:
    """P2-03 done-when: assert every label with checked=yes, report the others."""
    with capsys.disabled():
        print("\n" + table(reads))
        unchecked = [(i, label) for i, label in LABELS.items() if not label.checked]
        right = sum(1 for i, label in unchecked if reads[i].inchikey == label.inchikey)
        print(f"\nunchecked labels (reported, not asserted): {right} of {len(unchecked)} correct")
    failures = [
        f"crop {i}, label {label.number}: expected {label.inchikey}, got {reads[i].inchikey} "
        f"({reads[i].smiles or reads[i].body})"
        for i, label in LABELS.items()
        if label.checked and reads[i].inchikey != label.inchikey
    ]
    assert failures == []


def test_a_crop_is_either_read_or_refused_as_cut_off_never_a_server_error(
    reads: list[Read],
) -> None:
    # Crop 15 is a figure with correlation arrows: the model never writes an end token for it.
    for read in reads:
        if read.status != 200:
            assert read.status == 422, (read.crop.path.name, read.status, read.body)
            assert read.body["detail"].startswith("no complete structure")
    assert len(predictions(reads)) >= 15


def test_every_token_has_a_confidence_in_zero_to_one(reads: list[Read]) -> None:
    for read in predictions(reads):
        assert set(read.body) == {"smiles", "token_confidence", "mean_confidence"}
        pairs = read.body["token_confidence"]
        assert pairs, read.crop.path.name
        assert "".join(token for token, _ in pairs) == read.smiles
        for token, confidence in pairs:
            assert isinstance(token, str)
            assert token != ""
            assert isinstance(confidence, float)
            assert 0.0 <= confidence <= 1.0, (read.crop.path.name, token, confidence)
        mean = sum(c for _, c in pairs) / len(pairs)
        assert read.body["mean_confidence"] == pytest.approx(mean, abs=1e-12)
        assert 0.0 < read.body["mean_confidence"] <= 1.0


def test_the_confidence_is_not_a_constant(reads: list[Read]) -> None:
    means = [r.body["mean_confidence"] for r in predictions(reads)]
    assert max(means) - min(means) > 0.05


@pytest.mark.usefixtures("client")  # the library is imported (and the models loaded) by now
def test_the_installed_library_still_uses_the_urls_and_files_we_check() -> None:
    from DECIMER import decimer

    assert {f"{k}_model": v for k, v in decimer.model_urls.items()} == model_module.MODEL_URLS
    assert Path(str(decimer.default_path)) == weights_dir()


@pytest.mark.usefixtures("client")
def test_the_array_route_gives_what_the_librarys_own_file_route_gives(model: OcsrModel) -> None:
    # No temporary file is written because predict_SMILES takes an array; this is the check that
    # doing so changes nothing: same SMILES, same confidence for every token.
    from DECIMER import predict_SMILES

    for crop in (CROPS[0], CROPS[10], CROPS[16]):
        ours = model.infer(crop.path.read_bytes(), {})
        smiles, pairs = predict_SMILES(str(crop.path), confidence=True)
        assert ours["smiles"] == smiles
        assert ours["token_confidence"] == [[t, float(c)] for t, c in pairs]


@pytest.mark.usefixtures("client")
@pytest.mark.parametrize("mode", ["RGBA", "L", "P", "1"])
def test_the_same_crop_in_other_pixel_modes_reads_the_same(model: OcsrModel, mode: str) -> None:
    gray = Image.open(CROPS[10].path).convert("L")
    if mode == "1":
        gray = gray.point(lambda v: 255 if v > 200 else 0)  # type: ignore[operator]
    expected = model.infer(_png(gray.convert("RGB")), {})["smiles"]

    if mode == "1":
        converted = gray.convert("1")
    elif mode == "P":
        converted = gray.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE)
    else:
        converted = gray.convert(mode)
    assert model.infer(_png(converted), {})["smiles"] == expected


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.usefixtures("client")
@pytest.mark.parametrize(
    ("name", "make"),
    [
        ("1x1 black", lambda: Image.new("RGB", (1, 1), "black")),
        ("blank page", lambda: Image.new("RGB", (300, 300), "white")),
        ("thin strip", lambda: Image.new("RGB", (1, 2000), "white")),
    ],
)
def test_degenerate_images_are_answered_not_crashed(model: OcsrModel, name: str, make: Any) -> None:
    # Whatever the model makes of them (each takes the full 302 steps, about 45 s, and ends at the
    # token limit), a caller gets a prediction or a 422, never a 500.
    try:
        result = model.infer(_png(make()), {})
    except ValueError as exc:
        assert str(exc).startswith("no complete structure"), name
        return
    assert set(result) == {"smiles", "token_confidence", "mean_confidence"}, name
    assert math.isfinite(result["mean_confidence"])


@module_loop
async def test_a_request_adds_no_library_output_on_stdout_or_in_the_logs(
    client: httpx.AsyncClient, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    crop = CROPS[0].path
    with caplog.at_level(logging.INFO):
        r = await client.post(
            "/infer", files={"image": (crop.name, crop.read_bytes(), "image/png")}
        )
    assert r.status_code == 200
    assert capsys.readouterr().out == ""
    assert [rec.getMessage() for rec in caplog.records if rec.name == "absl"] == []


@module_loop
async def test_a_request_writes_nothing_to_the_working_directory(
    client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    crop = CROPS[0].path
    r = await client.post("/infer", files={"image": (crop.name, crop.read_bytes(), "image/png")})
    assert r.status_code == 200
    assert list(tmp_path.iterdir()) == []


@pytest.mark.usefixtures("client")
async def test_it_also_works_on_a_thread_that_did_not_load_the_model(model: OcsrModel) -> None:
    data = CROPS[0].path.read_bytes()
    results: list[dict[str, Any]] = []
    thread = threading.Thread(target=lambda: results.append(model.infer(data, {})))
    thread.start()
    thread.join(timeout=300)
    assert len(results) == 1
    assert results[0]["smiles"]
