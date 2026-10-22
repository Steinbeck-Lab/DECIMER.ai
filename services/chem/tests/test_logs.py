"""Log privacy: the structures people draw, and the names made for them, never reach the logs."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from decimer_service.logs import JsonFormatter

from decimer_chem.logs import RedactOrthonym, failure_fields, log_failure


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """The root logger writes JSON lines into a string, through the redacting filter."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactOrthonym())
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG)
    try:
        yield out
    finally:
        root.handlers[:] = before
        root.setLevel(level)


def lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_orthonym_records_keep_logger_and_level_and_nothing_else(stream: io.StringIO) -> None:
    try:
        raise ValueError("Invalid SMILES: CCOc1ccccc1")
    except ValueError:
        logging.getLogger("orthonym.namer").warning(
            "winner=%r for %s",
            "2-(acetyloxy)benzoic acid",
            "CC(=O)Oc1ccccc1C(=O)O",
            extra={"smiles": "CCO", "structure_name": "ethanol"},
            exc_info=True,
        )
    [record] = lines(stream)
    assert record["logger"] == "orthonym.namer"
    assert record["level"] == "WARNING"
    assert record["message"] == "orthonym log message withheld"
    assert set(record) == {"time", "level", "logger", "message"}
    text = stream.getvalue()
    for secret in ("acetyloxy", "CCO", "ethanol", "Invalid SMILES", "ValueError"):
        assert secret not in text


def test_other_loggers_are_left_alone(stream: io.StringIO) -> None:
    logging.getLogger("other").warning("fine %s", "text", extra={"n": 3})
    [record] = lines(stream)
    assert record["message"] == "fine text"
    assert record["n"] == 3


def test_failure_fields_have_the_exception_type_and_the_frames_but_not_the_message() -> None:
    def inner() -> None:
        raise KeyError("CC(=O)Oc1ccccc1C(=O)O")

    try:
        inner()
    except KeyError as exc:
        fields = failure_fields(exc)
    assert fields["exc_type"] == "KeyError"
    assert "in inner" in str(fields["traceback"])
    assert "CC(=O)O" not in json.dumps(fields)


def test_log_failure_logs_at_error_with_the_fields_and_no_exc_info(stream: io.StringIO) -> None:
    try:
        raise RuntimeError("secret message")
    except RuntimeError as exc:
        log_failure(logging.getLogger("decimer_chem"), "naming failed", exc, error_id="abc123")
    [record] = lines(stream)
    assert record["level"] == "ERROR"
    assert record["error_id"] == "abc123"
    assert record["exc_type"] == "RuntimeError"
    assert "exc" not in record  # JsonFormatter's formatted exc_info would carry the message
    assert "secret message" not in stream.getvalue()
