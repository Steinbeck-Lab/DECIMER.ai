from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from decimer_api.models import (
    Job,
    JobStatus,
    JobView,
    Result,
    StageName,
    StageState,
    is_valid_job_id,
    new_job_id,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_job_ids_are_random_128_bit_hex() -> None:
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(re.fullmatch(r"[0-9a-f]{32}", i) for i in ids)
    # Not sequential: no two consecutive ids share a long prefix.
    ordered = sorted(ids)
    assert all(a[:6] != b[:6] for a, b in zip(ordered, ordered[1:], strict=False))


def test_job_id_pattern_rejects_everything_else() -> None:
    assert is_valid_job_id("0123456789abcdef0123456789abcdef")
    for bad in ("", "..", "0123456789ABCDEF0123456789ABCDEF", "g" * 32, "a" * 31, "a" * 33, "../x"):
        assert not is_valid_job_id(bad)


def test_new_job_has_upload_done_and_the_rest_pending() -> None:
    job = Job.create(
        job_id=new_job_id(), client_ip_hash="h" * 32, pages=3, pages_processed=3, now=NOW
    )
    assert job.status is JobStatus.QUEUED
    assert [(s.name, s.state) for s in job.stages] == [
        (StageName.UPLOAD, StageState.DONE),
        (StageName.RENDER, StageState.PENDING),
        (StageName.SEGMENT, StageState.PENDING),
        (StageName.RECOGNISE, StageState.PENDING),
        (StageName.CHECK, StageState.PENDING),
    ]
    assert job.results == []
    assert job.warnings == []
    assert job.truncated is False
    assert job.structures_dropped == 0


def test_json_uses_camel_case_and_the_public_view_hides_the_ip_hash() -> None:
    job = Job.create(
        job_id=new_job_id(), client_ip_hash="h" * 32, pages=12, pages_processed=10, now=NOW
    )
    stored = json.loads(job.model_dump_json(by_alias=True))
    assert stored["clientIpHash"] == "h" * 32
    assert stored["pagesProcessed"] == 10
    assert stored["structuresDropped"] == 0
    view = json.loads(JobView.from_job(job).model_dump_json(by_alias=True))
    assert "clientIpHash" not in view
    assert "clientIp" not in view
    assert {"id", "status", "stages", "results", "warnings", "truncated", "pages"} <= set(view)
    assert view["createdAt"].startswith("2026-10-22T12:00:00")
    assert view["expiresAt"] is not None


def test_stored_job_round_trips() -> None:
    job = Job.create(
        job_id=new_job_id(), client_ip_hash="h" * 32, pages=1, pages_processed=1, now=NOW
    )
    job.results.append(
        Result(
            index=0,
            page_index=0,
            bbox=(1, 2, 30, 40),
            smiles="CCO",
            inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            valid=True,
            is_structure=True,
            confidence=0.93,
            name="ethanol",
            name_tier="pin_verified",
        )
    )
    again = Job.model_validate_json(job.model_dump_json(by_alias=True))
    assert again == job


def test_result_view_carries_number_and_media_url() -> None:
    job = Job.create(job_id="a" * 32, client_ip_hash="h" * 32, pages=1, pages_processed=1, now=NOW)
    job.results.append(
        Result(
            index=2,
            page_index=1,
            bbox=(0, 0, 10, 10),
            smiles="C",
            inchikey="",
            valid=False,
            is_structure=False,
            confidence=0.1,
        )
    )
    result = json.loads(JobView.from_job(job).model_dump_json(by_alias=True))["results"][0]
    assert result["number"] == 3
    assert result["cropUrl"] == f"/api/jobs/{'a' * 32}/media/crop-2.png"
    assert result["pageIndex"] == 1
    assert result["isStructure"] is False
    assert result["name"] is None and result["nameTier"] is None


def test_the_stored_record_holds_nothing_derived() -> None:
    job = Job.create(job_id="b" * 32, client_ip_hash="h" * 32, pages=1, pages_processed=1, now=NOW)
    job.results.append(
        Result(
            index=4,
            page_index=0,
            bbox=(0, 0, 5, 5),
            smiles="C",
            inchikey="",
            valid=True,
            is_structure=True,
            confidence=0.5,
        )
    )
    stored = json.loads(job.model_dump_json(by_alias=True))
    assert "queuePosition" not in stored
    assert "cropUrl" not in stored["results"][0] and "number" not in stored["results"][0]
    view = json.loads(JobView.from_job(job, queue_position=3).model_dump_json(by_alias=True))
    assert view["queuePosition"] == 3
    assert view["results"][0]["cropUrl"].endswith("/media/crop-4.png")
    assert view["results"][0]["number"] == 5
    # A job that is not waiting has no position, whatever the caller passes.
    job.status = JobStatus.RUNNING
    running = json.loads(JobView.from_job(job, queue_position=3).model_dump_json(by_alias=True))
    assert running["queuePosition"] is None
