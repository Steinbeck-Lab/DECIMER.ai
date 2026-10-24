import base64
import io
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import pytest
from helpers import FakeSegmenter, OverlapProbe, encode_image, iou, png_bytes, rgba_crop
from PIL import Image

import decimer_seg.model as seg_model
from decimer_seg.model import SegModel

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SEG_ROOT = Path(__file__).resolve().parents[1]


def decode_png_field(value: str) -> np.ndarray:
    raw = base64.b64decode(value, validate=True)
    assert raw.startswith(PNG_SIGNATURE)
    decoded = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded is not None
    return decoded


def run_python(code: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=SEG_ROOT,
    )


def seen_by_segmenter(data: bytes) -> np.ndarray:
    fake = FakeSegmenter()
    SegModel(fake).infer(data, {})
    (image,) = fake.calls
    return image


# --- one call into the library at a time ----------------------------------------------------
#
# decimer_segmentation is not thread-safe: two segment_chemical_structures() calls at once (even
# from two threads) abort the process. /readyz runs the selftest outside the /infer gate (see
# decimer_service), so the model has to protect the library itself, as the ocsr and cls models do.


def test_the_library_is_never_entered_by_two_threads_at_once() -> None:
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    model = SegModel(probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(model.infer, png_bytes(100, 100), {})
        probe.wait_until_inside()
        second = pool.submit(model.infer, png_bytes(100, 100), {})
        time.sleep(0.2)  # long enough for an unguarded second call to get in

        assert probe.inside == 1
        probe.release.set()
        first.result(timeout=10)
        second.result(timeout=10)
    assert probe.peak == 1
    assert len(probe.calls) == 2  # both ran, one after the other


def test_selftest_returns_at_once_while_a_page_is_being_segmented() -> None:
    # No second call into the library, and no waiting for the first: a page in progress shows
    # that the model is alive (and a probe that waited would time out behind a slow page).
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    model = SegModel(probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(model.infer, png_bytes(100, 100), {})
        probe.wait_until_inside()
        probed = pool.submit(model.selftest)

        probed.result(timeout=2)  # returned, while the page is still inside the library
        assert probe.inside == 1
        probe.release.set()
        running.result(timeout=10)
    assert probe.peak == 1
    assert len(probe.calls) == 1  # the selftest did not segment anything


def test_a_request_arriving_during_a_selftest_waits_for_it() -> None:
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    model = SegModel(probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        selftest = pool.submit(model.selftest)
        probe.wait_until_inside()
        request = pool.submit(model.infer, png_bytes(100, 100), {})
        time.sleep(0.2)  # long enough for an unguarded request to get in

        assert probe.inside == 1
        probe.release.set()
        selftest.result(timeout=10)
        request.result(timeout=10)
    assert probe.peak == 1
    assert len(probe.calls) == 2  # the selftest, then the request


def test_two_probes_at_once_run_one_selftest_between_them() -> None:
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    model = SegModel(probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(model.selftest)
        probe.wait_until_inside()
        second = pool.submit(model.selftest)

        second.result(timeout=2)
        probe.release.set()
        first.result(timeout=10)
    assert probe.peak == 1
    assert len(probe.calls) == 1


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_segmentation_that_never_ends_fails_the_probe() -> None:
    # "Alive and busy" must not last for ever: a hung call keeps the library busy for ever.
    probe = OverlapProbe([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    clock = Clock()
    model = SegModel(probe, hang_after=100.0, clock=clock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(model.infer, png_bytes(100, 100), {})
        probe.wait_until_inside()
        try:
            clock.now += 100.0  # exactly the limit: still fine
            model.selftest()
            clock.now += 1.0
            with pytest.raises(RuntimeError, match=r"running for 101 s"):
                model.selftest()
        finally:
            probe.release.set()
            running.result(timeout=10)
    model.selftest()  # it ended: the model is fine again


def test_a_finished_segmentation_leaves_no_start_time_behind_to_look_like_a_hang() -> None:
    # A stale start time would make a probe that finds the library just taken (a call that has
    # not started its clock yet) report a hang. Held by hand here to stand for that moment.
    fake = FakeSegmenter([rgba_crop(30, 20)], [(10, 20, 40, 40)])
    clock = Clock()
    model = SegModel(fake, hang_after=100.0, clock=clock)
    model.infer(png_bytes(100, 100), {})
    fake.error = ValueError("inside mrcnn")
    with pytest.raises(RuntimeError, match="segmentation failed"):
        model.infer(png_bytes(100, 100), {})  # a failed call must clear it too
    clock.now += 10_000.0

    assert model._library_lock.acquire(blocking=False)
    try:
        model.selftest()
    finally:
        model._library_lock.release()


# --- result shape -------------------------------------------------------------------------


def test_bbox_is_converted_from_y0x0y1x1_to_x0y0x1y1_plain_ints() -> None:
    # The library's (y0, x0, y1, x1) uses numpy ints; four distinct values expose any swap.
    bbox = tuple(np.int64(v) for v in (10, 20, 30, 40))
    fake = FakeSegmenter([rgba_crop(20, 20)], [bbox])
    result = SegModel(fake).infer(png_bytes(200, 100), {})

    boxes = [s["bbox"] for s in result["segments"]]
    assert boxes == [[20, 10, 40, 30]]
    assert all(type(v) is int for v in boxes[0])
    json.dumps(result)  # numpy scalars anywhere in the result would raise here


def test_crop_png_round_trips_an_rgba_array() -> None:
    crop = rgba_crop(7, 5, seed=1)
    result = SegModel(FakeSegmenter([crop], [(0, 0, 7, 5)])).infer(png_bytes(50, 50), {})

    decoded = decode_png_field(result["segments"][0]["png"])
    assert decoded.shape == (7, 5, 4)
    assert np.array_equal(decoded, crop)


def test_segments_keep_the_libraries_order_and_get_sequential_indices() -> None:
    crops = [rgba_crop(4, 6, seed=1), rgba_crop(5, 3, seed=2), rgba_crop(2, 2, seed=3)]
    bboxes = [(0, 0, 4, 6), (10, 10, 15, 13), (20, 30, 22, 32)]
    result = SegModel(FakeSegmenter(crops, bboxes)).infer(png_bytes(100, 100), {})

    assert [s["index"] for s in result["segments"]] == [0, 1, 2]
    assert [s["bbox"] for s in result["segments"]] == [
        [0, 0, 6, 4],
        [10, 10, 13, 15],
        [30, 20, 32, 22],
    ]
    for segment, crop in zip(result["segments"], crops, strict=True):
        assert np.array_equal(decode_png_field(segment["png"]), crop)


def test_reports_page_size_as_width_then_height() -> None:
    result = SegModel(FakeSegmenter()).infer(png_bytes(width=200, height=100), {})
    assert (result["width"], result["height"]) == (200, 100)


def test_no_structures_gives_an_empty_list() -> None:
    result = SegModel(FakeSegmenter()).infer(png_bytes(20, 20), {"page_index": "4"})
    assert result == {"page_index": 4, "width": 20, "height": 20, "segments": []}


@pytest.mark.parametrize(
    "degenerate",
    [
        (0, 0, 0, 0),  # what the library answers for an empty mask, with a 1x1 crop
        (5, 5, 5, 9),  # (y0, x0, y1, x1): no height
        (5, 5, 9, 5),  # no width
        (9, 5, 5, 9),  # upside down
        (5, 9, 9, 5),  # back to front
    ],
)
def test_boxes_without_area_are_dropped_and_the_rest_renumbered(
    degenerate: tuple[int, int, int, int],
) -> None:
    fake = FakeSegmenter([rgba_crop(1, 1), rgba_crop(3, 3)], [degenerate, (20, 30, 23, 33)])
    result = SegModel(fake).infer(png_bytes(50, 50), {})
    assert [(s["index"], s["bbox"]) for s in result["segments"]] == [(0, [30, 20, 33, 23])]


# --- page_index ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "expected"),
    [({}, 0), ({"page_index": "0"}, 0), ({"page_index": "7"}, 7), ({"page_index": "99999"}, 99999)],
)
def test_page_index_defaults_to_zero_and_is_echoed(params: dict[str, str], expected: int) -> None:
    assert SegModel(FakeSegmenter()).infer(png_bytes(4, 4), params)["page_index"] == expected


@pytest.mark.parametrize(
    "bad",
    [
        "abc",
        "1.5",
        "",
        "-1",
        "+7",
        " 7",
        "7 ",
        "7\n",
        "1_000",
        "2e3",
        "0x10",
        "٧",  # ARABIC-INDIC DIGIT SEVEN: int() accepts it, the API contract does not
        "７",  # FULLWIDTH DIGIT SEVEN
        "100000",  # more than 5 digits
    ],
)
def test_bad_page_index_is_a_value_error(bad: str) -> None:
    with pytest.raises(ValueError, match="page_index"):
        SegModel(FakeSegmenter()).infer(png_bytes(4, 4), {"page_index": bad})


# --- decoding -----------------------------------------------------------------------------


@pytest.mark.parametrize("garbage", [b"this is not an image", b"", b"\x89PNG\r\n\x1a\nbroken"])
def test_undecodable_bytes_are_a_value_error(garbage: bytes) -> None:
    fake = FakeSegmenter()
    with pytest.raises(ValueError, match="could not decode image"):
        SegModel(fake).infer(garbage, {})
    assert fake.calls == []


def test_segmenter_gets_a_bgr_uint8_image() -> None:
    image = seen_by_segmenter(png_bytes(8, 6, bgr=(0, 0, 255)))  # pure red in BGR order

    assert image.dtype == np.uint8
    assert image.shape == (6, 8, 3)
    assert image[0, 0].tolist() == [0, 0, 255]


def test_transparent_png_is_composited_onto_white() -> None:
    rgba = np.zeros((20, 30, 4), np.uint8)  # fully transparent; the colour data underneath is black
    rgba[5:8, 5:25] = (0, 0, 0, 255)  # an opaque black stroke
    rgba[12, 3] = (10, 20, 30, 255)  # an opaque colour, to catch swapped channels
    rgba[14, 3] = (0, 0, 0, 128)  # half-transparent black
    rgba[16, 3] = (0, 0, 255, 128)  # half-transparent red (BGR order)

    image = seen_by_segmenter(encode_image(rgba))

    assert image.shape == (20, 30, 3)
    assert image.dtype == np.uint8
    assert image[0, 0].tolist() == [255, 255, 255]
    assert image[6, 10].tolist() == [0, 0, 0]
    assert image[12, 3].tolist() == [10, 20, 30]
    assert np.abs(image[14, 3].astype(int) - 127).max() <= 1
    assert np.abs(image[16, 3].astype(int) - [127, 127, 255]).max() <= 1


def test_opaque_rgba_png_loses_only_its_alpha_channel() -> None:
    rgba = rgba_crop(6, 9, seed=4)
    rgba[:, :, 3] = 255
    image = seen_by_segmenter(encode_image(rgba))
    assert image.shape == (6, 9, 3)
    assert np.array_equal(image, rgba[:, :, :3])


def test_grayscale_png_reaches_the_segmenter_as_three_equal_channels() -> None:
    gray = (np.arange(12 * 16, dtype=np.uint8) % 251).reshape(12, 16)
    image = seen_by_segmenter(encode_image(gray))

    assert image.shape == (12, 16, 3)
    assert image.dtype == np.uint8
    for channel in range(3):
        assert np.array_equal(image[:, :, channel], gray)


def test_sixteen_bit_pngs_are_scaled_down_to_eight_bit() -> None:
    gray16 = np.array([[0, 257 * 100, 65535]], np.uint16)
    image = seen_by_segmenter(encode_image(gray16))
    assert image.dtype == np.uint8
    assert image.shape == (1, 3, 3)
    assert image[0, :, 0].tolist() == [0, 100, 255]

    bgr16 = np.array([[[0, 257 * 50, 65535]]], np.uint16)
    image = seen_by_segmenter(encode_image(bgr16))
    assert image.shape == (1, 1, 3)
    assert image[0, 0].tolist() == [0, 50, 255]


def test_sixteen_bit_png_with_alpha_is_scaled_and_composited() -> None:
    bgra16 = np.array([[[0, 0, 0, 0], [257 * 40, 257 * 80, 257 * 120, 65535]]], np.uint16)
    image = seen_by_segmenter(encode_image(bgra16))
    assert image.dtype == np.uint8
    assert image[0, 0].tolist() == [255, 255, 255]
    assert image[0, 1].tolist() == [40, 80, 120]


def test_float_images_are_refused_rather_than_guessed_at() -> None:
    float_tiff = encode_image(np.zeros((4, 4), np.float32), ".tif")
    with pytest.raises(ValueError, match="unsupported"):
        SegModel(FakeSegmenter()).infer(float_tiff, {})


def test_exif_orientation_is_ignored_so_boxes_are_in_the_stored_pixel_grid() -> None:
    # Documented contract: the caller sends upright pixels (the API normalises uploads).
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise when displayed
    buffer = io.BytesIO()
    Image.new("RGB", (100, 50), (255, 255, 255)).save(buffer, "JPEG", exif=exif)

    result = SegModel(FakeSegmenter()).infer(buffer.getvalue(), {})
    assert (result["width"], result["height"]) == (100, 50)


# --- failures inside the library ----------------------------------------------------------


def test_library_value_error_is_not_reported_as_bad_input() -> None:
    # Only the caller's own mistakes may become 422; a ValueError from inside the library is a 500.
    model = SegModel(FakeSegmenter(error=ValueError("shape mismatch deep in mrcnn")))
    with pytest.raises(RuntimeError, match="segmentation failed"):
        model.infer(png_bytes(10, 10), {})


def test_segments_and_bboxes_of_different_length_are_an_error() -> None:
    model = SegModel(FakeSegmenter([rgba_crop(2, 2)], []))
    with pytest.raises(RuntimeError, match="1 segments but 0 bboxes"):
        model.infer(png_bytes(10, 10), {})


def test_infer_before_load_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="not loaded"):
        SegModel().infer(png_bytes(10, 10), {})


# --- selftest -----------------------------------------------------------------------------


def test_selftest_asset_is_a_small_real_png() -> None:
    data = seg_model.selftest_png()
    assert data.startswith(PNG_SIGNATURE)
    assert 1_000 < len(data) < 20_000
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert image.shape[0] >= 256 and image.shape[1] >= 256
    assert 0 < int((image < 128).sum()) < image.size // 10  # drawn lines on a mostly white page


def test_selftest_goes_through_infer_on_the_embedded_image_and_accepts_a_structure() -> None:
    fake = FakeSegmenter([rgba_crop(5, 5)], [(1, 1, 6, 6)])
    SegModel(fake).selftest()

    (image,) = fake.calls
    embedded = cv2.imdecode(np.frombuffer(seg_model.selftest_png(), np.uint8), cv2.IMREAD_COLOR)
    assert np.array_equal(image, embedded)


def test_selftest_fails_when_the_model_finds_no_structure() -> None:
    with pytest.raises(RuntimeError, match="no structure"):
        SegModel(FakeSegmenter()).selftest()


def test_selftest_fails_when_only_a_degenerate_box_comes_back() -> None:
    with pytest.raises(RuntimeError, match="no structure"):
        SegModel(FakeSegmenter([rgba_crop(1, 1)], [(0, 0, 0, 0)])).selftest()


def test_selftest_raises_when_the_segmenter_does() -> None:
    with pytest.raises(RuntimeError, match="segmentation failed"):
        SegModel(FakeSegmenter(error=ValueError("weights not loaded"))).selftest()


# --- loading ------------------------------------------------------------------------------


def test_load_with_an_injected_segmenter_does_not_import_tensorflow() -> None:
    done = run_python(
        "import sys\n"
        "from decimer_seg.model import SegModel\n"
        "SegModel(lambda image: ([], [])).load()\n"
        "bad = [m for m in ('tensorflow', 'decimer_segmentation') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    assert done.returncode == 0, done.stderr


@pytest.fixture
def real_loader(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the real library loader with a recorder, so `load()` can run without TensorFlow."""
    calls: list[str] = []

    def fake_loader() -> seg_model.SegmentFn:
        calls.append("imported")
        return lambda image: ([], [])

    monkeypatch.setattr(seg_model, "_real_segment_fn", fake_loader)
    monkeypatch.delenv("SEG_ALLOW_WEIGHT_DOWNLOAD", raising=False)
    return calls


def weights_in(home: Path) -> Path:
    return home / "DECIMER-Segmentation_model" / "mask_rcnn_molecule.h5"


def test_missing_weights_raise_a_clear_error_before_the_library_is_imported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))

    with pytest.raises(RuntimeError) as excinfo:
        SegModel().load()

    assert str(weights_in(tmp_path)) in str(excinfo.value)
    assert "SEG_ALLOW_WEIGHT_DOWNLOAD" in str(excinfo.value)
    assert real_loader == []
    assert not (tmp_path / "DECIMER-Segmentation_model").exists()  # checking must not create it


def test_weights_present_lets_the_library_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    weights_in(tmp_path).parent.mkdir()
    weights_in(tmp_path).write_bytes(b"weights")

    SegModel().load()

    assert real_loader == ["imported"]


def test_weight_download_has_to_be_asked_for_explicitly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, real_loader: list[str]
) -> None:
    monkeypatch.setenv("PYSTOW_HOME", str(tmp_path))
    for not_a_yes in ("0", "true", "yes", ""):
        monkeypatch.setenv("SEG_ALLOW_WEIGHT_DOWNLOAD", not_a_yes)
        with pytest.raises(RuntimeError, match="mask_rcnn_molecule.h5"):
            SegModel().load()
    assert real_loader == []

    monkeypatch.setenv("SEG_ALLOW_WEIGHT_DOWNLOAD", "1")
    SegModel().load()
    assert real_loader == ["imported"]


def test_missing_weights_never_import_tensorflow_or_the_library(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "SEG_ALLOW_WEIGHT_DOWNLOAD"}
    env["PYSTOW_HOME"] = str(tmp_path)
    done = run_python(
        "import sys\n"
        "from decimer_seg.model import SegModel\n"
        "try:\n"
        "    SegModel().load()\n"
        "except RuntimeError as exc:\n"
        "    print(exc)\n"
        "else:\n"
        "    sys.exit('load() did not raise')\n"
        "bad = [m for m in ('tensorflow', 'decimer_segmentation') if m in sys.modules]\n"
        "assert not bad, bad\n",
        env,
    )
    assert done.returncode == 0, done.stderr
    assert str(weights_in(tmp_path)) in done.stdout


def test_whatever_the_library_prints_while_loading_stays_off_stdout(tmp_path: Path) -> None:
    # decimer_segmentation prints the weights directory when it is imported.
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "decimer_segmentation.py").write_text(
        "print('/some/weights/dir')\n"
        "import types\n"
        "def get_model():\n"
        "    print('get_model ran')\n"
        "    return types.SimpleNamespace(keras_model=object())\n"
        "def segment_chemical_structures(image, **kwargs):\n"
        "    return [], []\n"
    )
    weights_in(tmp_path / "home").parent.mkdir(parents=True)
    weights_in(tmp_path / "home").write_bytes(b"x")
    env = {k: v for k, v in os.environ.items() if k != "SEG_ALLOW_WEIGHT_DOWNLOAD"}
    env["PYSTOW_HOME"] = str(tmp_path / "home")
    env["PYTHONPATH"] = str(tmp_path / "lib")

    done = run_python(
        "import sys\n"
        "from decimer_seg.model import SegModel\n"
        "SegModel().load()\n"
        "assert 'decimer_segmentation' in sys.modules\n",
        env,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout == ""


# --- pixel cap ----------------------------------------------------------------------------

CAP_PROBE = (
    "import os, sys\n"
    "import decimer_seg.model as m  # first: importing the package sets the cap\n"
    "assert os.environ['OPENCV_IO_MAX_IMAGE_PIXELS'] == '40000000', os.environ\n"
    "import cv2, numpy as np\n"
    "def png(side):\n"
    "    ok, buf = cv2.imencode('.png', np.zeros((side, side, 3), np.uint8))\n"
    "    return buf.tobytes()\n"
    "model = m.SegModel(lambda image: ([], []))\n"
    "model.infer(png(6300), {})  # 39.7 Mpx: just under the cap\n"
    "try:\n"
    "    model.infer(png(6400), {})  # 41.0 Mpx: just over it\n"
    "except ValueError as exc:\n"
    "    sys.exit(0 if 'could not decode image' in str(exc) else 2)\n"
    "sys.exit(3)\n"
)


def test_default_pixel_cap_is_40_megapixels_and_oversized_images_are_a_value_error() -> None:
    env = {k: v for k, v in os.environ.items() if k != "OPENCV_IO_MAX_IMAGE_PIXELS"}
    done = run_python(CAP_PROBE, env)
    assert done.returncode == 0, (done.returncode, done.stderr)


def test_pixel_cap_from_the_environment_is_respected() -> None:
    env = {**os.environ, "OPENCV_IO_MAX_IMAGE_PIXELS": "1000"}
    code = (
        "import sys\n"
        "import cv2, numpy as np\n"
        "from decimer_seg.model import SegModel\n"
        "ok, png = cv2.imencode('.png', np.zeros((100, 100, 3), np.uint8))\n"
        "try:\n"
        "    SegModel(lambda image: ([], [])).infer(png.tobytes(), {})\n"
        "except ValueError as exc:\n"
        "    sys.exit(0 if 'could not decode image' in str(exc) else 2)\n"
        "sys.exit(3)\n"
    )
    assert run_python(code, env).returncode == 0


def test_overlay_script_sets_the_cap_before_it_imports_cv2() -> None:
    env = {k: v for k, v in os.environ.items() if k != "OPENCV_IO_MAX_IMAGE_PIXELS"}
    done = run_python(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('overlay', 'scripts/overlay.py')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "import cv2, numpy as np\n"
        "ok, buf = cv2.imencode('.png', np.zeros((6400, 6400, 3), np.uint8))\n"
        "try:\n"
        "    cv2.imdecode(np.frombuffer(buf.tobytes(), np.uint8), cv2.IMREAD_COLOR)\n"
        "except cv2.error:\n"
        "    sys.exit(0)\n"
        "sys.exit(3)\n",
        env,
    )
    assert done.returncode == 0, (done.returncode, done.stderr)


# --- helpers used by the slow tests, kept honest here -------------------------------------


def test_iou_helper() -> None:
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [10, 0, 20, 10]) == 0.0
    assert iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(1 / 3)
