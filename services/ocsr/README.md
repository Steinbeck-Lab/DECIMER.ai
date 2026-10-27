# decimer-ocsr

DECIMER Image Transformer (OCSR) model service. It receives **one image of a chemical structure** (normally a crop from `seg`) and returns its SMILES with a confidence for every token. It builds on the shared skeleton in `../common` (`decimer_service.create_app`), so `/healthz`, `/readyz`, the bounded `/infer` queue, and the 422/500/503 behaviour are described in `../common/README.md` and `docs/ARCHITECTURE.md`. `decimer_ocsr.app` holds the production wiring (`LIMITS`, `build_app()`), as in `seg` and `cls`.

The model is `decimer==2.8.1` from PyPI, on TensorFlow 2.15.1 and Python 3.11 (a later task, P6-03, moves this service to Python 3.12 and TF 2.20; decimer 2.8.1 allows TF up to 2.20). CPU only. One replica, one inference at a time. It calls `DECIMER.predict_SMILES(array, confidence=True, hand_drawn=False)`; the hand-drawn model is out of scope and nothing in a request can select it. Since decimer 2.8.1 (TODO, upstream PR #134) it is not loaded either: `import DECIMER` loads nothing, the service has the library load the printed-structure model at start-up (`DECIMER.load_models(printed=True, hand_drawn=False)`), and the hand-drawn model would only be loaded by a `hand_drawn=True` call.

## Run

```bash
cd services/ocsr
uv sync
uv run python -m decimer_ocsr            # listens on 0.0.0.0:8002
PORT=9000 uv run python -m decimer_ocsr  # another port
```

The weights have to be on disk first (see "Weights"); the first time, `OCSR_ALLOW_WEIGHT_DOWNLOAD=1 uv run python -m decimer_ocsr` lets `DECIMER` fetch the printed-structure model (about 300 MB, one zip of 285 MiB) from Zenodo into `~/.data/DECIMER-V2/`.

**Startup takes about 25 s** from process start to `/healthz` answering (`OcsrModel.load()` measured at 22.9 s on 2026-10-25 with 2 + 1 TensorFlow threads, `nice`, on the busy dev box: 4-5 s importing TensorFlow and `DECIMER`, the rest the tokenizer and the printed-structure model; with decimer 2.8.0 the same step took 46.9 s and the whole service 50.5 s, because `import DECIMER` also loaded the hand-drawn model, which took 26 s in the first measurement). The model loads inside the lifespan (`load()` has the library load it there and then, not at the first request), so `/healthz` only answers once it is loaded. The first prediction after start traces the graph and takes 5 to 8 s longer than later ones; `/readyz` runs the selftest (the same prediction), so a deploy check on `/readyz` pays that cost and not the first user (measured: 5.9 s). Unlike `seg` and `cls`, this service turns the skeleton's readiness cache **off** (`LIMITS.ready_cache_seconds = 0`): the skeleton refreshes that cache on every 200 `/infer`, so with a cache `/readyz` would not reach the model's own readiness policy while requests succeed (see "Selftest and `/readyz`").

| Variable | Default | Effect |
|---|---|---|
| `PORT` | `8002` | Listening port. |
| `PYSTOW_HOME` | `~/.data` | Where `DECIMER` looks for its weights: `$PYSTOW_HOME/DECIMER-V2/`. |
| `OCSR_ALLOW_WEIGHT_DOWNLOAD` | unset | Only the value `1` lets `DECIMER` download missing weights. Without it a missing or stale weight file is a `RuntimeError` naming the path, before `DECIMER` is imported. |
| `TF_NUM_INTRAOP_THREADS`, `TF_NUM_INTEROP_THREADS` | TensorFlow decides (all cores) | TensorFlow reads these itself. All the numbers below were measured with `2` and `1`. |
| `TF_CPP_MIN_LOG_LEVEL` | `2` (set by `decimer_ocsr` if unset) | Importing the package sets it before TensorFlow is imported. **Three ERROR lines still appear at startup** (cuDNN, cuFFT and cuBLAS "Unable to register factory", harmless on a CPU-only machine). |

Limits (`decimer_ocsr.app.LIMITS`): one inference at a time, 4 waiting, uploads up to 20 MiB (413 above that, 503 with `Retry-After: 5` when the queue is full).

## `POST /infer`

Multipart form: `image` (file), PNG, JPEG, WebP or BMP. Nothing else is read; other form fields are ignored (a test posts `hand_drawn=true` and checks it changes nothing). GIF and TIFF are not accepted, for the same reason as in `cls`: crops are PNG and the API vets uploads (PNG, JPEG, WebP; HEIC is converted before it gets here).

Response `200`, exactly these four keys (real output for the daidzein crop, shortened; `incomplete` is new with D25, below):

```json
{
  "smiles": "C1=CC(=CC=C1C2=COC3=CC(=CC=C3C2=O)O)O",
  "token_confidence": [["C", 0.9919716119766235], ["1", 0.9968532919883728], ["=", 0.9979627132415771], "..."],
  "mean_confidence": 0.9940,
  "incomplete": false
}
```

- `smiles` is DECIMER's output, unchanged (see "Not done here" below).
- `token_confidence` has one `[token, confidence]` pair per token, in order. The tokens are the model's own: single characters (`C`, `(`, `=`, `[`, `@`, `H`, `]`, digits) and element symbols (`Cl`, `Br`, `Si`...), 156 vocabulary entries in all and **they concatenate to exactly `smiles`**, so a client can colour the SMILES by position. A token's confidence is the decoder's softmax probability for the token it chose, so it is in [0, 1]; a slow test checks that for every token of every golden crop. The values are float32 numbers sent as they are (not rounded).
- `mean_confidence` is the **arithmetic mean of the token confidences** (`math.fsum(c) / n`). Alternatives (minimum, geometric mean, the probability of the whole sequence) are all derivable from `token_confidence`; the mean was chosen because it does not collapse to 0 for a long structure with one doubtful token. On the golden crops, clean structures score 0.89 to 0.998 and the garbage readings of the figures with correlation arrows 0.77 to 0.82, but a wrong structure can score high too (crop 19 is read with an extra ring methyl at 0.966), so it is a hint, not a verdict.
- `incomplete` is a boolean, **`true` only when decoding stopped at the model's token limit (302) without an end token** (D25, owner decision 2026-10-27; this used to be a 422, see "The token limit"). `smiles` is then the cut-off text, exactly what the library returned and what legacy kept, so the caller must not take it for a reading. It is `false` for every other answer, an empty one included. The library leaves the confidence of the last token out in that case; the service puts that token back as `["<token>", 0.0]` ("not reported") so that the tokens still concatenate to `smiles`, and `mean_confidence` is the mean of the pairs it returns (the 0.0 is in it: 1/302 of the weight at most).
- **An empty prediction** (the model answers "end" immediately) is `{"smiles": "", "token_confidence": [], "mean_confidence": 0.0, "incomplete": false}`: `0.0` and not `null` or NaN, so the field stays a float that sorts and serialises. Nothing in the golden set or the degenerate-image tests produced one (a blank page is read as a 302-token string and returned flagged `incomplete`, below).

Errors:

- `422`:
  - bytes that do not decode as one of the formats above (including truncated files);
  - `image too large`: more than 17,500,000 pixels, or Pillow's own decompression-bomb limit; decided from the header, before any pixel is decoded;
  - `image too long`: a longest side above 8192 pixels (see "Memory" for why a side limit is needed besides the pixel limit);
  - `unsupported pixel mode: X`: a mode that does not convert to RGBA (see below);
  - **`no complete structure: ...`**: the model read no usable structure from the image. **The detail always starts with `no complete structure`; that prefix is stable** (`decimer_ocsr.model.NO_STRUCTURE`, pinned by a test), so the worker and the UI can tell "not a structure" from the other 422s (undecodable, too large, ...) by it and show their own wording. **One cause since D25** (a decode that ends at the token limit is a 200, `incomplete: true`, next section; the selftest still refuses such an answer):
    - `no complete structure: the model wrote a special token (<unk>)` (or `<pad>`, `<start>`, `<end>`): a special token in the output, the one the library leaves out of the confidences when it stops at the token limit included, in either of the spellings the library produces (`<unk>` or its decoder form `4unk5`; `DECIMER.utils.decoder` turns `<` into `4` and `>` into `5`). `<unk>` is something outside the model's vocabulary. Only a whole token counts: `4` and `5` on their own are ordinary tokens.
- `500` with an `error_id`: the model itself failed. A `ValueError` raised inside the library is reported as 500, not 422, because it is not the caller's mistake.
- `413` too large, `503` busy.

### The token limit

The model decodes at most 302 tokens (`assets/max_length.pkl`) and the limit is inside the exported graph. **Any input that is not a structure never writes the "end" token and runs all 302 steps**: a blank page, a black square, a 1 x 1 image, a strip, a page of text and a whole rendered page all did (measured), as did the golden crop of a figure with correlation arrows (crop 15). A decode that stops at the limit is cut off in the middle. **Since D25 (owner decision 2026-10-27, legacy parity) it is answered `200` with the cut-off SMILES and `incomplete: true`** (it was a 422 `no complete structure: too long or not a structure` until then). Legacy kept that string, let CDK 2.8 judge its validity and write the molfile (CDK's parser is more tolerant than RDKit's), and used RDKit only for the InChIKey; the API does the same with its RDKit-then-CDK check and carries the flag as the result's `incomplete`, so that the UI can offer an editable start in Ketcher and say that the SMILES is cut off. A structure of more than about 300 tokens ends here too; the model cannot tell that from garbage. (decimer 2.8.1 also drops the last token's confidence in that case, so the confidences would not line up with the SMILES; the service detects it by exactly that: `smiles` starts with the concatenated tokens but is longer, and the missing last token is `smiles` after them. That token is checked for the special spellings too: a special token there is still the 422. **Only exactly that case is flagged:** 301 confidences (`TOKEN_LIMIT - 1`) and a leftover that is one vocabulary token (one character, or one of the 95 two-letter element symbols in `MULTI_CHAR_TOKENS`, which a slow test compares with the library's tokenizer; `Cc` is two tokens, not one). Any other prefix, and any other mismatch, is a 500, because it would mean the library changed.) A log line (`decode stopped at the token limit: N tokens, returned flagged incomplete`) says it happened and never the SMILES. **The selftest stays strict:** an incomplete answer for the embedded structure fails it (`selftest failed: the embedded structure was refused: no complete structure: too long or not a structure`).

**This costs about 38 to 55 s of CPU per request** (2 + 1 threads), against 2 to 5 s for a real structure, and `max_concurrency` is 1. The worker's per-request timeout is 120 s, which covers one such decode with room to spare; the queue allows 4 waiting (`max_waiting`), so a queue of structures (2 to 5 s each) waits about 25 s at worst, but the last of five non-structures would wait about 4 x 55 s = 220 s, longer than that timeout. The worker should not send images that `cls` has already called non-structures to `ocsr` unless the single-image fallback needs it, and should treat a timeout or a 503 as "retry later", not as a result. Decoding time grows faster than linearly with the token count (each step re-runs the decoder over the whole prefix): 45 tokens about 3 s, 101 about 10 s, 197 about 24 s, 285 about 40 s.

### Pixel modes and preprocessing

`DECIMER` reads `Image.open(path).convert("RGBA")` for a path. Given a numpy array it does `Image.fromarray(array).convert("RGBA")`, so the service decodes the bytes itself (Pillow, format allow-list, the pixel and side limits from the header, `image.load()` inside a try) and gives it exactly that RGBA array: **no temporary file is written**, so there is nothing to clean up on an error. A slow test checks that the array route and the library's own file route give the same SMILES and the same confidence for every token on three crops, and that the same crop saved as RGBA, L, P and 1-bit reads the same.

- Transparency is **not** flattened here: the RGBA array keeps its alpha and the library composites it onto white (`remove_transparent`). The golden crops from `seg` are RGBA with alpha 255 everywhere.
- Supported pixel modes: `1, L, LA, P, PA, RGB, RGBA, CMYK, YCbCr, I;16`. A 16-bit grayscale PNG (`I;16`) is scaled by its high byte; Pillow's own conversion would clip everything above 255 and make it white. Any other mode (LAB, F, 32-bit I) is a 422 instead of a 500 from Pillow's "conversion not supported". With GIF and TIFF gone, the four formats cannot produce those modes today; this is a guard for a future allow-list change.
- EXIF orientation is **not** applied, as in legacy; the API applies it at upload.
- The rest is the library's (alpha onto white, contrast stretch, grayscale, 512 px resize, border crop, square white padding, brightness, PNG round trip, 512 x 512 Gaussian resize, EfficientNet preprocessing). It is the same code as in the legacy site's DECIMER commit (diffed against the container's `config.py`: the functions are identical apart from array support and an `int()` cast against uint8 overflow).

### Not done here

Legacy's `decimer_predictor_server.py` post-processes the SMILES: with more than four dot-separated fragments, or a first fragment shorter than all the others together, it keeps only the first fragment. This service returns the model's output as it is. Three of the 32 golden crops (27, 28 and 29: labels S9 to S11, unchecked, all read wrong) contain `.` fragments, and five contain R-group atoms (`[R]`, `[R1]`, `[R2]`, from the fragments of the biosynthesis scheme). Whether to filter fragments, and how to show "invalid / R-group" results, is a pipeline decision.

## Selftest and `/readyz`

`selftest()` reads the embedded `selftest.png` (6.8 KB, the golden paper's daidzein crop, 434 x 198 RGBA, pixel for pixel the crop of label 11 in the legacy golden run) through the whole path (decode, array, DECIMER, token check) and requires the **exact SMILES** `C1=CC(=CC=C1C2=COC3=CC(=CC=C3C2=O)O)O`. A model that runs but reads something else (wrong weights, a broken tokenizer, a changed preprocessing step) fails `/readyz` with a 503 and both strings in the message.

**`/readyz` never waits long for the model, and never takes "busy" or "a request completed" for "correct".** A decode that ends at the token limit holds the model for 38 to 55 s (and answers `incomplete: true` since D25), so a selftest that queued behind it would time out and the container would turn unhealthy. The skeleton's readiness cache is off for this service (`app.LIMITS.ready_cache_seconds = 0`), so **every `/readyz` reaches `OcsrModel.selftest()`**, which keeps the policy itself. (The skeleton refreshes its cache on every 200 `/infer`; with a cache, a model that answers wrongly but completely, as wrong weights do, was 503 on the first probe and then "ready" for as long as requests kept succeeding: reproduced with a wrong model, one 200 `/infer` and `/readyz`.) `selftest()` decides in this order (all times are constructor arguments, pinned by a test; the clock is injectable; the 30 s window is the model's own constant, `READY_WINDOW`, not read from `LIMITS`):

1. **The model is busy** (a decode holds the lock): the model is alive, **unless that decode has been running for more than 90 s** (`hang_after`, above the 55 s worst case at the token limit). Then the probe fails with `decode running for N s`, so a hung decode cannot keep the container "ready" for ever. If the last real pass is older than 5 minutes (item 4) the selftest does not return "alive" but waits for the model, below.
2. **A selftest passed, or a request completed, within the last 30 s** (`ready_window`): the probe returns at once, without decoding again (an idle container is not decoding on every 5 s probe; the pass is cached by the model as the skeleton's cache would have done). A request counts **whatever it made of its image**, including a cut-off answer at the token limit (`incomplete`, D25; a 422 before it): it answers. The time is taken when the predictor returns, not when the request started. A request whose decode crashed does not count.
3. **Otherwise the real selftest runs** (the answer check above). **Only a passed selftest is remembered, and only after its answer is checked.** The selftest's own decode is not a "completed request": it is judged by its answer, so a model that reads wrongly fails every probe, not just the first one (before this rule the failed selftest's decode was recorded before its answer was checked, and the next probes within 30 s returned "ready": 503, ready, ready, ready, which is what a 5 s healthcheck sees).
4. **A real selftest pass is required at least every 5 minutes** (`pass_interval`); completed requests cannot stand in for it any longer than that: a model that has never passed one (just started, or wrong weights that still answer requests) is overdue, so a completed request cannot stand in for the first selftest. When overdue and the model is busy, the selftest waits for it for at most 25 s (`lock_wait`, under the 35 s the container's healthcheck allows per probe), then runs for real. If the model stays busy it fails with `selftest could not run: no real selftest pass for N s, and the model was busy for 25 s`, and a decode that is already past 90 s fails at once (item 1) instead of making the probe wait.

The tests use a fake predictor that can be held "in flight" (`gate`) and a fake clock, and cover each item: a decode held in flight and `/readyz` answering 200 within 5 s without a second decode; a hung decode at exactly 90 s (fine) and 91 s (503), and at 191 s; a token-limit answer (a 422 before D25) followed by `/readyz` without a selftest; the window edge; a passed selftest remembered for exactly 30 s and then run again; a failed selftest that keeps failing (also over HTTP, four probes in a row, and **a wrong model, a 200 `/infer`, then `/readyz`: still 503**, through the production wiring); an idle correct model probed six times decoding once; a request completing on a model that never passed, or after a failed selftest; a crashed decode; a decode that ends after the window; a pass that is 300 s old (fine) and 300.5 s old with a request 0.6 s ago (real selftest); an overdue selftest that waits and then runs, one that times out with the message above, and one that does not wait for a hung decode.

## Memory

Measured on the dev box with TensorFlow limited to 2 + 1 threads (`VmRSS` of the process; MB here are MiB, as `/proc` reports them).

**Resident:**

| Stage | RSS |
|---|---|
| `import tensorflow` | 441 MB |
| service after `load()` (decimer 2.8.1: tokenizer and printed-structure model, `DECIMER_model`) | 2,436 MB |
| after the selftest (graph traced) | 2,648 MB |
| after the 20 golden crops | 2,739 MB (peak 2,741) |
| after 100 more crop requests | flat at 2,674 MB at 25, 50, 75 and 100 requests (a separate run of 100 structure crops straight after the selftest: no growth, unlike `cls`) |
| after the worst-case request below | 3,050 MB (peak 3,117) |

With decimer 2.8.0 (the previous pin) the same service and the same measuring script gave 4,346 MB after `load()`, 4,557 after the selftest, 4,647 after the 20 golden crops and 4,929 (peak 4,996) after the worst-case request: `import DECIMER` also loaded the hand-drawn model (`DECIMER_Hand_drawn = tf.saved_model.load(...)` at module level, also downloading it if it was missing), which cost **1,895 MB resident and 26 s of startup, and nothing here could use it** (the first measurement, 2026-10-22; the before and after of this re-pin, 4,346 to 2,436 MB and 46.9 to 22.9 s for `load()`, differ from it by 15 MB and 2 s, which is what a busy box does to timings). About 20 threads either way. **decimer 2.8.1 loads it only for `hand_drawn=True`, which nothing here asks for, so it is not in memory.** Its 348 MB of weights are still in the image: owner decision D15 (no stub, a hand-drawn option stays possible later) is unchanged and costs disk only now. The weights check does not demand them, nothing reads them, and a slow test checks that the library's hand-drawn model is not loaded after start-up. Dropping them from the image is the owner's call (the list of edits is in the comment of `deploy/weights/SHA256SUMS`).

**One request, on top of that.** Decode, RGBA array and DECIMER's preprocessing at the pixel and side limits, measured without running the transformer (which adds roughly 50 to 80 MB of working memory):

| Input | Peak above resident |
|---|---|
| RGB, RGBA or L, 4183 x 4183 (17.5 Mpx, the cap) | 296 MB |
| 8192 x 2136 (17.5 Mpx, the longest side allowed) | 312 MB |
| 8192 x 100 (thin, the longest side allowed) | 197 MB |
| framed noise, 8192 x 2136 (RGB 4x4 blocks 332 MB, RGBA 344 MB, 8-bit gray 310 MB) | up to 344 MB |
| **the same RGBA noise through the whole service, real model** | **345 to 448 MB** (the first request after load is the high one; the review measured 406 MB) |

A real rendered page (`golden` page 3, through the whole service with the limits lifted): 1 Mpx +77 MB, 8.7 Mpx +162 MB, 17.4 Mpx +287 MB, 47 Mpx +889 MB (and RSS ended up 150 MB higher, which the allocator keeps).

The last row is the worst case to size against: a framed, noisy image at the longest side (nothing for the border crop to remove, nothing for the PNG to compress) takes the full preprocessing and then 302 decode steps. A 20 MiB upload can hold such an image (8-bit gray noise of that size is 16.6 MiB). RSS ended 380 MB higher after that request, which the allocator keeps.

**Why two limits** (`MAX_PIXELS = 17,500,000`, `MAX_SIDE = 8192`). Crops from `seg` are a few hundred pixels on a side; only the single-image fallback can send a whole upload. The library pads the image to a square of 1.2 x its longest side before shrinking it to 512 x 512, so **memory follows the square of the longest side, not the pixel count**: a 100,000 x 100 strip is 10 Mpx but would ask for a 120,000 x 120,000 canvas (14 GB). The pixel limit (A3 at 300 DPI is 17.4 Mpx) and the side limit (a 48 Mpx phone photo is 8064 px long) together keep a request to 0.3 to 0.45 GB above the resident model (345 to 448 MB for the worst case, a framed, noisy 8192 x 2136 image through the whole service). This is lower than D11's 50 Mpx upload limit and `cls`'s cap. A larger single image needs downscaling by the worker before it comes here, which is safe because the model looks at a 512 x 512 thumbnail; `seg` already refuses above 40 Mpx, so the worker has to do that for large uploads anyway. Nothing at 50 Mpx is worth +0.9 GB: a whole page is read as garbage anyway (see "The token limit").

**Sizing the container limit (P2-05; 4 GB since 2026-10-26, was 6 GB for decimer 2.8.0).** Resident 2.65 to 3.05 GB (2,653 MB after load and selftest, 3,050 MB after the worst-case request above), plus 0.46 GB for one worst-case request (peak 3,117 MB), plus the request bodies: the skeleton reads each body into memory (up to 20 MiB) before it decides whether to queue or refuse, so 5 slots (1 running, 4 waiting) are 0.1 GB. That is about 3.6 GB at the very worst, **inside 4 GB with about 0.4 GB to spare**, the margin decimer 2.8.0 had under its 6 GB (it needed 5.6 GB at the very worst, 4.9 to 5.0 GB resident). The replica counts are unchanged. Memory did not grow over 100 requests, but nothing here is a soak of thousands.

## Weights

decimer 2.8.1 keeps its weights in `$PYSTOW_HOME/DECIMER-V2/` (default `~/.data/DECIMER-V2/`) and **downloads them, and deletes and re-downloads them when the marker file says another URL, when it loads its models** (`DECIMER.load_models()`, or the first `predict_SMILES()`; decimer 2.8.0 did it as a side effect of `import DECIMER`). `_require_weights()` therefore checks the files first, and refuses to import the library when one is missing or a `.model_url` marker differs from the expected URL (unless `OCSR_ALLOW_WEIGHT_DOWNLOAD=1`). It checks what `load_models(printed=True, hand_drawn=False)` reads: the printed-structure SavedModel and its tokenizer (which decodes the output of either model). Nothing under `DECIMER_HandDrawn_model/` is read, checked or downloaded (the check used to demand it, and its marker, because decimer 2.8.0 loaded it on import), and neither are `max_length.pkl` and the hand-drawn `tokenizer_pubchem.pkl`; the image carries the hand-drawn files all the same.

Downloaded on 2026-10-22 from Zenodo (zip hashes are of the files as downloaded; the library extracts and deletes them):

| Zip | URL | Bytes | SHA-256 |
|---|---|---|---|
| `models.zip` | `https://zenodo.org/record/8300489/files/models.zip` | 298,662,033 | `8ebc7db18fffac7e71a2cf8487708053620d124846bc9502eddac1283b431d2b` |
| `DECIMER_HandDrawn_model.zip` | `https://zenodo.org/records/10781330/files/DECIMER_HandDrawn_model.zip` | 298,515,112 | `91c70fee79b7d64c09d433ff82e2216d16070e4d098b83da2c13d34c52439fde` |

Extracted files (sizes and SHA-256; the four of `DECIMER_model` that `load_models(printed=True)` reads are the ones `_require_weights()` checks):

| File under `DECIMER-V2/` | Bytes | SHA-256 |
|---|---|---|
| `DECIMER_model/saved_model.pb` | 28,080,309 | `d1a6e61de631e09cb347ddb47d4519732e2d000365b4f2318e5e8a1d403f9055` |
| `DECIMER_model/variables/variables.data-00000-of-00001` | 319,860,349 | `fa16b7e712c18af64a5930240a75ca34c8dec163f5407376b9a5300ebbda38eb` |
| `DECIMER_model/variables/variables.index` | 65,628 | `1793a178fe88af3f49bb2683f84560d5bfb79c871402fdc93b08c741051dfede` |
| `DECIMER_model/assets/tokenizer_SMILES.pkl` | 4,670 | `c1d579f958146bb1a948320dad92f0eecf4a51001ec9dec1f48767c7b681b43c` |
| `DECIMER_model/assets/max_length.pkl` | 15 | `e9b21b8f7e5bee625ee3d5dfc1cca40c7d6f9791c54dc5901441cee0a8f3e520` |
| `DECIMER_HandDrawn_model/saved_model.pb` | 28,080,328 | `d3b124e3ad4b0ed57ff834e2c8cb8b2e3d4aee4b0546b138634d26e58979e6e5` |
| `DECIMER_HandDrawn_model/variables/variables.data-00000-of-00001` | 319,860,349 | `7c2c678eea80be1ce42bca1029f63ca3322cc6f9649ef70da21e5c37ad18a376` |
| `DECIMER_HandDrawn_model/variables/variables.index` | 65,628 | `98938e8df526dcd638e6c71c3d73aa48cd36d0ebbcee8128f8c0f3f0d6aa786e` |
| `DECIMER_HandDrawn_model/assets/tokenizer_pubchem.pkl` | 4,670 | `c1d579f958146bb1a948320dad92f0eecf4a51001ec9dec1f48767c7b681b43c` |

The library also writes `DECIMER_model/.model_url` (`https://zenodo.org/record/8300489/files/models.zip`, 50 bytes) and `DECIMER_HandDrawn_model/.model_url` (69 bytes) after a download. A baked image can omit them or write exactly these.

**The five `DECIMER_model` files are byte-identical to the legacy site's** (the archive `weights-archive/2026-10-22/decimer-transformer/DECIMER-V2/DECIMER_model/`, same URL, same SHA-256 for all five): decimer 2.8.0 and 2.8.1 use the same printed-structure weights as the 2023 legacy commit. The hand-drawn model is new since then and legacy never had it. So any difference from legacy on the same crop comes from the code and runtime around the weights (TensorFlow 2.7 to 2.15.1, Pillow, numpy), not from the model; on the golden crops there is none (below).

A slow test checks that the installed library still has the URL in `MODEL_URLS` and looks in `weights_dir()`.

## Dependencies

`decimer==2.8.1` (PyPI), `tensorflow==2.15.1` (Keras 2.15), `numpy==1.26.3`, `protobuf==4.25.1`, `pillow` 13.0.0, `pystow` 0.10.3. decimer's own requirements (`tensorflow>=2.12,<=2.20`, `opencv-python`, `pillow-heif`, `efficientnet`, `selfies`, `pyyaml`) resolve without an override. Resolved: decimer 2.8.1 (the only change from 2.8.0 is the lazy loading, in `DECIMER/__init__.py` and `decimer.py`: same models, same decode, byte-identical answers, confidences included, on the 20 golden crops of the run `next-20261025-154248`), efficientnet 1.1.1, opencv-python 4.11.0.86, pillow-heif 1.8.1, selfies 2.2.0, scikit-image 0.26.0. `opencv-python` needs libGL in a bare container: P2-05 can use `opencv-python-headless` through a uv override, as planned for `seg`. Dev: RDKit 2026.9.1 (golden InChIKeys, selftest oracle), httpx, pytest-asyncio.

## Tests

```bash
uv run pytest               # 137 fast tests, no TensorFlow import (slow tests are deselected), about 3 s
uv run pytest -m slow       # 19 tests on the real model: about 6 minutes at 2 + 1 threads
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

The fast tests use an injected predictor (a stand-in that returns numpy float32 confidences like the library does, and does the library's first step, `Image.fromarray(...).convert("RGBA")` and alpha compositing, so unusable arrays fail in it too) and cover the response shape and types, the confidence pairs, the mean (chosen so that arithmetic mean, median, minimum, geometric mean and product all differ), the empty prediction, non-finite confidences, the token-limit answer (`incomplete`, with the last token put back; a special token among them is still the 422) and the other token mismatch, what the predictor receives for every format and mode (pixel for pixel, transparency kept, 16-bit scaled, EXIF not applied), every ValueError path (decode, formats, pixel cap, side cap, header-only check, decompression bomb, unsupported mode), that nothing is written to the working or temp directory, the lock, the selftest (passes, wrong structure, empty answer, crash), the weights check (each of the four files, the hand-drawn weights not needed, the `.model_url` marker, the explicit opt-in, no TensorFlow import when weights are missing, library output kept off stdout), the wiring to `DECIMER.load_models` (the printed model only, before any prediction) and `predict_SMILES`, and the HTTP app through `httpx.ASGITransport` with the lifespan (limits pinned through `build_app`, the 20 MiB boundary, 422/500/503, `/readyz`, `__main__`). Deliberate breakages of `model.py`, `app.py` and the weights check were each caught by a test (first review round: 16; second: 14 more; third: 10 more; fourth: 4 more): the mean computed as a minimum or without the last token, an empty prediction scoring 1.0, no `float()` on the confidences, no lock, a selftest that never fails, the token-limit rule removed, the non-finite check removed, each size limit removed, the weights check skipped, `hand_drawn=True`, `confidence=False`, transparency flattened to black, 16-bit not scaled, and a `ValueError` from the library no longer wrapped. Second round: the selftest waiting for the lock, ignoring recent decodes, using `<` instead of `<=` at the window edge, never recording a decode, recording it at its start, counting only valid answers; fourth round (cache off): the skeleton cache left at its default 30 s, the window read from `LIMITS`, the model not caching its own pass, the pass-interval gate dropped from the shortcut; third round (readiness): the selftest's own decode recorded as a completed request, the pass remembered before its answer is checked, a busy model never reported hung, `>=` instead of `>` at 90 s, the decode start never cleared, the pass interval ignored or its edge flipped, a busy model never overdue, the overdue wait skipped or unbounded; special tokens accepted, or only in their raw spelling; the 422 wording changed; `FORMATS` or `SUPPORTED_MODES` widened; the `import DECIMER` output logged only on success; the `.model_url` check limited to `DECIMER_model`; `build_app` not passing the readiness window.

The slow tests run the whole service path (lifespan load, production limits, `/infer`) on the crops of the committed legacy run `golden/runs/legacy-20261022-140954`, rows 0 to 19 (legacy caps a job at 20 structures). **P2-03's check pins the exact set of crops whose label the model reads right** (`labels.csv` points at the crop through `legacy_segment`): 13 of the 19 labels on these crops (crops 0, 3–11 and 16–18), the same as legacy (owner decision 2026-10-26; a change in that set fails, and changing it needs Kohulan). The fixture test checks the 19 labels, 17 of them `checked=yes`: since commit `eafb21c` (2026-10-23) `checked` means "checked by Kohulan on the review page", and the two Figure 3 drawings with correlation arrows are `checked=no`/`scored=no`. They also check that every token of every prediction has a confidence in [0, 1] and that the tokens concatenate to the SMILES, the real model's selftest, the array route against the library's file route, the other pixel modes, three degenerate images (1 x 1, blank, strip: a prediction, `incomplete` when cut off, never a 500), that exactly crop 15 is flagged `incomplete` and every crop is answered 200, that a request prints nothing and writes nothing, and that it works on a thread that did not load the model.

`TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 uv run python scripts/compare_golden.py` prints the numbers instead of pass/fail (`--all` reads the 12 crops legacy never processed as well).

## Golden crops, rows 0 to 19

`*` is a PubChem-confirmed label (`checked=yes` when this table was made; see "Tests" for what `checked` means now). "legacy" and "new" say whether the SMILES gives the labelled full InChIKey (RDKit, stereo included). "same" says whether the SMILES string is identical to legacy's.

| crop | label | legacy | new | mean | same |
|---|---|---|---|---|---|
| 0 | 1 | correct | correct | 0.949 | yes |
| 1 | 2 | wrong | wrong | 0.915 | yes |
| 2 | 3 | wrong | wrong | 0.888 | yes |
| 3 | 4 * | correct | correct | 0.979 | yes |
| 4 | 5 * | correct | correct | 0.981 | yes |
| 5 | 6 * | correct | correct | 0.986 | yes |
| 6 | 7 * | correct | correct | 0.989 | yes |
| 7 | 8 | correct | correct | 0.980 | yes |
| 8 | 9 | correct | correct | 0.998 | yes |
| 9 | 10 | correct | correct | 0.995 | yes |
| 10 | 11 * | correct | correct | 0.994 | yes |
| 11 | 12 * | correct | correct | 0.995 | yes |
| 12 | 1 (Fig 3) | wrong | wrong | 0.767 | yes |
| 13 | none | n/a | n/a | 0.801 | yes |
| 14 | 2 (Fig 3) | wrong | wrong | 0.822 | yes |
| 15 | 3 (Fig 3) | wrong | **200, `incomplete`** | 0.659 | no |
| 16 | S1 | correct | correct | 0.982 | yes |
| 17 | S2 | correct | correct | 0.992 | yes |
| 18 | S3 | correct | correct | 0.994 | yes |
| 19 | S4 | wrong | wrong | 0.966 | yes |

**All 6 checked labels (then) are correct. 13 of the 19 labels that sit on one of these crops are correct, the same 13 as legacy; no crop is better or worse than legacy.** 19 of 20 SMILES strings are identical to legacy's (the twentieth is the token-limit crop, where legacy returned a 302-character string ending in a long chain of carbons; **since D25 the service returns that same string, byte for byte, flagged `incomplete`, which RDKit does not parse: 20 of 20, measured 2026-10-27**). The unchecked labels: 7 of 13 correct. The six wrong ones are the model's own limits and are the same wrong answers legacy gives: stereo drawn where a wavy bond means none (1), atom numbers read as 13C (2), the three figures with correlation arrows (12, 14, 15), a ring methyl that is not there (19). Of the 26 labels, 7 sit on crops 20 to 31, which legacy never processed; reading those too (`--all`), the service gets S5 and S6 right and S7 to S11 wrong, **15 of 26 labels in all** (reported only; `golden/check.py` against the whole pipeline is P2-06, and the pass rule in ROADMAP §5 compares with legacy's 13 of 26 there).

Time per crop (2 + 1 threads, `nice`, the site running beside it): median 3.1 to 3.5 s over the 20 crops, 1.2 to 5.6 s for crops of up to about 60 tokens, 10 s for crop 13 (101 tokens), 24 s for crop 14 (197), 39 to 41 s for crop 12 (285), 38 to 43 s for crop 15 (the token limit); the 20 crops take 160 s in all (decimer 2.8.0; about 117 s with either pin in the 2.8.1 re-pin measurement, 2026-10-25, the answers identical). Peak resident 2,741 MB with decimer 2.8.1 after load, selftest and these 20 crops (4,800 MB with 2.8.0, when the timings above were taken).

## Things to know about `DECIMER` 2.8.1

- `import DECIMER` loads TensorFlow and nothing else (2.8.0 loaded **both** SavedModels and downloaded whatever was missing, on import). The models are downloaded, if missing, and loaded by `DECIMER.load_models()` or, if that was not called, by the first `predict_SMILES()`, thread-safely. `OcsrModel.load()` checks the weights first (the service never downloads), imports the library only there, with `contextlib.redirect_stdout` (it prints download progress and paths while it loads; what it printed is logged in a `finally`, so a failed download or a corrupt file is explained even when the import or the load raises), and calls `load_models(printed=True, hand_drawn=False)` so the first request and `/readyz` find a loaded model; importing `decimer_ocsr.model` is cheap and the fast tests never touch TensorFlow.
- It sets `CUDA_VISIBLE_DEVICES=0` and enables GPU memory growth, and assigns `TF_CPP_MIN_LOG_LEVEL=3` to the environment after importing TensorFlow. On this CPU-only service these are no-ops.
- `predict_SMILES(..., confidence=True)` returns `(smiles, [(token, float32), ...])`. The confidence array is indexed by decoding step, with step 0 (the start token) unwritten, so the library cuts `[1:-1]` to drop start and end; at the token limit that cuts a real token (see above).
- The model is a `tf.saved_model` called from Python, so only one request may run at a time. `/readyz` is not behind the `/infer` gate, so a selftest can overlap a request; a lock inside `OcsrModel` makes the two take turns instead.
- `predict_SMILES` does not print during inference, so the service needs no Keras progress-bar handling (unlike `cls`); a slow test checks that a request adds nothing to stdout or the logs.
- Pillow is given an allow-list of formats. Pillow can open many more, and EPS shells out to Ghostscript on load.
