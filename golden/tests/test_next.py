"""The `next` run against a fake API: what it sends, how it waits, what it writes."""

from __future__ import annotations

import email.utils
import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fake_api import (
    BENZENE,
    CAFFEINE,
    JOB_ID,
    PNG,
    Clock,
    FakeApi,
    error_json,
    job_json,
    lifecycle,
    result_json,
    stages,
)

import decimer_golden as golden
from decimer_golden import next_stack
from decimer_golden.docker_memory import MemorySampler
from decimer_golden.score import Label, read_predictions, score, to_inchikey

OPENAPI = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "openapi.json"


def pdf_file(tmp_path: Path) -> Path:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nnot a real paper\n")
    return pdf


def run_with(
    api: FakeApi, tmp_path: Path, clock: Clock | None = None, **kwargs: Any
) -> next_stack.NextRun:
    clock = clock or Clock()
    with api.client() as client:
        return next_stack.run(
            client, pdf_file(tmp_path), clock=clock, sleep=clock.sleep, **{"poll": 2.0, **kwargs}
        )


def done_job(**kwargs: Any) -> dict[str, Any]:
    results = [
        result_json(0, CAFFEINE, page_index=2, name="caffeine", name_tier="pin_verified"),
        result_json(1, BENZENE, page_index=3, is_structure=False, confidence=0.5),
        result_json(2, "", page_index=3, is_structure=None, confidence=0.0),  # recognition failed
        result_json(3, "CCO", page_index=6),  # valid but not in the labels
    ]
    return job_json("done", results=results, dropped=2, found=6, **kwargs)


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The runs directory of a CLI run; the fake API is installed by `serve`."""
    monkeypatch.setattr(golden, "RUNS", tmp_path / "runs")
    return tmp_path / "runs"


def serve(monkeypatch: pytest.MonkeyPatch, api: FakeApi) -> None:
    monkeypatch.setattr(next_stack, "new_client", lambda origin: api.client())


def only_run(runs: Path) -> Path:
    (run_dir,) = runs.iterdir()
    return run_dir


# --- the happy path --------------------------------------------------------------------------


def test_cli_writes_a_run_that_scores_like_a_legacy_run(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = FakeApi(lifecycle(done_job()))
    serve(monkeypatch, api)
    status = golden.main(
        ["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0", "--timeout", "5"]
    )
    assert status == 0
    run_dir = only_run(cli)
    assert re.fullmatch(r"next-\d{8}-\d{6}", run_dir.name)

    predictions = read_predictions(run_dir / "predictions.csv")
    assert [p.smiles for p in predictions] == [CAFFEINE, BENZENE, "", "CCO", "", ""]
    first, second, failed, extra, cut, _ = predictions
    assert first.inchikey == to_inchikey(CAFFEINE)
    assert (first.page, first.valid, first.confidence) == (3, True, 0.9)
    assert (first.name, first.name_tier) == ("caffeine", "pin_verified")
    assert (first.classifier, second.classifier, failed.classifier) == ("True", "False", "")
    assert first.segment == "crops/00_crop-0.png"
    assert (run_dir / first.segment).read_bytes() == PNG
    assert (failed.valid, failed.inchikey) == (False, None)
    assert (cut.segment, cut.valid) == ("", None)  # the rows for what the cap cut

    # Scored like a `legacy` run: caffeine and benzene match (the classifier's "not a structure"
    # does not matter to scoring, as for the earlier version), the failed recognition is a refusal
    # (not an invalid SMILES), CCO is an extra, and the two structures the cap cut count as dropped.
    labels = [
        Label("1", 3, to_inchikey(CAFFEINE) or "", CAFFEINE, True),
        Label("2", 4, to_inchikey(BENZENE) or "", BENZENE, True),
    ]
    s = score(labels, predictions)
    assert (s.correct, s.extra, s.invalid, s.refused, s.dropped) == (2, 1, 0, 1, 2)

    # The upload is the multipart form the web app sends.
    (post,) = [r for r in api.requests if r.method == "POST"]
    assert post.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="paper.pdf"' in post.content
    assert b"not a real paper" in post.content
    assert [r.url.path for r in api.requests if "media" in r.url.path] == [
        f"/api/jobs/{JOB_ID}/media/crop-{i}.png" for i in range(4)
    ]
    assert "4 returned" in capsys.readouterr().out


def test_cli_run_json_records_the_job(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = done_job(warnings=[("structures_truncated", "6 structures were found.")])
    serve(monkeypatch, FakeApi(lifecycle(final)))
    assert golden.main(["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0"]) == 0
    meta = json.loads((only_run(cli) / "run.json").read_text())
    assert meta["stack"] == "next"
    assert meta["status"] == "done"
    assert meta["failure"] is None
    assert meta["job_id"] == JOB_ID
    assert meta["pages"] == meta["pages_processed"] == meta["page_images"] == 10
    assert (meta["structures_found"], meta["structures_dropped"], meta["results"]) == (6, 2, 4)
    assert meta["segments"] == 6
    assert meta["truncated"] is True
    assert meta["warnings"] == [
        {"code": "structures_truncated", "message": "6 structures were found."}
    ]
    assert meta["stages"]["check"] == "done"
    assert meta["polls"] == 4
    assert meta["pdf"] == "paper.pdf"
    assert len(meta["pdf_sha256"]) == 64
    assert {"upload", "processing", "total", "per_page", "crops"} <= set(meta["seconds"])
    assert meta["memory"] is None


def test_waits_until_done_and_times_the_run(tmp_path: Path) -> None:
    api = FakeApi(lifecycle(done_job()))
    clock = Clock()
    out = run_with(api, tmp_path, clock, poll=2.0)
    assert out.failure is None
    assert out.job is not None and out.job.status == "done"
    assert len(out.job.results) == 4  # the results are in the last answer only
    assert out.polls == 4
    assert clock.sleeps == [2.0] * 4
    assert out.seconds == {"upload": 0.0, "processing": 8.0, "total": 8.0}
    assert out.status_seen == {"queued": 0.0, "running": 2.0, "done": 8.0}
    assert out.stage_seen["render"] == {"active": 2.0, "done": 4.0}
    assert out.stage_seen["recognise"] == {"active": 6.0, "done": 8.0}
    assert out.stage_seconds() == {"render": 2.0, "segment": 2.0, "recognise": 2.0, "check": 0.0}
    assert [(t["t"], t["status"]) for t in out.trace] == [
        (0.0, "queued"),
        (2.0, "running"),
        (4.0, "running"),
        (6.0, "running"),
        (8.0, "done"),
    ]
    assert out.trace[0]["queue_position"] == 1


def test_started_at_is_when_the_run_began_not_when_run_json_was_written(tmp_path: Path) -> None:
    clock = Clock()
    began = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    t0 = clock.now
    # A wall clock that moves with the fake one, which moves only while the run sleeps.
    wall = SimpleNamespace(now=lambda tz=None: began + timedelta(seconds=clock.now - t0))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(next_stack, "datetime", wall)
        out = run_with(FakeApi(lifecycle(done_job())), tmp_path, clock, poll=2.0)
        assert clock.now - t0 == 8.0  # the run took 8 s, so "now" is no longer the start
        assert out.job is not None
        predictions = next_stack.to_predictions(out.job)
        meta = next_stack.build_meta(
            out, predictions, pdf=pdf_file(tmp_path), poll=2.0, timeout=900.0
        )
    assert meta["started_at"] == "2026-10-22T12:00:00+00:00"


def test_polls_until_the_job_is_finished_however_long_it_takes(tmp_path: Path) -> None:
    states = [job_json("queued", done=0, active=False)] + [
        job_json("running", done=1) for _ in range(30)
    ]
    states.append(done_job())
    out = run_with(FakeApi(states), tmp_path, poll=1.0, timeout=900)
    assert out.polls == 31
    assert out.finished


# --- failures ---------------------------------------------------------------------------------


def test_a_failed_job_exits_non_zero_with_run_json(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = job_json(
        "failed",
        done=1,
        active=False,
        error="The service is busy.",
        warnings=[
            ("page_render_failed", "Page 2 could not be rendered."),
            ("service_unavailable", "The service is busy."),
        ],
    )
    serve(monkeypatch, FakeApi([lifecycle(failed)[0], failed]))
    status = golden.main(
        ["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0", "--timeout", "5"]
    )
    assert status == 1
    run_dir = only_run(cli)
    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["status"] == "failed"
    assert meta["failure"] == {
        "kind": "job_failed",
        "message": "The service is busy.",
        "http_status": None,
        "code": "service_unavailable",
    }
    assert meta["job_id"] == JOB_ID
    assert [w["code"] for w in meta["warnings"]] == ["page_render_failed", "service_unavailable"]
    assert meta["seconds"]["total"] >= 0
    assert not (run_dir / "predictions.csv").exists()
    assert "FAILED (job_failed)" in capsys.readouterr().err


def test_upload_refusals_are_reported_with_the_api_code(tmp_path: Path) -> None:
    refusal = httpx.Response(415, json=error_json("unsupported", "Not a PDF."))
    out = run_with(FakeApi([job_json("queued")], post_replies=[refusal]), tmp_path)
    assert out.failure is not None
    assert (out.failure.kind, out.failure.http_status, out.failure.code) == (
        "upload_rejected",
        415,
        "unsupported",
    )
    assert "Not a PDF." in out.failure.message
    assert out.job_id is None


def test_an_unreachable_api_is_not_retried_on_upload(tmp_path: Path) -> None:
    api = FakeApi([job_json("queued")], post_replies=[httpx.ConnectError("refused")])
    out = run_with(api, tmp_path)
    assert out.failure is not None and out.failure.kind == "unreachable"
    assert api.paths("POST") == ["http://api.test/api/jobs"]  # never a second job


def test_network_errors_while_polling_are_ridden_out_up_to_a_limit(tmp_path: Path) -> None:
    flaky = [httpx.ReadTimeout("slow"), httpx.ConnectError("reset")]
    out = run_with(FakeApi(lifecycle(done_job()), get_replies=flaky), tmp_path)
    assert out.finished

    api = FakeApi(lifecycle(done_job()), get_replies=[httpx.ConnectError("down")] * 5)
    out = run_with(api, tmp_path)
    assert out.failure is not None and out.failure.kind == "unreachable"
    assert out.job_id == JOB_ID  # the job exists; the run says which


def test_an_expired_job_and_a_garbled_answer_are_failures(tmp_path: Path) -> None:
    gone = httpx.Response(404, json=error_json("not_found", "Not found. The job may have expired."))
    out = run_with(FakeApi(lifecycle(done_job()), get_replies=[gone]), tmp_path)
    assert out.failure is not None
    assert (out.failure.kind, out.failure.http_status, out.failure.code) == (
        "http_error",
        404,
        "not_found",
    )

    html = httpx.Response(200, text="<html>proxy error</html>")
    out = run_with(FakeApi(lifecycle(done_job()), get_replies=[html]), tmp_path)
    assert out.failure is not None and out.failure.kind == "bad_response"

    unknown = job_json("exploded")
    out = run_with(FakeApi([job_json("queued"), unknown]), tmp_path)
    assert out.failure is not None and out.failure.kind == "bad_response"


# --- Retry-After and the timeout --------------------------------------------------------------


def test_429_and_503_on_upload_wait_for_retry_after(tmp_path: Path) -> None:
    api = FakeApi(
        lifecycle(done_job()),
        post_replies=[
            httpx.Response(429, json=error_json("rate_limited"), headers={"Retry-After": "7"}),
            httpx.Response(503, json=error_json("queue_full"), headers={"Retry-After": "3"}),
        ],
    )
    clock = Clock()
    out = run_with(api, tmp_path, clock, poll=2.0)
    assert out.finished
    assert len(api.paths("POST")) == 3
    assert clock.sleeps[:2] == [7.0, 3.0]  # as long as the server asked, before the next try
    assert [(r["status"], r["retry_after"]) for r in out.retries] == [(429, 7.0), (503, 3.0)]
    # Wall time runs from the upload the API accepted, not from the refused ones.
    assert out.seconds["total"] == 8.0


def test_a_429_without_retry_after_waits_a_default_while_polling(tmp_path: Path) -> None:
    api = FakeApi(
        lifecycle(done_job()),
        get_replies=[httpx.Response(429, json=error_json("rate_limited"))],
    )
    clock = Clock()
    out = run_with(api, tmp_path, clock, poll=2.0)
    assert out.finished
    assert 5.0 in clock.sleeps  # the default, since the API's own 429 has no header
    assert out.retries[0]["what"] == "poll"


def test_retry_after_may_be_a_date() -> None:
    soon = datetime.now(UTC) + timedelta(seconds=60)
    header = email.utils.format_datetime(soon, usegmt=True)
    wait = next_stack.retry_after(httpx.Response(503, headers={"Retry-After": header}))
    assert 50 < wait <= 60
    for garbage in ("", "soon", "-3"):
        reply = httpx.Response(503, headers={"Retry-After": garbage})
        assert next_stack.retry_after(reply) == next_stack.DEFAULT_RETRY_AFTER


def test_a_job_that_never_finishes_ends_at_the_timeout(tmp_path: Path) -> None:
    stuck = [job_json("queued", done=0, active=False), job_json("running", done=1)]
    api = FakeApi(stuck)
    clock = Clock()
    start = clock.now
    out = run_with(api, tmp_path, clock, poll=2.0, timeout=10.0)
    assert out.failure is not None and out.failure.kind == "timeout"
    assert "10 s" in out.failure.message
    assert clock.now - start == 10.0  # not a second more
    assert len(api.requests) == 1 + 5  # the upload and five polls
    assert out.job_id == JOB_ID
    assert out.job is not None and out.job.status == "running"
    assert not out.finished
    assert "total" not in out.seconds


def test_the_timeout_also_bounds_retries_on_upload(tmp_path: Path) -> None:
    busy = httpx.Response(503, json=error_json("queue_full"), headers={"Retry-After": "4"})
    api = FakeApi([job_json("queued")], post_replies=[busy] * 50)
    clock = Clock()
    start = clock.now
    out = run_with(api, tmp_path, clock, timeout=10.0)
    assert out.failure is not None and out.failure.kind == "timeout"
    assert clock.now - start <= 10.0
    assert len(api.paths("POST")) == 3  # t = 0, 4, 8; the next wait would end after 10
    assert out.job_id is None


def test_a_retry_after_longer_than_the_time_left_gives_up_at_once(tmp_path: Path) -> None:
    busy = httpx.Response(503, json=error_json("queue_full"), headers={"Retry-After": "600"})
    clock = Clock()
    out = run_with(
        FakeApi([job_json("queued")], post_replies=[busy]), tmp_path, clock, timeout=60.0
    )
    assert out.failure is not None and out.failure.kind == "timeout"
    assert clock.sleeps == []


def test_ctrl_c_leaves_a_failure_not_a_traceback(tmp_path: Path) -> None:
    clock = Clock()

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    with FakeApi(lifecycle(done_job())).client() as client:
        out = next_stack.run(client, pdf_file(tmp_path), clock=clock, sleep=interrupt)
    assert out.failure is not None and out.failure.kind == "interrupted"
    assert out.job_id == JOB_ID


# --- crops ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crop_url",
    ["https://evil.example/api/jobs/x/media/crop-0.png", "//evil.example/crop-0.png"],
)
def test_a_crop_url_off_the_apis_origin_is_never_fetched(tmp_path: Path, crop_url: str) -> None:
    (tmp_path / "crops").mkdir()
    results = [result_json(0, CAFFEINE, crop_url=crop_url)]
    api = FakeApi(lifecycle(job_json("done", results=results)))
    clock = Clock()
    with api.client() as client:
        out = next_stack.run(
            client, pdf_file(tmp_path), run_dir=tmp_path, clock=clock, sleep=clock.sleep
        )
    assert out.failure is not None and out.failure.kind == "crop_download"
    assert {r.url.host for r in api.requests} == {"api.test"}
    assert not list((tmp_path / "crops").iterdir())


def test_a_crop_that_is_not_an_image_fails_the_run_but_keeps_the_predictions(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeApi(lifecycle(done_job()), media=httpx.Response(200, text="<html>oops</html>"))
    serve(monkeypatch, api)
    assert golden.main(["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0"]) == 1
    run_dir = only_run(cli)
    meta = json.loads((run_dir / "run.json").read_text())
    assert meta["failure"]["kind"] == "crop_download"
    assert meta["status"] == "failed"
    assert len(read_predictions(run_dir / "predictions.csv")) == 6  # the scored part is intact


def test_crops_are_fetched_politely_when_the_edge_rate_limits(tmp_path: Path) -> None:
    (tmp_path / "crops").mkdir()
    api = FakeApi(lifecycle(done_job()))
    original = api.__call__
    limited = {"left": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/media/" in request.url.path and limited["left"]:
            limited["left"] -= 1
            return httpx.Response(429, headers={"Retry-After": "5"})
        return original(request)

    clock = Clock()
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")
    with client:
        out = next_stack.run(
            client, pdf_file(tmp_path), run_dir=tmp_path, clock=clock, sleep=clock.sleep
        )
    assert out.finished
    assert 5.0 in clock.sleeps
    assert len(list((tmp_path / "crops").iterdir())) == 4


# --- results to predictions -------------------------------------------------------------------


def test_predictions_follow_the_results_in_order() -> None:
    job = next_stack.parse_job(
        job_json(
            "done",
            results=[
                result_json(0, BENZENE, page_index=0, is_structure=False),
                result_json(1, CAFFEINE, page_index=4, name="caffeine", name_tier="pin_verified"),
                result_json(2, "CCO", page_index=9, is_structure=None),
            ],
            dropped=1,
        )
    )
    predictions = next_stack.to_predictions(job)
    assert [(p.index, p.smiles, p.page) for p in predictions] == [
        (0, BENZENE, 1),
        (1, CAFFEINE, 5),
        (2, "CCO", 10),
        (3, "", None),
    ]
    assert [p.segment for p in predictions] == [
        "crops/00_crop-0.png",
        "crops/01_crop-1.png",
        "crops/02_crop-2.png",
        "",
    ]
    assert [p.classifier for p in predictions] == ["False", "True", "", ""]
    assert (predictions[1].name, predictions[1].name_tier) == ("caffeine", "pin_verified")
    assert predictions[0].name == "" and predictions[0].confidence == 0.9


def test_the_inchikey_is_the_one_the_scorer_computes_and_differences_are_reported() -> None:
    job = next_stack.parse_job(
        job_json(
            "done",
            results=[
                result_json(0, CAFFEINE, inchikey="WRONG-KEY"),
                result_json(1, BENZENE),
                result_json(2, "C1CC", valid=True, inchikey="ZZZZZZZZZZZZZZ-ZZZZZZZZZZ-N"),
            ],
        )
    )
    predictions = next_stack.to_predictions(job)
    assert predictions[0].inchikey == to_inchikey(CAFFEINE)
    assert predictions[2].inchikey is None  # RDKit rejects it, whatever the API said
    assert next_stack.inchikey_differences(job, predictions) == [0, 2]


def test_the_apis_incomplete_flag_reaches_the_predictions_and_the_score() -> None:
    # ocsr stopped at its token limit, the API says so; a result without the field (an API
    # from before) is complete.
    job = next_stack.parse_job(
        job_json(
            "done",
            results=[
                result_json(0, CAFFEINE, incomplete=False),
                result_json(1, "C1CC", incomplete=True),
                result_json(2, BENZENE),
            ],
        )
    )
    assert [r.incomplete for r in job.results] == [False, True, False]
    predictions = next_stack.to_predictions(job)
    assert [p.incomplete for p in predictions] == [False, True, False]
    s = score([], predictions)
    assert (s.extra, s.invalid, s.incomplete) == (2, 0, 1)  # not "invalid 1": it is cut off


def test_an_incomplete_that_is_not_a_boolean_is_a_bad_response() -> None:
    raw = job_json("done", results=[{**result_json(0, CAFFEINE), "incomplete": "yes"}])
    with pytest.raises(next_stack.BadResponse, match="incomplete"):
        next_stack.parse_job(raw)


def test_a_failed_recognition_is_refused_and_a_cut_structure_is_dropped() -> None:
    job = next_stack.parse_job(
        job_json("done", results=[result_json(0, "", is_structure=None)], dropped=3)
    )
    s = score([], next_stack.to_predictions(job))
    assert (s.refused, s.invalid, s.dropped, s.extra, s.correct) == (1, 0, 3, 0, 0)


# --- memory -----------------------------------------------------------------------------------


def test_run_json_carries_the_peak_memory_of_each_container(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = iter(
        [
            "decimer-next-seg-1 1.00GiB / 15GiB\ndecimer-next-ocsr-1 512MiB / 15GiB",
            "decimer-next-seg-1 3.00GiB / 15GiB\ndecimer-next-ocsr-1 900MiB / 15GiB",
            "decimer-next-seg-1 2.00GiB / 15GiB\ndecimer-next-ocsr-1 700MiB / 15GiB",
        ]
    )

    def fake_docker(args: list[str]) -> str:
        if args[0] == "ps":
            return "decimer-next-seg-1\ndecimer-next-ocsr-1\n"
        return next(
            readings, "decimer-next-seg-1 2.00GiB / 15GiB\ndecimer-next-ocsr-1 700MiB / 15GiB"
        )

    monkeypatch.setattr("decimer_golden.docker_memory.run_docker", fake_docker)
    serve(monkeypatch, FakeApi(lifecycle(done_job())))
    args = [
        "next",
        "--pdf",
        str(pdf_file(tmp_path)),
        "--poll",
        "0.05",
        "--docker-project",
        "decimer-next",
    ]
    assert golden.main(args) == 0
    meta = json.loads((only_run(cli) / "run.json").read_text())
    memory = meta["memory"]
    assert memory["project"] == "decimer-next"
    # One reading before the upload, one after the job (the 2 s timer never fires in this run).
    assert memory["baseline_mib"] == {"decimer-next-ocsr-1": 512.0, "decimer-next-seg-1": 1024.0}
    assert memory["peak_mib"] == {"decimer-next-ocsr-1": 900.0, "decimer-next-seg-1": 3072.0}
    assert (memory["baseline_total_mib"], memory["peak_total_mib"]) == (1536.0, 3972.0)
    assert memory["peak_delta_total_mib"] == 2436.0
    assert memory["peak_delta_per_page_mib"] == 243.6  # 10 pages
    assert memory["samples"] == 2
    assert memory["errors"] == []


def test_missing_docker_does_not_fail_the_run(tmp_path: Path) -> None:
    def no_docker(_args: list[str]) -> str:
        raise FileNotFoundError("docker is not installed or not on PATH")

    sampler = MemorySampler("decimer-next", docker=no_docker)
    out = run_with(FakeApi(lifecycle(done_job())), tmp_path, sampler=sampler)
    assert out.finished
    assert out.memory is not None
    assert out.memory["samples"] == 0
    assert out.memory["errors"] == ["FileNotFoundError: docker is not installed or not on PATH"]


def test_the_sampler_brackets_the_job_not_the_crops(tmp_path: Path) -> None:
    (tmp_path / "crops").mkdir()
    events: list[str] = []

    class Spy(MemorySampler):
        def start(self) -> None:
            events.append("start")

        def stop(self) -> dict[str, Any]:
            events.append("stop")
            return {}

    api = FakeApi(lifecycle(done_job()))
    original = api.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        events.append(f"{request.method} {request.url.path.rsplit('/', 1)[-1][:4]}")
        return original(request)

    clock = Clock()
    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test") as client:
        next_stack.run(
            client,
            pdf_file(tmp_path),
            run_dir=tmp_path,
            sampler=Spy("p"),
            clock=clock,
            sleep=clock.sleep,
        )
    assert events[0] == "start"
    assert events[1] == "POST jobs"
    assert events.index("stop") < events.index("GET crop")
    assert events[events.index("stop") - 1].startswith("GET ")


# --- command line -----------------------------------------------------------------------------


def test_run_directory_is_named_with_the_utc_time(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, FakeApi(lifecycle(done_job())))
    monkeypatch.setattr(
        time, "gmtime", lambda: time.struct_time((2026, 10, 22, 21, 5, 9, 3, 295, 0))
    )
    assert golden.main(["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0"]) == 0
    assert only_run(cli).name == "next-20261022-210509"


def test_two_runs_started_in_the_same_second_get_their_own_directories(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        time, "gmtime", lambda: time.struct_time((2026, 10, 22, 21, 5, 9, 3, 295, 0))
    )
    args = ["next", "--pdf", str(pdf_file(tmp_path)), "--poll", "0"]
    for _ in range(3):
        serve(monkeypatch, FakeApi(lifecycle(done_job())))
        assert golden.main(args) == 0
    names = sorted(path.name for path in cli.iterdir())
    assert names == ["next-20261022-210509", "next-20261022-210509-2", "next-20261022-210509-3"]
    for name in names:  # each is a whole run: nothing was overwritten or left empty
        run_dir = cli / name
        assert json.loads((run_dir / "run.json").read_text())["status"] == "done"
        assert len(read_predictions(run_dir / "predictions.csv")) == 6
        assert len(list((run_dir / "crops").iterdir())) == 4


def test_a_run_directory_that_is_taken_is_never_reused(cli: Path) -> None:
    first = golden._make_run_dir("legacy", "20261022-140954")
    (first / "predictions.csv").write_text("kept")
    second = golden._make_run_dir("legacy", "20261022-140954")
    assert (first.name, second.name) == ("legacy-20261022-140954", "legacy-20261022-140954-2")
    assert (first / "predictions.csv").read_text() == "kept"
    assert (second / "crops").is_dir()


@pytest.mark.parametrize(
    "argv",
    [
        ["--base-url", "http://127.0.0.1:8090/beta"],
        ["--base-url", "ftp://127.0.0.1"],
        ["--pdf", "/nonexistent/paper.pdf"],
    ],
)
def test_bad_arguments_stop_before_any_request_or_run_directory(
    cli: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    def no_network(origin: httpx.URL) -> httpx.Client:
        raise AssertionError("no client should be made")

    monkeypatch.setattr(next_stack, "new_client", no_network)
    args = ["next", "--pdf", str(pdf_file(tmp_path)), *argv]
    assert golden.main(args) == 2
    assert not cli.exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--docker-project", "x; rm -rf /"],
        ["--docker-project", "Has Space"],
        ["--poll", "-1"],
        ["--timeout", "0"],
    ],
)
def test_argument_values_are_checked(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        golden.main(["next", *argv])
    assert stop.value.code == 2


def test_defaults_point_at_the_new_stack_and_the_golden_paper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def capture(args: Any) -> int:
        seen.update(vars(args))
        return 0

    monkeypatch.setattr(golden, "_next", capture)
    assert golden.main(["next"]) == 0
    assert seen["base_url"] == "http://127.0.0.1:8090"
    assert seen["pdf"] == str(golden.PAPER)
    assert (seen["poll"], seen["timeout"], seen["docker_project"]) == (1.0, 900.0, None)


def test_a_boolean_is_not_a_number_and_a_wrong_shape_is_refused() -> None:
    sample = job_json("done", results=[result_json(0, CAFFEINE)])
    assert next_stack.parse_job(sample).pages == 10
    for key, bad in (("pages", True), ("truncated", 1), ("status", None), ("results", {})):
        with pytest.raises(next_stack.BadResponse):
            next_stack.parse_job({**sample, key: bad})
    with pytest.raises(next_stack.BadResponse):
        next_stack.parse_result({**sample["results"][0], "confidence": True})
    with pytest.raises(next_stack.BadResponse, match="not a job id"):
        next_stack.parse_job({**sample, "id": "../../etc/passwd"})
    with pytest.raises(next_stack.BadResponse):
        next_stack.parse_job([sample])


# --- the fake against the real contract --------------------------------------------------------


class Reads(dict[str, Any]):
    """A dict that remembers which keys were looked at."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data)
        self.looked_at: set[str] = set()

    def __contains__(self, key: object) -> bool:
        self.looked_at.add(str(key))
        return super().__contains__(key)


@pytest.mark.skipif(not OPENAPI.exists(), reason="web/src/api/openapi.json is not in this checkout")
def test_the_fake_and_the_parser_match_the_apis_openapi_schema() -> None:
    schemas = json.loads(OPENAPI.read_text())["components"]["schemas"]
    for name, sample, parse in (
        ("JobView", job_json("done", results=[result_json(0, CAFFEINE)]), next_stack.parse_job),
        ("ResultView", result_json(0, CAFFEINE), next_stack.parse_result),
    ):
        schema = schemas[name]
        assert set(schema["required"]) <= set(sample), f"{name}: the fake lacks required fields"
        assert set(sample) <= set(schema["properties"]), f"{name}: the fake invents fields"
        recorded = Reads(sample)
        parse(recorded)
        assert recorded.looked_at <= set(schema["properties"]), (
            f"{name}: the client reads {recorded.looked_at - set(schema['properties'])}"
        )
    assert set(schemas["JobStatus"]["enum"]) == set(next_stack.STATUSES)
    assert [s["name"] for s in stages(4)] == schemas["StageName"]["enum"]
    assert {s["state"] for s in stages(2)} <= set(schemas["StageState"]["enum"])
