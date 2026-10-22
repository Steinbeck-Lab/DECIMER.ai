"""The `cls` model: says whether one image shows a chemical structure.

`decimer_image_classifier` imports TensorFlow and loads its EfficientNetB0 SavedModel (shipped
inside the package) when `DecimerImageClassifier()` is built. This module therefore touches it only
inside `ClsModel.load()`, so importing the service module stays fast and the pure logic can be
tested with an injected `score_fn` and no TensorFlow at all.

Decision rule (identical to legacy, which calls `is_chemical_structure(img=path)`): the library
computes `score = sigmoid(logit)` and the image is a structure when `score <= THRESHOLD`. The score
is therefore a *non-structure* probability: low means "structure".

Intentional deviations from legacy, both in `_flatten`: images with transparency are composited
onto white here instead of by the library (identical pixels for RGBA, but without the library's
write of "caffeine_mod.png" into the working directory), and LA, palette-with-transparency and tRNS
images get the same treatment instead of having their alpha channel dropped.
"""

from __future__ import annotations

import io
import logging
import math
import threading
from collections.abc import Callable, Mapping
from typing import Any

from PIL import Image, ImageDraw

# `DecimerImageClassifier.is_chemical_structure(threshold=0.000089)`; a slow test checks that the
# installed library still has this default.
THRESHOLD = 0.000089

# Largest image, in pixels, that is decoded: D11, the most the API accepts per image. The
# single-image fallback sends the whole upload to `cls`, so a lower number would answer 422 for a
# valid 48 MP phone photo. Decoding and preprocessing 50 MP takes about 0.43 GB on top of the
# resident model (README: "Memory"). Crops from `seg` are far smaller.
MAX_PIXELS = 50_000_000

_INPUT_SIZE = 224

# Allow-list for Image.open. Pillow can open far more (EPS shells out to Ghostscript on load, for
# one); an upload has no business reaching those decoders. Crops are PNG, and the API vets uploads
# (PNG, JPEG, WebP, with HEIC converted before it gets here), so GIF and TIFF have no caller.
FORMATS = ["PNG", "JPEG", "WEBP", "BMP"]

# Pixel modes the library's grayscale conversion handles. It raises ValueError for others (LAB, La:
# "conversion from LAB to RGB not supported"), which would come back as a 500 for something the
# caller sent. The formats above cannot produce those modes today; this keeps it that way if a
# decoder or the allow-list changes.
SUPPORTED_MODES = frozenset(
    {"1", "L", "P", "RGB", "RGBA", "LA", "PA", "CMYK", "YCbCr", "I", "I;16", "F"}
)

ScoreFn = Callable[[Image.Image], float]


def _real_score_fn() -> ScoreFn:
    import tensorflow as tf
    from decimer_image_classifier import DecimerImageClassifier

    # Keras predict() draws a progress bar on stdout for every call. Interactive logging off moves
    # it to the "absl" logger as one INFO line per request, and that logger goes to WARNING so the
    # JSON log keeps only the service's own "inferred" line.
    logging.getLogger("absl").setLevel(logging.WARNING)
    classifier = DecimerImageClassifier()  # loads the SavedModel; sets CUDA_VISIBLE_DEVICES=0

    def score(image: Image.Image) -> float:
        # The switch is thread-local in Keras, and requests run on whichever worker thread is free,
        # so it has to be set here and not once in load().
        tf.keras.utils.disable_interactive_logging()
        return float(classifier.get_classifier_score(image))

    return score


def _decode(data: bytes) -> Image.Image:
    # Pillow has many failure modes on untrusted bytes (OSError, SyntaxError, struct/zlib errors,
    # DecompressionBombError, ...); all of them mean "this is not an image we can use".
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        width, height = image.size  # from the header; nothing is decoded yet
    except Exception:
        raise ValueError("could not decode image") from None
    if width * height > MAX_PIXELS:
        raise ValueError(f"image too large: {width}x{height} pixels, at most {MAX_PIXELS}")
    try:
        image.load()
    except Exception:
        raise ValueError("could not decode image") from None
    if image.mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported pixel mode: {image.mode}")
    return image


def _flatten(image: Image.Image) -> Image.Image:
    """Composite anything with transparency onto white; hand every other image over untouched.

    INTENTIONAL DEVIATION FROM LEGACY. The library does this for RGBA only, and by saving
    "caffeine_mod.png" into the working directory as it goes (not thread-safe, and a write into
    whatever the cwd is). Doing it here, the same way (paste with the alpha channel as mask), gives
    the library only opaque images and keeps its own grayscale conversion for them; for RGBA the
    pixels are identical to what legacy fed the network (a test compares them to the library's own
    recipe). LA, palette and tRNS images get the same treatment instead of showing their
    transparent pixels' hidden colour, which is where this differs from legacy.
    """
    if not image.has_transparency_data:
        return image
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")  # no copy when already RGBA
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba)  # an RGBA mask contributes its alpha band
    return flat


def _check_shape(image: Image.Image) -> None:
    # The library scales by `int(side * 224 / longest)`; a side that rounds to 0 makes its resize
    # raise. That is a property of the image (a 1 px wide strip), so it is a 422, not a 500.
    longest = max(image.size)
    if int(min(image.size) * (float(_INPUT_SIZE) / longest)) == 0:
        raise ValueError(f"image too elongated to classify: {image.width}x{image.height} pixels")


def _blank_image() -> Image.Image:
    return Image.new("RGB", (128, 128), (255, 255, 255))


def _selftest_image() -> Image.Image:
    """A white square with one drawn hexagon: the smallest thing that looks like a structure."""
    image = Image.new("RGB", (128, 128), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    corners = [
        (64 + 40 * math.cos(math.radians(60 * i)), 64 + 40 * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]
    draw.polygon(corners, outline=(0, 0, 0), width=3)
    return image


class ClsModel:
    name = "cls"

    def __init__(self, score_fn: ScoreFn | None = None) -> None:
        self._score_fn = score_fn
        # /readyz runs the selftest outside the /infer gate, so it can overlap a request.
        self._lock = threading.Lock()

    def load(self) -> None:
        if self._score_fn is None:
            self._score_fn = _real_score_fn()

    def selftest(self) -> None:
        # Both answers are checked, not just that the model runs: a model that says "yes" or "no"
        # to everything (wrong weights, a broken preprocessing step) would otherwise stay "ready".
        hexagon = self._score(_selftest_image())
        if hexagon > THRESHOLD:
            raise RuntimeError(
                f"selftest failed: a drawn hexagon scored {hexagon:.3g}, above the threshold "
                f"{THRESHOLD}, so the model does not call it a structure"
            )
        blank = self._score(_blank_image())
        if blank <= THRESHOLD:
            raise RuntimeError(
                f"selftest failed: a blank page scored {blank:.3g}, at or below the threshold "
                f"{THRESHOLD}, so the model calls it a structure"
            )

    def infer(self, image: bytes, params: Mapping[str, str]) -> dict[str, Any]:
        picture = _flatten(_decode(image))
        _check_shape(picture)
        score = self._score(picture)
        return {"is_structure": score <= THRESHOLD, "score": score}

    def _score(self, image: Image.Image) -> float:
        if self._score_fn is None:
            raise RuntimeError("model not loaded; call load() first")
        try:
            with self._lock:
                score = float(self._score_fn(image))
        except ValueError as exc:
            # create_app maps ValueError to 422 (bad input); a failure inside the library is ours.
            raise RuntimeError(f"classification failed: {exc}") from exc
        if not math.isfinite(score):
            raise RuntimeError(f"classifier returned a non-finite score: {score!r}")
        return score
