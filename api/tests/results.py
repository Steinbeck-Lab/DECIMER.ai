"""Finished jobs for the tests of validate, export and report: results built with the same code
the worker uses, crop files on disk whose bytes say which crop they are, and a fake namer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from decimer_api.chemistry import analyse_smiles
from decimer_api.models import Job, JobStatus, Result, new_job_id
from decimer_api.names import NO_NAME, NameInfo
from decimer_api.settings import Settings
from decimer_api.store import Admission, JobStore
from tests.cdk_molblocks import PENTAVALENT_MOLBLOCK, PENTAVALENT_SMILES
from tests.factories import make_png

if TYPE_CHECKING:
    from tests.conftest import FakeClock

# Five different structures, none of them a substring or a permutation of another in its SMILES.
SMILES = ["CCO", "c1ccccc1", "CC(=O)O", "CCN", "C1CCCCC1"]


def name_of(smiles: str) -> str:
    return f"name of {smiles}"


def crop_bytes(index: int) -> bytes:
    """A PNG whose width is 40 + index: the export's image for result `index` is this."""
    return make_png((40 + index, 30))


def make_results(smiles: Sequence[str] = SMILES, *, named: bool = True) -> list[Result]:
    results = []
    for index, text in enumerate(smiles):
        chem = analyse_smiles(text)
        results.append(
            Result(
                index=index,
                page_index=0,
                bbox=(0, 0, 40 + index, 30),
                smiles=chem.smiles,
                inchikey=chem.inchikey,
                valid=chem.valid,
                is_structure=True,
                confidence=0.9,
                name=name_of(chem.smiles) if named and chem.valid else None,
                name_tier="pin_verified" if named and chem.valid else None,
                name_verified="opsin" if named and chem.valid else None,
                svg=chem.svg,
                molfile=chem.molfile,
            )
        )
    return results


def make_cdk_only_result(index: int = 0) -> Result:
    """A result as the worker leaves it for a SMILES that RDKit refused and CDK accepted:
    `valid: false` (RDKit's word), `validBy: cdk`, DECIMER's SMILES, no InChIKey and no name, and
    CDK's molblock."""
    assert not analyse_smiles(PENTAVALENT_SMILES).valid  # the premise
    return Result(
        index=index,
        page_index=0,
        bbox=(0, 0, 40 + index, 30),
        smiles=PENTAVALENT_SMILES,
        inchikey="",
        valid=False,
        valid_by="cdk",
        is_structure=True,
        confidence=0.9,
        svg="<svg/>",
        molfile=PENTAVALENT_MOLBLOCK,
    )


def make_incomplete_result(index: int, smiles: str, *, valid: bool = True) -> Result:
    """A result as the worker leaves it when ocsr stopped at its token limit: the cut-off
    SMILES kept (`valid` as RDKit judged it, no name whatever chem could say), `incomplete` set."""
    chem = analyse_smiles(smiles)
    assert chem.valid is valid  # the premise
    return Result(
        index=index,
        page_index=0,
        bbox=(0, 0, 40 + index, 30),
        smiles=chem.smiles,
        inchikey=chem.inchikey,
        valid=chem.valid,
        is_structure=True,
        confidence=0.6,
        incomplete=True,
        svg=chem.svg,
        molfile=chem.molfile,
    )


async def make_done_job(
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    results: list[Result] | None = None,
    *,
    ip: str = "owner",
    status: JobStatus = JobStatus.DONE,
    crops: bool = True,
) -> Job:
    """A job as the worker leaves it: admitted, finished, its crop files in the job folder."""
    job = Job.create(
        job_id=new_job_id(), client_ip_hash=ip, pages=1, pages_processed=1, now=clock()
    )
    folder = settings.data_dir / job.id
    (folder / "input").mkdir(parents=True)
    assert await store.admit(job) is Admission.OK
    job.status = status
    job.results = make_results() if results is None else results
    job.structures_found = len(job.results)
    if crops:
        for result in job.results:
            (folder / f"crop-{result.index}.png").write_bytes(crop_bytes(result.index))
    await store.finish(job)
    return job


class FakeNamer:
    """A `Namer` that records what it was asked and answers from a function (default: a name
    that says which SMILES it is for). `delay` makes it slow; `error` makes it fail."""

    def __init__(
        self,
        answer: Callable[[str], NameInfo] | None = None,
    ) -> None:
        self.answer = answer or (lambda smiles: NameInfo(name_of(smiles), "valid", "opsin"))
        self.calls: list[list[str]] = []
        self.patient: list[bool] = []
        self.closed = False
        self.error: Exception | None = None
        self.delay = 0.0

    async def __call__(self, smiles: Sequence[str], *, patient: bool = False) -> list[NameInfo]:
        self.calls.append(list(smiles))
        self.patient.append(patient)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return [self.answer(item) for item in smiles]

    async def aclose(self) -> None:
        self.closed = True


def no_names(_smiles: str) -> NameInfo:
    return NO_NAME


def molfile_of(smiles: str) -> str:
    """What the editor would send for a drawing of `smiles`."""
    return analyse_smiles(smiles).molfile


def crop_path(settings: Settings, job: Job, index: int) -> Path:
    return settings.data_dir / job.id / f"crop-{index}.png"
