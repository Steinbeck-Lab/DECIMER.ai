"""What the API knows about an upload without parsing it: the type from the first bytes, the
count and mix rules, and the types shared with the ingest child process.

Deliberately free of Pillow and pdfium: the API process imports this module, and must never load
the libraries that parse untrusted files (`ingest` and `ingest_worker` do, in the child).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, BinaryIO, Self

from .errors import ApiError
from .models import JobWarning

if TYPE_CHECKING:
    from .settings import Settings

HEAD_BYTES = 2048
PDF_MARKER_WINDOW = 1024  # the PDF spec lets the header start anywhere in the first 1024 bytes

HEIC_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs"}
HEIF_CONTAINER_BRANDS = {b"mif1", b"msf1"}


class FileKind(StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    HEIF = "heif"


def sniff(head: bytes) -> FileKind | None:
    """The file type from its first bytes, or None when it is not one we accept."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if head.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return FileKind.WEBP
    if head[4:8] == b"ftyp":
        major = head[8:12]
        size = int.from_bytes(head[:4], "big")
        compatible = {head[offset : offset + 4] for offset in range(16, min(size, len(head)), 4)}
        if major in HEIC_BRANDS or (major in HEIF_CONTAINER_BRANDS and compatible & HEIC_BRANDS):
            return FileKind.HEIF
        return None
    if b"%PDF-" in head[:PDF_MARKER_WINDOW]:
        return FileKind.PDF
    return None


@dataclass(frozen=True)
class Upload:
    """One file of the request. `name` is only ever shown back to the user."""

    name: str
    data: BinaryIO


@dataclass(frozen=True)
class Limits:
    max_pdf_pages: int
    max_image_pixels: int
    max_job_pixels: int
    max_upload_files: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            max_pdf_pages=settings.max_pdf_pages,
            max_image_pixels=settings.max_image_pixels,
            max_job_pixels=settings.max_job_pixels,
            max_upload_files=settings.max_upload_files,
        )


@dataclass
class Ingested:
    pages: int
    pages_processed: int
    truncated: bool = False
    warnings: list[JobWarning] = field(default_factory=list)


def label(name: str) -> str:
    """A client file name made safe to show back in a message."""
    cleaned = "".join(char for char in name if char.isprintable())[:60]
    return f"'{cleaned}'" if cleaned else "(unnamed)"


def classify(uploads: list[Upload], limits: Limits) -> list[FileKind]:
    """The cheap checks that need no parser: how many files, what type each is, and whether
    the mix is allowed. Raises `ApiError`."""
    if not uploads:
        raise ApiError(400, "empty_upload", "No file was uploaded.")
    if len(uploads) > limits.max_upload_files:
        raise ApiError(
            400,
            "too_many_files",
            f"At most {limits.max_upload_files} files can be uploaded at once.",
        )
    kinds: list[FileKind] = []
    for position, upload in enumerate(uploads, start=1):
        upload.data.seek(0)
        kind = sniff(upload.data.read(HEAD_BYTES))
        if kind is None:
            raise ApiError(
                415,
                "unsupported",
                f"File {position} {label(upload.name)} is not a supported file. "
                "Valid formats: PDF, PNG, JPEG, WebP, HEIC.",
            )
        kinds.append(kind)
    pdfs = [kind for kind in kinds if kind is FileKind.PDF]
    if pdfs and len(pdfs) != len(kinds):
        raise ApiError(
            400,
            "mixed_upload",
            "Invalid mixed inputs. Please upload a PDF document or chemical structure images, "
            "not both.",
        )
    if len(pdfs) > 1:
        raise ApiError(400, "multiple_pdfs", "Please upload one PDF document at a time.")
    return kinds


def processing_limit(reason: str = "needed too much memory") -> ApiError:
    """The parser hit a resource limit (memory, time, CPU or file size)."""
    return ApiError(
        413,
        "processing_limit",
        f"The file {reason} to process. Please upload a smaller or simpler file.",
    )
