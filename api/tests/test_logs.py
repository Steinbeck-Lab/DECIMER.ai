"""JSON logs for the api and the worker: one object per line, no user data."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from decimer_api.__main__ import uvicorn_config
from decimer_api.logs import JsonFormatter, configure_logging
from decimer_api.settings import Settings

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
JOB = "7a9f7a9f7a9f7a9f7a9f7a9f7a9f7a9f"
CLIENT_IP = "141.35.2.10"


@pytest.fixture
def clean_root() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def lines(captured: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in captured.splitlines() if line.strip()]


def test_a_line_is_one_json_object_with_time_level_logger_and_message(
    clean_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    logging.getLogger("decimer_api.worker").info("worker ready: queue=%s", "arq:queue")
    (entry,) = lines(capsys.readouterr().err)
    assert set(entry) == {"time", "level", "logger", "message"}
    assert entry["level"] == "INFO" and entry["logger"] == "decimer_api.worker"
    assert entry["message"] == "worker ready: queue=arq:queue"
    assert str(entry["time"]).endswith("+00:00") and "T" in str(entry["time"])


def test_a_traceback_is_one_field_of_the_same_line(
    clean_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("decimer_api").exception("job failed")
    (entry,) = lines(capsys.readouterr().err)
    assert entry["level"] == "ERROR"
    assert str(entry["exc"]).startswith("Traceback") and "RuntimeError: boom" in str(entry["exc"])


def test_no_smiles_and_no_public_address_is_written(
    clean_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    log = logging.getLogger("decimer_api")
    try:
        raise ValueError(f"cannot parse {ASPIRIN} from {CLIENT_IP}")
    except ValueError:
        log.exception("job %s failed on %s for %s", JOB, ASPIRIN, CLIENT_IP)
    out = capsys.readouterr().err
    for secret in (ASPIRIN, CLIENT_IP):
        assert secret not in out
    (entry,) = lines(out)
    assert entry["message"] == f"job {JOB} failed on [smiles] for [ip]"  # the job id stays
    assert "cannot parse [smiles] from [ip]" in str(entry["exc"])


def test_the_addresses_of_the_infrastructure_stay_readable(
    clean_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging()
    logging.getLogger("decimer_api.worker").info("replica %s joined", "172.29.80.5")
    (entry,) = lines(capsys.readouterr().err)
    assert entry["message"] == "replica 172.29.80.5 joined"


def test_the_level_and_the_text_format_follow_the_settings(
    clean_root: None, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(fmt="text", level="WARNING")
    log = logging.getLogger("decimer_api")
    log.info("quiet")
    log.warning("peer %s sent a header", CLIENT_IP)
    out = capsys.readouterr().err
    assert "quiet" not in out
    assert "WARNING decimer_api peer [ip] sent a header" in out and CLIENT_IP not in out


def test_the_settings_choose_json_at_info_by_default() -> None:
    settings = Settings()
    assert (settings.log_format, settings.log_level) == ("json", "INFO")


def test_uvicorn_logs_through_ours_and_its_access_log_stays_off() -> None:
    config = uvicorn_config(Settings(host="127.0.0.1", port=8123))
    assert config.log_config is None  # uvicorn installs no handlers of its own
    assert config.access_log is False


def test_the_formatter_never_raises_on_odd_messages() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s %s", ("only one",), None)
    record.msg = "no args at all %s"
    record.args = None
    assert json.loads(JsonFormatter().format(record))["message"] == "no args at all %s"
