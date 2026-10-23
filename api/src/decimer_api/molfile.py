"""What a client's molfile must be before RDKit reads it, judged from its text alone.

RDKit's cost for a molfile is not bounded by its size: 44 dummy atoms joined each to each are 15 KB
and take 5.7 s and 484 MB to parse, 60 take 46 s and 2.6 GB, and RDKit holds the GIL meanwhile. So
`clean_molfile` counts the structure itself, from the text, and refuses what is too big or too
tangled (`MAX_*` below; a structure DECIMER reads has a few dozen atoms, and fullerene C60 has 60
atoms and a ring rank of 31). This module imports nothing chemical.

**The gate is a copy of a parser, so it must agree with the parser.** What it counts has to be what
RDKit would read: when the gate says "12 atoms" and RDKit sees 44, the limits mean nothing (this was
found: a counts line ending `V2000 V3000` made the gate read a tiny V3000 block that RDKit never
looked at). Hence the rules are strict where RDKit is lax, and a molfile that is ambiguous is
refused rather than guessed at:

* everything from the counts line on is ASCII (RDKit reads columns by byte, and Python splits at
  Unicode spaces that RDKit does not: `k 1<NBSP>p q r` is the bond (q, r) to one and (p, q) to
  the other), and fields are split at ASCII spaces only (a tab is refused);
* the version is the counts line's field at columns 35-39 (`line[34:39]`), as RDKit reads it: blank
  or missing is V2000, `V2000` and `V3000` are themselves, anything else is refused. (RDKit reads
  any other text there as V2000 and ignores the rest of the line; a `V3000` anywhere else on the
  line does not count.)
* a V2000 file has no `M  V30` line, and a V3000 file has nothing but `M  V30` lines after its
  counts line (no V2000 atom or bond block);
* numbers are ASCII digits (Python's `int` also takes `+3`, `1_0` and non-ASCII digits, which
  another parser may read differently);
* a V3000 file has exactly one connection table: BEGIN CTAB, COUNTS, at most one ATOM and one BOND
  block, SGROUP, COLLECTION and OBJ3D blocks (not nested), END CTAB; a continued line ends with
  ` -` and its continuation does not start with a space.

`tests/test_molfile_fuzz.py` generates adversarial molfiles and checks every one the gate admits
against RDKit's own atom and bond counts.
"""

from __future__ import annotations

import re

# Control characters other than tab, line feed and carriage return: never in a molfile.
MOLFILE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Only these break a line. `str.splitlines` also breaks at \x85, U+2028 and others.
LINE_BREAK = re.compile(r"\r\n|\r|\n")
MOLFILE_END = "M  END"
SDF_RECORD_END = "$$$$"

MAX_ATOMS = 300
MAX_BONDS = 350
MAX_RING_RANK = 40  # bonds - atoms + connected pieces
MAX_NEIGHBOURS = 8  # bonds at one atom (no element in a molecule from a drawing has more)

V3000_PREFIX = "M  V30 "
# Blocks of a V3000 connection table that may be present besides ATOM and BOND; their lines are
# not read (they carry no atom or bond).
OTHER_BLOCKS = ("SGROUP", "COLLECTION", "OBJ3D")
V2000_FIELD = re.compile(r" *[0-9]+ *")
NUMBER = re.compile(r"[0-9]+")


class MolfileError(ValueError):
    """The text is not a molfile (or not one that may be stored, written to a file or read)."""


class TooComplex(MolfileError):
    """A molfile that passed the text checks but cost too much to read: it took too long or too
    much memory in the process that reads molfiles, which was stopped."""


def clean_molfile(text: str, *, bounded: bool = True) -> str:
    """The molfile as it may be stored and written: line breaks as `\\n`, ending at its own
    `M  END` line with one final line break. What follows `M  END` is dropped. Raises
    `MolfileError` for a text that cannot be one: control characters, text that is not valid
    Unicode (a lone surrogate cannot be written to a file), no `M  END` line, a `$$$$` line
    before it (that would end a record of an SDF early and let the sender write the next one), or
    one that is too big, too tangled or too ambiguous to read (`bounded`, see the module doc).
    `bounded=False` is for a molfile this service wrote: it is only written to a file, never
    read by RDKit."""
    if MOLFILE_CONTROL.search(text):
        raise MolfileError("it contains control characters")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise MolfileError("it is not valid text") from error
    lines = LINE_BREAK.split(text)
    for number, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(SDF_RECORD_END):
            raise MolfileError(f"line {number + 1} is an SDF record separator")
        if line.rstrip() == MOLFILE_END:
            if bounded:
                check_cost(lines[:number])
            return "\n".join([*lines[:number], MOLFILE_END]) + "\n"
    raise MolfileError("it has no M  END line")


