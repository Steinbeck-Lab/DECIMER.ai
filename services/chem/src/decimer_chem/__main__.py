"""`python -m decimer_chem`: serve the chem service on 0.0.0.0:$PORT (default 8004).

Imports live inside `main()` on purpose: the naming process is started with the `spawn` method,
which imports this module again in the child, and the child needs none of the web stack.
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn
    from decimer_service.logs import configure

    from .app import build_app

    configure()
    # log_config=None: leave uvicorn's loggers to propagate into the JSON handler set above.
    uvicorn.run(
        build_app(),
        host="0.0.0.0",  # noqa: S104  # inside the container; compose decides who can reach it
        port=int(os.environ.get("PORT", "8004")),
        log_config=None,
    )


if __name__ == "__main__":
    main()
