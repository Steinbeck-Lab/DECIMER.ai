"""`python -m decimer_seg`: serve the seg model on 0.0.0.0:$PORT (default 8001)."""

from __future__ import annotations

from decimer_service.server import serve

from .app import build_app


def main() -> None:
    serve(build_app, default_port=8001)


if __name__ == "__main__":
    main()
