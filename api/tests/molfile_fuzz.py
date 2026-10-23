"""Adversarial molfiles for the gate in `decimer_api.molfile`, generated from a seed.

The gate is a copy of a parser and must agree with the parser: whenever it admits a molfile, RDKit
must read as many atoms and bonds as the gate counted (or refuse to read it). `generate(seed)`
builds one molfile from a small base structure and a few mutations, each a way a reader could be
made to disagree with another: counts lines that lie, version fields that are not quite `V2000` or
`V3000`, lines continued with a trailing space, blocks in the wrong place or twice, property
lines (R groups, isotopes, charges, atom lists, S-groups), query atoms, coordinates that are
NaN, infinite or huge, many fragments, odd line endings and blank lines.

Graphs are kept small (at most 40 atoms) so that a disagreement costs milliseconds to find, not
seconds; the cost of the big ones is the gate's business, tested elsewhere.
"""

from __future__ import annotations

import random
from collections.abc import Callable

Lines = list[str]

UNICODE_SPACES = [
    "\u00a0", "\u0085", "\u1680", "\u2000", "\u2003", "\u200a", "\u2028", "\u2029",
    "\u202f", "\u205f", "\u3000", "\u200b", "\ufeff", "\u00e9", "\u0663",
]  # fmt: skip

SYMBOLS = [
    "C",
    "N",
    "O",
    "S",
    "Cl",
    "A",
    "Q",
    "*",
    "R#",
    "L",
    "H",
    "D",
    "X",
    "M",
    "Xe",
    "NOT [C,N]",
]
COORDS = ["0.0000", "1.5000", "-2.2500", "nan", "NaN", "inf", "-inf", "1e999", "1e9", "-1e-9", "0"]
COUNTS_TAILS = [
    " V2000", " V3000", "", " ", " V2000 V3000", " V3000 V2000", " v2000", " v3000", " V2001",
    " V1000", " V3001", "  V2000", "  V3000", " V300", " V30000", "V2000", "V3000", "\tV2000",
    "\tV3000", " V2000   ", " V3000   ", " V3000 junk", " XXXXX", " V 2000", " V3000V2000",
    "              V3000", "   V3000", " VV3000",
]  # fmt: skip
INT_FORMS: list[Callable[[int, int], str]] = [
    lambda n, w: f"{n:{w}d}",
    lambda n, w: f"{n:0{w}d}",
    lambda n, w: f"{n:<{w}d}",
    lambda n, w: f"+{n}".rjust(w),
    lambda n, w: f"{n}_0".rjust(w) if n else "_".rjust(w),
    lambda n, w: ("٣" * 1).rjust(w) if n == 3 else f"{n:{w}d}",  # an Arabic-Indic digit
    lambda n, w: f"{n}.0".rjust(w),
    lambda n, w: f"{n:x}".rjust(w),
    lambda n, w: " " * w,
    lambda n, w: f"-{n}".rjust(w),
]


def _graph(rng: random.Random) -> tuple[int, list[tuple[int, int]]]:
    atoms = rng.randint(0, 12)
    bonds: set[tuple[int, int]] = set()
    if atoms >= 2:
        for _ in range(rng.randint(0, atoms + 3)):
            a, b = rng.sample(range(1, atoms + 1), 2)
            bonds.add((min(a, b), max(a, b)))
    return atoms, sorted(bonds)


def _v2000(rng: random.Random, atoms: int, bonds: list[tuple[int, int]]) -> Lines:
    lines = ["title", "  generator", ""]
    counts = f"{atoms:3d}{len(bonds):3d}  0  0  0  0  0  0  0  0999"
    lines.append(counts + rng.choice(COUNTS_TAILS[:1] * 6 + COUNTS_TAILS))
    for _ in range(atoms):
        x, y, z = (rng.choice(COORDS) for _ in range(3))
        symbol = rng.choice(SYMBOLS[:4] * 8 + SYMBOLS)
        lines.append(f"{x:>10}{y:>10}{z:>10} {symbol:<3} 0  0  0  0  0  0  0  0  0  0  0  0")
    for a, b in bonds:
        lines.append(f"{a:3d}{b:3d}{rng.choice([1, 1, 1, 2, 3, 4, 8]):3d}  0")
    return lines


def _v3000(rng: random.Random, atoms: int, bonds: list[tuple[int, int]]) -> Lines:
    lines = ["title", "  generator", "", "  0  0  0     0  0            999 V3000"]
    lines += ["M  V30 BEGIN CTAB", f"M  V30 COUNTS {atoms} {len(bonds)} 0 0 0", "M  V30 BEGIN ATOM"]
    for number in range(1, atoms + 1):
        x, y = rng.choice(COORDS), rng.choice(COORDS)
        lines.append(f"M  V30 {number} {rng.choice(SYMBOLS[:4] * 6 + SYMBOLS)} {x} {y} 0 0")
    lines += ["M  V30 END ATOM", "M  V30 BEGIN BOND"]
    for number, (a, b) in enumerate(bonds, start=1):
        lines.append(f"M  V30 {number} {rng.choice([1, 1, 2, 3])} {a} {b}")
    lines += ["M  V30 END BOND", "M  V30 END CTAB"]
    return lines


