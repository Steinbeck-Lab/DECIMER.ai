"""HTTP calls to the model services (seg, ocsr, cls) and to chem, over one shared client.

What this layer promises:

* One `httpx.AsyncClient` for everything, no environment proxies (the services are on the
  internal network), no redirects.
* A service is a set of replicas found through DNS (`replicas.py`, D18). A call goes to the
  least-busy healthy replica, at most `<SERVICE>_CONCURRENCY` calls in flight PER REPLICA,
  counted over every job of this process; when all are busy the calls wait in a fair queue
  (small jobs first, jobs taking turns, see `replicas.py`). The slot is held while a call waits
  to retry after a 503, so a busy replica is not hammered by the callers queued behind it.
* 503 means busy: wait `Retry-After` (default and cap from the settings) and try again, up to
  `BUSY_RETRIES` times; then `ServiceUnavailable`. The retry goes to the same replica.
* Could not connect (refused, or no answer to the connection attempt): that replica is down or
  starting and is skipped for a while, the names are looked up again, and the call goes to
  another replica at once if one is healthy. When none is, retry with doubling backoff (capped
  per wait) until `CONNECT_RETRY_BUDGET_SECONDS` have been spent waiting, which covers a cold
  start of ocsr; then `ServiceUnavailable`. The budget is per call, over all replicas.
* The connection broke after the request went out (reset, closed by the server): that replica
  is skipped for a while and the call is retried `SENT_RETRIES` times (once; on another replica
  when one is healthy), then `ServiceFailed` for this call. The service may have started the
  work or died on this very input, so it is not waited for. An input that crashes the service
  therefore takes down at most `1 + SENT_RETRIES` (2) replicas per call: the retry is the only
  time a request is sent again after it may have been processed (a refused connection never
  reached a replica, so moving on from it costs none).
* 422 and 413 (the service refuses this input) and every other status are never retried:
  `ServiceRejected` and `ServiceFailed`. A read timeout is `ServiceTimedOut`, not retried (a
  slow model would only get a longer queue).
* A caller that has a body to prepare takes its slot first (`slot()`) and prepares the body
  once it has it, so that only as many bodies are in memory (and being decoded) as there are
  slots, not one per waiting call of every job.
* An answer is read as a stream and refused once it passes the service's limit; an answer that
  is not the documented JSON is a `ServiceFailed`, never an exception from deep inside pydantic
  or base64.
* What seg says is checked against what it was sent: the echoed page index and image size, and
  every box inside the image.

The messages of these errors may quote the service and go to the log only.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json as json_module
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import ServiceFailed, ServiceRejected, ServiceTimedOut, ServiceUnavailable
from .files import png_size
from .replicas import (
    OUTSIDE_A_JOB,
    Clock,
    Lease,
    ReplicaPool,
    Resolver,
    Ticket,
    system_resolver,
)
from .settings import WorkerSettings

log = logging.getLogger("decimer_api.worker.services")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SERVICES = ("seg", "ocsr", "cls", "chem")

Sleep = Callable[[float], Awaitable[None]]
Gates = dict[str, asyncio.Semaphore]
Pools = dict[str, ReplicaPool]

# No connection was made: the request never reached the service. ConnectTimeout is listed here,
# ahead of the general timeouts, on purpose: the service may simply not be listening yet.
COULD_NOT_CONNECT = (httpx.ConnectError, httpx.ConnectTimeout)
# The connection broke once the request was on its way.
BROKE_AFTER_SENDING = (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError)
ERROR_BODY_BYTES = 2048  # how much of an error answer is kept, for the log
REJECTED_STATUSES = (413, 422)


# --- what the services answer --------------------------------------------------------------


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Segment(_Answer):
    index: int
    bbox: tuple[int, int, int, int]
    png: bytes  # decoded and checked: the service sends it as base64

    @field_validator("png", mode="before")
    @classmethod
    def _decode_png(cls, value: object) -> bytes:
        if not isinstance(value, str):
            raise ValueError("png must be a base64 string")
        try:
            data = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("png is not base64") from error
        if not data.startswith(PNG_SIGNATURE):
            raise ValueError("png is not a PNG")
        return data

    @field_validator("bbox")
    @classmethod
    def _check_box(cls, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = box
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("bbox has no area or is outside the page")
        return box


class SegPage(_Answer):
    page_index: int
    width: int
    height: int
    segments: list[Segment]

    @model_validator(mode="after")
    def _boxes_inside_the_page(self) -> SegPage:
        for segment in self.segments:
            if segment.bbox[2] > self.width or segment.bbox[3] > self.height:
                raise ValueError(
                    f"box {segment.bbox} reaches past the {self.width}x{self.height} page"
                )
        return self


class ClsAnswer(_Answer):
    is_structure: bool
    score: float


class OcsrAnswer(_Answer):
    smiles: str
    mean_confidence: float = Field(description="Clamped to [0, 1] after parsing.")

    @field_validator("mean_confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return 0.0 if math.isnan(value) else min(1.0, max(0.0, value))

    @property
    def confidence(self) -> float:
        return self.mean_confidence


class NameAnswer(_Answer):
    """One structure's answer from chem. `verified` says how the name was checked (`opsin`, ...).
    `reason` says why there is no name (`timeout`, `UNNAMEABLE`, `no_verified_name`, ...); it is
    None for a name. The worker uses `reason` for its warnings and does not store it."""

    name: str | None = Field(default=None, max_length=10_000)
    tier: str | None = Field(default=None, max_length=200)
    verified: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=200)


class NamesAnswer(_Answer):
    results: list[NameAnswer]


# --- the caller --------------------------------------------------------------------------------


def make_gates(settings: WorkerSettings) -> Gates:
    """The semaphores of this process that are not about a service: page rendering."""
    return {"render": asyncio.Semaphore(settings.render_concurrency)}


def make_pools(
    settings: WorkerSettings,
    *,
    resolver: Resolver = system_resolver,
    clock: Clock = time.monotonic,
) -> Pools:
    """One pool of replicas per service, shared by every job of this process (chem only when
    `CHEM_URL` is set)."""
    return {
        name: ReplicaPool(
            name,
            settings.url(name),
            settings.concurrency(name),
            resolver=resolver,
            clock=clock,
            refresh_seconds=settings.replica_refresh_seconds,
            cooldown_seconds=settings.replica_cooldown_seconds,
            small_burst=settings.small_first_burst,
        )
        for name in SERVICES
        if settings.url(name)
    }


class ServiceCaller:
    def __init__(
        self,
        http: httpx.AsyncClient,
        pools: Pools,
        settings: WorkerSettings,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._http = http
        self._pools = pools
        self._settings = settings
        self._sleep = sleep

    def _timeout(self, service: str) -> httpx.Timeout:
        read = self._settings.read_timeout(service)
        return httpx.Timeout(
            connect=self._settings.connect_timeout_seconds, read=read, write=read, pool=read
        )

    async def capacity(self, service: str) -> int:
        """Calls the service takes at once (all its replicas): how many a job may have going."""
        return await self._pools[service].capacity()

    @contextlib.asynccontextmanager
    async def slot(self, service: str, ticket: Ticket | None = None) -> AsyncIterator[Lease]:
        """Wait for a slot on a replica of `service` (in the fair order) and keep it for the
        block. Pass it to `post(lease=...)` for the call: the caller prepares what it sends once
        it has the slot, instead of holding it while it waits."""
        lease = await self._pools[service].acquire(ticket or OUTSIDE_A_JOB)
        try:
            yield lease
        finally:
            lease.release()

    async def aclose(self) -> None:
        for pool in self._pools.values():
            await pool.aclose()

    async def post(
        self,
        service: str,
        path: str,
        *,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        data: dict[str, str] | None = None,
        json: object = None,
        ticket: Ticket | None = None,
        lease: Lease | None = None,
    ) -> Any:
        """POST and return the parsed JSON answer, or raise a `ServiceError`. `lease` is a slot
        taken with `slot()`, used for the first try; the call releases it (releasing twice is
        harmless). A call outside a job (no `ticket`) shares the ticket `OUTSIDE_A_JOB`."""
        pool = self._pools[service]
        ticket = ticket or OUTSIDE_A_JOB
        settings = self._settings
        busy = resent = connect_attempts = switches = 0
        waited = 0.0
        try:
            while True:
                if lease is None:
                    lease = await pool.acquire(ticket)
                try:
                    status, retry_after, body = await self._exchange(
                        service, f"{lease.url}{path}", files=files, data=data, json=json
                    )
                except COULD_NOT_CONNECT as error:
                    lease.release(alive=False)
                    lease = None
                    await pool.refresh(force=True)  # a replica that restarted has a new address
                    if pool.has_healthy() and switches < pool.replica_count:
                        switches += 1  # another replica may be fine: no waiting
                        continue
                    delay = min(
                        settings.connect_backoff_seconds * 2**connect_attempts,
                        settings.connect_backoff_max_seconds,
                    )
                    if waited + delay > settings.connect_retry_budget_seconds:
                        raise ServiceUnavailable(
                            service, f"unreachable ({type(error).__name__})"
                        ) from error
                    connect_attempts += 1
                    waited += delay
                    log.warning(
                        "%s unreachable (%s); retry %d in %.1f s",
                        service,
                        type(error).__name__,
                        connect_attempts,
                        delay,
                    )
                    await self._sleep(delay)
                    continue
                except BROKE_AFTER_SENDING as error:
                    lease.release(alive=False)
                    lease = None
                    if resent >= settings.sent_retries:
                        raise ServiceFailed(
                            service, f"connection lost mid-request ({type(error).__name__})"
                        ) from error
                    resent += 1
                    log.warning(
                        "%s dropped the connection (%s); trying once more",
                        service,
                        type(error).__name__,
                    )
                    await self._sleep(settings.connect_backoff_seconds)
                    continue
                except httpx.TimeoutException as error:
                    raise ServiceTimedOut(service, f"timed out ({type(error).__name__})") from error
                except httpx.HTTPError as error:
                    raise ServiceFailed(service, f"transport error: {error!r}") from error

                lease.alive = True  # it answered, whatever it said
                if status == 200:
                    return self._json(service, body)
                if status == 503:
                    if busy >= settings.busy_retries:
                        raise ServiceUnavailable(service, "busy (503), retries used up")
                    delay = self._retry_after(retry_after)
                    busy += 1
                    log.info("%s is busy (503); retry %d in %.1f s", service, busy, delay)
                    await self._sleep(delay)  # the slot is kept: the callers behind wait too
                    continue
                detail = body[:200].decode(errors="replace").replace("\n", " ")
                if status in REJECTED_STATUSES:
                    raise ServiceRejected(service, f"HTTP {status}: {detail}")
                raise ServiceFailed(service, f"HTTP {status}: {detail}")
        finally:
            if lease is not None:
                lease.release()

    async def _exchange(
        self,
        service: str,
        url: str,
        *,
        files: dict[str, tuple[str, bytes, str]] | None,
        data: dict[str, str] | None,
        json: object,
    ) -> tuple[int, str | None, bytes]:
        """One request. The body of a 200 is read in pieces and refused above the service's
        limit; that of any other answer is only kept as far as the log needs it."""
        limit = self._settings.max_response_bytes(service)
        async with self._http.stream(
            "POST", url, files=files, data=data, json=json, timeout=self._timeout(service)
        ) as response:
            status = response.status_code
            retry_after = response.headers.get("Retry-After")
            if status == 503:
                return status, retry_after, b""
            keep = limit if status == 200 else ERROR_BODY_BYTES
            declared = response.headers.get("Content-Length", "")
            if status == 200 and declared.isdigit() and int(declared) > limit:
                raise ServiceFailed(service, f"answer too large ({declared} bytes, limit {limit})")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > keep:
                    if status == 200:
                        raise ServiceFailed(service, f"answer too large (limit {limit} bytes)")
                    chunks.append(chunk[: keep - (total - len(chunk))])
                    break
                chunks.append(chunk)
            return status, retry_after, b"".join(chunks)

    def _retry_after(self, header: str | None) -> float:
        settings = self._settings
        try:
            value = float(header or "")
        except ValueError:
            return settings.busy_retry_after_default_seconds
        if math.isnan(value):
            return settings.busy_retry_after_default_seconds
        return min(max(value, 0.0), settings.busy_retry_after_max_seconds)

    @staticmethod
    def _json(service: str, body: bytes) -> Any:
        try:
            return json_module.loads(body)
        except ValueError as error:
            raise ServiceFailed(service, "the answer is not JSON") from error


def _detail(response: httpx.Response) -> str:
    """What the service said, shortened, for the log."""
    return response.text[:200].replace("\n", " ")


class Services:
    """The four services, as typed calls. `for_job` binds the calls to one job's `Ticket`, which
    is what the fair dispatcher of `replicas.py` goes by."""

    def __init__(
        self, caller: ServiceCaller, settings: WorkerSettings, ticket: Ticket | None = None
    ) -> None:
        self._caller = caller
        self._settings = settings
        self._ticket = ticket

    def for_job(self, job_id: str, small: bool) -> Services:
        """The same services, for the calls of one job of the given size class."""
        return Services(self._caller, self._settings, Ticket(job_id, small))

    async def capacity(self, service: str) -> int:
        """Calls `service` takes at once over all its replicas (how many a job may have going)."""
        return await self._caller.capacity(service)

    def slot(self, service: str) -> AbstractAsyncContextManager[Lease]:
        """Wait for a slot of `service` for this job (see `ServiceCaller.slot`); pass it to
        `segment` or `recognise` as `lease`."""
        return self._caller.slot(service, self._ticket)

    async def aclose(self) -> None:
        await self._caller.aclose()

    @property
    def chem_enabled(self) -> bool:
        return self._settings.chem_url is not None

    async def segment(self, png: bytes, page_index: int, *, lease: Lease | None = None) -> SegPage:
        """Ask seg about one page image. What seg echoes (the page index and the size of the
        image) must be what was sent, or its boxes mean nothing for this image."""
        sent = png_size(png)
        raw = await self._caller.post(
            "seg",
            "/infer",
            files={"image": ("page.png", png, "image/png")},
            data={"page_index": str(page_index)},
            ticket=self._ticket,
            lease=lease,
        )
        page = _parse("seg", SegPage, raw)
        if page.page_index != page_index:
            raise ServiceFailed("seg", f"echoed page {page.page_index}, expected {page_index}")
        if (page.width, page.height) != sent:
            raise ServiceFailed(
                "seg", f"echoed size {page.width}x{page.height}, expected {sent[0]}x{sent[1]}"
            )
        return page

    async def classify(self, png: bytes) -> ClsAnswer:
        raw = await self._caller.post(
            "cls", "/infer", files={"image": ("crop.png", png, "image/png")}, ticket=self._ticket
        )
        return _parse("cls", ClsAnswer, raw)

    async def recognise(self, png: bytes, *, lease: Lease | None = None) -> OcsrAnswer:
        raw = await self._caller.post(
            "ocsr",
            "/infer",
            files={"image": ("crop.png", png, "image/png")},
            ticket=self._ticket,
            lease=lease,
        )
        return _parse("ocsr", OcsrAnswer, raw)

    async def names(self, smiles: list[str]) -> list[NameAnswer]:
        """One answer per SMILES, in order, from a single `POST /names` (at most 20 per call)."""
        raw = await self._caller.post(
            "chem", "/names", json={"smiles": smiles}, ticket=self._ticket
        )
        answers = _parse("chem", NamesAnswer, raw).results
        if len(answers) != len(smiles):
            raise ServiceFailed("chem", f"{len(answers)} answers for {len(smiles)} structures")
        return answers


def _parse[T: BaseModel](service: str, model: type[T], raw: object) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as error:
        raise ServiceFailed(service, f"unexpected answer: {error.errors()[0]['msg']}") from error


def make_services(
    http: httpx.AsyncClient,
    settings: WorkerSettings,
    *,
    sleep: Sleep = asyncio.sleep,
    resolver: Resolver = system_resolver,
    clock: Clock = time.monotonic,
) -> Services:
    pools = make_pools(settings, resolver=resolver, clock=clock)
    return Services(ServiceCaller(http, pools, settings, sleep=sleep), settings)


def make_http_client(settings: WorkerSettings) -> httpx.AsyncClient:
    """The shared client. `trust_env=False`: no HTTP(S)_PROXY for calls inside our own network."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.connect_timeout_seconds),
        limits=httpx.Limits(
            # No cap on connections: what is in flight is already bounded, exactly, by the replica
            # pools (replicas x `*_CONCURRENCY` per service, a number only DNS knows). A fixed cap
            # below that sum would turn a wait for a connection into a PoolTimeout, which reads as
            # a slow service (`ServiceTimedOut`).
            max_connections=None,
            max_keepalive_connections=32,
            # The services are uvicorn servers, which close an idle connection after 5 s
            # (timeout_keep_alive). With the same 5 s here a connection can be reused at the very
            # moment the server closes it: the request then fails with a reset (ReadError or
            # RemoteProtocolError), which the caller takes for a dead replica and cools it. A
            # shorter expiry makes the client the one that closes first.
            keepalive_expiry=2.0,
        ),
        trust_env=False,
        follow_redirects=False,
    )
