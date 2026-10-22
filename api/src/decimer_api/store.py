"""Job records, per-IP slots and the waiting queue, all in Redis.

Keys (all under the `decimer:` prefix):

* `job:<id>`            the JSON record, with a TTL. The only source of truth about a job.
* `reservation:<token>` a marker, with a short TTL, for an upload that has started.
* `active:<iphash>`     set of one client's queued or running job ids, and of its reservations
                        (`r-<token>`). Its size is the client's slot count.
* `queue`               sorted set of the ids waiting for a worker, scored by arrival number.
* `queue:seq`           the counter that hands out those numbers.

A slot or a queue entry can never outlive its job. Every read of `active:` checks the job
records (and reservation markers) and drops members that are gone, finished or unreadable;
`queue` entries whose record is gone are dropped the same way. So a crashed worker, or an upload
that died, cannot lock a client out for longer than the matching TTL, and nothing needs a
decrement to be exact.

Lifecycle of a client's slot: `reserve()` when the request arrives (before its body is read),
`admit(job, reservation)` swaps the reservation for the job, `finish(job)` frees it; `release()`
frees a reservation whose upload failed.

Worker contract: `mark_running(id)` when a job starts, `save(job)` after each stage
(each save of a running job pushes its expiry out by `running_job_ttl_seconds`), and
`finish(job)` exactly once with a terminal status, in a `finally`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import WatchError

from .models import Job, JobStatus
from .settings import Settings

log = logging.getLogger("decimer_api.store")

PREFIX = "decimer:"
RESERVATION_MEMBER = "r-"  # job ids are 32 hex characters, so no job id starts like this
ADMIT_ATTEMPTS = 25
PRUNE_INTERVAL_SECONDS = 1.0

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class Admission(StrEnum):
    OK = "ok"
    RATE_LIMITED = "rate_limited"
    QUEUE_FULL = "queue_full"


class Contended(Exception):  # noqa: N818  (not an "Error": the caller should retry later)
    """Redis was too busy to reserve a slot after several attempts."""


class CorruptRecord(Exception):  # noqa: N818
    """A job record exists but cannot be read."""


@dataclass(frozen=True)
class Reservation:
    """A client's slot, held while its upload is read and checked."""

    ip_hash: str
    token: str

    @property
    def member(self) -> str:
        return f"{RESERVATION_MEMBER}{self.token}"


