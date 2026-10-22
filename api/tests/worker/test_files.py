from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from decimer_api.worker import files


def png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_link_or_copy_makes_the_same_bytes_available_under_the_new_name(tmp_path: Path) -> None:
    source = tmp_path / "0001.png"
    source.write_bytes(b"pixels")
    files.link_or_copy(source, tmp_path / "page-0.png")
    assert (tmp_path / "page-0.png").read_bytes() == b"pixels"
    assert not (tmp_path / "page-0.png").is_symlink()  # the media endpoint refuses symlinks


def test_link_or_copy_falls_back_to_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "0001.png"
    source.write_bytes(b"pixels")

    def refuse(*_args: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", refuse)
    files.link_or_copy(source, tmp_path / "page-0.png")
    assert (tmp_path / "page-0.png").read_bytes() == b"pixels"
    assert (tmp_path / "page-0.png").stat().st_ino != source.stat().st_ino


def test_png_size_reads_the_header(tmp_path: Path) -> None:
    path = tmp_path / "p.png"
    path.write_bytes(png((123, 45)))
    assert files.png_size(path.read_bytes()) == (123, 45)
    assert files.png_file_size(path) == (123, 45)


def test_png_size_refuses_what_is_not_a_png() -> None:
    with pytest.raises(ValueError, match="PNG"):
        files.png_size(b"GIF89a" + bytes(30))


MIB = 1024 * 1024


def noise(size: tuple[int, int], mode: str = "RGB") -> bytes:
    """A PNG that does not compress: random pixels (a scan, a photograph of a page)."""
    channels = len(mode)
    image = Image.frombytes(mode, size, os.urandom(size[0] * size[1] * channels))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=1)
    return buffer.getvalue()


def test_a_png_within_every_limit_is_returned_untouched() -> None:
    data = png((200, 100))
    fitted = files.fit_png(data, max_pixels=1_000_000, max_bytes=MIB, max_side=4096)
    assert fitted.data is data
    assert (fitted.width, fitted.height, fitted.scaled) == (200, 100, False)


def test_a_noisy_16_megapixel_image_is_cut_down_to_what_the_models_take() -> None:
    """The case that used to go to ocsr as it was: 48 MB of pixels that cannot be compressed,
    for services that take 20 MiB and look at a 512 px thumbnail."""
    data = noise((4000, 4000))
    assert len(data) > 20 * MIB
    fitted = files.fit_png(data, max_pixels=4_000_000, max_bytes=19 * MIB, max_side=4096)
    assert fitted.scaled
    assert fitted.width * fitted.height <= 4_000_000
    assert len(fitted.data) <= 19 * MIB
    assert files.png_size(fitted.data) == (fitted.width, fitted.height)
    assert abs(fitted.width - fitted.height) <= 1  # the shape is kept


def test_a_panorama_is_limited_by_its_longest_side_too() -> None:
    fitted = files.fit_png(
        png((12000, 1000)), max_pixels=4_000_000, max_bytes=19 * MIB, max_side=4096
    )
    assert max(fitted.width, fitted.height) <= 4096
    assert fitted.width == 4096
    assert abs(fitted.width / fitted.height - 12) < 0.1
    assert fitted.scaled


def test_the_side_limit_is_optional() -> None:
    fitted = files.fit_png(png((12000, 1000)), max_pixels=40_000_000, max_bytes=19 * MIB)
    assert (fitted.width, fitted.height, fitted.scaled) == (12000, 1000, False)


def test_an_image_within_the_pixel_limit_but_over_the_byte_limit_is_shrunk_until_it_fits() -> None:
    data = noise((1000, 1000))  # 3 MB of noise
    fitted = files.fit_png(data, max_pixels=40_000_000, max_bytes=600_000)
    assert len(fitted.data) <= 600_000
    assert (fitted.width, fitted.height) < (1000, 1000)
    assert fitted.scaled
    tighter = files.fit_png(data, max_pixels=40_000_000, max_bytes=300_000)
    assert len(tighter.data) <= 300_000
    assert tighter.width < fitted.width


def test_what_cannot_be_made_small_enough_is_refused() -> None:
    with pytest.raises(files.ImageTooLarge):
        files.fit_png(noise((300, 300)), max_pixels=40_000_000, max_bytes=50)  # below any PNG


def test_a_big_image_is_downscaled_keeping_its_shape() -> None:
    for size in [(4000, 3000), (3000, 4000), (9000, 500), (10, 50_000)]:
        fitted = files.fit_png(png(size), max_pixels=400_000, max_bytes=MIB)
        assert fitted.width * fitted.height <= 400_000
        assert (
            fitted.width * fitted.height > 400_000 * 0.85
        )  # rounding down costs most for a sliver
        assert (
            abs(fitted.width / fitted.height - size[0] / size[1])
            < 0.02 * (size[0] / size[1]) + 1 / fitted.height
        )


def test_downscaling_converts_to_rgb_or_gray_and_flattens_transparency_on_white() -> None:
    rgb = files.fit_png(png((1000, 1000), "RGBA"), max_pixels=10_000, max_bytes=MIB)
    with Image.open(io.BytesIO(rgb.data)) as image:
        assert image.mode == "RGB"
        assert image.getpixel((0, 0)) == (255, 255, 255)  # transparent black becomes white
    gray = files.fit_png(png((1000, 1000), "L"), max_pixels=10_000, max_bytes=MIB)
    with Image.open(io.BytesIO(gray.data)) as image:
        assert image.mode == "L"


@pytest.mark.parametrize("junk", [b"not an image at all" * 10, b"", b"\x89PNG\r\n\x1a\n"])
def test_bytes_that_are_not_a_png_are_refused(junk: bytes) -> None:
    with pytest.raises(ValueError, match="PNG"):
        files.fit_png(junk, max_pixels=10, max_bytes=MIB)


def test_a_header_that_lies_is_refused_too() -> None:
    """A PNG header that claims a huge size over no data: decoding fails."""
    data = bytearray(png((10, 10)))
    data[16:24] = (50_000).to_bytes(4, "big") * 2
    with pytest.raises(ValueError):
        files.fit_png(bytes(data), max_pixels=1000, max_bytes=MIB)


# --- boxes ----------------------------------------------------------------------------------------


def test_a_box_is_scaled_back_to_the_full_page() -> None:
    assert files.scale_box((10, 20, 110, 70), sent=(500, 400), full=(1000, 800)) == (
        20,
        40,
        220,
        140,
    )


def test_a_scaled_box_rounds_outwards_and_stays_on_the_page() -> None:
    # 3 -> 3 * 2.5 = 7.5 rounds down to 7; 7 -> 17.5 rounds up to 18; the page edge clamps
    assert files.scale_box((3, 3, 7, 7), sent=(100, 100), full=(250, 250)) == (7, 7, 18, 18)
    assert files.scale_box((90, 90, 100, 100), sent=(100, 100), full=(251, 251)) == (
        225,
        225,
        251,
        251,
    )
    # a box on the edge of the shrunk copy: ceil(11 * 25/11) can float to 26, one past the page
    assert files.scale_box((0, 0, 11, 11), sent=(11, 11), full=(25, 25)) == (0, 0, 25, 25)


def test_a_box_with_no_area_after_scaling_is_dropped() -> None:
    assert files.scale_box((5, 5, 5, 9), sent=(10, 10), full=(20, 20)) is None
