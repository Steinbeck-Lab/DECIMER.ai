"""Runs a PDF through the earlier Laravel version of decimer.ai the way its browser form does.

The site keeps state in the session plus hidden form fields: upload, then segmentation,
then OCSR, each answered by a redirect back to "/" whose page carries the next fields.
"""

from __future__ import annotations

import html
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# Laravel builds https URLs behind a TLS-terminating proxy; without this the redirects break.
HEADERS = {"X-Forwarded-Proto": "https"}
STEP_TIMEOUT = 600.0


@dataclass
class LegacyRun:
    page_images: list[str]
    segments: list[str]
    smiles: list[str]
    classifier: list[str]
    seconds: dict[str, float]


def _field(page: str, name: str) -> str:
    flat = re.sub(r"\s+", " ", page)
    m = re.search(rf'name="{re.escape(name)}"\s+value="([^"]*)"', flat)
    if m is None:
        raise RuntimeError(f"hidden field {name!r} not found on the page")
    return html.unescape(m.group(1))


def _token(page: str) -> str:
    return _field(page, "_token")


def _json_list(raw: str) -> list[str]:
    value = json.loads(raw) if raw else []
    if not isinstance(value, list):
        raise RuntimeError(f"expected a JSON list, got {raw[:80]!r}")
    return [str(v) for v in value]


def run(base_url: str, pdf: Path) -> LegacyRun:
    seconds: dict[str, float] = {}
    with httpx.Client(base_url=base_url, headers=HEADERS, timeout=STEP_TIMEOUT) as client:

        def post(step: str, **kwargs: object) -> str:
            start = time.monotonic()
            r = client.post(f"/{step}", **kwargs)  # type: ignore[arg-type]
            seconds[step] = round(time.monotonic() - start, 2)
            if r.status_code not in (200, 302):
                raise RuntimeError(f"{step}: HTTP {r.status_code}")
            return client.get("/").text

        page = client.get("/").text
        with pdf.open("rb") as f:
            page = post(
                "file-upload",
                data={"_token": _token(page)},
                files={"file[]": (pdf.name, f, "application/pdf")},
            )
        img_paths = _field(page, "img_paths")

        page = post(
            "decimer-segmentation",
            data={
                "_token": _token(page),
                "img_paths": img_paths,
                "single_image_upload": "false",
            },
        )
        segment_paths = _field(page, "structure_depiction_img_paths")

        page = post(
            "decimer-ocsr",
            data={
                "_token": _token(page),
                "img_paths": img_paths,
                "structure_depiction_img_paths": segment_paths,
                "has_segmentation_already_run": "true",
                "single_image_upload": "false",
            },
        )
        return LegacyRun(
            page_images=_json_list(img_paths),
            segments=_json_list(segment_paths),
            smiles=_json_list(_field(page, "smiles_array")),
            classifier=_json_list(_field(page, "classifier_result_array")),
            seconds=seconds,
        )


def download(base_url: str, media_path: str, dest: Path) -> None:
    """Fetches one media file (a segment crop) the site exposes under /storage/."""
    url = "/storage/" + media_path.lstrip("/").removeprefix("storage/")
    with httpx.Client(base_url=base_url, headers=HEADERS, timeout=60.0) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
