from __future__ import annotations

import logging
from ipaddress import ip_address, ip_network
from pathlib import Path

import pytest

from decimer_api.settings import Settings


def test_defaults_match_the_roadmap_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "REDIS_URL",
        "JOB_TTL_SECONDS",
        "MAX_PDF_PAGES",
        "MAX_STRUCTURES",
        "MAX_UPLOAD_MB",
        "MAX_ACTIVE_JOBS_PER_IP",
        "TRUSTED_PROXY_CIDR",
        "DATA_DIR",
        "QUEUE_MAX",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.job_ttl_seconds == 3600
    assert settings.max_pdf_pages == 10
    assert settings.max_structures == 20
    assert settings.max_upload_mb == 100
    assert settings.max_upload_bytes == 100 * 1024 * 1024
    assert settings.max_active_jobs_per_ip == 2
    assert settings.queue_max == 50
    assert settings.data_dir == Path("/data/jobs")
    assert settings.trusted_proxy_cidr == []


def test_reads_the_names_in_env_example(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("JOB_TTL_SECONDS", "120")
    monkeypatch.setenv("MAX_PDF_PAGES", "3")
    monkeypatch.setenv("MAX_STRUCTURES", "5")
    monkeypatch.setenv("MAX_UPLOAD_MB", "7")
    monkeypatch.setenv("MAX_ACTIVE_JOBS_PER_IP", "4")
    monkeypatch.setenv("QUEUE_MAX", "9")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENTRY_DSN", "")  # listed in .env.example, not used here
    settings = Settings()
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.job_ttl_seconds == 120
    assert settings.max_pdf_pages == 3
    assert settings.max_structures == 5
    assert settings.max_upload_mb == 7
    assert settings.max_active_jobs_per_ip == 4
    assert settings.queue_max == 9
    assert settings.data_dir == tmp_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("   ", []),
        ("10.0.0.0/8", ["10.0.0.0/8"]),
        ("10.0.0.0/8, 192.0.2.0/24", ["10.0.0.0/8", "192.0.2.0/24"]),
        ("2001:db8::/32 172.16.0.0/12", ["2001:db8::/32", "172.16.0.0/12"]),
        ("10.1.2.3/8", ["10.0.0.0/8"]),  # host bits are tolerated, not trusted
    ],
)
def test_trusted_proxy_cidr_accepts_empty_lists_and_ipv6(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", raw)
    settings = Settings()
    assert settings.trusted_proxy_cidr == [ip_network(item) for item in expected]


def test_trusted_proxy_cidr_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", "not-a-cidr")
    with pytest.raises(ValueError, match="TRUSTED_PROXY_CIDR"):
        Settings()


def test_limits_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(max_active_jobs_per_ip=0)
    with pytest.raises(ValueError):
        Settings(queue_max=0)


def test_trusted_networks_contain_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", "10.0.0.0/8")
    settings = Settings()
    assert ip_address("10.1.2.3") in settings.trusted_proxy_cidr[0]


# --- TRUSTED_PROXY_CIDR safety --------------------------------------------------------------


@pytest.mark.parametrize("raw", ["0.0.0.0/0", "::/0", "10.0.0.1/32, 0.0.0.0/0", "0.0.0.0/0, ::/0"])
def test_a_network_that_trusts_everyone_is_refused(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", raw)
    with pytest.raises(ValueError, match="trust every client"):
        Settings()


@pytest.mark.parametrize(
    "raw", ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "10.0.0.0/23", "2001:db8::/32"]
)
def test_broad_networks_are_accepted_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", raw)
    with caplog.at_level(logging.WARNING, logger="decimer_api.settings"):
        settings = Settings()
    assert settings.trusted_proxy_cidr == [ip_network(raw)]
    [record] = caplog.records
    assert (
        raw in record.getMessage() and "proxy addresses, not client networks" in record.getMessage()
    )


@pytest.mark.parametrize(
    "raw", ["10.0.0.7/32", "10.0.0.0/24", "192.0.2.0/25", "2001:db8:1:2::/64", "2001:db8::1/128"]
)
def test_narrow_networks_do_not_warn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", raw)
    with caplog.at_level(logging.WARNING, logger="decimer_api.settings"):
        Settings()
    assert caplog.records == []


def test_each_broad_network_in_a_list_is_named(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TRUSTED_PROXY_CIDR", "10.0.0.0/8, 192.0.2.5/32, 172.16.0.0/12")
    with caplog.at_level(logging.WARNING, logger="decimer_api.settings"):
        Settings()
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "10.0.0.0/8" in messages and "172.16.0.0/12" in messages and "192.0.2.5" not in messages


def test_new_limits_have_sane_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MAX_JOB_PIXELS", "RUNNING_JOB_TTL_SECONDS", "INGEST_WAIT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.max_image_pixels == 50_000_000
    assert settings.max_job_pixels == 10 * settings.max_image_pixels
    assert settings.running_job_ttl_seconds == 20 * 60
    assert settings.queued_job_ttl_seconds == 6 * 3600
    assert settings.running_job_ttl_seconds < settings.queued_job_ttl_seconds
    assert settings.proxy_read_timeout_seconds == 60  # nginx's default
    assert settings.ingest_concurrency == 2


# --- timeouts and ingest concurrency --------------------------------------------------------


def test_waiting_plus_the_child_deadline_must_fit_inside_the_proxy_read_timeout() -> None:
    """Otherwise nginx gives up (504) while the API is still working on the request."""
    Settings(ingest_wait_seconds=20, ingest_timeout_seconds=30, proxy_read_timeout_seconds=60)
    with pytest.raises(ValueError, match="PROXY_READ_TIMEOUT_SECONDS"):
        Settings(ingest_wait_seconds=30, ingest_timeout_seconds=30, proxy_read_timeout_seconds=60)
    # a margin of 5 s is kept for everything around the child: 20 + 30 + 5 = 55 < 56
    with pytest.raises(ValueError, match="PROXY_READ_TIMEOUT_SECONDS"):
        Settings(ingest_wait_seconds=20, ingest_timeout_seconds=30, proxy_read_timeout_seconds=55)
    Settings(ingest_wait_seconds=20, ingest_timeout_seconds=30, proxy_read_timeout_seconds=56)


def test_the_timeouts_can_be_set_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROXY_READ_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("INGEST_TIMEOUT_SECONDS", "90")
    settings = Settings()
    assert settings.proxy_read_timeout_seconds == 120 and settings.ingest_timeout_seconds == 90
    monkeypatch.setenv("INGEST_TIMEOUT_SECONDS", "119")
    with pytest.raises(ValueError, match="PROXY_READ_TIMEOUT_SECONDS"):
        Settings()


def test_one_ingest_slot_would_let_a_single_client_block_everyone() -> None:
    """Each client may run one ingest at a time, so at least two slots are needed for the
    others to get in."""
    with pytest.raises(ValueError, match="at least 2"):
        Settings(ingest_concurrency=1)
    assert Settings(ingest_concurrency=2).ingest_concurrency == 2
