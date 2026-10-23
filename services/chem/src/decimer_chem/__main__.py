"""`python -m decimer_chem`: serve the chem service on 0.0.0.0:$PORT (default 8004).

Imports live inside `main()` on purpose: the naming process is started with the `spawn` method,
which imports this module again in the child, and the child needs none of the web stack.
"""

from __future__ import annotations


def main() -> None:
    from decimer_service.server import serve

    from .app import build_app

    serve(build_app, default_port=8004)


if __name__ == "__main__":
    main()
