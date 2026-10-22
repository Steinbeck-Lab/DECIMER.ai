"""Writes the job API's OpenAPI schema to stdout, as JSON, without Redis or a network.

Run it with the API's own environment:

    uv run --project ../api --no-sync python scripts/export-openapi.py

`create_app` takes everything it uses, so the schema is read from an app built with
stand-ins: nothing connects anywhere, and no request is ever served. Python is the
source of truth for the API (ROADMAP §3); `npm run gen:api` turns this into TypeScript.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, cast

from decimer_api.app import create_app
from decimer_api.settings import Settings


class _NoStore:
    """Stands in for the Redis-backed JobStore: never called, because no request is served."""


async def _no_enqueue(_job_id: str) -> None:
    raise RuntimeError("the schema export never enqueues")


def main() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        settings = Settings(data_dir=Path(data_dir))
        app = create_app(settings, cast(Any, _NoStore()), _no_enqueue)
        schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
