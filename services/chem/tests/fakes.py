"""Stand-ins for the naming machinery. Nothing here imports Orthonym, RDKit or starts a JVM.

`FakeNamer` replaces the whole supervisor (app tests, in process). `FakeEngine` replaces Orthonym
inside a real naming process (supervisor tests): the supervisor spawns a child that imports
`fakes:make_engine`, which works because the tests directory is on `sys.path` and a spawned child
inherits `sys.path`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable, Sequence

from decimer_chem.app import CAFFEINE_NAME, CAFFEINE_SMILES
from decimer_chem.results import CdkResult, NameResult
from decimer_chem.settings import Settings


def named(smiles: str) -> NameResult:
    if smiles == CAFFEINE_SMILES:
        return NameResult(CAFFEINE_NAME, "pin_verified", "opsin", None)
    return NameResult(f"name of {smiles}", "pin_verified", "opsin", None)


def fake_molfile(atoms: int, bonds: int, title: str = "fake") -> str:
    """A molblock of the right shape (the parent checks the shape), with `atoms` carbons in a chain
    and `bonds` bond lines; not a real structure."""
    lines = [title, "  fake", "", f"{atoms:3d}{bonds:3d}  0  0  0  0  0  0  0  0999 V2000"]
    lines += [
        f"{i:10.4f}{0.0:10.4f}{0.0:10.4f} C   0  0  0  0  0  0  0  0  0  0  0  0"
        for i in range(atoms)
    ]
    lines += [f"{1:3d}{2:3d}  1  0  0  0  0" for _ in range(bonds)]
    return "\n".join([*lines, "M  END", ""])


def molfile_for(smiles: str) -> CdkResult:
    """The fake CDK: caffeine gets the counts the selftest requires, everything else 3 and 2."""
    counts = (14, 15) if smiles == CAFFEINE_SMILES else (3, 2)
    return CdkResult(fake_molfile(*counts, title=smiles), None)


class FakeNamer:
    """The app-level `Namer`: records calls, answers by rule, can be held in flight."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.start_error: Exception | None = None
        self.calls: list[list[str]] = []
        self.rule: Callable[[str], NameResult] = named
        self.cdk_calls: list[list[str]] = []
        self.cdk_rule: Callable[[str], CdkResult] = molfile_for
        self.error: Exception | None = None
        self.hold: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self._inflight = 0

    @property
    def busy(self) -> bool:
        return self._inflight > 0

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def name_many(self, smiles: Sequence[str]) -> list[NameResult]:
        self.calls.append(list(smiles))
        self._inflight += 1
        self.entered.set()
        try:
            if self.hold is not None:
                await self.hold.wait()
            if self.error is not None:
                raise self.error
            return [self.rule(s) for s in smiles]
        finally:
            self._inflight -= 1

    async def cdk_many(self, smiles: Sequence[str]) -> list[CdkResult]:
        self.cdk_calls.append(list(smiles))
        self._inflight += 1
        self.entered.set()
        try:
            if self.hold is not None:
                await self.hold.wait()
            if self.error is not None:
                raise self.error
            return [self.cdk_rule(s) for s in smiles]
        finally:
            self._inflight -= 1


