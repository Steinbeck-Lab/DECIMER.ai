"""Golden-set check for DECIMER.ai (docs/ROADMAP.md §5).

uv run decimer-golden legacy --base-url http://127.0.0.1:8080   run the paper through legacy
uv run decimer-golden next --base-url http://127.0.0.1:8090     run the paper through the new stack
uv run decimer-golden score runs/<run>/predictions.csv          score a run against labels.csv
uv run decimer-golden draft-labels runs/<run>/predictions.csv   first-pass labels to correct
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import legacy, next_stack
from .docker_memory import PROJECT_PATTERN, MemorySampler
from .score import Prediction, read_labels, read_predictions, score, to_inchikey, write_predictions

HERE = Path(__file__).resolve().parents[2]
PAPER = HERE / "zou2024_penicillium_shentong.pdf"
LABELS = HERE / "labels.csv"
RUNS = HERE / "runs"


def _make_run_dir(stack: str, stamp: str) -> Path:
    """Create `runs/<stack>-<stamp>/crops`, or with -2, -3, ... appended when a run of the same
    second already has the name. `mkdir` is the claim on a name, so two runs cannot share one."""
    RUNS.mkdir(parents=True, exist_ok=True)
    attempt = 1
    while True:
        run_dir = RUNS / (f"{stack}-{stamp}" if attempt == 1 else f"{stack}-{stamp}-{attempt}")
        try:
            run_dir.mkdir()
        except FileExistsError:
            attempt += 1
            continue
        (run_dir / "crops").mkdir()
        return run_dir


def _legacy(args: argparse.Namespace) -> int:
    run_dir = _make_run_dir("legacy", time.strftime("%Y%m%d-%H%M%S"))
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


def _shown(path: Path) -> str:
    return str(path.relative_to(HERE)) if path.is_relative_to(HERE) else str(path)


def _next(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"error: {pdf} is not a file", file=sys.stderr)
        return 2
    try:
        origin = next_stack.check_origin(args.base_url)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    run_dir = _make_run_dir("next", time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
    sampler = MemorySampler(args.docker_project) if args.docker_project else None
    with next_stack.new_client(origin) as client:
        result = next_stack.run(
            client, pdf, run_dir=run_dir, poll=args.poll, timeout=args.timeout, sampler=sampler
        )
    predictions = []
    if result.job is not None and result.job.status == "done":
        predictions = next_stack.to_predictions(result.job)
        write_predictions(run_dir / "predictions.csv", predictions)
    meta = next_stack.build_meta(result, predictions, pdf=pdf, poll=args.poll, timeout=args.timeout)
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2) + "\n")
    job = result.job
    if result.failure is not None or job is None:
        failure = result.failure or next_stack.Failure("bad_response", "no job record")
        print(f"{_shown(run_dir)}: FAILED ({failure.kind}): {failure.message}", file=sys.stderr)
        return 1
    print(
        f"{_shown(run_dir)}: {job.pages_processed} pages, {job.structures_found} structures found, "
        f"{len(job.results)} returned, {result.seconds['total']:.1f} s {result.seconds}"
    )
    if result.memory is not None:
        print(f"memory: {result.memory}")
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


def _non_negative(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive(text: str) -> float:
    value = _non_negative(text)
    if value == 0:
        raise argparse.ArgumentTypeError("must be above zero")
    return value


def _project(text: str) -> str:
    if PROJECT_PATTERN.fullmatch(text) is None:
        raise argparse.ArgumentTypeError("not a Compose project name (lower case, digits, - _)")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="decimer-golden")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("legacy", help="run the paper through the legacy site")
    p.add_argument("--base-url", default="http://127.0.0.1:8080")
    p.set_defaults(func=_legacy)
    p = sub.add_parser("next", help="run the paper through the new stack's HTTP API")
    p.add_argument("--base-url", default="http://127.0.0.1:8090")
    p.add_argument("--pdf", default=str(PAPER), help="default: the golden-set paper")
    p.add_argument("--poll", type=_non_negative, default=1.0, help="seconds between polls")
    p.add_argument(
        "--timeout", type=_positive, default=900.0, help="seconds for the upload and the wait"
    )
    p.add_argument(
        "--docker-project",
        type=_project,
        help="Compose project whose containers' peak memory is sampled every 2 s",
    )
    p.set_defaults(func=_next)
    for name, func in (("score", _score), ("draft-labels", _draft_labels)):
        p = sub.add_parser(name)
        p.add_argument("predictions")
        p.set_defaults(func=func)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