def structure_counts(cleaned: str) -> tuple[int, int]:
    """The atoms and bonds the gate counts in a molfile that `clean_molfile` returned."""
    return check_cost(cleaned.split("\n")[:-2])  # not the M  END line and what follows it


def structure_graph(cleaned: str) -> tuple[list[int], list[tuple[int, int]]]:
    """The graph the gate sees in a molfile that `clean_molfile` returned: its atom numbers in
    order, and its bonds as pairs of atom numbers (what `tests/test_molfile_fuzz.py` compares with
    the graph RDKit builds: the counts alone would not show two readers joining different atoms)."""
    lines = cleaned.split("\n")[:-2]
    if _version(lines[3]) == "V3000":
        return _graph_v3000(lines)
    return _graph_v2000(lines)


def _too_big(what: str, found: int, limit: int) -> MolfileError:
    return MolfileError(f"it is too large to read: {found} {what}, at most {limit}")


def check_cost(lines: list[str]) -> tuple[int, int]:
    """Refuse a molfile that costs too much to read, or that cannot be read the same way twice:
    the atoms and bonds it declares, checked against the lines that are there, and what they make
    of the graph (ring rank, the most bonds at one atom). `lines` are the lines before `M  END`.
    Nothing here calls RDKit. Returns the numbers of atoms and bonds RDKit will read. Raises
    `MolfileError`."""
    if len(lines) < 4:
        raise MolfileError("it has no counts line")
    if not all(line.isascii() for line in lines[3:]):
        # From the counts line to M  END, ASCII only. RDKit reads columns by byte, where the gate
        # reads characters; and Python splits at Unicode spaces (U+00A0, U+2003, U+3000, U+0085,
        # ...) where RDKit does not, so `k 1<NBSP>p q r` is the bond (q, r) to RDKit and (p, q) to
        # `str.split`. Only the three title lines may hold other text.
        raise MolfileError("its connection table has characters that are not ASCII")
    version = _version(lines[3])
    if version == "V3000":
        atoms, bonds = _graph_v3000(lines)
    else:
        atoms, bonds = _graph_v2000(lines)
    if len(atoms) > MAX_ATOMS:
        raise _too_big("atoms", len(atoms), MAX_ATOMS)
    if len(bonds) > MAX_BONDS:
        raise _too_big("bonds", len(bonds), MAX_BONDS)
    parent = {atom: atom for atom in atoms}

    def find(atom: int) -> int:
        while parent[atom] != atom:
            parent[atom] = parent[parent[atom]]
            atom = parent[atom]
        return atom

    degree = dict.fromkeys(atoms, 0)
    pieces = len(atoms)
    for a, b in bonds:
        if a not in parent or b not in parent or a == b:
            raise MolfileError("a bond joins atoms that are not there")
        degree[a] += 1
        degree[b] += 1
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b
            pieces -= 1
    if degree and max(degree.values()) > MAX_NEIGHBOURS:
        raise _too_big("bonds at one atom", max(degree.values()), MAX_NEIGHBOURS)
    if len(bonds) - len(atoms) + pieces > MAX_RING_RANK:
        raise _too_big("independent rings", len(bonds) - len(atoms) + pieces, MAX_RING_RANK)
    return len(atoms), len(bonds)


def _version(counts_line: str) -> str:
    """`V2000` or `V3000`, from the counts line's version field, columns 35-39."""
    field = counts_line[34:39]
    if field.strip() == "":
        return "V2000"  # no version field: RDKit reads V2000
    if field in ("V2000", "V3000"):
        return field
    raise MolfileError("its counts line names a version that this service does not read")


def _tokens(record: str) -> list[str]:
    """The fields of a V3000 record: split at ASCII spaces, and nowhere else."""
    return [token for token in record.split(" ") if token]


def _number(text: str, what: str) -> int:
    if not NUMBER.fullmatch(text):
        raise MolfileError(f"its {what} cannot be read")
    return int(text)


def _field(line: str, start: int, end: int, what: str) -> int:
    """A fixed-width integer field of a V2000 line: ASCII digits and spaces around them."""
    field = line[start:end]
    if not V2000_FIELD.fullmatch(field):
        raise MolfileError(f"its {what} cannot be read")
    return int(field)


