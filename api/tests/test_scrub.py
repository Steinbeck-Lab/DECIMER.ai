"""What an error report or a log line may carry: no structure, no file, no address.

Three layers: the text scrubber on its own, the `before_send` allowlist on a crafted event, and the
real Sentry SDK with a transport that keeps what would have been sent (an API request that crashes,
a log line at ERROR, with a SMILES, a molfile, an IP address and a file name put everywhere an SDK
looks).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
from fastapi import FastAPI, Request
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from decimer_api import handlers
from decimer_api.chemistry import analyse_smiles
from decimer_api.observability import init_error_reporting
from decimer_api.scrub import (
    FILE,
    IP,
    JOB_ID,
    MOLFILE_REMOVED,
    SMILES,
    scrub_event,
    scrub_text,
    scrub_transaction,
)

MOLFILE = analyse_smiles("CCO").molfile  # a real V2000 block, as RDKit writes it
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
CAFFEINE = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
CLIENT_IP = "141.35.2.10"  # a public address (University of Jena): what a client looks like
CLIENT_IPV6 = "2001:4860:4860::8888"
FILENAME = "my_secret_structure.pdf"
JOB = "7a9f7a9f7a9f7a9f7a9f7a9f7a9f7a9f"

SMILES_STRINGS = [
    ASPIRIN,
    CAFFEINE,
    "C1CC1",
    "[Na+].[Cl-]",
    "C[C@H](N)C(=O)O",
    "c1ccccc1",
    "CCCCO",
    "OC(=O)CCN",
    "C=C",
    "CCO",
    "[13CH3]C(=O)[O-]",
    "N#Cc1ccc(cc1)C(F)(F)F",
    "O=C(O)C(Cl)Br",
    "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
]
SURVIVORS = [
    "RuntimeError",
    "TypeError",
    "Failed to fetch",
    "2026-10-23",
    "v1.2.3",
    "index-abc123.js",
    "decimer-web",
    "useEffect",
    "NetworkError when attempting to fetch resource",
    "INFO started",
    "decimer_api.worker.pipeline",
    "redis.exceptions.ConnectionError: Error 111 connecting to redis:6379.",
    "ServiceUnavailable: seg unreachable (ConnectError)",
    "pid 1234 killed by SIGKILL after 30.0 s",
    "12:30:45",
    "std::vector",
    "[ip] [file] [smiles] :id [molfile removed]",
    "timed out after 30s with 25C and 5MB and 1e5 rows",
    "10s",
    "2 pages, 10 structures",
    "n=1 key=value can:be a:b x=2 id=7",
    "http://decimer.ai:8000/x",
    "ERROR INFO WARNING DEBUG",
]


@pytest.mark.parametrize("smiles", SMILES_STRINGS)
def test_a_smiles_is_removed_wherever_it_stands(smiles: str) -> None:
    assert scrub_text(smiles) == SMILES
    assert scrub_text(f"could not parse '{smiles}' because it is odd") == (
        f"could not parse '{SMILES}' because it is odd"
    )
    assert scrub_text(f"smiles={smiles} failed") == f"smiles={SMILES} failed"
    assert scrub_text(f'{{"smiles": "{smiles}"}}') == f'{{"smiles": "{SMILES}"}}'
    cleaned = scrub_text(f"bad ({smiles}).")  # the brackets and the full stop are SMILES pieces too
    assert cleaned.startswith("bad ") and SMILES in cleaned and smiles not in cleaned


@pytest.mark.parametrize("text", SURVIVORS)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert scrub_text(text) == text


# The probes of the Opus review (2026-10-23): a SMILES that is glued to punctuation, a quote of
# another kind, a key of one letter, a query string, ... Every one used to get through; the text
# around it must survive.
GLUED_SMILES = [
    "id=CCO",
    "x:CC(=O)O",
    "[SMILES:CCO]",
    "smiles=[CCO]",
    "{CCO}",
    "`CCO`",
    "\u00abCCO\u00bb",
    "\u201cCCO\u201d",
    "\u2018CCO\u2019",
    "CCO|CCN",
    "c1cc[te]cc1",
    "CC(=O)O!",
    "CC(=O)O?",
    "'c1ccccc1?'",
    "KeyError('c1ccccc1?')",
    "c1ccccc1&",
    "smiles=CCO&n=1",
    "/api/jobs/x?smiles=CCO",
    "GET /api/x?smiles=CCO HTTP/1.1",
    "host:8000/x?smiles=CCO",
    "((CCO))",
    "[[CCO]]",
    "mol:{smiles:CCO}",
    "id=CCO/id=CCO/[ip]",
    "a=CCO&b=CCN|c=OCC=O?d=CCCl",
    "http://chem:8004/names/CCO",
    "GET /api/names/CC(=O)O/x",
]


@pytest.mark.parametrize("text", GLUED_SMILES)
def test_a_smiles_glued_to_punctuation_is_removed(text: str) -> None:
    cleaned = scrub_text(text)
    for secret in ("CCO", "CC(=O)O", "c1ccccc1", "[te]", "CCN"):
        assert secret not in cleaned, (text, cleaned)


def test_a_token_with_several_smiles_in_it_loses_all_of_them_and_scrubbing_is_stable() -> None:
    cleaned = scrub_text("id=CCO/id=CCN/[ip] and key:CCCl/x=OCC=O")
    assert "CC" not in cleaned.replace("[smiles]", ""), cleaned
    assert scrub_text(cleaned) == cleaned


def test_a_chemdraw_file_name_is_a_file_name() -> None:
    for name in ("g.cdx", "h.cdxml"):
        assert scrub_text(f"cannot read {name} now") == f"cannot read {FILE} now"


def test_the_text_around_a_glued_smiles_stays() -> None:
    assert scrub_text("bad `CCO`!") == "bad `[smiles]`!"
    assert scrub_text("saw {CCO} and \u00abCCN\u00bb") == "saw {[smiles]} and \u00ab[smiles]\u00bb"
    assert scrub_text("GET /api/x?smiles=CCO HTTP/1.1") == "GET /api/x HTTP/1.1"
    assert scrub_text("really?") == "really?"


def test_a_query_string_is_cut_from_a_path_with_or_without_a_scheme() -> None:
    assert scrub_text("/api/jobs/x?name=Zou&n=1") == "/api/jobs/x"
    assert scrub_text("x/y?smiles=CCO#top") == "x/y"
    assert scrub_text("see c1ccccc1/C=C/C?") == f"see {SMILES}"  # a `/` in a SMILES is not a path


@pytest.mark.parametrize(
    "text",
    [
        "paper.pptx",
        "scan.avif",
        "scan.jfif",
        "foo.svg",
        "sheet.xlsx",
        "x.ket",
        "y.mol2",
        "z.tif",
        "a.eps",
    ],
)
def test_more_kinds_of_file_name_are_removed(text: str) -> None:
    assert scrub_text(f"cannot read {text} now") == f"cannot read {FILE} now"


@pytest.mark.parametrize(
    "quoted",
    [
        "`Zou et al 2024.pdf`",
        "\u00abZou 2024 draft.pdf\u00bb",
        "\u201cmy paper.PNG\u201d",
        "\u2018my paper.pdf\u2019",
    ],
)
def test_a_file_name_with_spaces_in_any_kind_of_quotes_is_removed(quoted: str) -> None:
    cleaned = scrub_text(f"upload {quoted} failed")
    assert cleaned == f"upload {FILE} failed", cleaned


def test_a_molfile_takes_the_whole_text_with_it() -> None:
    assert scrub_text(MOLFILE) == MOLFILE_REMOVED
    assert scrub_text(f"cannot read\n{MOLFILE}\nsorry") == MOLFILE_REMOVED
    assert scrub_text("a V3000 block follows") == MOLFILE_REMOVED
    assert scrub_text("record 1\n$$$$\nrecord 2") == MOLFILE_REMOVED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"from {CLIENT_IP} to the proxy", f"from {IP} to the proxy"),
        (f"[{CLIENT_IPV6}]:8000 refused", f"[{IP}]:8000 refused"),
        ("peer 172.29.80.2 is not trusted", f"peer {IP} is not trusted"),
        ("link-local fe80::1 and ::1", f"link-local {IP} and {IP}"),
        ("::ffff:141.35.2.10", f"{IP}:{IP}"),  # an IPv4-mapped address: both halves go
        ("2001:db8::7334: refused", f"{IP}: refused"),
        ("2001:0db8:85a3:0000:0000:8a2e:0370:7334 x", f"{IP} x"),
    ],
)
def test_an_ip_address_is_removed(text: str, expected: str) -> None:
    assert scrub_text(text) == expected


def test_the_logs_keep_the_addresses_of_the_infrastructure_and_drop_public_ones() -> None:
    text = f"{CLIENT_IP} {CLIENT_IPV6} 172.29.80.2 10.1.2.3 ::1 fe80::1"

    def clean(**kwargs: bool) -> str:
        return scrub_text(text, public_addresses_only=True, **kwargs)

    assert clean() == f"{IP} {IP} 172.29.80.2 10.1.2.3 ::1 fe80::1"
    assert scrub_text(text) == " ".join([IP] * 6)  # a report keeps no address at all


@pytest.mark.parametrize(
    "text",
    [
        f"'{FILENAME}' is damaged",
        f"file {FILENAME} is damaged",
        "'Zou et al 2024 final.pdf' is damaged",
        "File 2 'structure_scan.PNG' could not be read as an image.",
        "C:\\Users\\me\\paper.pdf",
        f"/data/jobs/{JOB}/input/document.pdf",
        "see ../../drawing.cdxml",
    ],
)
def test_a_file_name_is_removed(text: str) -> None:
    cleaned = scrub_text(text)
    assert FILE in cleaned
    for leaked in ("secret", "Zou", "structure_scan", "Users", "paper", "drawing", JOB, "document"):
        assert leaked not in cleaned, (leaked, cleaned)


def test_a_file_name_at_the_end_of_a_sentence_is_removed_too() -> None:
    assert scrub_text(f"could not read {FILENAME}.") == f"could not read {FILE}."
    assert scrub_text(f"could not read {FILENAME}. Next") == f"could not read {FILE}. Next"


def test_the_query_and_the_fragment_of_a_url_in_a_message_are_removed() -> None:
    cleaned = scrub_text(f"GET https://decimer.ai/api/jobs/x/export?smiles={ASPIRIN}#top failed")
    assert cleaned == "GET https://decimer.ai/api/jobs/x/export failed"
    assert scrub_text("fetch http://127.0.0.1:8090/a.png?smiles=CCO") == f"fetch {FILE}"


def test_a_text_cut_at_the_limit_does_not_leak_the_end_of_a_smiles() -> None:
    cleaned = scrub_text("C" * 25_000)
    assert "CCC" not in cleaned and cleaned.endswith("...[truncated]")


def test_a_job_id_is_replaced_in_a_report_and_kept_in_a_log() -> None:
    assert scrub_text(f"job {JOB} failed") == f"job {JOB_ID} failed"
    assert scrub_text(f"job {JOB} failed", job_ids=False) == f"job {JOB} failed"


def test_scrubbing_twice_changes_nothing_more() -> None:
    nasty = f"{ASPIRIN} {CLIENT_IP} '{FILENAME}' {JOB} {CLIENT_IPV6} [ip] [smiles] {CAFFEINE}"
    once = scrub_text(nasty)
    assert scrub_text(once) == once
    assert SMILES in once and IP in once and FILE in once


def test_huge_and_hostile_text_is_cut_and_stays_fast() -> None:
    started = time.monotonic()
    for text in ("a" * 19_000 + ".png", "/" * 19_000, "[C" * 9_000, "C" * 19_000, "x" * 500_000):
        assert len(scrub_text(text)) < 21_000
    assert time.monotonic() - started < 5.0  # no pattern backtracks without bound


# --- the event ------------------------------------------------------------------------------------

SECRETS = [ASPIRIN, CAFFEINE, CLIENT_IP, CLIENT_IPV6, FILENAME, JOB, "ketcher-cookie", "M  END"]


def crafted_event() -> dict[str, Any]:
    """An event as the SDK builds it, with something to hide in every place it can sit."""
    frame = {
        "filename": "decimer_api/ingest.py",
        "abs_path": f"/opt/venv/lib/{FILENAME}?q={ASPIRIN}",
        "function": "ingest",
        "module": "decimer_api.ingest",
        "lineno": 42,
        "in_app": True,
        "vars": {"smiles": ASPIRIN, "upload": FILENAME, "molfile": MOLFILE, "addr": CLIENT_IP},
        "pre_context": [f"x = '{ASPIRIN}'"],
        "context_line": f"raise ValueError(f'{CAFFEINE}')",
        "post_context": [FILENAME],
    }
    return {
        "event_id": "a" * 32,
        "timestamp": "2026-10-23T10:00:00Z",
        "platform": "python",
        "level": "error",
        "logger": "decimer_api",
        "release": "2026-10-23",
        "environment": "production",
        "server_name": "api",
        "message": f"failed on {ASPIRIN} from {CLIENT_IP}",
        "logentry": {
            "message": "job %s failed: %s",
            "formatted": f"job {JOB} failed: {CAFFEINE}",
            "params": [JOB, ASPIRIN],
        },
        "transaction": f"/api/jobs/{JOB}",
        "tags": {"service": "api", "smiles": ASPIRIN, "n": 3},
        "extra": {
            "molfile": MOLFILE,
            "file": FILENAME,
            "ip": CLIENT_IP,
            "arq-job": {"args": [JOB]},
        },
        "user": {"id": "u1", "ip_address": CLIENT_IP, "email": "me@uni-jena.de", "username": "me"},
        "breadcrumbs": {
            "values": [
                {"message": f"opened {FILENAME}", "data": {"smiles": ASPIRIN}, "category": "x"}
            ]
        },
        "modules": {"rdkit": "2026.9.1"},
        "sdk": {
            "name": "sentry.python",
            "version": "2.71.0",
            "packages": [{"name": "pypi:sentry-sdk"}],
        },
        "contexts": {
            "runtime": {"name": "CPython", "version": "3.12.15", "build": "x"},
            "os": {"name": "Linux", "kernel_version": "6.8.0", "version": 3},
            "device": {"name": "laptop"},
            "trace": {"trace_id": "t" * 32},
            "response": {"headers": {"set-cookie": "ketcher-cookie"}},
        },
        "request": {
            "url": f"https://decimer.ai/api/jobs/{JOB}/export?smiles={ASPIRIN}#frag-{FILENAME}",
            "method": "POST",
            "query_string": f"smiles={ASPIRIN}",
            "data": {"molfile": MOLFILE},
            "cookies": {"session": "ketcher-cookie"},
            "env": {"REMOTE_ADDR": CLIENT_IP, "SERVER_NAME": "api"},
            "headers": {
                "Content-Type": "multipart/form-data; boundary=x",
                "content-length": "123",
                "Cookie": "session=ketcher-cookie",
                "X-Forwarded-For": f"{CLIENT_IP}, {CLIENT_IPV6}",
                "User-Agent": f"curl on {FILENAME}",
                "Authorization": "Bearer ketcher-cookie",
                "Referer": f"https://decimer.ai/?smiles={ASPIRIN}",
            },
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": f"cannot parse '{ASPIRIN}' for {CLIENT_IP} in '{FILENAME}'",
                    "module": "builtins",
                    "mechanism": {"type": "logging", "handled": True, "data": {"x": ASPIRIN}},
                    "stacktrace": {"frames": [frame]},
                    "thread_id": 5,
                },
                {"type": "RuntimeError", "value": MOLFILE},
            ]
        },
        "threads": {"values": [{"id": 1, "name": FILENAME, "stacktrace": {"frames": [frame]}}]},
        "fingerprint": ["{{ default }}"],
        "_meta": {"request": {"data": {"": {"rem": [["x"]]}}}},
        "debug_meta": {"images": [{"code_file": FILENAME}]},
    }


def test_an_event_keeps_what_reading_a_crash_needs() -> None:
    event = scrub_event(crafted_event())
    assert event is not None
    assert event["event_id"] == "a" * 32 and event["level"] == "error"
    assert event["release"] == "2026-10-23" and event["environment"] == "production"
    assert event["server_name"] == "api" and event["logger"] == "decimer_api"
    assert event["fingerprint"] == ["{{ default }}"]
    assert event["sdk"] == {"name": "sentry.python", "version": "2.71.0"}
    assert event["contexts"] == {
        "runtime": {"name": "CPython", "version": "3.12.15", "build": "x"},
        "os": {"name": "Linux", "kernel_version": "6.8.0"},
    }
    first, second = event["exception"]["values"]
    assert first["type"] == "ValueError" and first["module"] == "builtins"
    assert first["mechanism"] == {"type": "logging", "handled": True}
    assert first["value"] == f"cannot parse '{SMILES}' for {IP} in {FILE}"
    assert second == {"type": "RuntimeError", "value": MOLFILE_REMOVED}
    (frame,) = first["stacktrace"]["frames"]
    assert frame["function"] == "ingest" and frame["lineno"] == 42 and frame["in_app"] is True
    assert frame["module"] == "decimer_api.ingest" and frame["filename"] == "decimer_api/ingest.py"
    assert set(frame) <= {"filename", "abs_path", "function", "module", "lineno", "colno", "in_app"}
    assert event["threads"]["values"][0]["stacktrace"]["frames"][0]["function"] == "ingest"
    assert event["request"]["method"] == "POST"
    assert event["request"]["headers"] == {
        "Content-Type": "multipart/form-data; boundary=x",
        "content-length": "123",
    }
    assert event["request"]["url"] == f"https://decimer.ai/api/jobs/{JOB_ID}/export"
    assert event["transaction"] == f"/api/jobs/{JOB_ID}"
    assert event["logentry"] == {"message": f"job {JOB_ID} failed: {SMILES}"}
    assert event["message"] == f"failed on {SMILES} from {IP}"
    assert event["tags"] == {"service": "api", "smiles": SMILES, "n": "3"}


def test_nothing_secret_is_anywhere_in_the_event() -> None:
    event = scrub_event(crafted_event())
    assert event is not None
    text = json.dumps(event)
    for secret in SECRETS:
        assert secret not in text, secret


@pytest.mark.parametrize(
    "dropped",
    ["extra", "user", "breadcrumbs", "modules", "_meta", "debug_meta"],
)
def test_a_field_that_is_not_on_the_allowlist_is_gone(dropped: str) -> None:
    event = scrub_event(crafted_event())
    assert event is not None and dropped not in event


def test_a_field_an_sdk_adds_tomorrow_is_not_sent() -> None:
    crafted = crafted_event() | {"new_field": {"smiles": ASPIRIN}, "spans": [{"op": "x"}]}
    event = scrub_event(crafted)
    assert event is not None and "new_field" not in event and "spans" not in event


def test_a_transaction_is_never_sent() -> None:
    """Performance data is off; if an SDK ever made one, none of it leaves."""
    assert (
        scrub_transaction({"type": "transaction", "transaction": "/api/jobs/x", "spans": []})
        is None
    )


def test_an_event_that_cannot_be_read_is_not_sent() -> None:
    assert (
        scrub_event({"exception": {"values": 5}}) is None
    )  # a bug in the scrubber drops the report


# --- the real SDK ---------------------------------------------------------------------------------


class Capture(Transport):
    """What would have been sent: the events, and the serialised envelopes they travel in."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        super().__init__(options)
        self.sent: list[bytes] = []
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.sent.append(envelope.serialize())
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))

    def payload(self) -> str:
        return b"".join(self.sent).decode()


