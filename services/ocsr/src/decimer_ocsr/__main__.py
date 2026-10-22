"""`python -m decimer_ocsr`: serve the ocsr model on 0.0.0.0:$PORT (default 8002)."""

from __future__ import annotations

import os

import uvicorn
from decimer_service.logs import configure

from .app import build_app


def main() -> None:
    configure()
    # log_config=None: leave uvicorn's loggers to propagate into the JSON handler set above.
    uvicorn.run(
        build_app(),
        host="0.0.0.0",  # noqa: S104  # inside the container; compose decides who can reach it
        port=int(os.environ.get("PORT", "8002")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
