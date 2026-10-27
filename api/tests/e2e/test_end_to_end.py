"""End to end: uploads through `/api`, an arq worker, the pipeline against fake model
services, then poll, validate, export and delete; nothing in between is replaced except the models.

Run it like any test (`uv run pytest tests/e2e`): fakeredis stands in for Redis. With
`E2E_REDIS_URL=redis://host:port/0` (CI and scripts/check.sh) it runs on a real one, whose
`decimer:*` and `arq:*` keys it owns (none must exist before; it deletes them after, and nothing
else). `stack.py` says what is real.

The fake services (`tests/worker/fakes.py`) make every crop say which crop it is, so the checks
read the order, the cap and which crop went where from the results themselves.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pypdfium2 as pdfium
import pytest
from rdkit import Chem

from decimer_api.exports import SMILES_FILE
from tests.e2e.conftest import MakeStack
from tests.e2e.stack import REDIS_URL_ENV, Gate, Stack, eventually
from tests.factories import make_pdf, make_png, multipart
from tests.results import molfile_of
from tests.worker.fakes import FakeBackend, bbox_for, make_crop, png_size, smiles_for

pytestmark = pytest.mark.timeout(110)  # the suite's 60 s is for unit tests; this one renders a PDF

GOLDEN_PDF = Path(__file__).resolve().parents[3] / "golden" / "zou2024_penicillium_shentong.pdf"
SMALL_PAGE = (144.0, 144.0)  # 2 x 2 in: 600 x 600 px at 300 DPI
CAP = 20  # MAX_STRUCTURES

# What the fake seg finds on the golden paper (0-based page -> structures), 33 in all: more than
# the cap, which falls in the middle of page 4. (The real paper has structures on pages 3, 4 and 7.)
LAYOUT = {0: 1, 2: 9, 3: 9, 4: 3, 6: 8, 7: 3}
# The structures the job keeps, in the order they must come back: page by page, then seg's order.
KEPT = [(page, k) for page in sorted(LAYOUT) for k in range(LAYOUT[page])][:CAP]
FOUND = sum(LAYOUT.values())
# seg answers page 0 only after it has answered this many later pages of the PDF, so the answers
# arrive out of order and the order of the results has to come from the numbering, not the arrival.
PAGES_BEFORE_PAGE_ZERO = 3
# Names the fake chem knows: three of the recognised structures, and what the edits below are.
NAMED = (0, 7, 19)
NAMES = {
    **{smiles_for(*KEPT[i]): (f"name of structure {i + 1}", "pin_verified") for i in NAMED},
    "c1ccccc1": ("benzene", "pin_verified"),
    "CCO": ("ethanol", "pin_verified"),
}
# The user's edits, by 0-based result index, sent in the reverse of the order of the job.
EDITS = {"7": molfile_of("CCO"), "3": molfile_of("c1ccccc1")}
BENZENE_KEY = "UHOVQNZJYSORNB-UHFFFAOYSA-N"
ETHANOL_KEY = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"

STATUS_RANK = {"queued": 0, "running": 1, "done": 2, "failed": 2}
STATE_RANK = {"pending": 0, "active": 1, "done": 2}


# --- the API as a client uses it -------------------------------------------------------------


async def post_files(api: httpx.AsyncClient, files: list[tuple[str, bytes]]) -> httpx.Response:
    body, headers = multipart(files)
    return await api.post("/api/jobs", content=body, headers=headers)


async def create_job(api: httpx.AsyncClient, files: list[tuple[str, bytes]]) -> dict[str, Any]:
    response = await post_files(api, files)
    assert response.status_code == 202, response.text
    job: dict[str, Any] = response.json()
    assert response.headers["location"] == f"/api/jobs/{job['id']}"
    assert re.fullmatch(r"[0-9a-f]{32}", job["id"])
    return job


async def get_job(api: httpx.AsyncClient, job_id: str) -> dict[str, Any]:
    response = await api.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    job: dict[str, Any] = response.json()
    return job


async def poll_until_finished(api: httpx.AsyncClient, job_id: str) -> list[dict[str, Any]]:
    """GET the job until it is done or failed, as the progress view does; every answer, in order."""
    seen: list[dict[str, Any]] = []

    async def finished() -> bool:
        seen.append(await get_job(api, job_id))
        return bool(seen[-1]["status"] in ("done", "failed"))

    await eventually(finished, f"job {job_id} to finish")
    return seen


def assert_progress_is_legal(seen: list[dict[str, Any]]) -> None:
    """What a client polling the job may see: the status only moves forward, a stage never goes
    back, is never done before the stages before it, and the results come with the last stage."""
    previous_status, previous_stages = 0, [0] * 5
    for job in seen:
        status = STATUS_RANK[job["status"]]
        stages = [STATE_RANK[stage["state"]] for stage in job["stages"]]
        assert [stage["name"] for stage in job["stages"]] == [
            "upload",
            "render",
            "segment",
            "recognise",
            "check",
        ]
        assert status >= previous_status, [j["status"] for j in seen]
        assert all(now >= before for now, before in zip(stages, previous_stages, strict=True))
        for position, state in enumerate(stages):
            assert state != 2 or all(earlier == 2 for earlier in stages[:position]), stages
        if job["results"]:
            assert stages[-1] == 2
        previous_status, previous_stages = status, stages


# --- reading the exports ---------------------------------------------------------------------


def smiles_of_molfile(molfile: str) -> str:
    molecule = Chem.MolFromMolBlock(molfile)
    assert molecule is not None, molfile
    return str(Chem.MolToSmiles(molecule))


def sdf_records(text: str) -> list[tuple[str, dict[str, str]]]:
    """(molfile, data fields) of every record of an SDF, in file order."""
    records = []
    for record in text.split("$$$$\n")[:-1]:
        molfile, end, data = record.partition("M  END\n")
        assert end
        records.append((molfile + end, dict(re.findall(r"^> <(\w+)>\n(.*)$", data, re.MULTILINE))))
    return records


class AnswerPageZeroLast(Gate):
    """The `pauses["seg"]` hook that holds page 0 of the PDF (the pasted image, which is small, is
    left alone) until `PAGES_BEFORE_PAGE_ZERO` other pages have been let through. `answered` is
    the order in which seg answered the pages of the PDF."""

    def __init__(self) -> None:
        super().__init__()
        self.answered: list[int] = []

    async def __call__(self, parts: dict[str, bytes]) -> None:
        if png_size(parts["image"])[0] < 1000:
            return
        page = int(parts["page_index"])
        if page == 0:
            await self.wait_until(lambda: len(self.answered) >= PAGES_BEFORE_PAGE_ZERO)
        self.answered.append(page)


def page_pixels() -> list[tuple[float, float]]:
    """The size in pixels at 300 DPI of every page of the golden paper, from pdfium."""
    document = pdfium.PdfDocument(GOLDEN_PDF)
    try:
        return [
            (width * 300 / 72, height * 300 / 72)
            for width, height in (document[i].get_size() for i in range(len(document)))
        ]
    finally:
        document.close()


# --- the test --------------------------------------------------------------------------------


async def test_a_golden_pdf_and_a_pasted_image_go_through_the_whole_stack(
    make_stack: MakeStack,
) -> None:
    assert GOLDEN_PDF.is_file(), "golden/ is part of the repository: CI must have it"
    seg_order = AnswerPageZeroLast()
    backend = FakeBackend(layout=LAYOUT, pauses={"seg": seg_order}, names=NAMES)
    stack = await make_stack(backend, worker={"seg_concurrency": 3, "ocsr_concurrency": 2})
    api, settings = stack.api, stack.settings

    # The fast worker is up and the default one is not: a small job has a lane of its own.
    fast = await stack.start_worker(settings.worker_fast_queue_name)
    pdf = await create_job(api, [("zou2024_penicillium_shentong.pdf", GOLDEN_PDF.read_bytes())])
    assert (pdf["status"], pdf["pages"], pdf["pagesProcessed"], pdf["truncated"]) == (
        "queued",
        10,
        10,
        False,
    )
    assert [stage["state"] for stage in pdf["stages"]] == ["done"] + ["pending"] * 4
    assert (pdf["queuePosition"], pdf["results"], pdf["warnings"]) == (1, [], [])
    pasted = await create_job(api, [("image.png", make_png((321, 123)))])
    assert (pasted["pages"], pasted["pagesProcessed"]) == (1, 1)

    image_seen = await poll_until_finished(api, pasted["id"])
    assert_progress_is_legal(image_seen)
    image = image_seen[-1]
    assert image["status"] == "done", image
    await eventually(lambda: fast.worker.jobs_complete == 1, "arq to count the fast job")
    # The PDF waited for the default worker, which is not there yet: queued, first in line.
    waiting = await get_job(api, pdf["id"])
    assert (waiting["status"], waiting["queuePosition"]) == ("queued", 1)
    assert await stack.redis.zcard(settings.worker_queue_name) == 1

    # --- the pasted image: one structure, as seg boxed it ---
    assert [stage["state"] for stage in image["stages"]] == ["done"] * 5
    assert (image["structuresFound"], image["structuresDropped"], image["truncated"]) == (
        1,
        0,
        False,
    )
    assert image["warnings"] == [] and image["error"] is None
    (only,) = image["results"]
    assert (only["index"], only["number"], only["pageIndex"]) == (0, 1, 0)
    assert only["bbox"] == list(bbox_for(0, 0))
    assert (only["smiles"], only["valid"], only["validBy"]) == (smiles_for(0, 0), True, "rdkit")
    assert only["isStructure"] is True and only["confidence"] == 0.9
    assert only["name"] == "name of structure 1"  # chem names by SMILES, and this is structure 1's
    assert only["svg"].startswith("<svg") and "V2000" in only["molfile"]
    assert only["cropUrl"] == f"/api/jobs/{pasted['id']}/media/crop-0.png"
    crop = await api.get(only["cropUrl"])
    assert (crop.status_code, crop.headers["content-type"]) == (200, "image/png")
    assert crop.content == make_crop(0, 0)

    # --- the PDF, once the default worker is there ---
    default = await stack.start_worker(settings.worker_queue_name)
    seen = await poll_until_finished(api, pdf["id"])
    assert_progress_is_legal(seen)
    job = seen[-1]
    assert job["status"] == "done", job
    await eventually(lambda: default.worker.jobs_complete == 1, "arq to count the PDF job")
    assert (fast.worker.jobs_complete, default.worker.jobs_failed, fast.worker.jobs_failed) == (
        1,
        0,
        0,
    )
    assert [stage["state"] for stage in job["stages"]] == ["done"] * 5
    assert job["error"] is None and job["queuePosition"] is None

    # the cap: found 33, the first 20 kept, in document order, and the warning says so
    assert (job["structuresFound"], job["structuresDropped"], job["truncated"]) == (
        FOUND,
        FOUND - CAP,
        True,
    )
    assert [w["code"] for w in job["warnings"]] == ["structures_truncated"]
    assert f"{FOUND} structures were found" in job["warnings"][0]["message"]
    results = job["results"]
    assert [r["index"] for r in results] == list(range(CAP))
    assert [r["number"] for r in results] == list(range(1, CAP + 1))
    assert [r["pageIndex"] for r in results] == [page for page, _ in KEPT]
    assert [r["smiles"] for r in results] == [smiles_for(*structure) for structure in KEPT]
    assert [r["bbox"] for r in results] == [list(bbox_for(*structure)) for structure in KEPT]
    assert all(r["valid"] and r["isStructure"] and r["validBy"] == "rdkit" for r in results)
    assert all(re.fullmatch(r"[A-Z]{14}-[A-Z]{10}-[A-Z]", r["inchikey"]) for r in results)
    assert len({r["inchikey"] for r in results}) == CAP  # twenty different structures
    assert all(r["svg"].startswith("<svg") and "V2000" in r["molfile"] for r in results)
    # names: the three chem knew, in the places they belong; the others have none
    assert [r["name"] for r in results] == [
        f"name of structure {i + 1}" if i in NAMED else None for i in range(CAP)
    ]
    assert [r["nameTier"] for r in results if r["name"]] == ["pin_verified"] * len(NAMED)
    assert [r["nameVerified"] for r in results if r["name"]] == ["opsin"] * len(NAMED)

    # the files: each crop is the crop of its own structure; nothing was kept for the dropped ones
    for number, (result, structure) in enumerate(zip(results, KEPT, strict=True)):
        crop = await api.get(result["cropUrl"])
        assert crop.status_code == 200, number
        assert crop.content == make_crop(*structure), number
    assert (await api.get(f"/api/jobs/{job['id']}/media/crop-{CAP}.png")).status_code == 404
    pixels = page_pixels()
    seg_pages = {
        int(parts["page_index"]): parts["image"]
        for parts in backend.parts_of("seg")
        if png_size(parts["image"])[0] > 1000  # the pasted image is 321 x 123
    }
    assert sorted(seg_pages) == list(range(10))  # every page is segmented, also past the cap
    # seg did answer out of order (page 0 after pages 1-3, which have structures too): the results
    # above are in document order all the same
    assert seg_order.answered.index(0) >= PAGES_BEFORE_PAGE_ZERO
    assert sorted(seg_order.answered) == list(range(10))
    for page, (width, height) in enumerate(pixels):
        served = await api.get(f"/api/jobs/{job['id']}/media/page-{page}.png")
        assert served.status_code == 200
        assert served.content == seg_pages[page]  # what seg was sent is what is served
        assert all(
            abs(a - b) < 1 for a, b in zip(png_size(served.content), (width, height), strict=True)
        )
    assert backend.count("seg") == 10 + 1
    assert backend.count("ocsr") == CAP + 1 and backend.count("cls") == CAP + 1
    named = [batch for batch in backend.chem_batches("/names") if len(batch) == CAP]
    assert named == [[smiles_for(*structure) for structure in KEPT]]  # one batch, in order

    # --- validate: an edited structure is read again by the server ---
    benzene = molfile_of("c1ccccc1")
    validated = await api.post(
        f"/api/jobs/{job['id']}/results/3/validate", json={"molfile": benzene}
    )
    assert validated.status_code == 200, validated.text
    assert validated.json() | {"svg": ""} == {
        "smiles": "c1ccccc1",
        "inchikey": BENZENE_KEY,
        "svg": "",
        "valid": True,
        "name": "benzene",
        "nameTier": "pin_verified",
        "nameVerified": "opsin",
    }
    assert validated.json()["svg"].startswith("<svg")
    assert (
        await api.post(f"/api/jobs/{job['id']}/results/{CAP}/validate", json={"molfile": benzene})
    ).status_code == 404

    # --- export: the job's order, the user's edits, three formats ---
    kept_smiles = [smiles_for(*structure) for structure in KEPT]
    shown = [{**r, "smiles": kept_smiles[i]} for i, r in enumerate(results)]
    shown[3] = {**shown[3], "smiles": "c1ccccc1", "inchikey": BENZENE_KEY, "name": "benzene"}
    shown[7] = {**shown[7], "smiles": "CCO", "inchikey": ETHANOL_KEY, "name": "ethanol"}
    edited = {3, 7}

    exported = await api.post(
        f"/api/jobs/{job['id']}/export", json={"format": "zip", "molfiles": EDITS}
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert exported.headers["content-disposition"] == (
        f'attachment; filename="decimer-results-{job["id"][:8]}.zip"'
    )
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    assert archive.testzip() is None
    assert archive.namelist() == [
        f"structure-{number:02d}.{extension}"
        for number in range(1, CAP + 1)
        for extension in ("png", "mol")
    ] + [SMILES_FILE]
    for position, structure in enumerate(KEPT):
        stem = f"structure-{position + 1:02d}"
        assert archive.read(f"{stem}.png") == make_crop(*structure), stem
        assert smiles_of_molfile(archive.read(f"{stem}.mol").decode()) == shown[position]["smiles"]
    assert archive.read(SMILES_FILE).decode().splitlines() == [
        f"structure-{position + 1:02d}.png\t{row['smiles']}\t{row['name'] or ''}"
        for position, row in enumerate(shown)
    ]

    exported = await api.post(
        f"/api/jobs/{job['id']}/export", json={"format": "sdf", "molfiles": EDITS}
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "chemical/x-mdl-sdfile"
    records = sdf_records(exported.text)
    assert [fields["NUMBER"] for _, fields in records] == [str(n) for n in range(1, CAP + 1)]
    for position, ((molfile, fields), row) in enumerate(zip(records, shown, strict=True)):
        assert smiles_of_molfile(molfile) == row["smiles"] == fields["SMILES"], position
        assert fields["INCHIKEY"] == row["inchikey"], position
        assert fields.get("NAME") == row["name"], position
        assert fields["EDITED"] == ("true" if position in edited else "false"), position

    exported = await api.post(
        f"/api/jobs/{job['id']}/export", json={"format": "csv", "molfiles": EDITS}
    )
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "text/csv; charset=utf-8"
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert list(rows[0]) == [
        "number",
        "smiles",
        "inchikey",
        "valid",
        "name",
        "edited",
        "incomplete",
    ]
    assert [row["number"] for row in rows] == [str(n) for n in range(1, CAP + 1)]
    for position, (row, expected) in enumerate(zip(rows, shown, strict=True)):
        assert row == {
            "number": str(position + 1),
            "smiles": expected["smiles"],
            "inchikey": expected["inchikey"],
            "valid": "true",
            "name": expected["name"] or "",
            "edited": "true" if position in edited else "false",
            "incomplete": "false",
        }, position

    # an export without edits is the job as stored
    plain = await api.post(f"/api/jobs/{job['id']}/export", json={"format": "csv"})
    assert [row["smiles"] for row in csv.DictReader(io.StringIO(plain.text))] == kept_smiles

    # --- delete: the job, its files and its answers go; so does the one pasted image ---
    folder = settings.data_dir / job["id"]
    assert folder.is_dir()
    assert (await api.delete(f"/api/jobs/{job['id']}")).status_code == 204
    assert (await api.get(f"/api/jobs/{job['id']}")).status_code == 404
    assert (await api.get(f"/api/jobs/{job['id']}/media/crop-0.png")).status_code == 404
    assert (
        await api.post(f"/api/jobs/{job['id']}/export", json={"format": "csv"})
    ).status_code == 404
    assert (await api.delete(f"/api/jobs/{job['id']}")).status_code == 404
    assert not folder.exists()
    assert (await api.delete(f"/api/jobs/{pasted['id']}")).status_code == 204
    assert (await api.get(f"/api/jobs/{pasted['id']}")).status_code == 404
    await assert_nothing_left(stack)


async def assert_nothing_left(stack: Stack) -> None:
    """No job record, no slot held, nothing of arq's but the workers' health keys."""
    redis = stack.redis
    assert await redis.keys("decimer:job:*") == []
    for key in await redis.keys("decimer:active:*"):
        assert await redis.smembers(key) == set(), key
    assert await redis.zcard("decimer:queue") == 0

    async def arq_is_empty() -> bool:
        leftovers = [k for k in await redis.keys("arq:*") if not k.endswith("health-check")]
        return not leftovers

    await eventually(arq_is_empty, "arq to hold nothing")
    assert list(stack.settings.data_dir.iterdir()) == []


async def test_delete_gives_back_the_slot_of_a_queued_job_and_the_worker_skips_it(
    make_stack: MakeStack,
) -> None:
    """One client may have two jobs. The first runs (held in seg by a gate), the second waits for
    the one place of the worker. Deleting the waiting one frees its slot at once, so the client can
    upload again; the worker later finds no record for it and does no work for it. The new upload
    is an 11-page PDF: the 10-page limit warns, and 10 pages are processed."""
    gate = Gate()
    backend = FakeBackend(layout={page: 1 for page in range(10)}, pauses={"seg": gate})
    stack = await make_stack(backend, worker={"worker_max_jobs": 1})
    api, settings = stack.api, stack.settings
    default = await stack.start_worker(settings.worker_queue_name)

    first = await create_job(api, [("two.pdf", make_pdf(2, page_size=SMALL_PAGE))])
    await eventually(lambda: gate.waiting >= 1, "the first job to reach seg")
    assert (await get_job(api, first["id"]))["status"] == "running"
    second = await create_job(api, [("three.pdf", make_pdf(3, page_size=SMALL_PAGE))])
    queued = await get_job(api, second["id"])
    assert (queued["status"], queued["queuePosition"]) == ("queued", 1)

    refused = await post_files(api, [("again.png", make_png())])
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"

    assert (await api.delete(f"/api/jobs/{second['id']}")).status_code == 204
    assert (await api.get(f"/api/jobs/{second['id']}")).status_code == 404
    assert not (settings.data_dir / second["id"]).exists()
    third = await create_job(
        api, [("eleven.pdf", make_pdf(11, page_size=SMALL_PAGE))]
    )  # a slot again
    assert (third["pages"], third["pagesProcessed"], third["truncated"]) == (11, 10, True)
    assert [w["code"] for w in third["warnings"]] == ["pages_truncated"]
    assert (await post_files(api, [("again.png", make_png())])).status_code == 429  # full again

    gate.open()
    done_first = (await poll_until_finished(api, first["id"]))[-1]
    done_third = (await poll_until_finished(api, third["id"]))[-1]
    assert (done_first["status"], done_third["status"]) == ("done", "done")
    assert [r["pageIndex"] for r in done_first["results"]] == [0, 1]
    assert [r["pageIndex"] for r in done_third["results"]] == list(range(10))
    assert [w["code"] for w in done_third["warnings"]] == ["pages_truncated"]
    assert done_third["truncated"] is True and done_third["structuresDropped"] == 0
    # The worker took the deleted job off arq's queue and did nothing for it: 2 + 10 pages, not 15.
    await eventually(
        lambda: default.worker.jobs_complete == 3, "the worker to pass the deleted job"
    )
    assert backend.count("seg") == 2 + 10
    assert (await api.get(f"/api/jobs/{second['id']}")).status_code == 404  # still gone

    # Both finished jobs gave their slots back by themselves: the client has room for another
    # upload while they are still there. Deleting everything leaves nothing behind.
    later = await create_job(api, [("later.png", make_png((200, 100)))])
    assert (await poll_until_finished(api, later["id"]))[-1]["status"] == "done"
    for job in (first, third, later):
        assert (await api.delete(f"/api/jobs/{job['id']}")).status_code == 204
    await assert_nothing_left(stack)


async def test_the_stack_runs_on_the_redis_the_environment_names(make_stack: MakeStack) -> None:
    """A run that is meant to use a real Redis must not quietly use the fake one."""
    stack = await make_stack(FakeBackend())
    version = await stack.redis_backend.version(stack.redis)
    if os.environ.get(REDIS_URL_ENV):
        assert version is not None and version[0].isdigit()
    else:
        assert version is None
    ready = await stack.api.get("/readyz")  # Redis answers and the data directory is writable
    assert (ready.status_code, ready.json()) == (200, {"status": "ready"})