@pytest.fixture
def transport() -> Iterator[Capture]:
    capture = Capture()
    yield capture
    sentry_sdk.get_client().close(timeout=1)
    sentry_sdk.init(dsn=None, default_integrations=False, auto_enabling_integrations=False)


def start(transport: Capture, service: str = "api") -> None:
    started = init_error_reporting(
        "http://publickey@glitchtip:8000/1",
        service=service,
        release="test",
        transport=transport,
    )
    assert started is True


def test_reporting_is_off_without_a_dsn() -> None:
    assert init_error_reporting(None, service="api") is False
    assert init_error_reporting("", service="api") is False
    assert not sentry_sdk.get_client().is_active()


@pytest.mark.parametrize(
    "dsn",
    [
        "glitchtip",
        "ftp://k@host/1",
        "http://host/1",
        "http://k@host/x",
        "http://k@/1",
        "http://k@h:99999999/1",
    ],
)
def test_a_bad_dsn_is_ignored_with_a_warning_not_a_crash(
    dsn: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert init_error_reporting(dsn, service="api") is False
    assert not sentry_sdk.get_client().is_active()
    assert any("not a valid DSN" in record.getMessage() for record in caplog.records)


def test_the_sdk_is_started_without_anything_that_collects(transport: Capture) -> None:
    start(transport)
    options = sentry_sdk.get_client().options
    assert options["send_default_pii"] is False
    assert options["max_request_body_size"] == "never"
    assert options["include_local_variables"] is False
    assert options["include_source_context"] is False
    assert options["max_breadcrumbs"] == 0
    assert options["default_integrations"] is False
    assert options["auto_enabling_integrations"] is False
    assert options["auto_session_tracking"] is False
    assert options["send_client_reports"] is False
    assert options["traces_sample_rate"] == 0
    assert options["enable_logs"] is False and options["enable_metrics"] is False
    assert options["before_send"] is scrub_event
    assert options["before_send_transaction"] is scrub_transaction
    assert options["server_name"] == "api"
    names = set(sentry_sdk.get_client().integrations)
    logging_integration = sentry_sdk.get_client().integrations["logging"]
    assert logging_integration._breadcrumb_handler is None  # noqa: SLF001  (no log line becomes a breadcrumb)
    assert logging_integration._handler is not None  # noqa: SLF001  (and an ERROR becomes an event)
    assert {"fastapi", "starlette", "logging", "dedupe"} <= names
    assert "stdlib" not in names and "argv" not in names and "modules" not in names


def test_the_worker_has_arq_and_not_the_web_integrations(transport: Capture) -> None:
    start(transport, service="worker")
    names = set(sentry_sdk.get_client().integrations)
    assert "arq" in names and "logging" in names
    assert "fastapi" not in names and "starlette" not in names


def test_a_logged_error_reaches_the_transport_scrubbed(transport: Capture) -> None:
    import logging

    start(transport)
    try:
        raise ValueError(f"cannot parse '{ASPIRIN}' from {CLIENT_IP} in '{FILENAME}'")
    except ValueError:
        logging.getLogger("decimer_api").exception("job %s failed on %s", JOB, CAFFEINE)
    sentry_sdk.flush(timeout=2)
    payload = transport.payload()
    assert "ValueError" in payload and "decimer_api" in payload
    for secret in (ASPIRIN, CAFFEINE, CLIENT_IP, FILENAME, JOB):
        assert secret not in payload, secret
    assert SMILES in payload and IP in payload and FILE in payload


def crashing_app() -> FastAPI:
    app = FastAPI()
    handlers.register(app)  # the real 500 handler: it logs the error and answers in our shape

    @app.post("/api/jobs/{job_id}/export")
    async def export(job_id: str, request: Request) -> dict[str, str]:
        smiles = ASPIRIN  # a local variable the SDK would send with the frame, if it were asked to
        body = await request.body()
        raise ValueError(
            f"cannot read {smiles} from {CLIENT_IP} in '{FILENAME}' ({len(body)} bytes)"
        )

    return app


async def test_a_crashing_request_reaches_the_transport_with_no_trace_of_the_request(
    transport: Capture,
) -> None:
    start(transport)
    headers = {
        "Cookie": "session=ketcher-cookie",
        "X-Forwarded-For": f"{CLIENT_IP}, {CLIENT_IPV6}",
        "User-Agent": f"curl on {FILENAME}",
        "Content-Type": "text/plain",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=crashing_app(), client=(CLIENT_IP, 4711), raise_app_exceptions=False
        ),
        base_url="https://decimer.ai",
    ) as client:
        response = await client.post(
            f"/api/jobs/{JOB}/export?smiles={ASPIRIN}", content=MOLFILE, headers=headers
        )
    assert response.status_code == 500
    sentry_sdk.flush(timeout=2)
    payload = transport.payload()
    assert "ValueError" in payload and "export" in payload  # the crash and the function it was in
    for secret in SECRETS + ["curl", "smiles="]:
        assert secret not in payload, secret
    assert transport.events
    for event in transport.events:
        assert "user" not in event and "extra" not in event and "breadcrumbs" not in event
        assert set(event.get("request", {})) <= {"method", "url", "headers"}
        assert set(event.get("request", {}).get("headers", {})) <= {
            "content-type",
            "content-length",
        }
        assert event["server_name"] == "api"
    requests = [event["request"] for event in transport.events if "request" in event]
    assert requests, "the request's method and path are what the report is for"
    assert {request["method"] for request in requests} == {"POST"}
    assert {request["url"] for request in requests} == {
        f"https://decimer.ai/api/jobs/{JOB_ID}/export"
    }
