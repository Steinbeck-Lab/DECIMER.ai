"""Runs a PDF through the new stack's HTTP API the way the web app does.

POST /api/jobs (multipart, field "file"), then GET /api/jobs/{id} until the job is done or
failed. A finished job lists its results with the media URL of every crop; those are fetched
from the same origin. The API's limits are respected: one job at a time, 429 and 503 are
answered by waiting for Retry-After, and the whole run is bounded by `timeout`.

The job record exposes stage *states* only (no timestamps), so stage timings are what this
client saw while polling: accurate to about one poll interval.
"""

from __future__ import annotations

import email.utils
import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .docker_memory import MemorySampler
from .score import Prediction, to_inchikey

JOB_ID = re.compile(r"[0-9a-f]{32}")
STATUSES = ("queued", "running", "done", "failed")
STAGES = ("render", "segment", "recognise", "check")  # after "upload", in the worker's order
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

RETRY_POST = frozenset({429, 503})  # refused before a job exists: safe to send again
RETRY_GET = frozenset({429, 502, 503, 504})
DEFAULT_RETRY_AFTER = 5.0  # the API's 429 carries no Retry-After; nginx's carries 5
MIN_RETRY_SLEEP = 0.5
MAX_TRANSPORT_ERRORS = 5  # in a row, while polling or fetching
UPLOAD_TIMEOUT = 120.0  # one request: the API reads and checks the PDF before it answers
REQUEST_TIMEOUT = 30.0
CROP_TIMEOUT = 300.0  # for all crops together


class BadResponse(ValueError):
    """The API answered with something that is not the documented shape."""


@dataclass(frozen=True)
class Failure:
    """Why a run did not finish. `kind` is one of: unreachable, upload_rejected, http_error,
    bad_response, job_failed, timeout, interrupted, crop_download."""

    kind: str
    message: str
    http_status: int | None = None
    code: str | None = None  # the API's error code, or the failed job's warning code

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "http_status": self.http_status,
            "code": self.code,
        }


class _Abort(Exception):
    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.message)
        self.failure = failure


@dataclass(frozen=True)
class ApiResult:
    """One entry of a job's `results` (ResultView in web/src/api/openapi.json), minus the
    fields a scoring run has no use for (bbox, svg, molfile)."""

    index: int
    page_index: int
    smiles: str
    inchikey: str
    valid: bool
    is_structure: bool | None
    confidence: float
    name: str | None
    name_tier: str | None
    name_verified: str | None
    crop_url: str


@dataclass(frozen=True)
class JobSnapshot:
    """A JobView as read from the API."""

    id: str
    status: str
    stages: tuple[tuple[str, str], ...]  # (name, state)
    pages: int
    pages_processed: int
    truncated: bool
    structures_found: int
    structures_dropped: int
    warnings: tuple[tuple[str, str], ...]  # (code, message)
    error: str | None
    queue_position: int | None
    results: tuple[ApiResult, ...]


@dataclass
class NextRun:
    origin: str
    started_at: datetime  # wall clock, taken before anything is sent
    job_id: str | None = None
    job: JobSnapshot | None = None  # the last record seen
    seconds: dict[str, float] = field(default_factory=dict)
    status_seen: dict[str, float] = field(default_factory=dict)  # first poll that saw each
    stage_seen: dict[str, dict[str, float]] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)  # one entry per change
    retries: list[dict[str, Any]] = field(default_factory=list)
    polls: int = 0
    memory: dict[str, Any] | None = None
    failure: Failure | None = None

    @property
    def finished(self) -> bool:
        """The job is done and the whole run, crops included, went through."""
        return self.failure is None and self.job is not None and self.job.status == "done"

    def stage_seconds(self) -> dict[str, float]:
        """Time from one stage's first poll as done to the next one's, starting when the job was
        first seen running (the first stage therefore includes up to one poll interval)."""
        start = self.status_seen.get("running", self.seconds.get("upload"))
        durations: dict[str, float] = {}
        for name in STAGES:
            finished = self.stage_seen.get(name, {}).get("done")
            if start is None or finished is None:
                break
            durations[name] = round(finished - start, 2)
            start = finished
        return durations


