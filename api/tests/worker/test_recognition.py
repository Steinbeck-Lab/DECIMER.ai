"""What is done with ocsr's SMILES: legacy's fragment filter, R groups, then RDKit."""

from __future__ import annotations

import logging
from collections.abc import Callable

import httpx
import pytest
from rdkit import Chem

from decimer_api.models import Job, JobStatus
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, crop_id, json_response

RigFactory = Callable[..., Rig]


def ocsr_says(*answers: str) -> Callable[..., httpx.Response]:
    """An ocsr hook that gives `answers[k]` to crop k of page 0, whatever the order of asking."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        _page, index = crop_id(parts["image"])
        return json_response(
            {"smiles": answers[index], "token_confidence": [], "mean_confidence": 0.8}
        )

    return hook


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


async def one_run(make_rig: RigFactory, *answers: str) -> tuple[Rig, Job]:
    backend = FakeBackend(layout={0: len(answers)}, hooks={"ocsr": ocsr_says(*answers)})
    rig = make_rig(backend=backend, ocsr_concurrency=1)
    return rig, await rig.run(await rig.pdf_job(1))


# --- the fragment filter -------------------------------------------------------------------------


async def test_more_than_four_fragments_keeps_the_first(make_rig: RigFactory) -> None:
    long_first = "C" * 12 + "O"
    rig, job = await one_run(make_rig, f"{long_first}.C.C.C.C")
    (result,) = job.results
    assert result.smiles == long_first
    assert result.valid is True
    assert codes(job) == ["fragments_trimmed"]
    assert "Structure 1" in job.warnings[0].message


async def test_a_first_fragment_shorter_than_the_rest_keeps_the_first(make_rig: RigFactory) -> None:
    rig, job = await one_run(make_rig, "CCO.CCCCCC")
    assert job.results[0].smiles == "CCO"
    assert codes(job) == ["fragments_trimmed"]


async def test_a_prediction_the_filter_does_not_touch_is_left_as_it_is(
    make_rig: RigFactory,
) -> None:
    rig, job = await one_run(make_rig, "CCCCCCCC.O", "CCO", "[Na+].[Cl-]")
    assert [r.smiles for r in job.results] == [
        "CCCCCCCC.O",
        "CCO",
        "[Cl-].[Na+]",
    ]  # canonical order
    assert codes(job) == []


async def test_the_filter_runs_before_rdkit_so_the_inchikey_is_of_the_first_fragment(
    make_rig: RigFactory,
) -> None:
    rig, job = await one_run(make_rig, "OCC.CCCCCCCCCC")
    assert job.results[0].smiles == "CCO"
    assert job.results[0].inchikey == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"


async def test_the_raw_prediction_is_kept_in_the_debug_log(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG, logger="decimer_api.worker"):
        await one_run(make_rig, "CCO.CCCCCC")
    assert "CCO.CCCCCC" in caplog.text


async def test_the_raw_prediction_is_not_in_the_log_at_the_default_level(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Uploaded structures stay out of the ordinary log."""
    with caplog.at_level(logging.INFO, logger="decimer_api.worker"):
        await one_run(make_rig, "CCO.CCCCCC")
    assert "CCCCCC" not in caplog.text


async def test_a_trimmed_structure_that_is_invalid_is_still_invalid(make_rig: RigFactory) -> None:
    rig, job = await one_run(make_rig, "c1ccccc.CCCCCCCCCC")
    assert (job.results[0].valid, job.results[0].smiles) == (False, "c1ccccc")


# --- R groups -------------------------------------------------------------------------------------


async def test_a_numbered_r_group_is_a_valid_structure_with_an_r_atom_in_its_molfile(
    make_rig: RigFactory,
) -> None:
    rig, job = await one_run(make_rig, "[R1]CC(=O)O")
    (result,) = job.results
    assert result.valid is True
    assert result.smiles == "[1*]CC(=O)O"
    assert "*" in result.smiles  # the UI flags it as an R group from this
    assert result.inchikey == ""
    assert "R#" in result.molfile and "M  RGP" in result.molfile
    back = Chem.MolFromMolBlock(result.molfile)
    assert back is not None and Chem.MolToSmiles(back) == result.smiles
    assert codes(job) == []


async def test_a_bare_r_is_a_plain_dummy_atom(make_rig: RigFactory) -> None:
    rig, job = await one_run(make_rig, "[R]c1ccccc1")
    assert job.results[0].valid is True
    assert job.results[0].smiles == "*c1ccccc1"


async def test_an_r_group_structure_is_not_sent_for_naming(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 2}, hooks={"ocsr": ocsr_says("[R1]CC(=O)O", "CCO")})
    rig = make_rig(backend=backend, ocsr_concurrency=1, chem_url="http://chem.test")
    await rig.run(await rig.pdf_job(1))
    import json

    sent = [json.loads(p["json"])["smiles"] for p in backend.parts_of("chem")]
    assert sent == [["CCO"]]


async def test_a_prediction_with_r_groups_and_fragments_follows_both_rules(
    make_rig: RigFactory,
) -> None:
    rig, job = await one_run(make_rig, "[R1]CCCCCCCC.[R2].[R3].[R4].[R5]")
    assert job.results[0].smiles == "[1*]CCCCCCCC"
    assert codes(job) == ["fragments_trimmed"]
    assert job.status is JobStatus.DONE
