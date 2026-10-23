"""Fixtures of the end-to-end test: `make_stack(backend, api=..., worker=...)` starts the whole
stack (see `stack.py`) and it is stopped when the test ends, also when the test failed."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from tests.e2e.stack import Stack
from tests.worker.fakes import FakeBackend

MakeStack = Callable[..., Awaitable[Stack]]


@pytest.fixture
async def make_stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MakeStack]:
    stacks: list[Stack] = []

    async def factory(
        backend: FakeBackend,
        *,
        api: dict[str, Any] | None = None,
        worker: dict[str, Any] | None = None,
    ) -> Stack:
        stack = await Stack.start(tmp_path, monkeypatch, backend, api=api, worker=worker)
        stacks.append(stack)
        return stack

    yield factory
    for stack in reversed(stacks):
        await stack.stop()
