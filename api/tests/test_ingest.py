"""The ingest function itself, called directly: what it accepts, what it refuses,
and which parser each kind of file may reach."""

from __future__ import annotations

import errno
import io
import warnings
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageCms, features

from decimer_api import ingest as ingest_module
from decimer_api.errors import ApiError
from decimer_api.ingest import ingest, run_guarded
from decimer_api.uploads import Ingested, Limits, Upload, classify
from tests.factories import (
    make_corrupt_pdf,
    make_encrypted_pdf,
    make_heic,
    make_jpeg,
    make_pdf,
    make_png,
    make_png16,
    make_self_referencing_pdf,
    make_webp,
    pdf_page_texts,
)


def limits(**overrides: int) -> Limits:
    values = {
        "max_pdf_pages": 10,
        "max_image_pixels": 20_000_000,
        "max_job_pixels": 100_000_000,
        "max_upload_files": 50,
    }
    return Limits(**{**values, **overrides})


def uploads(*files: tuple[str, bytes]) -> list[Upload]:
    return [Upload(name=name, data=io.BytesIO(data)) for name, data in files]


def run(tmp_path: Path, *files: tuple[str, bytes], **overrides: int) -> Ingested:
    return ingest(uploads(*files), tmp_path / "input", limits(**overrides))


def refusal(tmp_path: Path, *files: tuple[str, bytes], **overrides: int) -> ApiError:
    with pytest.raises(ApiError) as caught:
        run(tmp_path, *files, **overrides)
    return caught.value


def bomb(side: int) -> bytes:
    """A white 1-bit PNG: a few KB on the wire, side x side pixels once decoded."""
    buffer = io.BytesIO()
    Image.new("1", (side, side), 1).save(buffer, format="PNG")
    return buffer.getvalue()


# --- classify: the cheap checks that need no parser -----------------------------------------


def test_classify_refuses_before_any_parsing() -> None:
    with pytest.raises(ApiError) as caught:
        classify(uploads(("a.png", make_png()), ("b.pdf", make_pdf())), limits())
    assert caught.value.code == "mixed_upload"
    with pytest.raises(ApiError) as caught:
        classify(uploads(("a.png", b"nope")), limits())
    assert (caught.value.status, caught.value.code) == (415, "unsupported")
    with pytest.raises(ApiError) as caught:
        classify([], limits())
    assert caught.value.code == "empty_upload"
    with pytest.raises(ApiError) as caught:
        classify(uploads(*[("a.png", make_png())] * 3), limits(max_upload_files=2))
    assert caught.value.code == "too_many_files"


# --- which parser a file may reach ----------------------------------------------------------


def test_every_kind_is_opened_with_only_its_own_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str] | None] = []
    real_open = Image.open

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("formats"))
        return real_open(*args, **kwargs)

    monkeypatch.setattr(ingest_module.Image, "open", spy)
    run(tmp_path, ("a", make_png()), ("b", make_jpeg()), ("c", make_webp()), ("d", make_heic()))
    assert None not in seen
    assert {tuple(formats) for formats in seen if formats} == {
        ("PNG",),
        ("JPEG",),
        ("WEBP",),
        ("HEIF",),
    }


