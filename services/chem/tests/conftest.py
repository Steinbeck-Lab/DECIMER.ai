"""A session-wide check: the tests must not leave scratch directories of the naming process behind.

Every `ProcessNamer` makes a `decimer-chem-*` directory in the temporary directory and removes it
in `stop()` (and when `start()` fails). A test that leaks one shows up here as a teardown error.
(Another run of the service on the same machine during the tests would be counted too.)
"""

from __future__ import annotations

import glob
import os
import tempfile
from collections.abc import Iterator

import pytest


def scratch_dirs() -> set[str]:
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "decimer-chem-*")))


@pytest.fixture(scope="session", autouse=True)
def no_scratch_directories_are_left_behind() -> Iterator[None]:
    before = scratch_dirs()
    yield
    leaked = sorted(scratch_dirs() - before)
    assert not leaked, f"the tests left scratch directories behind: {leaked}"
