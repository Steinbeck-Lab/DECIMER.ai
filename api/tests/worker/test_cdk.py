"""The CDK fallback: the SMILES RDKit refuses go to chem's `POST /cdk` in one
batch per job; a structure only CDK accepts is a result with `valid: false`, `validBy: "cdk"`,
CDK's molblock, RDKit's drawing of it and the SMILES as DECIMER wrote it. Never a failure of the
job."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import httpx
import pytest

from decimer_api.chemistry import analyse_smiles
from decimer_api.models import Job, JobStatus, JobWarning
from decimer_api.molfile import clean_molfile
from decimer_api.worker.pipeline import _reset
from tests.cdk_molblocks import (
    ALANINE_MOLBLOCK,
    ALANINE_SMILES,
    NINE_BONDS_MOLBLOCK,
    NINE_BONDS_SMILES,
    PENTAVALENT_MOLBLOCK,
    PENTAVALENT_SMILES,
)
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, crop_id, json_response, smiles_for

RigFactory = Callable[..., Rig]
CHEM = {"chem_url": "http://chem.test"}
UNCLOSED = "c1ccccc"  # RDKit and CDK both refuse an open ring


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def ocsr_says(*answers: str) -> Callable[..., httpx.Response]:
    """An ocsr hook that gives `answers[k]` to crop k of page 0, whatever the order of asking."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        _page, index = crop_id(parts["image"])
        return json_response(
            {"smiles": answers[index], "token_confidence": [], "mean_confidence": 0.8}
        )

    return hook


