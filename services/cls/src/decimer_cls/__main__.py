"""`python -m decimer_cls`: serve the cls model on 0.0.0.0:$PORT (default 8003)."""

from __future__ import annotations

from decimer_service.server import serve

from .app import build_app


def main() -> None:
    serve(build_app, default_port=8003)


if __name__ == "__main__":
    main()
