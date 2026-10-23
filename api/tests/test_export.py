"""POST /api/jobs/{id}/export: ZIP, SDF and CSV of a job's structures."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass

import httpx
import pytest

from decimer_api.chemistry import analyse_smiles
from decimer_api.exports import SMILES_FILE, file_stem
from decimer_api.models import Job, new_job_id
from decimer_api.names import NameInfo
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.cdk_molblocks import PENTAVALENT_MOLBLOCK, PENTAVALENT_SMILES
from tests.conftest import FakeClock
from tests.results import (
    FakeNamer,
    crop_bytes,
    crop_path,
    make_cdk_only_result,
    make_done_job,
    make_results,
    molfile_of,
    name_of,
)

ENTRY_NAME = re.compile(r"structure-\d{2,}\.(png|mol)|results\.smiles\.txt")


@dataclass
class Exported:
    response: httpx.Response

    @property
    def zip(self) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(self.response.content))

    @property
    def names(self) -> list[str]:
        return self.zip.namelist()

    def text(self, name: str) -> str:
        return self.zip.read(name).decode()

    @property
    def smiles_lines(self) -> list[list[str]]:
        return [line.split("\t") for line in self.text(SMILES_FILE).splitlines()]


async def export(
    client: httpx.AsyncClient,
    job_id: str,
    fmt: str = "zip",
    molfiles: dict[str, str] | None = None,
) -> httpx.Response:
    body: dict[str, object] = {"format": fmt}
    if molfiles is not None:
        body["molfiles"] = molfiles
    return await client.post(f"/api/jobs/{job_id}/export", json=body)


async def export_zip(
    client: httpx.AsyncClient, job_id: str, molfiles: dict[str, str] | None = None
) -> Exported:
    response = await export(client, job_id, "zip", molfiles)
    assert response.status_code == 200, response.text
    return Exported(response)


@pytest.fixture
async def job(store: JobStore, settings: Settings, clock: FakeClock) -> Job:
    return await make_done_job(store, settings, clock)


# --- the ZIP -------------------------------------------------------------------------------------


async def test_the_zip_has_each_structures_image_and_molfile_in_result_order_and_a_smiles_file(
    client: httpx.AsyncClient, job: Job
) -> None:
    exported = await export_zip(client, job.id)
    expected = [
        name
        for number in range(1, 6)
        for name in (f"structure-0{number}.png", f"structure-0{number}.mol")
    ] + [SMILES_FILE]
    assert exported.names == expected
    assert exported.zip.testzip() is None


async def test_the_images_are_the_jobs_crops_and_the_molfiles_are_the_stored_ones(
    client: httpx.AsyncClient, job: Job
) -> None:
    exported = await export_zip(client, job.id)
    for result in job.results:
        stem = file_stem(result.index + 1, len(job.results))
        assert exported.zip.read(f"{stem}.png") == crop_bytes(result.index)
        assert exported.text(f"{stem}.mol") == result.molfile


async def test_the_smiles_file_has_image_smiles_and_the_iupac_name_per_line(
    client: httpx.AsyncClient, job: Job
) -> None:
    exported = await export_zip(client, job.id)
    assert exported.smiles_lines == [
        [f"structure-0{n}.png", result.smiles, name_of(result.smiles)]
        for n, result in enumerate(job.results, start=1)
    ]


async def test_a_structure_without_a_name_has_an_empty_third_column(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    unnamed = await make_done_job(store, settings, clock, make_results(named=False), ip="x")
    exported = await export_zip(client, unnamed.id)
    assert all(line[2] == "" for line in exported.smiles_lines)
    assert [line[1] for line in exported.smiles_lines] == [r.smiles for r in unnamed.results]


async def test_the_order_is_the_jobs_not_the_order_of_the_request(
    client: httpx.AsyncClient, job: Job
) -> None:
    """The client lists its edits from the last result to the first; the export is in job order."""
    edits = {
        str(i): molfile_of(s)
        for i, s in reversed(list(enumerate(["CCCl", "CCBr", "CCF", "CCI", "CCS"])))
    }
    exported = await export_zip(client, job.id, edits)
    assert [line[0] for line in exported.smiles_lines] == [
        f"structure-0{n}.png" for n in range(1, 6)
    ]
    assert [line[1] for line in exported.smiles_lines] == ["CCCl", "CCBr", "CCF", "CCI", "CCS"]
    assert exported.names[:2] == ["structure-01.png", "structure-01.mol"]


async def test_the_order_follows_the_results_even_when_their_smiles_are_not_sorted(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    """Names, SMILES and crops all come out in the order of the job, which is not alphabetical."""
    results = make_results(["CCN", "CCO", "C1CCCCC1", "CC(=O)O"])
    scrambled = await make_done_job(store, settings, clock, results, ip="y")
    exported = await export_zip(client, scrambled.id)
    assert [line[1] for line in exported.smiles_lines] == ["CCN", "CCO", "C1CCCCC1", "CC(=O)O"]
    for index in range(4):
        assert exported.zip.read(f"structure-0{index + 1}.png") == crop_bytes(index)


async def test_an_edited_molfile_wins_over_the_stored_one(
    client: httpx.AsyncClient, job: Job
) -> None:
    edited = molfile_of("CCCl")
    exported = await export_zip(client, job.id, {"1": edited})
    assert exported.text("structure-02.mol") == edited  # result 1's file is the edit, verbatim
    assert exported.text("structure-02.mol") != job.results[1].molfile
    for n in (1, 3, 4, 5):  # the others are as stored
        assert exported.text(f"structure-0{n}.mol") == job.results[n - 1].molfile


async def test_an_edit_changes_the_row_not_only_the_file(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    """The SMILES and the name in the list come from the edited molfile, never from the old row."""
    exported = await export_zip(client, job.id, {"1": molfile_of("CCCl")})
    assert exported.smiles_lines[1] == ["structure-02.png", "CCCl", name_of("CCCl")]
    assert exported.smiles_lines[0][1:] == [job.results[0].smiles, name_of(job.results[0].smiles)]
    assert namer.calls == [["CCCl"]]  # only the edited structure is named again


async def test_an_edit_of_a_structure_without_a_molfile_adds_one(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    results = make_results(["CCO", "CCN"])
    results[1] = results[1].model_copy(
        update={
            "smiles": "not a smiles",
            "inchikey": "",
            "valid": False,
            "molfile": "",
            "name": None,
        }
    )
    job = await make_done_job(store, settings, clock, results, ip="z")
    plain = await export_zip(client, job.id)
    assert plain.names == ["structure-01.png", "structure-01.mol", "structure-02.png", SMILES_FILE]
    assert plain.smiles_lines[1] == ["structure-02.png", "not a smiles", ""]
    fixed = await export_zip(client, job.id, {"1": molfile_of("CCN")})
    assert fixed.names[3] == "structure-02.mol"
    assert fixed.smiles_lines[1][1] == "CCN"


async def test_an_emptied_canvas_is_exported_as_the_edit_it_is(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    empty = "\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    exported = await export_zip(client, job.id, {"0": empty})
    assert exported.text("structure-01.mol") == empty  # not the recognised molfile
    assert exported.smiles_lines[0] == ["structure-01.png", "", ""]
    assert namer.calls == []


async def test_a_valid_edit_with_an_r_group_is_not_named(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    exported = await export_zip(client, job.id, {"0": molfile_of("[R1]CC")})
    assert "*" in exported.smiles_lines[0][1] and exported.smiles_lines[0][2] == ""
    assert namer.calls == []


async def test_names_of_edits_are_asked_again_every_time(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    edits = {"0": molfile_of("CCCl")}
    await export_zip(client, job.id, edits)
    await export_zip(client, job.id, edits)
    assert namer.calls == [["CCCl"], ["CCCl"]]


async def test_without_chem_an_edited_structure_has_no_name_and_the_export_works(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    namer.answer = lambda _s: NameInfo()
    exported = await export_zip(client, job.id, {"0": molfile_of("CCCl")})
    assert exported.smiles_lines[0] == ["structure-01.png", "CCCl", ""]
    assert exported.smiles_lines[1][2] == name_of(job.results[1].smiles)  # stored names stay


async def test_the_headers_say_it_is_a_download_and_not_to_cache_or_sniff_it(
    client: httpx.AsyncClient, job: Job
) -> None:
    response = await export(client, job.id, "zip")
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="decimer-results-{job.id[:8]}.zip"'
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_the_zip_response_has_no_length_because_it_is_streamed(
    client: httpx.AsyncClient, job: Job
) -> None:
    response = await export(client, job.id, "zip")
    assert "content-length" not in response.headers


def test_the_archive_is_written_in_pieces_and_a_large_file_is_not_held_whole() -> None:
    from datetime import UTC, datetime

    from decimer_api.exports import CHUNK_BYTES, Entry, zip_stream

    big = bytes(range(256)) * 16384  # 4 MiB
    entries = [
        Entry("a.png", io.BytesIO(big), stored=True),
        Entry("b.txt", b"text"),
    ]
    pieces = list(zip_stream(entries, datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)))
    assert len(pieces) > len(big) // CHUNK_BYTES // 2  # about one per chunk read
    assert max(len(piece) for piece in pieces) <= 2 * CHUNK_BYTES
    archive = zipfile.ZipFile(io.BytesIO(b"".join(pieces)))
    assert archive.read("a.png") == big and archive.read("b.txt") == b"text"
    assert archive.getinfo("b.txt").date_time == (2026, 10, 23, 12, 0, 0)


def test_the_files_of_the_archive_are_closed_even_when_the_reader_stops_early() -> None:
    from datetime import UTC, datetime

    from decimer_api.exports import Entry, zip_stream

    files = [io.BytesIO(b"x" * 300_000), io.BytesIO(b"y")]
    stream = zip_stream(
        [Entry("a.png", files[0], stored=True), Entry("b.png", files[1], stored=True)],
        datetime(2026, 10, 23, tzinfo=UTC),
    )
    next(stream)
    stream.close()  # the client went away
    assert all(file.closed for file in files)


async def test_the_crops_are_open_when_the_archive_starts(
    client: httpx.AsyncClient, job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimer_api import result_routes

    seen: list[list[bool]] = []
    real = result_routes.zip_stream

    def spy(entries, moment):  # type: ignore[no-untyped-def]
        seen.append(
            [entry.source.closed for entry in entries if not isinstance(entry.source, bytes)]
        )
        return real(entries, moment)

    monkeypatch.setattr(result_routes, "zip_stream", spy)
    assert (await export(client, job.id, "zip")).status_code == 200
    assert seen == [[False] * 5]


async def test_over_a_real_connection_the_zip_arrives_in_pieces_and_survives_a_deleted_job(
    app: object, job: Job, settings: Settings
) -> None:
    """ASGITransport buffers a response, so this one runs a real server: the archive is chunked,
    and the folder can be removed after the first piece (the user starts over, the sweep runs)
    without cutting the archive short."""
    import asyncio
    import shutil

    import uvicorn

    big = crop_bytes(0) + bytes(range(256)) * 8192  # 2 MiB: more than the socket buffers hold
    crop_path(settings, job, 0).write_bytes(big)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="off")  # type: ignore[arg-type]
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as real:
            async with real.stream(
                "POST", f"/api/jobs/{job.id}/export", json={"format": "zip"}
            ) as response:
                assert response.headers["transfer-encoding"] == "chunked"
                pieces = []
                async for piece in response.aiter_raw():
                    pieces.append(piece)
                    if len(pieces) == 1:
                        shutil.rmtree(settings.data_dir / job.id)
    finally:
        server.should_exit = True
        await task
    assert len(pieces) > 2
    archive = zipfile.ZipFile(io.BytesIO(b"".join(pieces)))
    assert archive.testzip() is None
    assert archive.read("structure-01.png") == big
    assert archive.read("structure-05.png") == crop_bytes(4)


async def test_more_than_ninety_nine_structures_are_numbered_to_the_width_of_the_largest(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    many = make_results(["CCO"] * 101)
    big = await make_done_job(store, settings, clock, many, ip="many")
    exported = await export_zip(client, big.id)
    assert exported.names[0] == "structure-001.png" and exported.names[-2] == "structure-101.mol"
    assert sorted(exported.names) == sorted(set(exported.names))


# --- SDF and CSV ---------------------------------------------------------------------------------


async def test_the_sdf_has_a_record_per_structure_in_order_with_the_edit(
    client: httpx.AsyncClient, job: Job
) -> None:
    edited = molfile_of("CCCl")
    response = await export(client, job.id, "sdf", {"2": edited})
    assert response.status_code == 200
    assert response.headers["content-type"] == "chemical/x-mdl-sdfile"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="decimer-results-{job.id[:8]}.sdf"'
    )
    records = response.text.split("$$$$\n")
    assert records.pop() == ""  # the text ends with a record separator
    assert len(records) == 5
    numbers = [re.search(r"> <NUMBER>\n(\d+)\n", record) for record in records]
    assert [m.group(1) for m in numbers if m] == ["1", "2", "3", "4", "5"]
    assert records[2].startswith(edited)  # the edit, as sent
    assert "> <SMILES>\nCCCl\n" in records[2] and f"> <NAME>\n{name_of('CCCl')}\n" in records[2]
    assert "> <EDITED>\ntrue\n" in records[2] and "> <EDITED>\nfalse\n" in records[0]
    assert records[0].startswith(job.results[0].molfile)
    assert f"> <INCHIKEY>\n{analyse_smiles('CCCl').inchikey}\n" in records[2]


async def test_the_sdf_leaves_out_a_structure_with_no_molfile(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    results = make_results(["CCO", "CCN", "CCC"])
    results[1] = results[1].model_copy(update={"molfile": "", "valid": False, "smiles": "bad"})
    job = await make_done_job(store, settings, clock, results, ip="gap")
    records = (await export(client, job.id, "sdf")).text.split("$$$$\n")[:-1]
    assert [
        re.search(r"> <NUMBER>\n(\d+)", r).group(1)
        for r in records
        if re.search(r"> <NUMBER>\n(\d+)", r)
    ] == ["1", "3"]  # type: ignore[union-attr]


async def test_the_csv_has_a_row_per_structure_in_order_with_the_edit(
    client: httpx.AsyncClient, job: Job
) -> None:
    response = await export(client, job.id, "csv", {"3": molfile_of("CCCl")})
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["number", "smiles", "inchikey", "valid", "name", "edited"]
    assert [row[0] for row in rows[1:]] == ["1", "2", "3", "4", "5"]
    assert rows[4] == [
        "4",
        "CCCl",
        analyse_smiles("CCCl").inchikey,
        "true",
        name_of("CCCl"),
        "true",
    ]
    assert rows[1] == ["1", "CCO", analyse_smiles("CCO").inchikey, "true", name_of("CCO"), "false"]


async def test_the_csv_of_an_invalid_structure_says_so(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    results = make_results(["CCO"])
    results[0] = results[0].model_copy(
        update={"smiles": "C(C", "inchikey": "", "valid": False, "molfile": "", "name": None}
    )
    bad = await make_done_job(store, settings, clock, results, ip="bad")
    rows = list(csv.reader(io.StringIO((await export(client, bad.id, "csv")).text)))
    assert rows[1] == ["1", "C(C", "", "false", "", "false"]


# --- a structure that only CDK accepted ---------------------------------------------


@pytest.fixture
async def mixed_job(store: JobStore, settings: Settings, clock: FakeClock) -> Job:
    """Result 0 is ethanol (RDKit's), result 1 only CDK accepted, result 2 neither."""
    results = make_results(["CCO", "CCN", "CCC"])
    results[1] = make_cdk_only_result(1)
    results[2] = results[2].model_copy(
        update={
            "smiles": "c1ccccc",
            "inchikey": "",
            "valid": False,
            "valid_by": None,
            "molfile": "",
            "name": None,
        }
    )
    return await make_done_job(store, settings, clock, results, ip="mixed")


async def test_the_csv_valid_column_is_true_cdk_or_false(
    client: httpx.AsyncClient, mixed_job: Job
) -> None:
    rows = list(csv.reader(io.StringIO((await export(client, mixed_job.id, "csv")).text)))
    assert rows[0] == ["number", "smiles", "inchikey", "valid", "name", "edited"]
    assert [row[3] for row in rows[1:]] == ["true", "cdk", "false"]
    # CDK-only: DECIMER's SMILES, no InChIKey, no name, not edited.
    assert rows[2] == ["2", PENTAVALENT_SMILES, "", "cdk", "", "false"]


async def test_a_cdk_only_structure_exports_cdks_molblock_in_the_zip_and_the_sdf(
    client: httpx.AsyncClient, mixed_job: Job
) -> None:
    exported = await export_zip(client, mixed_job.id)
    assert exported.names == [
        "structure-01.png",
        "structure-01.mol",
        "structure-02.png",
        "structure-02.mol",
        "structure-03.png",  # neither accepted it: no molfile
        SMILES_FILE,
    ]
    assert exported.text("structure-02.mol") == PENTAVALENT_MOLBLOCK
    assert exported.smiles_lines[1] == ["structure-02.png", PENTAVALENT_SMILES, ""]

    records = (await export(client, mixed_job.id, "sdf")).text.split("$$$$\n")[:-1]
    assert len(records) == 2  # ethanol and the CDK-only structure; the third has no molfile
    assert records[1].startswith(PENTAVALENT_MOLBLOCK)
    assert f"> <SMILES>\n{PENTAVALENT_SMILES}\n" in records[1]
    assert "> <VALID>\ncdk\n" in records[1]
    assert "> <INCHIKEY>" not in records[1] and "> <NAME>" not in records[1]
    assert "> <VALID>" not in records[0]  # said only where it adds something


async def test_an_edited_cdk_only_structure_is_judged_by_rdkit_alone(
    client: httpx.AsyncClient, mixed_job: Job, namer: FakeNamer
) -> None:
    """The edit comes from the editor and RDKit reads it: `true` or `false`, never `cdk`."""
    fixed = {"1": molfile_of("CCCl")}
    rows = list(csv.reader(io.StringIO((await export(client, mixed_job.id, "csv", fixed)).text)))
    assert rows[2] == [
        "2",
        "CCCl",
        analyse_smiles("CCCl").inchikey,
        "true",
        name_of("CCCl"),
        "true",
    ]
    # An edit RDKit still refuses is `false`, whatever CDK said of the structure before the edit.
    still_bad = {"1": PENTAVALENT_MOLBLOCK}
    rows = list(
        csv.reader(io.StringIO((await export(client, mixed_job.id, "csv", still_bad)).text))
    )
    assert rows[2][3] == "false" and rows[2][5] == "true"
    sdf = (await export(client, mixed_job.id, "sdf", still_bad)).text
    assert "> <VALID>" not in sdf  # an edit is never `cdk`
    assert namer.calls == [["CCCl"]]  # chem is asked about the valid edit only


# --- text from outside is made harmless ----------------------------------------------------------


HOSTILE = "evil\n$$$$\n> <NUMBER>\n99\n\r> <X> y"


async def test_a_hostile_name_cannot_add_records_lines_or_columns(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    namer.answer = lambda _s: NameInfo(HOSTILE, "valid", "opsin")
    edits = {"0": molfile_of("CCCl")}
    sdf = (await export(client, job.id, "sdf", edits)).text
    lines = sdf.splitlines()
    assert lines.count("$$$$") == 5  # the five records' own separators, no more
    assert not any(line.startswith("> <X>") for line in lines)
    assert len(re.findall(r"^> <NUMBER>$", sdf, re.MULTILINE)) == 5
    csv_rows = list(csv.reader(io.StringIO((await export(client, job.id, "csv", edits)).text)))
    assert len(csv_rows) == 6 and all(len(row) == 6 for row in csv_rows)
    exported = await export_zip(client, job.id, edits)
    assert len(exported.smiles_lines) == 5 and all(len(line) == 3 for line in exported.smiles_lines)


async def test_a_hostile_stored_name_or_smiles_is_made_harmless_too(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    results = make_results(["CCO", "CCN"])
    results[0] = results[0].model_copy(update={"name": HOSTILE, "smiles": "$$$$"})
    results[1] = results[1].model_copy(
        update={"smiles": "> <SMILES>", "name": "=cmd|' /C calc'!A0"}
    )
    hostile = await make_done_job(store, settings, clock, results, ip="host")
    sdf = (await export(client, hostile.id, "sdf")).text
    lines = sdf.splitlines()
    assert lines.count("$$$$") == 2  # one separator per record; the value "$$$$" is not one
    assert not any(line.startswith("> <SMILES>") and line != "> <SMILES>" for line in lines)
    assert len(re.findall(r"^> <SMILES>$", sdf, re.MULTILINE)) == 2
    exported = await export_zip(client, hostile.id)
    assert len(exported.smiles_lines) == 2
    rows = list(csv.reader(io.StringIO((await export(client, hostile.id, "csv")).text)))
    assert rows[2][4].startswith("'=")  # a spreadsheet will not run it
    assert len(rows) == 3


async def test_a_molfile_in_the_store_that_could_break_an_sdf_is_left_out_not_exported(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    results = make_results(["CCO", "CCN"])
    results[0] = results[0].model_copy(update={"molfile": "x\n$$$$\nM  END\n"})
    tampered = await make_done_job(store, settings, clock, results, ip="tamper")
    assert (await export_zip(client, tampered.id)).names[:2] == [
        "structure-01.png",
        "structure-02.png",
    ]
    assert (await export(client, tampered.id, "sdf")).text.splitlines().count("$$$$") == 1


async def test_what_follows_m_end_in_an_edited_molfile_is_not_exported(
    client: httpx.AsyncClient, job: Job
) -> None:
    sneaky = molfile_of("CCCl") + ">  <NAME>\nattacker\n\n"
    exported = await export_zip(client, job.id, {"0": sneaky})
    assert exported.text("structure-01.mol") == molfile_of("CCCl")
    sdf = (await export(client, job.id, "sdf", {"0": sneaky})).text
    assert "attacker" not in sdf


# --- refusals: what the client sends is never trusted --------------------------------------------


async def test_an_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    assert (await export(client, new_job_id())).status_code == 404


@pytest.mark.parametrize("job_id", ["..", "x", "A" * 32, "../" + "a" * 29])
async def test_a_malformed_job_id_is_404(client: httpx.AsyncClient, job_id: str) -> None:
    assert (await export(client, job_id)).status_code == 404


async def test_a_job_with_nothing_to_export_is_409(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    empty = await make_done_job(store, settings, clock, [], ip="none")
    response = await export(client, empty.id)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_results"


async def test_a_job_that_has_not_finished_has_nothing_to_export(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    created = Job.create(
        job_id=new_job_id(), client_ip_hash="q", pages=1, pages_processed=1, now=clock()
    )
    assert await store.admit(created) is not None
    assert (await export(client, created.id)).status_code == 409


@pytest.mark.parametrize("fmt", ["pdf", "ZIP", "", None, 3])
async def test_an_unknown_format_is_422(client: httpx.AsyncClient, job: Job, fmt: object) -> None:
    response = await client.post(f"/api/jobs/{job.id}/export", json={"format": fmt})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "key",
    [
        "5",  # one past the last result
        "-1",
        "01",
        " 1",
        "1 ",
        "+1",
        "1.0",
        "abc",
        "",
        "1e1",
        "../../etc/passwd",
        "../1",
        "٣",  # an Arabic-Indic digit: int() would take it
        "9" * 20,
    ],
)
async def test_a_key_that_is_not_a_result_of_this_job_refuses_the_whole_export(
    client: httpx.AsyncClient, job: Job, key: str
) -> None:
    response = await export(
        client, job.id, "zip", {"0": molfile_of("CCCl"), key: molfile_of("CCO")}
    )
    assert response.status_code == 422, key
    assert response.json()["detail"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "extra",
    [
        {"names": ["../../x"]},
        {"filename": "../x.zip"},
        {"paths": ["/etc/passwd"]},
        {"order": [4, 3, 2, 1, 0]},
    ],
)
async def test_the_client_cannot_send_names_paths_or_an_order(
    client: httpx.AsyncClient, job: Job, extra: dict[str, object]
) -> None:
    response = await client.post(f"/api/jobs/{job.id}/export", json={"format": "zip", **extra})
    assert response.status_code == 422


async def test_file_names_are_made_here_whatever_the_molfile_says(
    client: httpx.AsyncClient, job: Job
) -> None:
    sneaky = molfile_of("CCCl").replace("RDKit", "../../etc/passwd")
    exported = await export_zip(client, job.id, {"0": sneaky})
    assert all(ENTRY_NAME.fullmatch(name) for name in exported.names)
    assert "passwd" in exported.text("structure-01.mol")  # a title line is only content


@pytest.mark.parametrize(
    "molfile",
    [
        "not a molfile",
        "",
        "title\n\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n",
        molfile_of("CCO").replace("\n", "\x00\n", 1),
        molfile_of("CCO").replace("RDKit", "$$$$"),
        "\ud800\nM  END\n",
    ],
)
async def test_a_molfile_that_cannot_be_used_refuses_the_whole_export_naming_nothing_of_it(
    client: httpx.AsyncClient, job: Job, molfile: str
) -> None:
    import json

    response = await client.post(
        f"/api/jobs/{job.id}/export",
        content=json.dumps({"format": "zip", "molfiles": {"1": molfile}}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"


async def test_a_molfile_over_the_size_limit_is_413_and_one_at_the_limit_is_fine(
    client: httpx.AsyncClient, job: Job, settings: Settings
) -> None:
    base = molfile_of("CCO").lstrip("\n")
    limit = settings.max_molfile_bytes

    def padded(size: int) -> str:
        return "x" * (size - len(base) - 1) + "\n" + base

    assert (await export(client, job.id, "csv", {"0": padded(limit)})).status_code == 200
    over = await export(client, job.id, "csv", {"0": padded(limit + 1)})
    assert over.status_code == 413
    assert over.json()["detail"]["code"] == "too_large"


async def test_a_request_body_over_one_megabyte_is_413_before_it_is_read(
    client: httpx.AsyncClient, job: Job
) -> None:
    molfiles = {str(i): "x" * 60_000 for i in range(5)} | {
        str(i): "y" * 60_000 for i in range(5, 40)
    }
    response = await export(client, job.id, "zip", molfiles)
    assert response.status_code == 413


async def test_a_body_with_no_length_is_cut_off_at_one_megabyte(
    client: httpx.AsyncClient, job: Job
) -> None:
    sent = 0

    async def body() -> object:
        nonlocal sent
        yield b'{"format": "zip", "molfiles": {"0": "'
        for _ in range(2000):
            sent += 1
            yield b"x" * 4096
        yield b'"}}'

    response = await client.post(
        f"/api/jobs/{job.id}/export",
        content=body(),  # type: ignore[arg-type]
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert sent * 4096 < 2 * 1024 * 1024


async def test_every_edit_of_a_full_job_fits_when_the_molfiles_are_normal_sized(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    twenty = await make_done_job(store, settings, clock, make_results(["CCO"] * 20), ip="twenty")
    edits = {str(i): molfile_of("CCCl") for i in range(20)}
    exported = await export_zip(client, twenty.id, edits)
    assert all(line[1] == "CCCl" for line in exported.smiles_lines)


async def test_a_result_without_a_crop_file_is_left_out_of_the_zip_images_and_nothing_else(
    client: httpx.AsyncClient, job: Job, settings: Settings
) -> None:
    """An image of a job of several images that could not be read has no crop: its image is
    left out, its molfile and its line of the SMILES list stay, and the others are untouched."""
    crop_path(settings, job, 3).unlink()
    exported = await export_zip(client, job.id)
    assert exported.names == [
        name
        for number in range(1, 6)
        for name in (f"structure-0{number}.png", f"structure-0{number}.mol")
        if name != "structure-04.png"
    ] + [SMILES_FILE]
    assert exported.zip.testzip() is None
    assert exported.zip.read("structure-05.png") == crop_bytes(4)
    assert exported.text("structure-04.mol") == job.results[3].molfile
    assert [line[1] for line in exported.smiles_lines] == [r.smiles for r in job.results]


async def test_an_unreadable_image_is_a_line_of_the_list_with_no_image_and_no_molfile(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    """What the worker leaves for an image it could not read: an empty SMILES, no molfile, no
    crop file. The archive has the other images and a line for it, the numbers do not move."""
    results = make_results(["CCO", "CCO", "c1ccccc1"])
    results[1] = results[1].model_copy(
        update={
            "smiles": "",
            "inchikey": "",
            "valid": False,
            "molfile": "",
            "svg": "",
            "name": None,
        }
    )
    unreadable = await make_done_job(store, settings, clock, results, ip="unreadable")
    crop_path(settings, unreadable, 1).unlink()
    exported = await export_zip(client, unreadable.id)
    assert exported.names == [
        "structure-01.png",
        "structure-01.mol",
        "structure-03.png",
        "structure-03.mol",
        SMILES_FILE,
    ]
    # the list keeps a line for every structure, numbered as before, by the image's name
    assert [line[0] for line in exported.smiles_lines] == [
        "structure-01.png",
        "structure-02.png",
        "structure-03.png",
    ]
    assert exported.smiles_lines[1][1] == ""


async def test_a_job_with_no_crop_file_at_all_still_exports_its_molfiles_and_list(
    client: httpx.AsyncClient, job: Job, settings: Settings
) -> None:
    for index in range(len(job.results)):
        crop_path(settings, job, index).unlink()
    exported = await export_zip(client, job.id)
    assert not [name for name in exported.names if name.endswith(".png")]
    assert len([name for name in exported.names if name.endswith(".mol")]) == len(job.results)
    assert len(exported.smiles_lines) == len(job.results)


async def test_a_symlinked_crop_is_not_followed_and_its_image_is_left_out(
    client: httpx.AsyncClient, job: Job, settings: Settings, tmp_path: object
) -> None:
    from pathlib import Path

    secret = Path(str(tmp_path)) / "secret.png"
    secret.write_bytes(b"secret")
    target = crop_path(settings, job, 0)
    target.unlink()
    target.symlink_to(secret)
    exported = await export_zip(client, job.id)
    assert "structure-01.png" not in exported.names and "structure-01.mol" in exported.names
    assert b"secret" not in exported.response.content


# --- rate limit ----------------------------------------------------------------------------------


async def test_exports_are_rate_limited_per_client(
    make_client: object, job: Job, settings: Settings, clock: FakeClock
) -> None:
    one = make_client("203.0.113.1")  # type: ignore[operator]
    other = make_client("203.0.113.2")  # type: ignore[operator]
    for _ in range(settings.export_rate_per_minute):
        assert (await export(one, job.id, "csv")).status_code == 200
    refused = await export(one, job.id, "csv")
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert int(refused.headers["retry-after"]) >= 1
    assert (await export(other, job.id, "csv")).status_code == 200
    clock.advance(61)
    assert (await export(one, job.id, "csv")).status_code == 200


# --- the cost gate and the namer's patience, through the export (review 1 and 3) ---------


@pytest.mark.parametrize("count", [44, 60])
async def test_an_edit_that_would_cost_seconds_and_gigabytes_refuses_the_export_at_once(
    client: httpx.AsyncClient, job: Job, count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from rdkit import Chem

    from tests.molfiles import RdkitWasAsked, complete_graph_of_dummies

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        # A BaseException, so that no `except Exception` on the way can turn it into a refusal.
        raise RdkitWasAsked("RDKit was asked to read a molfile the gate should have refused")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    started = time.monotonic()
    response = await export(client, job.id, "zip", {"1": complete_graph_of_dummies(count)})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert time.monotonic() - started < 0.5


async def test_a_stored_molfile_is_exported_whatever_it_costs_to_read_it_is_only_written(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    """The gate is for what RDKit will read. A recognised structure this service wrote has a
    molfile of any size, and the export must still carry it."""
    from tests.molfiles import chain

    results = make_results(["CCO", "CCN"])
    results[0] = results[0].model_copy(update={"molfile": chain(320)})  # over MAX_ATOMS
    big = await make_done_job(store, settings, clock, results, ip="big")
    exported = await export_zip(client, big.id)
    assert exported.text("structure-01.mol") == chain(320)


async def test_an_export_asks_the_namer_to_be_patient_because_a_person_waits_for_the_file(
    client: httpx.AsyncClient, job: Job, namer: FakeNamer
) -> None:
    await export_zip(client, job.id, {"0": molfile_of("CCCl")})
    assert namer.patient == [True]
