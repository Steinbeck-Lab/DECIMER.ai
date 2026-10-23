"""The multi-image measurement against a fake API: what it uploads, scores and cleans up."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fake_api import BENZENE, CAFFEINE, error_json, job_json, result_json
from test_single_image import ETHANOL, Api, item, labels_file, png

import decimer_golden as golden
from decimer_golden import multi_image, next_stack
from decimer_golden.single_image import Item

SIZE = (100, 60)


def row(smiles: str, index: int, page: int, bbox: tuple[int, int, int, int]) -> dict[str, Any]:
    return {**result_json(index, smiles, page_index=page), "bbox": list(bbox)}


def whole_job(*smiles: str) -> dict[str, Any]:
    """A job as D23 makes it: one result per image, in order, each the whole image."""
    results = [row(s, n, n, (0, 0, *SIZE)) for n, s in enumerate(smiles)]
    return job_json("done", results=results, found=len(smiles))


def images(tmp_path: Path, *labels: str) -> list[Item]:
    return [
        item(tmp_path, f"{n:02d}_crop-{n}.png", SIZE, *label.split())
        for n, label in enumerate(labels)
    ]


def measure(api: Api, groups: list[multi_image.Group], **kwargs: Any) -> list[multi_image.Row]:
    with api.client() as client:
        return multi_image.measure(client, groups, sleep=lambda seconds: None, **kwargs)


def pair(tmp_path: Path, first: str = "1", second: str = "S1") -> multi_image.Group:
    return multi_image.Group(2, 1, tuple(images(tmp_path, first, second)))


# --- the groups ----------------------------------------------------------------------------------


def test_the_crops_are_taken_in_position_order_in_full_groups_of_each_size(tmp_path: Path) -> None:
    items = images(tmp_path, *[""] * 12)
    groups = multi_image.make_groups(items, [2, 5])
    assert [(g.size, g.number) for g in groups] == [(2, n) for n in range(1, 7)] + [(5, 1), (5, 2)]
    assert [i.path.name for i in groups[1].items] == ["02_crop-2.png", "03_crop-3.png"]
    assert [i.path.name for i in groups[-1].items] == [
        f"{n:02d}_crop-{n}.png" for n in range(5, 10)
    ]
    # the leftover (10 and 11) is not sent in a group of five
    assert all(len(g.items) == g.size for g in groups)


def test_a_limit_keeps_the_first_jobs_of_each_size(tmp_path: Path) -> None:
    groups = multi_image.make_groups(images(tmp_path, *[""] * 12), [2, 5], limit=1)
    assert [(g.size, g.number) for g in groups] == [(2, 1), (5, 1)]


def test_a_group_of_one_or_a_negative_limit_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least 2"):
        multi_image.make_groups(images(tmp_path, "", ""), [1])
    with pytest.raises(ValueError, match="negative"):
        multi_image.make_groups(images(tmp_path, "", ""), [2], limit=-1)


def test_fewer_crops_than_a_group_make_no_job(tmp_path: Path) -> None:
    assert multi_image.make_groups(images(tmp_path, "", "", ""), [5]) == []


# --- what it sends and cleans up -----------------------------------------------------------------


def test_a_group_is_one_job_with_its_images_in_order_and_every_job_is_deleted(
    tmp_path: Path,
) -> None:
    api = Api([whole_job(CAFFEINE, BENZENE), whole_job(ETHANOL, ETHANOL)])
    groups = multi_image.make_groups(images(tmp_path, "1", "S1", "", ""), [2])
    rows = measure(api, groups)
    assert len(rows) == 4 and [r.position for r in rows] == [0, 1, 0, 1]
    assert [m for m, _ in api.log if m == "POST"] == ["POST", "POST"]
    assert [m for m, _ in api.log if m == "DELETE"] == ["DELETE", "DELETE"]
    assert not api.overlap and not api.live
    first = api.bodies[0]
    assert first.count(b'name="file"') == 2 and first.count(b"image/png") == 2
    assert first.index(b"00_00_crop-0.png") < first.index(b"01_01_crop-1.png")  # upload order


def test_a_failed_job_is_deleted_and_every_image_of_it_is_wrong(tmp_path: Path) -> None:
    api = Api([job_json("failed", error="nope")])
    rows = measure(api, [pair(tmp_path)])
    assert [(r.job.status, r.correct, r.job.note) for r in rows] == [("failed", False, "nope")] * 2
    assert not api.live


def test_a_refused_upload_is_a_status_and_the_run_goes_on(tmp_path: Path) -> None:
    refused = httpx.Response(422, json=error_json("too_many_files"))
    api = Api([whole_job(CAFFEINE, BENZENE), whole_job(CAFFEINE, BENZENE)], posts=[refused])
    first, _, second, _ = measure(api, [pair(tmp_path), pair(tmp_path)])
    assert first.job.status == "rejected" and "422" in first.job.note
    assert second.job.status == "done" and second.correct is True


def test_a_record_that_is_not_the_documented_shape_is_a_status_and_its_job_is_deleted(
    tmp_path: Path,
) -> None:
    broken = whole_job(CAFFEINE, BENZENE)
    del broken["results"][1]["bbox"]
    api = Api([broken])
    rows = measure(api, [pair(tmp_path)])
    assert {r.job.status for r in rows} == {"bad_response"}
    assert not api.live


# --- what it scores ------------------------------------------------------------------------------


def test_a_job_that_reads_like_d23_is_whole_ordered_and_scored_per_image(tmp_path: Path) -> None:
    api = Api([whole_job(CAFFEINE, ETHANOL)])
    first, second = measure(api, [pair(tmp_path)])
    job = first.job
    assert (job.status, job.found, job.results, job.ordered, job.boxes_whole) == (
        "done",
        2,
        2,
        True,
        True,
    )
    assert job.whole(2) is True
    assert (first.correct, second.correct) == (True, False)  # image 2 is S1 (benzene): ethanol
    assert first.seen.whole and first.seen.results == 1
    csv = first.as_csv()
    assert (csv["size"], csv["group"], csv["position"], csv["job_whole"]) == ("2", "1", "0", "True")
    assert csv["bbox"] == "0 0 100 60" and csv["found"] == "2" and csv["checked"] == "True"


def test_a_job_that_still_segments_is_not_d23_and_is_scored_by_the_images_page_index(
    tmp_path: Path,
) -> None:
    """Seg found two structures on the first image: three results, the second image's is number 3
    (index 2), its box is seg's own."""
    results = [
        row(CAFFEINE, 0, 0, (5, 5, 50, 50)),
        row(ETHANOL, 1, 0, (50, 5, 95, 50)),
        row(BENZENE, 2, 1, (10, 10, 40, 40)),
    ]
    api = Api([job_json("done", results=results, found=3)])
    first, second = measure(api, [pair(tmp_path)])
    job = first.job
    assert (job.found, job.results, job.ordered, job.boxes_whole) == (3, 3, False, False)
    assert job.whole(2) is False
    assert (first.seen.results, second.seen.results) == (2, 1)
    assert (first.correct, second.correct) == (True, True)  # each image's own results
    assert first.as_csv()["job_whole"] == "False"


