"""A prediction that stopped at the model's token limit: ocsr answers 200 with the cut-off
SMILES and `incomplete: true` (it used to refuse with a 422). The worker keeps it, as the earlier
Laravel version of decimer.ai did, sends it through the same RDKit and CDK checks as any other
SMILES, and puts `incomplete` on the result. No name is looked up for it, and the log says so
without writing the SMILES."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import pytest

from decimer_api.models import Job, JobStatus
from decimer_api.worker.services import OcsrAnswer
from tests.cdk_molblocks import PENTAVALENT_MOLBLOCK, PENTAVALENT_SMILES
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, crop_id, json_response

RigFactory = Callable[..., Rig]
CHEM = {"chem_url": "http://chem.test"}
CHAIN = "CCCCCCCCCCCC"  # a cut-off chain: RDKit reads it
UNCLOSED = "c1ccccc"  # a cut-off ring: neither RDKit nor CDK reads it


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def ocsr_says(
    *answers: tuple[str, bool | None],
) -> Callable[[httpx.Request, dict[str, bytes]], httpx.Response]:
    """An ocsr hook that gives `answers[k]` = (SMILES, incomplete) to crop k of page 0. `None` for
    the flag leaves the field out, as an ocsr that predates it does."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        _page, index = crop_id(parts["image"])
        smiles, incomplete = answers[index]
        body: dict[str, object] = {
            "smiles": smiles,
            "token_confidence": [],
            "mean_confidence": 0.8,
        }
        if incomplete is not None:
            body["incomplete"] = incomplete
        return json_response(body)

    return hook


async def one_run(
    make_rig: RigFactory, *answers: tuple[str, bool | None], **backend: object
) -> tuple[Rig, FakeBackend, Job]:
    fake = FakeBackend(layout={0: len(answers)}, hooks={"ocsr": ocsr_says(*answers)}, **backend)  # type: ignore[arg-type]
    rig = make_rig(backend=fake, ocsr_concurrency=1, **CHEM)
    return rig, fake, await rig.run(await rig.pdf_job(1))


# --- what ocsr's answer says ----------------------------------------------------------------------


def test_the_answer_of_ocsr_has_incomplete_and_defaults_to_false() -> None:
    assert OcsrAnswer.model_validate({"smiles": "C", "mean_confidence": 0.5}).incomplete is False
    answer = OcsrAnswer.model_validate({"smiles": "C", "mean_confidence": 0.5, "incomplete": True})
    assert answer.incomplete is True


# --- the result -----------------------------------------------------------------------------------


async def test_an_incomplete_prediction_is_kept_and_flagged(make_rig: RigFactory) -> None:
    _rig, _backend, job = await one_run(make_rig, ("CCO", False), (CHAIN, True))
    assert job.status is JobStatus.DONE
    kept, cut_off = job.results
    assert (kept.incomplete, kept.smiles) == (False, "CCO")
    assert cut_off.incomplete is True
    assert cut_off.smiles == CHAIN  # the text ocsr sent, not an empty result
    assert cut_off.confidence == pytest.approx(0.8)


async def test_the_flag_is_false_when_ocsr_does_not_send_it(make_rig: RigFactory) -> None:
    _rig, _backend, job = await one_run(make_rig, ("CCO", None), ("CCN", False))
    assert [r.incomplete for r in job.results] == [False, False]


async def test_an_incomplete_prediction_is_no_failure_and_gives_no_warning(
    make_rig: RigFactory,
) -> None:
    _rig, _backend, job = await one_run(make_rig, (UNCLOSED, True), (CHAIN, True))
    assert job.status is JobStatus.DONE
    assert codes(job) == []  # not recognition_failed: the structure was read, as far as it went


async def test_the_flag_reaches_the_json_the_client_reads(make_rig: RigFactory) -> None:
    from decimer_api.models import JobView

    _rig, _backend, job = await one_run(make_rig, ("CCO", False), (CHAIN, True))
    view = JobView.from_job(job).model_dump(by_alias=True)
    assert [r["incomplete"] for r in view["results"]] == [False, True]


# --- RDKit, then CDK, unchanged -------------------------------------------------------------------


