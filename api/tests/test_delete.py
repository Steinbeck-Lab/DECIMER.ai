"""DELETE /api/jobs/{id}: stop a job, free the client's slot, remove its files."""

from __future__ import annotations

import httpx
import pytest

from decimer_api.models import JobStatus, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import PREFIX, JobStore
from decimer_api.worker import cancel as worker_cancel
from tests.conftest import FakeClock, FakeEnqueue
from tests.factories import make_png, multipart
from tests.results import make_done_job


async def create(client: httpx.AsyncClient) -> str:
    body, headers = multipart([("a.png", make_png())])
    response = await client.post("/api/jobs", content=body, headers=headers)
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def test_delete_removes_a_queued_job_its_files_and_its_queue_place(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    first = await create(client)
    second = await create(client)
    assert await store.queue_position(second) == 2
    response = await client.delete(f"/api/jobs/{first}")
    assert response.status_code == 204 and response.content == b""
    assert (await client.get(f"/api/jobs/{first}")).status_code == 404
    assert not (settings.data_dir / first).exists()
    assert await store.queue_position(first) is None
    assert await store.queue_position(second) == 1  # the one behind moved up
    assert (await client.get(f"/api/jobs/{second}")).status_code == 200  # others are untouched
    assert (settings.data_dir / second).is_dir()


async def test_delete_frees_the_clients_slot(
    client: httpx.AsyncClient, enqueue: FakeEnqueue
) -> None:
    """MAX_ACTIVE_JOBS_PER_IP is 2 in the tests: the third upload is 429 until one is deleted."""
    first = await create(client)
    await create(client)
    body, headers = multipart([("a.png", make_png())])
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429
    assert (await client.delete(f"/api/jobs/{first}")).status_code == 204
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 202


async def test_delete_does_not_free_a_slot_of_another_client(
    make_client: object, store: JobStore
) -> None:
    mine = make_client("203.0.113.1")  # type: ignore[operator]
    theirs = make_client("203.0.113.2")  # type: ignore[operator]
    await create(mine)
    await create(mine)
    other_job = await create(theirs)
    assert (await theirs.delete(f"/api/jobs/{other_job}")).status_code == 204
    body, headers = multipart([("a.png", make_png())])
    assert (await mine.post("/api/jobs", content=body, headers=headers)).status_code == 429


async def test_a_deleted_job_is_gone_for_every_endpoint(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = await make_done_job(store, settings, clock)
    assert (await client.delete(f"/api/jobs/{job.id}")).status_code == 204
    assert (await client.get(f"/api/jobs/{job.id}")).status_code == 404
    assert (await client.get(f"/api/jobs/{job.id}/media/crop-0.png")).status_code == 404
    assert (
        await client.post(f"/api/jobs/{job.id}/export", json={"format": "csv"})
    ).status_code == 404
    assert (
        await client.post(f"/api/jobs/{job.id}/results/0/validate", json={"molfile": "x"})
    ).status_code == 404
    assert (await client.post(f"/api/jobs/{job.id}/report", json={"index": 0})).status_code == 404
    assert not (settings.data_dir / job.id).exists()


async def test_delete_is_404_for_a_job_that_is_not_there_and_the_second_time(
    client: httpx.AsyncClient,
) -> None:
    job_id = await create(client)
    assert (await client.delete(f"/api/jobs/{job_id}")).status_code == 204
    again = await client.delete(f"/api/jobs/{job_id}")
    assert again.status_code == 404 and again.json()["detail"]["code"] == "not_found"
    assert (await client.delete(f"/api/jobs/{new_job_id()}")).status_code == 404


@pytest.mark.parametrize("job_id", ["..", "x", "A" * 32, "0" * 31, "%2e%2e", "a" * 32 + "x"])
async def test_a_malformed_id_is_404_and_deletes_nothing(
    client: httpx.AsyncClient, settings: Settings, job_id: str
) -> None:
    kept = await create(client)
    assert (await client.delete(f"/api/jobs/{job_id}")).status_code == 404
    assert (settings.data_dir / kept).is_dir()


async def test_delete_only_removes_the_job_folder_not_its_neighbours_or_the_data_dir(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    first = await create(client)
    second = await create(client)
    await client.delete(f"/api/jobs/{first}")
    assert settings.data_dir.is_dir()
    assert [p.name for p in settings.data_dir.iterdir()] == [second]


async def test_delete_of_a_running_job_removes_it_and_the_worker_cannot_bring_it_back(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    job_id = await create(client)
    running = await store.mark_running(job_id)
    assert running is not None and running.status is JobStatus.RUNNING
    assert (await client.delete(f"/api/jobs/{job_id}")).status_code == 204
    assert await store.is_cancelled(job_id)
    # What the worker does next: save after a stage, and finish at the end. Neither writes.
    await store.save(running)
    assert await store.get(job_id) is None
    running.status = JobStatus.DONE
    await store.finish(running)
    assert await store.get(job_id) is None
    assert not await store.is_live(job_id)
    assert (await client.get(f"/api/jobs/{job_id}")).status_code == 404
    # The slot is not free: the models may still be busy with it (see the next tests).
    assert await store.active_count(running.client_ip_hash) == 1


async def test_the_cancel_marker_outlives_a_worker_run_and_then_goes(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    job_id = await create(client)
    await client.delete(f"/api/jobs/{job_id}")
    ttl = await store.redis.ttl(store.cancel_key(job_id))
    assert 0 < ttl <= settings.running_job_ttl_seconds
    assert ttl > settings.running_job_ttl_seconds - 5


async def test_a_job_that_was_not_deleted_is_written_as_before(
    client: httpx.AsyncClient, store: JobStore
) -> None:
    job_id = await create(client)
    job = await store.mark_running(job_id)
    assert job is not None
    assert not await store.is_cancelled(job_id)
    job.structures_found = 3
    await store.save(job)
    saved = await store.get(job_id)
    assert saved is not None and saved.structures_found == 3


async def test_delete_does_not_touch_the_arq_queue_entry_a_worker_skips_it_by_its_missing_record(
    client: httpx.AsyncClient, enqueue: FakeEnqueue, store: JobStore
) -> None:
    job_id = await create(client)
    assert enqueue.job_ids == [job_id]
    await client.delete(f"/api/jobs/{job_id}")
    assert enqueue.job_ids == [job_id]  # nothing is un-enqueued; the worker finds no record
    assert await store.get(job_id) is None


# --- the slot of a running job is kept for its whole time, whatever the worker does (review) ---


async def test_a_deleted_running_job_keeps_the_slot_so_upload_delete_upload_cannot_pile_up_work(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    """MAX_ACTIVE_JOBS_PER_IP is 2. Upload, let it run, delete, upload again: the models are still
    busy with every deleted job (the calls the worker abandoned go on computing in the services),
    so the third upload waits for the slot's whole time, not until the worker has stopped."""
    first = await create(client)
    second = await create(client)
    for job_id in (first, second):
        assert await store.mark_running(job_id) is not None
    body, headers = multipart([("a.png", make_png())])
    assert (await client.delete(f"/api/jobs/{first}")).status_code == 204
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429
    assert (await client.delete(f"/api/jobs/{second}")).status_code == 204
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429
    # The worker stops both runs and cleans up: the slots are still held.
    for job_id in (first, second):
        await worker_cancel.remove(job_id, store, settings.data_dir / job_id)
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429
    # One slot's time is over (what the TTL does): that one comes back, and only that one.
    await store.redis.delete(store.cancelling_key(first))
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 202
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429


async def test_a_loop_of_upload_run_delete_never_has_more_than_the_limit_in_flight(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    accepted = 0
    body, headers = multipart([("a.png", make_png())])
    for _ in range(10):
        response = await client.post("/api/jobs", content=body, headers=headers)
        if response.status_code != 202:
            assert response.status_code == 429
            continue
        accepted += 1
        job_id = response.json()["id"]
        await store.mark_running(job_id)
        await client.delete(f"/api/jobs/{job_id}")
        # The worker stops the run at once and cleans up: the models are still busy, so nothing
        # is given back.
        await worker_cancel.remove(job_id, store, settings.data_dir / job_id)
    assert accepted == settings.max_active_jobs_per_ip
    members = await store.redis.smembers(store.active_key(await _client_hash(store)))  # type: ignore[misc]
    assert len(members) <= settings.max_active_jobs_per_ip


async def test_a_deleted_queued_job_gives_the_slot_back_at_once_nobody_has_it(
    client: httpx.AsyncClient, store: JobStore
) -> None:
    first = await create(client)
    await create(client)
    await client.delete(f"/api/jobs/{first}")
    body, headers = multipart([("a.png", make_png())])
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 202


async def test_the_held_slot_is_given_up_when_its_time_is_over(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    """A worker that died never confirms: the member lives CANCELLING_SLOT_SECONDS and goes."""
    first = await create(client)
    await create(client)
    await store.mark_running(first)
    await client.delete(f"/api/jobs/{first}")
    ttl = await store.redis.ttl(store.cancelling_key(first))
    assert 0 < ttl <= settings.cancelling_slot_seconds
    assert ttl > settings.cancelling_slot_seconds - 5
    body, headers = multipart([("a.png", make_png())])
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 429
    await store.redis.delete(store.cancelling_key(first))  # what the TTL does
    assert (await client.post("/api/jobs", content=body, headers=headers)).status_code == 202


async def test_a_deleted_finished_job_has_no_slot_to_hold(
    client: httpx.AsyncClient, store: JobStore, settings: Settings, clock: FakeClock
) -> None:
    job = await make_done_job(store, settings, clock, ip="done-owner")
    await store.cancel(job)
    assert await store.active_count("done-owner") == 0
    assert not await store.redis.exists(store.cancelling_key(job.id))


async def test_the_slot_is_held_for_its_whole_time_after_the_worker_has_stopped_and_cleaned_up(
    client: httpx.AsyncClient, store: JobStore, settings: Settings
) -> None:
    job_id = await create(client)
    await store.mark_running(job_id)
    await client.delete(f"/api/jobs/{job_id}")
    await worker_cancel.remove(job_id, store, settings.data_dir / job_id)
    ttl = await store.redis.ttl(store.cancelling_key(job_id))
    assert settings.cancelling_slot_seconds - 5 < ttl <= settings.cancelling_slot_seconds
    assert await store.redis.exists(store.cancelling_key(job_id))
    assert not hasattr(store, "release_cancelled")  # nothing gives a slot back early


async def test_a_job_deleted_just_as_a_worker_takes_it_is_not_started(
    client: httpx.AsyncClient, store: JobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker read the record, the owner deleted the job, the worker went on to mark it
    running: `mark_running` says no, so the run never starts on a slot that was freed."""
    job_id = await create(client)
    real_get = store.get

    async def get_then_delete(job_id_: str, **kwargs: bool) -> object:
        job = await real_get(job_id_, **kwargs)
        monkeypatch.setattr(store, "get", real_get)  # once
        assert job is not None
        await store.cancel(job)  # the owner's delete lands here
        return job

    monkeypatch.setattr(store, "get", get_then_delete)
    assert await store.mark_running(job_id) is None


# --- a save cannot bring back a deleted record (review finding 5) ----------------------------


@pytest.mark.parametrize("delete_lands", ["before the first command", "after the first command"])
async def test_a_save_that_races_with_a_delete_cannot_bring_the_record_back(
    client: httpx.AsyncClient,
    store: JobStore,
    monkeypatch: pytest.MonkeyPatch,
    delete_lands: str,
) -> None:
    """A save that looked at the marker and then wrote would, with the delete landing between the
    two, write the record again. The delete is made to land inside the save, at the worst moment."""
    job_id = await create(client)
    job = await store.mark_running(job_id)
    assert job is not None
    real = store.redis.execute_command
    state = {"saving": False, "fired": False}

    async def delete_once() -> None:
        state["fired"] = True
        await store.cancel(job)

    async def interleaved(*args: object, **kwargs: object) -> object:
        if state["saving"] and not state["fired"] and delete_lands == "before the first command":
            await delete_once()
        result = await real(*args, **kwargs)
        if state["saving"] and not state["fired"] and delete_lands == "after the first command":
            await delete_once()
        return result

    monkeypatch.setattr(store.redis, "execute_command", interleaved)
    state["saving"] = True
    job.structures_found = 7
    await store.save(job)
    state["saving"] = False
    assert state["fired"]
    assert await store.get(job_id) is None
    assert not await store.is_live(job_id)


async def _client_hash(store: JobStore) -> str:
    """The hash of the test client's address (the one slot set that the tests above fill)."""
    keys = [key async for key in store.redis.scan_iter(match=f"{PREFIX}active:*")]
    assert len(keys) == 1
    return str(keys[0]).removeprefix(f"{PREFIX}active:")
