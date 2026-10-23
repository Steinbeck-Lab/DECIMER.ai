"""The worker does not depend on chem: it starts without it, and a job runs to the end
while chem is down: no names, one warning, and no long wait (chem is optional, a model is not)."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from decimer_api.models import Job, JobStatus
from decimer_api.settings import Settings
from decimer_api.worker import arq_app
from decimer_api.worker.errors import ServiceUnreachable
from decimer_api.worker.services import ServiceCaller, Services, make_pools, make_services
from decimer_api.worker.settings import WorkerSettings
from tests.worker.conftest import Rig
from tests.worker.fakes import FakeBackend, FakeSleep, crop_id, json_response

RigFactory = Callable[..., Rig]
CHEM = {"chem_url": "http://chem.test", "chem_connect_retry_budget_seconds": 5.0}
UNCLOSED = "c1ccccc"  # RDKit refuses it, so the CDK step has something to ask chem about


def codes(job: Job) -> list[str]:
    return [warning.code for warning in job.warnings]


def refuse(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def ocsr_says(*answers: str) -> Callable[..., httpx.Response]:
    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response:
        _page, index = crop_id(parts["image"])
        return json_response(
            {"smiles": answers[index], "token_confidence": [], "mean_confidence": 0.8}
        )

    return hook


async def test_the_worker_starts_without_touching_chem(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start-up hook builds the pools and looks nothing up: chem may be down or starting."""
    looked_up: list[str] = []
    real = socket.getaddrinfo

    def spy(host: Any, *args: Any, **kwargs: Any) -> Any:
        looked_up.append(str(host))
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    worker = WorkerSettings(
        seg_url="http://seg.invalid:8001", chem_url="http://chem.invalid:8004", cdk_fallback=True
    )
    ctx: dict[str, Any] = {}
    await arq_app.make_startup(settings, worker)(ctx)
    assert arq_app.DEPS_KEY in ctx
    assert [host for host in looked_up if host.endswith(".invalid")] == []
    await arq_app.shutdown(ctx)


async def test_a_job_runs_to_the_end_while_chem_is_down(make_rig: RigFactory) -> None:
    backend = FakeBackend(layout={0: 3}, hooks={"chem": refuse})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert len(job.results) == 3 and all(result.valid for result in job.results)
    assert [result.name for result in job.results] == [None] * 3
    assert codes(job) == ["names_unavailable"]  # one warning, not one per structure


async def test_chem_down_costs_a_job_seconds_not_the_models_ninety(
    make_rig: RigFactory,
) -> None:
    """seg, ocsr and cls are waited for up to CONNECT_RETRY_BUDGET_SECONDS; chem, which is optional,
    only up to its own, much shorter budget."""
    backend = FakeBackend(layout={0: 3}, hooks={"chem": refuse})
    rig = make_rig(
        backend=backend,
        connect_retry_budget_seconds=90.0,
        **{**CHEM, "chem_connect_retry_budget_seconds": 2.0},
    )
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert 0 < sum(rig.sleep.delays) <= 2.0  # the waits of chem: 0.5 + 1 s, then it gave up
    assert backend.count("chem") >= 1


