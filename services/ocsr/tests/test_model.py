import io
import json
import logging
import math
import sys
import threading
import time
import types
from typing import Any

import numpy as np
import pytest
from decimer_service import Limits
from helpers import (
    DAIDZEIN,
    FakePredictor,
    cut_off,
    image_bytes,
    jpeg_with_exif_orientation,
    library_flatten,
    png_declaring,
)
from numpy.typing import NDArray
from PIL import Image

from decimer_ocsr import model as model_module
from decimer_ocsr.model import OcsrModel


def read(predictor: FakePredictor, data: bytes | None = None) -> dict[str, Any]:
    return OcsrModel(predictor).infer(data if data is not None else image_bytes(), {})


def seen_by_predictor(data: bytes) -> NDArray[np.uint8]:
    fake = FakePredictor()
    OcsrModel(fake).infer(data, {})
    (array,) = fake.calls
    return array


# --- the response ---------------------------------------------------------------------------


def test_response_is_exactly_smiles_token_confidence_mean_confidence_and_incomplete() -> None:
    result = read(FakePredictor())

    assert set(result) == {"smiles", "token_confidence", "mean_confidence", "incomplete"}
    assert result["smiles"] == DAIDZEIN
    assert result["incomplete"] is False


def test_response_holds_plain_python_types_that_survive_json() -> None:
    # The real library hands back numpy float32 confidences; json.dumps would raise on those.
    result = read(FakePredictor())

    assert type(result["smiles"]) is str
    assert type(result["token_confidence"]) is list
    assert type(result["mean_confidence"]) is float
    assert type(result["incomplete"]) is bool
    for pair in result["token_confidence"]:
        assert type(pair) is list
        assert len(pair) == 2
        assert type(pair[0]) is str
        assert type(pair[1]) is float
    assert json.loads(json.dumps(result)) == result


def test_token_confidence_keeps_every_token_and_its_own_confidence_in_order() -> None:
    pairs = [("C", np.float32(0.5)), ("N", np.float32(0.25)), ("O", np.float32(1.0))]
    result = read(FakePredictor(("CNO", pairs)))

    assert result["token_confidence"] == [["C", 0.5], ["N", 0.25], ["O", 1.0]]


def test_float32_confidences_are_reported_exactly_not_rounded() -> None:
    result = read(FakePredictor(("C", [("C", np.float32(0.9970000386238098))])))
    assert result["token_confidence"] == [["C", float(np.float32(0.9970000386238098))]]


def test_mean_confidence_is_the_arithmetic_mean_of_the_token_confidences() -> None:
    # Chosen so that the arithmetic mean (0.8) differs from the median (0.85), the minimum (0.5),
    # the geometric mean (0.774...) and the product (0.36).
    pairs = [("C", 0.9), ("C", 0.5), ("C", 1.0), ("C", 0.8)]
    result = read(FakePredictor(("CCCC", pairs)))

    assert result["mean_confidence"] == pytest.approx(0.8, abs=1e-12)


def test_mean_confidence_of_one_token_is_that_confidence() -> None:
    result = read(FakePredictor(("C", [("C", 0.37)])))
    assert result["mean_confidence"] == pytest.approx(0.37)


def test_an_empty_prediction_is_an_empty_smiles_no_tokens_and_zero_confidence() -> None:
    # The model can answer "<end>" straight away. 0.0 (not null, not NaN) keeps mean_confidence a
    # float that sorts and serialises, and says "no confidence in anything".
    result = read(FakePredictor(("", [])))

    assert result == {
        "smiles": "",
        "token_confidence": [],
        "mean_confidence": 0.0,
        "incomplete": False,
    }
    assert type(result["mean_confidence"]) is float


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_confidence_is_an_error_not_a_json_crash(bad: float) -> None:
    with pytest.raises(RuntimeError, match="non-finite"):
        read(FakePredictor(("CC", [("C", 0.9), ("C", bad)])))


def test_a_decode_cut_off_at_the_token_limit_is_returned_flagged_incomplete() -> None:
    # decimer 2.8.1 returns the whole SMILES (302 tokens) but the confidences of all tokens except
    # the last when decoding hits the token limit (golden crop 15 of the legacy crops, and crop 12
    # of the full-pipeline runs, do). The SMILES is cut off mid-structure. D25 (owner, 2026-10-27):
    # like legacy it is passed on, not refused, and the answer says so; the caller (the API, then
    # CDK and RDKit) judges it.
    result = read(FakePredictor(cut_off()))

    assert result["smiles"] == "C" * 302
    assert result["incomplete"] is True


