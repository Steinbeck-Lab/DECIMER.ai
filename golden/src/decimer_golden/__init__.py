"""Golden-set check for DECIMER.ai (docs/ROADMAP.md §5).

uv run decimer-golden legacy --base-url http://127.0.0.1:8080   run the paper through legacy
uv run decimer-golden score runs/<run>/predictions.csv          score a run against labels.csv
uv run decimer-golden draft-labels runs/<run>/predictions.csv   first-pass labels to correct
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

from . import legacy
from .score import Prediction, read_labels, read_predictions, score, to_inchikey, write_predictions

HERE = Path(__file__).resolve().parents[2]
PAPER = HERE / "zou2024_penicillium_shentong.pdf"
LABELS = HERE / "labels.csv"
RUNS = HERE / "runs"


def _legacy(args: argparse.Namespace) -> int:
    run_dir = RUNS / f"legacy-{time.strftime('%Y%m%d-%H%M%S')}"
    (run_dir / "crops").mkdir(parents=True)
    result = legacy.run(args.base_url, PAPER)
    predictions = []
    for i, segment in enumerate(result.segments):
        smiles = result.smiles[i] if i < len(result.smiles) else ""
        crop = f"crops/{i:02d}_{Path(segment).name}"
        legacy.download(args.base_url, segment, run_dir / crop)
        predictions.append(
            Prediction(
                index=i,
                smiles=smiles,
                inchikey=to_inchikey(smiles),
                segment=crop,
                classifier=result.classifier[i] if i < len(result.classifier) else "",
            )
        )
    write_predictions(run_dir / "predictions.csv", predictions)
    meta = {
        "stack": "legacy",
        "base_url": args.base_url,
        "seconds": result.seconds,
        "page_images": len(result.page_images),
        "segments": len(result.segments),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(
        f"{run_dir.relative_to(HERE)}: {len(result.page_images)} pages, "
        f"{len(result.segments)} segments, {sum(result.seconds.values()):.1f} s {result.seconds}"
    )
    return 0


def _score(args: argparse.Namespace) -> int:
    labels = read_labels(LABELS)
    unchecked = [label.number for label in labels if not label.checked]
    s = score(labels, read_predictions(Path(args.predictions)))
    print(
        f"correct {s.correct}/{s.labels} · extra {s.extra} · invalid {s.invalid} · "
        f"dropped by cap {s.dropped}"
    )
    if s.missing:
        print("missing labels:", ", ".join(s.missing))
    if unchecked:
        print(f"WARNING: {len(unchecked)} labels not yet checked by hand:", ", ".join(unchecked))
    return 0


def _draft_labels(args: argparse.Namespace) -> int:
    out = HERE / "labels.draft.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["number", "page", "inchikey", "smiles", "checked", "note"])
        for p in read_predictions(Path(args.predictions)):
            if p.inchikey:
                w.writerow(["?", 0, p.inchikey, p.smiles, "no", f"from {p.segment}"])
    print(
        f"wrote {out.relative_to(HERE)}: fill in number and page, correct the SMILES, "
        "set checked=yes, then save as labels.csv"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="decimer-golden")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("legacy", help="run the paper through the legacy site")
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.set_defaults(func=_legacy)
    for name, func in (("score", _score), ("draft-labels", _draft_labels)):
        p = sub.add_parser(name)
        p.add_argument("predictions")
        p.set_defaults(func=func)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
