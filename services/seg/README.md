# decimer-seg

DECIMER Segmentation model service. It receives **one page image** and returns the chemical structures found on it, as boxes and PNG crops, in reading order. It builds on the shared skeleton in `../common` (`decimer_service.create_app`), so `/healthz`, `/readyz`, the bounded `/infer` queue, and the 422/500/503 behaviour are described in `../common/README.md`.

The model is `decimer-segmentation` 1.6.0 from PyPI (pinned in `pyproject.toml`, hashes in `uv.lock`; a wheel, so nothing is built from source at install time), on TensorFlow 2.15.1 and Python 3.11. CPU only.

## Run

```bash
cd services/seg
uv sync
uv run python -m decimer_seg            # listens on 0.0.0.0:8001
PORT=9000 uv run python -m decimer_seg  # another port
```

Startup takes about 9 s (the model loads inside the lifespan, so `/healthz` answers only after it is loaded). If the weights are not on disk, startup fails at once with an error that names the expected path (see `PYSTOW_HOME` below).

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8001` | Listening port. |
| `PYSTOW_HOME` | `~/.data` | Where the weights live: `$PYSTOW_HOME/DECIMER-Segmentation_model/mask_rcnn_molecule.h5` (272 MB). `decimer_segmentation.get_model()` would download them from Zenodo when the file is missing; `load()` checks for the file first and refuses to start without it, so a missing file is a clear startup error, not a silent 272 MB download. |
| `SEG_ALLOW_WEIGHT_DOWNLOAD` | unset | Set to exactly `1` to let `decimer_segmentation` download missing weights (development only; a service without network access cannot). |
| `TF_NUM_INTRAOP_THREADS`, `TF_NUM_INTEROP_THREADS` | TensorFlow decides (all cores) | TensorFlow reads these itself. With `2` and `1`, the process drops from 60 to 47 threads and a page takes about 6 s instead of 3.6 s on an 8-core machine. |
| `OPENCV_IO_MAX_IMAGE_PIXELS` | `40000000` (set when `decimer_seg` is imported, if unset) | A decoded image above this many pixels is refused with 422. A3 at 300 DPI is about 17 Mpx; the library keeps one full-page boolean mask per detection, so a 150 Mpx page with ten structures would need 5-6 GB. OpenCV reads the variable once, when `cv2` is first imported, so anything that imports `cv2` in this project must import `decimer_seg` first. |

The production limits are `decimer_seg.app.LIMITS`, and `build_app()` is the app that `__main__` serves: one inference at a time, 8 waiting, uploads up to 40 MiB (413 above that, 503 with `Retry-After: 5` when the queue is full).

`/readyz` runs a real selftest: the whole `infer()` path on a small PNG of one real structure that ships in the package (`src/decimer_seg/selftest.png`, 4 KB: daidzein cropped from the golden paper, centred on a white 512x512 page), and it fails unless at least one segment comes back. A successful `/infer` counts as a pass too (see `../common`), so under load the selftest does not run at all. `/readyz` is not behind the `/infer` limit (a probe must not queue behind a slow page), so `SegModel.selftest` itself keeps away from a page in progress: it does not call the library beside it (see "not thread-safe" below) and does not wait for it, it answers ready, unless that page has run for more than 300 s (`HANG_AFTER_SECONDS`), which fails the probe.

## `POST /infer`

Multipart form:

- `image` (file): the page as PNG, JPEG or anything OpenCV decodes. The service does not rely on the caller normalising it: a transparent background is composited onto white, grayscale becomes 3 channels, 16-bit becomes 8-bit, and a float image (for example a float TIFF) is refused with 422. **EXIF orientation is not applied** (decoding keeps alpha and bit depth, and OpenCV then ignores the tag), so `width`, `height` and the boxes are in the pixel grid exactly as stored; send upright pixels.
- `page_index` (text, optional, default `0`): 1 to 5 ASCII digits (`[0-9]{1,5}`, so no sign, spaces, underscores or non-ASCII digits), echoed back. The service does not use it for anything else.

Response `200`:

```json
{
  "page_index": 2,
  "width": 2480,
  "height": 3509,
  "segments": [
    {"index": 0, "bbox": [356, 1834, 734, 2175], "png": "<base64 PNG>"}
  ]
}
```

- `width` and `height` are the decoded page size in pixels.
- `segments` is in the library's reading order: boxes are sorted by top edge, and a box joins the current row when its top is within 50 px of the **previous box in that row** (rows chain, so a row can span more than 50 px); each row is then sorted left to right. `index` counts from 0 in that order. A page without structures gives `"segments": []`.
- `bbox` is `[x0, y0, x1, y1]` in page pixels, as plain ints, with `x1`/`y1` exclusive. (`decimer_segmentation` itself returns `(y0, x0, y1, x1)`; this service converts.)
- `png` is the base64-encoded PNG of the crop, 4 channels (RGBA) and exactly `(y1 - y0)` x `(x1 - x0)` pixels. The library paints everything outside the mask opaque white, so alpha is 255 everywhere in practice.
- Boxes without area (`x1 <= x0` or `y1 <= y0`; the library's answer for an empty mask is a 1x1 crop with box `(0, 0, 0, 0)`) are dropped, and the remaining segments are numbered without gaps.

Errors: `422` for an undecodable or oversized image, an unsupported pixel format, or a bad `page_index`; `413` too large; `503` busy; `500` with an `error_id` when the model itself fails (a `ValueError` raised inside the library is reported as 500, not 422, because it is not the caller's mistake).

## Tests

```bash
uv run pytest               # fast tests, no TensorFlow import (slow tests are deselected)
uv run pytest -m slow       # the real model on 5 pages of the golden paper (about 35 s on 8 cores; about 46 s with TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1), plus the concurrency test below (about 65 s more)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The slow tests start `build_app()` with the production limits (lifespan load, `/readyz`, `/infer`), render pages of `golden/zou2024_penicillium_shentong.pdf` at 300 DPI with PyMuPDF (a dev dependency; see "PDFs and PyMuPDF" below), and compare the boxes with the fixtures in `tests/fixtures/` (same count, IoU >= 0.9 per box, same order). If the weights are missing they fail with the expected path instead of downloading them. Each fixture records in its `checked` field how a human checked it:

