"""Scores a stack's predictions for the golden-set paper against hand-checked labels.

Matching rule (ROADMAP §5): within the paper, compare the multiset of labelled InChIKeys
with the multiset of predicted InChIKeys. Each prediction can match at most one label.

A label with `scored=no` in labels.csv (the two Figure 3 drawings with correlation arrows over
the bonds, excluded 2026-10-23) is not counted: a prediction first matches a scored label, and
only when none is left may it use up an excluded one, which counts as neither correct nor extra.

What a prediction without a usable structure counts as (ROADMAP §5: "Invalid: SMILES that RDKit
rejects"; decided by Kohulan 2026-10-23, gate P2-08 passes as §5 words it):
- invalid: a non-empty SMILES that RDKit rejects;
- refused: an empty SMILES that the stack returned (`valid` is True or False, not None): it
  tried and its recognition declined to answer, e.g. the new stack's 422 "no complete structure".
  It is reported apart from invalid, so it never counts against the pass rule's invalid limit;
- cut: an empty SMILES with `valid` None, the padding rows for structures beyond the 20-structure
  cap (legacy writes its cut rows the same way).
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]  # missing from RDKit stubs


@dataclass(frozen=True)
class Label:
    number: str
    page: int
    inchikey: str
    smiles: str
    checked: bool
    scored: bool = True


@dataclass(frozen=True)
class Prediction:
    """One row of a run's predictions.csv. The first five fields are what the legacy runner
    writes; the rest are filled by stacks that report them (`next`) and stay empty for legacy."""

    index: int
    smiles: str
    inchikey: str | None
    segment: str = ""
    classifier: str = ""
    page: int | None = None  # 1-based, like labels.csv
    valid: bool | None = None  # the stack's own verdict on the SMILES; None: not reported
    confidence: float | None = None
    name: str = ""
    name_tier: str = ""


@dataclass
class Score:
    labels: int
    correct: int
    extra: int
    invalid: int  # a non-empty SMILES that RDKit rejects
    dropped: int  # cut by the 20-structure cap (padding rows: empty SMILES, valid None)
    missing: list[str] = field(default_factory=list)
    excluded: int = 0  # predictions that used up an excluded label (neither correct nor extra)
    refused: int = 0  # empty SMILES the stack returned: its recognition declined, not invalid


def to_inchikey(smiles: str) -> str | None:
    """Full InChIKey from SMILES with RDKit, or None if RDKit rejects it."""
    if not smiles.strip():
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    key: str = Chem.MolToInchiKey(mol)  # type: ignore[no-untyped-call]
    return key or None


def score(labels: list[Label], predictions: list[Prediction]) -> Score:
    counted = [label for label in labels if label.scored]
    remaining = Counter(label.inchikey for label in counted)
    spare = Counter(label.inchikey for label in labels if not label.scored)
    correct = extra = invalid = dropped = excluded = refused = 0
    for p in predictions:
        if not p.smiles.strip():
            if p.valid is not None:
                refused += 1  # a result the stack returned (valid either way) with no SMILES
            else:
                dropped += 1  # padding beyond the 20-structure cap (valid=None; legacy: "")
        elif p.inchikey is None:
            invalid += 1
        elif remaining[p.inchikey] > 0:
            remaining[p.inchikey] -= 1
            correct += 1
        elif spare[p.inchikey] > 0:
            spare[p.inchikey] -= 1
            excluded += 1
        else:
            extra += 1
    # Labels still unmatched; for duplicates, the later-listed copies count as missing.
    unmatched = remaining.copy()
    missing = []
    for label in reversed(counted):
        if unmatched[label.inchikey] > 0:
            unmatched[label.inchikey] -= 1
            missing.append(label.number)
    return Score(len(counted), correct, extra, invalid, dropped, missing[::-1], excluded, refused)


def read_labels(path: Path) -> list[Label]:
    with path.open(newline="") as f:
        return [
            Label(
                number=row["number"],
                page=int(row["page"]),
                inchikey=row["inchikey"],
                smiles=row["smiles"],
                checked=row["checked"].strip().lower() == "yes",
                scored=_parse_scored(row.get("scored")),
            )
            for row in csv.DictReader(f)
        ]


def _parse_scored(value: str | None) -> bool:
    """`yes` or `no`; a labels file without the column (older copies) scores every label."""
    if value is None:
        return True
    text = value.strip().lower()
    if text not in ("yes", "no"):
        raise ValueError(f"labels.csv: scored must be yes or no, not {value!r}")
    return text == "yes"


PREDICTION_FIELDS = [
    "index",
    "segment",
    "smiles",
    "inchikey",
    "classifier",
    "page",
    "valid",
    "confidence",
    "name",
    "name_tier",
]


def write_predictions(path: Path, predictions: list[Prediction]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=PREDICTION_FIELDS)
        w.writeheader()
        for p in predictions:
            w.writerow(
                {
                    "index": p.index,
                    "segment": p.segment,
                    "smiles": p.smiles,
                    "inchikey": p.inchikey or "",
                    "classifier": p.classifier,
                    "page": "" if p.page is None else p.page,
                    "valid": "" if p.valid is None else p.valid,
                    "confidence": "" if p.confidence is None else repr(p.confidence),
                    "name": p.name,
                    "name_tier": p.name_tier,
                }
            )


def read_predictions(path: Path) -> list[Prediction]:
    with path.open(newline="") as f:
        return [
            Prediction(
                index=int(row["index"]),
                smiles=row["smiles"],
                inchikey=row["inchikey"] or None,
                segment=row["segment"],
                classifier=row["classifier"],
                page=_optional(row, "page", int),
                valid=_optional(row, "valid", _parse_valid),
                confidence=_optional(row, "confidence", float),
                name=row.get("name") or "",
                name_tier=row.get("name_tier") or "",
            )
            for row in csv.DictReader(f)
        ]


def _optional[T](row: dict[str, str], column: str, convert: Callable[[str], T]) -> T | None:
    """A column that older runs (the legacy runner's) do not have, or that is left empty."""
    text = row.get(column)
    return convert(text) if text else None


def _parse_valid(text: str) -> bool:
    """The `valid` column as `write_predictions` writes it: exactly "True" or "False"."""
    if text not in ("True", "False"):
        raise ValueError(f"valid must be 'True', 'False' or empty, not {text!r}")
    return text == "True"