def _graph_v2000(lines: list[str]) -> tuple[list[int], list[tuple[int, int]]]:
    """Atom numbers and bonds of a V2000 molfile, from the counts line and the lines it counts."""
    if any(line.startswith("M  V30") for line in lines):
        raise MolfileError("it is a V2000 molfile with V3000 lines in it")
    atom_count = _field(lines[3], 0, 3, "counts line")
    bond_count = _field(lines[3], 3, 6, "counts line")
    if atom_count > MAX_ATOMS:
        raise _too_big("atoms", atom_count, MAX_ATOMS)
    if bond_count > MAX_BONDS:
        raise _too_big("bonds", bond_count, MAX_BONDS)
    first_bond = 4 + atom_count
    if len(lines) < first_bond + bond_count:
        raise MolfileError("it has fewer atom and bond lines than its counts line says")
    bonds = [
        (_field(line, 0, 3, "bond line"), _field(line, 3, 6, "bond line"))
        for line in lines[first_bond : first_bond + bond_count]
    ]
    return list(range(1, atom_count + 1)), bonds


def _records_v3000(lines: list[str]) -> list[str]:
    """The `M  V30` lines after the counts line as records: the prefix taken off, continued lines
    joined. Refuses a V3000 molfile that has anything else there, and a continuation that is not
    in the plain form (the line ends with ` -`, the next one does not start with a space)."""
    if any(line.startswith("M  V30") for line in lines[:3]):
        raise MolfileError("it has V3000 lines where the title lines are")
    records: list[str] = []
    pending: str | None = None
    for line in lines[4:]:
        if not line.startswith(V3000_PREFIX):
            raise MolfileError("it is a V3000 molfile with lines that are not V3000 lines")
        if "\t" in line:
            raise MolfileError("it has a tab in a V3000 line (fields are separated by spaces)")
        content = line[len(V3000_PREFIX) :]
        if pending is not None:
            if content[:1] in ("", " "):
                raise MolfileError("a continued V3000 line is not continued in the plain form")
            content, pending = pending + content, None
        if content.rstrip(" ").endswith("-"):
            if not content.endswith(" -"):
                raise MolfileError("a continued V3000 line is not continued in the plain form")
            pending = content[:-1]
            continue
        records.append(content.strip(" "))
    if pending is not None:
        raise MolfileError("the last V3000 line is continued, and nothing follows it")
    return records


def _graph_v3000(lines: list[str]) -> tuple[list[int], list[tuple[int, int]]]:
    """Atom numbers and bonds of a V3000 molfile: the entries of its one ATOM block and one BOND
    block, checked against its COUNTS line."""
    records = _records_v3000(lines)
    if len(records) < 3 or records[0] != "BEGIN CTAB" or records[-1] != "END CTAB":
        raise MolfileError("it must be one connection table, BEGIN CTAB to END CTAB")
    counts = _tokens(records[1])
    if len(counts) < 3 or counts[0] != "COUNTS":
        raise MolfileError("its COUNTS line must follow BEGIN CTAB")
    atom_count = _number(counts[1], "COUNTS line")
    bond_count = _number(counts[2], "COUNTS line")
    if atom_count > MAX_ATOMS:
        raise _too_big("atoms", atom_count, MAX_ATOMS)
    if bond_count > MAX_BONDS:
        raise _too_big("bonds", bond_count, MAX_BONDS)

    atoms: list[int] = []
    bonds: list[tuple[int, int]] = []
    seen: set[str] = set()
    block = ""
    for record in records[2:-1]:
        tokens = _tokens(record)
        head = tokens[0] if tokens else ""
        if head in ("BEGIN", "END"):
            name = tokens[1] if len(tokens) == 2 else ""
            if head == "BEGIN":
                if block or name not in ("ATOM", "BOND", *OTHER_BLOCKS):
                    raise MolfileError("it has a block that this service does not read")
                if name in ("ATOM", "BOND") and name in seen:
                    raise MolfileError(f"it has more than one {name} block")
                if name == "ATOM" and "BOND" in seen:
                    raise MolfileError("its ATOM block comes after its BOND block")
                seen.add(name)
                block = name
            else:
                if name != block:
                    raise MolfileError("its blocks are not properly nested")
                block = ""
        elif block == "ATOM":
            atoms.append(_number(tokens[0], "atom line"))
            if len(atoms) > MAX_ATOMS:
                raise _too_big("atoms", len(atoms), MAX_ATOMS)
        elif block == "BOND":
            if len(tokens) < 4:
                raise MolfileError("its bond line cannot be read")
            bonds.append((_number(tokens[2], "bond line"), _number(tokens[3], "bond line")))
            if len(bonds) > MAX_BONDS:
                raise _too_big("bonds", len(bonds), MAX_BONDS)
    if block:
        raise MolfileError("a block is not closed")
    if (len(atoms), len(bonds)) != (atom_count, bond_count) or len(set(atoms)) != len(atoms):
        raise MolfileError("its atom and bond lines do not match its COUNTS line")
    return atoms, bonds
