"""The `seg` model: finds chemical structures on one page image.

`import decimer_segmentation` loads TensorFlow but not the model: `decimer_segmentation.get_model()`
loads the Mask R-CNN weights, warms the model up and keeps it for every later call (and downloads
the weights if they are missing). This module therefore imports the library only inside
`SegModel.load()`, after checking that the weights are on disk, so importing the service module
stays fast and pure logic can be tested with an injected `segment_fn` and no TensorFlow.
"""

from __future__ import annotations

import base64
import contextlib
import io
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pystow
from numpy.typing import NDArray

log = logging.getLogger("decimer_seg")

Image = NDArray[np.uint8]
# (segments, bboxes): one crop per structure and its (y0, x0, y1, x1) box on the page.
SegmentFn = Callable[[Image], tuple[Sequence[Image], Sequence[Sequence[int]]]]

WEIGHTS_NAME = "mask_rcnn_molecule.h5"

# A call into the library that has run longer than this is hung, not slow: /readyz fails then. The
# pages measured take 3 to 8 s (2 to 4 threads); the biggest page the service admits (40 Mpx) has
# not been timed, so the limit is generous.
HANG_AFTER_SECONDS = 300.0


def weights_file() -> Path:
    """Where decimer_segmentation looks for (and, if missing, downloads) its weights."""
    return pystow.join("DECIMER-Segmentation_model", name=WEIGHTS_NAME, ensure_exists=False)


def _require_weights() -> None:
    """Refuse to start without the weights: `get_model()` would silently download 272 MB."""
    path = weights_file()
    if path.is_file():
        return
    if os.environ.get("SEG_ALLOW_WEIGHT_DOWNLOAD") == "1":
        log.warning("weights not found, letting decimer_segmentation download them: %s", path)
        return
    raise RuntimeError(
        f"segmentation weights not found at {path}. Put {WEIGHTS_NAME} there (the directory comes "
        "from PYSTOW_HOME, default ~/.data), or set SEG_ALLOW_WEIGHT_DOWNLOAD=1 to let "
        "decimer_segmentation download it from Zenodo."
    )


def _real_segment_fn() -> SegmentFn:
    # Importing the library does not load the model: `get_model()` does, once (the weights, then one
    # warm-up detection), and the library keeps it. Calling it here puts that at start-up and not
    # into the first request. The weights are on disk by now (`_require_weights`), so it downloads
    # nothing. What the library prints while loading goes to the log, not to stdout (before 1.6.0 it
    # printed the weights directory at import; 1.6.0 prints nothing when the weights are there).
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        import decimer_segmentation

        decimer_segmentation.get_model()
    if printed.getvalue().strip():
        log.info("decimer_segmentation printed: %s", printed.getvalue().strip())

    def segment(image: Image) -> tuple[Sequence[Image], Sequence[Sequence[int]]]:
        segments, bboxes = decimer_segmentation.segment_chemical_structures(
            image, expand=True, visualization=False, return_bboxes=True
        )
        return segments, bboxes

    return segment


_PAGE_INDEX = re.compile(r"[0-9]{1,5}")


def _parse_page_index(params: Mapping[str, str]) -> int:
    raw = params.get("page_index", "0")
    if not _PAGE_INDEX.fullmatch(raw):
        raise ValueError(f"page_index must be 1 to 5 ASCII digits, got {raw!r}")
    return int(raw)


def _to_bgr8(raw: NDArray[Any]) -> Image:
    """Whatever OpenCV decoded (gray, BGR or BGRA; 8 or 16 bit) as 8-bit BGR on a white page."""
    if raw.dtype == np.uint16:
        raw = (raw >> 8).astype(np.uint8)  # 65535 -> 255, and 257 * k -> k
    elif raw.dtype != np.uint8:
        raise ValueError(f"unsupported pixel format: {raw.dtype}")
    if raw.ndim == 2:
        return np.asarray(cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR), dtype=np.uint8)
    if raw.shape[2] == 3:
        return np.asarray(raw, dtype=np.uint8)
    if raw.shape[2] != 4:
        raise ValueError(f"unsupported number of channels: {raw.shape[2]}")
    bgr, alpha = raw[:, :, :3], raw[:, :, 3]
    if alpha.min() == 255:
        return np.ascontiguousarray(bgr)
    a = alpha[:, :, None].astype(np.uint16)
    on_white = (bgr.astype(np.uint16) * a + 255 * (255 - a) + 127) // 255
    return on_white.astype(np.uint8)


