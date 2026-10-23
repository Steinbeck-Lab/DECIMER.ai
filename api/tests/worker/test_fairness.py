"""No blocking and no starvation inside a worker process: jobs share the replicas, a small
job does not wait behind large ones, and a large one is not starved by small ones.

The order of events is what is asserted (which request reaches a replica next, which job
finishes first while others are held), never how long something takes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from decimer_api.models import Job, JobStatus
from decimer_api.worker.pipeline import _Run
from decimer_api.worker.replicas import Lease, Ticket
from tests.worker.conftest import Rig, wait_until
from tests.worker.fakes import FakeBackend, crop_id, fleet, png_size


def finished(rig: Rig, job: Job) -> bool:
    return any(done.id == job.id for done in rig.store.finishes)


async def stored_status(rig: Rig, job: Job) -> JobStatus:
    stored = await rig.store.get(job.id, include_expired=True)
    assert stored is not None
    return stored.status


# --- a small job and big ones ------------------------------------------------------------------


async def test_a_pasted_image_finishes_while_ten_page_pdfs_are_still_running(
    make_rig: Callable[..., Rig],
) -> None:
    """Two 10-page PDFs hold both ocsr replicas and queue more crops behind them. A pasted image
    arrives; it is served by the next free slot, ahead of the PDF crops that were already
    waiting, and finishes while both PDFs are still in their recognise stage."""
    permits = asyncio.Semaphore(0)

    async def hold_pdf_crops(parts: dict[str, bytes]) -> None:
        page, index = crop_id(parts["image"])
        if 0 <= page < 10 and 0 <= index < 10:  # a crop of a PDF page, as the fake seg cuts it
            await permits.acquire()

    hosts, resolver = fleet(seg=2, ocsr=2, cls=2)
    # Page 0 has no structures: the pasted image (page 0 of its job) is the whole-image fallback,
    # a crop of its own size, which is not held.
    layout = {page: 2 for page in range(1, 10)} | {0: 0}
    backend = FakeBackend(layout=layout, hosts=hosts, pauses={"ocsr": hold_pdf_crops})
    rig = make_rig(backend=backend, resolver=resolver)
    ocsr = rig.deps.services._caller._pools["ocsr"]

    first = await rig.pdf_job(10, ip="client-1")
    second = await rig.pdf_job(10, ip="client-2")
    pasted = await rig.image_job([(123, 77)], ip="client-3")
    tasks = [asyncio.create_task(rig.run(first)), asyncio.create_task(rig.run(second))]

    # Both ocsr replicas are busy with held PDF crops and more PDF crops are waiting.
    await wait_until(lambda: backend.active["ocsr"] == 2 and ocsr.waiting >= 2, timeout=20)
    waiting_before = ocsr.waiting
    small = asyncio.create_task(rig.run(pasted))
    await wait_until(lambda: ocsr.waiting == waiting_before + 1, timeout=20)  # its crop queued

    permits.release()  # one PDF crop is answered: one slot is free
    await asyncio.wait_for(small, 20)

    assert (await stored_status(rig, pasted)) is JobStatus.DONE
    ocsr_requests = [png_size(parts["image"]) for parts in backend.parts_of("ocsr")]
    assert ocsr_requests[2] == (123, 77)  # the third request to ocsr: the pasted image's
    assert not finished(rig, first) and not finished(rig, second)  # both PDFs are still held
    assert (await stored_status(rig, first)) is JobStatus.RUNNING

    for _ in range(40):  # let the PDFs finish
        permits.release()
    await asyncio.wait_for(asyncio.gather(*tasks), 30)
    for job in (first, second):
        assert (await stored_status(rig, job)) is JobStatus.DONE
        stored = await rig.store.get(job.id, include_expired=True)
        assert stored is not None and len(stored.results) == 18


async def test_two_large_jobs_take_turns_on_the_replicas(make_rig: Callable[..., Rig]) -> None:
    """With a free slot, the job served longest ago goes next, so two big jobs interleave their
    crops on ocsr instead of the second waiting for the whole first."""
    go = asyncio.Event()

    async def held(_parts: dict[str, bytes]) -> None:
        await go.wait()

    backend = FakeBackend(layout={0: 3, 1: 3}, pauses={"ocsr": held})
    # One ocsr call at a time, but each job may have three crops going (cls takes three): the
    # crops of both jobs are waiting for ocsr together.
    rig = make_rig(backend=backend, cls_concurrency=3)
    pool = rig.deps.services._caller._pools["ocsr"]
    served: list[str] = []
    acquire = pool.acquire

    async def spy(ticket: Ticket) -> Lease:
        lease = await acquire(ticket)
        served.append(ticket.job_id)
        return lease

    pool.acquire = spy  # type: ignore[method-assign]
    first = await rig.pdf_job(2, ip="client-1")
    second = await rig.pdf_job(2, ip="client-2")
    runs = asyncio.gather(rig.run(first), rig.run(second))
    await wait_until(lambda: pool.waiting == 5, timeout=20)  # one is on ocsr, five are waiting
    go.set()
    await asyncio.wait_for(runs, 20)
    assert sorted(served) == sorted([first.id] * 6 + [second.id] * 6)
    # While both jobs have crops waiting, the slot goes to each in turn (a strict queue would serve
    # the first job's three waiting crops before the second's).
    assert all(served[n] != served[n + 1] for n in range(5)), served


async def test_a_large_job_keeps_progressing_under_a_stream_of_small_jobs(
    make_rig: Callable[..., Rig],
) -> None:
    """Pasted images keep arriving, six at a time, from start to end of the run. They go first,
    but after `small_first_burst` of their calls in a row a waiting call of the large job gets
    the slot, so the large job finishes while the stream is still going."""
    total, together = 30, 6
    # Pages 1 and 2 have two structures each; page 0 none, so a pasted image is one crop.
    backend = FakeBackend(layout={1: 2, 2: 2}, service_delays={"ocsr": 0.05})
    # ocsr is the slow stage, so some small job is always waiting for it.
    rig = make_rig(backend=backend, small_first_burst=2)
    large = await rig.pdf_job(3, ip="large")
    small_done = 0

    async def pasted(number: int) -> None:
        nonlocal small_done
        job = await rig.image_job([(123, 77)], ip=f"small-{number}")
        await rig.run(job)
        small_done += 1

    async def stream() -> None:
        running: set[asyncio.Task[None]] = set()
        launched = 0
        while launched < total or running:
            while launched < total and len(running) < together:
                running.add(asyncio.create_task(pasted(launched)))
                launched += 1
            _, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)

    seen_by_the_large_job: list[int] = []
    large_run = asyncio.create_task(rig.run(large))
    large_run.add_done_callback(lambda _: seen_by_the_large_job.append(small_done))
    await asyncio.wait_for(asyncio.gather(stream(), large_run), 60)

    stored = large_run.result()
    assert stored.status is JobStatus.DONE and len(stored.results) == 4
    assert small_done == total
    # Without the bound the large job's calls would wait for the whole stream (30 jobs).
    assert seen_by_the_large_job[0] <= total - 2 * together, seen_by_the_large_job


# --- the size class ----------------------------------------------------------------------------


async def test_the_size_class_of_a_job_comes_from_what_the_api_stored(
    make_rig: Callable[..., Rig],
) -> None:
    rig = make_rig()
    cases = [
        (await rig.image_job([(50, 50)], ip="a"), True),  # a pasted image or one uploaded image
        (await rig.image_job([(50, 50), (60, 60)], ip="b"), False),  # two images
        (await rig.pdf_job(1, ip="c"), True),  # a PDF of one page
        (await rig.pdf_job(3, ip="d"), False),
        (await rig.pdf_job(10, total_pages=14, ip="e"), False),  # cut at the page limit
    ]
    assert [_Run(job, rig.deps).small for job, _ in cases] == [small for _, small in cases]


async def test_a_larger_limit_makes_two_page_jobs_small(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(api={"small_job_max_pages": 2})
    assert _Run(await rig.pdf_job(2, ip="a"), rig.deps).small
    assert not _Run(await rig.pdf_job(3, ip="b"), rig.deps).small


# --- every replica of a service is used by one job ---------------------------------------------


async def test_a_lone_pdf_uses_every_replica_of_every_stage(make_rig: Callable[..., Rig]) -> None:
    hosts, resolver = fleet(seg=2, ocsr=2, cls=2)
    # 6 pages, 12 crops. The calls take much longer than rendering a page, so that the next page
    # (and the next crop) is there before the call before it is answered.
    backend = FakeBackend(layout={page: 2 for page in range(6)}, hosts=hosts, delay=0.1)
    rig = make_rig(backend=backend, resolver=resolver)
    job = await rig.pdf_job(6)
    stored = await rig.run(job)
    assert stored.status is JobStatus.DONE and len(stored.results) == 12
    for service in ("seg", "ocsr", "cls"):
        replicas = resolver.answers[f"{service}.test"]
        assert all(backend.calls_to(replica) > 0 for replica in replicas), service
        assert backend.peak[service] == 2  # both at once: the job asked for as many as they take
    assert max(backend.host_peak.values()) == 1  # and never two at once on one replica


async def test_a_job_finishes_with_one_replica_dead_the_whole_time(
    make_rig: Callable[..., Rig],
) -> None:
    hosts, resolver = fleet(seg=2, ocsr=2, cls=2)
    backend = FakeBackend(layout={0: 2, 1: 2}, hosts=hosts)
    dead = resolver.answers["ocsr.test"][0]
    backend.down.add(dead)
    rig = make_rig(backend=backend, resolver=resolver)
    stored = await rig.run(await rig.pdf_job(2))
    assert stored.status is JobStatus.DONE and len(stored.results) == 4
    assert [w.code for w in stored.warnings] == []
    assert rig.sleep.delays == []  # the dead replica cost no waiting