async def test_the_models_are_still_waited_for_with_their_long_budget(
    make_rig: RigFactory,
) -> None:
    def seg_refuses(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    backend = FakeBackend(layout={0: 1}, hooks={"seg": seg_refuses})
    rig = make_rig(
        backend=backend,
        connect_retry_budget_seconds=30.0,
        **{**CHEM, "chem_connect_retry_budget_seconds": 1.0},
    )
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.FAILED  # seg stays down: the job fails, after the long wait
    assert sum(rig.sleep.delays) > 10.0


async def test_an_unreachable_chem_is_asked_once_per_job_not_once_for_cdk_and_again_for_names(
    make_rig: RigFactory,
) -> None:
    backend = FakeBackend(layout={0: 2}, hooks={"chem": refuse, "ocsr": ocsr_says(UNCLOSED, "CC")})
    rig = make_rig(backend=backend, **CHEM)
    job = await rig.run(await rig.pdf_job(1))
    assert job.status is JobStatus.DONE
    assert [result.valid for result in job.results] == [False, True]
    waits = list(rig.sleep.delays)
    # One series of waits (0.5 + 1 + 2 s, then the budget of 5 s would be passed), not two.
    assert waits == [0.5, 1.0, 2.0]
    assert sorted(codes(job)) == ["cdk_unavailable", "names_unavailable"]


class Blackhole:
    """A fake clock, and a chem that drops packets: every attempt takes the connect timeout."""

    def __init__(self, connect_timeout: float) -> None:
        self.now = 0.0
        self.attempts: list[float] = []
        self._timeout = connect_timeout

    def clock(self) -> float:
        return self.now

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.attempts.append(self.now)
        self.now += self._timeout
        raise httpx.ConnectTimeout("no answer", request=request)


async def blackholed_call(
    hole: Blackhole, *, chem_budget: float, models_budget: float
) -> tuple[Exception, list[float]]:
    worker = WorkerSettings(
        seg_url="http://seg.test",
        chem_url="http://chem.test",
        connect_timeout_seconds=hole._timeout,  # noqa: SLF001
        chem_connect_retry_budget_seconds=chem_budget,
        connect_retry_budget_seconds=models_budget,
    )
    sleep = FakeSleep()
    async with httpx.AsyncClient(transport=httpx.MockTransport(hole.handler)) as client:
        # `make_services`, as the worker builds it: the clock reaches the caller through it
        services = make_services(
            client, worker, sleep=sleep, resolver=no_dns_resolver, clock=hole.clock
        )
        try:
            with pytest.raises(ServiceUnreachable) as error:
                await services.names(["CCO"])
            return error.value, sleep.delays
        finally:
            await services.aclose()


async def no_dns_resolver(_host: str, _port: int) -> list[str]:
    return []


async def test_a_blackholed_chem_costs_one_connect_timeout_not_five_of_them() -> None:
    """The 5 s budget counts the failed attempts as well as the waiting between them: a chem that
    drops packets takes the connect timeout (5 s) for every attempt, and used to be tried 4 times
    (23 s)."""
    hole = Blackhole(connect_timeout=5.0)
    error, delays = await blackholed_call(hole, chem_budget=5.0, models_budget=90.0)
    assert len(hole.attempts) == 1, hole.attempts
    assert hole.now == 5.0 and delays == []  # one attempt, and no waiting after it
    assert "unreachable (ConnectTimeout)" in str(error)


async def test_a_chem_that_answers_slowly_to_connect_still_gets_its_few_tries() -> None:
    hole = Blackhole(
        connect_timeout=1.0
    )  # fails fast: 1 s each, backing off 0.5, 1, ... inside 5 s
    _error, delays = await blackholed_call(hole, chem_budget=5.0, models_budget=90.0)
    assert 1 < len(hole.attempts) <= 3
    assert hole.now + sum(delays) <= 5.0 + 1.0  # within the budget, give or take the last attempt


async def test_the_models_keep_their_long_budget_when_they_drop_packets() -> None:
    hole = Blackhole(connect_timeout=5.0)
    worker = WorkerSettings(
        seg_url="http://seg.test", connect_timeout_seconds=5.0, connect_retry_budget_seconds=30.0
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(hole.handler)) as client:
        pools = make_pools(worker, resolver=no_dns_resolver)
        caller = ServiceCaller(client, pools, worker, sleep=FakeSleep(), clock=hole.clock)
        services = Services(caller, worker)
        with pytest.raises(ServiceUnreachable):
            await services.segment(b"\x89PNG\r\n\x1a\n" + b"0" * 64, 0)
        await services.aclose()
    assert 3 <= len(hole.attempts) <= 6  # several tries, until 30 s are spent, not one


async def test_replicas_that_all_drop_packets_are_not_all_tried_past_the_budget() -> None:
    """Three replicas, all black holes (5 s to time out), a 4 s budget: the first attempt spends it,
    so the other two are not tried (without counting attempts: three timeouts, 15 s)."""
    hole = Blackhole(connect_timeout=5.0)

    async def three(_host: str, _port: int) -> list[str]:
        return ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    worker = WorkerSettings(
        seg_url="http://seg.test:8001",
        connect_timeout_seconds=5.0,
        connect_retry_budget_seconds=4.0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(hole.handler)) as client:
        services = make_services(
            client, worker, sleep=FakeSleep(), resolver=three, clock=hole.clock
        )
        with pytest.raises(ServiceUnreachable):
            await services.segment(b"\x89PNG\r\n\x1a\n" + b"0" * 64, 0)
        await services.aclose()
    assert len(hole.attempts) == 1, hole.attempts


async def test_the_chem_budget_is_five_seconds_by_default() -> None:
    settings = WorkerSettings()
    assert settings.connect_retry_budget("chem") == 5.0
    assert settings.connect_retry_budget("seg") == 90.0
    assert WorkerSettings(chem_connect_retry_budget_seconds=0).connect_retry_budget("chem") == 0
