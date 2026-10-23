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


# --- retention: a report is deleted when it is older than REPORTS_MAX_AGE_DAYS -------------------

DAY = 86400
NOW = 1_800_000_000.0  # a fixed "now", so that no test depends on the real clock


def make_report(
    reports: Path,
    name: str,
    *,
    age_days: float,
    reported_days_ago: float | None = None,
    record: str | None = None,
) -> Path:
    """A finished report folder: its own time `age_days` before NOW, and a `result.json` that says
    it was reported `reported_days_ago` days before NOW (the same by default). `record` replaces
    the file's text (a damaged record); `reported_days_ago=-1` leaves the file out."""
    from datetime import UTC, datetime

    folder = reports / name
    folder.mkdir(parents=True)
    (folder / "crop.png").write_bytes(b"png")
    if reported_days_ago != -1:
        when = datetime.fromtimestamp(NOW - (reported_days_ago or age_days) * DAY, UTC)
        text = record if record is not None else json.dumps({"reportedAt": when.isoformat()})
        (folder / "result.json").write_text(text)
    stamp = NOW - age_days * DAY
    os.utime(folder, (stamp, stamp))
    return folder


def names(reports: Path) -> list[str]:
    return sorted(path.name for path in reports.iterdir())


OLD, NEW, EDGE = "a" * 32 + "-0", "b" * 32 + "-1", "c" * 32 + "-2"


def test_expire_deletes_a_report_older_than_the_limit_and_keeps_a_recent_one(
    tmp_path: Path,
) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=366)
    make_report(reports, NEW, age_days=3)
    assert expire(reports, 365, now=NOW) == 1
    assert names(reports) == [NEW]  # the whole folder is gone, not only its files


def test_expire_keeps_a_report_until_it_is_older_than_the_limit(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, EDGE, age_days=365)  # exactly the limit: not yet
    assert expire(reports, 365, now=NOW) == 0
    assert names(reports) == [EDGE]
    assert expire(reports, 365, now=NOW + 1) == 1  # a second later it is past it
    assert names(reports) == []


def test_expire_takes_the_limit_from_the_setting(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=40)
    assert expire(reports, 365, now=NOW) == 0
    assert expire(reports, 30, now=NOW) == 1


def test_the_age_is_the_older_of_the_folder_time_and_the_recorded_time(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    # A copy of the volume gave a fresh folder time; the record still says it is old.
    make_report(reports, OLD, age_days=1, reported_days_ago=400)
    # The record says it is new (or was edited); the folder is old.
    make_report(reports, NEW, age_days=400, reported_days_ago=1)
    assert expire(reports, 365, now=NOW) == 2
    assert names(reports) == []


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        "{}",
        '{"reportedAt": "yesterday"}',
        "[1]",
        '{"reportedAt": 5}',
        '{"reportedAt": "9999-12-31T23:59:59"}',  # far in the future: the folder time decides
    ],
)
def test_a_report_with_a_damaged_record_is_judged_by_its_folder_time(
    tmp_path: Path, record: str
) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=500, record=record)
    make_report(reports, NEW, age_days=2, record=record)
    assert expire(reports, 365, now=NOW) == 1
    assert names(reports) == [NEW]


def test_a_report_without_a_record_is_judged_by_its_folder_time(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=500, reported_days_ago=-1)
    make_report(reports, NEW, age_days=2, reported_days_ago=-1)
    assert expire(reports, 365, now=NOW) == 1
    assert names(reports) == [NEW]


