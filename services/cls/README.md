# decimer-cls

DECIMER Image Classifier model service. It receives **one image** (normally a crop from `seg`) and says whether it shows a chemical structure. It builds on the shared skeleton in `../common` (`decimer_service.create_app`), so `/healthz`, `/readyz`, the bounded `/infer` queue, and the 422/500/503 behaviour are described in `../common/README.md`. `decimer_cls.app` holds the production wiring (`LIMITS`, `build_app()`), as in `seg`.

The model is `decimer-image-classifier` pinned to git commit `146b00b` (the same commit the earlier Laravel version of decimer.ai installs), an EfficientNetB0, on TensorFlow 2.15.1 and Python 3.11. CPU only. One replica, one inference at a time.

## Run

```bash
cd services/cls
uv sync
uv run python -m decimer_cls            # listens on 0.0.0.0:8003
PORT=9000 uv run python -m decimer_cls  # another port
```

Startup takes about 11 s from process start to `/healthz` answering (4-5 s of that is importing TensorFlow; the model loads inside the lifespan). The first prediction after start traces the graph and takes about 1.9 s. `/readyz` runs the selftest, so a deploy check on `/readyz` pays that cost, not the first user. The service also counts a successful `/infer` as a readiness pass for 30 s (see `../common`), so a `/readyz` that comes after a request does not run the selftest again.

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8003` | Listening port. |
| `TF_NUM_INTRAOP_THREADS`, `TF_NUM_INTEROP_THREADS` | TensorFlow decides (all cores) | TensorFlow reads these itself. The classifier is small; `2` and `1` give about 100 ms per crop on an 8-core machine. |
| `TF_CPP_MIN_LOG_LEVEL` | `2` (set by `decimer_cls` if unset) | Importing the package sets it before TensorFlow is imported. `2` hides INFO and WARNING and keeps ERROR. **Three ERROR lines still appear at startup** (cuDNN, cuFFT and cuBLAS "Unable to register factory", harmless on a CPU-only machine). `3` would hide them, and real C++ errors with them. |

Limits (`decimer_cls.app.LIMITS`): one inference at a time, 16 waiting, uploads up to 20 MiB (413 above that, 503 with `Retry-After: 5` when the queue is full).

## `POST /infer`

Multipart form: `image` (file), PNG, JPEG, WebP or BMP. Nothing else is read; other form fields are ignored (in particular, a caller cannot move the threshold). GIF and TIFF are not accepted: crops are PNG and the API vets uploads (PNG, JPEG, WebP; HEIC is converted before it gets here), so nothing needs those decoders. BMP is not an upload format either and can be dropped the same way if nothing ends up needing it.

Response `200`, exactly these two keys:

```json
{"is_structure": true, "score": 3.555789777204277e-12}
```

- `score` is the raw network output, `sigmoid(logit)`, as a plain float. **Lower means more structure-like**: it is the probability of "not a structure". Typical values on the golden paper are 5e-13 to 4e-8 for depictions and 0.01 to 0.03 for the two non-structures.
- `is_structure` is `score <= 0.000089`. That is the rule the earlier version uses (`DecimerImageClassifier.is_chemical_structure(img=path)`, whose default `threshold` is `0.000089`). The boundary is inclusive, as in the library. A slow test checks that the installed library still has this default.

Errors:

- `422`:
  - bytes that do not decode as one of the formats above (including truncated files);
  - `image too large`: more than 50 million pixels, decided from the header before any pixel is decoded;
  - `unsupported pixel mode: X`: a mode the library cannot convert (see below);
  - `image too elongated`: a shape where the library would resize one side to 0 px (for example 1 x 225; the check is `int(short_side * 224 / long_side) == 0`).
- `500` with an `error_id`: the model itself failed. A `ValueError` raised inside the library is reported as 500, not 422, because it is not the caller's mistake. The checks above exist so that nothing a caller can send reaches the library in a state where it raises.
- `413` too large, `503` busy.

The earlier version answers `False` for *any* failure, including a dead backend. This service does not: a caller sees 422/500/503 and decides. Whether the worker then treats a failed classification as "not a structure" is a pipeline decision, not this service's.

### Pixel modes

The library converts every non-RGBA image with `convert("L")`, and Pillow raises `ValueError` for some modes (`LAB`: "conversion from LAB to RGB not supported"; `La`). A LAB TIFF reproduced it: a 500 for something the caller sent. Pixel modes are now checked against `SUPPORTED_MODES` (`1, L, P, RGB, RGBA, LA, PA, CMYK, YCbCr, I, I;16, F`) after decoding and before the model lock, and anything else is `unsupported pixel mode`. With TIFF and GIF gone, the four formats we accept can only produce `1, L, LA, P, RGB, RGBA, I;16, CMYK` (checked against Pillow 13), all handled, so today this is a guard for a future decoder or allow-list change. A test runs every (format, mode) pair through a stand-in that does the library's first conversion, and one runs LAB through the real library.

### Preprocessing

The library does all of it (alpha onto white, grayscale, resize to 224 x 224 with Lanczos and white padding, EfficientNet `preprocess_input`); this service decodes with Pillow, as the earlier version does, and changes exactly one thing before handing the image over:

- **Intentional deviation from the earlier version: anything with transparency (RGBA, LA, palette or tRNS PNG) is composited onto white here**, with `paste` using the alpha channel as mask. For RGBA the pixels are identical to what the earlier version fed the network (a test compares them with the library's own recipe, and all 20 golden scores are bit-identical to the library's). The library does that itself for RGBA, but it also does `img.save("caffeine_mod.png")` into the current working directory every time, which is a file write per request and not thread-safe. LA, palette-with-transparency and tRNS images are where this really differs: the earlier version drops their alpha channel in `convert("L")`, so transparent pixels show whatever colour is stored under them (often black); here they are white. Other modes (RGB, L, CMYK, ...) are passed through untouched so the library's own grayscale conversion applies, as in the earlier version.
- EXIF orientation is **not** applied, because the earlier version does not either (a test with an orientation-6 JPEG pins it). If that ever changes, it changes scores for rotated photos, so it needs the golden-set check.

## Selftest and `/readyz`

`selftest()` classifies two generated 128 x 128 images and checks the **answers**, not just that the model runs: a drawn hexagon must score `<= 0.000089` and a blank white page must score above it. On the real model the hexagon scores about 1.9e-8 and the blank page 1.0, so there is a margin of three to four orders of magnitude each way. A model that says "yes" or "no" to everything (wrong weights, broken preprocessing) therefore fails `/readyz` with a 503 instead of staying ready.

## Memory

Measured with TensorFlow limited to 2 + 1 threads.

**Resident:** about 0.98 GB after load, 1.03 GB after 30 requests, 14 threads.

**One request, on top of that.** Decoding, flattening and the library's preprocessing, run on images of the cap size (50 Mpx), with the library's own functions and no model:

| Input | Peak above resident | At 17.4 Mpx (A3 at 300 DPI) |
|---|---|---|
| RGB, RGBA or CMYK (PNG, JPEG) | 430 MB | 151 MB |
| L (grayscale PNG) | 287 MB | 101 MB |

That is about 8.6 MB per megapixel (5.7 for grayscale), and it is flat across modes since `_flatten` no longer copies an RGBA image (it was 621 MB before). Through the running service the first 50 Mpx RGBA request peaked at +429 MB (VmHWM) in 1.25 s and **left RSS 164 MB higher** (the allocator keeps the pages); later large requests reuse them (RGB +269 MB above that, 0.97 s; grayscale 0.70 s).

**Why the cap is 50 Mpx** (`MAX_PIXELS`): it is the most the API accepts per image, and the single-image fallback sends the whole upload to `cls`. A lower cap would answer 422 for a valid 48 MP phone photo and the user would see "No structures were detected". A seg crop is at most one page and in practice a few hundred pixels on a side. If a lower cap is wanted (17.4 Mpx would cut the worst case to 151 MB), the worker has to downscale large single images before calling `cls`, which is safe because the classifier looks at a 224 x 224 thumbnail; that is a decision for the worker. The cap is checked from the header (a PNG that declares 7072 x 7072 and stops is refused as "too large", not "could not decode"), so a small file cannot make the service allocate.

**Sizing the container limit.** The worst case for one process is the resident set after a long run (1.09 GB after 600 requests, below), plus 0.43 GB for one 50 Mpx request (only one runs at a time), plus the request bodies. The skeleton reads each body into memory (up to 20 MiB) before it decides whether to queue or refuse, so every connection in flight holds up to 20 MiB: 17 slots (1 running + 16 waiting) is 0.34 GB, and requests that are then refused with 503 held theirs briefly too. That adds up to about 1.8 GB on a fresh process and 1.9 GB after 600 requests, so a limit of 2 GB is too tight; **2.5 GB is a reasonable start**, to be confirmed by the soak. Put a request-rate or connection limit in front if the service is ever reachable by more than the worker.

**RSS grows with every request.** Two 600-request runs measured about 57 KB and about 100 KB per request. In the second (sequential, the 20 golden crops, after 30 warm-up requests): RSS 1,032,588 kB, then 1,046,628 / 1,051,468 / 1,061,864 / 1,071,240 / 1,082,704 / 1,094,432 kB at every 100 requests, roughly linear. It was not investigated. Calling Keras `predict()` once per request (which is what the library does) is the usual suspect for this, but that is a hypothesis, not a finding. A soak of a few thousand requests that watches RSS would either find the cause or set the container limit and restart policy with the growth in mind.

## Weights

The weights ship **inside the Python package**; nothing is downloaded at install or at runtime. `decimer_image_classifier/model/` is a Keras SavedModel (about 26 MB):

| File | SHA-256 |
|---|---|
| `keras_metadata.pb` | `4c6f6149a612a52736ecc808434645242f746c57f7b38bb9eea2851112ee3cc6` |
| `saved_model.pb` | `1f1f192e3a63c8985f20d8a0777a558458ef829379e5373fdad25bc0b4161978` |
| `variables/variables.data-00000-of-00001` | `d4153f669cb35242739d0b901f5b9083d31acd5fcdb8d2c46d2a3589f9d1ed5a` |
| `variables/variables.index` | `bc74ce41019f517f4f766868d6b037566dbdf9da307af2414eb28ec7263680d6` |

These are byte-identical to the files the earlier version ships (it installs the same commit), so the model is the same one; only TensorFlow differs (2.7.0 there, 2.15.1 here). The hashes can be checked at image build time.

## Dependencies

`decimer-image-classifier` 1.0.1 declares `tensorflow==2.12.0` and `ipyplot`. The earlier version's image installs it with `pip --no-deps`; here `[tool.uv] override-dependencies` swaps in `tensorflow==2.15.1` and drops `ipyplot` (a notebook plotting helper the library never imports) with a marker that never matches. Pins: tensorflow 2.15.1 (Keras 2.15), numpy 1.26.3, protobuf 4.25.1, Pillow from the lock file. Pillow's `Image.LANCZOS` (which the library uses) still exists in the locked version.

## Tests

```bash
uv run pytest               # fast tests, no TensorFlow import (slow tests are deselected)
uv run pytest -m slow       # the real model: agreement with the earlier version on 20 crops (about 15 s on CPU)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The fast tests use an injected scoring function (a stand-in that returns a numpy float32 like the library does, and does the library's first grayscale conversion so unusable images fail in it too) and cover the response shape and types, the threshold rule including the boundary, the selftest's two answers, decoding failures, the format allow-list, the pixel cap from the header, pixel modes, transparency (including a pixel comparison with the library's own recipe), EXIF orientation, the lock, the production limits and the 20 MiB boundary, and the HTTP app through `httpx.ASGITransport` with the lifespan. Every behaviour above was checked by breaking the code and watching a test fail.