def test_an_incomplete_answer_keeps_the_tokens_adding_up_to_the_smiles() -> None:
    # The library leaves out the confidence of the last token, so it is put back with 0.0 ("not
    # reported"): token_confidence still concatenates to `smiles`, as the README promises, and the
    # mean is that of the pairs returned (it includes the 0.0).
    result = read(FakePredictor(cut_off("Cl", head=[("C", 0.5), ("N", 0.25)])))

    pairs = result["token_confidence"]
    assert len(pairs) == 302
    assert pairs[:2] == [["C", 0.5], ["N", 0.25]]
    assert pairs[-2:] == [["C", 0.9], ["Cl", 0.0]]
    assert "".join(token for token, _ in pairs) == result["smiles"]
    assert result["mean_confidence"] == pytest.approx((0.5 + 0.25 + 299 * 0.9) / 302)
    assert result["incomplete"] is True
    assert json.loads(json.dumps(result)) == result


def test_a_complete_answer_is_not_flagged_incomplete_whatever_its_confidence() -> None:
    result = read(FakePredictor(("CCC", [("C", 0.01), ("C", 0.01), ("C", 0.01)])))
    assert result["incomplete"] is False


@pytest.mark.parametrize("last", ["C", "[", "1", "@", "Cl", "Br", "Si", "Zr"])
def test_the_dropped_token_may_be_any_one_vocabulary_token(last: str) -> None:
    result = read(FakePredictor(cut_off(last)))
    assert result["incomplete"] is True
    assert result["token_confidence"][-1] == [last, 0.0]


@pytest.mark.parametrize(
    "answer",
    [
        ("C", []),  # a prefix, but no confidences at all
        ("CCC", [("C", 0.9), ("C", 0.9)]),  # a prefix, two confidences: not at the limit
        (cut_off()[0], cut_off()[1][:-1]),  # 300 confidences for 302 tokens: two are missing
        (cut_off()[0] + "C", cut_off()[1][:-1]),  # 300 confidences for 303 tokens
    ],
)
def test_a_prefix_that_is_not_one_token_short_of_the_limit_is_our_error(
    answer: tuple[str, list[tuple[str, float]]],
) -> None:
    # Only a decode that stopped at the limit is flagged; any other answer whose confidences stop
    # short of the SMILES means the library changed (a 500, not a result a user could take for a
    # reading).
    with pytest.raises(RuntimeError, match="confidences for a SMILES that is longer"):
        read(FakePredictor(answer))


@pytest.mark.parametrize("leftover", ["CC", "Cc", "Xx", "Cl2", "cc", "()"])
def test_a_dropped_text_that_is_not_one_token_is_our_error(leftover: str) -> None:
    # `Cc` is two tokens (C, c), not an element; `Xx` is no vocabulary entry.
    with pytest.raises(RuntimeError, match="is not one token"):
        read(FakePredictor(cut_off(leftover)))


def test_an_incomplete_answer_is_logged_without_its_smiles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="decimer_ocsr"):
        read(FakePredictor(cut_off("N")))

    messages = [r.getMessage() for r in caplog.records if r.name == "decimer_ocsr"]
    assert messages == [
        "decode stopped at the token limit: 302 tokens, returned flagged incomplete"
    ]
    assert "CCCC" not in messages[0]


def test_a_complete_answer_logs_nothing_about_the_token_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="decimer_ocsr"):
        read(FakePredictor())
    assert [r for r in caplog.records if "token limit" in r.getMessage()] == []


def test_the_token_the_library_dropped_is_checked_for_a_special_spelling_too() -> None:
    # The cut-off answer is returned only if nothing in it is a special token, the one the pairs
    # lack included (it is only in the SMILES): that is still the 422, not a flagged answer.
    for special in ("4unk5", "<unk>", "4pad5"):
        with pytest.raises(ValueError, match="^no complete structure: the model wrote a special"):
            read(FakePredictor(cut_off(special)))


def test_a_special_token_in_a_cut_off_answer_is_still_a_422() -> None:
    # Cut off at the token limit AND a special token inside: the special token decides.
    with pytest.raises(ValueError, match="^no complete structure: the model wrote a special"):
        read(FakePredictor(cut_off(head=[("C", 0.9), ("4unk5", 0.9)])))


def test_the_multi_character_tokens_are_two_letter_element_symbols() -> None:
    # A slow test compares the set with the library's tokenizer; this is its shape.
    assert len(model_module.MULTI_CHAR_TOKENS) == 95
    assert all(
        len(t) == 2 and t[0].isupper() and t[1].islower() for t in model_module.MULTI_CHAR_TOKENS
    )
    assert model_module.TOKEN_LIMIT == 302


def test_the_422_detail_prefix_the_worker_and_ui_match_on_is_pinned() -> None:
    assert model_module.NO_STRUCTURE == "no complete structure"


SPECIAL_TOKENS = [
    "<pad>",
    "<unk>",
    "<start>",
    "<end>",
    "4pad5",  # what `DECIMER.utils.decoder` turns "<pad>" into: "<" -> "4", ">" -> "5"
    "4unk5",
    "4start5",
    "4end5",
]


