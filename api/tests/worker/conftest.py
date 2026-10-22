"""Fixtures for the worker tests: fake services, a store that records, and a way to make jobs."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import fakeredis
import httpx
import pytest
from PIL import Image

from decimer_api.models import Job, JobStatus, JobWarning, StageState, new_job_id
from decimer_api.settings import Settings
from decimer_api.store import Admission, JobStore
from decimer_api.worker.pipeline import Deps, run_pipeline
from decimer_api.worker.render import ThreadRenderer
from decimer_api.worker.replicas import Resolver
from decimer_api.worker.services import make_gates, make_services
from decimer_api.worker.settings import WorkerSettings
from tests.conftest import FakeClock
from tests.factories import make_pdf
from tests.worker.fakes import FakeBackend, FakeSleep, no_dns

IP = "client-a"
SMALL_PAGE = (144.0, 144.0)  # 2 x 2 in: 600 x 600 px at 300 DPI


class SpyStore(JobStore):
    """A real store (on fakeredis) that remembers every save and every finish."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.saves: list[Job] = []
        self.finishes: list[Job] = []

    async def save(self, job: Job) -> None:
        self.saves.append(job.model_copy(deep=True))
        await super().save(job)

    async def finish(self, job: Job) -> None:
        self.finishes.append(job.model_copy(deep=True))
        await super().finish(job)

    def progress(self) -> list[tuple[str, ...]]:
        """The stage states (render, segment, recognise, check) of every save of a running job,
        without immediate repeats."""
        seen: list[tuple[str, ...]] = []
        for job in self.saves:
            if job.status is not JobStatus.RUNNING:
                continue
            states = tuple(stage.state.value for stage in job.stages[1:])
            if not seen or seen[-1] != states:
                seen.append(states)
        return seen


@dataclass
class Rig:
    settings: Settings
    worker: WorkerSettings
    store: SpyStore
    backend: FakeBackend
    sleep: FakeSleep
    deps: Deps
    clock: FakeClock

    def _folder(self, job_id: str) -> Path:
        return self.settings.data_dir / job_id

    async def _admit(
        self,
        pages: int,
        processed: int | None,
        warnings: list[JobWarning] | None,
        truncated: bool,
        ip: str,
    ) -> Job:
        job = Job.create(
            job_id=new_job_id(),
            client_ip_hash=ip,
            pages=pages,
            pages_processed=pages if processed is None else processed,
            now=self.clock(),
            warnings=warnings,
            truncated=truncated,
        )
        (self._folder(job.id) / "input").mkdir(parents=True)
        assert await self.store.admit(job) is Admission.OK
        return job

    async def pdf_job(
        self,
        pages: int = 3,
        *,
        data: bytes | None = None,
        total_pages: int | None = None,
        warnings: list[JobWarning] | None = None,
        truncated: bool = False,
        ip: str = IP,
    ) -> Job:
        job = await self._admit(total_pages or pages, pages, warnings, truncated, ip)
        pdf = data if data is not None else make_pdf(pages, page_size=SMALL_PAGE)
        (self._folder(job.id) / "input" / "document.pdf").write_bytes(pdf)
        return job

    async def image_job(self, sizes: list[tuple[int, int]] | None = None, *, ip: str = IP) -> Job:
        sizes = sizes or [(123, 77)]
        job = await self._admit(len(sizes), None, None, False, ip)
        for number, size in enumerate(sizes, start=1):
            buffer = io.BytesIO()
            Image.new("RGB", size, (250, 250, 250)).save(buffer, format="PNG")
            (self._folder(job.id) / "input" / f"{number:04d}.png").write_bytes(buffer.getvalue())
        return job

    def file(self, job: Job, name: str) -> Path:
        return self._folder(job.id) / name

    async def run(self, job: Job) -> Job:
        """Run the pipeline, then read the stored record back."""
        await run_pipeline(job.id, self.deps)
        stored = await self.store.get(job.id, include_expired=True)
        assert stored is not None
        return stored


@pytest.fixture(autouse=True)
def pillow_defaults() -> Iterator[None]:
    """`ingest.run_guarded` (run in-process by some tests) lowers Pillow's global pixel limit to
    the API's; in the worker process nothing does, so the worker tests start from Pillow's own."""
    original = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 89_478_485
    yield
    Image.MAX_IMAGE_PIXELS = original


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
def spy_store(redis: fakeredis.FakeAsyncRedis, settings: Settings, clock: FakeClock) -> SpyStore:
    return SpyStore(redis, settings, clock=clock)


@pytest.fixture
async def make_rig(
    settings: Settings, spy_store: SpyStore, clock: FakeClock, sleep: FakeSleep
) -> AsyncIterator[Callable[..., Rig]]:
    clients: list[httpx.AsyncClient] = []

    def factory(
        *,
        api: dict[str, object] | None = None,
        backend: FakeBackend | None = None,
        resolver: Resolver = no_dns,
        **worker_overrides: object,
    ) -> Rig:
        api_settings = settings.model_copy(update=api or {})
        values: dict[str, object] = {
            "seg_url": "http://seg.test",
            "ocsr_url": "http://ocsr.test",
            "cls_url": "http://cls.test",
            "chem_url": "",
            "connect_retry_budget_seconds": 1.5,  # two retries: waits of 0.5 s and 1 s
            "heartbeat_seconds": 60,
        }
        worker = WorkerSettings(**{**values, **worker_overrides})  # type: ignore[arg-type]
        fake = backend or FakeBackend()
        client = httpx.AsyncClient(transport=fake.transport)
        clients.append(client)
        gates = make_gates(worker)
        services = make_services(client, worker, sleep=sleep, resolver=resolver)
        deps = Deps(
            settings=api_settings,
            worker=worker,
            store=spy_store,
            services=services,
            renderer=ThreadRenderer(),
            gates=gates,
        )
        return Rig(api_settings, worker, spy_store, fake, sleep, deps, clock)

    yield factory
    for client in clients:
        await client.aclose()


@pytest.fixture
def rig(make_rig: Callable[..., Rig]) -> Rig:
    return make_rig()


def states(job: Job) -> list[str]:
    return [stage.state.value for stage in job.stages]


def all_done(job: Job) -> bool:
    return all(stage.state is StageState.DONE for stage in job.stages)


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)
