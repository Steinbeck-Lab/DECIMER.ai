"""Worker settings, read from the environment (a separate class so that `decimer_api.settings`
stays the API's). The job limits (`MAX_STRUCTURES`, `DATA_DIR`, the TTLs) come from the API's
`Settings`; everything here is about the model services and the cost of one job."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..settings import MIB, Settings

# arq cuts a run at the pipeline deadline plus this much, as a backstop for a run that has
# stopped listening to its own deadline.
ARQ_TIMEOUT_MARGIN_SECONDS = 60
# The heartbeat must be at most this fraction of the running record's life, so that several
# beats can fail before the record expires.
HEARTBEAT_FRACTION = 4


class WorkerSettings(BaseSettings):
    """Every setting has an environment variable of the same name in upper case."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # --- the model services (base URLs; the worker adds /infer or /name) ---
    seg_url: str = "http://127.0.0.1:8001"
    ocsr_url: str = "http://127.0.0.1:8002"
    cls_url: str = "http://127.0.0.1:8003"
    # Optional. Without it IUPAC names are null ("no verified name").
    chem_url: str | None = None

    # --- concurrency ---
    # Calls in flight at once to ONE REPLICA of a service, counted over all jobs of this process
    #. The worker finds the replicas through DNS and multiplies: a service with 3 replicas
    # takes 3 x this many calls. The model services run one inference at a time and queue a few
    # more (ocsr: 16, seg: 8), so 1 keeps a replica fed without filling its queue. ocsr is the
    # slow one (3 s for a structure, up to 55 s for an image that is not one), and its timeout
    # does not have to cover a queue.
    seg_concurrency: int = Field(default=1, gt=0)
    ocsr_concurrency: int = Field(default=1, gt=0)
    cls_concurrency: int = Field(default=1, gt=0)
    # chem names one structure at a time; a job sends one batch, so one call is enough.
    chem_concurrency: int = Field(default=1, gt=0)
    # Pages rendered at once, in this process. A 50 MP page needs about 0.5 GB while rendered.
    render_concurrency: int = Field(default=1, gt=0)
    # Jobs this process runs at once (arq max_jobs). Their calls share the replicas; a job needs
    # little memory beyond the page it renders. 4 beside legacy (2 replicas of each service), 8
    # after cutover (6/3/3).
    worker_max_jobs: int = Field(default=4, gt=0)

    # --- replicas and fair dispatching ---
    # The replicas of a service are looked up again this often, and at once after a call could not
    # connect.
    replica_refresh_seconds: float = Field(default=10.0, gt=0)
    # A replica that refused or dropped a connection is skipped this long (a call that finds it
    # dead goes to another replica at once). An answer from it ends the wait.
    replica_cooldown_seconds: float = Field(default=10.0, gt=0)
    # Calls of small jobs (a pasted image, one page) go before those of large jobs, but after this
    # many small calls in a row a waiting large call gets the next slot, so that a stream of small
    # jobs cannot starve a large one.
    small_first_burst: int = Field(default=4, ge=1)
    # The worker of the default queue looks this often for jobs stranded on the fast queue when no
    # fast worker is alive, and moves them to its own queue (0 switches it off).
    fast_lane_rescue_seconds: float = Field(default=30.0, ge=0)

    # --- timeouts (seconds) ---
    connect_timeout_seconds: float = Field(default=5.0, gt=0)
    # The read timeout is the longest a call may take, queue time inside the service included.
    # ocsr needs up to 55 s for an input that is not a structure; chem's own budget for a batch
    # is 60 s plus a restart of its naming process.
    seg_timeout_seconds: float = Field(default=180.0, gt=0)
    ocsr_timeout_seconds: float = Field(default=120.0, gt=0)
    cls_timeout_seconds: float = Field(default=60.0, gt=0)
    chem_timeout_seconds: float = Field(default=120.0, gt=0)
    # One job, start to finish. When it passes, the job fails with `timeout`. arq's own job
    # timeout is set a minute above it. `check_against` ties it to RUNNING_JOB_TTL_SECONDS.
    pipeline_timeout_seconds: float = Field(default=1000.0, gt=0)
    # A running job's record is saved at least this often, whatever the stage is doing.
    heartbeat_seconds: float = Field(default=60.0, gt=0)
    # A running job is checked this often for having been deleted by its owner (DELETE
    # /api/jobs/<id>); the run is stopped within this long. One Redis EXISTS per running job.
    cancel_poll_seconds: float = Field(default=2.0, gt=0)
    # arq waits this long for running jobs when it is told to stop (0: it cancels them, and a
    # cancelled job runs again after the restart). A container's stop grace period must exceed it.
    shutdown_wait_seconds: int = Field(default=0, ge=0)
    # After this many ocsr timeouts in one job the job gives up (`service_unavailable`): a
    # service that times out is wedged or overloaded, and every further crop would wait as long.
    ocsr_max_timeouts: int = Field(default=3, gt=0)

    # --- retries ---
    # A 503 means "busy": wait Retry-After (capped) and try again, this many times.
    busy_retries: int = Field(default=3, ge=0)
    busy_retry_after_default_seconds: float = Field(default=5.0, ge=0)
    busy_retry_after_max_seconds: float = Field(default=30.0, ge=0)
    # Could not connect (refused, or no answer to the connection attempt): the service is down or
    # starting. Retry with doubling backoff, each wait at most `connect_backoff_max_seconds`,
    # until this much time has been spent waiting. ocsr needs about 50 s to load, so the default
    # covers a restart. (Re-queueing the job with arq's Retry would redo the render and seg
    # work and count against arq's tries; waiting inside the call loses nothing.)
    connect_retry_budget_seconds: float = Field(default=90.0, ge=0)
    connect_backoff_seconds: float = Field(default=0.5, ge=0)
    connect_backoff_max_seconds: float = Field(default=10.0, gt=0)
    # The connection broke after the request was sent (reset, closed by the server): the work
    # may have started, or this very input may have crashed the service. Try again this many
    # times (a stale keep-alive connection is the usual cause), then give up on the call.
    sent_retries: int = Field(default=1, ge=0)

    # --- answers ---
    # The most a service's answer may be. seg sends the crops themselves (base64), the others
    # send a few hundred bytes to a few kilobytes.
    seg_response_limit_mb: float = Field(default=64.0, gt=0)
    small_response_limit_kb: float = Field(default=1024.0, gt=0)

    # --- rendering (a child process per page) ---
    render_timeout_seconds: float = Field(default=90.0, gt=0)
    render_memory_mb: int = Field(default=2048, gt=0)

    # --- images sent to the models ---
    # ocsr looks at a 512 px square and cls at 224 px, so a crop is cut down far below what they
    # accept (ocsr: 17.5 MP, 8192 px a side, 20 MiB; cls: 50 MP, 20 MiB). The crop file on disk
    # keeps its size. If the PNG is still over the byte limit it is shrunk further.
    model_input_max_pixels: int = Field(default=4_000_000, gt=0)
    model_input_max_side: int = Field(default=4096, gt=0)
    # Below the 20 MiB the services take, for the multipart framing.
    model_input_max_bytes: int = Field(default=19 * MIB, gt=0)
    # A page above this (a B2 page at 300 DPI is 49 MP; seg refuses 40 MP and 40 MiB) goes to seg
    # as a smaller copy. The boxes are scaled back and the crops cut from the full page.
    seg_input_max_pixels: int = Field(default=25_000_000, gt=0)
    seg_input_max_bytes: int = Field(default=36 * MIB, gt=0)

    # --- chemistry ---
    # Names are asked for in batches of at most this many structures (chem takes 20).
    chem_batch_size: int = Field(default=20, gt=0, le=20)
    # D4: the CDK fallback stays off until the golden-set data says otherwise. The seam is
    # `Deps.cdk_fallback`; with the flag on and nothing plugged in, the worker says so at start.
    cdk_fallback: bool = False

    @field_validator("chem_url", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def check_against(self, settings: Settings) -> None:
        """Raise `ValueError` when these settings and the API's disagree. Called at start.

        * A run that has stopped listening to its own deadline is cut by arq `ARQ_TIMEOUT_MARGIN`
          seconds after it; that has to happen before the running record would expire.
        * The heartbeat is what keeps the record alive during a long stage, so it must be well
          below the record's life (`HEARTBEAT_FRACTION` times).
        """
        ttl = settings.running_job_ttl_seconds
        problems: list[str] = []
        if self.pipeline_timeout_seconds + ARQ_TIMEOUT_MARGIN_SECONDS >= ttl:
            problems.append(
                f"PIPELINE_TIMEOUT_SECONDS ({self.pipeline_timeout_seconds:g}) plus arq's "
                f"{ARQ_TIMEOUT_MARGIN_SECONDS} s margin must be below "
                f"RUNNING_JOB_TTL_SECONDS ({ttl})"
            )
        slowest = max(
            self.seg_timeout_seconds,
            self.ocsr_timeout_seconds,
            self.cls_timeout_seconds,
            self.chem_timeout_seconds,
        )
        if self.cancel_poll_seconds + slowest > settings.cancelling_slot_seconds:
            problems.append(
                f"CANCEL_POLL_SECONDS ({self.cancel_poll_seconds:g}) plus the longest service "
                f"timeout ({slowest:g}) must not exceed CANCELLING_SLOT_SECONDS "
                f"({settings.cancelling_slot_seconds}): a deleted running job would give its "
                "client's slot back before the models are free of it"
            )
        if self.heartbeat_seconds * HEARTBEAT_FRACTION > ttl:
            problems.append(
                f"HEARTBEAT_SECONDS ({self.heartbeat_seconds:g}) must be at most a "
                f"{HEARTBEAT_FRACTION}th of RUNNING_JOB_TTL_SECONDS ({ttl})"
            )
        if problems:
            raise ValueError("; ".join(problems))

    def max_response_bytes(self, service: str) -> int:
        if service == "seg":
            return int(self.seg_response_limit_mb * MIB)
        return int(self.small_response_limit_kb * 1024)

    def url(self, service: str) -> str:
        """The base URL of `service` without a trailing slash."""
        value = {
            "seg": self.seg_url,
            "ocsr": self.ocsr_url,
            "cls": self.cls_url,
            "chem": self.chem_url or "",
        }[service]
        return value.rstrip("/")

    def read_timeout(self, service: str) -> float:
        return {
            "seg": self.seg_timeout_seconds,
            "ocsr": self.ocsr_timeout_seconds,
            "cls": self.cls_timeout_seconds,
            "chem": self.chem_timeout_seconds,
        }[service]

    def concurrency(self, service: str) -> int:
        """Calls in flight at once to one replica of `service` (to the process for render)."""
        return {
            "seg": self.seg_concurrency,
            "ocsr": self.ocsr_concurrency,
            "cls": self.cls_concurrency,
            "chem": self.chem_concurrency,
            "render": self.render_concurrency,
        }[service]