PROPERTY_LINES = [
    "M  CHG  1   1   1", "M  ISO  1   1  13", "M  RGP  1   1   1", "M  ALS   1  2 F C   N   ",
    "A    1", "V    1 something", "G    1   2", "M  STY  1   1 SUP", "M  SAL   1  2   1   2",
    "M  SBL   1  1   1", "M  SMT   1 Me", "M  RAD  1   1   2", "M  ZZC  1   1", "M  APO  1   1   1",
    "M  AAL  1  1  1 A", "M  LIN  1   1   2   3", "M  UNK", "> <data>", "text", "",
]  # fmt: skip
V30_EXTRAS = [
    ["M  V30 BEGIN CTAB", "M  V30 COUNTS 1 0 0 0 0", "M  V30 BEGIN ATOM", "M  V30 1 C 0 0 0 0",
     "M  V30 END ATOM", "M  V30 END CTAB"],
    ["M  V30 BEGIN COLLECTION", "M  V30 MDLV30/STEABS ATOMS=(1 1)", "M  V30 END COLLECTION"],
    ["M  V30 BEGIN SGROUP", "M  V30 1 DAT 0 ATOMS=(1 1) FIELDNAME=x", "M  V30 END SGROUP"],
    ["M  V30 BEGIN ATOM", "M  V30 1 C 0 0 0 0", "M  V30 END ATOM"],
    ["M  V30 BEGIN BOND", "M  V30 1 1 1 2", "M  V30 END BOND"],
    ["M  V30 BEGIN RGROUP", "M  V30 END RGROUP"],
    ["M  V30 END CTAB"],
    ["M  V30 BEGIN OBJ3D", "M  V30 END OBJ3D"],
    ["M  V30 LINKNODE 1 3 2 1 2 1 3"],
    ["M  V30"],
    ["M  V30BEGIN ATOM"],
    ["M  V300 BEGIN ATOM"],
]  # fmt: skip


