"""The HTTP layer, with a fake namer: shapes, status codes, limits, and that it stays up."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fakes import FakeNamer, named
from fastapi import FastAPI

from decimer_chem.app import (
    CAFFEINE_NAME,
    CAFFEINE_SMILES,
    LIMITS,
    Limits,
    build_app,
    create_app,
)
from decimer_chem.results import TIMEOUT_REASON, NameResult
from decimer_chem.supervisor import EngineUnavailable, ProcessNamer

KEYS = {"name", "tier", "verified", "reason"}


@pytest.fixture
def namer() -> FakeNamer:
    return FakeNamer()


async def client_for(
    namer: FakeNamer, limits: Limits | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(namer, limits or Limits())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chem") as client:
            yield client


@pytest.fixture
async def client(namer: FakeNamer) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(namer):
        yield c


# --- startup and the selftest ---------------------------------------------------------------------


async def test_startup_starts_the_namer_and_names_caffeine_before_serving(namer: FakeNamer) -> None:
    async for client in client_for(namer):
        assert namer.started
        assert namer.calls == [[CAFFEINE_SMILES]]
        assert (await client.get("/healthz")).json() == {"status": "ok", "service": "chem"}


async def test_startup_fails_when_caffeine_gets_another_name(namer: FakeNamer) -> None:
    namer.rule = lambda s: NameResult("something else", "pin_verified", "opsin", None)
    app = create_app(namer, Limits())
    with pytest.raises(RuntimeError, match="selftest"):
        async with app.router.lifespan_context(app):
            pytest.fail("the service must not start")
    assert namer.stopped


async def test_startup_fails_when_caffeine_name_was_not_checked_by_opsin(namer: FakeNamer) -> None:
    namer.rule = lambda s: NameResult(CAFFEINE_NAME, "pin_verified", "unverified", None)
    app = create_app(namer, Limits())
    with pytest.raises(RuntimeError, match="selftest"):
        async with app.router.lifespan_context(app):
            pytest.fail("the service must not start")


async def test_startup_fails_when_the_namer_cannot_start(namer: FakeNamer) -> None:
    namer.start_error = RuntimeError("OPSIN jar not found at /opt/jars")
    app = create_app(namer, Limits())
    with pytest.raises(RuntimeError, match="/opt/jars"):
        async with app.router.lifespan_context(app):
            pytest.fail("the service must not start")


async def test_the_namer_is_stopped_at_shutdown(namer: FakeNamer) -> None:
    async for _ in client_for(namer):
        assert not namer.stopped
    assert namer.stopped


async def test_the_caffeine_selftest_pins_the_name_in_the_readme() -> None:
    assert CAFFEINE_NAME == "1,3,7-trimethyl-3,7-dihydro-1H-purine-2,6-dione"
    assert CAFFEINE_SMILES == "Cn1cnc2c1c(=O)n(C)c(=O)n2C"


# --- /name ----------------------------------------------------------------------------------------


async def test_name_returns_exactly_four_keys_for_a_named_structure(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    r = await client.post("/name", json={"smiles": "CCO"})
    assert r.status_code == 200
    assert set(r.json()) == KEYS
    assert r.json() == {
        "name": "name of CCO",
        "tier": "pin_verified",
        "verified": "opsin",
        "reason": None,
    }
    assert namer.calls[-1] == ["CCO"]


async def test_a_decline_is_a_200_with_a_null_name_and_orthonyms_tier_and_reason(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.rule = lambda s: NameResult(None, "abstain", None, "UNNAMEABLE")
    r = await client.post("/name", json={"smiles": "CCO"})
    assert r.status_code == 200
    assert r.json() == {"name": None, "tier": "abstain", "verified": None, "reason": "UNNAMEABLE"}


async def test_a_timeout_is_a_200_decline_with_reason_timeout_not_a_504(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.rule = lambda s: NameResult(None, None, None, TIMEOUT_REASON)
    r = await client.post("/name", json={"smiles": "CCO"})
    assert r.status_code == 200
    assert r.json() == {"name": None, "tier": None, "verified": None, "reason": "timeout"}


async def test_surrounding_whitespace_is_stripped(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    r = await client.post("/name", json={"smiles": "  CCO\n"})
    assert r.status_code == 200
    assert namer.calls[-1] == ["CCO"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"smiles": ""},
        {"smiles": "   "},
        {"smiles": None},
        {"smiles": 5},
        {"smiles": ["CCO"]},
        {"smiles": "C" * 2001},
        {"smiles": "CC O"},  # RDKit would read " O" as the molecule's name
        {"smiles": "CC\tO"},
        {"smiles": "CC\x00O"},
        {"smiles": "CÄO"},
        {"smiles": "CCO", "extra": 1},
    ],
    ids=[
        "missing",
        "empty",
        "blank",
        "null",
        "number",
        "list",
        "too-long",
        "inner-space",
        "inner-tab",
        "nul",
        "non-ascii",
        "extra-field",
    ],
)
async def test_name_422_on_bad_input_and_the_namer_is_not_called(
    namer: FakeNamer, client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    before = len(namer.calls)
    r = await client.post("/name", json=body)
    assert r.status_code == 422
    assert len(namer.calls) == before


async def test_a_smiles_of_exactly_2000_characters_is_accepted(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    r = await client.post("/name", json={"smiles": "C" * 2000})
    assert r.status_code == 200


@pytest.mark.parametrize("raw", [b"", b"{", b"[1,2", b"not json", b"\xff\xfe"])
async def test_name_422_on_a_body_that_is_not_json(client: httpx.AsyncClient, raw: bytes) -> None:
    r = await client.post("/name", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422


INVALID = NameResult(None, None, None, "invalid_smiles")


async def test_a_smiles_the_engine_cannot_read_is_a_422_for_name(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.rule = lambda s: INVALID
    r = await client.post("/name", json={"smiles": "C1CC"})
    assert r.status_code == 422
    assert r.json() == {
        "detail": [{"loc": ["body", "smiles"], "msg": "not a valid SMILES", "type": "value_error"}]
    }


async def test_every_422_has_the_same_detail_shape(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.rule = lambda s: INVALID
    bodies = [
        await client.post("/name", json={"smiles": ""}),  # fails the rules
        await client.post("/names", json={"smiles": ["C"] * 21}),
        await client.post("/name", content=b"{", headers={"content-type": "application/json"}),
        await client.post("/name", json={"smiles": "C1CC"}),  # the engine cannot read it
    ]
    for r in bodies:
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert isinstance(detail, list) and detail
        assert all({"loc", "msg", "type"} <= set(entry) for entry in detail)
        assert all(entry["loc"][0] == "body" for entry in detail)


async def test_names_marks_an_unreadable_smiles_in_place_instead_of_failing_the_batch(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.rule = lambda s: INVALID if s == "C1CC" else named(s)
    r = await client.post("/names", json={"smiles": ["CCO", "C1CC", "CCN"]})
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["reason"] for x in results] == [None, "invalid_smiles", None]
    assert results[0]["name"] == "name of CCO"
    assert results[2]["name"] == "name of CCN"


async def test_a_value_error_out_of_the_namer_is_a_bug_and_a_500(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.error = ValueError("nope")
    r = await client.post("/name", json={"smiles": "CCO"})
    assert r.status_code == 500
    assert "error_id" in r.json()


async def test_an_unavailable_engine_is_a_503_with_retry_after_not_a_500(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.error = EngineUnavailable("naming process could not start")
    for path, body in (("/name", {"smiles": "CCO"}), ("/names", {"smiles": ["CCO", "CCN"]})):
        r = await client.post(path, json=body)
        assert r.status_code == 503
        assert r.headers["retry-after"] == "5"
        assert r.json() == {"detail": "naming engine unavailable"}


async def test_a_body_over_the_size_limit_is_413_and_not_read_into_the_namer(
    namer: FakeNamer,
) -> None:
    async for client in client_for(namer, Limits(max_body_bytes=64)):
        before = len(namer.calls)
        r = await client.post("/name", json={"smiles": "C" * 100})
        assert r.status_code == 413
        assert len(namer.calls) == before


async def test_a_chunked_body_over_the_limit_is_413_too(namer: FakeNamer) -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b'{"smiles": "' + b"C" * 20 + b'"}'

    async for client in client_for(namer, Limits(max_body_bytes=64)):
        before = len(namer.calls)
        # an async generator is sent chunked, without a Content-Length header
        r = await client.post("/name", content=body(), headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert len(namer.calls) == before


# --- /names ---------------------------------------------------------------------------------------


async def test_names_returns_results_in_request_order(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    smiles = ["CCO", "CCN", "CCC", "CO"]
    r = await client.post("/names", json={"smiles": smiles})
    assert r.status_code == 200
    results = r.json()["results"]
    assert [x["name"] for x in results] == [f"name of {s}" for s in smiles]
    assert all(set(x) == KEYS for x in results)
    assert namer.calls[-1] == smiles  # one call: the worker reuses one JVM for the batch


async def test_names_mixes_names_declines_and_timeouts_in_place(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    def rule(s: str) -> NameResult:
        if s == "CCN":
            return NameResult(None, "abstain", None, "UNNAMEABLE")
        if s == "CCC":
            return NameResult(None, None, None, TIMEOUT_REASON)
        return named(s)

    namer.rule = rule
    r = await client.post("/names", json={"smiles": ["CCO", "CCN", "CCC", "CO"]})
    assert [x["reason"] for x in r.json()["results"]] == [None, "UNNAMEABLE", "timeout", None]


async def test_names_accepts_twenty_and_refuses_twenty_one(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    assert (await client.post("/names", json={"smiles": ["C"] * 20})).status_code == 200
    before = len(namer.calls)
    assert (await client.post("/names", json={"smiles": ["C"] * 21})).status_code == 422
    assert len(namer.calls) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"smiles": []},
        {"smiles": "CCO"},
        {"smiles": ["CCO", ""]},
        {"smiles": ["CCO", "C C"]},
        {"smiles": ["CCO", 3]},
        {"smiles": ["C" * 2001]},
    ],
    ids=["missing", "empty-list", "string", "empty-item", "inner-space", "number", "too-long"],
)
async def test_names_422_on_bad_input(
    namer: FakeNamer, client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    before = len(namer.calls)
    r = await client.post("/names", json=body)
    assert r.status_code == 422
    assert len(namer.calls) == before


# --- failures: 500, 503 ---------------------------------------------------------------------------


async def test_an_unexpected_error_is_a_500_with_an_error_id_and_the_service_stays_up(
    namer: FakeNamer, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    namer.error = RuntimeError("jvm exploded: secret-path")
    with caplog.at_level(logging.ERROR, logger="decimer_chem"):
        r = await client.post("/name", json={"smiles": "CCO"})
    assert r.status_code == 500
    body = r.json()
    assert set(body) == {"detail", "error_id"}
    assert len(body["error_id"]) == 12
    assert "secret-path" not in r.text  # no internals in the response
    logged = [rec for rec in caplog.records if getattr(rec, "error_id", None) == body["error_id"]]
    assert len(logged) == 1  # the id leads to one log record: the exception type and its frames
    assert logged[0].exc_type == "RuntimeError"  # type: ignore[attr-defined]
    assert "in name_many" in logged[0].traceback  # type: ignore[attr-defined]
    assert logged[0].exc_info is None  # not the exception itself: its message is not logged
    assert "secret-path" not in " ".join(str(r.__dict__) for r in caplog.records)

    namer.error = None  # one bad input did not take the service down
    assert (await client.post("/name", json={"smiles": "CCO"})).status_code == 200
    assert (await client.get("/healthz")).status_code == 200


async def test_each_failure_gets_its_own_error_id(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    namer.error = RuntimeError("x")
    ids = {
        (await client.post("/name", json={"smiles": "CCO"})).json()["error_id"] for _ in range(3)
    }
    assert len(ids) == 3


async def test_503_with_retry_after_when_saturated_and_the_waiting_requests_still_finish(
    namer: FakeNamer,
) -> None:
    async for client in client_for(namer, Limits(max_concurrency=1, max_waiting=1)):
        namer.hold = asyncio.Event()
        namer.entered.clear()
        first = asyncio.create_task(client.post("/name", json={"smiles": "C"}))
        await asyncio.wait_for(namer.entered.wait(), 5)
        second = asyncio.create_task(client.post("/name", json={"smiles": "CC"}))
        await asyncio.sleep(0.05)  # the second is now waiting for the slot
        # bounded: a service that queued it instead would wait here for ever
        third = await asyncio.wait_for(client.post("/name", json={"smiles": "CCC"}), 5)
        assert third.status_code == 503
        assert third.headers["retry-after"] == "5"
        assert third.json() == {"detail": "busy"}
        assert (await client.get("/healthz")).status_code == 200  # health does not queue
        namer.hold.set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200
        assert (await client.post("/name", json={"smiles": "CCC"})).status_code == 200  # slot free


async def test_a_request_that_waits_too_long_for_the_slot_is_a_503_with_retry_after(
    namer: FakeNamer,
) -> None:
    limits = Limits(max_concurrency=1, max_waiting=2, queue_timeout_s=0.3)
    async for client in client_for(namer, limits):
        namer.hold = asyncio.Event()
        namer.entered.clear()
        first = asyncio.create_task(client.post("/name", json={"smiles": "C"}))
        await asyncio.wait_for(namer.entered.wait(), 5)
        started = time.monotonic()
        second = await asyncio.wait_for(client.post("/name", json={"smiles": "CC"}), 5)
        assert 0.25 <= time.monotonic() - started < 3
        assert second.status_code == 503
        assert second.headers["retry-after"] == "5"
        assert second.json() == {"detail": "busy"}
        namer.hold.set()
        assert (await first).status_code == 200  # the running request was not affected
        # and the gate is whole again: the timed-out waiter did not keep its place
        assert (await client.post("/name", json={"smiles": "CCC"})).status_code == 200
        assert [c for c in namer.calls if c == ["CC"]] == []  # the waiter was never named


async def asgi_post(
    app: FastAPI, path: str, body: bytes, disconnect: asyncio.Event
) -> list[dict[str, Any]]:
    """POST straight at the ASGI app, so the test can decide when the client goes away."""
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("test", 1),
        "server": ("chem", 80),
    }
    await app(scope, receive, send)
    return messages


async def test_work_is_skipped_for_a_client_that_left_while_waiting_for_the_slot(
    namer: FakeNamer,
) -> None:
    app = create_app(namer, Limits(max_concurrency=1, max_waiting=2, queue_timeout_s=30))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chem") as client:
            namer.hold = asyncio.Event()
            namer.entered.clear()
            first = asyncio.create_task(client.post("/name", json={"smiles": "C"}))
            await asyncio.wait_for(namer.entered.wait(), 5)

            gone = asyncio.Event()
            waiting = asyncio.create_task(asgi_post(app, "/name", b'{"smiles": "CCCCC"}', gone))
            await asyncio.sleep(0.1)  # it is queued behind the first
            gone.set()  # the client hangs up while waiting
            namer.hold.set()
            messages = await asyncio.wait_for(waiting, 5)
            assert (await first).status_code == 200
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            assert status == 499
            assert ["CCCCC"] not in namer.calls  # never sent to the engine


async def test_a_waiting_client_that_stays_is_served(namer: FakeNamer) -> None:
    app = create_app(namer, Limits(max_concurrency=1, max_waiting=2, queue_timeout_s=30))
    async with app.router.lifespan_context(app):
        stays = asyncio.Event()  # never set: the client does not hang up
        messages = await asyncio.wait_for(asgi_post(app, "/name", b'{"smiles": "CCCCC"}', stays), 5)
        assert next(m["status"] for m in messages if m["type"] == "http.response.start") == 200
        assert ["CCCCC"] in namer.calls


# --- readiness ------------------------------------------------------------------------------------


async def test_readyz_uses_a_recent_pass_instead_of_naming_again(
    namer: FakeNamer, client: httpx.AsyncClient
) -> None:
    before = len(namer.calls)
    assert (await client.get("/readyz")).json() == {"status": "ready", "service": "chem"}
    assert len(namer.calls) == before  # startup selftest is fresh


async def test_readyz_runs_the_selftest_again_once_the_pass_is_old(namer: FakeNamer) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.0)):
        before = len(namer.calls)
        assert (await client.get("/readyz")).status_code == 200
        assert namer.calls[before:] == [[CAFFEINE_SMILES]]


async def test_readyz_503_when_the_selftest_now_fails(namer: FakeNamer) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.0)):
        namer.rule = lambda s: NameResult(None, "abstain", None, "UNNAMEABLE")  # e.g. no JVM
        r = await client.get("/readyz")
        assert r.status_code == 503
        assert "selftest" in r.json()["detail"]
        namer.rule = named  # and it recovers
        assert (await client.get("/readyz")).status_code == 200


async def test_readyz_503_when_the_namer_itself_is_down(namer: FakeNamer) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.0)):
        namer.error = RuntimeError("naming process could not be started")
        assert (await client.get("/readyz")).status_code == 503


async def test_steady_declines_do_not_keep_readyz_green_when_the_selftest_would_fail(
    namer: FakeNamer,
) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.3)):
        namer.rule = lambda s: NameResult(None, "abstain", None, "UNNAMEABLE")  # e.g. no OPSIN
        await asyncio.sleep(0.4)  # the startup pass is old now
        for _ in range(3):  # requests keep coming and are answered, but only with declines
            assert (await client.post("/name", json={"smiles": "CCO"})).status_code == 200
        assert (await client.get("/readyz")).status_code == 503


async def test_a_name_that_was_not_fully_round_tripped_does_not_refresh_readyz_either(
    namer: FakeNamer,
) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.3)):
        namer.rule = lambda s: NameResult("x", "pin_verified", "identity", None)
        await asyncio.sleep(0.4)
        assert (await client.post("/name", json={"smiles": "CCO"})).status_code == 200
        assert (await client.get("/readyz")).status_code == 503


async def test_a_fully_round_tripped_name_does_refresh_readyz(namer: FakeNamer) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.3)):
        await asyncio.sleep(0.4)
        assert (await client.post("/name", json={"smiles": "CCO"})).status_code == 200
        calls = len(namer.calls)
        assert (await client.get("/readyz")).status_code == 200
        assert len(namer.calls) == calls  # answered from the pass, no selftest run


async def test_readyz_does_not_queue_behind_a_running_batch(namer: FakeNamer) -> None:
    async for client in client_for(namer, Limits(ready_cache_seconds=0.0)):
        namer.hold = asyncio.Event()
        namer.entered.clear()
        running = asyncio.create_task(client.post("/names", json={"smiles": ["C", "CC"]}))
        await asyncio.wait_for(namer.entered.wait(), 5)
        calls = len(namer.calls)
        r = await asyncio.wait_for(client.get("/readyz"), 2)
        assert r.status_code == 200
        assert len(namer.calls) == calls  # it did not run a selftest behind the batch
        namer.hold.set()
        await running


# --- logging --------------------------------------------------------------------------------------


async def test_a_request_logs_counts_and_time_but_never_the_smiles(
    namer: FakeNamer, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    namer.rule = lambda s: (
        NameResult(None, "abstain", None, "UNNAMEABLE") if s == "CCN" else named(s)
    )
    with caplog.at_level(logging.INFO, logger="decimer_chem"):
        await client.post("/names", json={"smiles": ["CCO", "CCN"]})
    rec = next(r for r in caplog.records if r.getMessage() == "named")
    assert (rec.structures, rec.named, rec.declined) == (2, 1, 1)  # type: ignore[attr-defined]
    assert rec.ms >= 0  # type: ignore[attr-defined]
    assert "CCN" not in json.dumps([r.__dict__ for r in caplog.records], default=str)


# --- production wiring ----------------------------------------------------------------------------


def test_production_limits() -> None:
    assert LIMITS.max_concurrency == 1  # one JVM, one structure at a time
    assert LIMITS.max_waiting == 4  # the worker sends one batch per job, at most 2 jobs at once
    assert LIMITS.queue_timeout_s == 15.0
    assert LIMITS.max_body_bytes == 256 * 1024
    assert LIMITS.ready_cache_seconds == 30.0


def test_build_app_serves_through_a_process_namer_by_default() -> None:
    app = build_app()
    assert app.title == "decimer-chem"
    assert isinstance(app.state.namer, ProcessNamer)
