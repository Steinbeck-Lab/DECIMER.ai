"""`python -m decimer_api`: serve the API with uvicorn."""

from __future__ import annotations

import logging

import uvicorn

from .settings import Settings


def uvicorn_config(settings: Settings) -> uvicorn.Config:
    return uvicorn.Config(
        "decimer_api.app:create_app_from_env",
        factory=True,
        host=settings.host,
        port=settings.port,
        # request.client must stay the TCP peer: decimer_api.clientip decides, from
        # TRUSTED_PROXY_CIDR, whether X-Forwarded-For may be believed. uvicorn's own
        # forwarded-header handling would rewrite the peer before that code runs.
        proxy_headers=False,
        server_header=False,
        # The access log would write every client address; the proxy keeps its own log.
        access_log=False,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.Server(uvicorn_config(Settings())).run()


if __name__ == "__main__":
    main()
