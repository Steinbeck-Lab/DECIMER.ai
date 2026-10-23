# Golden set

One open-access paper with hand-checked labels, used to check scientific output before any change to the models, TensorFlow, DECIMER or RDKit reaches users (ROADMAP §5).

**Paper:** Zou R., Li X., Chen X., Guo Y.-W., Xu B. *Chemical and biosynthetic potential of* Penicillium shentong *XL-F41.* Beilstein J. Org. Chem. 2024, 20, 597–606. https://doi.org/10.3762/bjoc.20.52. Licensed under the Beilstein-Institut Open Access License, which is identical to CC BY 4.0. File: `zou2024_penicillium_shentong.pdf` (SHA-256 `9429f108fc81cb950e6042e71868d7c7ef2fd35372cc6b664a452b777aace849`).

**Why this paper:** 10 pages (the site's page cap). It has 12 numbered structures with stereochemistry (Figure 2), the same structures redrawn with NMR correlation arrows (Figure 3), a 3D conformer image, and a busy biosynthesis scheme with R-groups and charges (Figure 5). That gives 32 segments in total, so the 20-structure cap is exercised as well.

## Labels (`labels.csv`)

- One row per drawn structure that is a complete molecule. R-group fragments and the 3D conformer image get no label.
- The label is what is **drawn**: stereochemistry only where it is drawn (a wavy bond means none).
- Structures drawn twice (Figure 3, and the dashed boxes in Figure 5) get one row per drawing.
- `checked=yes` only after a human check, or an independent full-InChIKey match on PubChem by compound name (noted in `note`).
- Change a label only with a written reason in its `note`.

`review/labels-review.png` shows each label's structure next to its crop.

## Commands

```bash
uv run decimer-golden legacy --base-url http://127.0.0.1:8080   # run the paper through the live legacy site
uv run decimer-golden next --base-url http://127.0.0.1:8090     # run the paper through the new stack's /api
uv run decimer-golden score runs/<run>/predictions.csv          # score a run against labels.csv
uv run decimer-golden draft-labels runs/<run>/predictions.csv   # first-pass labels from a run
```

Each run writes `runs/<stack>-<timestamp>/` (`-2`, `-3`, ... is appended when a run of the same second already has the name) containing `predictions.csv`, `run.json` and `crops/`.

### `next`

```bash
uv run decimer-golden next [--base-url http://127.0.0.1:8090] [--pdf FILE] [--poll 1.0] [--timeout 900] \
    [--docker-project decimer-next]
```

Uploads the PDF to `POST /api/jobs`, polls `GET /api/jobs/{id}` every `--poll` seconds until the job is `done` or `failed`, then downloads each result's crop from the media URL the job lists (same origin as `--base-url`, which is an origin, not a path). One job at a time. 429 and 503 are answered by waiting for `Retry-After` (5 s when the answer has none). `--timeout` bounds the upload and the wait together, retries included; the crop downloads get their own 5 minutes.

- `predictions.csv` has the legacy columns (`index,segment,smiles,inchikey,classifier`) plus `page` (1-based, as in `labels.csv`), `valid`, `confidence`, `name` and `name_tier`. `classifier` is the API's `isStructure` (`True`, `False`, or empty when the classifier gave no answer). The InChIKey is recomputed from the SMILES with RDKit, as for legacy; `run.json` lists the positions where the API's own `inchikey` differs. The structures the API found but did not return (its 20-structure cap, `structuresDropped`) are appended as empty rows, so "dropped by cap" is comparable with the legacy run. A result with an empty SMILES (recognition failed) scores as invalid, not as dropped; only the padding rows, whose `valid` is empty, count as cut by the cap. `score` reads `valid` strictly: `True`, `False` or empty, anything else is an error.
- `run.json` holds `started_at` (UTC, taken before the upload), `seconds` (`upload`, `processing`, `total` = from the start of the upload the API accepted to the poll that saw `done`, `per_page`, `retry_wait`, `crops`), `stage_seconds` and `stage_seen` (the job record has stage states but no timestamps, so these are what the polling saw: accurate to about `--poll`), `trace` (every change of status or stage), `retries`, the job's `warnings`, `pages`, `pages_processed`, `truncated`, `structures_found`, `structures_dropped`, the `failure` if any, and `pdf_sha256`.
- `--docker-project NAME` samples `docker stats --no-stream` for that Compose project's containers every 2 s while the job runs (not during the crop downloads) and records in `run.json` under `memory` each container's baseline (before the upload) and peak in MiB, the highest total at any one moment, and that peak minus the baseline, also per processed page. This is the "memory per page" of TODO. A sampling problem is recorded under `memory.errors` and never fails the run.
- A failed job, a refused upload, a timeout, an unreachable API or a crop that cannot be fetched writes `run.json` (with `failure.kind`, `message` and the job id) and exits 1; bad arguments exit 2. `predictions.csv` is written whenever the job finished `done`, so a crop problem still leaves a scorable run. There is no way to cancel a job yet, so a run that times out or is interrupted leaves its job to expire.

### `single-image`

```bash
uv run decimer-golden single-image [--base-url http://127.0.0.1:8090] [--crops runs/<run>/crops] \
    [--negatives single-negatives] [--also-without-rule] [--before runs/single-<run>/single-image.csv] \
    [--whole-fraction 0.6] [--poll 1.0] [--timeout 300]
```

Measures D20 (a job of one pasted image where seg finds exactly one structure covering at least `SINGLE_IMAGE_WHOLE_FRACTION` of it is read whole) from outside, through the API: every image is its own job, one at a time, and every job is deleted (`DELETE /api/jobs/{id}`) when it is done, also when it failed or its answer could not be read. Writes `runs/single-<timestamp>/single-image.csv` and `run.json`, and prints a table.

- `--crops`: the crops of a `next` run (`NN_crop-K.png`). Each is already one seg crop, so the rule should fire on all of them; its label is the one whose `legacy_segment` is `NN`, crops without one (the 3D conformer image) are measured but not scored.
- `--negatives`: `single-negatives/`, regions of the paper's pages where the rule should not fire (or may fire on a box that is mostly structure): a structure with its number, with a reaction arrow, with a dashed box, with the text next to it, with rows of Table 1 (a composite), and two structures side by side. `negatives.csv` says what each contains and which labels (numbers of `labels.csv`, `;`-separated) it holds; `single-negatives/render.py` regenerates them from the PDF (`cd api && uv run python ../golden/single-negatives/render.py`, pypdfium2 at 300 DPI).
- `--also-without-rule`: repeats every image as a job of two images (the image and a blank one). The rule never applies to a job like that, so it shows seg's own box and what the crop path reads, to set beside the single-image job; this is how a negative is judged. The columns `off_*` hold it.
- Scoring is by full InChIKey: an image is right when every label it contains is among its results. `fired` is the rule having made the job read the image whole (exact with `--also-without-rule`; without it a seg that found nothing, the older fallback, looks the same). `--before` compares with an earlier CSV per crop (predicted against fired, right before and after).
- **The labels are mostly drafts.** The table marks a label that no human has checked with `*` and the command ends with the same kind of WARNING as `score`; the CSV has a `checked` column. An absolute count ("13/19 right") is against those drafts, only the before/after difference is solid evidence.

## Baseline

| Run | Correct | Extra | Invalid | Cut by cap | Time |
|---|---|---|---|---|---|
| `legacy-20261022-140954` (TF 2.7, DECIMER `3db6954`, segmentation 1.4.0) | 13 / 26 | 6 | 1 | 12 | 95.3 s |
| `next-20261022-230836` (TF 2.15.1, DECIMER 2.8.0, 2 seg / 2 ocsr / 2 cls replicas, stages one after the other) | 13 / 26 | 5 | 2 | 12 | 98.3 s |
| `next-20261023-071544` (same, stages overlapping, P3-04c / D19; every prediction identical to the row above) | 13 / 26 | 5 | 2 | 12 | 80.2 s |

The two times are not measured alike. Legacy's 95.3 s is the sum of three step timers, one per request (upload 4.47 s + segmentation 38.85 s + recognition 52.0 s, from its `run.json`), not one wall-clock span: the page loads between the steps are not in it. `next`'s `total` is wall clock from the start of the upload to the poll that saw `done`, so it is good to one poll interval (1 s here), and its stage times to about 1 s.

`next-20261022-231212` is the same run with 3 ocsr replicas: 99.3 s and the same score, so recognition is bounded by one non-structure crop (about 46 s from the ocsr log's timestamps, ending in a 422), not by the replica count. Both runs predate the `started_at` fix: their `run.json` gives the end of the run there; the directory name is the start.
