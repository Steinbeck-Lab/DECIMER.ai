"""Molfiles built in code for the tests of the cost gate (the reviewer's probes, P3-06 review):
graphs whose parsing alone costs seconds and gigabytes inside the byte cap."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence


class RdkitWasAsked(BaseException):  # noqa: N818  (not an error to catch: a failed expectation)
    """Raised by a stand-in for RDKit's molfile reader, to prove it was never called. A
    BaseException, so that no `except Exception` on the way can turn it into a refusal."""


Atoms = Sequence[tuple[float, float]]
Bonds = Sequence[tuple[int, int, int]]  # 1-based atom numbers, bond order


def v2000(
    atoms: Atoms,
    bonds: Bonds,
    symbol: Callable[[int], str] = lambda _i: "C",
    *,
    counts_tail: str = " V2000",
    extra: Sequence[str] = (),
) -> str:
    """`counts_tail` is what follows `999` on the counts line; `extra` are lines before M  END."""
    lines = ["probe", "  RDKit          2D", ""]
    lines.append(f"{len(atoms):3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999{counts_tail}")
    for index, (x, y) in enumerate(atoms):
        lines.append(
            f"{x:10.4f}{y:10.4f}{0:10.4f} {symbol(index):<3} 0  0  0  0  0  0  0  0  0  0  0  0"
        )
    for a, b, order in bonds:
        lines.append(f"{a:3d}{b:3d}{order:3d}  0")
    lines.extend(extra)
    lines.append("M  END")
    return "\n".join(lines) + "\n"


def v3000(atoms: Atoms, bonds: Bonds, symbol: Callable[[int], str] = lambda _i: "C") -> str:
    lines = [
        "probe",
        "  RDKit          2D",
        "",
        "  0  0  0     0  0            999 V3000",
        "M  V30 BEGIN CTAB",
        f"M  V30 COUNTS {len(atoms)} {len(bonds)} 0 0 0",
        "M  V30 BEGIN ATOM",
    ]
    for index, (x, y) in enumerate(atoms):
        lines.append(f"M  V30 {index + 1} {symbol(index).replace('R#', '*')} {x:g} {y:g} 0 0")
    lines += ["M  V30 END ATOM", "M  V30 BEGIN BOND"]
    for number, (a, b, order) in enumerate(bonds, start=1):
        lines.append(f"M  V30 {number} {order} {a} {b}")
    lines += ["M  V30 END BOND", "M  V30 END CTAB", "M  END"]
    return "\n".join(lines) + "\n"


def molfile(atoms: Atoms, bonds: Bonds, symbol: Callable[[int], str] = lambda _i: "C") -> str:
    big = len(atoms) > 999 or len(bonds) > 999
    return (v3000 if big else v2000)(atoms, bonds, symbol)


def ring(count: int) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    atoms = [
        (math.cos(2 * math.pi * i / count) * count, math.sin(2 * math.pi * i / count) * count)
        for i in range(count)
    ]
    bonds = [(i + 1, (i + 1) % count + 1, 1) for i in range(count)]
    return atoms, bonds


SMALL_V3000_BLOCK = (
    "M  V30 BEGIN CTAB",
    "M  V30 COUNTS 1 0 0 0 0",
    "M  V30 BEGIN ATOM",
    "M  V30 1 C 0 0 0 0",
    "M  V30 END ATOM",
    "M  V30 END CTAB",
)


def version_confusion(count: int = 44) -> str:
    """The re-review's attack: a counts line that ends `V2000 V3000`, a complete graph of `count`
    R# atoms in V2000 lines, and a one-atom V3000 block after the bonds. RDKit reads the V2000
    graph (the version field is `V2000`); a gate that looks for `V3000` anywhere on the line counts
    the tiny block instead (5.5 s and 669 MB for 44 atoms)."""
    atoms = [(math.cos(i), math.sin(i)) for i in range(count)]
    bonds = [(i + 1, j + 1, 1) for i in range(count) for j in range(i + 1, count)]
    return v2000(atoms, bonds, lambda _i: "R#", counts_tail=" V2000 V3000", extra=SMALL_V3000_BLOCK)


def complete_graph_of_dummies(count: int) -> str:
    """K_n of R# atoms: no valence limit, so RDKit takes it all in (the review's 44 and 60)."""
    atoms = [(math.cos(i), math.sin(i)) for i in range(count)]
    bonds = [(i + 1, j + 1, 1) for i in range(count) for j in range(i + 1, count)]
    return molfile(atoms, bonds, lambda _i: "R#")


def chain(count: int, symbol: str = "C") -> str:
    """A zig-zag chain, the shape of a long alkane: `count` atoms, `count - 1` bonds."""
    atoms = [(i * 0.8, 0.5 * (i % 2)) for i in range(count)]
    bonds = [(i + 1, i + 2, 1) for i in range(count - 1)]
    return molfile(atoms, bonds, lambda _i: symbol)


def drug_like(heavy_atoms: int = 100) -> str:
    """A normal structure of about `heavy_atoms` atoms: a few fused rings and chains (RDKit's own
    molfile of a SMILES built from benzene rings joined by short chains)."""
    from rdkit import Chem
    from rdkit.Chem import rdDepictor

    pieces = []
    while len("".join(pieces).replace("(", "")) < heavy_atoms * 0.8:
        pieces.append("c1ccc(cc1)CC(=O)N")
    smiles = "C".join(pieces)
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    rdDepictor.Compute2DCoords(mol)
    return str(Chem.MolToMolBlock(mol))


UNICODE_SPACES = [
    "\u00a0", "\u0085", "\u1680", "\u2000", "\u2003", "\u200a", "\u2028", "\u2029",
    "\u202f", "\u205f", "\u3000",
]  # fmt: skip


def whitespace_confusion(count: int, space: str) -> str:
    """The third review's attack: the bond lines of a V3000 molfile separate their fields with
    `space`, a Unicode space that Python's `str.split` splits at and RDKit does not. RDKit reads
    `<index> <type><space><p> <q> <r>` as the bond (q, r), and builds the complete graph of
    `count` atoms; a gate that splits at `space` reads (p, q) and sees a forest of small stars."""
    clique = [(q, r) for q in range(1, count + 1) for r in range(q + 1, count + 1)]
    atoms = count + len(clique)
    records = ["BEGIN CTAB", f"COUNTS {atoms} {len(clique)} 0 0 0", "BEGIN ATOM"]
    records += [f"{i} C {math.cos(i):.3f} {math.sin(i):.3f} 0 0" for i in range(1, atoms + 1)]
    records += ["END ATOM", "BEGIN BOND"]
    for number, ((q, r), leaf) in enumerate(
        zip(clique, range(count + 1, atoms + 1), strict=True), start=1
    ):
        records.append(f"{number} 1{space}{leaf} {q} {r}")
    records += ["END BOND", "END CTAB"]
    header = ["probe", "  RDKit          2D", "", "  0  0  0     0  0            999 V3000"]
    return "\n".join([*header, *("M  V30 " + record for record in records), "M  END"]) + "\n"