def test_a_job_whose_results_are_in_a_different_order_is_not_ordered(tmp_path: Path) -> None:
    swapped = job_json(
        "done",
        results=[row(BENZENE, 0, 1, (0, 0, *SIZE)), row(CAFFEINE, 1, 0, (0, 0, *SIZE))],
        found=2,
    )
    first, second = measure(Api([swapped]), [pair(tmp_path)])
    assert first.job.ordered is False and first.job.whole(2) is False
    assert (first.correct, second.correct) == (True, True)  # found by page index all the same


def test_an_image_without_a_result_is_wrong_and_the_job_is_not_d23(tmp_path: Path) -> None:
    one = job_json("done", results=[row(CAFFEINE, 0, 0, (0, 0, *SIZE))], found=1)
    first, second = measure(Api([one]), [pair(tmp_path)])
    assert (first.correct, second.correct, second.seen.results) == (True, False, 0)
    assert first.job.whole(2) is False


def test_the_table_lists_a_job_per_line_with_the_signs_of_d23(tmp_path: Path) -> None:
    api = Api([whole_job(CAFFEINE, BENZENE), whole_job(ETHANOL, ETHANOL)])
    groups = multi_image.make_groups(images(tmp_path, "1", "S1", "1", "S1"), [2])
    rows = measure(api, groups)
    out = tmp_path / "out.csv"
    multi_image.write_rows(out, rows)
    text = multi_image.table(multi_image.read_rows(out))
    lines = text.splitlines()
    assert lines[1].split()[:6] == ["2x1", "done", "0.0", "2", "2", "yes"]
    assert "S1*" in lines[1] and "S1*!" in lines[2]  # the second job is wrong on both
    assert "groups of 2: 2 jobs, 4 images, right 2/4 scored, D23-shaped 2/2 jobs" in text
    assert multi_image.unchecked(rows) == ["S1"]


