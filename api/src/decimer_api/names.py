"""IUPAC names for the structures an endpoint computes (validate, export): the api's own client of
the chem service.

The worker names a job's structures in its pipeline; the api names an edited structure when the
user validates or exports it. Both go through `Services.names` (`worker/services.py`): the same
size limits and checks on the answer. What differs is the patience. A person is waiting for a
validated structure, so a call here has no retries (`connect_retry_budget_seconds=0`,
`busy_retries=0`, `sent_retries=0`) and `NAMES_TIMEOUT_SECONDS` for everything, queueing for chem
included. A name that cannot be had in that time is `NO_NAME`, the same as a name Orthonym
declines: "No verified name" in the UI. A failure is logged and never reaches the client as an
error. The chem service takes SMILES only; the molfile is turned into a SMILES by RDKit first.

Names are never kept between requests: Orthonym can name one structure differently depending on
what it named before (both names verified), so a name is only as good as the call that made it.

The worker has priority on chem. The chem service has one naming process and cannot tell the
api's calls from the worker's, so the api keeps out of the way: at most ONE call in flight from
this process, and a validate that finds chem occupied by another request of this process answers
with no name at once (`patient=False`) instead of waiting in line; only an export, whose user is
waiting for a file and asked for the names, queues for the place (`patient=True`), inside the
same budget. So the api puts at most one batch at a time in front of the worker's, however many
people edit.

`CHEM_URL` empty means no names at all (`NoNames`): every name is null, no connection is made.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from .settings import Settings
from .worker.services import Services, make_http_client, make_services
from .worker.settings import WorkerSettings

log = logging.getLogger("decimer_api.names")

# chem refuses a longer SMILES, and takes at most this many per call.
MAX_SMILES_CHARS = 2000
BATCH_SIZE = 20


@dataclass(frozen=True)
class NameInfo:
    """A name, the tier it was built at and how it was checked; all three are None together."""

    name: str | None = None
    tier: str | None = None
    verified: str | None = None


NO_NAME = NameInfo()


def nameable(smiles: str) -> bool:
    """Whether chem can be asked about this SMILES: not empty, not too long, no dummy atom
    (an R group has no name)."""
    return bool(smiles) and "*" not in smiles and len(smiles) <= MAX_SMILES_CHARS


class Namer(Protocol):
    async def __call__(self, smiles: Sequence[str], *, patient: bool = False) -> list[NameInfo]:
        """One answer per SMILES, in order. Never raises (except when cancelled); every SMILES
        must be `nameable`. `patient=False`: answer with no names at once if another call of this
        process is using chem; `patient=True`: wait for it (within the time budget)."""
        ...

    async def aclose(self) -> None: ...


class NoNames:
    """Names are switched off (`CHEM_URL` is empty)."""

    async def __call__(self, smiles: Sequence[str], *, patient: bool = False) -> list[NameInfo]:
        return [NO_NAME] * len(smiles)

    async def aclose(self) -> None:
        return None


class ChemNamer:
    def __init__(
        self, services: Services, *, timeout_seconds: float, http: httpx.AsyncClient | None = None
    ) -> None:
        self._services = services
        self._timeout = timeout_seconds
        self._http = http
        self._chem = asyncio.Semaphore(1)  # this process's one place in front of chem

    async def __call__(self, smiles: Sequence[str], *, patient: bool = False) -> list[NameInfo]:
        names = [NO_NAME] * len(smiles)
        if not smiles:
            return names
        if not patient and self._chem.locked():
            log.info("chem is busy with another request of this process: no name for this one")
            return names
        try:
            async with asyncio.timeout(self._timeout), self._chem:
                for start in range(0, len(smiles), BATCH_SIZE):
                    batch = list(smiles[start : start + BATCH_SIZE])
                    for offset, answer in enumerate(await self._services.names(batch)):
                        if answer.name:
                            names[start + offset] = NameInfo(
                                answer.name, answer.tier, answer.verified
                            )
        except Exception as error:  # a name is a bonus: nothing here may become a 500
            log.warning("no names: %s: %s", type(error).__name__, error)
        return names

    async def aclose(self) -> None:
        await self._services.aclose()
        if self._http is not None:
            await self._http.aclose()


def build_namer(settings: Settings) -> Namer:
    if not settings.chem_url:
        return NoNames()
    worker = WorkerSettings(
        chem_url=settings.chem_url,
        chem_timeout_seconds=settings.names_timeout_seconds,
        connect_retry_budget_seconds=0,
        busy_retries=0,
        sent_retries=0,
    )
    http = make_http_client(worker)
    return ChemNamer(
        make_services(http, worker), timeout_seconds=settings.names_timeout_seconds, http=http
    )
