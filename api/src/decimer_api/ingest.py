"""Turn uploads into normalised job inputs. Synchronous.

This module parses untrusted bytes, so in production it never runs inside the API process:
`ingest_runner.SubprocessIngest` runs it in a child process with resource limits and a deadline
(see `ingest_worker`). Calling `ingest` directly is for tests and development.

Nothing here trusts the client. The type of a file comes from its first bytes, never from its
name or declared content type; file names are not used for anything except a short label in
error messages; every file is written under a name this module chooses.

Normalised inputs, under `<input dir>/`:

* a PDF becomes `document.pdf` (the original bytes, or its first `max_pdf_pages` pages);
* every image becomes `0001.png`, `0002.png`, ... in upload order: 8-bit RGB, EXIF rotation
  applied, every metadata chunk (EXIF, ICC profile, text) dropped, transparency flattened onto
  WHITE. The seg service reads with OpenCV `IMREAD_COLOR`, which turns transparent pixels black
  and makes dark strokes invisible.
"""

from __future__ import annotations

import errno
import warnings
from pathlib import Path

import pillow_heif
import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pypdfium2 import raw as pdfium_raw

from .errors import ApiError, unavailable
from .models import JobWarning
from .uploads import (
    FileKind,
    Ingested,
    Limits,
    Upload,
    classify,
    label,
    processing_limit,
)

pillow_heif.register_heif_opener()

RENDER_DPI = 300  # what the worker renders at; page size is checked against it up front
PNG_COMPRESS_LEVEL = 4

# The image decoders Pillow may use for each kind. A file whose bytes lie about their type
# cannot reach another decoder: EPS, TIFF and the like are never tried, and HEIC never reaches
# Pillow's own AVIF plugin, which also accepts `ftyp mif1`.
DECODERS = {
    FileKind.PNG: ["PNG"],
    FileKind.JPEG: ["JPEG"],
    FileKind.WEBP: ["WEBP"],
    FileKind.HEIF: ["HEIF"],
}


def _too_many_pixels(limits: Limits, *, together: bool = False) -> ApiError:
    if together:
        return ApiError(
            413,
            "too_many_pixels",
            f"The files together are too large: at most {limits.max_job_pixels:,} pixels "
            "per upload (a PDF page is counted at 300 DPI).",
        )
    return ApiError(
        413,
        "too_many_pixels",
        f"An image or page is too large: at most {limits.max_image_pixels:,} pixels "
        "(a PDF page is counted at 300 DPI).",
    )


def _corrupt_image(position: int, upload: Upload) -> ApiError:
    return ApiError(
        400, "corrupt_file", f"File {position} {label(upload.name)} could not be read as an image."
    )


def _write_failure(error: OSError, corrupt: ApiError) -> ApiError:
    """An OSError is usually a decoder complaining about the file. Not when it is the limits:
    RLIMIT_FSIZE makes a write fail with EFBIG (Python ignores SIGXFSZ), and a full disk is
    our problem, not the file's."""
    if error.errno == errno.EFBIG:
        return processing_limit("would be too large")
    if error.errno in (errno.ENOSPC, errno.EDQUOT):
        return unavailable()
    return corrupt


def ingest(uploads: list[Upload], input_dir: Path, limits: Limits) -> Ingested:
    """Validate and store the uploads in `input_dir` (created here). Raises `ApiError`.

    `MemoryError` is deliberately not turned into an `ApiError` here; see `run_guarded`.
    """
    kinds = classify(uploads, limits)
    input_dir.mkdir(parents=True, exist_ok=False)
    if kinds[0] is FileKind.PDF:
        return _store_pdf(uploads[0], input_dir, limits)
    return _store_images(uploads, kinds, input_dir, limits)


def run_guarded(uploads: list[Upload], input_dir: Path, limits: Limits) -> Ingested:
    """`ingest`, with Pillow's global guard set from the limits, and running out of memory
    reported as a limit instead of a crash."""
    Image.MAX_IMAGE_PIXELS = limits.max_image_pixels
    # Pillow only warns between 1x and 2x its cap, and a warning is not a refusal.
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        return ingest(uploads, input_dir, limits)
    except MemoryError as error:
        raise processing_limit() from error


# --- PDF ------------------------------------------------------------------------------------


