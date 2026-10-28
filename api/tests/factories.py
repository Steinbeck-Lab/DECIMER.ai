"""Build small test uploads in code (no binary fixtures)."""

from __future__ import annotations

import hashlib
import io
import struct
import uuid
import zlib
from pathlib import Path

import pillow_heif
from PIL import Image, ImageDraw

pillow_heif.register_heif_opener()

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _save(image: Image.Image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def make_png(
    size: tuple[int, int] = (48, 40), colour: tuple[int, int, int] = (200, 30, 30)
) -> bytes:
    return _save(Image.new("RGB", size, colour), "PNG")


def make_jpeg(size: tuple[int, int] = (48, 40)) -> bytes:
    return _save(Image.new("RGB", size, (30, 120, 200)), "JPEG")


def make_webp(size: tuple[int, int] = (48, 40)) -> bytes:
    return _save(Image.new("RGB", size, (30, 200, 120)), "WEBP")


def make_heic(size: tuple[int, int] = (48, 40)) -> bytes:
    return _save(Image.new("RGB", size, (120, 30, 200)), "HEIF")


def make_transparent_png(size: tuple[int, int] = (60, 40)) -> bytes:
    """Fully transparent RGBA background (stored RGB is black) with opaque black strokes."""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.line([(5, 5), (size[0] - 6, size[1] - 6)], fill=(0, 0, 0, 255), width=3)
    return _save(image, "PNG")


def make_palette_png_with_transparency(size: tuple[int, int] = (30, 30)) -> bytes:
    image = Image.new("P", size, 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", transparency=0)
    return buffer.getvalue()


def build_pdf(objects: list[bytes], trailer: str = "") -> bytes:
    """A PDF from raw object bodies (object 1 must be the catalog), with a correct xref table."""
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R {trailer} >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_pdf(
    pages: int = 1,
    *,
    page_size: tuple[float, float] = (612, 792),
    page_sizes: list[tuple[float, float]] | None = None,
) -> bytes:
    """A PDF whose page N says "page N" (Helvetica); every page `page_size` unless `page_sizes`."""
    sizes = page_sizes or [page_size] * pages
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(len(sizes)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(sizes)} >>".encode(),
    ]
    font = "<< /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >>"
    for index, (width, height) in enumerate(sizes):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Contents {4 + 2 * index} 0 R /Resources {font} >>"
            ).encode()
        )
        text = f"BT /F1 24 Tf 72 72 Td (page {index + 1}) Tj ET".encode()
        objects.append(f"<< /Length {len(text)} >>\nstream\n".encode() + text + b"\nendstream")
    return build_pdf(objects)


def make_self_referencing_pdf() -> bytes:
    """A page tree whose only kid is itself, with /Count 2147483647 (a known hostile input: the
    previous PDF library raised RuntimeError on it, which became a 500)."""
    return build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [2 0 R] /Count 2147483647 >>",
        ]
    )


_PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key: bytes, data: bytes) -> bytes:
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) % 256
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) % 256
        j = (j + state[i]) % 256
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) % 256])
    return bytes(out)


def make_encrypted_pdf(
    user_password: str = "user-secret",  # noqa: S107
    owner_password: str = "owner-secret",  # noqa: S107
) -> bytes:
    """A one-page PDF with the standard security handler (RC4 40-bit, revision 2). Opening it
    without the user password fails; with an empty user password it opens (owner-only)."""
    # MD5 and RC4 are what the PDF standard prescribes here; nothing in this test protects data.
    owner_key = hashlib.md5((owner_password.encode() + _PAD)[:32], usedforsecurity=False).digest()[
        :5
    ]
    padded_user = (user_password.encode() + _PAD)[:32]
    owner_entry = _rc4(owner_key, padded_user)
    permissions = -4
    file_id = bytes(range(16))
    key = hashlib.md5(
        padded_user + owner_entry + struct.pack("<i", permissions) + file_id,
        usedforsecurity=False,
    ).digest()[:5]
    user_entry = _rc4(key, _PAD)
    encrypt = (
        f"<< /Filter /Standard /V 1 /R 2 /O <{owner_entry.hex()}> /U <{user_entry.hex()}> "
        f"/P {permissions} >>"
    )
    return build_pdf(
        [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
            encrypt.encode(),
        ],
        trailer=f"/Encrypt 4 0 R /ID [<{file_id.hex()}> <{file_id.hex()}>]",
    )


def make_corrupt_pdf() -> bytes:
    return b"%PDF-1.7\n1 0 obj\n<< /Broken true\nthis is not a pdf at all\n"


def pdf_page_texts(data: bytes | Path) -> list[str]:
    """The text of every page, read back with pypdfium2."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    try:
        return [
            document[index].get_textpage().get_text_range().strip()
            for index in range(len(document))
        ]
    finally:
        document.close()


def make_png16(values: list[list[int]], *, transparent_value: int | None = None) -> bytes:
    """A 16-bit grayscale PNG (colour type 0), optionally with a tRNS chunk, built by hand
    because Pillow cannot write that chunk for this mode."""

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    height, width = len(values), len(values[0])
    raw = b"".join(b"\x00" + b"".join(struct.pack(">H", v) for v in row) for row in values)
    parts = [
        PNG_SIGNATURE,
        chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, 0, 0, 0, 0)),
    ]
    if transparent_value is not None:
        parts.append(chunk(b"tRNS", struct.pack(">H", transparent_value)))
    parts += [chunk(b"IDAT", zlib.compress(raw)), chunk(b"IEND", b"")]
    return b"".join(parts)


def multipart(
    files: list[tuple[str, bytes]],
    *,
    field: str = "file",
    fields: list[tuple[str, str]] | None = None,
) -> tuple[bytes, dict[str, str]]:
    """A multipart/form-data body and its headers; every file part uses the same field name."""
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields or []:
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            ).encode()
        )
    for filename, content in files:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), {"content-type": f"multipart/form-data; boundary={boundary}"}
