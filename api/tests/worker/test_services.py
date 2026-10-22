"""The HTTP layer to the model services: retries, backoff, concurrency, response checking."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from decimer_api.worker.errors import (
    ServiceFailed,
    ServiceRejected,
    ServiceTimedOut,
    ServiceUnavailable,
)
from decimer_api.worker.services import Services, make_services
from decimer_api.worker.settings import WorkerSettings
from tests.worker.fakes import FakeBackend, FakeSleep, json_response, make_crop, no_dns

PNG = make_crop(0, 0)


def page_png(size: tuple[int, int] = (600, 600)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


Build = Callable[..., Services]


@pytest.fixture
def sleep() -> FakeSleep:
    return FakeSleep()


@pytest.fixture
async def build(sleep: FakeSleep) -> AsyncIterator[Build]:
    clients: list[httpx.AsyncClient] = []

    def factory(backend: FakeBackend, **overrides: object) -> Services:
        values: dict[str, object] = {
            "seg_url": "http://seg.test",
            "ocsr_url": "http://ocsr.test",
            "cls_url": "http://cls.test",
            "chem_url": "http://chem.test",
        }
        settings = WorkerSettings(**{**values, **overrides})  # type: ignore[arg-type]
        client = httpx.AsyncClient(transport=backend.transport)
        clients.append(client)
        return make_services(client, settings, sleep=sleep, resolver=no_dns)

    yield factory
    for client in clients:
        await client.aclose()


def busy(retry_after: str | None = "2") -> httpx.Response:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(503, json={"detail": "busy"}, headers=headers)


def fail_times(
    times: int, response: Callable[[], httpx.Response]
) -> Callable[..., httpx.Response | None]:
    """A hook that answers `response()` for the first `times` calls, then lets the fake answer."""
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        return response() if seen["n"] <= times else None

    return hook


# --- what the calls send and what they return ------------------------------------------------


async def test_segment_sends_the_page_and_decodes_the_crops(build: Build) -> None:
    backend = FakeBackend(layout={2: 2})
    page = page_png()
    answer = await build(backend).segment(page, 2)
    assert backend.parts_of("seg")[0]["page_index"] == b"2"
    assert backend.parts_of("seg")[0]["image"] == page
    assert (answer.width, answer.height) == (600, 600)
    assert [s.index for s in answer.segments] == [0, 1]
    assert answer.segments[1].png == make_crop(2, 1)  # base64 decoded back to the PNG bytes
    assert answer.segments[1].bbox == (110, 20, 151, 52)


async def test_recognise_classify_and_name(build: Build) -> None:
    backend = FakeBackend(names={"CC": ("ethane", "pin_verified")})
    services = build(backend)
    assert (await services.recognise(make_crop(0, 0))).smiles == "CC"
    assert (await services.classify(PNG)).is_structure is True
    answers = await services.names(["CC", "CCC"])
    assert [(a.name, a.tier, a.reason) for a in answers] == [
        ("ethane", "pin_verified", None),
        (None, None, "no_verified_name"),
    ]


async def test_a_trailing_slash_in_the_base_url_is_harmless(build: Build) -> None:
    backend = FakeBackend()
    await build(backend, ocsr_url="http://ocsr.test/").recognise(PNG)
    assert backend.count("ocsr") == 1


@pytest.mark.parametrize(("given", "expected"), [(1.7, 1.0), (-0.2, 0.0), (0.42, 0.42)])
async def test_confidence_is_clamped_to_zero_one(
    build: Build, given: float, expected: float
) -> None:
    backend = FakeBackend(
        hooks={
            "ocsr": lambda *_: json_response(
                {"smiles": "C", "token_confidence": [], "mean_confidence": given}
            )
        }
    )
    assert (await build(backend).recognise(PNG)).confidence == expected


async def test_chem_is_disabled_without_a_url(build: Build) -> None:
    services = build(FakeBackend(), chem_url="")
    assert services.chem_enabled is False


# --- 503: busy ------------------------------------------------------------------------------


async def test_a_503_is_retried_after_the_time_the_service_names(
    build: Build, sleep: FakeSleep
) -> None:
    backend = FakeBackend(hooks={"ocsr": fail_times(1, lambda: busy("2"))})
    answer = await build(backend).recognise(make_crop(0, 0))
    assert answer.smiles == "CC"
    assert sleep.delays == [2.0]
    assert backend.count("ocsr") == 2


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 5.0),
        ("soon", 5.0),
        ("-3", 0.0),
        ("3600", 30.0),
        ("0.5", 0.5),
        ("inf", 30.0),
        ("nan", 5.0),
    ],
)
async def test_retry_after_is_defaulted_and_capped(
    build: Build, sleep: FakeSleep, header: str | None, expected: float
) -> None:
    backend = FakeBackend(hooks={"cls": fail_times(1, lambda: busy(header))})
    await build(backend).classify(PNG)
    assert sleep.delays == [expected]


async def test_a_503_that_never_ends_gives_up_after_the_retry_budget(
    build: Build, sleep: FakeSleep
) -> None:
    backend = FakeBackend(hooks={"seg": lambda *_: busy("1")})
    with pytest.raises(ServiceUnavailable):
        await build(backend, busy_retries=2).segment(PNG, 0)
    assert backend.count("seg") == 3  # the first try and two retries
    assert sleep.delays == [1.0, 1.0]


# --- 422 and 500: no retry -------------------------------------------------------------------


@pytest.mark.parametrize("status", [422, 413])
async def test_a_refused_input_is_not_retried(build: Build, sleep: FakeSleep, status: int) -> None:
    backend = FakeBackend(
        hooks={"ocsr": lambda *_: httpx.Response(status, json={"detail": "image too elongated"})}
    )
    with pytest.raises(ServiceRejected, match="elongated"):
        await build(backend).recognise(PNG)
    assert backend.count("ocsr") == 1
    assert sleep.delays == []


@pytest.mark.parametrize("status", [500, 502, 400, 404])
async def test_a_model_error_is_not_retried(build: Build, sleep: FakeSleep, status: int) -> None:
    backend = FakeBackend(
        hooks={"ocsr": lambda *_: httpx.Response(status, json={"detail": "inference failed"})}
    )
    with pytest.raises(ServiceFailed):
        await build(backend).recognise(PNG)
    assert backend.count("ocsr") == 1
    assert sleep.delays == []


# --- connection errors ---------------------------------------------------------------------


def refuse(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
    raise httpx.ConnectError("connection refused")


async def test_connection_errors_are_retried_with_doubling_backoff(
    build: Build, sleep: FakeSleep
) -> None:
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if seen["n"] <= 3:
            raise httpx.ConnectError("connection refused")
        return None

    backend = FakeBackend(hooks={"ocsr": hook})
    assert (await build(backend).recognise(make_crop(0, 0))).smiles == "CC"
    assert sleep.delays == [0.5, 1.0, 2.0]


async def test_the_backoff_covers_a_cold_start_of_about_a_minute(
    build: Build, sleep: FakeSleep
) -> None:
    """ocsr needs about 50 s to load; a worker that finds it down keeps trying for that long.
    The delays double up to a cap, so the probing stays steady."""
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if sum(sleep.delays) < 55:  # the service answers once 55 s have passed
            raise httpx.ConnectError("connection refused")
        return None

    backend = FakeBackend(hooks={"ocsr": hook})
    assert (await build(backend).recognise(make_crop(0, 0))).smiles == "CC"
    assert sleep.delays[:6] == [0.5, 1.0, 2.0, 4.0, 8.0, 10.0]
    assert max(sleep.delays) == 10.0
    assert 55 <= sum(sleep.delays) <= 66


async def test_a_service_that_stays_down_ends_as_unavailable_after_the_budget(
    build: Build, sleep: FakeSleep
) -> None:
    backend = FakeBackend(hooks={"cls": refuse})
    with pytest.raises(ServiceUnavailable):
        await build(backend, connect_retry_budget_seconds=3.5).classify(PNG)
    assert sleep.delays == [0.5, 1.0, 2.0]
    assert backend.count("cls") == 4


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")])
async def test_both_kinds_of_could_not_connect_use_the_budget(
    build: Build, sleep: FakeSleep, error: Exception
) -> None:
    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        raise error

    with pytest.raises(ServiceUnavailable):
        await build(FakeBackend(hooks={"cls": hook}), connect_retry_budget_seconds=1.5).classify(
            PNG
        )
    assert sleep.delays == [0.5, 1.0]


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadError("reset"),
        httpx.RemoteProtocolError("server closed the connection"),
        httpx.WriteError("broken pipe"),
    ],
)
async def test_a_connection_lost_after_the_request_went_out_is_retried_once(
    build: Build, sleep: FakeSleep, error: Exception
) -> None:
    """The service may have started the work (or crashed on this very input): one more try, for
    a stale keep-alive connection, and no more. It is then a failure of this call, not of the
    service as a whole."""

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        raise error

    backend = FakeBackend(hooks={"ocsr": hook})
    with pytest.raises(ServiceFailed, match="lost"):
        await build(backend).recognise(PNG)
    assert backend.count("ocsr") == 2
    assert len(sleep.delays) == 1


async def test_one_dropped_connection_followed_by_success_is_invisible(build: Build) -> None:
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if seen["n"] == 1:
            raise httpx.RemoteProtocolError("server closed the connection")
        return None

    assert (
        await build(FakeBackend(hooks={"ocsr": hook})).recognise(make_crop(0, 0))
    ).smiles == "CC"


async def test_a_service_that_dies_mid_request_and_restarts_is_waited_for(
    build: Build, sleep: FakeSleep
) -> None:
    """Reset once (the retry), then refused while it restarts (the budget), then it answers."""
    seen = {"n": 0}

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        if seen["n"] == 1:
            raise httpx.ReadError("reset")
        if seen["n"] <= 4:
            raise httpx.ConnectError("connection refused")
        return None

    assert (
        await build(FakeBackend(hooks={"ocsr": hook})).recognise(make_crop(0, 0))
    ).smiles == "CC"
    assert len(sleep.delays) == 4


async def test_a_read_timeout_is_a_timeout_and_is_not_retried(
    build: Build, sleep: FakeSleep
) -> None:
    """A slow model gets no second chance: a retry would only add to its queue."""

    def hook(_request: httpx.Request, _parts: dict[str, bytes]) -> httpx.Response | None:
        raise httpx.ReadTimeout("too slow")

    backend = FakeBackend(hooks={"ocsr": hook})
    with pytest.raises(ServiceTimedOut, match="timed out"):
        await build(backend).recognise(PNG)
    assert backend.count("ocsr") == 1
    assert sleep.delays == []


def test_a_timeout_is_a_kind_of_failure() -> None:
    assert issubclass(ServiceTimedOut, ServiceFailed)


# --- answers the worker cannot use ------------------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"smiles": 5}),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, json={"smiles": "C"}),  # no confidence
    ],
)
async def test_a_malformed_answer_is_a_failure_not_a_crash(
    build: Build, response: httpx.Response
) -> None:
    backend = FakeBackend(hooks={"ocsr": lambda *_: response})
    with pytest.raises(ServiceFailed):
        await build(backend).recognise(PNG)


def seg_answering(payload: dict[str, object]) -> dict[str, Callable[..., httpx.Response]]:
    return {"seg": lambda *_: json_response(payload)}


def one_segment(png: str, bbox: list[int]) -> dict[str, object]:
    return {"index": 0, "bbox": bbox, "png": png}


@pytest.mark.parametrize(
    "png",
    ["!!!not base64!!!", base64.b64encode(b"GIF89a....").decode(), ""],
)
async def test_a_segment_that_is_not_a_png_is_a_failure(build: Build, png: str) -> None:
    payload = {
        "page_index": 0,
        "width": 9,
        "height": 9,
        "segments": [one_segment(png, [0, 0, 5, 5])],
    }
    with pytest.raises(ServiceFailed):
        await build(FakeBackend(hooks=seg_answering(payload))).segment(page_png((9, 9)), 0)


@pytest.mark.parametrize(
    "bbox",
    [
        [5, 5, 5, 9],
        [0, 0, 9],
        [9, 0, 2, 5],
        [-1, 0, 4, 4],
        [0, 0, 10, 5],
        [0, 0, 5, 10],
        [20, 20, 30, 30],
    ],
)
async def test_a_segment_with_a_nonsense_box_is_a_failure(build: Build, bbox: list[int]) -> None:
    """Including a box that reaches past the page: x1 <= width and y1 <= height."""
    crop = base64.b64encode(make_crop(0, 0)).decode()
    payload = {"page_index": 0, "width": 9, "height": 9, "segments": [one_segment(crop, bbox)]}
    with pytest.raises(ServiceFailed):
        await build(FakeBackend(hooks=seg_answering(payload))).segment(page_png((9, 9)), 0)


async def test_a_box_that_touches_the_page_edge_is_fine(build: Build) -> None:
    crop = base64.b64encode(make_crop(0, 0)).decode()
    payload = {
        "page_index": 0,
        "width": 9,
        "height": 9,
        "segments": [one_segment(crop, [0, 0, 9, 9])],
    }
    answer = await build(FakeBackend(hooks=seg_answering(payload))).segment(page_png((9, 9)), 0)
    assert answer.segments[0].bbox == (0, 0, 9, 9)


@pytest.mark.parametrize(
    ("page_index", "width", "height"),
    [(1, 9, 9), (0, 10, 9), (0, 9, 8), (0, 0, 0)],
)
async def test_seg_must_echo_the_page_index_and_the_size_it_was_sent(
    build: Build, page_index: int, width: int, height: int
) -> None:
    """The boxes are only meaningful for the image that was sent: an answer about another page
    or another size would put crops in the wrong places."""
    payload = {"page_index": page_index, "width": width, "height": height, "segments": []}
    with pytest.raises(ServiceFailed, match="echo|page|size"):
        await build(FakeBackend(hooks=seg_answering(payload))).segment(page_png((9, 9)), 0)


# --- the size of an answer -----------------------------------------------------------------------


async def test_an_answer_over_the_limit_is_refused_while_it_streams(build: Build) -> None:
    """Never hold more than the limit: the body is read in pieces and dropped at the limit."""
    pieces = {"read": 0}

    async def body() -> AsyncIterator[bytes]:
        for _ in range(1000):
            pieces["read"] += 1
            yield b"x" * 4096

    def hook(*_: object) -> httpx.Response:
        return httpx.Response(200, content=body())

    backend = FakeBackend(hooks={"ocsr": hook})
    with pytest.raises(ServiceFailed, match="too large"):
        await build(backend, small_response_limit_kb=16).recognise(PNG)
    assert pieces["read"] < 10  # stopped long before the 4 MB were read


async def test_a_declared_length_over_the_limit_is_refused_before_reading(build: Build) -> None:
    def hook(*_: object) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"Content-Length": "99999999"})

    with pytest.raises(ServiceFailed, match="too large"):
        await build(FakeBackend(hooks={"cls": hook}), small_response_limit_kb=16).classify(PNG)


async def test_seg_may_answer_with_more_than_the_small_services(build: Build) -> None:
    """Crops come back as base64 PNGs, so seg's limit is much higher than the others'."""
    backend = FakeBackend(layout={0: 3})
    answer = await build(backend, small_response_limit_kb=1).segment(page_png(), 0)
    assert len(answer.segments) == 3


async def test_the_limit_for_seg_is_its_own_setting(build: Build) -> None:
    backend = FakeBackend(layout={0: 3})
    with pytest.raises(ServiceFailed, match="too large"):
        await build(backend, seg_response_limit_mb=0.0001).segment(page_png(), 0)


# --- the batch of names --------------------------------------------------------------------------


async def test_names_are_asked_for_in_one_batch_and_come_back_in_order(build: Build) -> None:
    backend = FakeBackend(names={"CCC": ("propane", "pin_verified")}, reasons={"CC": "timeout"})
    answers = await build(backend).names(["CC", "CCC", "CCCC"])
    assert [(a.name, a.reason) for a in answers] == [
        (None, "timeout"),
        ("propane", None),
        (None, "no_verified_name"),
    ]
    assert backend.count("chem") == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"results": []},  # one answer too few
        {"results": [{"name": "x", "tier": "t", "verified": None, "reason": None}] * 3},  # too many
        {"results": "nope"},
        {"name": "ethane"},  # the single-structure shape
    ],
)
async def test_a_batch_answer_that_does_not_line_up_is_a_failure(
    build: Build, payload: dict[str, object]
) -> None:
    backend = FakeBackend(hooks={"chem": lambda *_: json_response(payload)})
    with pytest.raises(ServiceFailed):
        await build(backend).names(["CC", "CCC"])


# --- concurrency -----------------------------------------------------------------------------


async def test_calls_to_one_service_never_exceed_its_limit(build: Build) -> None:
    backend = FakeBackend(delay=0.01)
    services = build(backend, ocsr_concurrency=2)
    await asyncio.gather(*(services.recognise(make_crop(0, i)) for i in range(8)))
    assert backend.peak["ocsr"] == 2


async def test_the_limit_is_per_service(build: Build) -> None:
    """A busy ocsr does not hold up cls."""
    backend = FakeBackend(delay=0.01)
    services = build(backend, ocsr_concurrency=1, cls_concurrency=3)
    await asyncio.gather(
        *(services.recognise(PNG) for _ in range(3)), *(services.classify(PNG) for _ in range(3))
    )
    assert backend.peak["ocsr"] == 1
    assert backend.peak["cls"] == 3


async def test_a_call_that_is_waiting_to_retry_keeps_its_place(build: Build) -> None:
    """While a call waits out a 503 the other callers wait too: a busy service is not hammered."""
    seen = {"n": 0}

    def hook(_request: httpx.Request, parts: dict[str, bytes]) -> httpx.Response | None:
        seen["n"] += 1
        return busy("1") if seen["n"] == 1 else None

    backend = FakeBackend(hooks={"ocsr": hook})
    services = build(backend, ocsr_concurrency=1)
    await asyncio.gather(services.recognise(make_crop(0, 0)), services.recognise(make_crop(0, 1)))
    from tests.worker.fakes import crop_id

    order = [crop_id(parts["image"])[1] for parts in backend.parts_of("ocsr")]
    assert order == [0, 0, 1]
