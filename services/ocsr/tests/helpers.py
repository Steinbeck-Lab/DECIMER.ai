"""Fakes and builders shared by the fast tests (nothing here imports TensorFlow or DECIMER)."""

from __future__ import annotations

import io
import re
import struct
import threading
import time
import zlib
from collections.abc import Callable, Sequence
from typing import cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from decimer_ocsr.model import TOKEN_LIMIT

Pairs = Sequence[tuple[str, float | np.float32]]
Answer = tuple[str, Pairs]
AnswerRule = Answer | Callable[[NDArray[np.uint8]], Answer]

# What the selftest expects (daidzein) and what the real library hands back for it: (token,
# numpy.float32) pairs, one per token, no start or end token.
DAIDZEIN = "C1=CC(=CC=C1C2=COC3=CC(=CC=C3C2=O)O)O"
DAIDZEIN_TOKENS: list[tuple[str, float | np.float32]] = [
    (token, np.float32(0.99)) for token in re.findall(r"Cl|Br|.", DAIDZEIN)
]


def cut_off(last: str = "C", head: Pairs = ()) -> Answer:
    """What `predict_SMILES` returns when decoding stops at the token limit without an end token:
    a SMILES of TOKEN_LIMIT tokens (302) and TOKEN_LIMIT - 1 pairs, because the library cuts the
    last token's confidence. `head` are the first pairs, the rest are ("C", 0.9); `last` is the
    token that has no pair."""
    pairs: list[tuple[str, float | np.float32]] = [*head]
    pairs += [("C", 0.9)] * (TOKEN_LIMIT - 1 - len(pairs))
    return "".join(token for token, _ in pairs) + last, pairs


def image_bytes(
    width: int = 8,
    height: int = 8,
    *,
    fmt: str = "PNG",
    mode: str = "RGB",
    color: int | tuple[int, ...] | str = "white",
    **save_options: object,
) -> bytes:
    """A solid-colour image (white unless told otherwise), encoded as `fmt`.

    Beware that an int colour in an RGB image is a packed pixel: 255 is red, not white.
    """
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color).save(buffer, format=fmt, **save_options)
    return buffer.getvalue()


def png_declaring(width: int, height: int) -> bytes:
    """A PNG whose header claims `width` x `height` but which stops right after the IDAT header.

    Pillow reads the size from the header and only fails on the missing pixel data when the image
    is decoded, so this tells "refused from the header" apart from "refused because it would not
    decode".
    """

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + struct.pack(">I", 1 << 20) + b"IDAT"


def jpeg_with_exif_orientation(width: int, height: int, orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    return image_bytes(width, height, fmt="JPEG", exif=exif.tobytes())


def library_flatten(array: NDArray[np.uint8]) -> Image.Image:
    """What DECIMER's `remove_transparent` does with an array: RGBA, composited onto white."""
    rgba = Image.fromarray(array).convert("RGBA")
    return Image.alpha_composite(Image.new("RGBA", rgba.size, (255, 255, 255)), rgba)


class FakePredictor:
    """Stands in for `DECIMER.predict_SMILES(array, confidence=True)` and records what it got.

    `answer` is a (smiles, [(token, confidence), ...]) pair, or a function of the array. Like the
    library, it first turns the array into an RGBA image and composites it onto white, so an array
    the library would choke on fails here too.
    """

    def __init__(
        self,
        answer: AnswerRule | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
        gate: threading.Event | None = None,
    ) -> None:
        self.answer: AnswerRule = (DAIDZEIN, DAIDZEIN_TOKENS) if answer is None else answer
        self.error = error
        self.delay = delay
        # A decode that stays "in flight" until the test sets `gate` (a token-limit decode takes
        # 38-55 s on the real model); `entered` is set as soon as the predictor is running.
        self.gate = gate
        self.entered = threading.Event()
        self.calls: list[NDArray[np.uint8]] = []
        self.max_overlap = 0
        self._inside = 0
        self._guard = threading.Lock()

    def __call__(self, array: NDArray[np.uint8]) -> Answer:
        with self._guard:
            self._inside += 1
            self.max_overlap = max(self.max_overlap, self._inside)
        try:
            self.calls.append(array)
            library_flatten(array)  # the library's first step; may raise
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(30), "the test never released the decode"
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            return cast("Answer", self.answer(array) if callable(self.answer) else self.answer)
        finally:
            with self._guard:
                self._inside -= 1
