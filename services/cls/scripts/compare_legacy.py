"""Print, for the 20 crops the earlier Laravel version of decimer.ai classified, its flag, this
service's flag and the raw score.

Also calls the library exactly as the earlier version does (`get_classifier_score(path)`, from an
empty temporary working directory because the library writes caffeine_mod.png there) and shows
whether the score this service computes is the same number. Prints load time, per-crop time and
process memory.

    TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 uv run python scripts/compare_legacy.py
"""

from __future__ import annotations

import csv
import os
import tempfile
import time
from pathlib import Path

from decimer_cls.model import THRESHOLD, ClsModel

REPO = Path(__file__).resolve().parents[3]
RUN = REPO / "golden" / "runs" / "legacy-20261022-140954"
COLUMNS = "{:<44}{:>7}{:>8}{:>13}{:>10}{:>13}{:>6}"


def memory() -> str:
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS", "VmHWM")):
            name, value = line.split(":")
            fields[name] = value.strip()
    return ", ".join(f"{k} {v}" for k, v in fields.items())


def main() -> None:
    with (RUN / "predictions.csv").open(newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["classifier"] != ""]

    start = time.perf_counter()
    model = ClsModel()
    model.load()
    print(f"load: {time.perf_counter() - start:.2f} s   [{memory()}]")

    from decimer_image_classifier import DecimerImageClassifier

    # a second copy, only to call it the way the earlier version does
    library = DecimerImageClassifier()

    print(f"threshold {THRESHOLD}: structure when score <= threshold\n")
    print(COLUMNS.format("crop", "legacy", "service", "score", "x thr", "lib score", "ms"))
    disagreements = score_mismatches = 0
    timings = []
    legacy_cwd = Path.cwd()
    for row in rows:
        path = RUN / row["segment"]
        start = time.perf_counter()
        result = model.infer(path.read_bytes(), {})
        elapsed = (time.perf_counter() - start) * 1000
        timings.append(elapsed)
        with tempfile.TemporaryDirectory() as scratch:
            os.chdir(scratch)
            try:
                lib_score = float(library.get_classifier_score(str(path)))
            finally:
                os.chdir(legacy_cwd)
        legacy = row["classifier"] == "True"
        disagreements += legacy != result["is_structure"]
        print(
            COLUMNS.format(
                path.name,
                str(legacy),
                str(result["is_structure"]),
                f"{result['score']:.4g}",
                f"{result['score'] / THRESHOLD:.2g}",
                f"{lib_score:.4g}",
                f"{elapsed:.0f}",
            )
        )
        score_mismatches += lib_score != result["score"]
    print(f"\n{len(rows)} crops, {disagreements} disagreements with legacy")
    print(f"{score_mismatches} crops where the service's score differs from the library's own")
    print(
        f"first crop {timings[0]:.0f} ms (graph tracing); the rest median "
        f"{sorted(timings[1:])[len(timings[1:]) // 2]:.0f} ms, max {max(timings[1:]):.0f} ms"
    )
    print(f"memory at end: {memory()}")


if __name__ == "__main__":
    main()
