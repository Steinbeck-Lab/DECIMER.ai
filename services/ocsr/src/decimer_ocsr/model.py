"""The `ocsr` model: reads one chemical structure depiction and returns its SMILES.

`DECIMER` (decimer 2.8.1) imports TensorFlow and nothing else when it is imported; its models are
downloaded from Zenodo (if they are missing) and loaded by `DECIMER.load_models()`, or by the first
`predict_SMILES()` if that was not called. (decimer 2.8.0 did all of it, for both the printed-
structure and the hand-drawn model, as a side effect of `import DECIMER`.) This module therefore
imports the library only inside `OcsrModel.load()`, after checking that the weights are on disk (the
service never downloads), and has it load the printed-structure model there and then, so the first
request does not pay for it and /readyz's selftest finds a loaded model. Importing the service
module stays fast and the pure logic can be tested with an injected `predict_fn` and no TensorFlow.

The service calls `DECIMER.predict_SMILES(array, confidence=True, hand_drawn=False)`. The library
accepts a numpy array as well as a path, so the request bytes are decoded here (strictly, see
`_decode`) and handed over as the RGBA array that `Image.open(path).convert("RGBA")` would give;
no temporary file is written. The hand-drawn model is never loaded (not by `load_models` as called
here, not by `predict_SMILES(hand_drawn=False)`), and nothing in a request can select it.

Response of `infer` (always exactly these four keys, all plain Python types):

    {"smiles": "C1=CC(=CC=C1C2=COC3=CC(=CC=C3C2=O)O)O",
     "token_confidence": [["C", 0.99...], ["1", 0.99...], ...],
     "mean_confidence": 0.99...,
     "incomplete": false}

- `token_confidence` has one `[token, confidence]` pair per SMILES token, in order. The tokens are
  the model's own (`Cl` is one token, `C` and `l` are not) and concatenate to exactly `smiles`.
  The confidence is the decoder's softmax probability of the token it chose, in [0, 1].
- `mean_confidence` is the arithmetic mean of those confidences. An empty prediction (the model
  answers "end" at once) is `{"smiles": "", "token_confidence": [], "mean_confidence": 0.0,
  "incomplete": false}`.
- `incomplete` is true only when decoding stopped at the model's token limit (302) without an end
  token: `smiles` is then cut off in the middle, as the earlier Laravel version of decimer.ai
  returned it, and the caller is told so. The library does not report the confidence of the last
  token in that case; the pair for it is `[token, 0.0]` ("not reported"), so the tokens still
  concatenate to `smiles`.
"""

from __future__ import annotations

import contextlib
import io
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
import pystow
from numpy.typing import NDArray
from PIL import Image

log = logging.getLogger("decimer_ocsr")

Array = NDArray[np.uint8]
# (smiles, [(token, confidence), ...]), as `DECIMER.predict_SMILES(..., confidence=True)` returns.
Prediction = tuple[str, Sequence[tuple[str, float]]]
PredictFn = Callable[[Array], Prediction]

# Largest image that is decoded, checked from the header before any pixel is read (README "Memory"
# has the measurements). Crops from `seg` are a few hundred pixels on a side; the single-image
# fallback can send a whole upload.
#  - MAX_PIXELS: A3 at 300 DPI is 17.4 Mpx. A rendered page of this size costs 0.3 to 0.45 GB on
#    top of the resident model; a 50 Mpx one (the API's upload cap) costs 0.9 GB. DECIMER reads a
#    512 x 512 thumbnail, so the worker can downscale a larger single image without losing any.
#  - MAX_SIDE: DECIMER pads the image to a square of 1.2 x its longest side before it shrinks it, so
#    memory follows the square of the longest side, not the pixel count: a 100,000 x 100 strip is
#    only 10 Mpx but would need a 120,000 x 120,000 canvas (14 GB). 8192 px (a 48 Mpx phone photo
#    is 8064 px long) costs 345 to 448 MB for a framed, noisy image through the whole service.
MAX_PIXELS = 17_500_000
MAX_SIDE = 8192

# Allow-list for Image.open. Pillow can open far more (EPS shells out to Ghostscript on load, for
# one); an upload has no business reaching those decoders. Crops are PNG, and the API vets uploads
# (PNG, JPEG, WebP, with HEIC converted before it gets here).
FORMATS = ["PNG", "JPEG", "WEBP", "BMP"]

