"""Scores a stack's predictions for the golden-set paper against hand-checked labels.

Matching rule (ROADMAP §5): within the paper, compare the multiset of labelled InChIKeys
with the multiset of predicted InChIKeys. Each prediction can match at most one label.
"""

from __future__ import annotations

import csv
from collections import Counter
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


@dataclass(frozen=True)
class Prediction:
    index: int
    smiles: str
    inchikey: str | None
    segment: str = ""
    classifier: str = ""


@dataclass
class Score:
    labels: int
    correct: int
    extra: int
    invalid: int
    dropped: int
    missing: list[str] = field(default_factory=list)


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
    remaining = Counter(label.inchikey for label in labels)
    correct = extra = invalid = dropped = 0
    for p in predictions:
        if not p.smiles.strip():
            dropped += 1  # legacy fills structures beyond the 20-structure cap with ""
        elif p.inchikey is None:
            invalid += 1
        elif remaining[p.inchikey] > 0:
            remaining[p.inchikey] -= 1
            correct += 1
        else:
            extra += 1
    # Labels still unmatched; for duplicates, the later-listed copies count as missing.
    unmatched = remaining.copy()
    missing = []
    for label in reversed(labels):
        if unmatched[label.inchikey] > 0:
            unmatched[label.inchikey] -= 1
            missing.append(label.number)
    return Score(len(labels), correct, extra, invalid, dropped, missing[::-1])


def read_labels(path: Path) -> list[Label]:
    with path.open(newline="") as f:
        return [
            Label(
                number=row["number"],
                page=int(row["page"]),
                inchikey=row["inchikey"],
                smiles=row["smiles"],
                checked=row["checked"].strip().lower() == "yes",
            )
            for row in csv.DictReader(f)
        ]


PREDICTION_FIELDS = ["index", "segment", "smiles", "inchikey", "classifier"]


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
            )
            for row in csv.DictReader(f)
        ]