def _store_pdf(upload: Upload, input_dir: Path, limits: Limits) -> Ingested:
    upload.data.seek(0)
    data = upload.data.read()
    corrupt = ApiError(
        400, "corrupt_file", f"{label(upload.name)} could not be read as a PDF document."
    )
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        # pdfium keeps its "last error" between calls, so a stale PASSWORD code could be
        # reported for a file that is merely damaged. A really encrypted file names /Encrypt.
        if error.err_code == pdfium_raw.FPDF_ERR_PASSWORD and b"/Encrypt" in data:
            raise ApiError(
                400,
                "encrypted_pdf",
                f"{label(upload.name)} is password protected. Please remove the password first.",
            ) from error
        raise corrupt from error
    except MemoryError:
        raise
    except Exception as error:
        raise corrupt from error

    try:
        pages = len(document)
        if pages < 1:
            raise corrupt
        kept = min(pages, limits.max_pdf_pages)
        # Pages beyond the cap are dropped and never inspected or rendered, so a document
        # with thousands of pages costs no more than one with ten.
        total = 0
        for number in range(kept):
            width, height = document.get_page_size(number)
            if not (width > 0 and height > 0):
                raise corrupt
            pixels = (width / 72 * RENDER_DPI) * (height / 72 * RENDER_DPI)
            if pixels > limits.max_image_pixels:
                raise _too_many_pixels(limits)
            total += int(pixels)
        if total > limits.max_job_pixels:
            raise _too_many_pixels(limits, together=True)

        target = input_dir / "document.pdf"
        if pages == kept:
            target.write_bytes(data)
            return Ingested(pages=pages, pages_processed=kept)

        trimmed = pdfium.PdfDocument.new()
        try:
            trimmed.import_pages(document, pages=list(range(kept)))
            trimmed.save(target)
        finally:
            trimmed.close()
        warning = JobWarning(
            code="pages_truncated",
            message=f"The document has {pages} pages. Only the first {kept} were processed.",
        )
        return Ingested(pages=pages, pages_processed=kept, truncated=True, warnings=[warning])
    except (ApiError, MemoryError):
        raise
    except OSError as error:
        raise _write_failure(error, corrupt) from error
    except Exception as error:  # pdfium and pypdfium2 raise many types on damaged files
        raise corrupt from error
    finally:
        document.close()


# --- images ---------------------------------------------------------------------------------

_ALPHA_MODES = {"RGBA", "LA", "PA", "RGBa", "La"}


def _flatten(image: Image.Image) -> Image.Image:
    """8-bit RGB; any transparency is composited onto white."""
    if image.mode.startswith("I;16"):
        # A plain convert would clip 16-bit values to white. Scale to 8 bits first, and carry
        # the PNG's transparent value (a 16-bit gray value) over to the 8-bit image.
        transparency = image.info.get("transparency")
        gray = image.point(lambda value: value * (1 / 256), "L")
        if isinstance(transparency, int):
            gray.info["transparency"] = transparency >> 8
        image = gray
    if image.mode in _ALPHA_MODES or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _header_pixels(upload: Upload, kind: FileKind, position: int, limits: Limits) -> int:
    """Width x height from the file header, refusing an image over the per-image cap."""
    upload.data.seek(0)
    try:
        with Image.open(upload.data, formats=DECODERS[kind]) as image:
            width, height = image.size
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise _too_many_pixels(limits) from error
    except MemoryError:
        raise
    except Exception as error:
        raise _corrupt_image(position, upload) from error
    # Pillow's own guard only warns between 1x and 2x of its cap, and may be switched off:
    # this check is ours, and runs before any pixel is decoded.
    if width * height > limits.max_image_pixels:
        raise _too_many_pixels(limits)
    return width * height


def _store_images(
    uploads: list[Upload], kinds: list[FileKind], input_dir: Path, limits: Limits
) -> Ingested:
    total = 0
    for position, (upload, kind) in enumerate(zip(uploads, kinds, strict=True), start=1):
        total += _header_pixels(upload, kind, position, limits)
        if total > limits.max_job_pixels:
            raise _too_many_pixels(limits, together=True)

    for position, (upload, kind) in enumerate(zip(uploads, kinds, strict=True), start=1):
        upload.data.seek(0)
        try:
            with Image.open(upload.data, formats=DECODERS[kind]) as image:
                flat = _flatten(ImageOps.exif_transpose(image))
            flat.info = {}  # EXIF, ICC profile, text chunks: none of it is kept
            flat.save(
                input_dir / f"{position:04d}.png", format="PNG", compress_level=PNG_COMPRESS_LEVEL
            )
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise _too_many_pixels(limits) from error
        except (ApiError, MemoryError):
            raise
        except Exception as error:  # the decoders (libheif in particular) raise many types
            corrupt = _corrupt_image(position, upload)
            if isinstance(error, OSError):
                raise _write_failure(error, corrupt) from error
            raise corrupt from error
    return Ingested(pages=len(uploads), pages_processed=len(uploads))