def chem_says(status: int, *, only: str | None = None) -> Callable[..., httpx.Response | None]:
    """A chem hook that answers every request (or those to `only`, a path) with `status`."""

    def hook(request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        if only is not None and request.url.path != only:
            return None
        return httpx.Response(status, headers={"Retry-After": "1"}, text="busy")

    return hook


def chem_times_out(request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
    if request.url.path == "/cdk":
        raise httpx.ReadTimeout("too slow", request=request)
    return None


# --- one batch per job ---------------------------------------------------------------------------


async def test_the_structures_rdkit_refuses_go_to_cdk_in_one_request(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 5},
        hooks={"ocsr": ocsr_says("CC", UNCLOSED, "CCO", PENTAVALENT_SMILES, "C1CC")},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    # One request for the whole job: the three refused SMILES, in result order, as written.
    assert backend.chem_batches("/cdk") == [[UNCLOSED, PENTAVALENT_SMILES, "C1CC"]]
    assert job.status is JobStatus.DONE
    assert codes(job) == []


async def test_more_than_twenty_refused_structures_go_in_chunks_of_twenty(
    make_rig: RigFactory,
) -> None:
    def refused(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        page, index = crop_id(parts["image"])
        return json_response(
            {
                "smiles": f"C1{smiles_for(page, index)}",
                "token_confidence": [],
                "mean_confidence": 0.8,
            }
        )

    backend = FakeBackend(layout={0: 15, 1: 10}, hooks={"ocsr": refused})
    rig = make_rig(api={"max_structures": 25}, backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(2))
    assert len(job.results) == 25
    batches = backend.chem_batches("/cdk")
    assert [len(batch) for batch in batches] == [20, 5]
    assert batches[1][0] == f"C1{smiles_for(1, 5)}"  # the chunks follow the result order
    assert backend.chem_batches("/names") == []  # nothing valid, nothing to name


async def test_every_structure_rdkit_accepts_never_reaches_cdk(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 3})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.chem_batches("/cdk") == []
    assert [(r.valid, r.valid_by) for r in job.results] == [(True, "rdkit")] * 3
    # What RDKit says is what the pipeline says without CDK, byte for byte.
    for k, result in enumerate(job.results):
        expected = analyse_smiles(smiles_for(0, k))
        assert (result.smiles, result.inchikey, result.molfile, result.svg) == (
            expected.smiles,
            expected.inchikey,
            expected.molfile,
            expected.svg,
        )


# --- what CDK's answer becomes -------------------------------------------------------------------


async def test_a_structure_only_cdk_accepts_has_its_molblock_a_drawing_and_valid_by_cdk(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(
        layout={0: 3},
        hooks={"ocsr": ocsr_says("CC", PENTAVALENT_SMILES, "CCO")},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
        names={"CC": ("ethane", "pin_verified"), "CCO": ("ethanol", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    first, only_cdk, last = job.results
    assert analyse_smiles(PENTAVALENT_SMILES).valid is False  # the premise: RDKit refuses it
    assert only_cdk.valid is False  # RDKit's word
    assert only_cdk.valid_by == "cdk"
    assert only_cdk.smiles == PENTAVALENT_SMILES  # as DECIMER wrote it
    assert only_cdk.inchikey == ""
    assert only_cdk.molfile == clean_molfile(PENTAVALENT_MOLBLOCK) == PENTAVALENT_MOLBLOCK
    assert only_cdk.svg.startswith("<svg") and "<path" in only_cdk.svg
    assert (only_cdk.name, only_cdk.name_tier, only_cdk.name_verified) == (None, None, None)
    # The ones RDKit accepted are as ever, and are RDKit's.
    assert [(r.valid, r.valid_by, r.name) for r in (first, last)] == [
        (True, "rdkit", "ethane"),
        (True, "rdkit", "ethanol"),
    ]
    # Orthonym needs RDKit's reading: a structure only CDK accepts is not asked about.
    assert backend.chem_batches("/names") == [["CC", "CCO"]]


async def test_the_drawing_of_a_cdk_only_structure_is_not_the_one_of_a_valid_structure(
    make_rig: RigFactory,
) -> None:
    """The SVG comes from the molblock: another molblock, another drawing."""
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(PENTAVALENT_SMILES, "C1CC")},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK, "C1CC": ALANINE_MOLBLOCK},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    first, second = job.results
    assert first.svg != second.svg
    assert (first.valid_by, second.valid_by) == ("cdk", "cdk")
    assert second.smiles == "C1CC" and second.molfile == ALANINE_MOLBLOCK


async def test_a_structure_rdkit_and_cdk_both_accept_stays_rdkits(make_rig: RigFactory) -> None:
    """CDK is asked only about what RDKit refused; the answer for RDKit's own structures is never
    used, whatever CDK would have said."""
    backend = FakeBackend(
        layout={0: 1},
        hooks={"ocsr": ocsr_says(ALANINE_SMILES)},
        cdk={ALANINE_SMILES: ALANINE_MOLBLOCK},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    (result,) = job.results
    assert backend.chem_batches("/cdk") == []
    assert (result.valid, result.valid_by) == (True, "rdkit")
    assert result.inchikey == analyse_smiles(ALANINE_SMILES).inchikey


@pytest.mark.parametrize("reason", ["invalid_smiles", "layout_failed", "too_large"])
async def test_a_structure_cdk_declines_stays_invalid_without_a_warning(
    make_rig: RigFactory, reason: str
) -> None:
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(UNCLOSED, PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
        cdk_reasons={UNCLOSED: reason},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    declined, accepted = job.results
    assert (declined.valid, declined.valid_by) == (False, None)
    assert declined.smiles == UNCLOSED
    assert (declined.inchikey, declined.molfile, declined.svg) == ("", "", "")
    assert accepted.valid_by == "cdk"
    assert codes(job) == []


@pytest.mark.parametrize("reason", ["timeout", "engine_error", "engine_unavailable"])
async def test_a_structure_cdk_could_not_decide_on_gives_one_warning_for_the_job(
    make_rig: RigFactory, reason: str
) -> None:
    backend = FakeBackend(
        layout={0: 3},
        hooks={"ocsr": ocsr_says(UNCLOSED, "C1CC", PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
        cdk_reasons={UNCLOSED: reason, "C1CC": reason},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert codes(job) == ["cdk_unavailable"]  # once, not once per structure
    assert [r.valid_by for r in job.results] == [None, None, "cdk"]  # the others are still tried
    assert job.status is JobStatus.DONE


@pytest.mark.parametrize(
    "molfile",
    [
        "this is not a molfile",  # no M  END
        "\n  CDK\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n",  # nothing in it
        PENTAVALENT_MOLBLOCK.replace("V2000", "V9999"),  # a version nobody reads
        PENTAVALENT_MOLBLOCK.replace("M  END", "$$$$\nM  END"),  # would end an SDF record early
    ],
)
async def test_a_molblock_that_cannot_be_stored_or_read_is_not_an_acceptance(
    make_rig: RigFactory, molfile: str
) -> None:
    backend = FakeBackend(
        layout={0: 1},
        hooks={"ocsr": ocsr_says(PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: molfile},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    (result,) = job.results
    assert (result.valid, result.valid_by, result.molfile, result.svg) == (False, None, "", "")
    assert codes(job) == ["cdk_unavailable"]  # chem sent something unusable: said once


async def test_a_molblock_our_own_gate_refuses_for_its_size_is_a_decline_not_a_warning(
    make_rig: RigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """CDK read `C[S](C)(C)(C)(C)(C)(C)(C)C` (nine bonds at the sulfur) and laid it out; the gate
    that every stored molfile passes (at most 8 bonds at one atom) will not take it. Chem did
    nothing wrong: the structure stays invalid, quietly (debug log only)."""
    assert analyse_smiles(NINE_BONDS_SMILES).valid is False
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(NINE_BONDS_SMILES, PENTAVALENT_SMILES)},
        cdk={NINE_BONDS_SMILES: NINE_BONDS_MOLBLOCK, PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    with caplog.at_level("DEBUG", logger="decimer_api.worker"):
        job = await rig.run(await rig.pdf_job(1))
    too_big, accepted = job.results
    assert (too_big.valid, too_big.valid_by, too_big.molfile, too_big.svg) == (False, None, "", "")
    assert accepted.valid_by == "cdk"  # the next structure is not affected
    assert codes(job) == []
    assert not [r for r in caplog.records if r.levelno >= 30 and "CDK" in r.getMessage()]


async def test_chem_answering_with_the_wrong_number_of_results_is_one_warning(
    make_rig: RigFactory,
) -> None:
    """Not a crash of the job (`zip(strict=True)` over a short answer would be `internal_error`)."""

    def short(request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        if request.url.path != "/cdk":
            return None
        return json_response({"results": [{"molfile": PENTAVALENT_MOLBLOCK, "reason": None}]})

    backend = FakeBackend(
        layout={0: 3},
        hooks={"ocsr": ocsr_says(UNCLOSED, PENTAVALENT_SMILES, "CC"), "chem": short},
        names={"CC": ("ethane", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert codes(job) == ["cdk_unavailable"]
    assert [r.valid_by for r in job.results] == [None, None, "rdkit"]  # nothing taken from it
    assert job.results[2].name == "ethane"


async def test_chem_answering_with_too_many_results_is_one_warning_too(
    make_rig: RigFactory,
) -> None:
    def long(request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        if request.url.path != "/cdk":
            return None
        return json_response({"results": [{"molfile": None, "reason": "invalid_smiles"}] * 3})

    backend = FakeBackend(layout={0: 1}, hooks={"ocsr": ocsr_says(UNCLOSED), "chem": long})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert (job.status, codes(job)) == (JobStatus.DONE, ["cdk_unavailable"])


async def test_the_smiles_is_the_same_text_everywhere_whitespace_around_it_is_dropped_once(
    make_rig: RigFactory,
) -> None:
    """The text filtered, sent to chem, read back with its molblock and stored in the result is
    the same: what ocsr wrote, without the spaces and line breaks around it (a result that only
    CDK accepted shows the SMILES CDK was given)."""
    padded = f"  {PENTAVALENT_SMILES}\n"
    backend = FakeBackend(
        layout={0: 1},
        hooks={"ocsr": ocsr_says(padded)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.chem_batches("/cdk") == [[PENTAVALENT_SMILES]]
    (result,) = job.results
    assert result.valid_by == "cdk"
    assert result.smiles == PENTAVALENT_SMILES


# --- when chem is not there ----------------------------------------------------------------------


async def test_a_busy_chem_gives_one_warning_and_the_job_goes_on(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 3},
        hooks={
            "ocsr": ocsr_says(UNCLOSED, "CC", PENTAVALENT_SMILES),
            "chem": chem_says(503, only="/cdk"),
        },
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
        names={"CC": ("ethane", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert codes(job) == ["cdk_unavailable"]
    assert [(r.valid, r.valid_by) for r in job.results] == [
        (False, None),
        (True, "rdkit"),
        (False, None),
    ]
    assert job.results[1].name == "ethane"  # the names do not depend on CDK
    # busy_retries (3) more tries of the one batch, then it is given up: not one call per structure.
    assert len(backend.chem_batches("/cdk")) == 4


async def test_a_cdk_that_times_out_gives_one_warning(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(UNCLOSED, PENTAVALENT_SMILES), "chem": chem_times_out},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert codes(job) == ["cdk_unavailable"]
    assert [r.valid_by for r in job.results] == [None, None]
    assert len(backend.chem_batches("/cdk")) == 1  # a timeout is not retried


async def test_after_a_timeout_from_cdk_the_names_are_not_asked_for(make_rig: RigFactory) -> None:
    """A read timeout means chem is hung: a call for the names would wait as long again. The job
    goes on without names, with one warning for them (after CDK's)."""
    backend = FakeBackend(
        layout={0: 3},
        hooks={"ocsr": ocsr_says(UNCLOSED, "CC", "CCO"), "chem": chem_times_out},
        names={"CC": ("ethane", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert backend.chem_batches("/names") == []
    assert codes(job) == ["cdk_unavailable", "names_unavailable"]
    assert [r.name for r in job.results] == [None, None, None]


async def test_after_a_timeout_from_cdk_with_nothing_to_name_there_is_no_names_warning(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(
        layout={0: 1}, hooks={"ocsr": ocsr_says(UNCLOSED), "chem": chem_times_out}
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert codes(job) == ["cdk_unavailable"]


async def test_a_busy_chem_does_not_stop_the_names(make_rig: RigFactory) -> None:
    """Only a timeout says chem is hung; a 503 (busy) for CDK was already waited out and the names
    are tried."""
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(UNCLOSED, "CC"), "chem": chem_says(503, only="/cdk")},
        names={"CC": ("ethane", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert codes(job) == ["cdk_unavailable"]
    assert job.results[1].name == "ethane"


async def test_a_failed_batch_stops_the_following_ones_and_warns_once(make_rig: RigFactory) -> None:
    def refused(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        page, index = crop_id(parts["image"])
        return json_response(
            {
                "smiles": f"C1{smiles_for(page, index)}",
                "token_confidence": [],
                "mean_confidence": 0.8,
            }
        )

    backend = FakeBackend(layout={0: 15, 1: 10}, hooks={"ocsr": refused, "chem": chem_times_out})
    rig = make_rig(api={"max_structures": 25}, backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(2))
    assert codes(job) == ["cdk_unavailable"]
    assert (
        len(backend.chem_batches("/cdk")) == 1
    )  # the second chunk is not tried against a dead chem


async def test_the_cdk_warning_comes_before_the_names_warning(make_rig: RigFactory) -> None:
    """Both are raised by the check stage, CDK first (it is asked first)."""
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(UNCLOSED, "CC"), "chem": chem_says(500)},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert codes(job) == ["cdk_unavailable", "names_unavailable"]


def test_a_job_that_runs_again_starts_without_the_cdk_warning(
    clock: Callable[[], datetime],
) -> None:
    """The warnings of the worker are reset at the start of a run (arq runs an interrupted job
    again); the API's own, like the page limit, stay."""
    job = Job.create(
        job_id="a" * 32,
        client_ip_hash="x",
        pages=1,
        pages_processed=1,
        now=clock(),
        warnings=[
            JobWarning(code="pages_truncated", message="API's"),
            JobWarning(code="cdk_unavailable", message="worker's"),
        ],
    )
    _reset(job)
    assert codes(job) == ["pages_truncated"]


# --- the switches --------------------------------------------------------------------------------


async def test_with_the_flag_off_cdk_is_never_called(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 2},
        hooks={"ocsr": ocsr_says(UNCLOSED, PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, cdk_fallback=False, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.chem_batches("/cdk") == []
    assert [(r.valid, r.valid_by, r.molfile) for r in job.results] == [(False, None, "")] * 2
    assert codes(job) == []


async def test_without_chem_there_is_no_call_and_no_warning(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 1},
        hooks={"ocsr": ocsr_says(PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend)  # the flag is on by default, chem_url is not set
    job = await rig.run(await rig.pdf_job(1))
    assert backend.count("chem") == 0
    assert (job.results[0].valid_by, codes(job)) == (None, [])


async def test_a_prediction_chem_would_refuse_is_not_sent(make_rig: RigFactory) -> None:
    """chem answers 422 to the whole request for a SMILES with a space or one over 2000 characters;
    such a structure is invalid and is left out of the batch, the others go."""
    backend = FakeBackend(
        layout={0: 4},
        hooks={"ocsr": ocsr_says("C 1", "C1" + "C" * 2000, "", PENTAVALENT_SMILES)},
        cdk={PENTAVALENT_SMILES: PENTAVALENT_MOLBLOCK},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.chem_batches("/cdk") == [[PENTAVALENT_SMILES]]
    assert [r.valid_by for r in job.results] == [None, None, None, "cdk"]
    assert codes(job) == []


async def test_a_crop_ocsr_could_not_read_is_not_sent_to_cdk(make_rig: RigFactory) -> None:
    def broken(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        return httpx.Response(500, text="boom")

    backend = FakeBackend(layout={0: 1}, hooks={"ocsr": broken})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.chem_batches("/cdk") == []
    assert codes(job) == ["recognition_failed"]
