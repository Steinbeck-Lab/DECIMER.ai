"""IUPAC names: one batch per job to chem, never a failure of the job."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from decimer_api.models import Job, JobStatus
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, crop_id, json_response, smiles_for

RigFactory = Callable[..., Rig]
CHEM = {"chem_url": "http://chem.test"}


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def sent(backend: FakeBackend) -> list[list[str]]:
    """The SMILES of every batch sent to chem, in the order of the calls."""
    return [json.loads(parts["json"])["smiles"] for parts in backend.parts_of("chem")]


def ocsr_says(*answers: str) -> Callable[..., httpx.Response]:
    """An ocsr hook that gives `answers[k]` to crop k of page 0, whatever the order of asking."""

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        _page, index = crop_id(parts["image"])
        return json_response(
            {"smiles": answers[index], "token_confidence": [], "mean_confidence": 0.8}
        )

    return hook


async def test_all_the_names_of_a_job_come_from_one_request(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 5},
        names={"CC": ("ethane", "pin_verified"), "CCCC": ("butane", "pin_unverified")},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.count("chem") == 1
    assert sent(backend) == [[smiles_for(0, k) for k in range(5)]]
    assert [(r.name, r.name_tier) for r in job.results] == [
        ("ethane", "pin_verified"),
        (None, None),
        ("butane", "pin_unverified"),
        (None, None),
        (None, None),
    ]
    assert codes(job) == []


async def test_the_request_has_the_canonical_smiles_not_the_raw_prediction(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(layout={0: 1}, hooks={"ocsr": ocsr_says("OCC")})
    rig = make_rig(backend=backend, **CHEM)
    await rig.run(await rig.pdf_job(1))
    assert sent(backend) == [["CCO"]]


async def test_more_than_twenty_structures_are_asked_in_batches_of_twenty(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(layout={0: 15, 1: 10})
    rig = make_rig(api={"max_structures": 25}, backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(2))
    assert len(job.results) == 25
    assert [len(batch) for batch in sent(backend)] == [20, 5]
    assert sent(backend)[1][0] == smiles_for(1, 5)  # the batches follow the result order


async def test_only_structures_that_can_be_named_are_sent_and_the_names_find_their_result(
    make_rig: RigFactory,
) -> None:
    """Invalid SMILES and R groups are left out of the batch; the names that come back are
    matched to the right results, not to the first ones."""
    backend = FakeBackend(
        layout={0: 5},
        hooks={"ocsr": ocsr_says("CC", "c1ccccc", "[R1]CC(=O)O", "", "CCC")},
        names={"CC": ("ethane", "pin_verified"), "CCC": ("propane", "pin_verified")},
    )
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert sent(backend) == [["CC", "CCC"]]
    assert [r.name for r in job.results] == ["ethane", None, None, None, "propane"]
    assert [r.valid for r in job.results] == [True, False, True, False, True]


async def test_nothing_to_name_means_no_request(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 2}, hooks={"ocsr": ocsr_says("c1ccccc", "")})
    rig = make_rig(backend=backend, ocsr_concurrency=1, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.count("chem") == 0
    assert job.status is JobStatus.DONE


async def test_without_a_chem_url_there_is_no_request_and_no_warning(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 3})
    rig = make_rig(backend=backend)
    job = await rig.run(await rig.pdf_job(1))
    assert backend.count("chem") == 0
    assert [r.name for r in job.results] == [None] * 3
    assert codes(job) == []


async def test_a_tier_that_comes_without_a_name_is_dropped(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 1}, names={"CC": (None, "abstain")})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert (job.results[0].name, job.results[0].name_tier) == (None, None)


# --- reasons --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "UNNAMEABLE",
        "NO_VERIFIED_PIN",
        "UNSUPPORTED_ELEMENT",
        "STRUCTURE_TOO_LARGE",
        "no_verified_name",
        "tier_not_accepted",
    ],
)
async def test_a_decline_is_just_no_name(make_rig: RigFactory, reason: str) -> None:
    """These are Orthonym saying it has no verified name: the UI shows "no verified name"."""
    backend = FakeBackend(layout={0: 2}, reasons={"CC": reason, "CCC": reason})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [r.name for r in job.results] == [None, None]
    assert codes(job) == []  # not a problem worth a warning


async def test_a_timeout_inside_chem_is_no_name_and_one_warning(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 4},
        names={"CCC": ("propane", "pin_verified")},
        reasons={"CC": "timeout", "CCCC": "timeout", "CCCCC": "timeout"},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [r.name for r in job.results] == [None, "propane", None, None]  # the others are kept
    assert codes(job) == ["names_unavailable"]  # once, not once per structure
    assert job.status is JobStatus.DONE


# --- chem failing ---------------------------------------------------------------------------------


def broken(status: int) -> Callable[..., httpx.Response]:
    return lambda *_: httpx.Response(status, json={"detail": "x"}, headers={"Retry-After": "1"})


@pytest.mark.parametrize("status", [500, 422, 413, 503])
async def test_chem_answering_with_an_error_means_no_names_and_one_warning(
    make_rig: RigFactory, status: int
) -> None:
    backend = FakeBackend(layout={0: 4}, hooks={"chem": broken(status)})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [r.name for r in job.results] == [None] * 4
    assert all(r.valid for r in job.results)
    assert codes(job) == ["names_unavailable"]


async def test_chem_timing_out_means_no_names_and_one_warning(make_rig: RigFactory) -> None:
    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise httpx.ReadTimeout("no answer in 120 s")

    backend = FakeBackend(layout={0: 3}, hooks={"chem": hook})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert codes(job) == ["names_unavailable"]
    assert backend.count("chem") == 1  # a timeout is not retried


async def test_chem_unreachable_means_no_names_and_one_warning(make_rig: RigFactory) -> None:
    def refuse(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend = FakeBackend(layout={0: 3}, hooks={"chem": refuse})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert codes(job) == ["names_unavailable"]


async def test_after_a_failed_batch_the_next_batches_are_not_tried(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 15, 1: 10}, hooks={"chem": broken(500)})
    rig = make_rig(api={"max_structures": 25}, backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(2))
    assert backend.count("chem") == 1
    assert codes(job) == ["names_unavailable"]


async def test_a_batch_answer_with_the_wrong_number_of_names_is_not_used(
    make_rig: RigFactory,
) -> None:
    """Names are matched by position: a short answer would put a name on the wrong structure."""
    answer = {
        "results": [{"name": "ethane", "tier": "pin_verified", "verified": "opsin", "reason": None}]
    }
    backend = FakeBackend(layout={0: 3}, hooks={"chem": lambda *_: json_response(answer)})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [r.name for r in job.results] == [None] * 3
    assert codes(job) == ["names_unavailable"]