@pytest.mark.parametrize("special", SPECIAL_TOKENS)
def test_a_special_token_in_the_output_is_a_422_not_a_smiles(special: str) -> None:
    # `<unk>` means the model met something outside its vocabulary; the others should never be
    # emitted mid-structure. Either way no structure was read.
    pairs = [("C", 0.9), (special, 0.9), ("C", 0.9)]
    with pytest.raises(ValueError, match="^no complete structure") as excinfo:
        read(FakePredictor(("C" + special + "C", pairs)))
    assert special in str(excinfo.value) or "special" in str(excinfo.value)


def test_a_special_token_is_a_422_even_when_the_library_dropped_it_from_the_smiles() -> None:
    # `detokenize_output` deletes "<start>"/"<end>" from the SMILES string but not from the pairs,
    # so the tokens no longer add up; that must still be the caller-visible 422, not a 500.
    pairs = [("C", 0.9), ("4start5", 0.9), ("C", 0.9)]
    with pytest.raises(ValueError, match="^no complete structure"):
        read(FakePredictor(("CC", pairs)))


def test_digits_four_and_five_on_their_own_are_ordinary_tokens() -> None:
    # Only a whole token equal to a decoded special token counts, not a substring of the SMILES.
    pairs = [("C", 0.9), ("4", 0.9), ("5", 0.9)]
    assert read(FakePredictor(("C45", pairs)))["smiles"] == "C45"


def test_the_accepted_formats_and_pixel_modes_are_pinned() -> None:
    assert model_module.FORMATS == ["PNG", "JPEG", "WEBP", "BMP"]
    assert model_module.SUPPORTED_MODES == frozenset(
        {"1", "L", "LA", "P", "PA", "RGB", "RGBA", "CMYK", "YCbCr", "I;16"}
    )


def test_tokens_that_do_not_add_up_to_the_smiles_in_any_other_way_are_our_error() -> None:
    # The client highlights low-confidence tokens inside the SMILES; that only works if the tokens
    # are the SMILES. Anything but the cut-off case above means the library changed.
    with pytest.raises(RuntimeError, match="do not add up"):
        read(FakePredictor(("CCC", [("N", 0.9), ("N", 0.9), ("N", 0.9)])))
    with pytest.raises(RuntimeError, match="do not add up"):
        read(FakePredictor(("", [("C", 0.9)])))


def test_whatever_the_params_say_the_predictor_is_called_the_same_way() -> None:
    # The hand-drawn model is out of scope: no form field may reach the predictor.
    fake = FakePredictor()
    OcsrModel(fake).infer(image_bytes(), {"hand_drawn": "true", "confidence": "false"})
    assert len(fake.calls) == 1


def test_the_model_answers_from_what_the_predictor_returns_not_from_the_image() -> None:
    # Guards against a model that returns a canned answer: two different answers give two results.
    first = read(FakePredictor(("CC", [("C", 0.9), ("C", 0.9)])))
    second = read(FakePredictor(("O", [("O", 0.1)])))
    assert first["smiles"] == "CC"
    assert second["smiles"] == "O"
    assert second["mean_confidence"] == pytest.approx(0.1)


# --- what the predictor is given -----------------------------------------------------------


