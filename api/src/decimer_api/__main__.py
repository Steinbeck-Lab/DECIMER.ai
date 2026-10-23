"""`python -m decimer_api`: serve the API with uvicorn."""

from __future__ import annotations

import uvicorn

from .logs import configure_logging
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
        # No uvicorn logging set-up of its own: its loggers propagate to the JSON handler of
        # `logs.configure_logging` (uvicorn's default format is plain text with the client address).
        log_config=None,
    )


def main() -> None:
    settings = Settings()
    configure_logging(fmt=settings.log_format, level=settings.log_level)
    uvicorn.Server(uvicorn_config(settings)).run()


if __name__ == "__main__":
    main()
