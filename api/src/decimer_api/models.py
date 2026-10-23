"""The job model. Python is the source of truth; JSON uses camelCase.

Two layers, so that nothing derived is ever stored:

* `Job` (with `Result`) is the **record** kept in Redis. It holds the client-address hash and
  nothing that can be computed from the rest.
* `JobView` (with `ResultView`) is what the API **returns**: the record without the hash, plus
  the queue position, the compound number, the media URL of every crop and of every page.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from pydantic.alias_generators import to_camel

JOB_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def new_job_id() -> str:
    """128 random bits as 32 lowercase hex characters; never sequential."""
    return secrets.token_hex(16)


def is_valid_job_id(value: str) -> bool:
    return JOB_ID_PATTERN.fullmatch(value) is not None


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, validate_by_alias=True
    )


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def terminal(self) -> bool:
        return self in (JobStatus.DONE, JobStatus.FAILED)


class StageName(StrEnum):
    UPLOAD = "upload"
    RENDER = "render"
    SEGMENT = "segment"
    RECOGNISE = "recognise"
    CHECK = "check"


class StageState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"


class Stage(ApiModel):
    name: StageName
    state: StageState


class JobWarning(ApiModel):
    """A machine-readable code (the UI picks the wording) and an English fallback message."""

    code: str
    message: str


# Who read the SMILES as a structure: RDKit, or (when RDKit refused it) CDK alone.
ValidBy = Literal["rdkit", "cdk"]


class Result(ApiModel):
    """One recognised structure, as stored. Filled in by the worker."""

    index: int = Field(ge=0, description="0-based position in the job; edits and exports use it.")
    page_index: int = Field(ge=0, description="0-based page the structure was found on.")
    bbox: tuple[int, int, int, int] = Field(
        description="[x0, y0, x1, y1] in pixels of the rendered page."
    )
    smiles: str
    inchikey: str = Field(description="Empty when the SMILES is invalid.")
    valid: bool = Field(description="RDKit accepts the SMILES.")
    valid_by: ValidBy | None = Field(
        default=None,
        description="Who accepted the SMILES: `rdkit` (then `valid` is true), `cdk` (RDKit refused "
        "it, CDK read it: `valid` is false, `molfile` and `svg` are CDK's reading) or null "
        "(neither). Equals `rdkit` for every valid result.",
    )
    is_structure: bool | None = Field(
        description="The classifier believes this is a chemical structure. "
        "Null when the classifier gave no answer (the worker could not reach it)."
    )
    confidence: float = Field(ge=0, le=1, description="Recognition confidence.")
    name: str | None = Field(default=None, description="IUPAC name that passed the round trip.")
    name_tier: str | None = Field(default=None, description="Tier of `name`, e.g. pin_verified.")
    name_verified: str | None = Field(
        default=None,
        description="How `name` was checked, as chem reports it (e.g. opsin); null without a name.",
    )
    svg: str = Field(default="", description="RDKit depiction as an inline SVG string.")
    molfile: str = Field(
        default="",
        description="V2000 molfile with 2D coordinates: RDKit's, or CDK's when `validBy` is cdk.",
    )

    @model_validator(mode="after")
    def _valid_means_rdkit(self) -> Self:
        # A record stored before `valid_by` existed has none: a valid result was RDKit's.
        if self.valid and "valid_by" not in self.model_fields_set:
            self.valid_by = "rdkit"
        return self


class ResultView(Result):
    """A result as returned by the API."""

    crop_url: str = Field(description="URL of the crop image of this result.")

    @computed_field(description="1-based compound number shown in the UI.")  # type: ignore[prop-decorator]
    @property
    def number(self) -> int:
        return self.index + 1


def initial_stages() -> list[Stage]:
    return [
        Stage(name=StageName.UPLOAD, state=StageState.DONE),
        *(
            Stage(name=name, state=StageState.PENDING)
            for name in (
                StageName.RENDER,
                StageName.SEGMENT,
                StageName.RECOGNISE,
                StageName.CHECK,
            )
        ),
    ]


class JobBase(ApiModel):
    id: str
    status: JobStatus
    stages: list[Stage]
    pages: int = Field(description="Pages in the upload (an image counts as one page).")
    pages_processed: int = Field(description="Pages that will be, or were, processed.")
    truncated: bool = Field(description="A page or structure limit cut the job short.")
    structures_found: int = 0
    structures_dropped: int = Field(default=0, description="Found but not returned (the cap).")
    warnings: list[JobWarning] = Field(default_factory=list)
    error: str | None = Field(default=None, description="Set when `status` is failed.")
    created_at: datetime
    expires_at: datetime = Field(description="When the job and its files are deleted.")


class Job(JobBase):
    """The stored record. The client address is kept only as a keyed hash."""

    results: list[Result] = Field(default_factory=list)
    client_ip_hash: str

    @classmethod
    def create(
        cls,
        *,
        job_id: str,
        client_ip_hash: str,
        pages: int,
        pages_processed: int,
        now: datetime,
        warnings: list[JobWarning] | None = None,
        truncated: bool = False,
    ) -> Self:
        return cls(
            id=job_id,
            status=JobStatus.QUEUED,
            stages=initial_stages(),
            pages=pages,
            pages_processed=pages_processed,
            truncated=truncated,
            warnings=warnings or [],
            created_at=now,
            expires_at=now,  # set by the store when the record is saved
            client_ip_hash=client_ip_hash,
        )


class JobView(JobBase):
    """The job as the API returns it."""

    queue_position: int | None = Field(
        default=None, description="1-based place in the queue while `status` is queued."
    )
    page_urls: list[str] = Field(
        default_factory=list,
        description="URLs of the page images of the upload, in page order: `page-0.png` to "
        "`page-<pagesProcessed - 1>.png`, one for each page the job processes, and same-origin "
        "like `cropUrl`. An upload of images lists every image; a PDF lists its first "
        "`MAX_PDF_PAGES` pages (the pages past the cap are not processed, so they are not "
        "listed). The list is the plan, not a check that the files exist: a page the worker "
        "could not render (warning `page_render_failed`), or has not rendered yet, answers 404.",
    )
    results: list[ResultView] = Field(default_factory=list)

    @classmethod
    def from_job(cls, job: Job, queue_position: int | None = None) -> Self:
        base = job.model_dump(exclude={"results", "client_ip_hash"})
        results = [
            ResultView(
                **result.model_dump(),
                crop_url=f"/api/jobs/{job.id}/media/crop-{result.index}.png",
            )
            for result in job.results
        ]
        return cls(
            **base,
            page_urls=[
                f"/api/jobs/{job.id}/media/page-{index}.png" for index in range(job.pages_processed)
            ],
            results=results,
            queue_position=queue_position if job.status is JobStatus.QUEUED else None,
        )