def _mutate(rng: random.Random, lines: Lines, is_v3000: bool) -> Lines:
    lines = list(lines)
    operation = rng.randrange(23)
    if operation == 0 and len(lines) > 3:  # the counts line lies
        counts = lines[3]
        atoms = int(counts[0:3]) if counts[0:3].strip().isdigit() else 0
        bonds = int(counts[3:6]) if counts[3:6].strip().isdigit() else 0
        atoms, bonds = max(0, atoms + rng.randint(-3, 40)), max(0, bonds + rng.randint(-3, 40))
        lines[3] = f"{atoms:3d}{bonds:3d}" + counts[6:]
    elif operation == 1 and len(lines) > 3:  # the integer fields are written another way
        counts = lines[3]
        form = rng.choice(INT_FORMS)
        lines[3] = form(int(counts[0:3] or 0) if counts[0:3].strip().isdigit() else 0, 3)[:3].ljust(
            3
        )
        lines[3] += form(int(counts[3:6] or 0) if counts[3:6].strip().isdigit() else 0, 3)[
            :3
        ].ljust(3)
        lines[3] += counts[6:]
    elif operation == 2 and len(lines) > 3:  # the version field
        lines[3] = (
            lines[3][:34].ljust(34)
            + rng.choice(COUNTS_TAILS).lstrip(" ")[:5]
            + rng.choice(["", " x"])
        )
    elif operation == 3 and len(lines) > 4:  # a bond or atom line is written another way
        index = rng.randrange(4, len(lines))
        if not is_v3000 and len(lines[index]) >= 6:
            form = rng.choice(INT_FORMS)
            lines[index] = (
                form(int(lines[index][0:3] or 0) if lines[index][0:3].strip().isdigit() else 1, 3)[
                    :3
                ].ljust(3)
                + lines[index][3:]
            )
        elif is_v3000:
            lines[index] = lines[index].replace(" ", rng.choice(["  ", "\t", " "]), 1)
    elif operation == 4:  # property lines before M  END
        lines += rng.sample(PROPERTY_LINES, rng.randint(1, 4))
    elif operation == 5:  # a block of the other version, or a stray block
        lines += rng.choice(V30_EXTRAS)
    elif operation == 6 and is_v3000:  # continued lines, plain and not
        candidates = [i for i, line in enumerate(lines) if line.startswith("M  V30 ") and i > 4]
        if len(candidates) > 1:
            i = rng.choice(candidates[:-1])
            ending = rng.choice([" -", "-", " - ", "- ", "  -", "\t-", " -  "])
            lines[i] = lines[i] + ending
            if rng.random() < 0.5:
                lines[i + 1] = lines[i + 1].replace(
                    "M  V30 ", rng.choice(["M  V30  ", "M  V30 \t", "M  V30 "]), 1
                )
    elif operation == 7:  # V2000 atom and bond blocks inside a V3000 file, or the other way round
        extra = lines[4:] if not is_v3000 else []
        if not is_v3000 and extra:
            lines = lines[:4] + extra + extra[:3]
        elif is_v3000:
            lines.insert(4, "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0")
    elif operation == 8:  # many fragments: a lot of separate atoms
        if not is_v3000:
            count = rng.randint(20, 40)
            lines = (
                lines[:3]
                + [f"{count:3d}  0  0  0  0  0  0  0  0  0999 V2000"]
                + ["    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0"] * count
            )
    elif operation == 9:  # blank lines and padding
        lines.insert(rng.randrange(0, len(lines) + 1), rng.choice(["", " ", "   "]))
    elif operation == 10:  # the title lines
        lines[rng.randrange(0, min(3, len(lines)))] = rng.choice(
            [
                "M  V30 BEGIN CTAB",
                "M  V30 ",
                "V3000",
                "  1  0  0  0  0  0  0  0  0  0999 V3000",
                "M  END x",
            ]
        )
    elif operation == 11 and lines:  # trailing spaces on a line
        i = rng.randrange(len(lines))
        lines[i] += rng.choice([" ", "   ", "\t"])
    elif operation == 12 and is_v3000:  # a duplicate or renumbered atom
        for i, line in enumerate(lines):
            if line.startswith("M  V30 2 "):
                lines[i] = line.replace(
                    "M  V30 2 ",
                    rng.choice(
                        ["M  V30 1 ", "M  V30 0 ", "M  V30 07 ", "M  V30 +2 ", "M  V30 2_0 "]
                    ),
                    1,
                )
                break
    elif operation in (14, 15):  # the version confusion: one version declared, the other's body
        both = [" V2000 V3000", " V3000 V2000", " V2000", " V3000", " V3000  V2000"]
        counts = lines[3] if len(lines) > 3 else ""
        if not is_v3000 and len(lines) > 3:
            lines[3] = counts[:34].ljust(34) + rng.choice(both)
            lines += rng.choice(V30_EXTRAS[:3])
        elif is_v3000 and len(lines) > 3:
            lines[3] = "  3  2  0  0  0  0  0  0  0  0999" + rng.choice(both)
            lines.insert(4, "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0")
    elif operation in (16, 17) and len(lines) > 3:  # a character of two bytes: RDKit counts bytes
        index = rng.choice([3, 3, 3, rng.randrange(len(lines))])
        position = rng.choice([0, 1, 2, 3, 5, 6, 10, 30, 33, 34, 35, 38, 39, 40, len(lines[index])])
        char = rng.choice(["\u00e9", "\u00df", "\u4e2d", "\u2022", "\U0001f600"])
        lines[index] = lines[index][:position] + char + lines[index][position:]
        if rng.random() < 0.5 and index == 3:  # and the version field is written to line up
            lines[3] = lines[3].replace(" V3000", "V3000").replace(" V2000", "V2000")
    elif operation == 18:  # lines that look a little like the end of the molfile
        lines.insert(
            rng.randrange(4, len(lines) + 1),
            rng.choice(
                ["M  END junk", " M  END", "M  END\t", "M  ENDX", "M END", "m  end", "M  END  "]
            ),
        )
    elif operation in (19, 20) and len(lines) > 3:  # Unicode whitespace where a space is
        index = rng.randrange(3, len(lines))
        space = rng.choice(UNICODE_SPACES)
        line = lines[index]
        spots = [i for i, char in enumerate(line) if char == " "]
        if spots and rng.random() < 0.8:
            spot = rng.choice(spots)
            lines[index] = line[:spot] + space + line[spot + 1 :]
        else:  # next to a token, or inside a number
            spot = rng.randrange(len(line) + 1)
            lines[index] = line[:spot] + space + line[spot:]
    elif operation in (21, 22) and is_v3000:  # a bond line with one field more, joined by a space
        bond_lines = [i for i, line in enumerate(lines) if len(line.split()) == 6 and i > 4]
        if bond_lines:
            index = rng.choice(bond_lines)
            _, _, number, kind, a, b = lines[index].split()
            space = rng.choice(UNICODE_SPACES[:11] + ["\t", " "])
            extra = rng.randint(1, 20)
            lines[index] = f"M  V30 {number} {kind}{space}{extra} {a} {b}"
    elif operation == 13:  # a second M  END and text after it
        lines += ["M  END"] + rng.choice(
            [["junk"], ["  1  0  0  0  0  0  0  0  0  0999 V2000"], []]
        )
    return lines


def generate(seed: int) -> str:
    rng = random.Random(seed)  # noqa: S311  (a test generator, nothing secret)
    atoms, bonds = _graph(rng)
    is_v3000 = rng.random() < 0.45
    lines = (_v3000 if is_v3000 else _v2000)(rng, atoms, bonds)
    for _ in range(rng.choice([0, 1, 1, 2, 3])):
        lines = _mutate(rng, lines, is_v3000)
    ending = rng.choice(["\n"] * 6 + ["\r\n", "\r"])
    return ending.join([*lines, "M  END"]) + ending