def test_expire_leaves_alone_what_is_not_a_report(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    reports.mkdir()
    long_ago = NOW - 900 * DAY
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep.txt").write_text("x")
    leave = {
        ".tmp-old": "dir",  # work in progress, or a crash's leftover: `tidy`'s rule, not this one
        "notes": "dir",  # not named like a report
        "NOTES.txt": "file",
        ("A" * 32) + "-0": "dir",  # upper case is not a job id
        ("a" * 32) + "-x": "dir",
        ("a" * 31) + "-0": "dir",
        ("a" * 32) + "-0.bak": "dir",
        ("d" * 32) + "-0": "link",  # named like a report, but a symlink: never followed
    }
    for name, kind in leave.items():
        path = reports / name
        if kind == "dir":
            path.mkdir()
            (path / "result.json").write_text("{}")
        elif kind == "file":
            path.write_text("x")
        else:
            path.symlink_to(elsewhere, target_is_directory=True)
        os.utime(path, (long_ago, long_ago), follow_symlinks=False)
        if kind == "dir":
            os.utime(path, (long_ago, long_ago))
    assert expire(reports, 365, now=NOW) == 0
    assert names(reports) == sorted(leave)
    assert (elsewhere / "keep.txt").read_text() == "x"


def test_expire_has_nothing_to_do_before_the_directory_exists(tmp_path: Path) -> None:
    from decimer_api.reports import expire

    assert expire(tmp_path / "not-yet", 365, now=NOW) == 0


def test_a_report_that_cannot_be_deleted_is_logged_and_the_others_still_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import shutil

    from decimer_api import reports as reports_module

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=500)
    make_report(reports, EDGE, age_days=500)
    real = shutil.rmtree

    def fails_for_one(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path).name == OLD:
            raise PermissionError("read-only")
        real(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reports_module.shutil, "rmtree", fails_for_one)
    assert reports_module.expire(reports, 365, now=NOW) == 1
    assert names(reports) == [OLD]
    assert f"could not delete the expired report {OLD}" in caplog.text
    monkeypatch.setattr(reports_module.shutil, "rmtree", real)
    assert reports_module.expire(reports, 365, now=NOW) == 1  # the next run tries again
    assert names(reports) == []


def test_tidy_deletes_old_reports_when_given_a_limit_and_keeps_the_temporary_rules(
    tmp_path: Path,
) -> None:
    from decimer_api.reports import tidy

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=400)
    make_report(reports, NEW, age_days=10)
    for name, age in ((".tmp-stale", 2 * 3600), (".tmp-fresh", 60)):
        (reports / name).mkdir()
        os.utime(reports / name, (NOW - age, NOW - age))
    assert tidy(reports, 100, None, 365, now=NOW) == 2  # the old report and the stale temporary one
    assert names(reports) == [".tmp-fresh", NEW]


def test_tidy_without_a_limit_deletes_no_report_for_its_age(tmp_path: Path) -> None:
    from decimer_api.reports import tidy

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=4000)
    assert tidy(reports, 100, now=NOW) == 0
    assert names(reports) == [OLD]


def test_the_warning_at_80_percent_counts_what_is_left_after_the_old_reports_are_gone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from decimer_api.reports import tidy

    reports = tmp_path / "r"
    for number in range(4):
        make_report(reports, f"{'e' * 32}-{number}", age_days=500)
    with caplog.at_level(logging.WARNING, logger="decimer_api.reports"):
        assert tidy(reports, 5, None, 365, now=NOW) == 4
    assert "of at most" not in caplog.text


@pytest.mark.parametrize(
    "record",
    [
        # Nested deeper than the JSON reader's stack: a RecursionError, not a ValueError.
        pytest.param("[" * 200_000, id="nested arrays"),
        pytest.param('{"reportedAt":' * 50_000, id="nested objects"),
    ],
)
def test_a_tampered_record_that_overflows_the_json_reader_is_judged_by_its_folder_time(
    tmp_path: Path, record: str
) -> None:
    from decimer_api.reports import expire

    reports = tmp_path / "r"
    make_report(reports, OLD, age_days=500, record=record)
    make_report(reports, NEW, age_days=2, record=record)
    assert expire(reports, 365, now=NOW) == 1
    assert names(reports) == [NEW]


def test_a_report_never_outlives_the_twelve_months_the_page_promises() -> None:
    """The age limit plus the longest wait for the next run must stay within 365 days, the shortest
    twelve months: a run every hour, and a limit that leaves the hour (and more) to spare."""
    from decimer_api.reports import TIDY_INTERVAL_SECONDS
    from decimer_api.settings import Settings

    limit = Settings(redis_url="redis://x:6379/0").reports_max_age_days * DAY
    assert TIDY_INTERVAL_SECONDS <= 3600
    assert limit + TIDY_INTERVAL_SECONDS <= 365 * DAY


