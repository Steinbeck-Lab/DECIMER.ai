"""Request and response bodies of the endpoints that work on a finished job's results: validate,
export, report and engines. The job record and its view are in `models`.

Requests refuse unknown fields: nothing the client adds is ever used, so it is better told.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import ConfigDict, Field, StrictInt

from .models import ApiModel


class ValidateRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    molfile: str = Field(description="The edited structure, as the editor writes it (V2000/V3000).")


class ValidateView(ApiModel):
    """What the server computes for an edited structure: the same values the worker computes for a
    recognised one, so the screen and the export agree (`chemistry.py`)."""

    smiles: str = Field(description="Canonical SMILES. Empty when the structure is not valid.")
    inchikey: str = Field(description="Empty when the structure is not valid or has no InChI.")
    svg: str = Field(
        description="RDKit depiction as an inline SVG string. For a structure RDKit reads but "
        "will not sanitize (`valid` false) it is the drawing of the molfile as it is; empty when "
        "the molfile has no atoms or the drawing fails."
    )
    valid: bool = Field(description="RDKit accepts the structure and it has at least one atom.")
    name: str | None = Field(
        default=None, description="IUPAC name that passed the round trip; null when none."
    )
    name_tier: str | None = Field(default=None, description="Tier of `name`.")
    name_verified: str | None = Field(default=None, description="How `name` was checked.")


class ExportFormat(StrEnum):
    ZIP = "zip"
    SDF = "sdf"
    CSV = "csv"


class ExportRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    format: ExportFormat
    molfiles: dict[str, str] = Field(
        default_factory=dict,
        description="Edited molfiles keyed by result index (0-based, as a decimal string). "
        "A result that is not listed is exported as the server has it. The order of the "
        "export is the job's, never the request's.",
    )


class ReportRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    index: StrictInt = Field(ge=0, description="0-based index of the result the problem is about.")


class EngineView(ApiModel):
    id: str = Field(description="Stable short name, e.g. decimer-segmentation.")
    name: str
    role: str = Field(description="What it does here, e.g. segmentation, ocsr, naming.")
    version: str
    revision: str | None = Field(
        default=None, description="Git commit the version was built from, when it is not a release."
    )


class EnginesView(ApiModel):
    engines: list[EngineView]
