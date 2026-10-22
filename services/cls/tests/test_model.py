import io
import json
import math
import subprocess
import sys
import threading
from typing import Any

import numpy as np
import pytest
from helpers import (
    FakeScorer,
    image_bytes,
    jpeg_with_exif_orientation,
    png_declaring,
    structure_if_inked,
)
from PIL import Image, ImageOps

from decimer_cls import model as model_module
from decimer_cls.model import ClsModel

# The library's own default in `DecimerImageClassifier.is_chemical_structure` (see README).
LEGACY_THRESHOLD = 0.000089


def classify(scorer: FakeScorer, data: bytes | None = None) -> dict[str, Any]:
    return ClsModel(scorer).infer(data if data is not None else image_bytes(), {})


# --- the response ---------------------------------------------------------------------------


def test_response_is_exactly_is_structure_and_score_with_plain_python_types() -> None:
    # The real library hands back a numpy float32; json.dumps would raise on it.
    result = classify(FakeScorer(np.float32(3.5e-12)))

    assert set(result) == {"is_structure", "score"}
    assert type(result["is_structure"]) is bool
    assert type(result["score"]) is float
    assert json.loads(json.dumps(result)) == result


def test_score_is_the_raw_classifier_output_not_a_rescaled_value() -> None:
    result = classify(FakeScorer(np.float32(0.0289601)))
    assert result["score"] == pytest.approx(0.0289601, rel=1e-6)


def test_threshold_is_the_legacy_default() -> None:
    assert model_module.THRESHOLD == LEGACY_THRESHOLD


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.0, True),
        (3.5e-12, True),  # a typical depiction in the golden run
        (0.00008, True),
        (LEGACY_THRESHOLD, True),  # legacy: `score <= threshold`, so the boundary is a structure
        (0.00009, False),
        (0.0109, False),  # a typical non-structure in the golden run
        (0.5, False),
        (1.0, False),
    ],
)
def test_low_score_means_structure_and_the_boundary_is_inclusive(
    score: float, expected: bool
) -> None:
    assert classify(FakeScorer(score))["is_structure"] is expected


def test_the_flag_follows_the_score_in_both_directions() -> None:
    # Guards against a mapping that ignores the score or inverts it.
    assert classify(FakeScorer(1e-9))["is_structure"] is True
    assert classify(FakeScorer(0.3))["is_structure"] is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_score_is_an_error_not_a_json_crash(bad: float) -> None:
    with pytest.raises(RuntimeError, match="non-finite"):
        classify(FakeScorer(bad))


# --- decoding -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "garbage",
    [b"this is not an image", b"", b"\x89PNG\r\n\x1a\nbroken", image_bytes(40, 40)[:60]],
    ids=["text", "empty", "bad-png-chunk", "truncated-png"],
)
def test_undecodable_bytes_are_a_value_error_and_the_classifier_is_not_called(
    garbage: bytes,
) -> None:
    scorer = FakeScorer()
    with pytest.raises(ValueError, match="could not decode image"):
        classify(scorer, garbage)
    assert scorer.calls == []


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "BMP"])
def test_the_listed_formats_decode(fmt: str) -> None:
    scorer = FakeScorer()
    classify(scorer, image_bytes(12, 9, fmt=fmt))
    assert [c.size for c in scorer.calls] == [(12, 9)]


@pytest.mark.parametrize("fmt", ["GIF", "TIFF"])
def test_gif_and_tiff_are_not_accepted(fmt: str) -> None:
    # Uploads are vetted by the API and crops are PNG, so nothing needs these decoders.
    data = image_bytes(12, 9, fmt=fmt)
    assert Image.open(io.BytesIO(data)).size == (12, 9)  # Pillow itself reads them
    scorer = FakeScorer()
    with pytest.raises(ValueError, match="could not decode image"):
        classify(scorer, data)
    assert scorer.calls == []


def test_formats_outside_the_allow_list_are_refused() -> None:
    # Pillow would open a PGM; EPS (which shells out to Ghostscript) is why there is a list at all.
    pgm = b"P5\n2 2\n255\n\x00\x00\x00\x00"
    assert Image.open(io.BytesIO(pgm)).size == (2, 2)  # Pillow itself accepts it
    scorer = FakeScorer()
    with pytest.raises(ValueError, match="could not decode image"):
        classify(scorer, pgm)
    assert scorer.calls == []


