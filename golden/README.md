# Golden set

One open-access paper with hand-checked labels, used to check scientific output before any change to the models, TensorFlow, DECIMER or RDKit reaches users (ROADMAP §5).

**Paper:** Zou R., Li X., Chen X., Guo Y.-W., Xu B. *Chemical and biosynthetic potential of* Penicillium shentong *XL-F41.* Beilstein J. Org. Chem. 2024, 20, 597–606. https://doi.org/10.3762/bjoc.20.52. Licensed under the Beilstein-Institut Open Access License, which is identical to CC BY 4.0. File: `zou2024_penicillium_shentong.pdf` (SHA-256 `9429f108fc81cb950e6042e71868d7c7ef2fd35372cc6b664a452b777aace849`).

**Why this paper:** 10 pages (the site's page cap). It has 12 numbered structures with stereochemistry (Figure 2), the same structures redrawn with NMR correlation arrows (Figure 3), a 3D conformer image, and a busy biosynthesis scheme with R-groups and charges (Figure 5). That gives 32 segments in total, so the 20-structure cap is exercised as well.

## Labels (`labels.csv`)

- One row per drawn structure that is a complete molecule. R-group fragments and the 3D conformer image get no label.
- The label is what is **drawn**: stereochemistry only where it is drawn (a wavy bond means none).
- Structures drawn twice (Figure 3, and the dashed boxes in Figure 5) get one row per drawing.
- `scored=no` leaves a label out of the count (see Baseline). `score` reads it strictly: `yes` or `no`.
- `checked=yes` only after a human check, or an independent full-InChIKey match on PubChem by compound name (noted in `note`).
- Change a label only with a written reason in its `note`.

`review/labels-review.png` shows each label's structure next to its crop.

## Commands

```bash
uv run decimer-golden legacy --base-url http://127.0.0.1:8080   # run the paper through the live legacy site
uv run decimer-golden next --base-url http://127.0.0.1:8090     # run the paper through the new stack's /api
uv run decimer-golden single-image --crops runs/<run>/crops      # every crop as a pasted image
uv run decimer-golden multi-image --crops runs/<run>/crops       # crops in groups of 2 and 5
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

- `predictions.csv` has the legacy columns (`index,segment,smiles,inchikey,classifier`) plus `page` (1-based, as in `labels.csv`), `valid`, `confidence`, `name` and `name_tier`. `classifier` is the API's `isStructure` (`True`, `False`, or empty when the classifier gave no answer). The InChIKey is recomputed from the SMILES with RDKit, as for legacy; `run.json` lists the positions where the API's own `inchikey` differs. The structures the API found but did not return (its 20-structure cap, `structuresDropped`) are appended as empty rows, so "dropped by cap" is comparable with the legacy run. A result with an empty SMILES (recognition failed: the OCSR refused the crop) scores as refused, not as invalid and not as dropped; only the padding rows, whose `valid` is empty, count as cut by the cap. Invalid is a non-empty SMILES that RDKit rejects. `score` reads `valid` strictly: `True`, `False` or empty, anything else is an error.
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
- `--also-without-rule`: repeats every image as a job of two images (the image and a blank one). The rule never applies to a job like that, so on a stack before D23 it shows seg's own box and what the crop path reads, to set beside the single-image job; this is how a negative is judged. The columns `off_*` hold it. From D23 on such a job never calls seg (each image is read whole), so the option no longer shows seg's box; see `multi-image`.
- Scoring is by full InChIKey: an image is right when every label it contains is among its results. `fired` is the rule having made the job read the image whole (exact with `--also-without-rule`; without it a seg that found nothing, the older fallback, looks the same). `--before` compares with an earlier CSV per crop (predicted against fired, right before and after).
- **The labels are mostly drafts.** The table marks a label that no human has checked with `*` and the command ends with the same kind of WARNING as `score`; the CSV has a `checked` column. An absolute count ("13/19 right") is against those drafts, only the before/after difference is solid evidence.

### `multi-image`

```bash
uv run decimer-golden multi-image --crops runs/<run>/crops [--base-url http://127.0.0.1:8090] \
    [--sizes 2,5] [--limit N] [--before runs/multi-<run>/multi-image.csv] [--poll 1.0] [--timeout 300]
```

Measures D23 (a job of several images never calls seg: every image is read whole, numbered in image order, the box is the image) from outside, through the API. The crops of a `next` run are sent together, in consecutive groups of each `--sizes` size (pairs and groups of five by default; a leftover shorter than a group is not sent; `--limit` caps the jobs per size), one job at a time, every job deleted afterwards. Writes `runs/multi-<timestamp>/multi-image.csv` and `run.json`, and prints one line per job.

- Each image is scored by FULL InChIKey against the results with its own `pageIndex`, so a stack that still segments (more than one result for an image, a shifted numbering) is scored fairly too.
- The signs of D23 per job: `found` (`structuresFound`) and `job_results` equal the number of images, `ordered` (result N is `index` N on `pageIndex` N) and every box the whole image; `job_whole` is all of them. A stack before D23 shows `job_whole=False` on every job; the number of D23-shaped jobs is the before/after check, the right/scored count the accuracy one (the labels are drafts, as for `single-image`).
- Run it before and after a change of the worker and compare with `--before`.

## Baseline

| Run | Correct | Extra | Invalid | Refused | Cut by cap | Time |
|---|---|---|---|---|---|---|
| `legacy-20261022-140954` (TF 2.7, DECIMER `3db6954`, segmentation 1.4.0) | 13 / 24 | 6 | 1 | 0 | 12 | 95.3 s |
| `next-20261022-230836` (TF 2.15.1, DECIMER 2.8.0, 2 seg / 2 ocsr / 2 cls replicas, stages one after the other) | 13 / 24 | 5 | 1 | 1 | 12 | 98.3 s |
| `next-20261022-231212` (same, 3 ocsr replicas; recognition is bounded by the one crop 12, see below) | 13 / 24 | 5 | 1 | 1 | 12 | 99.3 s |
| `next-20261023-071544` (same as 230836, stages overlapping, P3-04c / D19; every prediction identical to `230836`) | 13 / 24 | 5 | 1 | 1 | 12 | 80.2 s |
| `next-20261023-090133` (stages overlapping, CDK fallback on) | 13 / 24 | 5 | 1 | 1 | 12 | 83.2 s |
| `next-20261023-121537` (main after P3-04d, CDK on, quiet machine: load about 3) | 13 / 24 | 5 | 1 | 1 | 12 | 75.2 s |

Two things changed in how a run is scored on 2026-10-23, so the rows above differ from the earlier README's 13 / 26 and, for the new stack, invalid 2 (the predictions did not change):

- **The two Figure 3 drawings of structures 1 and 2 are not scored** (`scored=no` in `labels.csv`): the NMR correlation arrows run over their bonds, and Kohulan's label review marked them as difficult. 24 labels count, not 26. A prediction fills a scored label first; one that can only fill an excluded label counts as neither correct nor extra, so reading the structure well is never punished.
- **A refusal is counted apart from an invalid SMILES**, as ROADMAP §5 words it ("Invalid: SMILES that RDKit rejects"; gate P2-08 passes as §5 words it). Refused is an empty SMILES that the stack returned: it tried and its recognition declined to answer. Invalid is only a non-empty SMILES RDKit rejects; cut is only the padding rows beyond the cap.

**Incomplete.** The API's `incomplete` (the model stopped at its token limit, so the SMILES is cut off) is the last column of `predictions.csv` (`True` or `False`; older runs have no column and read as `False`). `score` counts such a prediction as *incomplete*, not as invalid, extra, correct or refused, and its line gains ` · incomplete N` after `invalid` when a run has any: `invalid 1 · incomplete 1 · refused 0`. What the owner accepted is a cut-off prediction of a label that is excluded from scoring: not a new failure (it was a refusal before), correct stays 13/24 = legacy, and the invalid count is 2 with 1 of them incomplete against legacy's 1 (legacy's one invalid is itself a cut-off string, index 15). The tool reports every flagged prediction apart, and `deploy/cutover/golden-smoke.sh` allows at most one (`GOLDEN_MAX_INCOMPLETE`, default 1), so a build that cuts off more structures fails the cutover gate. The committed runs predate the column, so their numbers and the table above are unchanged. Crop 12 of `next-20261026-151657` is the case: refused in that run; built after D25 it is a cut-off SMILES that RDKit and CDK 2.8 both reject.

The only refusal in any committed next run is crop 12, which is the `1 (Fig 3)` drawing: the very drawing the first change excludes. The classifier called it "not a structure" although it is one, the OCSR declined it (the job's `recognition_failed` warning for structure 13), and legacy read the same crop as a valid but wrong SMILES (an extra). The invalid SMILES are one in each stack: legacy's is index 15, the `3 (Fig 3)` drawing, the new stack's is index 13, a structure with no label. So under the old rule (a refusal is invalid) the new stack had 2 invalid against legacy's 1 and P2-08 failed only because of a refusal on an excluded drawing. With refusals counted apart it is 1 against 1, and with the excluded drawings left out entirely it would also be 1 against 1. Refusals are reported, not limited by the pass rule's invalid count; correct is what catches a stack that refuses too much.

The two times are not measured alike. Legacy's 95.3 s is the sum of three step timers, one per request (upload 4.47 s + segmentation 38.85 s + recognition 52.0 s, from its `run.json`), not one wall-clock span: the page loads between the steps are not in it. `next`'s `total` is wall clock from the start of the upload to the poll that saw `done`, so it is good to one poll interval (1 s here), and its stage times to about 1 s.

`next-20261022-231212` is the same run with 3 ocsr replicas: 99.3 s and the same score, so recognition is bounded by one crop, crop 12 (the `1 (Fig 3)` drawing: a structure, though the classifier said it is not one), which took about 46 s from the ocsr log's timestamps and ended in a 422; the replica count does not bound it. Both runs predate the `started_at` fix: their `run.json` gives the end of the run there; the directory name is the start.
