"""Print, for the 20 crops the earlier Laravel version of decimer.ai processed, the label and the
verdicts of that version ("legacy" below) and this service.

For each crop: whether legacy's SMILES and this service's SMILES give the labelled InChIKey (only
crops that have a label in golden/labels.csv can be judged), the mean confidence, the time, and
the SMILES. Ends with totals, load time, per-crop time and process memory.

    TF_NUM_INTRAOP_THREADS=2 TF_NUM_INTEROP_THREADS=1 nice uv run python scripts/compare_golden.py
    ... scripts/compare_golden.py --all    # all 32 crops, against all 26 labels

Legacy processed only the first 20 crops (it caps a job at 20 structures); with --all the other 12
crops are read by this service only, and their rows show "no legacy" instead of a verdict.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from golden_set import crops, inchikey, labels_on_the_first_crops  # noqa: E402

from decimer_ocsr.model import OcsrModel  # noqa: E402

COLUMNS = "{:>4} {:>9} {:>8} {:>8} {:>6} {:>5} {:>5}  {}"


def memory() -> str:
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS", "VmHWM")):
            name, value = line.split(":")
            fields[name] = value.strip()
    return ", ".join(f"{k} {v}" for k, v in fields.items())


def main() -> None:
    limit = 32 if "--all" in sys.argv[1:] else 20
    labels = labels_on_the_first_crops(limit)
    start = time.perf_counter()
    model = OcsrModel()
    model.load()
    print(f"load: {time.perf_counter() - start:.1f} s   [{memory()}]")

    start = time.perf_counter()
    model.selftest()
    print(f"selftest (first inference, traces the graph): {time.perf_counter() - start:.2f} s\n")

    print(
        COLUMNS.format(
            "crop", "label", "legacy", "new", "mean", "sec", "= leg", "new SMILES or error"
        )
    )
    tally = {"legacy": 0, "new": 0, "both": 0, "only new": 0, "only legacy": 0, "neither": 0}
    judged = checked_ok = checked = 0
    timings = []
    same_as_legacy = refused = cut_off = new_all = 0
    for crop in crops(limit):
        begin = time.perf_counter()
        try:
            result = model.infer(crop.path.read_bytes(), {})
            smiles, mean, error = result["smiles"], f"{result['mean_confidence']:.3f}", ""
            if result["incomplete"]:  # stopped at the token limit: returned, flagged
                cut_off += 1
                error = "[incomplete] "
        except ValueError as exc:  # what the service answers with a 422
            smiles, mean, error = "", "-", f"422 {exc}"
            refused += 1
        elapsed = time.perf_counter() - begin
        timings.append(elapsed)
        label = labels.get(crop.index)
        new_key = inchikey(smiles)
        shown, old_v, new_v = "-", "no label", "no label"
        if label is not None:
            judged += 1
            shown = label.number + (" *" if label.checked else "")
            old = crop.index < 20 and crop.legacy_inchikey == label.inchikey
            new = new_key == label.inchikey
            old_v = "no legacy" if crop.index >= 20 else "correct" if old else "WRONG"
            new_v = "correct" if new else "WRONG"
            new_all += new
            if crop.index < 20:  # the window legacy processed: judged side by side
                tally["legacy"] += old
                tally["new"] += new
                tally[
                    "both"
                    if old and new
                    else "only new"
                    if new
                    else "only legacy"
                    if old
                    else "neither"
                ] += 1
            if label.checked:
                checked += 1
                checked_ok += new
        same = bool(smiles) and smiles == crop.legacy_smiles
        same_as_legacy += same
        print(
            COLUMNS.format(
                crop.index,
                shown,
                old_v,
                new_v,
                mean,
                f"{elapsed:.1f}",
                "yes" if same else "no",
                (error + smiles if smiles else error)[:60],
            )
        )
    print(f"\n{judged} of the {limit} crops have a label (* = checked=yes)")
    window = sum(1 for crop in crops(20) if crop.index in labels)
    print(f"first 20 crops: correct legacy {tally['legacy']}, new {tally['new']}  (of {window})")
    if limit > 20:
        print(f"all {limit} crops: new correct {new_all} of {judged} labels")
    print(
        f"per crop: both {tally['both']}, only new {tally['only new']}, "
        f"only legacy {tally['only legacy']}, neither {tally['neither']}"
    )
    print(f"checked labels: {checked_ok} of {checked} correct")
    print(
        f"identical SMILES string to legacy's: {same_as_legacy} of {min(limit, 20)} crops "
        f"({refused} crops refused with a 422, {cut_off} cut off at the token limit)"
    )
    ordered = sorted(timings)
    print(
        f"time per crop: median {ordered[len(ordered) // 2]:.2f} s, min {ordered[0]:.2f} s, "
        f"max {ordered[-1]:.2f} s, total {sum(timings):.1f} s   [{memory()}]"
    )


if __name__ == "__main__":
    main()
