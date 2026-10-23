"""`python -m decimer_ocsr`: serve the ocsr model on 0.0.0.0:$PORT (default 8002)."""

from __future__ import annotations

from decimer_service.server import serve

from .app import build_app


def main() -> None:
    serve(build_app, default_port=8002)


if __name__ == "__main__":
    main()
