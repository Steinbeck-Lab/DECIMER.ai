"""Job-scoped media files: strict names, resolved strictly inside the job's own folder."""

from __future__ import annotations

import re
from pathlib import Path

from .models import is_valid_job_id

# crop-<result index>.png and page-<page index>.png, nothing else. The worker writes them
# directly into the job folder, next to `input/`, which these names can never reach.
MEDIA_NAME = re.compile(r"(?:crop|page)-[0-9]{1,6}\.png")


def is_valid_media_name(name: str) -> bool:
    return MEDIA_NAME.fullmatch(name) is not None


def resolve_media(data_dir: Path, job_id: str, name: str) -> Path | None:
    """The file for `name` in the folder of `job_id`, or None. Never follows a symlink out."""
    if not (is_valid_job_id(job_id) and is_valid_media_name(name)):
        return None
    folder = data_dir / job_id
    candidate = folder / name
    try:
        if folder.is_symlink() or candidate.is_symlink() or not candidate.is_file():
            return None
        resolved = candidate.resolve(strict=True)
        if resolved.parent != folder.resolve(strict=True):
            return None
    except OSError:
        return None
    return resolved
