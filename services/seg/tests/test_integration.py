"""The real Mask R-CNN on real pages. Slow (CPU, TensorFlow): run with `pytest -m slow`.

Tip on a shared machine: `TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 nice pytest -m slow`.
"""

import base64
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
import pymupdf
import pytest
import pytest_asyncio
from helpers import iou

from decimer_seg.app import build_app
from decimer_seg.model import weights_file

pytestmark = [pytest.mark.slow, pytest.mark.asyncio(loop_scope="module")]

REPO = Path(__file__).resolve().parents[3]
PDF = REPO / "golden" / "zou2024_penicillium_shentong.pdf"
FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("page*_bboxes.json"))
MIN_IOU = 0.9


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


def render(page_index: int, dpi: int) -> bytes:
    with pymupdf.open(PDF) as document:
        return bytes(document[page_index].get_pixmap(dpi=dpi, alpha=False).tobytes("png"))


async def test_real_model_is_ready(client: httpx.AsyncClient) -> None:
    r = await client.get("/readyz")
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("fixture_path", FIXTURES, ids=lambda p: p.stem)
async def test_boxes_match_the_ones_a_human_checked(
    client: httpx.AsyncClient, fixture_path: Path
) -> None:
    expected: dict[str, Any] = json.loads(fixture_path.read_text())
    page_png = render(expected["page_index"], expected["dpi"])

    r = await client.post(
        "/infer",
        files={"image": ("page.png", page_png, "image/png")},
        data={"page_index": str(expected["page_index"])},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert (body["width"], body["height"]) == (expected["width"], expected["height"])
    assert body["page_index"] == expected["page_index"]
    got = [s["bbox"] for s in body["segments"]]
    assert len(got) == len(expected["bboxes"]), got
    assert [s["index"] for s in body["segments"]] == list(range(len(got)))
    # Same reading order as the reference: index i must be the structure the human checked as i.
    scores = [iou(g, e) for g, e in zip(got, expected["bboxes"], strict=True)]
    assert all(s >= MIN_IOU for s in scores), [round(s, 3) for s in scores]

    for segment in body["segments"]:
        x0, y0, x1, y1 = segment["bbox"]
        crop = cv2.imdecode(
            np.frombuffer(base64.b64decode(segment["png"]), np.uint8), cv2.IMREAD_UNCHANGED
        )
        assert crop.shape == (y1 - y0, x1 - x0, 4), segment["index"]
