"""Log privacy. The structures people draw, and the names made for them, never reach the logs.

Orthonym logs the name it picked, and exception messages can carry the SMILES, so:

* `RedactOrthonym` (a handler filter, installed in the naming process) reduces every record from
  an `orthonym*` logger to its logger, level and one fixed sentence;
* `log_failure` logs an exception as its type and its frames, never its message.
"""

from __future__ import annotations

import logging
import traceback

_REDACTED = "orthonym log message withheld"

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class RedactOrthonym(logging.Filter):
    """Rewrite records of the `orthonym` loggers to fixed text; leave every other record alone."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "orthonym" or record.name.startswith("orthonym."):
            record.msg = _REDACTED
            record.args = None
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            for key in [k for k in vars(record) if k not in _STANDARD]:
                del record.__dict__[key]
        return True


def failure_fields(exc: BaseException) -> dict[str, object]:
    """The exception's type and its stack frames (file, line, function), without its message."""
    # Not `traceback.format_tb`: that prints each source line, and a line such as
    # `raise ValueError(f"... {smiles}")` is code, but a literal in it could be anything.
    frames = [
        f"{frame.filename}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)
    ]
    return {"exc_type": type(exc).__name__, "traceback": "\n".join(frames)}


def log_failure(log: logging.Logger, message: str, exc: BaseException, **extra: object) -> None:
    """Log `message` at ERROR with the exception's type and frames, not its message."""
    log.error(message, extra={**extra, **failure_fields(exc)})