def test_a_truncated_body_is_a_value_error_even_when_the_header_is_fine() -> None:
    whole = image_bytes(64, 64, fmt="PNG", mode="RGB", color=(10, 200, 30))
    with pytest.raises(ValueError, match="could not decode image"):
        classify(FakeScorer(), whole[: len(whole) // 2])


def test_pixel_cap_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module, "MAX_PIXELS", 100)
    scorer = FakeScorer()
    classify(scorer, image_bytes(10, 10))  # exactly at the cap: fine
    with pytest.raises(ValueError, match="too large"):
        classify(scorer, image_bytes(11, 10))
    assert len(scorer.calls) == 1


def test_pixel_cap_is_decided_from_the_header_before_any_pixel_is_decoded() -> None:
    # A PNG that declares more than the cap and then stops: only the header can have been read.
    side = math.isqrt(model_module.MAX_PIXELS) + 1
    huge = png_declaring(side, side)
    assert side * side > model_module.MAX_PIXELS
    with Image.open(io.BytesIO(huge)) as opened:
        assert opened.size == (side, side)  # Pillow takes the header; decoding is what would fail

    scorer = FakeScorer()
    with pytest.raises(ValueError, match="too large"):
        classify(scorer, huge)
    # Control: the same truncated body under the cap does fail to decode, so "too large" above
    # can only have come from the size in the header.
    with pytest.raises(ValueError, match="could not decode image"):
        classify(scorer, png_declaring(10, 10))
    assert scorer.calls == []


def test_pillows_own_decompression_bomb_error_is_a_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)  # Pillow refuses above 2x this at open()
    with pytest.raises(ValueError, match="could not decode image"):
        classify(FakeScorer(), image_bytes(20, 20))


# Every (format, mode) pair that Pillow can read back from the formats we accept.
FORMAT_MODES = [
    (fmt, mode)
    for fmt, modes in {
        "PNG": ["1", "L", "LA", "P", "RGB", "RGBA", "I;16"],
        "JPEG": ["L", "RGB", "CMYK"],
        "WEBP": ["RGB", "RGBA"],
        "BMP": ["1", "L", "RGB"],
    }.items()
    for mode in modes
]


@pytest.mark.parametrize(("fmt", "mode"), FORMAT_MODES)
def test_every_format_and_mode_we_accept_is_something_the_library_can_convert(
    fmt: str, mode: str
) -> None:
    # The fake does the library's first step (convert to grayscale and back to RGB), so a mode
    # that would crash the real classifier fails here as a RuntimeError.
    scorer = FakeScorer()
    classify(scorer, image_bytes(6, 6, fmt=fmt, mode=mode, color=0))
    assert len(scorer.calls) == 1


@pytest.mark.parametrize("mode", sorted(model_module.SUPPORTED_MODES))
def test_every_supported_mode_survives_flattening_and_the_librarys_conversion(mode: str) -> None:
    flat = model_module._flatten(Image.new(mode, (4, 4)))
    flat.convert("L").convert("RGB")  # must not raise


def test_the_premise_pillow_cannot_convert_lab_and_the_service_does_not_support_it() -> None:
    with pytest.raises(ValueError, match="not supported"):
        Image.new("LAB", (2, 2)).convert("L").convert("RGB")
    assert "LAB" not in model_module.SUPPORTED_MODES
    assert "La" not in model_module.SUPPORTED_MODES  # La -> L fails the same way


def test_a_pixel_mode_the_library_cannot_convert_is_a_422_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A LAB TIFF is what produced "conversion from LAB to RGB not supported" inside the library,
    # i.e. a 500. TIFF is no longer accepted, so let it through to reach the mode check.
    monkeypatch.setattr(model_module, "FORMATS", [*model_module.FORMATS, "TIFF"])
    data = image_bytes(6, 6, fmt="TIFF", mode="LAB", color=(50, 0, 0))
    assert Image.open(io.BytesIO(data)).mode == "LAB"
    scorer = FakeScorer()
    with pytest.raises(ValueError, match="unsupported pixel mode: LAB"):
        classify(scorer, data)
    assert scorer.calls == []


