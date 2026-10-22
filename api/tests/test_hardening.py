"""Making the API process non-dumpable (PR_SET_DUMPABLE), so its children cannot read it."""

from __future__ import annotations

import ctypes
import logging
import sys

import pytest

from decimer_api import hardening
from decimer_api.hardening import make_undumpable

PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4


@pytest.mark.skipif(sys.platform != "linux", reason="prctl is Linux only")
def test_the_process_becomes_non_dumpable_and_the_flag_is_really_set() -> None:
    libc = ctypes.CDLL(None)
    try:
        assert make_undumpable() is True
        assert libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    finally:
        libc.prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)


def test_it_is_a_logged_no_op_elsewhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hardening, "_is_linux", lambda: False)
    with caplog.at_level(logging.INFO, logger="decimer_api.hardening"):
        assert make_undumpable() is False
    assert any("not Linux" in record.getMessage() for record in caplog.records)


def test_a_failing_prctl_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingLibc:
        def prctl(self, *_args: int) -> int:
            return -1

    monkeypatch.setattr(hardening.ctypes, "CDLL", lambda *_a, **_k: FailingLibc())
    with caplog.at_level(logging.WARNING, logger="decimer_api.hardening"):
        assert make_undumpable() is False
    assert any("PR_SET_DUMPABLE" in record.getMessage() for record in caplog.records)