# --- reading the API's answers ------------------------------------------------------------------

_REQUIRED: Any = object()
NONE = type(None)


def _field(
    data: dict[str, Any], key: str, kinds: tuple[type, ...], default: Any = _REQUIRED
) -> Any:
    """`data[key]`, which must be one of `kinds` (a bool is not an int). A missing key gives
    `default`, or a BadResponse when there is none."""
    if key not in data:
        if default is _REQUIRED:
            raise BadResponse(f"field {key!r} is missing")
        return default
    value = data[key]
    if not isinstance(value, kinds) or (isinstance(value, bool) and bool not in kinds):
        raise BadResponse(f"field {key!r} has the wrong type ({type(value).__name__})")
    return value


def _object(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BadResponse(f"{what} is not a JSON object")
    return value


def parse_result(raw: object) -> ApiResult:
    d = _object(raw, "a result")
    return ApiResult(
        index=_field(d, "index", (int,)),
        page_index=_field(d, "pageIndex", (int,)),
        smiles=_field(d, "smiles", (str,)),
        inchikey=_field(d, "inchikey", (str,), ""),
        valid=_field(d, "valid", (bool,)),
        is_structure=_field(d, "isStructure", (bool, NONE), None),
        confidence=float(_field(d, "confidence", (int, float))),
        name=_field(d, "name", (str, NONE), None),
        name_tier=_field(d, "nameTier", (str, NONE), None),
        name_verified=_field(d, "nameVerified", (str, NONE), None),
        crop_url=_field(d, "cropUrl", (str,)),
    )


def parse_job(raw: object) -> JobSnapshot:
    d = _object(raw, "the job")
    job_id = _field(d, "id", (str,))
    if JOB_ID.fullmatch(job_id) is None:
        raise BadResponse(f"{job_id[:40]!r} is not a job id")
    status = _field(d, "status", (str,))
    if status not in STATUSES:
        raise BadResponse(f"unknown job status {status!r}")
    stages = tuple(
        (_field(s, "name", (str,)), _field(s, "state", (str,)))
        for s in (_object(item, "a stage") for item in _field(d, "stages", (list,)))
    )
    warnings = tuple(
        (_field(w, "code", (str,)), _field(w, "message", (str,)))
        for w in (_object(item, "a warning") for item in _field(d, "warnings", (list,), []))
    )
    return JobSnapshot(
        id=job_id,
        status=status,
        stages=stages,
        pages=_field(d, "pages", (int,)),
        pages_processed=_field(d, "pagesProcessed", (int,)),
        truncated=_field(d, "truncated", (bool,)),
        structures_found=_field(d, "structuresFound", (int,), 0),
        structures_dropped=_field(d, "structuresDropped", (int,), 0),
        warnings=warnings,
        error=_field(d, "error", (str, NONE), None),
        queue_position=_field(d, "queuePosition", (int, NONE), None),
        results=tuple(parse_result(item) for item in _field(d, "results", (list,), [])),
    )


def retry_after(response: httpx.Response) -> float:
    """Seconds the server asks us to wait (delta-seconds or an HTTP date), else the default."""
    header = response.headers.get("retry-after", "").strip()
    if header.isdigit():
        return float(header)
    try:
        when = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return DEFAULT_RETRY_AFTER
    if when.tzinfo is None:
        return DEFAULT_RETRY_AFTER
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _api_error(response: httpx.Response) -> tuple[str | None, str]:
    """(code, message) from the API's `{"detail": {"code": ..., "message": ...}}`, or (None, the
    start of the body) when the answer has another shape (a proxy's page, say)."""
    try:
        detail = response.json()["detail"]
        return str(detail["code"]), str(detail["message"])
    except (ValueError, KeyError, TypeError):
        return None, response.text[:200]


def _http_failure(response: httpx.Response, what: str, kind: str) -> Failure:
    code, message = _api_error(response)
    return Failure(
        kind, f"{what}: HTTP {response.status_code}: {message}", response.status_code, code
    )


# --- the run -------------------------------------------------------------------------------------


def check_origin(base_url: str) -> httpx.URL:
    """The API's origin (scheme://host:port). The API puts root-relative URLs in its answers, so
    a path prefix in `base_url` would be ambiguous and is refused."""
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL as error:
        raise ValueError(f"not a URL: {base_url!r}") from error
    if url.scheme not in ("http", "https") or not url.host or url.path not in ("", "/"):
        raise ValueError(f"base URL must look like http://host:port, got {base_url!r}")
    return httpx.URL(scheme=url.scheme, host=url.host, port=url.port, path="/")


def new_client(origin: httpx.URL) -> httpx.Client:
    return httpx.Client(
        base_url=origin,
        timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=10.0),
        follow_redirects=False,
        headers={"User-Agent": "decimer-golden"},
    )