def test_an_rgb_png_reaches_the_predictor_as_an_rgba_uint8_array_pixel_for_pixel() -> None:
    img = Image.new("RGB", (5, 3), (255, 255, 255))
    img.putpixel((1, 2), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    array = seen_by_predictor(buf.getvalue())

    assert array.dtype == np.uint8
    assert array.shape == (3, 5, 4)  # rows, columns, RGBA, as `.convert("RGBA")` gives
    assert tuple(array[2, 1]) == (10, 20, 30, 255)
    assert tuple(array[0, 0]) == (255, 255, 255, 255)


def test_the_array_is_what_the_library_would_have_read_from_the_file(tmp_path: Any) -> None:
    # DECIMER's path route is `Image.open(path).convert("RGBA")`; the array route must be the same.
    img = Image.new("RGB", (40, 30), (255, 255, 255))
    for x in range(10, 30):
        img.putpixel((x, 15), (0, 0, 0))
    path = tmp_path / "crop.png"
    img.save(path)

    array = seen_by_predictor(path.read_bytes())

    assert np.array_equal(array, np.asarray(Image.open(path).convert("RGBA")))


def test_transparency_is_left_for_the_library_to_composite_onto_white() -> None:
    # An RGBA crop with a fully transparent black background: the library paints that white.
    rgba = Image.new("RGBA", (6, 6), (0, 0, 0, 0))
    rgba.putpixel((2, 2), (0, 0, 0, 255))
    buf = io.BytesIO()
    rgba.save(buf, format="PNG")

    array = seen_by_predictor(buf.getvalue())

    assert tuple(array[0, 0]) == (0, 0, 0, 0)  # alpha kept; not flattened to black here
    flat = np.asarray(library_flatten(array).convert("RGB"))
    assert tuple(flat[0, 0]) == (255, 255, 255)
    assert tuple(flat[2, 2]) == (0, 0, 0)


def test_a_palette_png_with_a_transparent_colour_keeps_its_transparency() -> None:
    palette = Image.new("P", (4, 4), 0)
    palette.putpalette([0, 0, 0, 255, 255, 255])  # 0 black (transparent below), 1 white
    palette.putpixel((1, 1), 1)
    buf = io.BytesIO()
    palette.save(buf, format="PNG", transparency=0)

    array = seen_by_predictor(buf.getvalue())

    assert array[0, 0, 3] == 0
    assert array[1, 1, 3] == 255


@pytest.mark.parametrize(
    ("fmt", "mode", "color"),
    [
        ("PNG", "1", 1),
        ("PNG", "L", 200),
        ("PNG", "LA", (200, 255)),
        ("PNG", "RGB", (1, 2, 3)),
        ("PNG", "RGBA", (1, 2, 3, 4)),
        ("JPEG", "L", 200),
        ("JPEG", "RGB", (1, 2, 3)),
        ("JPEG", "CMYK", (0, 0, 0, 0)),
        ("WEBP", "RGB", (1, 2, 3)),
        ("WEBP", "RGBA", (1, 2, 3, 4)),
        ("BMP", "1", 1),
        ("BMP", "L", 200),
        ("BMP", "RGB", (1, 2, 3)),
    ],
)
def test_every_format_and_mode_we_accept_reaches_the_predictor_as_rgba(
    fmt: str, mode: str, color: int | tuple[int, ...]
) -> None:
    array = seen_by_predictor(image_bytes(7, 5, fmt=fmt, mode=mode, color=color))
    assert array.dtype == np.uint8
    assert array.shape == (5, 7, 4)


def test_a_16_bit_grayscale_png_is_scaled_not_clipped_to_white() -> None:
    # Pillow's own conversion clips values above 255, so a 16-bit crop would come out all white.
    gray = Image.fromarray(np.full((4, 4), 0x8000, dtype=np.uint16))  # mid grey
    assert gray.mode == "I;16"
    buf = io.BytesIO()
    gray.save(buf, format="PNG")

    array = seen_by_predictor(buf.getvalue())

    assert tuple(array[0, 0]) == (128, 128, 128, 255)


def test_exif_orientation_is_not_applied() -> None:
    # The API applies it at upload; legacy did not either, and the pixels given to DECIMER
    # must stay the ones in the file.
    array = seen_by_predictor(jpeg_with_exif_orientation(30, 10, 6))
    assert array.shape == (10, 30, 4)


# --- bad input is a ValueError (422), never a crash ---------------------------------------


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image", b"\x89PNG\r\n\x1a\n" + b"\0" * 20, image_bytes(30, 30)[:40]],
    ids=["empty", "text", "png header only", "truncated png"],
)
def test_bytes_that_do_not_decode_are_a_value_error(data: bytes) -> None:
    fake = FakePredictor()
    with pytest.raises(ValueError, match="could not decode image"):
        OcsrModel(fake).infer(data, {})
    assert fake.calls == []


@pytest.mark.parametrize("fmt", ["GIF", "TIFF"])
def test_formats_outside_the_allow_list_are_refused_without_a_decoder_running(fmt: str) -> None:
    fake = FakePredictor()
    with pytest.raises(ValueError, match="could not decode image"):
        OcsrModel(fake).infer(image_bytes(8, 8, fmt=fmt), {})
    assert fake.calls == []


def test_the_pixel_cap_is_decided_from_the_header_before_anything_is_decoded() -> None:
    fake = FakePredictor()
    side = int(model_module.MAX_PIXELS**0.5) + 1
    with pytest.raises(ValueError, match="image too large"):
        OcsrModel(fake).infer(png_declaring(side, side), {})
    assert fake.calls == []


def test_the_pixel_cap_boundary_is_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module, "MAX_PIXELS", 100)
    fake = FakePredictor()
    model = OcsrModel(fake)

    model.infer(image_bytes(10, 10), {})  # exactly at the cap
    with pytest.raises(ValueError, match="image too large"):
        model.infer(image_bytes(101, 1), {})
    assert len(fake.calls) == 1


