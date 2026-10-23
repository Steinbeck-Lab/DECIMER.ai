"""JSON logs for the containers: one object per line with `time`, `level`, `logger` and
`message`, and `exc` when there is a traceback. Same shape as the model services' logs
(`services/common`, `decimer_service.logs`), so one `jq` filter reads all of them.

Nothing a user gave us is written: `message` and `exc` go through `scrub.scrub_text`, which removes
molfiles, file names, SMILES strings and public IP addresses. Job ids are kept (the logs are how a
problem with one job is followed up; the id stops working an hour after the job ends) and so are
private addresses (docker's own: `replica 172.29.0.5 joined`, a proxy missing from
TRUSTED_PROXY_CIDR). The API never writes a client address (`clientip` keeps only a keyed hash) and
uvicorn's access log is off; this is the second line of defence.

`configure_logging` also routes uvicorn's and arq's loggers here (uvicorn would install its own
plain format otherwise: the API passes `log_config=None`).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .scrub import scrub_text


def _clean(text: str) -> str:
    return scrub_text(text, job_ids=False, public_addresses_only=True)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": _clean(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = _clean(self.formatException(record.exc_info))
        return json.dumps(payload)


class CleanTextFormatter(logging.Formatter):
    """The development format, with the same scrubbing."""

    def format(self, record: logging.LogRecord) -> str:
        return _clean(super().format(record))


def configure_logging(*, fmt: str = "json", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else CleanTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
