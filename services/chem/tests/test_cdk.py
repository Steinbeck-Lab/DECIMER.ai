"""`CdkLayout` without a JVM: the thread, the soft deadline and the guards around the Java calls.

What CDK itself does with a SMILES (accept, decline, lay out) is tested with the real JVM in
`test_cdk_integration.py`; here the conversion is replaced by a function so the machinery around it
can be checked fast.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from decimer_chem.cdk import MAX_ATOMS, MAX_BONDS, CdkLayout, counts_of
from decimer_chem.results import MAX_SMILES_CHARS, CdkResult

LOADER = object()


class FakeJavaThread:
    """`java.lang.Thread`, as far as `molfile` uses it: it records the context class loader."""

    loaders: list[tuple[str, object]] = []

    @classmethod
    def currentThread(cls) -> type[FakeJavaThread]:  # noqa: N802
        return cls

    @classmethod
    def setContextClassLoader(cls, loader: object) -> None:  # noqa: N802
        cls.loaders.append((threading.current_thread().name, loader))


def layout_with(convert: Callable[[str], CdkResult]) -> CdkLayout:
    layout = CdkLayout(Path("/nonexistent/cdk-2.8.jar"))
    layout._classes = {"thread": FakeJavaThread}
    layout._loader = LOADER
    layout._convert = convert  # type: ignore[method-assign]
    return layout


def test_the_limits_are_what_decimer_can_write() -> None:
    # DECIMER decodes at most 302 tokens (assets/max_length.pkl; the last step is the end token),
    # each atom costs a token and each ring closure two: at most 301 atoms and 300 bonds. Far below
    # the 999 that a V2000 counts line can hold, which is why this is the limit and not that.
    assert (MAX_ATOMS, MAX_BONDS) == (300, 300)


def test_a_result_comes_back_from_a_thread_of_its_own_with_cdks_context_class_loader() -> None:
    FakeJavaThread.loaders.clear()
    seen: list[str] = []

    def convert(smiles: str) -> CdkResult:
        seen.append(threading.current_thread().name)
        return CdkResult(f"mol for {smiles}", None)

    assert layout_with(convert).molfile("CCO", 5.0) == CdkResult("mol for CCO", None)
    assert seen == ["chem-cdk"] and seen[0] != threading.current_thread().name
    # CDK's own logging and service loading look at the thread's context class loader
    assert FakeJavaThread.loaders == [("chem-cdk", LOADER)]


def test_every_conversion_runs_on_the_same_thread_so_the_jvm_attaches_it_once() -> None:
    # JPype attaches each Python thread that calls Java to the JVM and does not let go of it when
    # the thread ends. A thread per call leaked about 8 KB per structure: the naming process grew
    # from 300 MB to 536 MB over 16,000 structures. One thread for the life of the process does not.
    FakeJavaThread.loaders.clear()
    idents: set[int] = set()

    def convert(smiles: str) -> CdkResult:
        idents.add(threading.get_ident())
        return CdkResult("mol", None)

    layout = layout_with(convert)
    threads_before = threading.active_count()
    for _ in range(200):
        assert layout.molfile("CCO", 5.0) == CdkResult("mol", None)
    assert len(idents) == 1 and threading.get_ident() not in idents
    assert threading.active_count() == threads_before + 1  # the one worker, not one per call
    assert FakeJavaThread.loaders == [("chem-cdk", LOADER)]  # and its class loader is set once


def test_a_smiles_over_the_cap_is_declined_without_asking_cdk() -> None:
    asked: list[str] = []

    def convert(smiles: str) -> CdkResult:
        asked.append(smiles)
        return CdkResult("mol", None)

    layout = layout_with(convert)
    assert layout.molfile("C" * (MAX_SMILES_CHARS + 1), 5.0) == CdkResult(None, "invalid_smiles")
    assert asked == []
    assert layout.molfile("C" * MAX_SMILES_CHARS, 5.0) == CdkResult("mol", None)  # exactly the cap
    assert len(asked) == 1


def test_a_conversion_past_the_soft_limit_is_a_timeout_and_the_process_is_to_be_replaced() -> None:
    release = threading.Event()
    started = threading.Event()

    def convert(smiles: str) -> CdkResult:
        started.set()
        release.wait(30)  # a Java call that does not come back
        return CdkResult("too late", None)

    layout = layout_with(convert)
    try:
        began = time.monotonic()
        result = layout.molfile("C", 0.3)
        elapsed = time.monotonic() - began
        assert started.is_set()
        assert result == CdkResult(None, "timeout")
        assert 0.25 <= elapsed < 5  # at the soft limit, not when the thread is done
        assert layout.needs_new_process is True
        # a thread of an earlier call is still in the JVM: this process must not be asked again
        with pytest.raises(RuntimeError, match="replaced"):
            layout.molfile("C", 1.0)
    finally:
        release.set()


def test_a_failure_to_set_the_context_class_loader_is_every_calls_error() -> None:
    class Broken(FakeJavaThread):
        @classmethod
        def setContextClassLoader(cls, loader: object) -> None:  # noqa: N802
            raise RuntimeError("no such loader")

    layout = layout_with(lambda smiles: CdkResult("mol", None))
    layout._classes = {"thread": Broken}
    for _ in range(2):
        with pytest.raises(RuntimeError, match="no such loader"):
            layout.molfile("CCO", 5.0)


def test_a_fast_conversion_does_not_wait_for_the_limit() -> None:
    began = time.monotonic()
    layout_with(lambda smiles: CdkResult("mol", None)).molfile("C", 30.0)
    assert time.monotonic() - began < 5
    assert layout_with(lambda smiles: CdkResult("mol", None)).needs_new_process is False


def test_an_exception_in_the_conversion_reaches_the_caller_as_an_engine_error() -> None:
    def convert(smiles: str) -> CdkResult:
        raise RuntimeError("CDK wrote a molblock that does not match its molecule")

    with pytest.raises(RuntimeError, match="does not match"):
        layout_with(convert).molfile("C", 5.0)


def test_cdk_before_start_is_an_error() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        CdkLayout(Path("/nonexistent/cdk-2.8.jar")).molfile("CCO", 1.0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\n  CDK\n\n  3  2  0  0  0  0  0  0  0  0999 V2000\n", (3, 2)),
        ("t\nu\nv\n999998  0  0  0  0  0  0  0  0999 V2000\n", (999, 998)),
        ("t\nu\nv\n  0  0  0  0  0  0  0  0  0  0999 V2000\n", (0, 0)),
        ("too\nshort\n", None),
        ("t\nu\nv\nxyz\n", None),
        ("", None),
    ],
)
def test_counts_of_reads_three_digits_each(text: str, expected: Any) -> None:
    assert counts_of(text) == expected
