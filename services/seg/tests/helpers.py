"""Fakes and builders shared by the fast tests (nothing here imports TensorFlow)."""

from __future__ import annotations

from collections.abc import Sequence
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


def iou(a: list[int], b: list[int]) -> float:
    """Intersection over union of two [x0, y0, x1, y1] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(w, 0) * max(h, 0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union
