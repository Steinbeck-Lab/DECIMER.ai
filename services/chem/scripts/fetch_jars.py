"""Development helper: download the OPSIN and centres jars once, with Orthonym's pinned checksums.

The service never downloads at runtime; this is the explicit step that puts the jars on disk
(the Docker image does the same once, at build time, and bakes them in).

    CHEM_JARS_DIR=/some/dir uv run python scripts/fetch_jars.py   # default: Orthonym's cache dir
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def main() -> None:
    if os.environ.get("CHEM_JARS_DIR"):
        os.environ["ORTHONYM_JAR_DIR"] = os.environ["CHEM_JARS_DIR"]
    from orthonym.jars import fetch_all

    for kind, path in fetch_all(verbose=True).items():
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        print(f"{kind}: {path}  sha256={digest}")


if __name__ == "__main__":
    main()