def test_the_longest_side_is_capped_too_because_memory_follows_its_square(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DECIMER pads to a square of 1.2 x the longest side: a thin strip is few pixels but a
    # huge canvas.
    monkeypatch.setattr(model_module, "MAX_SIDE", 50)
    fake = FakePredictor()
    model = OcsrModel(fake)

    model.infer(image_bytes(50, 2), {})  # exactly at the limit, in both orientations
    model.infer(image_bytes(2, 50), {})
    with pytest.raises(ValueError, match=r"image too long: 51x2 pixels, longest side at most 50"):
        model.infer(image_bytes(51, 2), {})
    with pytest.raises(ValueError, match="image too long"):
        model.infer(image_bytes(2, 51), {})
    assert len(fake.calls) == 2


def test_the_side_cap_is_decided_from_the_header_too() -> None:
    fake = FakePredictor()
    with pytest.raises(ValueError, match="image too long"):
        OcsrModel(fake).infer(png_declaring(model_module.MAX_SIDE + 1, 1), {})
    assert fake.calls == []


def test_the_caps_are_pinned() -> None:
    assert model_module.MAX_PIXELS == 17_500_000
    assert model_module.MAX_SIDE == 8192


def test_the_cap_message_says_how_big_the_image_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(model_module, "MAX_PIXELS", 5)
    with pytest.raises(ValueError, match=r"image too large: 3x2 pixels, at most 5"):
        OcsrModel(FakePredictor()).infer(image_bytes(3, 2), {})


def test_an_image_pillow_itself_calls_a_decompression_bomb_is_too_large_not_undecodable() -> None:
    # Pillow refuses to even open images above twice its own limit (about 179 Mpx); the answer
    # should still say why.
    with pytest.raises(ValueError, match="image too large"):
        OcsrModel(FakePredictor()).infer(png_declaring(20_000, 20_000), {})


def test_an_unsupported_pixel_mode_is_a_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # TIFF is not accepted, but a decoder or allow-list change must not turn LAB into a 500.
    monkeypatch.setattr(model_module, "FORMATS", [*model_module.FORMATS, "TIFF"])
    lab = image_bytes(6, 6, fmt="TIFF", mode="LAB", color=(50, 0, 0))
    fake = FakePredictor()

    with pytest.raises(ValueError, match="unsupported pixel mode: LAB"):
        OcsrModel(fake).infer(lab, {})
    assert fake.calls == []


def test_the_real_predictor_loads_the_printed_model_up_front_and_asks_for_it_with_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The wiring to the library, without TensorFlow: a stand-in `DECIMER` module records the calls.
    seen: list[tuple[object, dict[str, object]]] = []
    loads: list[dict[str, object]] = []

    def load_models(**kwargs: object) -> None:
        assert seen == []  # before any prediction: the first request must not pay for the load
        loads.append(kwargs)

    def predict_SMILES(image: object, **kwargs: object) -> tuple[str, list[tuple[str, float]]]:  # noqa: N802
        seen.append((image, kwargs))
        return "CC", [("C", 0.5), ("C", 0.75)]

    stand_in = types.ModuleType("DECIMER")
    stand_in.load_models = load_models  # type: ignore[attr-defined]
    stand_in.predict_SMILES = predict_SMILES  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "DECIMER", stand_in)

    predict = model_module._real_predict_fn()
    # Said outright (the library's defaults happen to be the same): only the printed model.
    assert loads == [{"printed": True, "hand_drawn": False}]
    answer = predict("ARRAY")  # type: ignore[arg-type]

    assert answer == ("CC", [("C", 0.5), ("C", 0.75)])
    assert seen == [("ARRAY", {"confidence": True, "hand_drawn": False})]
    assert len(loads) == 1