class FakeEngine:
    """The engine side of the supervisor protocol. The SMILES string picks the behaviour."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.recycle_requested = False

    def start(self) -> dict[str, object]:
        delay = os.environ.get("FAKE_ENGINE_START_DELAY")
        if delay:
            time.sleep(float(delay))
        if os.environ.get("FAKE_ENGINE_FAIL_START"):
            raise RuntimeError("jar missing at /nowhere/opsin.jar")
        if os.environ.get("FAKE_ENGINE_HANG_START"):
            time.sleep(60)
        if os.environ.get("FAKE_ENGINE_EXIT_START"):
            os._exit(3)  # a hard exit: no message reaches the parent
        return {"engine": "fake", "pid": os.getpid()}

    def name(self, smiles: str, timeout_s: float) -> NameResult:
        if smiles == "HANG":  # a call that ignores the soft limit, as a stuck JNI call would
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            while True:
                time.sleep(0.05)
        if smiles == "CRASH":
            os._exit(13)
        if smiles == "BOOM":
            raise RuntimeError("engine bug")
        if smiles == "BAD":
            raise ValueError("Invalid SMILES: BAD")
        if smiles == "LOG":
            logging.getLogger("orthonym.namer").info("winner='secret-structure-name'")
            logging.getLogger("orthonym.namer").warning("orthonym-warning-visible")
            return NameResult("logged", "pin_verified", "opsin", None)
        if smiles == "SUBCLASS":  # fields that are str subclasses from a module only we import
            from child_only_types import Derived

            return NameResult(Derived("n"), Derived("pin_verified"), Derived("opsin"), None)
        if smiles == "BADFIELD":  # a field that is not a string at all
            return NameResult(123, "pin_verified", "opsin", None)  # type: ignore[arg-type]
        if smiles == "HUGE":  # a reply far larger than any name: the parent must not read it
            return NameResult("x" * (2 << 20), "pin_verified", "opsin", None)
        if smiles == "SURROGATE":  # JSON allows "\ud800", a lone surrogate that is not UTF-8
            return NameResult("\ud800", "pin_verified", "opsin", None)
        if smiles == "CWD":  # where the process runs, reported as the name
            return NameResult(os.getcwd(), "pin_verified", "opsin", None)
        if smiles == "LEAK":  # a result, after which the process asks to be replaced
            self.recycle_requested = True
            return NameResult(str(os.getpid()), "pin_verified", "opsin", None)
        if smiles == "PID":
            return NameResult(str(os.getpid()), "pin_verified", "opsin", None)
        if smiles.startswith("SLOW"):
            time.sleep(0.2)
        if smiles == "DECLINE":
            return NameResult(None, "abstain", None, "UNNAMEABLE")
        return NameResult(f"{smiles}@{timeout_s:.2f}", "pin_verified", "opsin", None)

    def cdk(self, smiles: str, timeout_s: float) -> CdkResult:
        """The same behaviours for CDK requests. A molblock carries what the test wants to read
        back (the process id, the soft limit, the SMILES) in its title line."""
        if smiles == "HANG":
            signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
            while True:
                time.sleep(0.05)
        if smiles == "CRASH":
            os._exit(13)
        if smiles == "BOOM":
            raise RuntimeError("cdk bug")
        if smiles == "BADFIELD":
            return CdkResult(123, None)  # type: ignore[arg-type]
        if smiles == "SOFTTIMEOUT":  # what the real engine does: answer, then ask to be replaced
            self.recycle_requested = True
            return CdkResult(None, "timeout")
        if smiles == "BOTH":  # a molblock and a reason: not an answer
            return CdkResult(fake_molfile(3, 2), "invalid_smiles")
        if smiles == "NEITHER":
            return CdkResult(None, None)
        if smiles == "NOTMOL":  # a string that is not a molblock
            return CdkResult("this is not a molblock", None)
        if smiles == "OVERCAP":  # a real-shaped molblock just over the cap the parent enforces
            return CdkResult(fake_molfile(3000, 3000), None)
        if smiles == "HUGE":  # a reply far larger than the pipe carries
            return CdkResult(fake_molfile(30000, 30000), None)
        if smiles == "SURROGATE":
            return CdkResult(fake_molfile(3, 2, title="\ud800"), None)
        if smiles == "LEAK":  # a result, after which the process asks to be replaced
            self.recycle_requested = True
            return CdkResult(fake_molfile(3, 2, title=str(os.getpid())), None)
        if smiles == "PID":
            return CdkResult(fake_molfile(3, 2, title=str(os.getpid())), None)
        if smiles.startswith("SLOW"):
            time.sleep(0.2)
        if smiles == "DECLINE":
            return CdkResult(None, "invalid_smiles")
        return CdkResult(fake_molfile(3, 2, title=f"{smiles}@{timeout_s:.2f}"), None)


def make_engine(settings: Settings) -> FakeEngine:
    return FakeEngine(settings)


__all__ = ["FakeEngine", "FakeNamer", "fake_molfile", "make_engine", "molfile_for", "named"]
