"""The `python -m decimer_<service>` entry point that every service shares."""

from __future__ import annotations

import os
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from .logs import configure


def serve(build_app: Callable[[], FastAPI], default_port: int) -> None:
    """Set up the JSON logs, then serve `build_app()` on 0.0.0.0:$PORT (default `default_port`)."""
    configure()
    # log_config=None: leave uvicorn's loggers to propagate into the JSON handler set above.
    uvicorn.run(
        build_app(),
        host="0.0.0.0",  # noqa: S104  # inside the container; compose decides who can reach it
        port=int(os.environ.get("PORT", str(default_port))),
        log_config=None,
    )
