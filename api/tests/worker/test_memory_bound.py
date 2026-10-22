"""What a worker holds in memory while jobs wait for replicas.

A page (or a crop) is read from disk, decoded and held as a PNG only once it has a slot on a
replica. Before, every waiting page of every job was read first: 8 jobs of 6 seg slots each held
48 pages of up to 36 MiB, and up to a dozen threads decoded 25-50 MP images at once."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from decimer_api.models import JobStatus
from decimer_api.worker import pipeline
from tests.worker.conftest import Rig, wait_until
from tests.worker.fakes import FakeBackend, fleet


@pytest.fixture
def prepares(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """How many images were prepared for seg (pages) and for the models (crops)."""
    counts = {"pages": 0, "crops": 0}
    real = pipeline.fit_png

    def counting(data: bytes, **limits: Any) -> Any:
        counts["crops" if "max_side" in limits else "pages"] += 1
        return real(data, **limits)

    monkeypatch.setattr(pipeline, "fit_png", counting)
    return counts


async def test_a_page_is_read_only_when_it_has_a_seg_slot(
    make_rig: Callable[..., Rig], prepares: dict[str, int]
) -> None:
    go = asyncio.Event()

    async def held(_parts: dict[str, bytes]) -> None:
        await go.wait()

    hosts, resolver = fleet(seg=2, ocsr=2, cls=2)
    backend = FakeBackend(layout={page: 1 for page in range(6)}, hosts=hosts, pauses={"seg": held})
    rig = make_rig(backend=backend, resolver=resolver)
    seg = rig.deps.services._caller._pools["seg"]
    jobs = [await rig.pdf_job(6, ip=f"client-{n}") for n in range(3)]
    runs = asyncio.gather(*(rig.run(job) for job in jobs))
    # Both seg replicas are busy, and the other pages (two per job may be going) are waiting.
    await wait_until(lambda: backend.active["seg"] == 2 and seg.waiting == 4, timeout=20)
    assert prepares["pages"] == 2  # the two pages that have a slot, not the six that want one
    go.set()
    stored = await asyncio.wait_for(runs, 30)
    assert all(job.status is JobStatus.DONE for job in stored)
    assert prepares["pages"] == 18  # and every page was read in the end


async def test_a_crop_is_read_only_when_it_has_an_ocsr_slot(
    make_rig: Callable[..., Rig], prepares: dict[str, int]
) -> None:
    go = asyncio.Event()

    async def held(_parts: dict[str, bytes]) -> None:
        await go.wait()

    hosts, resolver = fleet(seg=2, ocsr=2, cls=2)
    backend = FakeBackend(layout={0: 3, 1: 3}, hosts=hosts, pauses={"ocsr": held})
    # cls takes six calls at once, so a job has six crops going and two ocsr slots to put them in.
    rig = make_rig(backend=backend, resolver=resolver, cls_concurrency=3)
    ocsr = rig.deps.services._caller._pools["ocsr"]
    jobs = [await rig.pdf_job(2, ip=f"client-{n}") for n in range(2)]
    runs = asyncio.gather(*(rig.run(job) for job in jobs))
    await wait_until(lambda: backend.active["ocsr"] == 2 and ocsr.waiting == 10, timeout=20)
    assert prepares["crops"] == 2  # the crops that have a slot; ten more are waiting for one
    go.set()
    stored = await asyncio.wait_for(runs, 30)
    assert all(job.status is JobStatus.DONE for job in stored)
    assert prepares["crops"] == 12


async def test_a_slot_is_given_back_when_the_page_cannot_be_read(
    make_rig: Callable[..., Rig], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A page that cannot be prepared is skipped with its warning and its slot is free for the
    next call, not held by the block that failed."""
    rig = make_rig(backend=FakeBackend(layout={0: 1}))

    def unreadable(_data: bytes, **_limits: Any) -> Any:
        raise ValueError("not an image")

    monkeypatch.setattr(pipeline, "fit_png", unreadable)
    seg = rig.deps.services._caller._pools["seg"]
    stored = await rig.run(await rig.pdf_job(1))
    assert stored.status is JobStatus.FAILED  # no page could be searched
    assert [w.code for w in stored.warnings][:1] == ["page_segmentation_failed"]
    assert seg.in_flight == {"seg.test": 0}
