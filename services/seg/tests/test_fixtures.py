"""The hand-checked bbox fixtures must be well-formed, or the slow test checks nothing."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("page*_bboxes.json"))


def load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(path.read_text())
    return loaded


def test_there_are_fixtures_for_the_figure_pages_and_a_text_only_page() -> None:
    by_page = {load(p)["page_index"]: load(p) for p in FIXTURES}
    # pages 3, 4 and 7: the pages where the earlier Laravel version of decimer.ai found crops
    assert {2, 3, 6} <= set(by_page)
    assert len(by_page[2]["bboxes"]) == 12  # Figure 2
    assert any(f["bboxes"] == [] for f in by_page.values())  # a page without structures


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_is_well_formed(path: Path) -> None:
    fixture = load(path)
    assert path.stem == f"page{fixture['page_index'] + 1}_bboxes"
    assert fixture["pdf"] == "zou2024_penicillium_shentong.pdf"
    assert fixture["dpi"] == 300
    assert fixture["checked"], "say how and when a human checked these boxes"
    width, height = fixture["width"], fixture["height"]
    for x0, y0, x1, y1 in fixture["bboxes"]:
        assert 0 <= x0 < x1 <= width
        assert 0 <= y0 < y1 <= height
