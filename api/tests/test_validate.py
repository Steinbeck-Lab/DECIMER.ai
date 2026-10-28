"""POST /api/jobs/{id}/results/{n}/validate."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from decimer_api.chemistry import analyse_smiles
from decimer_api.models import new_job_id
from decimer_api.names import NameInfo
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.cdk_molblocks import ALANINE_MOLBLOCK
from tests.conftest import FakeClock
from tests.results import (
    FakeNamer,
    make_cdk_only_result,
    make_done_job,
    molfile_of,
    name_of,
    no_names,
)

PENTAVALENT_CARBON = """
  RDKit          2D

  6  5  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
   -1.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000    1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.0000   -1.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    0.5000    0.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  1  3  1  0
  1  4  1  0
  1  5  1  0
  1  6  1  0
M  END
"""
EMPTY_CANVAS = "\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"


async def validate(client: httpx.AsyncClient, job_id: str, n: int, molfile: str) -> httpx.Response:
    return await client.post(f"/api/jobs/{job_id}/results/{n}/validate", json={"molfile": molfile})


@pytest.fixture
async def job_id(store: JobStore, settings: Settings, clock: FakeClock) -> str:
    return (await make_done_job(store, settings, clock)).id


# --- what it computes ----------------------------------------------------------------------------


async def test_an_edited_structure_gets_the_values_the_worker_would_compute(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    response = await validate(client, job_id, 0, molfile_of("CCCl"))
    assert response.status_code == 200
    body = response.json()
    svg = body.pop("svg")
    assert body == {
        "smiles": "CCCl",
        "inchikey": analyse_smiles("CCCl").inchikey,
        "valid": True,
        "name": name_of("CCCl"),
        "nameTier": "valid",
        "nameVerified": "opsin",
    }
    # An inline SVG (no XML prolog) of this structure: chlorine is drawn green.
    assert svg.startswith("<svg") and "#00CC00" in svg
    assert namer.calls == [["CCCl"]]  # the name is asked for the canonical SMILES of the edit
    assert response.headers["cache-control"] == "no-store"


async def test_the_answer_is_about_the_molfile_not_about_the_stored_result(
    client: httpx.AsyncClient, job_id: str
) -> None:
    """Result 0 is ethanol; the molfile sent is benzene."""
    body = (await validate(client, job_id, 0, molfile_of("c1ccccc1"))).json()
    assert body["smiles"] == "c1ccccc1"
    assert body["inchikey"] == analyse_smiles("c1ccccc1").inchikey


async def test_n_is_the_zero_based_index_and_any_existing_result_will_do(
    client: httpx.AsyncClient, job_id: str
) -> None:
    for n in range(5):
        assert (await validate(client, job_id, n, molfile_of("CCO"))).status_code == 200
    assert (await validate(client, job_id, 5, molfile_of("CCO"))).status_code == 404


async def test_a_molfile_that_is_not_a_valid_structure_is_valid_false_without_asking_chem(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    """`valid: false` is RDKit's word: no SMILES, no InChIKey, no name. But the drawing is the
    molfile as it is, drawn without sanitizing (as the worker draws what only CDK accepted): an
    edit of a still hypervalent structure must not make the tile blank ("No depiction") just
    because RDKit will not call the structure valid. (This answer used to have `svg: ""`.)"""
    body = (await validate(client, job_id, 0, PENTAVALENT_CARBON)).json()
    svg = body.pop("svg")
    assert body == {
        "smiles": "",
        "inchikey": "",
        "valid": False,
        "name": None,
        "nameTier": None,
        "nameVerified": None,
    }
    assert svg.startswith("<svg") and "<?xml" not in svg
    assert svg.count("class='bond-") == 5  # the five bonds of the molfile, as drawn
    assert namer.calls == []


# --- a structure that only CDK accepted ---------------------------------------------


async def test_the_stored_molfile_of_a_cdk_only_result_goes_through_validate(
    client: httpx.AsyncClient,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    namer: FakeNamer,
) -> None:
    """The editor opens the stored molfile (CDK's text) and writes its own back. Both are accepted
    by the gate; RDKit, which refuses this structure, answers `valid: false` for the very text the
    worker stored, and without asking chem for a name. There is no CDK fallback here: validate
    answers for the molfile, in RDKit's words."""
    cdk_only = make_cdk_only_result()
    job = await make_done_job(store, settings, clock, [cdk_only], ip="cdk")
    response = await validate(client, job.id, 0, job.results[0].molfile)
    assert response.status_code == 200
    body = response.json()
    assert body.pop("svg").startswith("<svg")  # drawn, as the tile of the result is
    assert body == {
        "smiles": "",
        "inchikey": "",
        "valid": False,
        "name": None,
        "nameTier": None,
        "nameVerified": None,
    }
    assert namer.calls == []


async def test_an_edit_that_leaves_nothing_on_the_canvas_has_no_drawing_either(
    client: httpx.AsyncClient, job_id: str
) -> None:
    body = (await validate(client, job_id, 0, EMPTY_CANVAS)).json()
    assert (body["valid"], body["svg"]) == (False, "")


async def test_a_molblock_as_cdk_writes_it_is_read_like_any_other(
    client: httpx.AsyncClient, job_id: str
) -> None:
    """CDK's blank title line and its timestamp line pass the gate and RDKit."""
    body = (await validate(client, job_id, 0, ALANINE_MOLBLOCK)).json()
    assert body["valid"] is True and body["smiles"] == "C[C@H](N)C(=O)O"


async def test_an_emptied_canvas_is_valid_false(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    response = await validate(client, job_id, 0, EMPTY_CANVAS)
    assert response.status_code == 200
    assert response.json()["valid"] is False and response.json()["smiles"] == ""
    assert namer.calls == []


async def test_an_r_group_is_valid_and_has_no_name_and_no_inchikey(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    body = (await validate(client, job_id, 0, molfile_of("[R1]CC"))).json()
    assert body["valid"] is True and "*" in body["smiles"]
    assert body["inchikey"] == "" and body["name"] is None
    assert namer.calls == []


async def test_a_v3000_molfile_is_read(client: httpx.AsyncClient, job_id: str) -> None:
    from rdkit import Chem

    v3000 = Chem.MolToV3KMolBlock(Chem.MolFromSmiles("Oc1ccccc1"))
    body = (await validate(client, job_id, 0, v3000)).json()
    assert body["valid"] is True and body["smiles"] == "Oc1ccccc1"


async def test_crlf_line_breaks_are_fine(client: httpx.AsyncClient, job_id: str) -> None:
    body = (await validate(client, job_id, 0, molfile_of("CCO").replace("\n", "\r\n"))).json()
    assert body["smiles"] == "CCO"


# --- the name is a bonus -------------------------------------------------------------------------


async def test_without_chem_the_name_is_null_and_the_rest_is_there(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    namer.answer = no_names
    body = (await validate(client, job_id, 0, molfile_of("CCO"))).json()
    assert body["valid"] is True and body["smiles"] == "CCO"
    assert (body["name"], body["nameTier"], body["nameVerified"]) == (None, None, None)


async def test_a_name_that_chem_declines_is_null(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    namer.answer = lambda _smiles: NameInfo()
    assert (await validate(client, job_id, 0, molfile_of("CCO"))).json()["name"] is None


async def test_the_default_app_has_no_chem_and_says_so_with_nulls(
    settings: Settings, store: JobStore, enqueue: object, clock: FakeClock
) -> None:
    """No CHEM_URL: `create_app` builds the namer that never connects anywhere (and, with the
    default analysis, the real pool of molfile-reading children: closed again at the end)."""
    from decimer_api.analysis_pool import AnalysisPool
    from decimer_api.app import create_app
    from decimer_api.names import NoNames

    app = create_app(settings, store, enqueue)  # type: ignore[arg-type]
    assert isinstance(app.state.deps.namer, NoNames)
    assert isinstance(app.state.deps.analysis, AnalysisPool)
    job = await make_done_job(store, settings, clock)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.7", 1))
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            body = (await validate(client, job.id, 0, molfile_of("CCO"))).json()
    finally:
        await app.state.deps.analysis.aclose()
    assert body["valid"] is True and body["name"] is None


# --- refusals ------------------------------------------------------------------------------------


async def test_an_unknown_job_is_404(client: httpx.AsyncClient) -> None:
    response = await validate(client, new_job_id(), 0, molfile_of("CCO"))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


@pytest.mark.parametrize("job", ["..", "x", "A" * 32, "0" * 33, "%2e%2e"])
async def test_a_malformed_job_id_is_404(client: httpx.AsyncClient, job: str) -> None:
    assert (await validate(client, job, 0, molfile_of("CCO"))).status_code == 404


async def test_an_expired_job_is_404(
    client: httpx.AsyncClient, job_id: str, clock: FakeClock
) -> None:
    clock.advance(3601)
    assert (await validate(client, job_id, 0, molfile_of("CCO"))).status_code == 404


async def test_a_job_that_has_no_results_has_no_result_n(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    empty = await make_done_job(store, settings, clock, results=[], ip="other")
    assert (await validate(client, empty.id, 0, molfile_of("CCO"))).status_code == 404


@pytest.mark.parametrize("n", ["-1", "abc", "1.5", "0x1"])
async def test_a_result_number_that_is_not_an_index_is_invalid_request(
    client: httpx.AsyncClient, job_id: str, n: str
) -> None:
    response = await client.post(
        f"/api/jobs/{job_id}/results/{n}/validate", json={"molfile": molfile_of("CCO")}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "molfile",
    [
        "",
        "this is not a molfile",
        "title\n\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n",  # counts line lies
        "no end line\n\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\n",
        EMPTY_CANVAS.replace("\n", "\x00\n", 1),  # control character
        "\ud800\nM  END\n",  # a lone surrogate, which JSON lets through and a file cannot hold
        EMPTY_CANVAS.replace("Ketcher", "$$$$"),  # would end an SDF record
    ],
)
async def test_text_that_is_not_a_molfile_is_422(
    client: httpx.AsyncClient, job_id: str, molfile: str
) -> None:
    response = await client.post(
        f"/api/jobs/{job_id}/results/0/validate",
        content=json.dumps({"molfile": molfile}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"molfile": 5},
        {"molfile": None},
        {"molfile": "x", "extra": 1},
        [],
        "just a string",
    ],
)
async def test_a_body_of_the_wrong_shape_is_422(
    client: httpx.AsyncClient, job_id: str, body: object
) -> None:
    response = await client.post(f"/api/jobs/{job_id}/results/0/validate", json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


async def test_a_body_that_is_not_json_is_422(client: httpx.AsyncClient, job_id: str) -> None:
    response = await client.post(
        f"/api/jobs/{job_id}/results/0/validate",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422


# --- size ----------------------------------------------------------------------------------------


def padded(limit: int, extra: int) -> str:
    """A molfile of exactly `limit + extra` bytes: ethanol, with a long title line."""
    base = molfile_of("CCO").lstrip("\n")
    filler = "x" * (limit + extra - len(base) - 1)
    return f"{filler}\n{base}"


async def test_a_molfile_over_the_limit_is_413_and_one_at_the_limit_is_not(
    client: httpx.AsyncClient, job_id: str, settings: Settings
) -> None:
    limit = settings.max_molfile_bytes
    at_limit = padded(limit, 0)
    assert len(at_limit.encode()) == limit
    assert (await validate(client, job_id, 0, at_limit)).status_code == 200
    over = await validate(client, job_id, 0, padded(limit, 1))
    assert over.status_code == 413
    assert over.json()["detail"]["code"] == "too_large"


async def test_a_declared_body_over_the_cap_is_refused_before_it_is_read(
    client: httpx.AsyncClient, job_id: str, settings: Settings
) -> None:
    body = json.dumps({"molfile": "x" * (4 * settings.max_molfile_bytes)})
    response = await client.post(
        f"/api/jobs/{job_id}/results/0/validate",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


async def test_a_body_with_no_length_is_cut_off_at_the_cap(
    client: httpx.AsyncClient, job_id: str, settings: Settings
) -> None:
    """Chunked, so there is no Content-Length to check; the stream is what stops it."""
    sent = 0

    async def body() -> object:
        nonlocal sent
        yield b'{"molfile": "'
        for _ in range(200):
            sent += 1
            yield b"x" * 4096
        yield b'"}'

    response = await client.post(
        f"/api/jobs/{job_id}/results/0/validate",
        content=body(),  # type: ignore[arg-type]
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert sent * 4096 < 2 * settings.max_molfile_bytes + 1024 + 4096  # it stopped reading


# --- rate limit ----------------------------------------------------------------------------------


async def test_a_client_over_its_rate_is_429_with_retry_after_and_others_are_not(
    make_client: object, job_id: str, settings: Settings, clock: FakeClock
) -> None:
    limit = settings.validate_rate_per_minute
    one = make_client("203.0.113.1")  # type: ignore[operator]
    other = make_client("203.0.113.2")  # type: ignore[operator]
    mol = molfile_of("CCO")
    for _ in range(limit):
        assert (await validate(one, job_id, 0, mol)).status_code == 200
    refused = await validate(one, job_id, 0, mol)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["retry-after"]) <= 61
    assert (await validate(other, job_id, 0, mol)).status_code == 200
    clock.advance(61)  # the window is over
    assert (await validate(one, job_id, 0, mol)).status_code == 200


async def test_requests_that_fail_still_count(
    client: httpx.AsyncClient, job_id: str, settings: Settings
) -> None:
    """Refusals are not free: probing with bad molfiles uses the same allowance."""
    for _ in range(settings.validate_rate_per_minute):
        assert (await validate(client, job_id, 0, "junk")).status_code == 422
    assert (await validate(client, job_id, 0, molfile_of("CCO"))).status_code == 429


async def test_the_rate_is_per_kind_of_request(
    client: httpx.AsyncClient, job_id: str, settings: Settings
) -> None:
    for _ in range(settings.validate_rate_per_minute):
        await validate(client, job_id, 0, molfile_of("CCO"))
    assert (await validate(client, job_id, 0, molfile_of("CCO"))).status_code == 429
    assert (
        await client.post(f"/api/jobs/{job_id}/export", json={"format": "csv"})
    ).status_code == 200


# --- chem misbehaving never makes it an error ----------------------------------------------------


async def test_a_namer_that_is_slow_does_not_hold_the_answer_forever(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    """The namer's own timeout is tested in test_names; here the route just waits for it."""
    namer.delay = 0.01
    assert (await validate(client, job_id, 0, molfile_of("CCO"))).status_code == 200


async def test_concurrent_validations_are_independent(
    client: httpx.AsyncClient, job_id: str
) -> None:
    # As many at once as there are places (MAX_CONCURRENT_ANALYSES, 2): a third would be 503 busy.
    mols = {"CCO": molfile_of("CCO"), "CCCl": molfile_of("CCCl")}
    answers = await asyncio.gather(*(validate(client, job_id, 0, m) for m in mols.values()))
    assert [a.json()["smiles"] for a in answers] == list(mols)


async def test_a_client_that_hangs_up_mid_body_is_not_an_internal_error(
    app: object, job_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Driven at the ASGI level: the body starts, then the connection goes."""
    import logging

    sent: list[dict[str, object]] = []
    messages = iter(
        [
            {"type": "http.request", "body": b'{"molfile": "abc', "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def receive() -> dict[str, object]:
        return next(messages)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": f"/api/jobs/{job_id}/results/0/validate",
        "raw_path": f"/api/jobs/{job_id}/results/0/validate".encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("203.0.113.9", 1),
        "server": ("api", 80),
    }
    with caplog.at_level(logging.ERROR, logger="decimer_api"):
        await app(scope, receive, send)  # type: ignore[operator]
    assert caplog.records == []  # no "unhandled error"
    assert sent[0]["status"] == 400


async def test_a_body_declared_too_large_is_refused_without_reading_any_of_it(
    app: object, job_id: str, settings: Settings
) -> None:
    """The Content-Length is enough: the app never asks for the body (the stream limit is the
    second line of defence, for a body that lies or has no length)."""
    reads = 0

    async def receive() -> dict[str, object]:
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"x", "more_body": True}

    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    declared = str(10 * settings.max_molfile_bytes).encode()
    path = f"/api/jobs/{job_id}/results/0/validate"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), (b"content-length", declared)],
        "client": ("203.0.113.9", 1),
        "server": ("api", 80),
    }
    await app(scope, receive, send)  # type: ignore[operator]
    assert sent[0]["status"] == 413
    assert reads == 0


# --- the real chem client, misbehaving, through the routes ---------------------------------------


@pytest.fixture
async def slow_chem_app(settings: Settings, store: JobStore, enqueue: object) -> object:
    """An app whose namer is the real `ChemNamer` over a fake chem slower than its budget."""
    from decimer_api.analysis_pool import InProcessAnalysis
    from decimer_api.app import create_app
    from decimer_api.names import ChemNamer
    from decimer_api.worker.services import make_services
    from decimer_api.worker.settings import WorkerSettings
    from tests.worker.fakes import FakeBackend, no_dns

    backend = FakeBackend(names={"CCO": ("ethanol", "valid")}, service_delays={"chem": 5.0})
    http = httpx.AsyncClient(transport=backend.transport)
    worker = WorkerSettings(
        chem_url="http://chem.test",
        chem_timeout_seconds=0.1,
        connect_retry_budget_seconds=0,
        busy_retries=0,
        sent_retries=0,
    )
    namer = ChemNamer(make_services(http, worker, resolver=no_dns), timeout_seconds=0.1, http=http)
    app = create_app(
        settings,
        store,
        enqueue,  # type: ignore[arg-type]
        namer=namer,
        analysis=InProcessAnalysis(2),
    )
    yield app
    await namer.aclose()


async def test_a_slow_chem_makes_validate_answer_without_a_name_not_with_an_error(
    slow_chem_app: object, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    import time

    job = await make_done_job(store, settings, clock)
    transport = httpx.ASGITransport(app=slow_chem_app, client=("203.0.113.8", 1))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        started = time.monotonic()
        response = await validate(client, job.id, 0, molfile_of("CCO"))
        took = time.monotonic() - started
        assert response.status_code == 200
        assert response.json()["valid"] is True and response.json()["smiles"] == "CCO"
        assert response.json()["name"] is None
        assert took < 2  # the budget, not chem's five seconds
        # and the same for an export that has to name an edit
        exported = await client.post(
            f"/api/jobs/{job.id}/export",
            json={"format": "csv", "molfiles": {"0": molfile_of("CCO")}},
        )
        assert exported.status_code == 200
        assert exported.text.splitlines()[1].split(",")[4] == ""  # the name column


# --- a molfile that is cheap to write and ruinous to read (review finding 1) ----------------------


@pytest.mark.parametrize("count", [44, 60])
async def test_a_molfile_that_would_cost_seconds_and_gigabytes_is_422_before_it_is_read(
    client: httpx.AsyncClient,
    job_id: str,
    app: object,
    count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rdkit import Chem

    from tests.molfiles import RdkitWasAsked, complete_graph_of_dummies

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        # A BaseException, so that no `except Exception` on the way can turn it into a refusal.
        raise RdkitWasAsked("RDKit was asked to read a molfile the gate should have refused")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    text = complete_graph_of_dummies(count)
    assert len(text.encode()) < 65536  # inside the byte cap
    started = time.monotonic()
    response = await validate(client, job_id, 0, text)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_request"
    assert time.monotonic() - started < 0.5  # not 5.7 s, not 46 s
    assert app.state.deps.analysis.capacity.in_use == 0  # type: ignore[attr-defined]


async def test_a_normal_drug_sized_molfile_still_validates(
    client: httpx.AsyncClient, job_id: str
) -> None:
    from tests.molfiles import drug_like

    body = (await validate(client, job_id, 0, drug_like(100))).json()
    assert body["valid"] is True and body["smiles"]


async def test_validate_asks_for_the_name_without_waiting_for_a_busy_chem(
    client: httpx.AsyncClient, job_id: str, namer: FakeNamer
) -> None:
    """The route does not ask the namer to be patient: a busy chem means no name, at once."""
    await validate(client, job_id, 0, molfile_of("CCO"))
    assert namer.patient == [False]