| Fixture | Page | Content | Boxes |
|---|---|---|---|
| `page3_bboxes.json` | 3 | Figure 2, the 12 numbered structures | 12 |
| `page4_bboxes.json` | 4 | Figure 3: 3 annotated structures and a 3D conformer; table, ECD plot and legend are not boxed | 4 |
| `page7_bboxes.json` | 7 | Figure 5B biosynthesis scheme: 15 structures and one R-group fragment (its `checked` text lists three loose boxes) | 16 |
| `page2_bboxes.json` | 2 | text plus an HPLC chromatogram, no structure | 0 |
| `page10_bboxes.json` | 10 | text only | 0 |

`tests/test_process.py` starts `python -m decimer_seg` as its own process (real socket, production limits, cold readiness cache) and fires 8 pages and 3 `/readyz` probes at it at once. It fails if the process dies (the failure message carries its exit code and a faulthandler dump) or if any answer differs from the fixtures. Without the lock it aborted every time. The fast tests that guard the same thing without TensorFlow are in `tests/test_model.py` ("one call into the library at a time": two calls, a probe during a page, two probes, a hung page) and `test_a_probe_during_a_request_never_runs_a_second_inference` in `tests/test_app.py` (production wiring).

Fast tests check that the fixtures are well formed. To redo the hand check after a model or DPI change:

```bash
uv run python scripts/overlay.py ../../golden/zou2024_penicillium_shentong.pdf 3 --out /tmp/page3.png
```

It prints the output path, load and inference times, and memory use, and draws every returned box with its index on the page. Add `--write-bboxes path.json` to save new boxes, and only replace the fixture after looking at the overlay.

## Things to know about `decimer_segmentation`