def _origin_of(url: httpx.URL) -> tuple[str, str, int | None]:
    return (url.scheme, url.host, url.port)


class _Runner:
    def __init__(
        self,
        client: httpx.Client,
        out: NextRun,
        *,
        poll: float,
        timeout: float,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self.client = client
        self.out = out
        self.poll = poll
        self.clock = clock
        self.sleep = sleep
        self.began = clock()
        self.attempt_began = self.began  # start of the latest request sent by `send`
        self.last_seen: tuple[Any, ...] = ()
        self.set_budget(timeout)

    def set_budget(self, timeout: float) -> None:
        self.timeout = timeout
        self.deadline = self.clock() + timeout

    # -- requests

    def _wait(self, seconds: float, why: str) -> None:
        """Sleep for a retry. When the wait would end after the deadline, give up now."""
        if seconds >= self.deadline - self.clock():
            raise _Abort(Failure("timeout", f"timed out after {self.timeout:g} s ({why})"))
        self.sleep(max(seconds, MIN_RETRY_SLEEP))

    def send(
        self, method: str, url: str, *, what: str, retry_on: frozenset[int], **kwargs: Any
    ) -> httpx.Response:
        """One request, repeated while the server says to wait (`retry_on` statuses, honouring
        Retry-After). A POST is never repeated after a network error: it may have started a job."""
        transport_errors = 0
        while True:
            self.attempt_began = self.clock()
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError as error:
                transport_errors += 1
                if method == "POST" or transport_errors >= MAX_TRANSPORT_ERRORS:
                    raise _Abort(Failure("unreachable", f"{what}: {error!r}")) from error
                self._wait(max(self.poll, 1.0), f"{what}: {type(error).__name__}")
                continue
            transport_errors = 0
            if response.status_code not in retry_on:
                return response
            wait = retry_after(response)
            self.out.retries.append(
                {
                    "what": what,
                    "status": response.status_code,
                    "retry_after": wait,
                    "at": round(self.clock() - self.began, 2),
                }
            )
            self._wait(wait, f"{what}: HTTP {response.status_code}, Retry-After {wait:g} s")

    def _job_from(self, response: httpx.Response, what: str) -> JobSnapshot:
        try:
            return parse_job(response.json())
        except ValueError as error:  # not JSON, or not a job
            raise _Abort(
                Failure("bad_response", f"{what}: {error}", response.status_code)
            ) from error

    # -- the job

    def upload_and_wait(self, pdf_name: str, pdf_bytes: bytes) -> None:
        """Raises _Abort unless the job is done."""
        response = self.send(
            "POST",
            "/api/jobs",
            what="upload",
            retry_on=RETRY_POST,
            files={"file": (pdf_name, pdf_bytes, "application/pdf")},
            timeout=UPLOAD_TIMEOUT,
        )
        if not response.is_success:
            kind = "upload_rejected" if 400 <= response.status_code < 500 else "http_error"
            raise _Abort(_http_failure(response, "upload", kind))
        started = self.attempt_began  # "upload start": the request the API accepted
        job = self._job_from(response, "upload")
        self.out.job_id = job.id
        self.out.seconds["upload"] = round(self.clock() - started, 2)
        self._observe(job, started)

        while job.status not in ("done", "failed"):
            if self.clock() >= self.deadline:
                raise _Abort(
                    Failure("timeout", f"not done after {self.timeout:g} s (status {job.status})")
                )
            self.sleep(min(self.poll, max(0.0, self.deadline - self.clock())))
            response = self.send("GET", f"/api/jobs/{job.id}", what="poll", retry_on=RETRY_GET)
            if not response.is_success:
                what = (
                    "poll (the job is gone or expired)" if response.status_code == 404 else "poll"
                )
                raise _Abort(_http_failure(response, what, "http_error"))
            job = self._job_from(response, "poll")
            self.out.polls += 1
            self._observe(job, started)

        total = self.clock() - started
        self.out.seconds["processing"] = round(total - self.out.seconds["upload"], 2)
        self.out.seconds["total"] = round(total, 2)
        if job.status == "failed":
            code = next((c for c, m in reversed(job.warnings) if m == job.error), None)
            raise _Abort(Failure("job_failed", job.error or "the job failed", code=code))

    def _observe(self, job: JobSnapshot, started: float) -> None:
        seen = round(self.clock() - started, 2)
        self.out.job = job
        self.out.status_seen.setdefault(job.status, seen)
        for name, state in job.stages:
            if state != "pending":
                self.out.stage_seen.setdefault(name, {}).setdefault(state, seen)
        snapshot = (job.status, job.stages, job.queue_position)
        if snapshot != self.last_seen:
            self.last_seen = snapshot
            self.out.trace.append(
                {
                    "t": seen,
                    "status": job.status,
                    "queue_position": job.queue_position,
                    "stages": dict(job.stages),
                }
            )

    # -- the crops

    def fetch_crops(self, job: JobSnapshot, run_dir: Path) -> None:
        """Every crop of a finished job into `run_dir`, from the media URLs it lists. A URL that
        leaves the API's origin is refused. Raises _Abort."""
        self.set_budget(CROP_TIMEOUT)
        origin = httpx.URL(str(self.client.base_url))
        for position, result in enumerate(job.results):
            target = origin.join(result.crop_url)
            if _origin_of(target) != _origin_of(origin):
                raise _Abort(
                    Failure(
                        "crop_download", f"crop {position}: {result.crop_url!r} leaves {origin}"
                    )
                )
            response = self.send("GET", str(target), what=f"crop {position}", retry_on=RETRY_GET)
            if not response.is_success:
                raise _Abort(_http_failure(response, f"crop {position}", "crop_download"))
            if not response.content.startswith(PNG_MAGIC):
                raise _Abort(Failure("crop_download", f"crop {position}: not a PNG image"))
            (run_dir / crop_path(position, result)).write_bytes(response.content)


def run(
    client: httpx.Client,
    pdf: Path,
    *,
    run_dir: Path | None = None,
    poll: float = 1.0,
    timeout: float = 900.0,
    sampler: MemorySampler | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> NextRun:
    """Upload `pdf`, wait for the job, then (when `run_dir` is given) fetch its crops into
    `run_dir/crops/` (which must exist). Never raises for a failed job or request: `failure` of
    the result says why. `timeout` bounds the upload and the wait together, retries included.
    `sampler`, when given, brackets the job (not the crop downloads)."""
    out = NextRun(origin=str(client.base_url), started_at=datetime.now(UTC))
    if sampler is not None:
        sampler.start()
    runner = _Runner(client, out, poll=poll, timeout=timeout, clock=clock, sleep=sleep)
    try:
        try:
            runner.upload_and_wait(pdf.name, pdf.read_bytes())
        finally:
            if sampler is not None:
                out.memory = sampler.stop()
        if run_dir is not None and out.job is not None:
            began = clock()
            runner.fetch_crops(out.job, run_dir)
            out.seconds["crops"] = round(clock() - began, 2)
    except _Abort as abort:
        out.failure = abort.failure
    except KeyboardInterrupt:
        out.failure = Failure("interrupted", "interrupted; the job, if any, is left to expire")
    return out


# --- results -> run directory --------------------------------------------------------------------


def crop_path(position: int, result: ApiResult) -> str:
    """Where a result's crop is kept in the run directory; the name never comes from the server."""
    return f"crops/{position:02d}_crop-{result.index}.png"


def classifier(is_structure: bool | None) -> str:
    """The legacy runner's `classifier` column holds 'True' or 'False'; empty: no answer."""
    return "" if is_structure is None else str(is_structure)


def to_predictions(job: JobSnapshot) -> list[Prediction]:
    """One prediction per result, in the API's order, then one empty row for each structure the
    API found but did not return (its cap), which `score` counts as "dropped by cap" like the
    legacy runner's empty rows. The InChIKey is recomputed here, from the SMILES, with the same
    RDKit call the legacy run was scored with."""
    predictions = [
        Prediction(
            index=position,
            smiles=result.smiles,
            inchikey=to_inchikey(result.smiles),
            segment=crop_path(position, result),
            classifier=classifier(result.is_structure),
            page=result.page_index + 1,
            valid=result.valid,
            confidence=result.confidence,
            name=result.name or "",
            name_tier=result.name_tier or "",
        )
        for position, result in enumerate(job.results)
    ]
    start = len(predictions)
    predictions += [
        Prediction(index=start + i, smiles="", inchikey=None) for i in range(job.structures_dropped)
    ]
    return predictions


def inchikey_differences(job: JobSnapshot, predictions: list[Prediction]) -> list[int]:
    """Positions whose InChIKey from the API is not the one computed here (an empty one from the
    API counts as none)."""
    return [
        position
        for position, result in enumerate(job.results)
        if (result.inchikey or None) != predictions[position].inchikey
    ]


def build_meta(
    out: NextRun,
    predictions: list[Prediction],
    *,
    pdf: Path,
    poll: float,
    timeout: float,
) -> dict[str, Any]:
    """The contents of run.json. `seconds`, `page_images` and `segments` are the legacy
    runner's keys; the rest is what the job record and the sampler add."""
    job = out.job
    seconds = dict(out.seconds)
    seconds["retry_wait"] = round(sum(r["retry_after"] for r in out.retries), 2)
    memory = out.memory
    if job is not None and job.pages_processed:
        if "total" in seconds:
            seconds["per_page"] = round(seconds["total"] / job.pages_processed, 2)
        if memory is not None:
            memory = {
                **memory,
                "peak_delta_per_page_mib": round(
                    memory["peak_delta_total_mib"] / job.pages_processed, 1
                ),
            }
    return {
        "stack": "next",
        "started_at": out.started_at.isoformat(timespec="seconds"),
        "base_url": out.origin,
        "pdf": pdf.name,
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "poll_seconds": poll,
        "timeout_seconds": timeout,
        "status": "done" if out.finished else "failed",
        "failure": out.failure.as_dict() if out.failure else None,
        "job_id": out.job_id,
        "seconds": seconds,
        "queued_seconds": (
            round(out.status_seen["running"] - out.seconds["upload"], 2)
            if "running" in out.status_seen and "upload" in out.seconds
            else None
        ),
        "stage_seconds": out.stage_seconds(),
        "stage_seen": out.stage_seen,
        "trace": out.trace,
        "polls": out.polls,
        "retries": out.retries,
        "pages": job.pages if job else None,
        "pages_processed": job.pages_processed if job else None,
        "page_images": job.pages_processed if job else None,
        "truncated": job.truncated if job else None,
        "structures_found": job.structures_found if job else None,
        "structures_dropped": job.structures_dropped if job else None,
        "segments": job.structures_found if job else None,
        "results": len(job.results) if job else None,
        "stages": dict(job.stages) if job else None,
        "warnings": [{"code": c, "message": m} for c, m in job.warnings] if job else [],
        "inchikey_differs_from_api": (
            inchikey_differences(job, predictions) if job and job.status == "done" else []
        ),
        "memory": memory,
    }
