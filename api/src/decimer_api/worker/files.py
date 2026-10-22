"""Small file helpers for the pipeline. All synchronous: async callers use `asyncio.to_thread`."""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_HEADER_BYTES = 24  # signature, IHDR length and type, then width and height


def link_or_copy(source: Path, target: Path) -> None:
    """Make `target` another name for the bytes of `source` (a hard link, else a copy).

    Never a symlink: the media endpoint refuses those.
    """
    temporary = target.with_name(target.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copyfile(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the IHDR chunk, without decoding anything."""
    if len(data) < PNG_HEADER_BYTES or not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:PNG_HEADER_BYTES])
    return int(width), int(height)


def png_file_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        return png_size(handle.read(PNG_HEADER_BYTES))


class ImageTooLarge(ValueError):
    """The image cannot be made small enough, however far it is scaled down."""


@dataclass(frozen=True)
class Fitted:
    """A PNG that fits a service's limits, and its size; `scaled` when smaller than the original."""

    data: bytes
    width: int
    height: int
    scaled: bool


MAX_FIT_ATTEMPTS = 8
BYTE_SHRINK = 0.95  # aim a little under the byte limit, so one more round is rarely needed
MAX_SHRINK_PER_ROUND = 0.9


def fit_png(png: bytes, *, max_pixels: int, max_bytes: int, max_side: int | None = None) -> Fitted:
    """`png` itself when it is within every limit, else a smaller PNG that is.

    The limits are on the pixel count, optionally on the longest side, and on the encoded size.
    The aspect ratio is kept (Lanczos); transparency is flattened onto white; the result is RGB or
    gray. When the encoded size is still too big the image is shrunk again, from the original each
    time, until it fits. Raises `ValueError` for anything that is not a PNG that decodes, and
    `ImageTooLarge` when no size makes it fit.
    """
    width, height = png_size(png)
    scale = min(
        1.0,
        math.sqrt(max_pixels / (width * height)),
        1.0 if max_side is None else max_side / max(width, height),
    )
    if scale >= 1.0 and len(png) <= max_bytes:
        return Fitted(png, width, height, False)

    from PIL import Image  # imported here: most calls never need it

    try:
        with Image.open(io.BytesIO(png), formats=["PNG"]) as image:
            source = _flatten(image)
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(f"not a PNG that decodes: {error}") from error
    for _ in range(MAX_FIT_ATTEMPTS):
        size = (max(1, int(width * scale)), max(1, int(height * scale)))
        small = source if size == (width, height) else source.resize(size, Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        small.save(buffer, format="PNG", compress_level=1)
        data = buffer.getvalue()
        if len(data) <= max_bytes:
            return Fitted(data, size[0], size[1], size != (width, height))
        scale *= min(MAX_SHRINK_PER_ROUND, math.sqrt(max_bytes / len(data)) * BYTE_SHRINK)
    raise ImageTooLarge(f"cannot fit {width}x{height} into {max_bytes} bytes")


def _flatten(image: Image.Image) -> Image.Image:
    """8-bit gray stays gray; anything else becomes RGB, with transparency on white."""
    from PIL import Image

    if image.mode == "L":
        image.load()
        return image.copy()
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def scale_box(
    box: tuple[int, int, int, int], *, sent: tuple[int, int], full: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """A box in the pixels of the image that was sent to seg, in those of the full page: the
    corner moves outwards when it falls between pixels, and the box stays on the page. None when
    nothing is left of it."""
    x_factor, y_factor = full[0] / sent[0], full[1] / sent[1]
    x0, y0, x1, y1 = box
    scaled = (
        max(0, math.floor(x0 * x_factor)),
        max(0, math.floor(y0 * y_factor)),
        min(full[0], math.ceil(x1 * x_factor)),
        min(full[1], math.ceil(y1 * y_factor)),
    )
    if scaled[2] <= scaled[0] or scaled[3] <= scaled[1]:
        return None
    return scaled