async def test_a_cut_off_smiles_rdkit_reads_is_valid_by_rdkit(make_rig: RigFactory) -> None:
    _rig, _backend, job = await one_run(make_rig, (CHAIN, True))
    (result,) = job.results
    assert (result.valid, result.valid_by, result.incomplete) == (True, "rdkit", True)
    assert result.inchikey and result.molfile and result.svg


async def test_a_cut_off_smiles_only_cdk_reads_keeps_valid_by_cdk(make_rig: RigFactory) -> None:
    _rig, backend, job = await one_run(
        make_rig, (PENTAVALENT_SMILES, True), cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK}
    )
    (result,) = job.results
    assert backend.chem_batches("/cdk") == [[PENTAVALENT_SMILES]]  # it went the usual way
    assert (result.valid, result.valid_by, result.incomplete) == (False, "cdk", True)
    assert result.smiles == PENTAVALENT_SMILES
    assert result.molfile and result.inchikey == ""


async def test_a_cut_off_smiles_nobody_reads_stays_invalid_but_keeps_its_text(
    make_rig: RigFactory,
) -> None:
    _rig, backend, job = await one_run(make_rig, (UNCLOSED, True))
    (result,) = job.results
    assert backend.chem_batches("/cdk") == [[UNCLOSED]]
    assert (result.valid, result.valid_by, result.incomplete) == (False, None, True)
    assert result.smiles == UNCLOSED  # an invalid result keeps the raw text, as ever
    assert (result.inchikey, result.molfile, result.svg) == ("", "", "")


async def test_a_complete_prediction_refused_by_rdkit_is_not_flagged(make_rig: RigFactory) -> None:
    _rig, _backend, job = await one_run(make_rig, (UNCLOSED, False))
    assert (job.results[0].valid, job.results[0].incomplete) == (False, False)


async def test_the_fragment_rule_still_applies_to_a_cut_off_smiles(make_rig: RigFactory) -> None:
    _rig, _backend, job = await one_run(make_rig, (CHAIN + ".C.C.C.C", True))
    (result,) = job.results
    assert (result.smiles, result.incomplete) == (CHAIN, True)
    assert codes(job) == ["fragments_trimmed"]


# --- names ----------------------------------------------------------------------------------------


async def test_no_name_is_asked_for_an_incomplete_structure(make_rig: RigFactory) -> None:
    _rig, backend, job = await one_run(
        make_rig,
        ("CCO", False),
        (CHAIN, True),
        names={"CCO": ("ethanol", "pin_verified"), CHAIN: ("dodecane", "pin_verified")},
    )
    complete, cut_off = job.results
    assert backend.chem_batches("/names") == [["CCO"]]  # the cut-off one is not even asked about
    assert complete.name == "ethanol"
    assert (cut_off.valid, cut_off.name, cut_off.name_tier, cut_off.name_verified) == (
        True,
        None,
        None,
        None,
    )


async def test_a_job_of_incomplete_structures_alone_calls_chem_for_no_names(
    make_rig: RigFactory,
) -> None:
    _rig, backend, job = await one_run(make_rig, (CHAIN, True))
    assert backend.chem_batches("/names") == []
    assert codes(job) == []


# --- the log --------------------------------------------------------------------------------------


async def test_the_log_names_the_structure_and_never_its_smiles(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="decimer_api.worker"):
        _rig, _backend, job = await one_run(make_rig, ("CCO", False), (CHAIN, True))
    lines = [r.getMessage() for r in caplog.records if "token limit" in r.getMessage()]
    assert lines == [
        f"job {job.id}: structure 2: the prediction stopped at the token limit (incomplete)"
    ]
    assert CHAIN not in caplog.text


async def test_a_complete_prediction_adds_nothing_about_the_token_limit_to_the_log(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="decimer_api.worker"):
        await one_run(make_rig, ("CCO", False))
    assert "token limit" not in caplog.text


# --- the schema the web types are generated from --------------------------------------------------


async def test_the_openapi_schema_has_incomplete_as_an_optional_boolean(
    client: httpx.AsyncClient,
) -> None:
    schemas = (await client.get("/api/openapi.json")).json()["components"]["schemas"]
    for name in ("Result", "ResultView"):
        if name in schemas:
            assert schemas[name]["properties"]["incomplete"]["type"] == "boolean"
            assert schemas[name]["properties"]["incomplete"]["default"] is False
            assert "incomplete" not in schemas[name].get("required", [])
    assert "ResultView" in schemas
