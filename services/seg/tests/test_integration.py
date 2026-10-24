"""The real Mask R-CNN on real pages. Slow (CPU, TensorFlow): run with `pytest -m slow`.

Tip on a shared machine: `TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 nice pytest -m slow`.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import pytest_asyncio
from helpers import FIXTURES, assert_matches_fixture, load_fixture, render_page

from decimer_seg.app import build_app
from decimer_seg.model import _decode, _PredictOnBatch, selftest_png, weights_file

pytestmark = [pytest.mark.slow, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module", autouse=True)
def weights_are_on_disk() -> None:
    """Fail with the path instead of silently downloading 272 MB into someone's home directory."""
    path = weights_file()
    if not path.is_file() and os.environ.get("SEG_ALLOW_WEIGHT_DOWNLOAD") != "1":
        pytest.fail(
            f"segmentation weights are missing: expected {path}. Put mask_rcnn_molecule.h5 there "
            "(the directory is $PYSTOW_HOME/DECIMER-Segmentation_model), or set "
            "SEG_ALLOW_WEIGHT_DOWNLOAD=1 to let decimer_segmentation download them.",
            pytrace=False,
        )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # The whole service path with the production limits: the lifespan loads the model, and
    # /readyz runs the selftest on it.
    app = build_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://seg", timeout=300) as c:
            yield c


async def test_real_model_is_ready(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda p: p.stem)
async def test_boxes_match_the_ones_a_human_checked(
    client: httpx.AsyncClient, fixture_path: Path
) -> None:
    expected: dict[str, Any] = load_fixture(fixture_path)
    page_png = render_page(expected["page_index"], expected["dpi"])

    r = await client.post(
        "/infer",
        files={"image": ("page.png", page_png, "image/png")},
        data={"page_index": str(expected["page_index"])},
    )
    assert r.status_code == 200, r.text
    assert_matches_fixture(r.json(), expected)


async def test_the_leak_shim_does_not_change_what_the_model_finds(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `_PredictOnBatch` (decimer_seg.model) runs the library's forward pass as predict_on_batch to
    # stop a memory leak. The service's own start-up (the `client` fixture) put it on the model; the
    # same page goes through the library's `detect` with it and, with it taken off for a moment, the
    # way the pinned library calls the model on its own. The library has no other caller here: the
    # app is idle and this test is the only thread in it.
    import decimer_segmentation

    model = decimer_segmentation.get_model()
    shim = model.keras_model
    assert isinstance(shim, _PredictOnBatch)
    page = _decode(selftest_png())  # BGR, as segment_chemical_structures() hands it to detect()

    # Count the real model's predict_on_batch calls: both sides give the same answer when the shim
    # silently falls back to the direct call, so only the call itself shows that it ran.
    calls: list[Any] = []
    real_predict_on_batch = shim._keras_model.predict_on_batch

    def spy(inputs: Any) -> Any:
        calls.append(inputs)
        return real_predict_on_batch(inputs)

    monkeypatch.setattr(shim._keras_model, "predict_on_batch", spy)

    with_shim = model.detect([page], verbose=0)[0]
    assert len(calls) == 1  # one batch, through the shim

    model.keras_model = shim._keras_model
    try:
        without = model.detect([page], verbose=0)[0]
    finally:
        model.keras_model = shim
    assert len(calls) == 1  # the direct call does not go through predict_on_batch

    assert len(with_shim["rois"]) >= 1  # the self-test structure is found at all
    assert with_shim["rois"].shape == without["rois"].shape
    np.testing.assert_array_equal(with_shim["rois"], without["rois"])
    np.testing.assert_array_equal(with_shim["class_ids"], without["class_ids"])
    np.testing.assert_allclose(with_shim["scores"], without["scores"], rtol=0, atol=1e-6)
    assert with_shim["masks"].shape == without["masks"].shape
    assert np.count_nonzero(with_shim["masks"] != without["masks"]) <= 1
