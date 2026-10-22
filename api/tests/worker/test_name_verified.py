"""`nameVerified`: how chem checked the name (its `verified`), next to `name` and `nameTier`."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from decimer_api.models import Job, JobView, Result
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, json_response

RigFactory = Callable[..., Rig]
CHEM = {"chem_url": "http://chem.test"}


def result(**extra: object) -> Result:
    values: dict[str, object] = {
        "index": 0,
        "page_index": 0,
        "bbox": (0, 0, 10, 10),
        "smiles": "CC",
        "inchikey": "OTMSDBZUPAUEDD-UHFFFAOYSA-N",
        "valid": True,
        "is_structure": True,
        "confidence": 0.9,
    }
    return Result(**{**values, **extra})  # type: ignore[arg-type]


# --- the model ------------------------------------------------------------------------------------


def test_the_field_is_called_name_verified_in_json() -> None:
    dumped = result(name="ethane", name_tier="pin_verified", name_verified="opsin").model_dump(
        by_alias=True
    )
    assert dumped["nameVerified"] == "opsin"
    assert (dumped["name"], dumped["nameTier"]) == ("ethane", "pin_verified")


def test_it_is_null_when_nothing_is_given() -> None:
    assert result().name_verified is None
    assert result().model_dump(by_alias=True)["nameVerified"] is None


def test_it_is_read_back_from_a_stored_record() -> None:
    stored = result(name="ethane", name_tier="pin_verified", name_verified="opsin")
    again = Result.model_validate_json(stored.model_dump_json(by_alias=True))
    assert again.name_verified == "opsin"


def test_the_api_view_has_it(clock: object) -> None:
    from datetime import UTC, datetime

    job = Job.create(
        job_id="a" * 32, client_ip_hash="x", pages=1, pages_processed=1, now=datetime.now(UTC)
    )
    job.results = [result(name="ethane", name_tier="pin_verified", name_verified="opsin")]
    view = JobView.from_job(job).model_dump(by_alias=True)
    assert view["results"][0]["nameVerified"] == "opsin"


async def test_the_openapi_schema_names_it(client: httpx.AsyncClient) -> None:
    """The frontend's types are generated from this schema."""
    schema = (await client.get("/api/openapi.json")).json()["components"]["schemas"]
    for name in ("Result", "ResultView"):
        if name in schema:
            assert "nameVerified" in schema[name]["properties"]
            assert "nameVerified" not in schema[name].get("required", [])
    assert "ResultView" in schema or "Result" in schema


# --- the pipeline ---------------------------------------------------------------------------------


async def test_it_is_filled_from_the_verified_of_chem(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 3},
        names={"CC": ("ethane", "pin_verified"), "CCC": ("propane", "pin_unverified")},
        verifieds={"CC": "opsin", "CCC": "opsin_constitution"},
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [(r.name, r.name_tier, r.name_verified) for r in job.results] == [
        ("ethane", "pin_verified", "opsin"),
        ("propane", "pin_unverified", "opsin_constitution"),
        (None, None, None),
    ]


async def test_it_is_null_whenever_the_name_is_null(make_rig: RigFactory) -> None:
    """Even if chem sends a `verified` without a name."""
    answer = {
        "results": [
            {"name": None, "tier": "abstain", "verified": "opsin", "reason": "UNNAMEABLE"},
            {"name": "", "tier": "pin_verified", "verified": "opsin", "reason": None},
        ]
    }
    backend = FakeBackend(layout={0: 2}, hooks={"chem": lambda *_: json_response(answer)})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [(r.name, r.name_tier, r.name_verified) for r in job.results] == [(None, None, None)] * 2


async def test_it_is_null_without_a_chem_service(make_rig: RigFactory) -> None:
    rig = make_rig(backend=FakeBackend(layout={0: 2}))
    job = await rig.run(await rig.pdf_job(1))
    assert [r.name_verified for r in job.results] == [None, None]


async def test_it_is_null_when_chem_fails(make_rig: RigFactory) -> None:
    backend = FakeBackend(
        layout={0: 2}, hooks={"chem": lambda *_: httpx.Response(500, json={"detail": "x"})}
    )
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert [r.name_verified for r in job.results] == [None, None]


async def test_chems_reason_is_not_part_of_the_result(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 1}, reasons={"CC": "UNNAMEABLE"})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert "reason" not in job.results[0].model_dump(by_alias=True)
    assert "UNNAMEABLE" not in job.results[0].model_dump_json()