def test_what_the_library_printed_is_logged_even_when_importing_it_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # The library may print while it is imported; if it then raises, those lines are the only
    # clue, and must not be lost.
    package = tmp_path / "DECIMER"
    package.mkdir()
    (package / "__init__.py").write_text(
        "print('Downloading trained model to /some/dir')\nraise OSError('connection reset')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "DECIMER", raising=False)

    with (
        caplog.at_level(logging.INFO, logger="decimer_ocsr"),
        pytest.raises(OSError, match="reset"),
    ):
        model_module._real_predict_fn()

    assert "Downloading trained model to /some/dir" in caplog.text


def test_what_the_library_printed_is_logged_even_when_loading_the_models_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    # decimer 2.8.1 prints (download progress, paths) in `load_models`, not in the import, and
    # the failures worth explaining (a failed download, a corrupt SavedModel) happen there.
    package = tmp_path / "DECIMER"
    package.mkdir()
    (package / "__init__.py").write_text(
        "def predict_SMILES(*args, **kwargs):\n"
        "    return '', []\n"
        "def load_models(**kwargs):\n"
        "    print('Downloading trained model to /some/dir')\n"
        "    raise OSError('connection reset')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "DECIMER", raising=False)

    with (
        caplog.at_level(logging.INFO, logger="decimer_ocsr"),
        pytest.raises(OSError, match="reset"),
    ):
        model_module._real_predict_fn()

    assert "Downloading trained model to /some/dir" in caplog.text


def test_what_the_library_printed_is_logged_when_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "DECIMER"
    package.mkdir()
    (package / "__init__.py").write_text(
        "print('printed while importing')\n"
        "def predict_SMILES(*args, **kwargs):\n"
        "    return '', []\n"
        "def load_models(**kwargs):\n"
        "    print('printed while loading')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "DECIMER", raising=False)

    with caplog.at_level(logging.INFO, logger="decimer_ocsr"):
        model_module._real_predict_fn()

    assert "printed while importing" in caplog.text
    assert "printed while loading" in caplog.text
    assert "printed while" not in capsys.readouterr().out


# --- failures inside the library are ours, not the caller's -------------------------------


def test_a_value_error_inside_the_library_is_reported_as_a_runtime_error() -> None:
    # create_app maps ValueError to 422 (the caller's fault); the library failing is ours (500).
    with pytest.raises(RuntimeError, match="recognition failed: zero-size array"):
        read(FakePredictor(error=ValueError("zero-size array to reduction operation")))


def test_any_other_library_error_propagates_for_a_500() -> None:
    with pytest.raises(OSError, match="disk"):
        read(FakePredictor(error=OSError("disk")))


def test_infer_before_load_is_a_clear_error() -> None:
    with pytest.raises(RuntimeError, match="not loaded"):
        OcsrModel().infer(image_bytes(), {})


def test_nothing_is_written_to_the_working_directory_or_temp_dir(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DECIMER accepts an array, so no temporary file is needed (and none can be left behind).
    import tempfile

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    read(FakePredictor())
    with pytest.raises(RuntimeError):
        read(FakePredictor(error=RuntimeError("boom")))
    with pytest.raises(ValueError, match="could not decode"):
        read(FakePredictor(), b"nope")
    assert list(tmp_path.iterdir()) == []


# --- the lock ------------------------------------------------------------------------------


def test_requests_and_selftests_never_overlap_on_the_one_model() -> None:
    # /readyz runs the selftest outside the /infer gate, so it can overlap a request. A selftest
    # that finds the model busy returns at once (see below), so it may or may not call the model.
    fake = FakePredictor(delay=0.05)
    model = OcsrModel(fake)
    threads = [threading.Thread(target=model.infer, args=(image_bytes(), {})) for _ in range(3)] + [
        threading.Thread(target=model.selftest) for _ in range(2)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert 3 <= len(fake.calls) <= 5
    assert fake.max_overlap == 1


# --- selftest ------------------------------------------------------------------------------


def test_selftest_passes_when_the_model_reads_the_embedded_structure_correctly() -> None:
    fake = FakePredictor()
    OcsrModel(fake).selftest()
    (array,) = fake.calls
    assert array.ndim == 3  # a real image went through the whole decode path


def test_selftest_fails_when_the_model_reads_a_different_structure() -> None:
    # A model that runs but answers wrongly (wrong weights, broken tokenizer) must not be ready.
    fake = FakePredictor(("CCO", [("C", 0.99), ("C", 0.99), ("O", 0.99)]))
    with pytest.raises(RuntimeError, match="selftest failed") as excinfo:
        OcsrModel(fake).selftest()
    assert "'CCO'" in str(excinfo.value)  # what it read
    assert DAIDZEIN in str(excinfo.value)  # what it should have read


def test_selftest_fails_on_an_empty_answer() -> None:
    with pytest.raises(RuntimeError, match="selftest failed"):
        OcsrModel(FakePredictor(("", []))).selftest()


def test_selftest_fails_when_the_model_refuses_the_embedded_structure() -> None:
    # A selftest answer that is cut off at the token limit is a failed selftest, not a 422.
    fake = FakePredictor(cut_off())
    with pytest.raises(RuntimeError, match="selftest failed.*no complete structure"):
        OcsrModel(fake).selftest()


def test_selftest_does_not_swallow_a_crash_in_the_model() -> None:
    with pytest.raises(OSError, match="weights"):
        OcsrModel(FakePredictor(error=OSError("weights gone"))).selftest()


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


WRONG = ("CCO", [("C", 0.99), ("C", 0.99), ("O", 0.99)])  # a model that runs but reads wrongly
CUT_OFF = cut_off()  # a decode cut off at the token limit


def passed_model(clock: Clock, **options: float) -> tuple[OcsrModel, FakePredictor]:
    """A model whose real selftest passed just now; the fake's call log and `entered` are clear."""
    fake = FakePredictor()
    model = OcsrModel(fake, clock=clock, **options)
    model.selftest()
    assert len(fake.calls) == 1
    fake.calls.clear()
    fake.entered.clear()  # so that waiting for it means waiting for the next decode
    return model, fake


def test_the_model_keeps_its_own_readiness_window_whatever_the_skeleton_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimer_ocsr import app as ocsr_app

    assert model_module.READY_WINDOW == 30.0
    seen: dict[str, Any] = {}

    def spy(model: object, limits: object = None) -> str:
        seen["model"] = model
        return "app"

    monkeypatch.setattr(ocsr_app, "create_app", spy)
    monkeypatch.setattr(ocsr_app, "LIMITS", Limits(ready_cache_seconds=7.0))
    ocsr_app.build_app()
    assert seen["model"].ready_window == 30.0  # not read from LIMITS


def test_the_readiness_timings_are_pinned() -> None:
    model = OcsrModel(FakePredictor())
    assert model.ready_window == 30.0  # a completed decode keeps the model ready this long
    assert model.pass_interval == 300.0  # a real selftest pass is needed at least this often
    assert model.hang_after == 90.0  # above the 55 s a decode at the token limit takes
    assert model.lock_wait == 25.0  # below the 35 s the container's healthcheck allows


# --- a selftest that fails keeps failing (it records nothing) -----------------------------


def test_a_failed_selftest_does_not_make_the_next_probe_ready() -> None:
    # Reproduced with wrong weights: 503, then "ready" for 30 s, because the selftest's own decode
    # was recorded as a completed decode before its answer was checked. The healthcheck probes
    # every 5 s, so that was enough to turn an unready container healthy.
    fake = FakePredictor(WRONG)
    model = OcsrModel(fake, clock=Clock())

    for probe in range(3):
        with pytest.raises(RuntimeError, match="selftest failed"):
            model.selftest()
        assert len(fake.calls) == probe + 1  # and it really ran each time, not skipped


def test_a_request_that_completes_on_a_model_that_never_passed_is_not_ready() -> None:
    # Wrong weights still answer requests (with the wrong structure). A completed request must not
    # stand in for the first real selftest.
    fake = FakePredictor(WRONG)
    model = OcsrModel(fake, clock=Clock())
    model.infer(image_bytes(), {})

    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()
    assert len(fake.calls) == 2


def test_a_request_after_a_failed_selftest_does_not_make_the_model_ready() -> None:
    fake = FakePredictor(WRONG)
    model = OcsrModel(fake, clock=Clock())
    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()
    model.infer(image_bytes(), {})  # a request completes within the window

    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()


def test_a_passed_selftest_is_remembered_for_the_window_and_then_runs_again() -> None:
    # The skeleton's cache is off (LIMITS.ready_cache_seconds is 0), so the model caches its own
    # pass; otherwise a 5 s healthcheck would keep it decoding about half of the time.
    clock = Clock()
    model, fake = passed_model(clock)
    fake.answer = WRONG  # from now on the model answers wrongly

    clock.now += 30.0  # exactly the window
    model.selftest()
    assert len(fake.calls) == 0  # remembered

    clock.now += 0.001
    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()
    assert len(fake.calls) == 1


# --- /readyz does not wait for the model; a hung decode is noticed --------------------------


def test_selftest_returns_at_once_while_a_decode_is_running() -> None:
    # A token-limit decode holds the model for 38-55 s; /readyz must not queue behind it.
    gate = threading.Event()
    clock = Clock()
    model, fake = passed_model(clock)
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    running.start()
    try:
        assert fake.entered.wait(10)
        clock.now += 40.0  # a long, but ordinary, token-limit decode

        done = threading.Event()
        selftest = threading.Thread(target=lambda: (model.selftest(), done.set()))
        selftest.start()
        assert done.wait(5), "selftest waited for the running decode"
        assert len(fake.calls) == 1  # and did not start a second decode
    finally:
        gate.set()
        running.join(timeout=30)


def test_a_decode_that_has_run_for_more_than_90_seconds_fails_the_probe() -> None:
    # A hung decode keeps the lock busy for ever; "alive and busy" must not last for ever.
    gate = threading.Event()
    clock = Clock()
    model, fake = passed_model(clock)
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    running.start()
    try:
        assert fake.entered.wait(10)
        for seconds in (1.0, 59.0, 30.0):  # 1, 60 and 90 s into the decode: still fine
            clock.now += seconds
            model.selftest()
        clock.now += 1.0  # 91 s
        with pytest.raises(RuntimeError, match=r"decode running for 91 s"):
            model.selftest()
        clock.now += 100.0
        with pytest.raises(RuntimeError, match=r"decode running for 191 s"):
            model.selftest()
    finally:
        gate.set()
        running.join(timeout=30)

    model.selftest()  # the decode ended: the model is fine again (and the pass is recent)
    assert len(fake.calls) == 1


def test_the_decode_start_is_cleared_when_it_ends_even_if_it_crashed() -> None:
    # A stale start time would make the next probe that finds the lock busy report a hung decode.
    model, fake = passed_model(Clock())
    model.infer(image_bytes(), {})
    assert model._decode_started is None
    fake.error = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        model.infer(image_bytes(), {})
    assert model._decode_started is None


# --- the window after a completed decode ---------------------------------------------------


def test_selftest_is_skipped_after_a_decode_that_completed_within_the_window() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    fake.answer = WRONG  # would fail the selftest if it ran
    model.infer(image_bytes(), {})
    assert len(fake.calls) == 1

    clock.now += 30.0  # exactly the window: the skeleton's cache also holds at 30 s
    model.selftest()
    assert len(fake.calls) == 1


def test_selftest_really_runs_when_no_decode_completed_within_the_window() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    fake.answer = WRONG
    model.infer(image_bytes(), {})

    clock.now += 30.001
    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()
    assert len(fake.calls) == 2


def test_selftest_runs_on_a_model_that_has_not_decoded_anything_yet() -> None:
    fake = FakePredictor()
    OcsrModel(fake, clock=Clock()).selftest()
    assert len(fake.calls) == 1


def test_a_decode_that_ends_at_the_token_limit_still_counts_as_ready() -> None:
    # The decode completed (the model is alive and answering), it just is not a structure: the
    # request is answered, flagged incomplete.
    clock = Clock()
    model, fake = passed_model(clock)
    fake.answer = CUT_OFF
    assert model.infer(image_bytes(), {})["incomplete"] is True

    model.selftest()  # would fail on this answer if it ran
    assert len(fake.calls) == 1


def test_a_decode_that_crashed_does_not_count_as_ready() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    fake.error = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        model.infer(image_bytes(), {})
    clock.now += 31.0  # past the window of the earlier pass

    with pytest.raises(RuntimeError, match="boom"):
        model.selftest()  # it ran, and failed
    assert len(fake.calls) == 2


def test_a_decode_counts_from_when_the_predictor_returns_not_from_when_it_started() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    gate = threading.Event()
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    running.start()
    assert fake.entered.wait(10)
    clock.now += 50.0  # the decode takes longer than the window
    gate.set()
    running.join(timeout=30)

    model.selftest()  # it finished just now, so the model is ready
    assert len(fake.calls) == 1


# --- a real selftest pass at least every few minutes ---------------------------------------


def test_a_real_selftest_is_required_every_five_minutes_whatever_requests_did() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    clock.now += 299.9
    fake.answer = WRONG
    model.infer(image_bytes(), {})  # completes at 299.9 s: inside the 30 s window
    clock.now += 0.6  # 300.5 s since the pass

    with pytest.raises(RuntimeError, match="selftest failed"):
        model.selftest()
    assert len(fake.calls) == 2  # the real selftest ran in spite of the recent request


def test_five_minutes_exactly_is_still_fine() -> None:
    clock = Clock()
    model, fake = passed_model(clock)
    clock.now += 299.9
    model.infer(image_bytes(), {})
    clock.now += 0.1  # 300 s since the pass

    model.selftest()
    assert len(fake.calls) == 1


def test_an_overdue_selftest_waits_for_the_model_and_then_runs_for_real() -> None:
    clock = Clock()
    model, fake = passed_model(clock, lock_wait=5.0)
    gate = threading.Event()
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    clock.now += 299.0
    running.start()
    assert fake.entered.wait(10)
    clock.now += 2.0  # 301 s since the pass; the decode has run for 2 s
    threading.Timer(0.2, gate.set).start()  # the decode ends while the selftest waits

    model.selftest()  # waited, then ran the real thing

    running.join(timeout=30)
    assert len(fake.calls) == 2  # the request's decode and the selftest's own


def test_an_overdue_selftest_that_cannot_get_the_model_in_time_says_why() -> None:
    clock = Clock()
    model, fake = passed_model(clock, lock_wait=0.2)
    gate = threading.Event()
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    clock.now += 299.0
    running.start()
    try:
        assert fake.entered.wait(10)
        clock.now += 2.0

        with pytest.raises(RuntimeError, match=r"no real selftest pass for 301 s.*busy for 0 s"):
            model.selftest()
        assert len(fake.calls) == 1  # no second decode was started
    finally:
        gate.set()
        running.join(timeout=30)


def test_an_overdue_selftest_does_not_wait_for_a_decode_that_is_already_hung() -> None:
    clock = Clock()
    model, fake = passed_model(clock, lock_wait=30.0)
    gate = threading.Event()
    fake.gate = gate
    running = threading.Thread(target=model.infer, args=(image_bytes(), {}))
    running.start()
    try:
        assert fake.entered.wait(10)
        clock.now += 400.0  # overdue and the decode has run for 400 s

        started = time.monotonic()
        with pytest.raises(RuntimeError, match=r"decode running for 400 s"):
            model.selftest()
        assert time.monotonic() - started < 5  # it did not sit out the 30 s lock wait
    finally:
        gate.set()
        running.join(timeout=30)


def test_the_embedded_image_is_a_small_real_png() -> None:
    data = model_module.selftest_png()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(data) < 20_000
    image = Image.open(io.BytesIO(data))
    image.load()
    assert min(image.size) >= 100  # a structure, not a thumbnail


def test_the_expected_selftest_smiles_is_the_structure_in_the_embedded_image() -> None:
    # The oracle does not come from the model: the expected string must be the molecule we say it
    # is, checked with RDKit (a dev dependency; the service itself does not need it).
    from rdkit import Chem

    mol = Chem.MolFromSmiles(model_module.SELFTEST_SMILES)
    assert mol is not None
    assert Chem.MolToInchiKey(mol) == model_module.SELFTEST_INCHIKEY


def test_decoding_survives_pillow_raising_odd_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise SyntaxError("broken chunk")

    monkeypatch.setattr(Image, "open", explode)
    with pytest.raises(ValueError, match="could not decode image"):
        OcsrModel(FakePredictor()).infer(image_bytes(), {})


def test_math_is_finite_for_float32_extremes() -> None:
    # Tiny but legal confidences (softmax underflow) must not break the mean.
    result = read(FakePredictor(("CC", [("C", np.float32(1e-30)), ("C", np.float32(1.0))])))
    assert math.isfinite(result["mean_confidence"])
