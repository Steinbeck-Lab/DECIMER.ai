"""The export files of a job: a ZIP, an SDF, a CSV. Pure functions, no HTTP.

What goes in, for each structure and in the order of the job's results (never the client's):

* **the edited molfile wins.** A molfile the client sends for a result replaces the stored one;
  the SMILES, InChIKey, validity and IUPAC name are then recomputed from it on the server (RDKit,
  and chem for the name), so that a row is never half edited. A result the client does not list is
  exported as stored.
* **file names are made here**, from the structure's number (`structure-01.png`, `structure-01.mol`,
  `results.smiles.txt`). Nothing the client or the job record says is ever used as a name or a path.
* **a structure without a molfile** (the recognition failed, or the SMILES is not valid) has no
  `.mol` file and no SDF record; it is still in the SMILES file and the CSV, so that the lists
  have every structure and the gaps are visible.
* text from outside (a SMILES that OCSR made up, a name from chem) is put on one line and cleaned
  of what could end an SDF record or start a spreadsheet formula.

The ZIP is written to an unseekable sink and handed out piece by piece, so a large crop is never
held in memory twice; the SDF and the CSV are small and built whole.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Buffer, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

from .analysis_pool import Analysis
from .chemistry import Chemistry
from .models import Result
from .molfile import SDF_RECORD_END, MolfileError, clean_molfile
from .names import NO_NAME, NameInfo, Namer, nameable

CHUNK_BYTES = 64 * 1024
SMILES_FILE = "results.smiles.txt"
CSV_COLUMNS = ("number", "smiles", "inchikey", "valid", "name", "edited")
# Characters that break a line or a record in a text file (`$` is dealt with separately).
LINE_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")
# A spreadsheet reads a cell that starts with one of these as a formula.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class StructureError(ValueError):
    """The molfile the client sent for one result cannot be read."""

    def __init__(self, index: int, reason: str) -> None:
        super().__init__(f"structure {index + 1}: {reason}")
        self.index = index
        self.reason = reason


@dataclass(frozen=True)
class Row:
    index: int  # Result.index, 0-based
    smiles: str
    inchikey: str
    valid: bool
    name: str | None
    molfile: str  # clean (see `clean_molfile`), or "" when there is none
    edited: bool

    @property
    def number(self) -> int:
        return self.index + 1


def file_stem(number: int, count: int) -> str:
    """`structure-01`: numbers are padded to the width of the largest, so files sort in order."""
    return f"structure-{number:0{max(2, len(str(count)))}d}"


# --- the rows ------------------------------------------------------------------------------------


def stored_molfile(result: Result) -> str:
    """The molfile of a recognised structure, as the job record has it (cleaned like any other,
    so that a damaged record cannot put anything odd in a file)."""
    if not result.molfile:
        return ""
    try:
        return clean_molfile(result.molfile, bounded=False)  # ours: written to a file, not read
    except MolfileError:
        return ""


async def build_rows(
    results: Sequence[Result], edits: Mapping[int, str], namer: Namer, analysis: Analysis
) -> list[Row]:
    """One row per result, in the order of `results`. `edits` maps a result index to a client's
    molfile that has already been through `clean_molfile`. All of them are read by RDKit one after
    the other in one session of `analysis`: one place, one child process (503 busy when there is
    no place). Raises `StructureError` for one that cannot be read, and for one that is too
    complex."""
    analysed: dict[int, Chemistry] = {}
    if edits:
        async with analysis.session() as session:
            for index, molfile in edits.items():
                try:
                    analysed[index] = await session.analyse(molfile)
                except MolfileError as error:
                    raise StructureError(index, str(error)) from error
    asked = [index for index, chem in analysed.items() if chem.valid and nameable(chem.smiles)]
    answers = (
        await namer([analysed[index].smiles for index in asked], patient=True) if asked else []
    )
    names: dict[int, NameInfo] = dict(zip(asked, answers, strict=True))

    rows: list[Row] = []
    for result in results:
        chem = analysed.get(result.index)
        if chem is None:
            rows.append(
                Row(
                    index=result.index,
                    smiles=result.smiles,
                    inchikey=result.inchikey,
                    valid=result.valid,
                    name=result.name,
                    molfile=stored_molfile(result),
                    edited=False,
                )
            )
        else:
            rows.append(
                Row(
                    index=result.index,
                    smiles=chem.smiles,
                    inchikey=chem.inchikey,
                    valid=chem.valid,
                    name=names.get(result.index, NO_NAME).name,
                    molfile=edits[result.index],
                    edited=True,
                )
            )
    return rows


# --- text files ----------------------------------------------------------------------------------


def one_line(text: str | None) -> str:
    """`text` with every line break and control character replaced by a space, then trimmed."""
    return LINE_UNSAFE.sub(" ", text or "").strip()


def smiles_text(rows: Sequence[Row]) -> str:
    """`<image>\\t<SMILES>\\t<IUPAC name>`, one line per structure (the legacy file had the first
    two columns)."""
    return "".join(
        f"{file_stem(row.number, len(rows))}.png\t{one_line(row.smiles)}\t{one_line(row.name)}\n"
        for row in rows
    )


def _sdf_value(text: str | None) -> str:
    """One line that cannot be read as the end of the record or as the next data header."""
    line = one_line(text)
    return f" {line}" if line.startswith((">", SDF_RECORD_END)) else line


def sdf_text(rows: Sequence[Row]) -> str:
    records: list[str] = []
    for row in rows:
        if not row.molfile:
            continue
        properties = {
            "NUMBER": str(row.number),
            "SMILES": _sdf_value(row.smiles),
            "INCHIKEY": row.inchikey,
            "NAME": _sdf_value(row.name),
            "EDITED": "true" if row.edited else "false",
        }
        body = "".join(f"> <{key}>\n{value}\n\n" for key, value in properties.items() if value)
        records.append(f"{row.molfile}{body}$$$$\n")
    return "".join(records)


def _cell(text: str) -> str:
    cleaned = one_line(text)
    return f"'{cleaned}" if cleaned.startswith(FORMULA_START) else cleaned


def csv_text(rows: Sequence[Row]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                row.number,
                _cell(row.smiles),
                row.inchikey,
                "true" if row.valid else "false",
                _cell(row.name or ""),
                "true" if row.edited else "false",
            ]
        )
    return out.getvalue()


# --- the ZIP -------------------------------------------------------------------------------------


@dataclass
class Entry:
    name: str
    source: bytes | BinaryIO  # bytes, or an open file that is read in pieces and then closed
    stored: bool = False  # True: not compressed (a PNG is already)

    def pieces(self) -> Iterator[bytes]:
        if isinstance(self.source, bytes):
            yield self.source
            return
        while chunk := self.source.read(CHUNK_BYTES):
            yield chunk

    def close(self) -> None:
        if not isinstance(self.source, bytes):
            self.source.close()


def zip_entries(rows: Sequence[Row], crops: Sequence[BinaryIO]) -> list[Entry]:
    """Image, then molfile, of each structure in order; the SMILES list last. `crops[i]` is the
    open crop file of `rows[i]`."""
    entries: list[Entry] = []
    for row, crop in zip(rows, crops, strict=True):
        stem = file_stem(row.number, len(rows))
        entries.append(Entry(f"{stem}.png", crop, stored=True))
        if row.molfile:
            entries.append(Entry(f"{stem}.mol", row.molfile.encode("utf-8")))
    entries.append(Entry(SMILES_FILE, smiles_text(rows).encode("utf-8", "replace")))
    return entries


class _Sink(io.RawIOBase):
    """Where the archive is written: collects the bytes until they are taken. Not seekable, so
    the ZIP uses data descriptors and each entry can be sent as soon as it is written."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, data: Buffer, /) -> int:
        part = bytes(data)
        self._parts.append(part)
        return len(part)

    def take(self) -> bytes:
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def zip_stream(entries: Iterable[Entry], moment: datetime) -> Iterator[bytes]:
    """The archive, piece by piece. Closes every entry's file, also when the reader stops early."""
    pending = list(entries)
    sink = _Sink()
    archive = zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED)
    stamp = (moment.year, moment.month, moment.day, moment.hour, moment.minute, moment.second)
    try:
        for entry in pending:
            info = zipfile.ZipInfo(entry.name, date_time=stamp)
            info.compress_type = zipfile.ZIP_STORED if entry.stored else zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with archive.open(info, "w") as out:
                for piece in entry.pieces():
                    out.write(piece)
                    if data := sink.take():
                        yield data
            if data := sink.take():
                yield data
        archive.close()
        if data := sink.take():
            yield data
    finally:
        for entry in pending:
            entry.close()
