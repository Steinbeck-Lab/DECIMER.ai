"""WorkerSettings: defaults that agree with each other and with the API's, checked at start."""

from __future__ import annotations

import pytest

from decimer_api.settings import Settings
from decimer_api.worker import arq_app
from decimer_api.worker.runtime import build_deps
from decimer_api.worker.settings import ARQ_TIMEOUT_MARGIN_SECONDS, WorkerSettings
from tests.worker.conftest import SpyStore


def test_the_defaults_are_consistent_with_the_defaults_of_the_api() -> None:
    WorkerSettings().check_against(Settings())  # does not raise


def test_a_job_deadline_that_outlives_the_running_record_is_refused() -> None:
    """arq cuts a run at the pipeline deadline plus its margin; that must come before the
    record's life ends, or a stuck run would have no explanation left to give."""
    ttl = Settings().running_job_ttl_seconds
    worker = WorkerSettings(pipeline_timeout_seconds=ttl - ARQ_TIMEOUT_MARGIN_SECONDS)
    with pytest.raises(ValueError, match="PIPELINE_TIMEOUT_SECONDS"):
        worker.check_against(Settings())
    ok = WorkerSettings(pipeline_timeout_seconds=ttl - ARQ_TIMEOUT_MARGIN_SECONDS - 1)
    ok.check_against(Settings())


def test_a_heartbeat_that_is_not_well_below_the_record_life_is_refused() -> None:
    ttl = Settings().running_job_ttl_seconds
    with pytest.raises(ValueError, match="HEARTBEAT_SECONDS"):
        WorkerSettings(heartbeat_seconds=ttl / 2).check_against(Settings())
    WorkerSettings(heartbeat_seconds=ttl / 4).check_against(Settings())  # several beats can fail


def test_both_problems_are_reported_together() -> None:
    with pytest.raises(ValueError) as caught:
        WorkerSettings(pipeline_timeout_seconds=5000, heartbeat_seconds=900).check_against(
            Settings()
        )
    assert "PIPELINE_TIMEOUT_SECONDS" in str(caught.value)
    assert "HEARTBEAT_SECONDS" in str(caught.value)
    assert "RUNNING_JOB_TTL_SECONDS" in str(caught.value)


def test_a_longer_record_life_makes_room_for_a_longer_deadline() -> None:
    WorkerSettings(pipeline_timeout_seconds=1500).check_against(
        Settings(running_job_ttl_seconds=1800)
    )


def test_the_worker_refuses_to_start_with_settings_that_do_not_agree() -> None:
    bad = WorkerSettings(pipeline_timeout_seconds=5000)
    with pytest.raises(ValueError, match="PIPELINE_TIMEOUT_SECONDS"):
        arq_app.build_arq_settings(Settings(), bad)


async def test_the_in_process_mode_and_build_deps_refuse_too(spy_store: SpyStore) -> None:
    with pytest.raises(ValueError, match="PIPELINE_TIMEOUT_SECONDS"):
        build_deps(Settings(), WorkerSettings(pipeline_timeout_seconds=5000), spy_store)


def test_the_defaults_leave_room_for_a_slow_ocsr() -> None:
    """ocsr takes up to 55 s on an input that is not a structure (it decodes to its token limit
    before it gives up), so a call must be allowed well over a minute."""
    worker = WorkerSettings()
    assert worker.ocsr_timeout_seconds >= 90
    assert worker.chem_timeout_seconds >= 90  # chem's own batch budget is 60 s plus a restart
    assert worker.ocsr_concurrency == 1  # ocsr runs one inference at a time
    assert worker.pipeline_timeout_seconds > worker.ocsr_timeout_seconds * 3


def test_the_image_limits_are_well_inside_what_the_services_take() -> None:
    worker = WorkerSettings()
    mib = 1024 * 1024
    assert worker.model_input_max_pixels <= 4_000_000  # ocsr takes 17.5 MP; it looks at 512 px
    assert worker.model_input_max_side <= 4096  # ocsr takes 8192
    assert worker.model_input_max_bytes < 20 * mib  # ocsr and cls take 20 MiB bodies
    assert worker.seg_input_max_pixels < 40_000_000  # seg takes 40 MP
    assert worker.seg_input_max_bytes < 40 * mib  # and 40 MiB bodies


def test_response_limits_are_per_service() -> None:
    worker = WorkerSettings()
    assert worker.max_response_bytes("seg") > worker.max_response_bytes("ocsr")
    assert worker.max_response_bytes("ocsr") == worker.max_response_bytes("cls")


# --- replicas and fair dispatching ---------------------------------------------------------


def test_the_concurrency_defaults_are_per_replica_and_one() -> None:
    """The worker multiplies them by the replicas it finds, so one keeps each replica fed
    without filling its queue. (Before P3-04b seg and cls were 2, as service totals.)"""
    worker = WorkerSettings()
    assert [worker.concurrency(s) for s in ("seg", "ocsr", "cls", "chem")] == [1, 1, 1, 1]


def test_the_dispatching_defaults() -> None:
    worker = WorkerSettings()
    assert worker.replica_refresh_seconds == 10
    assert worker.replica_cooldown_seconds == 10
    assert worker.small_first_burst == 4
    assert worker.fast_lane_rescue_seconds == 30
    assert worker.worker_max_jobs == 4  # the beside-legacy layout (compose: 8 after cutover)


@pytest.mark.parametrize(
    "bad",
    [
        {"seg_concurrency": 0},
        {"replica_refresh_seconds": 0},
        {"replica_cooldown_seconds": 0},  # a cooldown of 0 would make "skipped" mean nothing
        {"small_first_burst": 0},  # 0 would put large calls before small ones
        {"fast_lane_rescue_seconds": -1},
    ],
)
def test_dispatching_settings_out_of_range_are_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        WorkerSettings(**bad)  # type: ignore[arg-type]


def test_the_rescue_can_be_switched_off() -> None:
    assert WorkerSettings(fast_lane_rescue_seconds=0).fast_lane_rescue_seconds == 0


def test_the_dispatching_settings_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPLICA_REFRESH_SECONDS", "3")
    monkeypatch.setenv("REPLICA_COOLDOWN_SECONDS", "20")
    monkeypatch.setenv("SMALL_FIRST_BURST", "7")
    monkeypatch.setenv("FAST_LANE_RESCUE_SECONDS", "0")
    worker = WorkerSettings()
    assert (worker.replica_refresh_seconds, worker.replica_cooldown_seconds) == (3, 20)
    assert (worker.small_first_burst, worker.fast_lane_rescue_seconds) == (7, 0)
