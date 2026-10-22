"""Failures inside the worker.

Two families, kept apart on purpose:

* `ServiceError`: one HTTP call to a model service went wrong. The pipeline decides what that
  means (a crop is marked invalid, a name stays empty, or the whole job fails). The message is
  for the log only; it may quote what the service said.
* `PipelineFailure`: the job cannot go on. Its `Failure` (code and message) is what the user sees,
  so the message is fixed text with no detail from inside.
"""

from __future__ import annotations

from dataclasses import dataclass


class ServiceError(Exception):
    def __init__(self, service: str, message: str) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service


class ServiceRejected(ServiceError):
    """The service refused this input (422, 413). Retrying the same bytes cannot help."""


class ServiceFailed(ServiceError):
    """The service broke on this input (500), answered nonsense, or timed out."""


class ServiceTimedOut(ServiceFailed):
    """No answer within the read timeout. A kind of failure that also says the service is slow
    or stuck: an optional service that does this is left alone for the rest of the job."""


class ServiceUnavailable(ServiceError):
    """The service is busy (503) or unreachable, and the retries are used up."""


class RenderError(Exception):
    """A page could not be rendered (damaged PDF, child crashed, limit hit, took too long)."""


@dataclass(frozen=True)
class Failure:
    """A reason a job fails, as the user is told it."""

    code: str
    message: str


class PipelineFailure(Exception):
    def __init__(self, failure: Failure) -> None:
        super().__init__(f"{failure.code}: {failure.message}")
        self.failure = failure


# The user-facing failures. Messages are English fallbacks, like the API's error messages.
INPUT_MISSING = Failure(
    "input_missing", "The uploaded file could not be found. Please upload it again."
)
RENDER_FAILED = Failure(
    "render_failed", "A page of the document could not be rendered. Please check the file."
)
SEGMENTATION_FAILED = Failure(
    "segmentation_failed",
    "The pages could not be searched for chemical structures. Please try again.",
)
SERVICE_UNAVAILABLE = Failure(
    "service_unavailable",
    "A recognition service is temporarily unavailable. Please try again in a few minutes.",
)
TIMEOUT = Failure(
    "timeout", "The job took too long and was stopped. Try fewer pages or structures."
)
INTERNAL_ERROR = Failure("internal_error", "Something went wrong on our side. Please try again.")