def test_before_and_after_are_compared_per_image_and_per_job(tmp_path: Path) -> None:
    def table(job: dict[str, Any], folder: str) -> list[dict[str, str]]:
        (tmp_path / folder).mkdir()
        rows = measure(Api([job]), [pair(tmp_path / folder)])
        out = tmp_path / folder / "out.csv"
        multi_image.write_rows(out, rows)
        return multi_image.read_rows(out)

    before = table(
        job_json(
            "done",
            results=[row(ETHANOL, 0, 0, (5, 5, 50, 50)), row(ETHANOL, 1, 1, (5, 5, 9, 9))],
            found=2,
        ),
        "before",
    )
    after = table(whole_job(CAFFEINE, BENZENE), "after")
    text = multi_image.compare(before, after)
    assert "now right: 00_crop-0.png (x2), 01_crop-1.png (x2); now wrong: -" in text
    assert "groups of 2 D23-shaped: 0 jobs before, 1 after" in text


# --- the command ---------------------------------------------------------------------------------


def command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: Api, *args: str
) -> tuple[int, Path]:
    monkeypatch.setattr(next_stack, "new_client", lambda origin: api.client())
    monkeypatch.setattr(golden, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(golden, "LABELS", labels_file(tmp_path))
    return golden.main(["multi-image", "--poll", "0", *args]), tmp_path / "runs"


def crops_folder(tmp_path: Path, count: int) -> Path:
    folder = tmp_path / "crops"
    folder.mkdir()
    for n in range(count):
        (folder / f"{n:02d}_crop-{n}.png").write_bytes(png(*SIZE))
    return folder


def test_the_command_runs_pairs_and_a_group_of_five_and_writes_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    jobs = [whole_job(CAFFEINE, BENZENE), whole_job(CAFFEINE, BENZENE), whole_job(*[ETHANOL] * 5)]
    api = Api(jobs)
    folder = crops_folder(tmp_path, 5)
    code, runs = command(tmp_path, monkeypatch, api, "--crops", str(folder), "--limit", "2")
    assert code == 0
    (run_dir,) = runs.iterdir()
    assert run_dir.name.startswith("multi-")
    rows = multi_image.read_rows(run_dir / "multi-image.csv")
    assert [(r["size"], r["group"], r["position"]) for r in rows] == [
        ("2", "1", "0"), ("2", "1", "1"), ("2", "2", "0"), ("2", "2", "1"),
        ("5", "1", "0"), ("5", "1", "1"), ("5", "1", "2"), ("5", "1", "3"), ("5", "1", "4"),
    ]  # fmt: skip
    assert (run_dir / "run.json").is_file()
    out = capsys.readouterr().out
    assert "groups of 5: 1 jobs, 5 images" in out and "D23-shaped 1/1 jobs" in out
    assert len(api.uploads) == 3 and not api.live and not api.overlap


def test_the_command_compares_with_an_earlier_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = crops_folder(tmp_path, 2)
    code, runs = command(
        tmp_path,
        monkeypatch,
        Api([whole_job(ETHANOL, ETHANOL)]),
        "--crops",
        str(folder),
        "--sizes",
        "2",
    )
    assert code == 0
    (earlier,) = runs.iterdir()
    capsys.readouterr()
    code, _ = command(
        tmp_path,
        monkeypatch,
        Api([whole_job(CAFFEINE, BENZENE)]),
        "--crops", str(folder), "--sizes", "2",
        "--before", str(earlier / "multi-image.csv"),
    )  # fmt: skip
    assert code == 0
    assert "now right: 00_crop-0.png (x2)" in capsys.readouterr().out


def test_bad_arguments_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert golden.main(["multi-image", "--crops", str(tmp_path / "nope")]) == 2
    assert "is not a directory" in capsys.readouterr().err
    folder = crops_folder(tmp_path, 3)
    assert golden.main(["multi-image", "--crops", str(folder), "--sizes", "5"]) == 2
    assert "too few crops" in capsys.readouterr().err
    assert golden.main(["multi-image", "--crops", str(folder), "--limit", "-1"]) == 2
    assert "negative" in capsys.readouterr().err
    with pytest.raises(SystemExit) as stop:
        golden.main(["multi-image", "--crops", str(folder), "--sizes", "1"])
    assert stop.value.code == 2
