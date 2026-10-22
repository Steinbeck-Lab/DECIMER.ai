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
uv run decimer-golden score runs/<run>/predictions.csv          # score a run against labels.csv
uv run decimer-golden draft-labels runs/<run>/predictions.csv   # first-pass labels from a run
```

Each run writes `runs/<stack>-<timestamp>/` containing `predictions.csv`, `run.json` (step timings) and `crops/`.

## Baseline

| Run | Correct | Extra | Invalid | Cut by cap | Time |
|---|---|---|---|---|---|
| `legacy-20261022-140954` (TF 2.7, DECIMER `3db6954`, segmentation 1.4.0) | 13 / 26 | 6 | 1 | 12 | 95.3 s |
