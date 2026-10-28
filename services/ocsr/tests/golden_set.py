"""The golden-set paper as data: the crops and predictions of the earlier Laravel version of
decimer.ai ("legacy" below), and the hand-checked labels.

Used by the slow tests and by `scripts/compare_golden.py`. Needs RDKit (a dev dependency): the
service itself never turns SMILES into InChIKeys, the worker does.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")  # an invalid SMILES is a result here, not a log line

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "golden"
RUN = GOLDEN / "runs" / "legacy-20261022-140954"
LEGACY_CLASSIFIED = 20  # legacy caps a job at 20 structures: rows 0-19 are the ones it processed


def inchikey(smiles: str) -> str | None:
    """Full InChIKey with RDKit, as `golden/src/decimer_golden/score.py` computes it."""
    mol = Chem.MolFromSmiles(smiles) if smiles else None
    if mol is None:
        return None
    key: str = Chem.MolToInchiKey(mol)  # type: ignore[no-untyped-call]
    return key or None


@dataclass(frozen=True)
class Crop:
    index: int
    path: Path
    legacy_smiles: str

    @property
    def legacy_inchikey(self) -> str | None:
        return inchikey(self.legacy_smiles)


@dataclass(frozen=True)
class Label:
    number: str
    inchikey: str
    checked: bool
    legacy_segment: int
    note: str


def crops(limit: int = LEGACY_CLASSIFIED) -> list[Crop]:
    """The first `limit` crops of the legacy run (all 32 with `limit=32`)."""
    with (RUN / "predictions.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))[:limit]
    return [Crop(int(r["index"]), RUN / r["segment"], r["smiles"]) for r in rows]


def labels() -> list[Label]:
    with (GOLDEN / "labels.csv").open(newline="") as handle:
        return [
            Label(
                r["number"],
                r["inchikey"],
                r["checked"] == "yes",
                int(r["legacy_segment"]),
                r["note"],
            )
            for r in csv.DictReader(handle)
        ]


def labels_on_the_first_crops(limit: int = LEGACY_CLASSIFIED) -> dict[int, Label]:
    """crop index -> its label, for the first `limit` crops. One label per crop."""
    found: dict[int, Label] = {}
    for label in labels():
        if label.legacy_segment < limit:
            assert label.legacy_segment not in found, label
            found[label.legacy_segment] = label
    return found
