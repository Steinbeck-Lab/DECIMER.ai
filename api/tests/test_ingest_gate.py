"""The ingest gate: a few ingests at once overall, and one at a time per client."""

from __future__ import annotations

import asyncio

import pytest

from decimer_api.ingest_gate import IngestBusy, IngestGate


async def test_one_client_at_a_time_but_other_clients_run_alongside() -> None:
    gate = IngestGate(concurrency=2)
    release_a = asyncio.Event()
    log: list[str] = []

    async def hold(client: str, label: str) -> None:
        async with gate.slot(client, wait=5):
            log.append(f"{label} in")
            if client == "a":
                await release_a.wait()
            log.append(f"{label} out")

    first = asyncio.create_task(hold("a", "a1"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(hold("a", "a2"))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(hold("b", "b1"), timeout=2)  # b is not held up by a's second upload
    assert log == ["a1 in", "b1 in", "b1 out"]
    release_a.set()
    await asyncio.gather(first, second)
    assert log == ["a1 in", "b1 in", "b1 out", "a1 out", "a2 in", "a2 out"]


async def test_the_overall_limit_still_applies() -> None:
    gate = IngestGate(concurrency=2)
    running = 0
    peak = 0

    async def work(client: str) -> None:
        nonlocal running, peak
        async with gate.slot(client, wait=5):
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1

    await asyncio.gather(*(work(f"c{n}") for n in range(6)))
    assert peak == 2


async def test_waiting_too_long_raises_and_leaves_nothing_behind() -> None:
    gate = IngestGate(concurrency=2)
    holder = asyncio.Event()

    async def hold() -> None:
        async with gate.slot("a", wait=5):
            await holder.wait()

    task = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    with pytest.raises(IngestBusy):
        async with gate.slot("a", wait=0.1):
            pytest.fail("should not get in")
    # the waiting attempt gave back whatever it had taken: a third try after the holder works
    holder.set()
    await task
    async with gate.slot("a", wait=1):
        pass
    assert gate.tracked_clients == 0


async def test_a_waiter_that_is_cancelled_leaves_nothing_behind() -> None:
    gate = IngestGate(concurrency=2)
    holder = asyncio.Event()

    async def hold() -> None:
        async with gate.slot("a", wait=5):
            await holder.wait()

    async def wait_for_turn() -> None:
        async with gate.slot("a", wait=30):
            pass

    first = asyncio.create_task(hold())
    await asyncio.sleep(0.05)
    waiter = asyncio.create_task(wait_for_turn())
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    holder.set()
    await first
    assert gate.tracked_clients == 0  # no entry per client survives its last upload


async def test_the_number_of_tracked_clients_does_not_grow() -> None:
    gate = IngestGate(concurrency=2)
    for n in range(200):
        async with gate.slot(f"client-{n}", wait=1):
            pass
    assert gate.tracked_clients == 0


async def test_a_timeout_inside_the_body_is_not_mistaken_for_a_busy_gate() -> None:
    gate = IngestGate(concurrency=2)
    with pytest.raises(TimeoutError) as caught:
        async with gate.slot("a", wait=1):
            raise TimeoutError("from the work itself")
    assert not isinstance(caught.value, IngestBusy)
    assert gate.tracked_clients == 0