class JobStore:
    def __init__(self, redis: Redis, settings: Settings, *, clock: Clock = utcnow) -> None:
        if not redis.get_connection_kwargs().get("decode_responses"):  # type: ignore[no-untyped-call]
            raise ValueError("JobStore needs a Redis client created with decode_responses=True")
        self.redis = redis
        self.settings = settings
        self._clock = clock
        self._pruned_at: datetime | None = None

    # --- keys ---

    @staticmethod
    def job_key(job_id: str) -> str:
        return f"{PREFIX}job:{job_id}"

    @staticmethod
    def reservation_key(reservation: Reservation) -> str:
        return f"{PREFIX}reservation:{reservation.token}"

    @staticmethod
    def active_key(ip_hash: str) -> str:
        return f"{PREFIX}active:{ip_hash}"

    queue_key = f"{PREFIX}queue"
    queue_seq_key = f"{PREFIX}queue:seq"

    def now(self) -> datetime:
        return self._clock()

    # --- records ---

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    def _ttl(self, status: JobStatus) -> int:
        if status.terminal:
            return self.settings.job_ttl_seconds
        if status is JobStatus.RUNNING:
            return self.settings.running_job_ttl_seconds
        return self.settings.queued_job_ttl_seconds

    async def save(self, job: Job) -> None:
        """Write the record. Its TTL, and `expires_at`, follow the status."""
        ttl = self._ttl(job.status)
        job.expires_at = self._clock() + timedelta(seconds=ttl)
        await self.redis.set(self.job_key(job.id), job.model_dump_json(by_alias=True), ex=ttl)

    async def read(self, job_id: str) -> Job | None:
        """The record, expired or not. None when absent; `CorruptRecord` when unreadable."""
        raw = await self.redis.get(self.job_key(job_id))
        if raw is None:
            return None
        try:
            return Job.model_validate_json(raw)
        except ValidationError as error:
            raise CorruptRecord(job_id) from error

    async def get(self, job_id: str, *, include_expired: bool = False) -> Job | None:
        """The live record, or None (absent, expired or unreadable)."""
        try:
            job = await self.read(job_id)
        except CorruptRecord:
            log.warning("unreadable record for job %s", job_id)
            return None
        if job is None or (not include_expired and job.expires_at <= self._clock()):
            return None
        return job

    async def is_live(self, job_id: str) -> bool:
        """Whether a record exists. Redis's TTL is the authority; nothing is parsed."""
        return bool(await self.redis.exists(self.job_key(job_id)))

    async def delete_record(self, job_id: str) -> None:
        await self.redis.delete(self.job_key(job_id))

    # --- worker seams ---

    async def mark_running(self, job_id: str) -> Job | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = JobStatus.RUNNING
        await self.save(job)
        await self.redis.zrem(self.queue_key, job_id)
        return job

    async def finish(self, job: Job) -> None:
        """Store a terminal job (TTL: `job_ttl_seconds`) and free its slot."""
        if not job.status.terminal:
            raise ValueError("finish() needs a done or failed job")
        await self.save(job)
        await self._release_job(job)

    async def discard(self, job: Job) -> None:
        """Remove every trace of a job (a rejected or aborted admission)."""
        await self._release_job(job)
        await self.delete_record(job.id)

    async def _release_job(self, job: Job) -> None:
        await self.redis.srem(self.active_key(job.client_ip_hash), job.id)  # type: ignore[misc]
        await self.redis.zrem(self.queue_key, job.id)

    # --- slots ---

    async def _live_active_members(self, ip_hash: str) -> list[str]:
        """A client's slot holders; members without a live, unfinished record are removed."""
        key = self.active_key(ip_hash)
        members: set[str] = await self.redis.smembers(key)  # type: ignore[misc]
        if not members:
            return []
        ordered = sorted(members)
        keys = [
            f"{PREFIX}reservation:{member.removeprefix(RESERVATION_MEMBER)}"
            if member.startswith(RESERVATION_MEMBER)
            else self.job_key(member)
            for member in ordered
        ]
        values = await self.redis.mget(keys)
        live: list[str] = []
        stale: list[str] = []
        for member, value in zip(ordered, values, strict=True):
            if value is None or (
                not member.startswith(RESERVATION_MEMBER) and _is_finished_or_unreadable(value)
            ):
                stale.append(member)
            else:
                live.append(member)
        if stale:
            await self.redis.srem(key, *stale)  # type: ignore[misc]
        return live

    async def active_count(self, ip_hash: str) -> int:
        return len(await self._live_active_members(ip_hash))

    async def reserve(self, ip_hash: str) -> Reservation | Admission:
        """Take one of the client's slots for an upload that is about to be read.

        Done before the body is read, so a client over its limit costs nothing but this call.
        Atomic (WATCH/MULTI): parallel requests from one client can never hold more slots than
        the limit. Returns the refusal when there is no slot or the queue is full.
        """
        reservation = Reservation(ip_hash=ip_hash, token=secrets.token_hex(16))
        active = self.active_key(ip_hash)
        for _ in range(ADMIT_ATTEMPTS):
            # The client's own count first: a client over its limit is refused here, without the
            # queue being looked at at all.
            if (
                len(await self._live_active_members(ip_hash))
                >= self.settings.max_active_jobs_per_ip
            ):
                return Admission.RATE_LIMITED
            waiting = await self.queue_length()
            async with self.redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(active)
                    in_use = int(await pipe.scard(active))  # type: ignore[misc]
                    if in_use >= self.settings.max_active_jobs_per_ip:
                        return Admission.RATE_LIMITED
                    if waiting >= self.settings.queue_max:
                        return Admission.QUEUE_FULL
                    pipe.multi()  # type: ignore[no-untyped-call]
                    pipe.sadd(active, reservation.member)
                    pipe.expire(active, self.settings.queued_job_ttl_seconds)
                    pipe.set(
                        self.reservation_key(reservation),
                        "1",
                        ex=self.settings.reservation_ttl_seconds,
                    )
                    await pipe.execute()
                    return reservation
                except WatchError:
                    await asyncio.sleep(0)
        raise Contended("could not reserve a slot")

    async def release(self, reservation: Reservation) -> None:
        """Give a reservation back (idempotent)."""
        await self.redis.srem(self.active_key(reservation.ip_hash), reservation.member)  # type: ignore[misc]
        await self.redis.delete(self.reservation_key(reservation))

    # --- the queue ---

    async def _prune_queue(self) -> None:
        """Drop queue entries whose record has expired (the worker died or never came).

        Done at most about once a second per process: every upload would otherwise read the
        whole queue and check every record. A stale entry can inflate the queue length for a
        second, which only matters at the very edge of the limit.
        """
        now = self._clock()
        if (
            self._pruned_at is not None
            and (now - self._pruned_at).total_seconds() < PRUNE_INTERVAL_SECONDS
        ):
            return
        self._pruned_at = now
        members: list[str] = await self.redis.zrange(self.queue_key, 0, -1)
        if not members:
            return
        async with self.redis.pipeline(transaction=False) as pipe:
            for member in members:
                pipe.exists(self.job_key(member))
            alive = await pipe.execute()
        gone = [member for member, exists in zip(members, alive, strict=True) if not exists]
        if gone:
            await self.redis.zrem(self.queue_key, *gone)

    async def queue_length(self) -> int:
        await self._prune_queue()
        return int(await self.redis.zcard(self.queue_key))

    async def queue_position(self, job_id: str) -> int | None:
        rank = await self.redis.zrank(self.queue_key, job_id)
        return None if rank is None else int(rank) + 1

    async def admit(self, job: Job, reservation: Reservation | None = None) -> Admission:
        """Save a new queued job and give it the client's slot and a queue place, atomically.

        The slot is the client's reservation, swapped for the job; without one (or if it has
        expired) a free slot is needed. The check and the swap run in one WATCH/MULTI
        transaction. A rejected job leaves nothing behind (the reservation, if any, is left for
        the caller to `release`).
        """
        await self.save(job)  # first: a record must exist before the slot, or pruning drops it
        active = self.active_key(job.client_ip_hash)
        arrival = int(await self.redis.incr(self.queue_seq_key))
        try:
            for _ in range(ADMIT_ATTEMPTS):
                await self._live_active_members(job.client_ip_hash)
                await self._prune_queue()
                async with self.redis.pipeline(transaction=True) as pipe:
                    try:
                        await pipe.watch(active, self.queue_key)
                        in_use = int(await pipe.scard(active))  # type: ignore[misc]
                        holds_reservation = bool(
                            reservation is not None
                            and await pipe.sismember(active, reservation.member)  # type: ignore[misc]
                        )
                        if in_use - holds_reservation >= self.settings.max_active_jobs_per_ip:
                            await self.delete_record(job.id)
                            return Admission.RATE_LIMITED
                        if int(await pipe.zcard(self.queue_key)) >= self.settings.queue_max:
                            await self.delete_record(job.id)
                            return Admission.QUEUE_FULL
                        pipe.multi()  # type: ignore[no-untyped-call]
                        if reservation is not None:
                            pipe.srem(active, reservation.member)
                            pipe.delete(self.reservation_key(reservation))
                        pipe.sadd(active, job.id)
                        pipe.expire(active, self.settings.queued_job_ttl_seconds)
                        pipe.zadd(self.queue_key, {job.id: arrival})
                        await pipe.execute()
                        return Admission.OK
                    except WatchError:
                        await asyncio.sleep(0)
            raise Contended("could not reserve a slot: Redis is too contended")
        except BaseException:
            await self.discard(job)
            raise


def _is_finished_or_unreadable(raw: str) -> bool:
    """Whether a job record no longer holds a slot. Reads the status only."""
    try:
        status = json.loads(raw)["status"]
    except (ValueError, KeyError, TypeError):
        return True
    return status in (JobStatus.DONE, JobStatus.FAILED)