async def test_a_failure_of_the_start_up_tidy_of_any_kind_does_not_stop_the_api(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from decimer_api import app as app_module

    def broken(*_args: object) -> int:
        raise RecursionError("a record nested too deep")

    monkeypatch.setattr(app_module, "tidy", broken)
    async with app.router.lifespan_context(app):
        pass
    assert "could not tidy REPORTS_DIR" in caplog.text


def test_the_setting_defaults_to_364_days_and_must_be_positive() -> None:
    from pydantic import ValidationError

    from decimer_api.settings import Settings

    assert Settings(redis_url="redis://x:6379/0").reports_max_age_days == 364
    assert (
        Settings(redis_url="redis://x:6379/0", reports_max_age_days=30).reports_max_age_days == 30
    )
    with pytest.raises(ValidationError):
        Settings(redis_url="redis://x:6379/0", reports_max_age_days=0)


async def test_the_api_deletes_an_old_report_when_it_starts(
    app: FastAPI, reports_dir: Path, settings: Settings
) -> None:
    import time

    settings.reports_max_age_days = 30
    folder = make_report(reports_dir, OLD, age_days=0)
    long_ago = time.time() - 31 * DAY
    os.utime(folder, (long_ago, long_ago))
    make_report(reports_dir, NEW, age_days=0)
    async with app.router.lifespan_context(app):
        assert names(reports_dir) == [NEW]


async def test_tidy_every_runs_again_and_again_and_survives_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import asyncio
    import contextlib
    import time

    from decimer_api import reports as reports_module

    calls: list[tuple[object, ...]] = []
    real = reports_module.tidy

    def flaky(*args: object, **kwargs: object) -> int:
        calls.append(args)
        if len(calls) == 1:
            raise PermissionError("read-only volume")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(reports_module, "tidy", flaky)
    reports = tmp_path / "r"
    folder = make_report(reports, OLD, age_days=0)
    long_ago = time.time() - 400 * DAY
    os.utime(folder, (long_ago, long_ago))
    (folder / "result.json").write_text("{}")  # no recorded time: the folder's decides
    task = asyncio.create_task(reports_module.tidy_every(0.01, reports, 100, None, 365))
    try:
        async with asyncio.timeout(10):
            while names(reports):
                await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert len(calls) >= 2  # the first run failed; the loop went on
    assert calls[0] == (reports, 100, None, 365)
    assert "could not tidy REPORTS_DIR" in caplog.text


async def test_tidy_every_waits_one_interval_before_the_first_run(tmp_path: Path) -> None:
    import asyncio
    import time

    from decimer_api.reports import tidy_every

    reports = tmp_path / "r"
    folder = make_report(reports, OLD, age_days=0)
    long_ago = time.time() - 400 * DAY
    os.utime(folder, (long_ago, long_ago))
    (folder / "result.json").write_text("{}")
    task = asyncio.create_task(tidy_every(3600, reports, 100, None, 365))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert names(reports) == [OLD]  # the api tidies at start-up itself; this is the hourly one


async def test_the_api_goes_on_tidying_while_it_runs_and_stops_at_shutdown(
    app: FastAPI,
    reports_dir: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time

    from decimer_api import app as app_module

    monkeypatch.setattr(app_module, "TIDY_INTERVAL_SECONDS", 0.01)
    settings.reports_max_age_days = 30
    before = asyncio.all_tasks()
    async with app.router.lifespan_context(app):
        folder = make_report(reports_dir, OLD, age_days=0)  # made after the start-up tidy
        long_ago = time.time() - 31 * DAY
        os.utime(folder, (long_ago, long_ago))
        (folder / "result.json").write_text("{}")
        async with asyncio.timeout(10):
            while names(reports_dir):
                await asyncio.sleep(0.01)
    await asyncio.sleep(0)
    leftover = [t for t in asyncio.all_tasks() - before if "tidy_every" in repr(t.get_coro())]
    assert leftover == []  # cancelled and awaited at shutdown


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
