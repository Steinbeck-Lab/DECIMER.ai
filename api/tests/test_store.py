from __future__ import annotations

import asyncio
from datetime import timedelta

import fakeredis
import pytest

from decimer_api import store as store_module
from decimer_api.models import Job, JobStatus, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import Admission, Contended, CorruptRecord, JobStore, Reservation
from tests.conftest import FakeClock


def make_job(clock: FakeClock, ip_hash: str = "ip-a") -> Job:
    return Job.create(
        job_id=new_job_id(), client_ip_hash=ip_hash, pages=1, pages_processed=1, now=clock()
    )


async def verdict(store: JobStore, ip_hash: str) -> Admission:
    """What reserve() would answer right now, without keeping the slot."""
    result = await store.reserve(ip_hash)
    if isinstance(result, Reservation):
        await store.release(result)
        return Admission.OK
    return result


async def test_ping(store: JobStore) -> None:
    assert await store.ping() is True


async def test_the_store_needs_decoded_responses(settings: Settings) -> None:
    raw = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer())
    with pytest.raises(ValueError, match="decode_responses"):
        JobStore(raw, settings)


# --- records and their TTL ------------------------------------------------------------------


async def test_save_and_get_round_trip(store: JobStore, clock: FakeClock) -> None:
    job = make_job(clock)
    await store.save(job)
    assert await store.get(job.id) == job