# Pixel modes that become RGBA correctly. Pillow raises ValueError for others (LAB, F, 32-bit I:
# "conversion from LAB to RGBA not supported"), which would come back as a 500 for something the
# caller sent. The formats above cannot produce those modes today; this keeps it that way if a
# decoder or the allow-list changes. "I;16" (16-bit grayscale PNG) is handled in `_to_array`.
SUPPORTED_MODES = frozenset({"1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "YCbCr", "I;16"})

# --- weights --------------------------------------------------------------------------------

# `DECIMER.decimer.model_urls["DECIMER"]` in decimer 2.8.1 (a slow test checks the installed library
# still has it). The library keeps the URL it downloaded from in `<model dir>/.model_url` and
# downloads again, after deleting the directory, when the file says something else. Only the
# printed-structure model is listed: the library does not look at the hand-drawn directory unless
# `hand_drawn=True` is asked for, which this service never does (the image still carries those
# weights, so that a hand-drawn option stays possible, but nothing checks or reads them).
MODEL_URLS = {
    "DECIMER_model": "https://zenodo.org/record/8300489/files/models.zip",
}

# What `DECIMER.load_models(printed=True, hand_drawn=False)` reads: the printed-structure SavedModel
# and its tokenizer (which decodes the output of either model).
REQUIRED_FILES = {
    "DECIMER_model": (
        "saved_model.pb",
        "assets/tokenizer_SMILES.pkl",
        "variables/variables.index",
        "variables/variables.data-00000-of-00001",
    ),
}


def weights_dir() -> Path:
    """Where decimer 2.8.1 looks for (and, if missing, downloads) its weights."""
    return pystow.join("DECIMER-V2", ensure_exists=False)


def _weight_problems(root: Path) -> list[str]:
    problems = []
    for directory, files in REQUIRED_FILES.items():
        for name in files:
            if not (root / directory / name).is_file():
                problems.append(f"missing: {root / directory / name}")
        marker = root / directory / ".model_url"
        if marker.is_file() and marker.read_text().strip() != MODEL_URLS[directory]:
            problems.append(
                f"{marker} names another download URL than decimer 2.8.1 expects "
                f"({MODEL_URLS[directory]}), so the library would delete the model and download it"
            )
    return problems


def _require_weights() -> None:
    """Refuse to start without the weights: loading the models would download 300 MB."""
    root = weights_dir()
    problems = _weight_problems(root)
    if not problems:
        return
    if os.environ.get("OCSR_ALLOW_WEIGHT_DOWNLOAD") == "1":
        log.warning("weights not usable, letting DECIMER download them: %s", "; ".join(problems))
        return
    raise RuntimeError(
        f"DECIMER weights not found in {root} ({'; '.join(problems)}). Put DECIMER_model/ there "
        "(the directory comes from PYSTOW_HOME, default ~/.data), or set "
        "OCSR_ALLOW_WEIGHT_DOWNLOAD=1 to let DECIMER download it from Zenodo."
    )


def _real_predict_fn() -> PredictFn:
    # The library prints download progress and paths while it loads.
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            from DECIMER import load_models, predict_SMILES

            # Now, not at the first request: the tokenizer and the printed-structure model (about
            # 2 GB resident and 20 to 30 s). The hand-drawn model is not loaded and no request
            # can ask for it.
            load_models(printed=True, hand_drawn=False)
    finally:
        # Also when this raises: a failed download or a corrupt file is explained by what the
        # library printed just before.
        if printed.getvalue().strip():
            log.info("DECIMER printed: %s", printed.getvalue().strip())

    def predict(array: Array) -> Prediction:
        smiles, pairs = predict_SMILES(array, confidence=True, hand_drawn=False)
        return smiles, pairs

    return predict


# --- decoding -------------------------------------------------------------------------------


def _decode(data: bytes) -> Image.Image:
    # Pillow has many failure modes on untrusted bytes (OSError, SyntaxError, struct/zlib errors,
    # ...); all of them mean "this is not an image we can use".
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        width, height = image.size  # from the header; nothing is decoded yet
    except Image.DecompressionBombError:
        raise ValueError("image too large") from None
    except Exception:
        raise ValueError("could not decode image") from None
    if width * height > MAX_PIXELS:
        raise ValueError(f"image too large: {width}x{height} pixels, at most {MAX_PIXELS}")
    if max(width, height) > MAX_SIDE:
        raise ValueError(
            f"image too long: {width}x{height} pixels, longest side at most {MAX_SIDE}"
        )
    try:
        image.load()
    except Exception:
        raise ValueError("could not decode image") from None
    if image.mode not in SUPPORTED_MODES:
        raise ValueError(f"unsupported pixel mode: {image.mode}")
    return image


def _to_array(image: Image.Image) -> Array:
    """The pixels as `Image.open(path).convert("RGBA")` gives them, which is what DECIMER reads."""
    if image.mode == "I;16":
        # Pillow's conversion clips values above 255, which would turn a 16-bit crop all white.
        image = Image.fromarray((np.asarray(image) >> 8).astype(np.uint8))
    return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def selftest_png() -> bytes:
    """The structure `selftest()` reads (see SELFTEST_SMILES), as a small PNG."""
    return resources.files("decimer_ocsr").joinpath("selftest.png").read_bytes()


# `selftest.png` is daidzein, the crop of golden-set label 11 (a PubChem-confirmed structure) as the
# segmentation cuts it out: RGBA, 434 x 198. SELFTEST_SMILES is what decimer 2.8.1 reads from it
# (all 37 tokens at 0.92 or better); a test checks with RDKit that it is daidzein.
SELFTEST_SMILES = "C1=CC(=CC=C1C2=COC3=CC(=CC=C3C2=O)O)O"
SELFTEST_INCHIKEY = "ZQSIJRDFPHDXIC-UHFFFAOYSA-N"


# Every 422 that says "the image gave no usable structure" starts with this, so the worker and the
# UI can tell it from the other 422s (undecodable, too large, ...) without parsing the rest.
NO_STRUCTURE = "no complete structure"

# Tokens that are not part of a SMILES. `<unk>` is something outside the model's vocabulary; the
# others should never be written mid-structure. `DECIMER.utils.decoder` maps "<" to "4" and ">" to
# "5" in every token of the confidence list (and of the SMILES), so "<unk>" arrives as "4unk5";
# both spellings are refused. Whole tokens only: "4" and "5" on their own are ordinary tokens.
SPECIAL_TOKENS = frozenset(
    spelling for name in ("pad", "unk", "start", "end") for spelling in (f"<{name}>", f"4{name}5")
)


# The detail of the 422 for a decode that stopped at the token limit, which `_result` no longer
# raises (such a prediction is returned, flagged `incomplete`). The selftest still refuses it.
TOKEN_LIMIT_DETAIL = f"{NO_STRUCTURE}: too long or not a structure"

# The model decodes at most this many tokens (`assets/max_length.pkl`; README "The token limit"). A
# decode that reaches it without an end token has all of them in `smiles` and, because decimer
# 2.8.1 cuts `[1:-1]` of the confidence array, the confidences of all but the last: one fewer.
TOKEN_LIMIT = 302

# The vocabulary has 156 entries: 57 single characters, the four special tokens and these 95
# element symbols of two letters (a slow test compares them with the library's tokenizer). Every
# other token is one character, so a text is one token if it is one character or one of these.
MULTI_CHAR_TOKENS = frozenset(
    {
        "Ac", "Ag", "Al", "Am", "Ar", "As", "At", "Au", "Ba", "Be", "Bh", "Bi", "Bk", "Br", "Ca",
        "Cd", "Ce", "Cf", "Cl", "Cm", "Co", "Cr", "Cs", "Cu", "Db", "Dy", "Er", "Es", "Eu", "Fe",
        "Fm", "Fr", "Ga", "Gd", "Ge", "He", "Hf", "Hg", "Ho", "Hs", "In", "Ir", "Kr", "La", "Li",
        "Lr", "Lu", "Md", "Mg", "Mn", "Mo", "Mt", "Na", "Nb", "Nd", "Ne", "Ni", "No", "Np", "Os",
        "Pa", "Pb", "Pd", "Pm", "Po", "Pr", "Pt", "Pu", "Ra", "Rb", "Re", "Rf", "Rh", "Rn", "Ru",
        "Sb", "Sc", "Se", "Sg", "Si", "Sm", "Sn", "Sr", "Ta", "Tb", "Tc", "Te", "Th", "Ti", "Tl",
        "Tm", "Xe", "Yb", "Zn", "Zr",
    }
)  # fmt: skip


def _result(smiles: object, pairs: Sequence[tuple[str, float]]) -> dict[str, Any]:
    if not isinstance(smiles, str):
        raise RuntimeError(f"DECIMER returned a {type(smiles).__name__} instead of a SMILES string")
    tokens = [str(token) for token, _ in pairs]
    confidences = [float(confidence) for _, confidence in pairs]  # numpy float32 -> float
    if not all(math.isfinite(c) for c in confidences):
        raise RuntimeError(f"DECIMER returned a non-finite confidence for {smiles!r}")
    for token in tokens:
        if token in SPECIAL_TOKENS:
            # Before the check below: the library deletes "<start>" and "<end>" from the SMILES
            # but not from the pairs, so such a token also makes them differ.
            raise ValueError(f"{NO_STRUCTURE}: the model wrote a special token ({token})")
    joined = "".join(tokens)
    incomplete = joined != smiles
    if incomplete:
        if not smiles.startswith(joined):
            raise RuntimeError(f"tokens do not add up to the SMILES {smiles!r}")
        # decimer 2.8.1 drops the last token's confidence when decoding stops at the model's
        # token limit (302) without an end token, so the pairs are the SMILES minus its last
        # token. Such a SMILES is cut off in the middle: no structure was read to the end. The
        # earlier Laravel version of decimer.ai kept it and let CDK and RDKit judge it, so it is
        # returned, flagged `incomplete`, with the dropped token put back at confidence 0.0 (not
        # reported) so that the tokens still concatenate to the SMILES.
        last = smiles[len(joined) :]
        if last in SPECIAL_TOKENS:  # the token the pairs lack is not checked above
            raise ValueError(f"{NO_STRUCTURE}: the model wrote a special token ({last})")
        # Only exactly that case is flagged. Anything else that is a prefix (more than one token
        # missing, or fewer confidences than the limit allows) means the library changed.
        if len(pairs) != TOKEN_LIMIT - 1:
            raise RuntimeError(
                f"DECIMER returned {len(pairs)} confidences for a SMILES that is longer than "
                f"they are; a decode cut off at the token limit has {TOKEN_LIMIT - 1}"
            )
        if len(last) != 1 and last not in MULTI_CHAR_TOKENS:
            raise RuntimeError(f"the cut-off SMILES ends in {last!r}, which is not one token")
        tokens.append(last)
        confidences.append(0.0)
    mean = math.fsum(confidences) / len(confidences) if confidences else 0.0
    return {
        "smiles": smiles,
        "token_confidence": [[t, c] for t, c in zip(tokens, confidences, strict=True)],
        "mean_confidence": mean,
        "incomplete": incomplete,
    }


# How long a passed selftest or a completed request keeps the model "ready" for the next probes.
READY_WINDOW = 30.0


class OcsrModel:
    name = "ocsr"

    def __init__(
        self,
        predict_fn: PredictFn | None = None,
        *,
        ready_window: float = READY_WINDOW,
        pass_interval: float = 300.0,
        hang_after: float = 90.0,
        lock_wait: float = 25.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._predict_fn = predict_fn
        # /readyz runs the selftest outside the /infer gate, so it can overlap a request.
        self._lock = threading.Lock()
        # How long a passed selftest or a completed request keeps the model "ready". The model's
        # own policy: the skeleton's readiness cache is off for this service (`app.LIMITS`), so
        # every /readyz reaches `selftest()`.
        self.ready_window = ready_window
        # A real selftest pass is needed at least this often: because every /readyz reaches
        # `selftest()`, completed requests (which the skeleton would count as a pass) cannot stand
        # in for it for longer than this.
        self.pass_interval = pass_interval
        # A decode running longer than this is hung: the longest legitimate one (at the token
        # limit) takes 38 to 55 s.
        self.hang_after = hang_after
        # How long an overdue selftest waits for a busy model; the container's healthcheck allows
        # 35 s per probe.
        self.lock_wait = lock_wait
        self._clock = clock
        self._last_pass = -math.inf  # the last real selftest that passed
        self._last_request_done = -math.inf  # the last decode for a request that completed
        self._decode_started: float | None = None  # set while a decode (any) is running

    def load(self) -> None:
        if self._predict_fn is None:
            _require_weights()
            self._predict_fn = _real_predict_fn()

    def selftest(self) -> None:
        # /readyz must not queue behind a decode: one that ends at the token limit holds the model
        # for 38-55 s (and answers 200 with an `incomplete` prediction, which says
        # nothing about the model being right); a selftest that waited for it would time out and
        # the container would turn unhealthy. But it must not take "busy" or "a request
        # completed" for "correct" either.
        #   - Model busy: alive, unless the decode has run for more than `hang_after`.
        #   - A selftest passed, or a request completed, within `ready_window`: no need to decode
        #     again (the skeleton's cache would have done this; it is off, see `app.LIMITS`). A
        #     completed request says nothing about the answer being right, so a real selftest is
        #     still required every `pass_interval`.
        #   - Otherwise (or when overdue) the real selftest runs. Only a *passed* selftest is
        #     remembered, and only after its answer is checked; the selftest's own decode is not a
        #     completed request, so a failure keeps failing on the next probe.
        if not self._lock.acquire(blocking=False):
            self._raise_if_hung()
            if self._clock() - self._last_pass <= self.pass_interval:
                return
            if not self._lock.acquire(timeout=self.lock_wait):
                age = self._clock() - self._last_pass
                since = (
                    "no real selftest pass yet"
                    if math.isinf(age)
                    else f"no real selftest pass for {int(age)} s"
                )
                raise RuntimeError(
                    f"selftest could not run: {since}, and the model was busy for "
                    f"{int(self.lock_wait)} s"
                )
        try:
            now = self._clock()
            fresh = max(self._last_pass, self._last_request_done)
            if now - self._last_pass <= self.pass_interval and now - fresh <= self.ready_window:
                return
            smiles, pairs = self._call(_to_array(_decode(selftest_png())), is_request=False)
        finally:
            self._lock.release()
        # The answer is checked, not just that the model runs: wrong weights or a broken tokenizer
        # would otherwise stay "ready" while returning nonsense. Exact string, not canonical form:
        # decoding is greedy and deterministic, and a change of library or TensorFlow that alters
        # it has to be noticed (and go through the golden-set check) anyway.
        try:
            result = _result(smiles, pairs)
        except ValueError as exc:
            raise RuntimeError(
                f"selftest failed: the embedded structure was refused: {exc}"
            ) from exc
        if result["incomplete"]:
            # Strict, unlike a request: a model that cannot finish the embedded structure
            # is not ready, and a cut-off answer is never "the right one".
            raise RuntimeError(
                f"selftest failed: the embedded structure was refused: {TOKEN_LIMIT_DETAIL}"
            )
        got = result["smiles"]
        if got != SELFTEST_SMILES:
            raise RuntimeError(
                f"selftest failed: the embedded structure was read as {got!r}, "
                f"expected {SELFTEST_SMILES!r}"
            )
        self._last_pass = self._clock()

    def _raise_if_hung(self) -> None:
        started = self._decode_started
        if started is None:  # the decode is just starting
            return
        running = self._clock() - started
        if running > self.hang_after:
            raise RuntimeError(
                f"decode running for {int(running)} s, more than the {self.hang_after:.0f} s "
                "that even a decode at the token limit needs"
            )

    def infer(self, image: bytes, params: Mapping[str, str]) -> dict[str, Any]:
        array = _to_array(_decode(image))
        with self._lock:
            smiles, pairs = self._call(array, is_request=True)
        result = _result(smiles, pairs)
        if result["incomplete"]:
            # The count only: what the model wrote is the caller's data and stays out of the log.
            log.info(
                "decode stopped at the token limit: %d tokens, returned flagged incomplete",
                len(result["token_confidence"]),
            )
        return result

    def _call(self, array: Array, *, is_request: bool) -> Prediction:
        """Run the predictor; the caller holds the lock.

        Only a decode for a request counts as "a request completed" (and only when the predictor
        returns, whatever the answer is worth): the selftest's own decode is judged by its answer.
        """
        if self._predict_fn is None:
            raise RuntimeError("model not loaded; call load() first")
        self._decode_started = self._clock()
        try:
            answer = self._predict_fn(array)
        except ValueError as exc:
            # create_app maps ValueError to 422 (bad input); a failure inside the library is ours.
            raise RuntimeError(f"recognition failed: {exc}") from exc
        finally:
            self._decode_started = None
        if is_request:
            self._last_request_done = self._clock()
        return answer
