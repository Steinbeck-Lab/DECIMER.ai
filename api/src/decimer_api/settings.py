"""Runtime configuration, read from the environment (names as in `.env.example`)."""

from __future__ import annotations

import logging
import re
from ipaddress import IPv4Network, IPv6Network, ip_network
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

log = logging.getLogger("decimer_api.settings")

Network = IPv4Network | IPv6Network

MIB = 1024 * 1024
INGEST_MARGIN_SECONDS = 5.0  # for everything around the child: staging, Redis, the reply
BYTES_PER_MB = MIB


class Settings(BaseSettings):
    """Every setting has an environment variable of the same name in upper case."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # --- storage ---
    redis_url: str = "redis://localhost:6379/0"
    data_dir: Path = Path("/data/jobs")

    # --- retention (ROADMAP §3) ---
    # A finished job (done or failed) is deleted this long after it ends.
    job_ttl_seconds: int = Field(default=3600, gt=0)
    # A queued job's record lives this long. It must outlast the longest plausible wait in the
    # queue, otherwise a job could expire before it starts.
    queued_job_ttl_seconds: int = Field(default=6 * 3600, gt=0)
    # A running job's record lives this long after its last save(). The worker saves after every
    # stage; if it dies, the record (and the client's slot) goes after this long.
    running_job_ttl_seconds: int = Field(default=20 * 60, gt=0)
    # A client's slot is held from the moment its upload starts. If the request dies without
    # releasing it, the reservation expires after this long.
    reservation_ttl_seconds: int = Field(default=600, gt=0)
    # A client that deletes a job that is *running* keeps its slot for this long, whatever the
    # worker does: `CANCEL_POLL_SECONDS` (the worker looks that often) plus the longest read
    # timeout of a model service (seg, 180 s), the time a call abandoned by the worker can go on
    # computing in the service. `WorkerSettings.check_against` refuses a worker whose numbers add
    # up to more.
    cancelling_slot_seconds: int = Field(default=185, gt=0)
    # The sweep never touches a folder younger than this (an upload may still be in flight).
    sweep_grace_seconds: int = Field(default=900, gt=0)

    # --- limits (ROADMAP §3) ---
    max_pdf_pages: int = Field(default=10, gt=0)
    max_structures: int = Field(default=20, gt=0)  # applied by the worker
    max_upload_mb: int = Field(default=100, gt=0)
    max_upload_files: int = Field(default=50, gt=0)
    # Applies to every decoded image and to every PDF page rendered at 300 DPI. 50 MP is a B2
    # page (500 x 707 mm = 49 MP; A2 is 34.8 MP). The seg service itself refuses above 150 MP.
    max_image_pixels: int = Field(default=50_000_000, gt=0)
    # All pages of one job together. The biggest legitimate job is the page limit (10) at the
    # biggest page size (50 MP), so 10 x 50 MP; it also bounds the disk one job can take,
    # because every image is stored as PNG.
    max_job_pixels: int = Field(default=500_000_000, gt=0)
    # Uploads parsed at the same time in this process, each in a child process. One client may
    # run only one at a time (see `ingest_gate`), so this must be at least 2: with one slot a
    # single client could keep everybody else waiting.
    ingest_concurrency: int = 2
    # How long a request may wait for an ingest slot (its client's turn, then a free slot)
    # before it gets 503.
    ingest_wait_seconds: float = Field(default=20.0, gt=0)
    # Wall-clock limit for one ingest child process; it is killed after this.
    ingest_timeout_seconds: float = Field(default=30.0, gt=0)
    # The reverse proxy's read timeout (nginx proxy_read_timeout; its default is 60 s). The wait
    # plus the child's deadline plus a margin must fit inside it, or the proxy answers 504 while
    # the API is still working.
    proxy_read_timeout_seconds: float = Field(default=60.0, gt=0)
    # Address space an ingest child may allocate beyond what it uses after start-up (RLIMIT_AS).
    ingest_memory_mb: int = Field(default=1536, gt=0)

    # --- fairness ---
    max_active_jobs_per_ip: int = Field(default=2, gt=0)
    queue_max: int = Field(default=50, gt=0)
    queue_retry_after_seconds: int = Field(default=30, gt=0)
    # --- queues and the fast lane ---
    # The arq queue a worker serves (WORKER_QUEUE_NAME; arq's own default name) and the one the
    # api puts small jobs on (WORKER_FAST_QUEUE_NAME). A worker reads the first, so the fast
    # worker (`worker-fast`) is started with WORKER_QUEUE_NAME set to the fast queue's name.
    worker_queue_name: str = "arq:queue"
    worker_fast_queue_name: str = "decimer:fast"
    # A job of at most this many processed pages is small: it goes on the fast queue (when a
    # fast worker is alive) and its calls to the model services go before those of larger jobs.
    # An image counts as one page, so 1 means a pasted image, a single image upload or a one-page
    # PDF. 0 means no job is small.
    small_job_max_pages: int = Field(default=1, ge=0)
    # The api sends a small job to the fast queue only while a fast worker is alive (its
    # health-check key exists in Redis); the answer is kept this long.
    fast_lane_check_seconds: float = Field(default=5.0, gt=0)

    # CIDRs of the reverse proxies; X-Forwarded-For is honoured only from these peers.
    # Comma or space separated. Empty means no proxy is trusted.
    trusted_proxy_cidr: Annotated[list[Network], NoDecode] = Field(default_factory=list)
    # Key for hashing client addresses. Set it when running more than one API process;
    # when empty, a random key is generated at start (counters then reset on restart).
    ip_hash_secret: str = ""

    # --- media ---
    # Production hands the file to nginx with X-Accel-Redirect; development streams it itself.
    media_stream_direct: bool = False
    media_accel_prefix: str = "/_protected/jobs"

    # --- validate, export, report ---
    # The chem service (IUPAC names), base URL. Empty: every name is null ("no verified name").
    chem_url: str | None = None
    # The most validate and export spend on chem (waiting for the api's one place included) before
    # answering without a name. The worker has priority on chem: see `names`.
    names_timeout_seconds: float = Field(default=4.0, gt=0)
    # A molfile sent by the client, in bytes (a 900-atom V2000 structure is about 65 KB). A request
    # body is cut off at a multiple of this (see `guards`); nginx lets 1 MB through.
    max_molfile_bytes: int = Field(default=64 * 1024, gt=0)
    # Molfiles RDKit reads at the same time (validate, and each edit of an export), and ZIP
    # downloads streamed at the same time (each holds a file open per crop). The next request is
    # refused with 503 busy; nothing queues (see `capacity`).
    max_concurrent_analyses: int = Field(default=2, gt=0)
    max_concurrent_downloads: int = Field(default=8, gt=0)
    # ... of which one client address may hold this many (429 beyond), and for this long at most: a
    # download that is still going on after it is aborted and its place and files are freed.
    max_downloads_per_client: int = Field(default=2, gt=0)
    download_timeout_seconds: float = Field(default=120.0, gt=0)
    # Client molfiles are read in child processes (`analysis_pool`), as many as the analyses above.
    # A child may allocate this much beyond what it uses once RDKit is loaded (RLIMIT_AS), is given
    # this long for one molfile (an export's edits one after the other), this long to start, and is
    # replaced after this many molfiles.
    analysis_memory_mb: int = Field(default=384, gt=0)
    analysis_timeout_seconds: float = Field(default=5.0, gt=0)
    analysis_batch_seconds: float = Field(default=20.0, gt=0)
    analysis_startup_seconds: float = Field(default=30.0, gt=0)
    analysis_recycle_after: int = Field(default=500, gt=0)
    # Requests per client address (a fixed window), counted before the body is read. 429 beyond.
    validate_rate_per_minute: int = Field(default=60, gt=0)
    export_rate_per_minute: int = Field(default=10, gt=0)
    report_rate_per_hour: int = Field(default=30, gt=0)
    # Problem reports: a private directory (a volume that only the api mounts), one folder per
    # reported result. At most this many folders, and the crop image is left out above the size.
    reports_dir: Path = Path("/data/reports")
    max_reports: int = Field(default=5000, gt=0)
    # The bytes of all the reports together (counted from the disk, so a restart does not forget
    # them). Beyond it a report is refused with 503, and a warning is logged at 80 %.
    reports_max_bytes: int = Field(default=2 * 1024**3, gt=0)
    max_report_image_bytes: int = Field(default=4 * MIB, gt=0)
    # A report is deleted when it is older than this many days, whether or not anyone has looked
    # at it, by a run at start-up and then every hour. The privacy page says 12 months at the
    # latest, the shortest of which is 365 days: so 364, leaving the hour between runs to spare.
    reports_max_age_days: int = Field(default=364, gt=0)

    # --- serving (python -m decimer_api) ---
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)

    # --- observability ---
    # Error reports go to the GlitchTip of the stack, through its gate
    # (`http://<key>@glitchtip-gate:8000/<project id>`); empty switches them off. What a report may
    # carry is `scrub.py`; the SDK is set up in `observability.py`.
    sentry_dsn: str | None = None
    sentry_environment: str = "production"
    # The release the reports name (compose passes DECIMER_TAG); the package version when unset.
    sentry_release: str | None = None
    # `json`: one object per line (time, level, logger, message), for the containers; `text`: for a
    # terminal in development. The level applies to every logger.
    log_format: Literal["json", "text"] = "json"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # --- usage counter ---
    # Where the per-day counts go. Empty: REDIS_URL (the same Redis as the jobs, which loses them
    # when it restarts). Compose points it at a small Redis with a persistent volume (`usage`).
    usage_redis_url: str | None = None
    # How long a day's counts are kept. Counts have no addresses and no job ids.
    usage_ttl_days: int = Field(default=800, gt=0)

    # --- the ingest sidecar ---
    # `tcp://host:port` of the `ingest` container (`decimer_api.sidecar`). Empty: uploads are parsed
    # in child processes of this process, and molfiles are read by children of this process
    # (development, and the stack until the sidecar is switched on).
    ingest_sidecar_url: str | None = None
    # What the sidecar itself listens on (`python -m decimer_api.sidecar`; containers need 0.0.0.0).
    sidecar_host: str = "127.0.0.1"
    sidecar_port: int = Field(default=9000, ge=1, le=65535)
    # Where the sidecar keeps the request it is working on (its own volume; nothing else is on it).
    sidecar_scratch_dir: Path = Path("/scratch")

    @field_validator("trusted_proxy_cidr", mode="before")
    @classmethod
    def _split_cidrs(cls, value: Any) -> Any:
        if isinstance(value, str):
            items = [item for item in re.split(r"[,\s]+", value) if item]
            try:
                return [ip_network(item, strict=False) for item in items]
            except ValueError as error:
                raise ValueError(f"TRUSTED_PROXY_CIDR: {error}") from error
        return value

    @field_validator(
        "chem_url",
        "sentry_dsn",
        "sentry_release",
        "usage_redis_url",
        "ingest_sidecar_url",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _level_in_capitals(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("log_format", mode="before")
    @classmethod
    def _format_in_lower_case(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("ingest_sidecar_url")
    @classmethod
    def _sidecar_url_is_tcp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "tcp" or not parsed.hostname or parsed.port is None or parsed.path:
            raise ValueError("INGEST_SIDECAR_URL must look like tcp://ingest:9000")
        return value

    @field_validator("worker_queue_name", "worker_fast_queue_name")
    @classmethod
    def _queue_name_is_a_plain_key(cls, value: str) -> str:
        if not value or value != value.strip() or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("a queue name must be a non-empty Redis key without whitespace")
        return value

    @field_validator("ingest_concurrency")
    @classmethod
    def _at_least_two_ingests(cls, value: int) -> int:
        if value < 2:
            raise ValueError(
                "INGEST_CONCURRENCY must be at least 2: a client may use only one ingest slot "
                "at a time, and with a single slot that client would block everyone else."
            )
        return value

    @model_validator(mode="after")
    def _ingest_fits_in_the_proxy_timeout(self) -> Self:
        needed = self.ingest_wait_seconds + self.ingest_timeout_seconds + INGEST_MARGIN_SECONDS
        if needed >= self.proxy_read_timeout_seconds:
            raise ValueError(
                f"INGEST_WAIT_SECONDS ({self.ingest_wait_seconds:g}) + INGEST_TIMEOUT_SECONDS "
                f"({self.ingest_timeout_seconds:g}) + {INGEST_MARGIN_SECONDS:g} s margin must be "
                f"below PROXY_READ_TIMEOUT_SECONDS ({self.proxy_read_timeout_seconds:g}), "
                "or the proxy gives up on requests the API is still handling."
            )
        return self

    @field_validator("trusted_proxy_cidr")
    @classmethod
    def _check_cidrs(cls, networks: list[Network]) -> list[Network]:
        """A trusted network is one whose X-Forwarded-For we believe, so a broad one lets any
        client in it choose the address its limit is counted against."""
        for network in networks:
            if network.prefixlen == 0:
                raise ValueError(
                    f"TRUSTED_PROXY_CIDR: {network} would trust every client. "
                    "List the addresses of your proxies."
                )
            broad = 24 if network.version == 4 else 64
            if network.prefixlen < broad:
                log.warning(
                    "TRUSTED_PROXY_CIDR contains %s, wider than /%d. Every host in it can set "
                    "X-Forwarded-For and so choose the address its limits are counted against. "
                    "List proxy addresses, not client networks.",
                    network,
                    broad,
                )
        return networks

    def is_small_job(self, pages_processed: int) -> bool:
        """The size class of a job, from what the api knows when it creates it. The worker uses
        the same rule on the stored job, so both sides agree."""
        return pages_processed <= self.small_job_max_pages

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * BYTES_PER_MB

    @property
    def max_page_png_bytes(self) -> int:
        """A worst-case PNG of the biggest allowed image or page: what a child that writes one may
        write (RLIMIT_FSIZE)."""
        return 3 * self.max_image_pixels + 64 * MIB
