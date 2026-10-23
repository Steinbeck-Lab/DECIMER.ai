"""Fakes and builders shared by the tests (nothing here imports TensorFlow)."""

from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]


def png_bytes(width: int, height: int, bgr: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    """A solid-colour PNG, `width` x `height` pixels."""
    image = np.empty((height, width, 3), dtype=np.uint8)
    image[:] = bgr
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return bytes(encoded.tobytes())


def encode_image(image: NDArray[Any], ext: str = ".png") -> bytes:
    """Encode any array OpenCV can write (8/16-bit, 1/3/4 channels, float for .tif)."""
    ok, encoded = cv2.imencode(ext, image)
    assert ok
    return bytes(encoded.tobytes())


def rgba_crop(height: int, width: int, seed: int = 0) -> Image:
    """A random 4-channel crop, so a round-trip cannot pass by accident."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(height, width, 4), dtype=np.uint8)


class FakeSegmenter:
    """Stands in for decimer_segmentation: returns canned (segments, bboxes) and records calls."""

    def __init__(
        self,
        segments: Sequence[Image] = (),
        bboxes: Sequence[Sequence[int]] = (),
        error: Exception | None = None,
    ) -> None:
        self.segments = list(segments)
        self.bboxes = list(bboxes)
        self.error = error
        self.calls: list[Image] = []

    def __call__(self, image: Image) -> tuple[Sequence[Image], Sequence[Sequence[int]]]:
        self.calls.append(image)
        if self.error is not None:
            raise self.error
        return self.segments, self.bboxes


class OverlapProbe(FakeSegmenter):
    """A FakeSegmenter that counts how many calls are inside it at the same moment.

    decimer_segmentation is not thread-safe: two `detect` calls at once corrupt the heap and abort
    the process. Each call here blocks until `release` is set, so a test can look at what else has
    started in the meantime.
    """

    def __init__(
        self, segments: Sequence[Image] = (), bboxes: Sequence[Sequence[int]] = ()
    ) -> None:
        super().__init__(segments, bboxes)
        self.release = threading.Event()
        self.inside = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, image: Image) -> tuple[Sequence[Image], Sequence[Sequence[int]]]:
        with self._lock:
            self.inside += 1
            self.peak = max(self.peak, self.inside)
        try:
            self.release.wait(timeout=5)
            time.sleep(0.002)  # a released call still takes a moment: overlaps stay visible
            return super().__call__(image)
        finally:
            with self._lock:
                self.inside -= 1

    def wait_until_inside(self, count: int = 1) -> None:
        for _ in range(500):
            if self.inside >= count:
                return
            time.sleep(0.01)
        raise AssertionError(f"timed out waiting for {count} call(s) inside the segmenter")


def iou(a: list[int], b: list[int]) -> float:
    """Intersection over union of two [x0, y0, x1, y1] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union


# --- the golden paper: shared by the slow tests that run the real model ------------------------

REPO = Path(__file__).resolve().parents[3]
PDF = REPO / "golden" / "zou2024_penicillium_shentong.pdf"
FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("page*_bboxes.json"))
MIN_IOU = 0.9


def load_fixture(path: Path) -> dict[str, Any]:
    fixture: dict[str, Any] = json.loads(path.read_text())
    return fixture


def render_page(page_index: int, dpi: int) -> bytes:
    import pymupdf  # only the slow tests need it

    with pymupdf.open(PDF) as document:
        return bytes(document[page_index].get_pixmap(dpi=dpi, alpha=False).tobytes("png"))


def assert_matches_fixture(body: dict[str, Any], expected: dict[str, Any]) -> None:
    """The answer for a page is the one a human checked: same boxes, same order, real crops."""
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