@pytest.mark.parametrize(("width", "height"), [(1, 225), (225, 1), (2, 450), (3, 1000)])
def test_an_image_the_library_would_resize_to_zero_pixels_is_a_value_error(
    width: int, height: int
) -> None:
    # `int(short * 224 / long)` is 0, and the library's resize raises. That is the image's fault.
    scorer = FakeScorer()
    with pytest.raises(ValueError, match="too elongated"):
        classify(scorer, image_bytes(width, height))
    assert scorer.calls == []


@pytest.mark.parametrize(("width", "height"), [(1, 224), (224, 1), (1, 1), (3, 600), (500, 400)])
def test_extreme_but_classifiable_shapes_reach_the_classifier(width: int, height: int) -> None:
    scorer = FakeScorer()
    classify(scorer, image_bytes(width, height))
    assert len(scorer.calls) == 1


def test_a_value_error_from_inside_the_library_is_not_reported_as_bad_input() -> None:
    # Only the caller's own mistakes may become 422; a ValueError from the library is ours.
    with pytest.raises(RuntimeError, match="classification failed"):
        classify(FakeScorer(error=ValueError("shape mismatch deep in keras")))


# --- what the classifier is given -----------------------------------------------------------


def test_transparent_pixels_are_composited_onto_white_as_legacy_does() -> None:
    # Black ink on a transparent background must reach the network as black on white. If the alpha
    # channel were dropped instead, the transparent pixels (stored as black) would be black too.
    rgba = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
    rgba.putpixel((2, 2), (0, 0, 0, 255))
    rgba.putpixel((3, 3), (255, 0, 0, 255))
    scorer = FakeScorer()
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    classify(scorer, buffer.getvalue())

    (seen,) = scorer.calls
    assert seen.mode == "RGB"
    assert seen.getpixel((0, 0)) == (255, 255, 255)
    assert seen.getpixel((2, 2)) == (0, 0, 0)
    assert seen.getpixel((3, 3)) == (255, 0, 0)


def test_half_transparent_pixels_blend_with_white() -> None:
    rgba = Image.new("RGBA", (4, 4), (0, 0, 0, 128))
    buffer = io.BytesIO()
    rgba.save(buffer, format="PNG")
    scorer = FakeScorer()
    classify(scorer, buffer.getvalue())
    grey = scorer.calls[0].getpixel((0, 0))
    assert isinstance(grey, tuple)
    assert all(126 <= channel <= 128 for channel in grey)


def test_a_grayscale_plus_alpha_png_is_composited_onto_white_too() -> None:
    scorer = FakeScorer()
    classify(scorer, image_bytes(5, 5, mode="LA", color=(0, 0)))  # black, fully transparent
    assert scorer.calls[0].mode == "RGB"
    assert scorer.calls[0].getpixel((1, 1)) == (255, 255, 255)


def test_a_palette_png_with_a_transparent_colour_is_composited_onto_white() -> None:
    palette = Image.new("P", (5, 5), 0)
    palette.putpalette([0, 0, 0, 255, 0, 0])
    buffer = io.BytesIO()
    palette.save(buffer, format="PNG", transparency=0)  # colour 0 (black) is transparent
    scorer = FakeScorer()
    classify(scorer, buffer.getvalue())
    assert scorer.calls[0].getpixel((1, 1)) == (255, 255, 255)


@pytest.mark.parametrize(("mode", "color"), [("RGB", (9, 8, 7)), ("L", 77)])
def test_opaque_images_are_passed_through_untouched(mode: str, color: Any) -> None:
    # The library converts to grayscale itself; touching the pixels here would drift from legacy.
    scorer = FakeScorer()
    classify(scorer, image_bytes(5, 5, mode=mode, color=color))
    (seen,) = scorer.calls
    assert seen.mode == mode
    assert seen.getpixel((0, 0)) == color


@pytest.mark.parametrize("mode", ["RGBA", "LA"])
def test_no_rgba_image_ever_reaches_the_classifier(mode: str) -> None:
    # The library saves "caffeine_mod.png" into the working directory for every RGBA input (and is
    # not thread-safe about it). Flattening first, as above, is what keeps it from doing that.
    scorer = FakeScorer()
    classify(scorer, image_bytes(5, 5, mode=mode, color=(1, 2, 3, 4)[: len(mode)]))
    assert [call.mode for call in scorer.calls] == ["RGB"]


