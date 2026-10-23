"""The single-image measurement against a fake API: what it uploads, scores and cleans up."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_api import BENZENE, CAFFEINE, JOB_ID, error_json, job_json, result_json

import decimer_golden as golden
from decimer_golden import next_stack, single_image
from decimer_golden.score import to_inchikey
from decimer_golden.single_image import Expected, Item

LABELS = (
    "number,page,inchikey,smiles,checked,note,legacy_segment\n"
    f"1,3,{to_inchikey(CAFFEINE)},{CAFFEINE},yes,caffeine,0\n"
    f"S1,7,{to_inchikey(BENZENE)},{BENZENE},no,benzene,2\n"
    "S2,7,AAAAAAAAAAAAAA-BBBBBBBBBB-N,C,no,no crop of this one,\n"
)
ETHANOL = "CCO"


def png(width: int, height: int) -> bytes:
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
    return header + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00" + b"\0" * 12


class Api:
    """Answers each upload with the next scripted job and records every request, so a test can see
    that jobs are one at a time (never a POST while an earlier job is not deleted). `posts[i]`,
    when it is not None, replaces the answer to the i-th upload (the job is still made)."""

    def __init__(
        self,
        jobs: list[dict[str, Any]],
        *,
        poll_status: int = 200,
        posts: list[httpx.Response | None] | None = None,
    ) -> None:
        self.jobs = list(jobs)
        self.poll_status = poll_status
        self.posts = posts or []
        self.log: list[tuple[str, str]] = []
        self.live: set[str] = set()
        self.uploads: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.overlap = False
        self.current: dict[str, Any] = {}

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), base_url="http://api.test")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.log.append((request.method, path))
        if request.method == "POST":
            self.overlap = self.overlap or bool(self.live)
            self.uploads.append(request)
            self.bodies.append(request.read())
            job = self.jobs.pop(0)
            self.live.add(job["id"])
            self.current = job
            wanted = (
                self.posts[len(self.uploads) - 1] if len(self.uploads) <= len(self.posts) else None
            )
            if wanted is not None:
                return wanted
            return httpx.Response(
                202,
                json={**job, "status": "queued", "results": []},
                headers={"Location": f"/api/jobs/{job['id']}"},
            )
        if request.method == "GET":
            if self.poll_status != 200:
                return httpx.Response(self.poll_status, json=error_json("nope"))
            return httpx.Response(200, json=self.current)
        if request.method == "DELETE":
            self.live.discard(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        return httpx.Response(404)


def done(smiles: str, bbox: tuple[int, int, int, int], found: int = 1) -> dict[str, Any]:
    result = {**result_json(0, smiles, page_index=0), "bbox": list(bbox)}
    return job_json("done", results=[result], found=found)


def expected(*numbers: str) -> tuple[Expected, ...]:
    table = {
        "1": Expected("1", to_inchikey(CAFFEINE) or "", True),
        "S1": Expected("S1", to_inchikey(BENZENE) or "", False),
    }
    return tuple(table[n] for n in numbers)


def item(tmp_path: Path, name: str, size: tuple[int, int], *numbers: str) -> Item:
    path = tmp_path / name
    path.write_bytes(png(*size))
    return Item(path, expected(*numbers))


def measure(api: Api, items: list[Item], **kwargs: Any) -> list[single_image.Row]:
    with api.client() as client:
        return single_image.measure(
            client,
            items,
            whole_fraction=kwargs.pop("whole_fraction", 0.6),
            sleep=lambda seconds: None,
            **kwargs,
        )


def labels_file(tmp_path: Path) -> Path:
    path = tmp_path / "labels.csv"
    path.write_text(LABELS)
    return path


# --- what it sends and cleans up ----------------------------------------------------------------


def test_every_image_is_one_job_of_its_own_that_is_deleted_before_the_next(tmp_path: Path) -> None:
    api = Api([done(CAFFEINE, (0, 0, 100, 100)), done(BENZENE, (0, 0, 100, 100))])
    items = [
        item(tmp_path, "00_crop-0.png", (100, 100)),
        item(tmp_path, "01_crop-1.png", (100, 100)),
    ]
    rows = measure(api, items)
    assert [row.crop for row in rows] == ["00_crop-0.png", "01_crop-1.png"]
    assert [m for m, _ in api.log if m == "POST"] == ["POST", "POST"]
    assert [m for m, _ in api.log if m == "DELETE"] == ["DELETE", "DELETE"]
    assert api.log[-1] == ("DELETE", f"/api/jobs/{JOB_ID}")
    assert not api.overlap  # no upload while an earlier job still existed
    assert not api.live
    # the image goes up as a pasted PNG in the field "file", on its own
    assert b'name="file"' in api.bodies[0] and b"image/png" in api.bodies[0]
    assert api.bodies[0].count(b'name="file"') == 1


def test_a_job_that_failed_is_deleted_too_and_the_image_is_wrong(tmp_path: Path) -> None:
    api = Api([job_json("failed", error="nope")])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100), "1")])
    assert (row.on.status, row.correct, row.on.note) == ("failed", False, "nope")
    assert not api.live


def test_a_poll_that_breaks_still_deletes_the_job(tmp_path: Path) -> None:
    api = Api([done(CAFFEINE, (0, 0, 1, 1))], poll_status=500)
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100))])
    assert row.on.status == "http_error"
    assert not api.live and ("DELETE", f"/api/jobs/{JOB_ID}") in api.log


def test_a_refused_upload_is_waited_for_and_tried_again(tmp_path: Path) -> None:
    busy = httpx.Response(429, json=error_json("too_many_jobs"), headers={"Retry-After": "7"})
    api = Api([done(CAFFEINE, (0, 0, 100, 100))])
    answers = [busy]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and answers:
            return answers.pop()
        return api(request)

    waits: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")
    with client:
        (row,) = single_image.measure(
            client,
            [item(tmp_path, "a.png", (100, 100))],
            whole_fraction=0.6,
            sleep=waits.append,
        )
    assert row.on.status == "done"
    assert waits and waits[0] == 7.0


# --- an answer that is not the documented shape --------------------------------------------------


def test_an_accepted_upload_with_a_body_that_is_not_json_is_deleted_by_its_location(
    tmp_path: Path,
) -> None:
    garbled = httpx.Response(
        202, text="<html>proxy</html>", headers={"Location": f"/api/jobs/{JOB_ID}"}
    )
    api = Api([done(CAFFEINE, (0, 0, 9, 9)), done(CAFFEINE, (0, 0, 100, 100))], posts=[garbled])
    first, second = measure(
        api, [item(tmp_path, "a.png", (100, 100)), item(tmp_path, "b.png", (100, 100))]
    )
    assert first.on.status == "bad_response" and "not JSON" in first.on.note
    assert second.on.status == "done"  # the run went on
    assert ("DELETE", f"/api/jobs/{JOB_ID}") in api.log
    assert not api.live


def test_an_upload_answer_without_any_job_id_is_a_row_not_a_crash(tmp_path: Path) -> None:
    api = Api([done(CAFFEINE, (0, 0, 9, 9))], posts=[httpx.Response(202, text="ok")])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100))])
    assert (row.on.status, "no job id" in row.on.note) == ("bad_response", True)


def test_a_result_without_a_box_is_a_row_and_its_job_is_deleted(tmp_path: Path) -> None:
    result = result_json(0, CAFFEINE, page_index=0)
    del result["bbox"]
    api = Api([job_json("done", results=[result]), done(CAFFEINE, (0, 0, 100, 100))])
    broken, fine = measure(
        api, [item(tmp_path, "a.png", (100, 100), "1"), item(tmp_path, "b.png", (100, 100), "1")]
    )
    assert broken.on.status == "bad_response" and broken.correct is False
    assert fine.on.status == "done" and fine.correct is True
    assert not api.live


def test_a_poll_with_a_body_that_is_not_json_is_a_row_and_the_job_is_deleted(
    tmp_path: Path,
) -> None:
    api = Api([done(CAFFEINE, (0, 0, 9, 9))])
    real = api.__call__

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text="not json")
        return real(request)

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test") as c:
        (row,) = single_image.measure(
            c, [item(tmp_path, "a.png", (100, 100))], whole_fraction=0.6, sleep=lambda s: None
        )
    assert row.on.status == "bad_response"
    assert not api.live


# --- what it records ----------------------------------------------------------------------------


def test_crops_are_matched_to_the_label_of_the_same_drawing_by_legacy_segment(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "crops"
    folder.mkdir()
    for name in ("00_crop-0.png", "02_crop-2.png", "03_crop-3.png", "notes.txt"):
        (folder / name).write_bytes(png(10, 10))
    items = single_image.crop_items(folder, labels_file(tmp_path))
    assert [(i.path.name, [e.number for e in i.expected]) for i in items] == [
        ("00_crop-0.png", ["1"]),
        ("02_crop-2.png", ["S1"]),
        ("03_crop-3.png", []),  # no label has legacy_segment 3
    ]
    assert [e.checked for i in items for e in i.expected] == [True, False]


def test_the_smiles_is_scored_by_full_inchikey_against_the_label(tmp_path: Path) -> None:
    api = Api(
        [
            done(CAFFEINE, (0, 0, 100, 100)),
            done(ETHANOL, (0, 0, 100, 100)),
            done("C", (0, 0, 100, 100)),
        ]
    )
    rows = measure(
        api,
        [
            item(tmp_path, "00.png", (100, 100), "1"),
            item(tmp_path, "02.png", (100, 100), "S1"),
            item(tmp_path, "03.png", (100, 100)),
        ],
    )
    assert [r.correct for r in rows] == [True, False, None]
    assert rows[0].on.inchikey == to_inchikey(CAFFEINE)
    assert [r.as_csv()["checked"] for r in rows] == ["True", "False", ""]  # of the label
    assert single_image.unchecked(rows) == ["S1"]


def test_the_connectivity_block_alone_is_not_enough(tmp_path: Path) -> None:
    flat = to_inchikey("CC(N)C(=O)O") or ""
    stereo = "C[C@H](N)C(=O)O"
    assert flat != to_inchikey(stereo)
    path = tmp_path / "a.png"
    path.write_bytes(png(10, 10))
    api = Api([done(stereo, (0, 0, 10, 10))])
    (row,) = measure(api, [Item(path, (Expected("x", flat, True),))])
    assert row.correct is False


@pytest.mark.parametrize(
    ("bbox", "found", "predicted"),
    [
        ((0, 0, 100, 60), 1, True),  # exactly 60 %
        ((0, 0, 100, 59), 1, False),
        ((0, 0, 100, 100), 1, True),
        ((0, 0, 100, 100), 2, False),  # more than one structure: never
        ((10, 10, 20, 20), 1, False),
    ],
)
def test_the_rule_is_predicted_from_the_box_and_the_number_found(
    tmp_path: Path, bbox: tuple[int, int, int, int], found: int, predicted: bool
) -> None:
    api = Api([done(CAFFEINE, bbox, found=found)])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100))], whole_fraction=0.6)
    assert row.predicted_whole is predicted
    assert row.on.fraction == pytest.approx((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) / 10000)
    assert row.on.whole is (bbox == (0, 0, 100, 100))


def test_an_image_with_two_labels_is_right_only_when_both_are_among_the_results(
    tmp_path: Path,
) -> None:
    both = job_json(
        "done",
        results=[
            {**result_json(0, CAFFEINE, page_index=0), "bbox": [0, 0, 10, 10]},
            {**result_json(1, BENZENE, page_index=0), "bbox": [10, 0, 20, 10]},
        ],
    )
    one = job_json(
        "done", results=[{**result_json(0, CAFFEINE, page_index=0), "bbox": [0, 0, 10, 10]}]
    )
    api = Api([both, one])
    right, partly = measure(
        api,
        [
            item(tmp_path, "a.png", (100, 100), "1", "S1"),
            item(tmp_path, "b.png", (100, 100), "1", "S1"),
        ],
    )
    assert (right.on.matched, right.correct) == (2, True)
    assert (partly.on.matched, partly.correct) == (1, False)
    assert right.on.smiles == CAFFEINE  # the first result's


def test_the_negatives_come_from_their_csv_with_the_labels_they_name(tmp_path: Path) -> None:
    folder = tmp_path / "neg"
    folder.mkdir()
    (folder / "negatives.csv").write_text(
        "file,page,region_px,kind,contains,labels\n"
        "a.png,3,1 2 3 4,two structures,two,1;S1\n"
        "b.png,3,1 2 3 4,structure+text,none,\n"
    )
    items = single_image.negative_items(folder, labels_file(tmp_path))
    assert [(i.path.name, [e.number for e in i.expected]) for i in items] == [
        ("a.png", ["1", "S1"]),
        ("b.png", []),
    ]


# --- the job without the rule ---------------------------------------------------------------------


def test_the_job_without_the_rule_has_a_blank_companion_and_shows_seg_own_box(
    tmp_path: Path,
) -> None:
    on = done(CAFFEINE, (0, 0, 100, 100))  # the rule fired: the whole image
    off = done(ETHANOL, (10, 10, 90, 90))  # seg's own box, a wrong reading
    api = Api([on, off])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100), "1")], also_without_rule=True)
    assert api.bodies[0].count(b'name="file"') == 1
    assert api.bodies[1].count(b'name="file"') == 2 and b"blank.png" in api.bodies[1]
    assert (row.correct, row.off_correct) == (True, False)
    assert row.off is not None and row.off.bbox == (10, 10, 90, 90)
    assert row.fired is True and row.predicted_whole is True  # 64 % of the image
    csv = row.as_csv()
    assert (csv["off_found"], csv["off_bbox"], csv["off_correct"]) == ("1", "10 10 90 90", "False")
    assert not api.live


def test_the_rule_did_not_fire_when_the_job_without_it_shows_the_same_box(tmp_path: Path) -> None:
    api = Api([done(CAFFEINE, (10, 10, 40, 40)), done(CAFFEINE, (10, 10, 40, 40))])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100), "1")], also_without_rule=True)
    assert (row.fired, row.predicted_whole) == (False, False)


def test_a_seg_that_found_nothing_is_the_old_fallback_not_the_rule(tmp_path: Path) -> None:
    nothing = job_json("done", results=[], found=0)
    api = Api([done(CAFFEINE, (0, 0, 100, 100), found=1), nothing])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100))], also_without_rule=True)
    assert row.fired is False  # off.found == 0
    assert row.off is not None and row.off.found == 0 and row.off.status == "done"


# --- reading the table --------------------------------------------------------------------------


def run_table(folder: Path, jobs: list[dict[str, Any]], names: list[str]) -> list[dict[str, str]]:
    """One measurement in its own folder, read back from the CSV it was written to."""
    folder.mkdir()
    labels = {"00_crop-0.png": ("1",), "02_crop-2.png": ("S1",)}
    rows = measure(Api(jobs), [item(folder, n, (100, 100), *labels.get(n, ())) for n in names])
    out = folder / "out.csv"
    single_image.write_rows(out, rows)
    return single_image.read_rows(out)


def test_before_and_after_are_compared_per_crop(tmp_path: Path) -> None:
    names = ["00_crop-0.png", "02_crop-2.png"]
    before = run_table(
        tmp_path / "before",
        [done(ETHANOL, (10, 10, 90, 90)), done(BENZENE, (10, 10, 20, 20))],
        names,
    )
    after = run_table(
        tmp_path / "after",
        [done(CAFFEINE, (0, 0, 100, 100)), done(BENZENE, (10, 10, 20, 20))],
        names,
    )
    text = single_image.compare(before, after)
    first = next(line for line in text.splitlines() if line.startswith("00_crop-0"))
    assert first.split()[1:5] == ["yes", "yes", "no", "yes"]  # predicted, fired, before, after
    second = next(line for line in text.splitlines() if line.startswith("02_crop-2"))
    assert second.split()[1:5] == ["no", "no", "yes", "yes"]
    assert "predicted 1, fired 1, the same crops" in text
    assert "now right: 00_crop-0.png" in text


def test_a_rule_that_fires_on_other_crops_than_predicted_is_reported(tmp_path: Path) -> None:
    names = ["00_crop-0.png"]
    before = run_table(tmp_path / "before", [done(CAFFEINE, (10, 10, 20, 20))], names)
    after = run_table(tmp_path / "after", [done(CAFFEINE, (0, 0, 100, 100))], names)
    assert "DIFFERENT: 00_crop-0.png" in single_image.compare(before, after)


def test_the_table_marks_unchecked_labels_and_lists_the_rule(tmp_path: Path) -> None:
    rows = run_table(tmp_path / "t", [done(BENZENE, (0, 0, 100, 70))], ["02_crop-2.png"])
    text = single_image.table(rows)
    assert "02_crop-2.png" in text and "70%" in text and BENZENE in text
    assert "S1*" in text  # the label is a draft
    assert "right 1/1 scored; the rule applies to 1: 02_crop-2.png" in text


def test_the_table_with_the_job_without_the_rule_shows_both_readings(tmp_path: Path) -> None:
    api = Api([done(CAFFEINE, (0, 0, 100, 100)), done(ETHANOL, (10, 10, 90, 90))])
    rows = measure(api, [item(tmp_path, "a.png", (100, 100), "1")], also_without_rule=True)
    out = tmp_path / "out.csv"
    single_image.write_rows(out, rows)
    line = single_image.table(single_image.read_rows(out)).splitlines()[1]
    assert "crop path" in single_image.table(single_image.read_rows(out)).splitlines()[0]
    assert line.split()[:2] == ["a.png", "1"] and "64%" in line  # seg's own box, not the whole
    assert line.count("yes") >= 2 and "no" in line


# --- the command --------------------------------------------------------------------------------


def command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: Api, *args: str
) -> tuple[int, Path]:
    monkeypatch.setattr(next_stack, "new_client", lambda origin: api.client())
    monkeypatch.setattr(golden, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(golden, "LABELS", labels_file(tmp_path))
    return golden.main(["single-image", "--poll", "0", *args]), tmp_path / "runs"


def test_the_command_writes_the_table_and_its_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "crops"
    folder.mkdir()
    (folder / "00_crop-0.png").write_bytes(png(100, 100))
    api = Api([done(CAFFEINE, (0, 0, 100, 100))])
    code, runs = command(tmp_path, monkeypatch, api, "--crops", str(folder))
    assert code == 0
    (run_dir,) = runs.iterdir()
    assert run_dir.name.startswith("single-")
    rows = single_image.read_rows(run_dir / "single-image.csv")
    assert [(r["crop"], r["correct"]) for r in rows] == [("00_crop-0.png", "True")]
    assert (run_dir / "run.json").is_file()
    out = capsys.readouterr().out
    assert "right 1/1" in out and "WARNING" not in out  # label 1 is checked
    assert not api.live


def test_the_command_warns_about_unchecked_labels_like_score_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "crops"
    folder.mkdir()
    (folder / "02_crop-2.png").write_bytes(png(100, 100))  # S1: a draft label
    api = Api([done(BENZENE, (0, 0, 100, 100))])
    code, _ = command(tmp_path, monkeypatch, api, "--crops", str(folder))
    assert code == 0
    out = capsys.readouterr().out
    assert "WARNING: 1 labels not yet checked by hand (marked * above): S1" in out
    assert "before/after" in out


def test_the_command_runs_the_negatives_with_the_job_without_the_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path / "neg"
    folder.mkdir()
    (folder / "a.png").write_bytes(png(100, 100))
    (folder / "negatives.csv").write_text(
        "file,page,region_px,kind,contains,labels\na.png,3,1 2 3 4,structure+text,x,1\n"
    )
    api = Api([done(CAFFEINE, (0, 0, 100, 100)), done(CAFFEINE, (5, 5, 95, 95))])
    code, runs = command(
        tmp_path, monkeypatch, api, "--negatives", str(folder), "--also-without-rule"
    )
    assert code == 0
    (run_dir,) = runs.iterdir()
    (row,) = single_image.read_rows(run_dir / "single-image.csv")
    assert (row["fired"], row["off_bbox"], row["off_correct"]) == ("True", "5 5 95 95", "True")
    assert "crop path" in capsys.readouterr().out
    assert len(api.uploads) == 2 and not api.live


def test_a_directory_that_is_not_there_or_no_input_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert golden.main(["single-image", "--crops", str(tmp_path / "nope")]) == 2
    assert "is not a directory" in capsys.readouterr().err
    assert golden.main(["single-image"]) == 2
    assert "--crops and/or --negatives" in capsys.readouterr().err


def test_only_the_results_of_the_first_image_count(tmp_path: Path) -> None:
    results = [
        {**result_json(0, ETHANOL, page_index=1), "bbox": [0, 0, 10, 10]},  # the blank companion
        {**result_json(1, CAFFEINE, page_index=0), "bbox": [0, 0, 10, 10]},
    ]
    api = Api([job_json("done", results=results, found=1)])
    (row,) = measure(api, [item(tmp_path, "a.png", (100, 100), "1")])
    assert (row.on.results, row.on.smiles, row.correct) == (1, CAFFEINE, True)
