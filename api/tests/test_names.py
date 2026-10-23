"""The api's client of the chem service: a name is a bonus and never an error."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from decimer_api.names import (
    BATCH_SIZE,
    MAX_SMILES_CHARS,
    NO_NAME,
    ChemNamer,
    NameInfo,
    NoNames,
    build_namer,
    nameable,
)
from decimer_api.settings import Settings
from decimer_api.worker.services import make_services
from decimer_api.worker.settings import WorkerSettings
from tests.worker.fakes import FakeBackend, json_response, no_dns


@pytest.fixture
async def make_namer() -> AsyncIterator[Callable[..., tuple[ChemNamer, FakeBackend]]]:
    clients: list[httpx.AsyncClient] = []

    def factory(backend: FakeBackend, timeout: float = 5.0) -> tuple[ChemNamer, FakeBackend]:
        worker = WorkerSettings(
            chem_url="http://chem.test",
            chem_timeout_seconds=timeout,
            connect_retry_budget_seconds=0,
            busy_retries=0,
            sent_retries=0,
        )
        client = httpx.AsyncClient(transport=backend.transport)
        clients.append(client)
        services = make_services(client, worker, resolver=no_dns)
        return ChemNamer(services, timeout_seconds=timeout), backend

    yield factory
    for client in clients:
        await client.aclose()


async def test_names_come_back_in_order_with_their_tier_and_check(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(
        names={"CCO": ("ethanol", "pin_verified"), "CCN": ("ethanamine", "valid")},
        verifieds={"CCN": "opsin_constitution"},
    )
    namer, _ = make_namer(backend)
    assert await namer(["CCN", "C", "CCO"]) == [
        NameInfo("ethanamine", "valid", "opsin_constitution"),
        NO_NAME,  # chem declined methane
        NameInfo("ethanol", "pin_verified", "opsin"),
    ]


async def test_nothing_is_asked_when_there_is_nothing_to_ask(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    namer, backend = make_namer(FakeBackend())
    assert await namer([]) == []
    assert backend.count("chem") == 0


async def test_names_are_not_kept_between_calls(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    """Orthonym can name one structure differently depending on what it named before: every
    request asks again."""
    backend = FakeBackend(names={"CCO": ("ethanol", "valid")})
    namer, _ = make_namer(backend)
    await namer(["CCO"])
    backend.names["CCO"] = ("ethyl alcohol", "valid")
    assert (await namer(["CCO"]))[0].name == "ethyl alcohol"
    assert backend.count("chem") == 2


async def test_more_than_one_batch_is_split_in_batches_of_twenty(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    smiles = ["C" * (n + 1) for n in range(BATCH_SIZE + 5)]
    backend = FakeBackend(names={s: (f"name {s}", "valid") for s in smiles})
    namer, _ = make_namer(backend)
    answers = await namer(smiles)
    assert [a.name for a in answers] == [f"name {s}" for s in smiles]
    assert backend.count("chem") == 2


async def test_a_failure_in_the_second_batch_keeps_the_first_batchs_names(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    smiles = ["C" * (n + 1) for n in range(BATCH_SIZE + 3)]
    backend = FakeBackend(names={s: (f"name {s}", "valid") for s in smiles})
    seen = 0

    def second_call_fails(
        _request: httpx.Request, _parts: dict[str, bytes]
    ) -> httpx.Response | None:
        nonlocal seen
        seen += 1
        return httpx.Response(500, text="boom") if seen == 2 else None

    backend.hooks["chem"] = second_call_fails
    namer, _ = make_namer(backend)
    answers = await namer(smiles)
    assert [a.name for a in answers[:BATCH_SIZE]] == [f"name {s}" for s in smiles[:BATCH_SIZE]]
    assert answers[BATCH_SIZE:] == [NO_NAME] * 3


@pytest.mark.parametrize(
    "hook",
    [
        lambda request, _parts: httpx.Response(500, text="boom"),
        lambda request, _parts: httpx.Response(503, headers={"Retry-After": "30"}),
        lambda request, _parts: httpx.Response(422, json={"detail": "no"}),
        lambda request, _parts: httpx.Response(200, text="not json"),
        lambda request, _parts: json_response({"results": []}),  # one answer too few
        lambda request, _parts: json_response({"nonsense": True}),
    ],
    ids=["500", "503", "422", "not-json", "wrong-count", "wrong-shape"],
)
async def test_a_chem_that_misbehaves_means_no_name_and_no_exception(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]], hook: object
) -> None:
    backend = FakeBackend(names={"CCO": ("ethanol", "valid")})
    backend.hooks["chem"] = hook  # type: ignore[assignment]
    namer, _ = make_namer(backend)
    started = time.monotonic()
    assert await namer(["CCO"]) == [NO_NAME]
    assert time.monotonic() - started < 2  # no retry with sleeps: a person is waiting
    assert backend.count("chem") == 1  # and no second try


async def test_a_chem_that_cannot_be_reached_is_no_name_at_once(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(names={"CCO": ("ethanol", "valid")}, down={"chem.test"})
    namer, _ = make_namer(backend)
    started = time.monotonic()
    assert await namer(["CCO"]) == [NO_NAME]
    assert time.monotonic() - started < 1


async def test_a_chem_that_is_too_slow_is_no_name_after_the_timeout(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(names={"CCO": ("ethanol", "valid")}, service_delays={"chem": 5.0})
    namer, _ = make_namer(backend, timeout=0.1)
    started = time.monotonic()
    assert await namer(["CCO"]) == [NO_NAME]
    assert 0.09 < time.monotonic() - started < 1.5


async def test_the_timeout_covers_waiting_for_chem_too(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    """One call at a time to chem (CHEM_CONCURRENCY): a second request waits for the first, and
    that wait is inside the budget. The slow first call must not make the second wait for it."""
    import asyncio

    backend = FakeBackend(names={"CCO": ("ethanol", "valid")}, service_delays={"chem": 0.5})
    namer, _ = make_namer(backend, timeout=0.15)
    started = time.monotonic()
    answers = await asyncio.gather(namer(["CCO"]), namer(["CCO"]))
    assert answers == [[NO_NAME], [NO_NAME]]
    assert time.monotonic() - started < 0.4


async def test_a_cancelled_request_is_cancelled_not_swallowed(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    import asyncio

    backend = FakeBackend(service_delays={"chem": 5.0})
    namer, _ = make_namer(backend, timeout=30)
    task = asyncio.create_task(namer(["CCO"]))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --- which structures can be asked about ----------------------------------------------------------


@pytest.mark.parametrize(
    ("smiles", "expected"),
    [
        ("CCO", True),
        ("", False),
        ("[1*]CC", False),  # an R group has no name
        ("*C", False),
        ("C" * MAX_SMILES_CHARS, True),
        ("C" * (MAX_SMILES_CHARS + 1), False),
    ],
)
def test_nameable(smiles: str, expected: bool) -> None:
    assert nameable(smiles) is expected


# --- wiring --------------------------------------------------------------------------------------


async def test_no_chem_url_means_no_namer_that_connects_anywhere(settings: Settings) -> None:
    namer = build_namer(settings)
    assert isinstance(namer, NoNames)
    assert await namer(["CCO", "CCN"]) == [NO_NAME, NO_NAME]
    await namer.aclose()


async def test_a_chem_url_builds_a_namer_with_no_retries_and_a_budget(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"chem_url": "http://chem:8004", "names_timeout_seconds": 3}
    )
    namer = build_namer(configured)
    assert isinstance(namer, ChemNamer)
    worker = namer._services._settings  # noqa: SLF001
    assert worker.chem_url == "http://chem:8004"
    assert (worker.connect_retry_budget_seconds, worker.busy_retries, worker.sent_retries) == (
        0,
        0,
        0,
    )
    assert worker.chem_timeout_seconds == 3
    assert namer._timeout == 3  # noqa: SLF001
    await namer.aclose()
    assert namer._http is not None and namer._http.is_closed  # noqa: SLF001


def test_an_empty_chem_url_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHEM_URL", "  ")
    assert Settings().chem_url is None


# --- the api stays out of the worker's way (review finding 3) ------------------------------------


async def _held(
    backend: FakeBackend,
) -> tuple[asyncio.Event, asyncio.Event]:
    """Make chem hold every call until `release` is set; `arrived` is set when one is inside."""
    arrived, release = asyncio.Event(), asyncio.Event()

    async def hold(_parts: dict[str, bytes]) -> None:
        arrived.set()
        await release.wait()

    backend.pauses["chem"] = hold
    return arrived, release


async def test_a_validate_that_finds_chem_occupied_has_no_name_at_once_and_does_not_queue(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(names={"CCO": ("ethanol", "valid"), "CCN": ("ethanamine", "valid")})
    arrived, release = await _held(backend)
    namer, _ = make_namer(backend, timeout=5)
    first = asyncio.create_task(namer(["CCO"]))
    await arrived.wait()
    started = time.monotonic()
    second = await namer(["CCN"])  # the default: not patient
    assert second == [NO_NAME]
    assert time.monotonic() - started < 0.1  # no waiting for the first call
    assert backend.count("chem") == 1  # and it never reached chem
    release.set()
    assert (await first)[0].name == "ethanol"  # the first one is not disturbed


async def test_the_api_never_has_more_than_one_call_in_flight_to_chem(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(
        names={f"C{'C' * n}": (f"name {n}", "valid") for n in range(10)},
        service_delays={"chem": 0.02},
    )
    namer, _ = make_namer(backend, timeout=5)
    smiles = [f"C{'C' * n}" for n in range(10)]
    patient = [namer([s], patient=True) for s in smiles[:5]]
    impatient = [namer([s]) for s in smiles[5:]]
    await asyncio.gather(*patient, *impatient)
    assert backend.peak["chem"] == 1


async def test_an_export_waits_for_the_place_and_gets_its_names(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    """A person waiting for a file asked for the names: the export queues behind the validate."""
    backend = FakeBackend(
        names={"CCO": ("ethanol", "valid"), "CCN": ("ethanamine", "valid")},
        service_delays={"chem": 0.2},
    )
    namer, _ = make_namer(backend, timeout=5)
    first = asyncio.create_task(namer(["CCO"]))
    await asyncio.sleep(0.05)  # the validate is inside chem
    waited = await namer(["CCN"], patient=True)
    assert waited[0].name == "ethanamine"
    assert (await first)[0].name == "ethanol"
    assert backend.count("chem") == 2


async def test_a_patient_call_still_ends_at_the_budget(
    make_namer: Callable[..., tuple[ChemNamer, FakeBackend]],
) -> None:
    backend = FakeBackend(names={"CCO": ("ethanol", "valid")}, service_delays={"chem": 5.0})
    namer, _ = make_namer(backend, timeout=0.15)
    first = asyncio.create_task(namer(["CCO"]))
    await asyncio.sleep(0.03)
    started = time.monotonic()
    assert await namer(["CCO"], patient=True) == [NO_NAME]
    assert time.monotonic() - started < 0.4
    assert await first == [NO_NAME]


def test_the_budget_is_four_seconds() -> None:
    assert Settings().names_timeout_seconds == 4.0
