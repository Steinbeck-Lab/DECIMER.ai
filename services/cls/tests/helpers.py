"""Fakes and builders shared by the fast tests (nothing here imports TensorFlow)."""

from __future__ import annotations

import io
import struct
import threading
import time
import zlib
from collections.abc import Callable
from typing import cast

import numpy as np
from PIL import Image

Score = float | np.float32
ScoreRule = Score | Callable[[Image.Image], Score]


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


def has_ink(image: Image.Image) -> bool:
    """True when any pixel is dark: the drawn hexagon, but not a blank white page."""
    return int((np.asarray(image.convert("L")) < 128).sum()) > 0


def structure_if_inked(structure: Score | None = None, blank: Score = 1.0) -> ScoreRule:
    """A stand-in classifier that answers like the real one on the selftest's two images."""
    structure = np.float32(1e-9) if structure is None else structure

    def rule(image: Image.Image) -> Score:
        return structure if has_ink(image) else blank

    return rule


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


class FakeScorer:
    """Stands in for the real classifier: answers by `score` and records what it was given.

    The real library answers with a numpy float32, so that is what this returns by default.
    `score` may also be a function of the image. Like the library, it first converts the image to
    grayscale and back to RGB, so an image the library would choke on fails here too.
    """

    def __init__(
        self,
        score: ScoreRule | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self.score: ScoreRule = np.float32(1e-9) if score is None else score
        self.error = error
        self.delay = delay
        self.calls: list[Image.Image] = []
        self.max_overlap = 0
        self._inside = 0
        self._guard = threading.Lock()

    def __call__(self, image: Image.Image) -> float:
        with self._guard:
            self._inside += 1
            self.max_overlap = max(self.max_overlap, self._inside)
        try:
            self.calls.append(image)
            image.convert("L").convert("RGB")  # the library's first step; may raise ValueError
            if self.delay:
                time.sleep(self.delay)
            if self.error is not None:
                raise self.error
            answer = self.score(image) if callable(self.score) else self.score
            return cast("float", answer)  # a numpy float32 unless the test says otherwise
        finally:
            with self._guard:
                self._inside -= 1
