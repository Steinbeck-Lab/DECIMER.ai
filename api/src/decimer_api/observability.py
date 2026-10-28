"""Error reporting to the self-hosted GlitchTip: the Sentry SDK, set up so that a
report carries a crash and nothing a user gave us.

OFF unless `SENTRY_DSN` is set. In the stack it names the `glitchtip-gate` container on the
`glitchtip-sdk` network (`http://<key>@glitchtip-gate:8000/<project>`; the gate lets the envelope
endpoint through and nothing else): nothing leaves the host.

Everything that could carry user data is switched off at the source, and `scrub.scrub_event` (the
`before_send`) is an allowlist behind that, so a setting that is wrong cannot be the only guard:

* `send_default_pii=False`, no request bodies, no local variables, no source lines, no breadcrumbs,
  no sessions, no tracing, no metrics or logs, no client reports;
* `default_integrations=False` and an explicit list: the standard ones add breadcrumbs from the
  standard library (`StdlibIntegration` also changes the environment handed to subprocesses, and the
  ingest children must keep their minimal one), the process's command line and its installed
  modules;
* the logging integration turns every `log.error` / `log.exception` into an event (that is how the
  pipeline's "job failed unexpectedly" and the 500 handler reach GlitchTip) and records no
  breadcrumbs.

The ingest children, the render children and the molfile-reading children never import this module.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import sentry_sdk
from sentry_sdk.integrations import Integration
from sentry_sdk.integrations.arq import ArqIntegration
from sentry_sdk.integrations.atexit import AtexitIntegration
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.integrations.threading import ThreadingIntegration

from . import __version__
from .scrub import scrub_event, scrub_transaction

log = logging.getLogger("decimer_api.observability")

SHUTDOWN_TIMEOUT_SECONDS = 2


def _valid_dsn(dsn: str) -> bool:
    """`http(s)://<public key>@<host>[:port]/<project id>`, the shape every Sentry SDK takes."""
    try:
        parts = urlsplit(dsn)
        project = parts.path.rstrip("/").rsplit("/", 1)[-1]
        parts.port  # noqa: B018  (raises ValueError for a port outside 0-65535)
        return (
            parts.scheme in {"http", "https"}
            and bool(parts.username)
            and bool(parts.hostname)
            and project.isdigit()
        )
    except ValueError:
        return False


def integrations(*, service: str) -> list[Integration]:
    """The integrations of a service: the web ones for the api, arq for the worker."""
    common: list[Integration] = [
        DedupeIntegration(),
        ExcepthookIntegration(always_run=True),
        ThreadingIntegration(),
        AtexitIntegration(),
        LoggingIntegration(level=None, event_level=logging.ERROR),
    ]
    if service == "api":
        return [
            *common,
            StarletteIntegration(transaction_style="url"),
            FastApiIntegration(transaction_style="url"),
        ]
    return [*common, ArqIntegration()]


def init_error_reporting(
    dsn: str | None,
    *,
    service: str,
    environment: str = "production",
    release: str | None = None,
    transport: Any = None,
) -> bool:
    """Start reporting; returns whether it did. Never raises: a bad DSN is logged and ignored,
    because the service must run without its error reporter."""
    if not dsn:
        return False
    if not _valid_dsn(dsn):
        log.warning(
            "SENTRY_DSN is not a valid DSN (http(s)://<key>@<host>/<project id>): reporting is off"
        )
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            transport=transport,
            release=release or __version__,
            environment=environment,
            server_name=service,  # the service, not the container's host name
            send_default_pii=False,
            max_request_body_size="never",
            include_local_variables=False,
            include_source_context=False,
            max_breadcrumbs=0,
            before_breadcrumb=lambda crumb, hint: None,
            default_integrations=False,
            auto_enabling_integrations=False,
            integrations=integrations(service=service),
            auto_session_tracking=False,
            send_client_reports=False,
            traces_sample_rate=0,
            enable_logs=False,
            enable_metrics=False,
            attach_stacktrace=True,
            shutdown_timeout=SHUTDOWN_TIMEOUT_SECONDS,
            before_send=scrub_event,
            before_send_transaction=scrub_transaction,
        )
    except Exception:
        log.warning("error reporting could not be started", exc_info=True)
        return False
    sentry_sdk.set_tag("service", service)
    log.info("error reporting is on for the %s", service)
    return True


def close_error_reporting() -> None:
    """Send what is queued (for a short while) and stop."""
    client = sentry_sdk.get_client()
    if client.is_active():
        client.close(timeout=SHUTDOWN_TIMEOUT_SECONDS)
