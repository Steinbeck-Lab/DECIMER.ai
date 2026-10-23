"""POST /api/jobs/{id}/report: a problem report on the private volume."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from decimer_api.engines import engines
from decimer_api.models import Job, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock
from tests.results import crop_bytes, crop_path, make_done_job, make_results


async def report(client: httpx.AsyncClient, job_id: str, index: int) -> httpx.Response:
    return await client.post(f"/api/jobs/{job_id}/report", json={"index": index})


@pytest.fixture
async def job(store: JobStore, settings: Settings, clock: FakeClock) -> Job:
    return await make_done_job(store, settings, clock)


@pytest.fixture
def job_for_unit(clock: FakeClock) -> Job:
    """A job record that is not in any store, for the tests of `save_report` itself."""
    job = Job.create(
        job_id=new_job_id(), client_ip_hash="x", pages=1, pages_processed=1, now=clock()
    )
    job.results = make_results()
    return job


def entries(reports_dir: Path) -> list[str]:
    return sorted(path.name for path in reports_dir.iterdir()) if reports_dir.exists() else []


# --- what is stored, and where -------------------------------------------------------------------


async def test_a_report_keeps_the_crop_and_the_result_in_a_folder_made_here(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    response = await report(client, job.id, 2)
    assert response.status_code == 204 and response.content == b""
    assert entries(reports_dir) == [f"{job.id}-2"]  # nothing else: no temporary folder left
    folder = reports_dir / f"{job.id}-2"
    assert sorted(path.name for path in folder.iterdir()) == ["crop.png", "result.json"]
    assert (folder / "crop.png").read_bytes() == crop_bytes(2)
    record = json.loads((folder / "result.json").read_text())
    stored = job.results[2]
    assert record["jobId"] == job.id
    assert record["result"]["index"] == 2
    assert record["result"]["smiles"] == stored.smiles
    assert record["result"]["molfile"] == stored.molfile
    assert record["result"]["name"] == stored.name
    assert "svg" not in record["result"]  # derived, and large
    assert record["image"] == "crop.png" and record["imageOmitted"] is None
    assert record["job"] == {"pages": 1, "pagesProcessed": 1, "truncated": False, "warnings": []}
    assert {e["id"]: e["version"] for e in record["engines"]} == {
        e.id: e.version for e in engines()
    }
    assert record["reportedAt"]


async def test_the_reports_volume_is_private(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    await report(client, job.id, 0)
    folder = reports_dir / f"{job.id}-0"
    assert stat.S_IMODE(reports_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for name in ("crop.png", "result.json"):
        assert stat.S_IMODE((folder / name).stat().st_mode) == 0o600


async def test_a_report_is_not_kept_with_the_job_so_the_sweep_cannot_delete_it(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    await report(client, job.id, 1)
    assert not reports_dir.is_relative_to(settings.data_dir)
    assert not settings.data_dir.is_relative_to(reports_dir)


async def test_a_report_is_kept_after_its_job_is_gone(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    import shutil

    await report(client, job.id, 1)
    shutil.rmtree(settings.data_dir / job.id)
    assert (reports_dir / f"{job.id}-1" / "crop.png").read_bytes() == crop_bytes(1)


async def test_the_same_result_reported_twice_is_kept_once_and_both_are_ok(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    assert (await report(client, job.id, 0)).status_code == 204
    first = (reports_dir / f"{job.id}-0" / "result.json").read_bytes()
    assert (await report(client, job.id, 0)).status_code == 204
    assert entries(reports_dir) == [f"{job.id}-0"]
    assert (reports_dir / f"{job.id}-0" / "result.json").read_bytes() == first


async def test_every_result_has_its_own_report(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    for index in range(5):
        assert (await report(client, job.id, index)).status_code == 204
    assert entries(reports_dir) == sorted(f"{job.id}-{index}" for index in range(5))


async def test_the_report_is_about_the_results_the_server_has_not_what_the_client_says(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    response = await client.post(
        f"/api/jobs/{job.id}/report",
        json={"index": 1, "smiles": "CCCCCC", "molfile": "x", "path": "/etc/passwd"},
    )
    assert response.status_code == 422  # unknown fields are refused, not ignored
    assert entries(reports_dir) == []


# --- no paths from the client --------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"index": "../../etc"},
        {"index": "1/../../x"},
        {"index": -1},
        {"index": 1.5},
        {"index": None},
        {"index": True},
        {},
        {"path": "/etc/passwd"},
        {"index": 0, "dir": "/tmp"},  # noqa: S108
        [0],
        "0",
    ],
)
async def test_a_body_that_is_not_exactly_an_index_is_422_and_writes_nothing(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, body: object
) -> None:
    response = await client.post(f"/api/jobs/{job.id}/report", json=body)
    assert response.status_code == 422, body
    assert response.json()["detail"]["code"] == "invalid_request"
    assert entries(reports_dir) == []


async def test_the_job_id_in_the_path_is_checked_before_it_is_used_in_a_name(
    client: httpx.AsyncClient, reports_dir: Path
) -> None:
    for bad in ("..", "../x", "a" * 31, "A" * 32, "a" * 32 + "-0", "%2e%2e"):
        assert (await report(client, bad, 0)).status_code == 404
    assert entries(reports_dir) == []


@pytest.mark.parametrize("index", ["1", " 1", "01", 1.0, True])
async def test_only_a_json_integer_is_an_index(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, index: object
) -> None:
    """Not "1", not 1.0, not true: the lax readings pydantic would otherwise accept."""
    response = await client.post(f"/api/jobs/{job.id}/report", json={"index": index})
    assert response.status_code == 422
    assert entries(reports_dir) == []


async def test_an_unknown_job_or_result_is_404_and_writes_nothing(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    assert (await report(client, new_job_id(), 0)).status_code == 404
    assert (await report(client, job.id, 5)).status_code == 404
    assert (await report(client, job.id, 10**9)).status_code == 404
    assert entries(reports_dir) == []


async def test_an_expired_job_cannot_be_reported(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, clock: FakeClock
) -> None:
    clock.advance(3601)
    assert (await report(client, job.id, 0)).status_code == 404
    assert entries(reports_dir) == []


# --- bounds --------------------------------------------------------------------------------------


async def test_a_body_over_four_kilobytes_is_413(
    client: httpx.AsyncClient, job: Job, reports_dir: Path
) -> None:
    response = await client.post(
        f"/api/jobs/{job.id}/report", json={"index": 0, "comment": "x" * 5000}
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "too_large"
    assert entries(reports_dir) == []


async def test_a_crop_over_the_image_limit_is_left_out_and_the_record_says_so(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    crop_path(settings, job, 0).write_bytes(b"x" * (settings.max_report_image_bytes + 1))
    assert (await report(client, job.id, 0)).status_code == 204
    folder = reports_dir / f"{job.id}-0"
    assert sorted(path.name for path in folder.iterdir()) == ["result.json"]
    record = json.loads((folder / "result.json").read_text())
    assert record["image"] is None and record["imageOmitted"] == "too_large"


async def test_a_crop_exactly_at_the_limit_is_kept(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    crop_path(settings, job, 0).write_bytes(b"x" * settings.max_report_image_bytes)
    await report(client, job.id, 0)
    assert (
        reports_dir / f"{job.id}-0" / "crop.png"
    ).stat().st_size == settings.max_report_image_bytes


async def test_a_missing_crop_still_gives_a_report_without_an_image(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    crop_path(settings, job, 0).unlink()
    assert (await report(client, job.id, 0)).status_code == 204
    record = json.loads((reports_dir / f"{job.id}-0" / "result.json").read_text())
    assert record["image"] is None and record["imageOmitted"] == "missing"


async def test_a_symlinked_crop_is_not_copied(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    target = crop_path(settings, job, 0)
    target.unlink()
    target.symlink_to(secret)
    assert (await report(client, job.id, 0)).status_code == 204
    folder = reports_dir / f"{job.id}-0"
    assert not (folder / "crop.png").exists()
    assert b"secret" not in (folder / "result.json").read_bytes()


async def test_the_number_of_reports_is_capped(
    app: object, job: Job, reports_dir: Path, settings: Settings, make_client: object
) -> None:
    settings.max_reports = 2  # the app holds this very object
    client = make_client("203.0.113.5")  # type: ignore[operator]
    assert (await report(client, job.id, 0)).status_code == 204
    assert (await report(client, job.id, 1)).status_code == 204
    full = await report(client, job.id, 2)
    assert full.status_code == 503
    assert full.json()["detail"]["code"] == "unavailable"
    assert entries(reports_dir) == [f"{job.id}-0", f"{job.id}-1"]
    # a result that has its report already is still fine
    assert (await report(client, job.id, 0)).status_code == 204


async def test_a_volume_that_cannot_be_written_is_503_not_500_and_leaks_no_path(
    client: httpx.AsyncClient, job: Job, settings: Settings
) -> None:
    settings.reports_dir.parent.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.write_text("a file where the directory should be")
    response = await report(client, job.id, 0)
    assert response.status_code == 503
    assert str(settings.reports_dir) not in response.text


async def test_a_readonly_volume_is_503(
    client: httpx.AsyncClient, job: Job, settings: Settings
) -> None:
    settings.reports_dir.mkdir(parents=True)
    os.chmod(settings.reports_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        if os.access(settings.reports_dir, os.W_OK):
            pytest.skip("running as a user that ignores directory permissions")
        assert (await report(client, job.id, 0)).status_code == 503
    finally:
        os.chmod(settings.reports_dir, stat.S_IRWXU)


async def test_a_failed_report_leaves_no_half_written_folder(
    client: httpx.AsyncClient,
    job: Job,
    reports_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimer_api import reports

    def broken(_path: Path, _data: bytes) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(reports, "_write", broken)
    assert (await report(client, job.id, 0)).status_code == 503
    assert entries(reports_dir) == []


# --- rate limit ----------------------------------------------------------------------------------


async def test_reports_are_rate_limited_per_client_per_hour(
    make_client: object, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    settings.job_ttl_seconds = 10 * 3600  # the job must outlive the hour of the rate window
    many = await make_done_job(store, settings, clock, make_results(["CCO"] * 40), ip="many")
    one = make_client("203.0.113.1")  # type: ignore[operator]
    other = make_client("203.0.113.2")  # type: ignore[operator]
    for index in range(settings.report_rate_per_hour):
        assert (await report(one, many.id, index)).status_code == 204
    refused = await report(one, many.id, settings.report_rate_per_hour)
    assert refused.status_code == 429
    assert refused.json()["detail"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["retry-after"]) <= 3601
    assert (await report(other, many.id, 39)).status_code == 204
    clock.advance(3601)
    assert (await report(one, many.id, settings.report_rate_per_hour)).status_code == 204


def test_a_crop_that_became_a_symlink_after_it_was_looked_at_is_still_not_copied(
    job_for_unit: Job, tmp_path: Path
) -> None:
    """The route looks the crop up without following links; `save_report` opens it without
    following them either, in case it was swapped in between."""
    from datetime import UTC, datetime

    from decimer_api.reports import save_report

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"secret")
    link = tmp_path / "crop.png"
    link.symlink_to(secret)
    reports = tmp_path / "reports"
    with pytest.raises(OSError):
        save_report(
            reports,
            job_for_unit,
            job_for_unit.results[0],
            link,
            now=datetime.now(UTC),
            max_reports=10,
            max_image_bytes=1024,
        )
    assert list(reports.iterdir()) == []  # and no half-written folder


# --- the cap (review finding 6) ------------------------------------------------------------------


async def test_work_in_progress_does_not_count_against_the_cap(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    """`.tmp-*` folders are reports being written, or what a crash left: not reports."""
    settings.max_reports = 2
    reports_dir.mkdir(parents=True)
    for number in range(5):
        (reports_dir / f".tmp-{number:08x}").mkdir()
    assert (await report(client, job.id, 0)).status_code == 204
    assert (await report(client, job.id, 1)).status_code == 204
    assert (await report(client, job.id, 2)).status_code == 503  # two reports: that is the cap
    assert sorted(p.name for p in reports_dir.iterdir() if not p.name.startswith(".tmp-")) == [
        f"{job.id}-0",
        f"{job.id}-1",
    ]


def test_tidy_removes_old_temporary_folders_and_only_those(tmp_path: Path) -> None:
    import time

    from decimer_api.reports import tidy

    reports = tmp_path / "reports"
    reports.mkdir()
    old = reports / ".tmp-old"
    fresh = reports / ".tmp-fresh"
    kept = reports / ("a" * 32 + "-0")
    for folder in (old, fresh, kept):
        folder.mkdir()
        (folder / "result.json").write_text("{}")
    long_ago = time.time() - 7200
    os.utime(old, (long_ago, long_ago))
    os.utime(kept, (long_ago, long_ago))  # an old report is a report, not work in progress
    assert tidy(reports, 100) == 1
    assert sorted(p.name for p in reports.iterdir()) == [".tmp-fresh", kept.name]


def test_tidy_has_nothing_to_do_before_the_directory_exists(tmp_path: Path) -> None:
    from decimer_api.reports import tidy

    assert tidy(tmp_path / "not-yet", 100) == 0


async def test_the_api_tidies_the_volume_when_it_starts(app: FastAPI, reports_dir: Path) -> None:
    import time

    reports_dir.mkdir(parents=True)
    stale = reports_dir / ".tmp-crashed"
    stale.mkdir()
    long_ago = time.time() - 7200
    os.utime(stale, (long_ago, long_ago))
    async with app.router.lifespan_context(app):
        pass
    assert not stale.exists()


async def test_a_volume_that_cannot_be_tidied_does_not_stop_the_api_from_starting(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from decimer_api import app as app_module

    def broken(*_args: object) -> int:
        raise PermissionError("read-only volume")

    monkeypatch.setattr(app_module, "tidy", broken)
    async with app.router.lifespan_context(app):
        pass
    assert "could not tidy REPORTS_DIR" in caplog.text


def test_a_warning_is_logged_when_the_count_reaches_80_percent(
    job_for_unit: Job, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    from datetime import UTC, datetime

    from decimer_api.reports import save_report

    job_for_unit.results = make_results(["CCO"] * 5)
    with caplog.at_level(logging.WARNING, logger="decimer_api.reports"):
        for index in range(4):
            save_report(
                tmp_path / "r",
                job_for_unit,
                job_for_unit.results[index],
                None,
                now=datetime.now(UTC),
                max_reports=5,
                max_image_bytes=1024,
            )
            warned = [r for r in caplog.records if "nearly" in r.message or "at most" in r.message]
            assert len(warned) == (1 if index == 3 else 0), index  # the 4th of 5 is 80 %
    assert "4 of at most 5" in caplog.text


def test_tidy_warns_when_it_starts_with_the_volume_at_80_percent(
    job_for_unit: Job, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from decimer_api.reports import tidy

    reports = tmp_path / "r"
    for number in range(4):
        (reports / f"{'b' * 32}-{number}").mkdir(parents=True)
    with caplog.at_level(logging.WARNING, logger="decimer_api.reports"):
        tidy(reports, 5)
    assert "4 of at most 5" in caplog.text


def test_requests_at_the_same_time_cannot_all_find_room_for_one_more(
    job_for_unit: Job, tmp_path: Path
) -> None:
    """Twenty threads, room for three: exactly three reports are made. The count and the rename
    are one step (a count taken before the rename let every thread see two and make a report)."""
    import threading
    from datetime import UTC, datetime

    from decimer_api.reports import ReportsFull, count_reports, save_report

    job_for_unit.results = make_results(["CCO"] * 20)
    reports = tmp_path / "r"
    results: list[str] = []
    start = threading.Barrier(20)

    def one(index: int) -> None:
        start.wait()
        try:
            save_report(
                reports,
                job_for_unit,
                job_for_unit.results[index],
                None,
                now=datetime.now(UTC),
                max_reports=3,
                max_image_bytes=1024,
            )
            results.append("made")
        except ReportsFull:
            results.append("full")

    threads = [threading.Thread(target=one, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("made") == 3 and results.count("full") == 17
    assert count_reports(reports) == 3
    assert not any(p.name.startswith(".tmp-") for p in reports.iterdir())  # no leftovers


# --- the byte budget, and a crop that is gone (review 3) --------------------------------------


def test_a_crop_that_is_gone_when_the_report_is_saved_is_recorded_as_missing(
    job_for_unit: Job, tmp_path: Path
) -> None:
    """A DELETE racing a report: the route found the crop, the folder was removed, `save_report`
    asks for its size (`crop.stat()` used to be outside the try, and a 503 came of it)."""
    from datetime import UTC, datetime

    from decimer_api.reports import save_report

    reports = tmp_path / "r"
    made = save_report(
        reports,
        job_for_unit,
        job_for_unit.results[0],
        tmp_path / "gone" / "crop-0.png",  # looked up a moment ago, deleted since
        now=datetime.now(UTC),
        max_reports=10,
        max_image_bytes=1024,
        max_bytes=10**6,
    )
    assert made is True
    record = json.loads((reports / f"{job_for_unit.id}-0" / "result.json").read_text())
    assert record["image"] is None and record["imageOmitted"] == "missing"


async def test_a_delete_racing_a_report_is_a_report_without_an_image_not_a_503(
    client: httpx.AsyncClient,
    job: Job,
    reports_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimer_api import result_routes

    real = result_routes.resolve_media

    def found_then_deleted(data_dir: Path, job_id: str, name: str) -> Path | None:
        path = real(data_dir, job_id, name)
        if path is not None:
            path.unlink()  # the owner's DELETE lands between the lookup and the copy
        return path

    monkeypatch.setattr(result_routes, "resolve_media", found_then_deleted)
    assert (await report(client, job.id, 0)).status_code == 204
    record = json.loads((reports_dir / f"{job.id}-0" / "result.json").read_text())
    assert record["imageOmitted"] == "missing"


def folder_size(folder: Path) -> int:
    return sum(path.stat().st_size for path in folder.iterdir())


async def test_the_reports_have_a_byte_budget_beyond_which_it_is_503(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    assert (await report(client, job.id, 0)).status_code == 204
    one = folder_size(reports_dir / f"{job.id}-0")
    settings.reports_max_bytes = one * 2 + one // 2  # room for two reports and a half
    assert (await report(client, job.id, 1)).status_code == 204
    refused = await report(client, job.id, 2)
    assert refused.status_code == 503 and refused.json()["detail"]["code"] == "unavailable"
    assert entries(reports_dir) == [f"{job.id}-0", f"{job.id}-1"]  # nothing half-written is left
    # A result that has its report is still answered, whatever the budget says.
    assert (await report(client, job.id, 0)).status_code == 204


async def test_what_the_volume_already_holds_counts_against_the_budget(
    client: httpx.AsyncClient, job: Job, reports_dir: Path, settings: Settings
) -> None:
    """Counted from the disk, so a restart does not forget what is there."""
    old = reports_dir / f"{'c' * 32}-0"
    old.mkdir(parents=True)
    (old / "crop.png").write_bytes(b"x" * 10_000)
    (reports_dir / ".tmp-unfinished").mkdir()
    (reports_dir / ".tmp-unfinished" / "big").write_bytes(
        b"y" * 50_000
    )  # not a report: not counted
    settings.reports_max_bytes = 10_500
    assert (await report(client, job.id, 0)).status_code == 503
    settings.reports_max_bytes = 12_500
    assert (await report(client, job.id, 0)).status_code == 204


def test_a_warning_is_logged_when_the_bytes_reach_80_percent(
    job_for_unit: Job, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    from datetime import UTC, datetime

    from decimer_api.reports import save_report

    reports = tmp_path / "r"
    save_report(
        reports,
        job_for_unit,
        job_for_unit.results[0],
        None,
        now=datetime.now(UTC),
        max_reports=100,
        max_image_bytes=1024,
        max_bytes=10**9,
    )
    size = folder_size(reports / f"{job_for_unit.id}-0")
    with caplog.at_level(logging.WARNING, logger="decimer_api.reports"):
        save_report(
            reports,
            job_for_unit,
            job_for_unit.results[1],
            None,
            now=datetime.now(UTC),
            max_reports=100,
            max_image_bytes=1024,
            max_bytes=int(size * 2.4),  # the second report makes 2 of 2.4: past 80 %
        )
    warnings = [r.getMessage() for r in caplog.records if "bytes" in r.getMessage()]
    assert len(warnings) == 1  # at the crossing, once
    assert f"of at most {int(size * 2.4)} bytes" in warnings[0]


def test_tidy_warns_when_it_starts_with_the_bytes_at_80_percent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from decimer_api.reports import tidy

    reports = tmp_path / "r"
    (reports / f"{'d' * 32}-0").mkdir(parents=True)
    (reports / f"{'d' * 32}-0" / "crop.png").write_bytes(b"z" * 900)
    with caplog.at_level(logging.WARNING, logger="decimer_api.reports"):
        tidy(reports, 100, 1000)
    assert "900" in caplog.text and "1000" in caplog.text


async def test_nothing_is_copied_when_the_budget_is_already_used_up(
    client: httpx.AsyncClient,
    job: Job,
    reports_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from decimer_api import reports

    old = reports_dir / f"{'e' * 32}-0"
    old.mkdir(parents=True)
    (old / "crop.png").write_bytes(b"x" * 2000)
    settings.reports_max_bytes = 2000

    def must_not_copy(*_args: object) -> None:
        raise AssertionError("a crop was copied for a report that cannot be kept")

    monkeypatch.setattr(reports, "_copy", must_not_copy)
    assert (await report(client, job.id, 0)).status_code == 503
    assert not any(path.name.startswith(".tmp-") for path in reports_dir.iterdir())
