from __future__ import annotations

import pytest

from decimer_api.uploads import FileKind, sniff
from tests.factories import (
    make_heic,
    make_jpeg,
    make_pdf,
    make_png,
    make_webp,
)


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (make_png(), FileKind.PNG),
        (make_jpeg(), FileKind.JPEG),
        (make_webp(), FileKind.WEBP),
        (make_heic(), FileKind.HEIF),
        (make_pdf(), FileKind.PDF),
        (b"\n\n" + make_pdf(), FileKind.PDF),  # a few bytes of junk before %PDF- are tolerated
    ],
)
def test_recognises_the_accepted_formats(data: bytes, kind: FileKind) -> None:
    assert sniff(data[:2048]) is kind


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"plain text",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"GIF89a\x01\x00\x01\x00",
        b"PK\x03\x04" + b"\x00" * 30,  # zip
        b"MZ" + b"\x00" * 30,  # Windows executable
        b"RIFF\x24\x00\x00\x00WAVEfmt ",  # RIFF but not WebP
        b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00mif1avif",  # AVIF is not HEIC
        b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom",  # an MP4 video
        b"\x00" * 1100 + b"%PDF-1.7",  # the marker is too far into the file
    ],
)
def test_rejects_everything_else(data: bytes) -> None:
    assert sniff(data[:2048]) is None


def test_a_jpeg_that_mentions_pdf_is_still_a_jpeg() -> None:
    assert sniff(make_jpeg()[:3] + b"%PDF-1.7" + b"\x00" * 40) is FileKind.JPEG