def test_flattening_equals_the_librarys_own_rgba_branch_pixel_for_pixel() -> None:
    rng = np.random.default_rng(7)
    rgba = Image.fromarray(rng.integers(0, 256, (37, 53, 4), dtype=np.uint8))
    assert rgba.mode == "RGBA"
    # DecimerImageClassifier._get_RGB_grayscale_image, minus its file write:
    expected = Image.new("RGB", rgba.size, (255, 255, 255))
    expected.paste(rgba, mask=rgba.split()[3])

    assert np.array_equal(np.asarray(model_module._flatten(rgba)), np.asarray(expected))


def test_exif_orientation_is_not_applied_because_legacy_does_not() -> None:
    data = jpeg_with_exif_orientation(30, 10, orientation=6)  # "rotate 90 degrees to display"
    with Image.open(io.BytesIO(data)) as opened:
        assert ImageOps.exif_transpose(opened).size == (10, 30)  # Pillow would rotate it

    scorer = FakeScorer()
    classify(scorer, data)
    assert scorer.calls[0].size == (30, 10)


# --- lifecycle ------------------------------------------------------------------------------


def test_infer_before_load_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="not loaded"):
        ClsModel().infer(image_bytes(), {})


def test_load_keeps_an_injected_classifier_and_does_not_import_tensorflow() -> None:
    code = (
        "import sys\n"
        "from decimer_cls.model import ClsModel\n"
        "ClsModel(lambda image: 0.5).load()\n"
        "bad = [m for m in ('tensorflow', 'decimer_image_classifier') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    subprocess.run([sys.executable, "-I", "-c", code], check=True, timeout=60)


def test_unknown_form_fields_are_ignored() -> None:
    result = ClsModel(FakeScorer(0.2)).infer(image_bytes(), {"threshold": "0.9", "x": "y"})
    assert result["is_structure"] is False  # a caller cannot move the threshold


def test_selftest_asks_about_a_hexagon_and_a_blank_page_and_passes_on_the_right_answers() -> None:
    scorer = FakeScorer(structure_if_inked())
    ClsModel(scorer).selftest()

    hexagon, blank = scorer.calls
    for seen in (hexagon, blank):
        assert seen.mode == "RGB"
        assert max(seen.size) <= 256
    pixels = np.asarray(hexagon)
    assert pixels.max() == 255  # white page
    dark = int((pixels.min(axis=2) < 128).sum())
    assert 0 < dark < pixels.shape[0] * pixels.shape[1] // 10  # some lines, mostly background
    assert np.asarray(blank).min() == 255  # nothing drawn


def test_selftest_fails_when_the_drawn_hexagon_is_not_a_structure() -> None:
    with pytest.raises(RuntimeError, match=r"hexagon .* above the threshold"):
        ClsModel(FakeScorer(0.5)).selftest()  # a model that says "no" to everything


def test_selftest_fails_when_a_blank_page_is_a_structure() -> None:
    with pytest.raises(RuntimeError, match=r"blank .* at or below the threshold"):
        ClsModel(FakeScorer(1e-9)).selftest()  # a model that says "yes" to everything


def test_selftest_thresholds_are_the_same_inclusive_rule_as_infer() -> None:
    at = model_module.THRESHOLD
    ClsModel(FakeScorer(structure_if_inked(structure=at, blank=at * 1.01))).selftest()
    with pytest.raises(RuntimeError, match="blank"):
        ClsModel(FakeScorer(structure_if_inked(structure=at, blank=at))).selftest()
    with pytest.raises(RuntimeError, match="hexagon"):
        ClsModel(FakeScorer(structure_if_inked(structure=at * 1.01, blank=1.0))).selftest()


def test_selftest_raises_when_the_classifier_does() -> None:
    with pytest.raises(RuntimeError, match="classification failed"):
        ClsModel(FakeScorer(error=ValueError("weights not loaded"))).selftest()


def test_selftest_and_infer_never_run_the_classifier_at_the_same_time() -> None:
    # /readyz is not behind the /infer gate, so the selftest can overlap a request. Keras predict()
    # on a shared model from two threads is not something to rely on.
    scorer = FakeScorer(structure_if_inked(), delay=0.05)
    model = ClsModel(scorer)
    data = image_bytes(color=0)
    threads = [threading.Thread(target=model.infer, args=(data, {})) for _ in range(3)]
    threads.append(threading.Thread(target=model.selftest))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(scorer.calls) == 3 + 2  # three requests, and the selftest's hexagon and blank
    assert scorer.max_overlap == 1