The slow tests run the whole service path (lifespan load, production limits, `/infer`) on the crops of the committed run of the earlier version, `golden/runs/legacy-20261022-140954`: rows 0 to 19 have its `classifier` flag (rows 20 to 31 have none, because it caps a job at 20 structures). **The check is that the flag agrees on all 20** (18 structures, 2 non-structures; the test fails if either answer is missing from the fixture). They also call `ClsModel.selftest()` directly on the real model, check that a request prints nothing from Keras (including on a brand-new thread), writes nothing into the working directory, that a LAB image is a 422 on the real library, and that the threshold still matches the library.

To see the numbers rather than pass/fail (flags, scores, how far each is from the threshold, load and per-crop time, memory), and to compare against the library called exactly as the earlier version calls it:

```bash
TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 uv run python scripts/compare_legacy.py
```

## Things to know about `decimer_image_classifier`

- `DecimerImageClassifier()` imports TensorFlow and loads the model, so `ClsModel` builds it only inside `load()`; importing `decimer_cls.model` is cheap and the fast tests never touch TensorFlow.
- Its constructor sets `CUDA_VISIBLE_DEVICES=0` and enables GPU memory growth. On this CPU-only service both are no-ops.
- `model.predict` draws a progress bar on stdout. The service turns Keras interactive logging off (it is a **thread-local** flag, so it is set on every call, not once at load) and sets the `absl` logger to WARNING, so the JSON log has only the service's own `inferred` line per request.
- `/readyz` is not behind the `/infer` gate, so a selftest can overlap a request. A lock inside `ClsModel` makes the two take turns instead of calling one Keras model from two threads.
- Pillow is given an allow-list of formats. Pillow can open many more, and EPS shells out to Ghostscript on load.
- Pillow's `convert("L")` clips 16-bit grayscale values above 255 to white (`I;16` is accepted). That is the library's own conversion and the service does not rescale, so a 16-bit grayscale PNG comes out mostly white. Not compared with the earlier version (its Pillow is older); no known caller sends them.
