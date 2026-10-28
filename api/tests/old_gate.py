"""The gate as it was before its bypass was found, kept only so that a test can prove the
fuzz in `molfile_fuzz` is able to find that bypass. It took a molfile for V3000 when `V3000` was
anywhere on the counts line (RDKit reads columns 35-39 only), accepted any text `int()` accepts, and
ignored `M  V30` lines in a V2000 file. Do not use it for anything else."""

from __future__ import annotations

from decimer_api.molfile import (
    LINE_BREAK,
    MAX_ATOMS,
    MAX_BONDS,
    MOLFILE_END,
    MolfileError,
)


def clean_molfile(text: str) -> str:
    lines = LINE_BREAK.split(text)
    for number, line in enumerate(lines):
        if line.rstrip() == MOLFILE_END:
            check_cost(lines[:number])
            return "\n".join([*lines[:number], MOLFILE_END]) + "\n"
    raise MolfileError("it has no M  END line")


def structure_counts(cleaned: str) -> tuple[int, int]:
    return check_cost(cleaned.split("\n")[:-2])


def check_cost(lines: list[str]) -> tuple[int, int]:
    if len(lines) < 4:
        raise MolfileError("it has no counts line")
    declared_v3000 = "V3000" in lines[3]  # the bypass: anywhere on the line
    if declared_v3000 != any(line.startswith("M  V30") for line in lines):
        raise MolfileError("its version line and its body disagree")
    atoms, bonds = (_v3000 if declared_v3000 else _v2000)(lines)
    if atoms > MAX_ATOMS or bonds > MAX_BONDS:
        raise MolfileError("too large")
    return atoms, bonds


def _v2000(lines: list[str]) -> tuple[int, int]:
    try:
        atoms, bonds = int(lines[3][0:3]), int(lines[3][3:6])
    except ValueError as error:
        raise MolfileError("its counts line cannot be read") from error
    if atoms < 0 or bonds < 0 or len(lines) < 4 + atoms + bonds:
        raise MolfileError("fewer lines than the counts line says")
    return atoms, bonds


def _v3000(lines: list[str]) -> tuple[int, int]:
    records: list[str] = []
    pending = ""
    for line in lines:
        if not line.startswith("M  V30"):
            continue
        body = pending + line[6:].strip()
        pending = ""
        if body.endswith("-"):
            pending = body[:-1]
            continue
        records.append(body)
    counts = [record.split() for record in records if record.startswith("COUNTS ")]
    if len(counts) != 1 or len(counts[0]) < 3:
        raise MolfileError("it must have exactly one COUNTS line")
    try:
        return int(counts[0][1]), int(counts[0][2])
    except ValueError as error:
        raise MolfileError("its COUNTS line cannot be read") from error