async def test_derived_fields_are_not_stored(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.save(job)
    raw = await redis.get(store.job_key(job.id))
    assert raw is not None
    assert "queuePosition" not in raw and "cropUrl" not in raw and '"number"' not in raw


async def test_each_status_has_its_own_ttl(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.save(job)
    queued_ttl = await redis.ttl(store.job_key(job.id))
    assert settings.running_job_ttl_seconds < queued_ttl <= settings.queued_job_ttl_seconds
    assert job.expires_at == clock() + timedelta(seconds=settings.queued_job_ttl_seconds)

    job.status = JobStatus.RUNNING
    await store.save(job)
    running_ttl = await redis.ttl(store.job_key(job.id))
    assert 0 < running_ttl <= settings.running_job_ttl_seconds
    assert job.expires_at == clock() + timedelta(seconds=settings.running_job_ttl_seconds)

    job.status = JobStatus.DONE
    await store.save(job)
    done_ttl = await redis.ttl(store.job_key(job.id))
    assert 0 < done_ttl <= settings.job_ttl_seconds
    assert job.expires_at == clock() + timedelta(seconds=settings.job_ttl_seconds)


async def test_every_save_of_a_running_job_pushes_the_expiry_out(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.admit(job)
    running = await store.mark_running(job.id)
    assert running is not None
    first_expiry = running.expires_at
    clock.advance(600)
    await store.save(running)
    assert running.expires_at == first_expiry + timedelta(seconds=600)
    # Redis was told the same: close to the full running TTL again, not 600 s less.
    assert await redis.ttl(store.job_key(job.id)) > settings.running_job_ttl_seconds - 5


async def test_a_dead_worker_loses_its_record_after_the_running_ttl_not_the_queued_one(
    store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.admit(job)
    await store.mark_running(job.id)
    clock.advance(settings.running_job_ttl_seconds + 1)
    assert await store.get(job.id) is None  # far earlier than queued_job_ttl_seconds


async def test_unknown_and_expired_jobs_are_not_found(store: JobStore, clock: FakeClock) -> None:
    assert await store.get(new_job_id()) is None
    job = make_job(clock)
    await store.save(job)
    clock.advance(10 * 24 * 3600)
    assert await store.get(job.id) is None
    # The sweep can still see the expired record.
    assert (await store.get(job.id, include_expired=True)) is not None


async def test_a_corrupt_record_is_not_found_for_the_api_and_reported_to_the_sweep(
    store: JobStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    job_id = new_job_id()
    await redis.set(store.job_key(job_id), "{not json", ex=60)
    assert await store.get(job_id) is None
    with pytest.raises(CorruptRecord):
        await store.read(job_id)
    await redis.set(store.job_key(job_id), '{"id": 1}', ex=60)  # parses, wrong shape
    assert await store.get(job_id) is None
    with pytest.raises(CorruptRecord):
        await store.read(job_id)


async def test_is_live_only_asks_redis(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    job = make_job(clock)
    assert await store.is_live(job.id) is False
    await redis.set(store.job_key(job.id), "not even json", ex=60)
    assert await store.is_live(job.id) is True  # no parsing
    await redis.delete(store.job_key(job.id))
    assert await store.is_live(job.id) is False


# --- finishing and slots --------------------------------------------------------------------


async def test_finish_applies_the_one_hour_ttl_and_frees_the_slot(
    store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = make_job(clock)
    assert await store.admit(job) is Admission.OK
    job.status = JobStatus.DONE
    await store.finish(job)
    stored = await store.get(job.id)
    assert stored is not None and stored.status is JobStatus.DONE
    assert stored.expires_at == clock() + timedelta(seconds=settings.job_ttl_seconds)
    assert await store.active_count("ip-a") == 0
    assert await store.queue_length() == 0


async def test_finish_needs_a_terminal_status(store: JobStore, clock: FakeClock) -> None:
    with pytest.raises(ValueError, match="done or failed"):
        await store.finish(make_job(clock))


async def test_admission_limits_active_jobs_per_ip(store: JobStore, clock: FakeClock) -> None:
    first, second, third = (make_job(clock) for _ in range(3))
    assert await store.admit(first) is Admission.OK
    assert await store.admit(second) is Admission.OK
    assert await verdict(store, "ip-a") is Admission.RATE_LIMITED
    assert await store.admit(third) is Admission.RATE_LIMITED
    # The rejected job leaves no record and no slot behind.
    assert await store.get(third.id) is None
    assert await store.active_count("ip-a") == 2
    assert await store.queue_length() == 2
    # Another address is unaffected.
    assert await verdict(store, "ip-b") is Admission.OK
    assert await store.admit(make_job(clock, "ip-b")) is Admission.OK


async def test_a_running_job_still_counts_and_a_finished_one_does_not(
    store: JobStore, clock: FakeClock
) -> None:
    first, second = make_job(clock), make_job(clock)
    await store.admit(first)
    await store.admit(second)
    running = await store.mark_running(first.id)
    assert running is not None and running.status is JobStatus.RUNNING
    assert await store.active_count("ip-a") == 2  # running is active
    assert await store.queue_length() == 1  # but no longer waiting
    first.status = JobStatus.FAILED
    await store.finish(first)
    assert await store.active_count("ip-a") == 1
    assert await store.admit(make_job(clock)) is Admission.OK


async def test_slots_self_heal_when_records_expire_without_a_finish(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    jobs = [make_job(clock), make_job(clock)]
    for job in jobs:
        await store.admit(job)
    assert await verdict(store, "ip-a") is Admission.RATE_LIMITED
    # The worker died: nobody called finish(); Redis expires the records.
    for job in jobs:
        await redis.delete(store.job_key(job.id))
    assert await store.active_count("ip-a") == 0
    assert await verdict(store, "ip-a") is Admission.OK
    assert await store.admit(make_job(clock)) is Admission.OK


async def test_a_terminal_record_does_not_hold_a_slot_even_if_the_srem_was_lost(
    store: JobStore, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.admit(job)
    job.status = JobStatus.DONE
    await store.save(job)  # saved as done, but finish() never ran
    assert await store.active_count("ip-a") == 0


async def test_a_garbage_record_does_not_break_slot_counting(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.admit(job)
    await redis.set(store.job_key(job.id), "garbage", ex=60)
    assert await store.active_count("ip-a") == 0  # counted as gone, not an error
    assert await store.admit(make_job(clock)) is Admission.OK


async def test_counters_do_not_leak_redis_keys(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock
) -> None:
    jobs = [make_job(clock, f"ip-{n}") for n in range(5)]
    for job in jobs:
        await store.admit(job)
    active_keys = await redis.keys("decimer:active:*")
    assert len(active_keys) == 5
    for key in active_keys:
        assert 0 < await redis.ttl(key) <= settings.queued_job_ttl_seconds
    for job in jobs:
        job.status = JobStatus.DONE
        await store.finish(job)
    assert await redis.keys("decimer:active:*") == []
    assert await redis.zcard("decimer:queue") == 0


# --- reservations: the slot is taken before the upload is read -------------------------------


async def test_a_reservation_takes_a_slot_and_release_gives_it_back(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, settings: Settings
) -> None:
    first = await store.reserve("ip-a")
    second = await store.reserve("ip-a")
    assert isinstance(first, Reservation) and isinstance(second, Reservation)
    assert await store.active_count("ip-a") == 2
    assert await store.reserve("ip-a") is Admission.RATE_LIMITED
    assert 0 < await redis.ttl(store.reservation_key(first)) <= settings.reservation_ttl_seconds
    await store.release(first)
    await store.release(first)  # idempotent
    assert await store.active_count("ip-a") == 1
    assert isinstance(await store.reserve("ip-a"), Reservation)


async def test_reservations_and_jobs_share_the_limit(store: JobStore, clock: FakeClock) -> None:
    assert await store.admit(make_job(clock)) is Admission.OK
    held = await store.reserve("ip-a")
    assert isinstance(held, Reservation)
    assert await store.reserve("ip-a") is Admission.RATE_LIMITED


async def test_admit_swaps_the_reservation_for_the_job_without_a_second_slot(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    kept = make_job(clock)
    await store.admit(kept)
    held = await store.reserve("ip-a")  # now at the limit: 1 job + 1 reservation
    assert isinstance(held, Reservation)
    job = make_job(clock)
    assert await store.admit(job, held) is Admission.OK
    assert await store.active_count("ip-a") == 2
    assert await redis.exists(store.reservation_key(held)) == 0
    assert await store.queue_position(job.id) == 2


async def test_a_job_admitted_without_its_reservation_is_still_limited(
    store: JobStore, clock: FakeClock
) -> None:
    """The reservation expired mid-upload: admit() must still enforce the limit."""
    held = await store.reserve("ip-a")
    assert isinstance(held, Reservation)
    await store.release(held)  # expired in the meantime (the request is still running)
    assert await store.admit(make_job(clock)) is Admission.OK
    assert await store.admit(make_job(clock)) is Admission.OK
    assert await store.admit(make_job(clock), held) is Admission.RATE_LIMITED


async def test_a_lost_reservation_frees_its_slot_by_itself(
    store: JobStore, redis: fakeredis.FakeAsyncRedis
) -> None:
    held = [await store.reserve("ip-a"), await store.reserve("ip-a")]
    assert await store.reserve("ip-a") is Admission.RATE_LIMITED
    for reservation in held:
        assert isinstance(reservation, Reservation)
        await redis.delete(store.reservation_key(reservation))  # the request died, TTL passed
    assert await store.active_count("ip-a") == 0
    assert isinstance(await store.reserve("ip-a"), Reservation)


async def test_a_full_queue_refuses_a_reservation(
    store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    small = JobStore(store.redis, settings.model_copy(update={"queue_max": 1}), clock=clock)
    await small.admit(make_job(clock, "ip-x"))
    assert await small.reserve("ip-y") is Admission.QUEUE_FULL
    assert await small.active_count("ip-y") == 0


async def test_concurrent_reservations_never_exceed_the_limit(store: JobStore) -> None:
    results = await asyncio.gather(*(store.reserve("ip-a") for _ in range(8)))
    granted = [r for r in results if isinstance(r, Reservation)]
    assert len(granted) == 2
    assert results.count(Admission.RATE_LIMITED) == 6
    assert await store.active_count("ip-a") == 2


# --- the queue ------------------------------------------------------------------------------


async def test_global_queue_limit(store: JobStore, settings: Settings, clock: FakeClock) -> None:
    small = JobStore(store.redis, settings.model_copy(update={"queue_max": 3}), clock=clock)
    for n in range(3):
        assert await small.admit(make_job(clock, f"ip-{n}")) is Admission.OK
    assert await verdict(small, "ip-new") is Admission.QUEUE_FULL
    rejected = make_job(clock, "ip-new")
    assert await small.admit(rejected) is Admission.QUEUE_FULL
    assert await small.get(rejected.id) is None
    assert await small.active_count("ip-new") == 0
    assert await small.queue_length() == 3


async def test_queue_positions_follow_arrival_and_shift_when_work_starts(
    store: JobStore, clock: FakeClock
) -> None:
    jobs = []
    for n in range(3):
        job = make_job(clock, f"ip-{n}")
        assert await store.admit(job) is Admission.OK
        jobs.append(job)
    assert [await store.queue_position(j.id) for j in jobs] == [1, 2, 3]
    await store.mark_running(jobs[0].id)
    assert await store.queue_position(jobs[0].id) is None
    assert [await store.queue_position(j.id) for j in jobs[1:]] == [1, 2]


async def test_stale_queue_entries_expire_with_their_records(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock
) -> None:
    small = JobStore(redis, settings.model_copy(update={"queue_max": 1}), clock=clock)
    ghost = make_job(clock, "ip-ghost")
    await small.admit(ghost)
    assert await verdict(small, "ip-x") is Admission.QUEUE_FULL
    await redis.delete(small.job_key(ghost.id))  # record expired, queue entry orphaned
    clock.advance(2)  # the queue is only swept about once a second
    assert await verdict(small, "ip-x") is Admission.OK
    assert await small.admit(make_job(clock, "ip-x")) is Admission.OK
    assert await small.queue_length() == 1


async def test_concurrent_admissions_never_exceed_the_limit(
    store: JobStore, clock: FakeClock
) -> None:
    jobs = [make_job(clock) for _ in range(8)]
    results = await asyncio.gather(*(store.admit(job) for job in jobs))
    assert results.count(Admission.OK) == 2
    assert results.count(Admission.RATE_LIMITED) == 6
    assert await store.active_count("ip-a") == 2


async def test_running_out_of_retries_is_reported_not_swallowed(
    store: JobStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_module, "ADMIT_ATTEMPTS", 0)
    job = make_job(clock)
    with pytest.raises(Contended):
        await store.admit(job)
    assert await store.get(job.id) is None  # and it left nothing behind


async def test_discard_removes_every_trace(
    store: JobStore, redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    job = make_job(clock)
    await store.admit(job)
    await store.discard(job)
    assert await store.get(job.id) is None
    assert await store.active_count("ip-a") == 0
    assert await store.queue_length() == 0
    # Only the arrival counter remains: one integer key, not one per job or client.
    assert await redis.keys("decimer:*") == ["decimer:queue:seq"]


# --- the cost of reserve() ------------------------------------------------------------------


async def test_a_client_over_its_limit_is_refused_without_looking_at_the_queue(
    store: JobStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    await store.admit(make_job(clock))
    await store.admit(make_job(clock))

    async def forbidden() -> int:
        raise AssertionError("the queue was inspected for a client that is over its limit")

    monkeypatch.setattr(store, "queue_length", forbidden)
    assert await store.reserve("ip-a") is Admission.RATE_LIMITED


async def test_the_queue_is_swept_at_most_about_once_a_second(
    store: JobStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sweeps = 0
    real = store.redis.zrange

    async def counting(*args: object, **kwargs: object) -> object:
        nonlocal sweeps
        sweeps += 1
        return await real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store.redis, "zrange", counting)
    await store.admit(make_job(clock, "ip-q"))  # swept now, and something is in the queue
    sweeps = 0
    for n in range(10):  # ten reservations from ten clients within the same second
        held = await store.reserve(f"ip-{n}")
        assert isinstance(held, Reservation)
    assert sweeps == 0
    clock.advance(1.1)
    await store.reserve("ip-new")
    assert sweeps == 1  # and once more when the second is over


async def test_a_full_queue_is_still_noticed_between_sweeps(
    store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    small = JobStore(store.redis, settings.model_copy(update={"queue_max": 2}), clock=clock)
    for n in range(2):
        await small.admit(make_job(clock, f"ip-{n}"))
        assert await small.queue_length() == n + 1  # the count itself is never throttled
    assert await small.reserve("ip-x") is Admission.QUEUE_FULL