@pytest.mark.skipif(not features.check("avif"), reason="this Pillow build has no AVIF plugin")
def test_a_heic_file_never_reaches_the_avif_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pillow's AVIF plugin also accepts `ftyp mif1` files. If it were tried first, an AVIF
    payload dressed as HEIC would be decoded by it; only the HEIF decoder may see HEIC."""
    from PIL import AvifImagePlugin

    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(buffer, format="AVIF")
    dressed = bytearray(buffer.getvalue())
    dressed[8:12] = b"mif1"  # major brand mif1; the compatible brands list heic below
    dressed[16:20] = b"heic"

    Image.init()
    monkeypatch.setattr(Image, "ID", ["AVIF", *[name for name in Image.ID if name != "AVIF"]])
    calls: list[int] = []
    real = AvifImagePlugin.AvifImageFile._open

    def counting(self: Any) -> None:
        calls.append(1)
        real(self)

    monkeypatch.setattr(AvifImagePlugin.AvifImageFile, "_open", counting)

    # Control: with AVIF first and no restriction, Pillow does hand this file to the AVIF plugin.
    Image.open(io.BytesIO(bytes(dressed))).close()
    assert calls, "the control did not reach the AVIF plugin; this test proves nothing"
    calls.clear()

    try:
        run(tmp_path, ("dressed.heic", bytes(dressed)))
    except ApiError:
        pass  # whether libheif can decode it is not the point
    assert calls == []


# --- pixel limits ---------------------------------------------------------------------------


def test_the_header_check_does_not_depend_on_pillows_own_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)  # Pillow's guard is off

    def explode(self: Image.Image) -> None:
        raise AssertionError("decoded an image that is over the pixel cap")

    monkeypatch.setattr("PIL.ImageFile.ImageFile.load", explode)
    # 1.5x the cap: the zone in which Pillow itself only warns.
    error = refusal(tmp_path, ("b.png", bomb(1500)), max_image_pixels=1_500_000)
    assert (error.status, error.code) == (413, "too_many_pixels")


def test_pillows_global_guard_follows_the_limits_and_a_warning_is_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 89_000_000)
    with warnings.catch_warnings():
        run_guarded(
            uploads(("a.png", make_png())), tmp_path / "input", limits(max_image_pixels=12_345_678)
        )
        assert Image.MAX_IMAGE_PIXELS == 12_345_678
        with pytest.raises(Image.DecompressionBombWarning):
            warnings.warn("size", Image.DecompressionBombWarning, stacklevel=1)


def test_the_pixel_cap_is_inclusive(tmp_path: Path) -> None:
    run(tmp_path, ("a.png", make_png((100, 100))), max_image_pixels=10_000)  # exactly at the cap
    error = refusal(tmp_path / "b", ("a.png", make_png((100, 100))), max_image_pixels=9_999)
    assert error.code == "too_many_pixels"


def test_all_images_of_a_job_count_against_the_job_total(tmp_path: Path) -> None:
    files = [(f"{n}.png", make_png((3000, 3000))) for n in range(3)]  # 9 MP each
    error = refusal(tmp_path, *files, max_job_pixels=20_000_000)
    assert (error.status, error.code) == (413, "too_many_pixels")
    assert "together" in error.message
    assert not list((tmp_path / "input").glob("*.png"))  # refused from the headers alone
    run(tmp_path / "ok", *files, max_job_pixels=27_000_000)  # exactly 3 x 9 MP passes


def test_a_pdfs_pages_count_against_the_job_total_at_300_dpi(tmp_path: Path) -> None:
    pdf = make_pdf(3)  # three US-letter pages: 2550 x 3300 = 8.4 MP each at 300 DPI
    assert refusal(tmp_path, ("a.pdf", pdf), max_job_pixels=25_000_000).code == "too_many_pixels"
    assert run(tmp_path / "ok", ("a.pdf", pdf), max_job_pixels=26_000_000).pages == 3


def test_every_kept_page_of_a_pdf_is_checked_not_only_the_first(tmp_path: Path) -> None:
    pdf = make_pdf(page_sizes=[(612, 792), (14400, 14400), (612, 792)])
    error = refusal(tmp_path, ("poster.pdf", pdf))
    assert (error.status, error.code) == (413, "too_many_pixels")
    last = make_pdf(page_sizes=[(612, 792), (612, 792), (14400, 14400)])
    assert refusal(tmp_path / "last", ("poster.pdf", last)).code == "too_many_pixels"


def test_pages_beyond_the_page_cap_are_not_inspected(tmp_path: Path) -> None:
    """Only the kept pages matter: the rest are dropped, and never rendered."""
    sizes = [(612.0, 792.0)] * 10 + [(14400.0, 14400.0)]
    result = run(tmp_path, ("long.pdf", make_pdf(page_sizes=sizes)))
    assert result.truncated and result.pages == 11 and result.pages_processed == 10


# --- PDF: every parser failure is a corrupt file --------------------------------------------


def test_a_self_referencing_page_tree_is_a_corrupt_file(tmp_path: Path) -> None:
    """Found in review: /Kids [2 0 R] inside object 2 with /Count 2147483647."""
    error = refusal(tmp_path, ("loop.pdf", make_self_referencing_pdf()))
    assert (error.status, error.code) == (400, "corrupt_file")


def test_a_stale_password_error_from_pdfium_does_not_make_a_damaged_file_encrypted(
    tmp_path: Path,
) -> None:
    """pdfium remembers its last error between calls: after an encrypted file, a damaged one
    used to be reported as password protected too."""
    assert refusal(tmp_path / "first", ("a.pdf", make_encrypted_pdf())).code == "encrypted_pdf"
    assert (
        refusal(tmp_path / "second", ("b.pdf", make_self_referencing_pdf())).code == "corrupt_file"
    )
    assert refusal(tmp_path / "third", ("c.pdf", make_corrupt_pdf())).code == "corrupt_file"


def test_corrupt_and_encrypted_pdfs(tmp_path: Path) -> None:
    assert refusal(tmp_path, ("a.pdf", make_corrupt_pdf())).code == "corrupt_file"
    error = refusal(tmp_path / "enc", ("a.pdf", make_encrypted_pdf()))
    assert (error.status, error.code) == (400, "encrypted_pdf")


def test_a_pdf_with_only_an_owner_password_opens(tmp_path: Path) -> None:
    result = run(tmp_path, ("a.pdf", make_encrypted_pdf(user_password="", owner_password="o")))
    assert result.pages == 1


@pytest.mark.parametrize("failing", ["get_page_size", "import_pages", "__len__"])
def test_any_exception_from_the_pdf_library_is_a_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("something inside pdfium")

    monkeypatch.setattr(pdfium.PdfDocument, failing, boom)
    error = refusal(tmp_path, ("a.pdf", make_pdf(12)))  # 12 pages: the truncation path runs too
    assert (error.status, error.code) == (400, "corrupt_file")


def test_memory_exhaustion_is_not_called_a_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise MemoryError

    monkeypatch.setattr(pdfium.PdfDocument, "get_page_size", boom)
    with pytest.raises(MemoryError):
        run(tmp_path, ("a.pdf", make_pdf()))
    with pytest.raises(ApiError) as caught:
        run_guarded(uploads(("a.pdf", make_pdf())), tmp_path / "other", limits())
    assert (caught.value.status, caught.value.code) == (413, "processing_limit")


def test_the_truncated_pdf_keeps_the_first_pages_and_their_content(tmp_path: Path) -> None:
    result = run(tmp_path, ("long.pdf", make_pdf(12)))
    assert result.truncated and (result.pages, result.pages_processed) == (12, 10)
    assert [w.code for w in result.warnings] == ["pages_truncated"]
    texts = pdf_page_texts(tmp_path / "input" / "document.pdf")
    assert texts == [f"page {n}" for n in range(1, 11)]


# --- normalisation --------------------------------------------------------------------------


def stored(tmp_path: Path, name: str = "0001.png") -> Image.Image:
    image = Image.open(tmp_path / "input" / name)
    image.load()
    return image


def test_sixteen_bit_gray_keeps_its_tones(tmp_path: Path) -> None:
    run(tmp_path, ("g.png", make_png16([[0, 64 * 256, 128 * 256, 255 * 256 + 255]])))
    image = stored(tmp_path)
    assert image.mode == "RGB"
    assert [image.getpixel((x, 0))[0] for x in range(4)] == [0, 64, 128, 255]


def test_sixteen_bit_gray_with_a_transparent_value_is_flattened_onto_white(
    tmp_path: Path,
) -> None:
    """The transparent value must become white, and every other tone must survive. Before the
    fix, the whole image turned white. (The transparent value is 64 x 256, so that it is a
    different number once scaled to 8 bits: the scaling of the tRNS value is tested too.)"""
    values = [[0, 64 * 256, 128 * 256]]
    run(tmp_path, ("g.png", make_png16(values, transparent_value=64 * 256)))
    image = stored(tmp_path)
    assert image.getpixel((0, 0)) == (0, 0, 0)  # black stays black
    assert image.getpixel((1, 0)) == (255, 255, 255)  # the transparent value -> white
    assert image.getpixel((2, 0))[0] == 128


def test_icc_profile_and_text_chunks_are_not_stored(tmp_path: Path) -> None:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    from PIL import PngImagePlugin

    text = PngImagePlugin.PngInfo()
    text.add_text("Comment", "a note that should not survive")
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(
        buffer, format="PNG", icc_profile=profile, pnginfo=text, dpi=(300, 300)
    )
    assert b"iCCP" in buffer.getvalue() and b"tEXt" in buffer.getvalue()
    run(tmp_path, ("p.png", buffer.getvalue()))
    data = (tmp_path / "input" / "0001.png").read_bytes()
    for chunk in (b"iCCP", b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"pHYs"):
        assert chunk not in data, chunk
    assert b"a note that should not survive" not in data


@pytest.mark.parametrize("what", ["image", "pdf"])
def test_a_write_that_hits_the_file_size_limit_is_a_processing_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    """Python ignores SIGXFSZ, so RLIMIT_FSIZE shows up as EFBIG on write, not as a signal."""

    def too_big(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.EFBIG, "File too large")

    png, pdf = make_png(), make_pdf()  # built before the patch: the factories save images too
    if what == "image":
        monkeypatch.setattr(Image.Image, "save", too_big)
        error = refusal(tmp_path, ("a.png", png))
    else:
        monkeypatch.setattr(Path, "write_bytes", too_big)
        error = refusal(tmp_path, ("a.pdf", pdf))
    assert (error.status, error.code) == (413, "processing_limit")


def test_a_full_disk_is_our_problem_not_a_corrupt_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    png = make_png()
    monkeypatch.setattr(Image.Image, "save", full)
    error = refusal(tmp_path, ("a.png", png))
    assert (error.status, error.code) == (503, "unavailable")