def _decode(data: bytes) -> Image:
    try:
        # UNCHANGED keeps the alpha channel and the bit depth; the price is that OpenCV then
        # also ignores EXIF orientation, so coordinates are in the stored pixel grid.
        raw = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    except cv2.error:  # empty buffer, or a size above OPENCV_IO_MAX_IMAGE_PIXELS
        raw = None
    if raw is None:
        raise ValueError("could not decode image")
    return _to_bgr8(raw)


def _encode_png(crop: Image) -> str:
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError("could not encode crop as PNG")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def selftest_png() -> bytes:
    """A white 512x512 page with one real structure (daidzein, cropped from the golden paper)."""
    return resources.files("decimer_seg").joinpath("selftest.png").read_bytes()


class SegModel:
    name = "seg"

    def __init__(
        self,
        segment_fn: SegmentFn | None = None,
        *,
        hang_after: float = HANG_AFTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._segment_fn = segment_fn
        # decimer_segmentation is not thread-safe: two segment_chemical_structures() calls at once
        # (Mask R-CNN's detect() on TensorFlow), even from just two threads, corrupt the heap and
        # abort the whole process, not just a request. The HTTP skeleton limits /infer to one call
        # at a time (`decimer_seg.app.LIMITS`) but runs /readyz's selftest outside that limit (so
        # a probe is not queued behind a slow request), and the two reach the library from
        # different threads: this lock is what keeps them apart, whoever the caller is.
        self._library_lock = threading.Lock()
        self._hang_after = hang_after
        self._clock = clock
        self._running_since: float | None = None  # set while a call is inside the library

    def load(self) -> None:
        if self._segment_fn is None:
            _require_weights()
            self._segment_fn = _real_segment_fn()

    def selftest(self) -> None:
        # /readyz can arrive while a page is being segmented. It must not call the library beside
        # that page (see `_library_lock`), and it has no reason to wait for it either: a page in
        # progress shows that the model is alive (the skeleton counts the request's own success as
        # a pass), unless that page has been going on for too long.
        if self._library_lock.locked():
            self._raise_if_hung()
            return
        # The whole path (decode, segment, encode) on a real structure; it must be found.
        if not self.infer(selftest_png(), {})["segments"]:
            raise RuntimeError("selftest: no structure found in the embedded test image")

    def _raise_if_hung(self) -> None:
        started = self._running_since
        if started is not None and (running := self._clock() - started) > self._hang_after:
            raise RuntimeError(
                f"selftest failed: a segmentation has been running for {int(running)} s"
            )

    def infer(self, image: bytes, params: Mapping[str, str]) -> dict[str, Any]:
        page_index = _parse_page_index(params)
        page = _decode(image)
        height, width = page.shape[:2]
        segments: list[dict[str, Any]] = []
        for crop, (y0, x0, y1, x1) in self._segment(page):
            if x1 <= x0 or y1 <= y0:
                continue  # the library answers an empty mask with a 1x1 crop and box (0, 0, 0, 0)
            segments.append(
                {
                    "index": len(segments),
                    "bbox": [int(x0), int(y0), int(x1), int(y1)],
                    "png": _encode_png(crop),
                }
            )
        return {"page_index": page_index, "width": width, "height": height, "segments": segments}

    def _segment(self, page: Image) -> list[tuple[Image, Sequence[int]]]:
        if self._segment_fn is None:
            raise RuntimeError("model not loaded; call load() first")
        with self._library_lock:
            self._running_since = self._clock()
            try:
                segments, bboxes = self._segment_fn(page)
            except ValueError as exc:
                # create_app maps ValueError to 422 (bad input); a failure in the library is ours.
                raise RuntimeError(f"segmentation failed: {exc}") from exc
            finally:
                self._running_since = None
        if len(segments) != len(bboxes):
            raise RuntimeError(
                f"segmentation returned {len(segments)} segments but {len(bboxes)} bboxes"
            )
        return list(zip(segments, bboxes, strict=True))
