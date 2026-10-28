"""The gate against RDKit: wherever the gate admits a molfile, RDKit reads exactly the atoms and
bonds the gate counted, or none at all (it refuses the file). Seeded, so every run is the same
5000 molfiles (`molfile_fuzz.generate`): counts lines that lie, versions that are not quite right,
continued lines, misplaced blocks, property lines, query atoms, odd coordinates, many fragments,
multi-byte characters, line endings."""

from __future__ import annotations

import collections

import pytest
from rdkit import Chem, RDLogger

from decimer_api import molfile as gate
from tests import molfiles, old_gate
from tests.molfile_fuzz import generate

SEEDS = range(5000)


def rdkit_counts(text: str) -> tuple[int, int] | None:
    """What RDKit reads, the way `analyse_molfile` asks it to; None when it will not."""
    RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]
    try:
        mol = Chem.MolFromMolBlock(text, sanitize=False, removeHs=False, strictParsing=False)
    except Exception:
        return None
    return None if mol is None else (mol.GetNumAtoms(), mol.GetNumBonds())


def rdkit_graph(text: str) -> tuple[int, list[tuple[int, int]]] | None:
    """The atoms and which atoms the bonds join (by position), as RDKit builds them."""
    RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]
    try:
        mol = Chem.MolFromMolBlock(text, sanitize=False, removeHs=False, strictParsing=False)
    except Exception:
        return None
    if mol is None:
        return None
    bonds = [
        tuple(sorted((bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()))) for bond in mol.GetBonds()
    ]
    return mol.GetNumAtoms(), sorted(bonds)  # type: ignore[arg-type]


def gate_graph(cleaned: str) -> tuple[int, list[tuple[int, int]]]:
    """The same, as the gate reads it: atom numbers mapped to their positions."""
    atoms, bonds = gate.structure_graph(cleaned)
    position = {number: index for index, number in enumerate(atoms)}
    pairs = [tuple(sorted((position[a], position[b]))) for a, b in bonds]
    return len(atoms), sorted(pairs)  # type: ignore[arg-type]


def disagreements(
    clean: object, counts: object, seeds: range = SEEDS, *, graph: bool = False
) -> tuple[collections.Counter[str], list[tuple[int, object, object]]]:
    tally: collections.Counter[str] = collections.Counter()
    found = []
    for seed in seeds:
        try:
            cleaned = clean(generate(seed))  # type: ignore[operator]
        except (gate.MolfileError, ValueError, IndexError):
            tally["refused"] += 1
            continue
        tally["admitted"] += 1
        seen: object = rdkit_graph(cleaned) if graph else rdkit_counts(cleaned)
        if seen is None:
            tally["rdkit refused"] += 1
            continue
        tally["rdkit read"] += 1
        expected = gate_graph(cleaned) if graph else counts(cleaned)  # type: ignore[operator]
        if seen != expected:
            found.append((seed, expected, seen))
    return tally, found


def test_wherever_the_gate_admits_a_molfile_rdkit_reads_what_the_gate_counted() -> None:
    tally, found = disagreements(gate.clean_molfile, gate.structure_counts)
    assert found == [], f"gate and RDKit disagree: {found[:5]}"
    # ... and not only as many atoms and bonds: the same bonds, between the same atoms. (Two
    # readers that count alike can still join different atoms: non-ASCII whitespace.)
    _, found = disagreements(gate.clean_molfile, gate.structure_counts, graph=True)
    assert found == [], f"gate and RDKit build different graphs: {found[:3]}"
    # The fuzz is not vacuous: it produces files the gate admits and RDKit reads, and refusals.
    assert tally["admitted"] >= 1000
    assert tally["rdkit read"] >= 500
    assert tally["refused"] >= 1000


def test_the_fuzz_finds_the_bypass_in_the_gate_it_replaced() -> None:
    """The generator is able to find the known bypass: the previous gate takes a V2000
    molfile that says `V2000 V3000` for V3000 and counts the one-atom block RDKit never reads."""
    _, found = disagreements(old_gate.clean_molfile, old_gate.structure_counts)
    assert found, "no disagreement with the old gate: the fuzz has lost its teeth"
    assert any(seen[0] > expected[0] for _, expected, seen in found)  # type: ignore[index]  # MORE


def test_the_old_gates_bypass_is_an_actual_file() -> None:
    text = molfiles.version_confusion(12)
    assert old_gate.structure_counts(old_gate.clean_molfile(text)) == (1, 0)
    assert rdkit_counts(text) == (12, 66)  # what RDKit reads: the whole graph
    with pytest.raises(gate.MolfileError):
        gate.clean_molfile(text)


def test_the_generator_is_seeded() -> None:
    assert generate(7) == generate(7)
    assert len({generate(seed) for seed in range(50)}) > 40
