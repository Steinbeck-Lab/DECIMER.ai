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
from decimer_chem.results import NameResult
from decimer_chem.settings import Settings


def named(smiles: str) -> NameResult:
    if smiles == CAFFEINE_SMILES:
        return NameResult(CAFFEINE_NAME, "pin_verified", "opsin", None)
    return NameResult(f"name of {smiles}", "pin_verified", "opsin", None)


class FakeNamer:
    """The app-level `Namer`: records calls, answers by rule, can be held in flight."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.start_error: Exception | None = None
        self.calls: list[list[str]] = []
        self.rule: Callable[[str], NameResult] = named
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


def make_engine(settings: Settings) -> FakeEngine:
    return FakeEngine(settings)


__all__ = ["FakeEngine", "FakeNamer", "make_engine", "named"]