- **Importing it loads TensorFlow, not the model. `get_model()` loads the model** (since 1.6.0; before it the import built the Mask R-CNN, read the weights and warmed it up). `get_model()` does that once, thread-safely, keeps the model for later calls, and downloads the weights first if they are missing; `segment_chemical_structures` calls it on the first page. `SegModel.load()` runs `_require_weights()` first (so the service never downloads), then imports the library and calls `get_model()` itself, inside the lifespan, so the first request does not wait for the load and `/healthz` answers only once the model is ready. `SegModel` imports the library only inside `load()`, so importing `decimer_seg.model` is cheap and the fast tests never touch TensorFlow.
- Before 1.6.0 it `print`ed the weights directory at import; 1.6.0 prints nothing when the weights are there. `load()` still wraps the import and `get_model()` in `contextlib.redirect_stdout` and logs whatever it prints instead. It also calls `logging.basicConfig`; the JSON log configuration is set up before the import, which makes that a no-op.
- It **overrides `TF_ENABLE_ONEDNN_OPTS` (to `1`) and `TF_CPP_MIN_LOG_LEVEL` (to `2`) unconditionally** at import, so values set by the operator for those two are ignored. `TF_NUM_INTRAOP_THREADS` and `TF_NUM_INTEROP_THREADS` are not touched.
- The mask-expansion step starts its own pool of up to 4 threads (only when a page has more than 3 detections). One call with that pool is safe: pages with 12 and 16 detections ran many times in a row without a crash.
- **It is not thread-safe.** Two `segment_chemical_structures` calls at the same time, even from just two threads, abort the process (`free(): invalid pointer`, `corrupted double-linked list`, `double free or corruption`, or SIGSEGV; the captured dump has both threads inside the batch-norm layers of `detect()`): 2 of 2 runs with 2 threads and 2 of 2 with 3 threads died, 1 thread never did. `SegModel` therefore holds a lock around the library call, so two calls cannot overlap whoever makes them. The skeleton alone does not guarantee it: it admits one `/infer` at a time, but `/readyz` runs its selftest outside that limit, from another thread. Docker's health check (every 120 s, every 5 s during start-up; the selftest runs when no success is younger than 30 s) once ran a selftest beside the worker's `/infer` and killed both replicas 14 times (the worker's retry hid it). The ocsr and cls models guard themselves with a lock for the same reason. Throughput comes from running several replicas, never from threads inside one process.
- **It leaked memory on every call before 1.6.0, which fixes it, and this service no longer patches it.** `MaskRCNN.detect` ran its forward pass as a direct eager call, `keras_model(tensors, training=False)`, which leaks about 3.5 MB per call under TF 2.15 on CPU: 200 calls on 3 images grew the process from 1.79 to 2.49 GB, linearly, and the 3 GiB replicas were OOM-killed after about 14 h of health-check selftests alone (a throwaway replica under load after 385 requests). 1.6.0 runs it as `predict_on_batch` on numpy arrays: flat (1.77 to 1.73 GB over 200 calls), about 30 % faster, and the same detections (same counts, class ids and boxes, scores within 1.2e-7, at most 1 differing mask pixel per page). Before 1.6.0 `load()` put a wrapper on the model that made that one call (`_PredictOnBatch`); it is gone. `MALLOC_ARENA_MAX=2`, oneDNN off and thread reuse do not fix the leak, so a release that goes back to the direct call has no setting to fall back on: the slow test `test_the_library_runs_its_forward_pass_as_predict_on_batch` fails then.
- **PDFs and PyMuPDF.** The service takes page images; the API renders PDFs (pypdfium2). 1.6.0 reads PDFs with pypdfium2 by default, which arrives as a dependency of the library (Apache-2.0 / BSD-3), and with PyMuPDF only through its `pdf` extra. PyMuPDF is AGPL-3.0, so this project never installs that extra: it is in the `dev` group only (the tests and `scripts/overlay.py` render golden-paper pages with it), `uv export --locked --no-dev` has no `pymupdf`, `deploy/patches/apply-pyproject-patches.sh` checks that, and `deploy/seg.Dockerfile` fails the build if `pymupdf` is importable in the image.
