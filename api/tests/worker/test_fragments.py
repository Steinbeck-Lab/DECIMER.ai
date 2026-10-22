"""Legacy's fragment filter, reproduced exactly.

The oracle below is the condition of `app/Python/decimer_predictor_server.py` (DECIMER_Web,
lines 64-67), copied as it stands:

    if len(SMILES.split(".")) > 4 or len(SMILES.split(".")[0]) < sum(
            len(part) for part in SMILES.split(".")[1:]):
        processed_info = SMILES.split(".")[0]
    else:
        processed_info = SMILES

(Only the line break is ours.)
"""

from __future__ import annotations

import itertools

import pytest

from decimer_api.worker.fragments import trim_fragments


def legacy(SMILES: str) -> str:  # noqa: N803  (the name legacy uses)
    if len(SMILES.split(".")) > 4 or len(SMILES.split(".")[0]) < sum(
        len(part) for part in SMILES.split(".")[1:]
    ):
        return SMILES.split(".")[0]
    return SMILES


@pytest.mark.parametrize("smiles", ["CCO", "", "c1ccccc1", "CC.O", "CCCC.O.O", "CCCCCC.C.C.C"])
def test_a_prediction_the_filter_leaves_alone(smiles: str) -> None:
    assert trim_fragments(smiles) == smiles


def test_more_than_four_fragments_keeps_the_first_only() -> None:
    long_first = "C" * 40
    assert trim_fragments(f"{long_first}.C.C.C.C") == long_first  # five fragments
    assert trim_fragments(f"{long_first}.C.C.C") == f"{long_first}.C.C.C"  # four is still fine


def test_a_first_fragment_shorter_than_the_others_together_keeps_the_first_only() -> None:
    assert trim_fragments("C.CCC") == "C"
    assert trim_fragments("CC.CCC") == "CC"
    assert trim_fragments("CCO.CC.CC") == "CCO"  # 3 < 2 + 2


def test_the_comparison_is_against_the_sum_of_the_others_and_not_each() -> None:
    """The first fragment is longer than each of the others, but not than all of them together."""
    assert trim_fragments("CCCC.CCC.CCC") == "CCCC"  # 4 < 6
    assert trim_fragments("CCCC.CC.CC") == "CCCC.CC.CC"  # 4 < 4 is false: kept
    assert trim_fragments("CCCCC.CC.CC") == "CCCCC.CC.CC"


def test_equal_length_is_kept() -> None:
    assert trim_fragments("CC.CC") == "CC.CC"


def test_it_works_on_text_not_on_chemistry() -> None:
    """Lengths are of the strings, as in legacy: [Na+].[Cl-] has first fragment 5 characters and
    the other 5, so it is kept; a salt with a short cation is not."""
    assert trim_fragments("[Na+].[Cl-]") == "[Na+].[Cl-]"
    assert trim_fragments("[K+].[Cl-]") == "[K+]"  # 4 < 5


@pytest.mark.parametrize("smiles", [".", "..", "C.", ".C", "...C", "C....."])
def test_odd_dots_follow_the_same_rule(smiles: str) -> None:
    assert trim_fragments(smiles) == legacy(smiles)


def test_it_agrees_with_legacy_on_every_small_prediction() -> None:
    """Every list of one to six fragments, each one to five characters long (19,530 cases)."""
    cases = 0
    for count in range(1, 7):
        for lengths in itertools.product(range(1, 6), repeat=count):
            smiles = ".".join("C" * length for length in lengths)
            assert trim_fragments(smiles) == legacy(smiles), smiles
            cases += 1
    assert cases == sum(5**count for count in range(1, 7))


def test_both_branches_are_reached_by_the_enumeration() -> None:
    """The comparison above is only worth anything if it changes some answers."""
    changed = unchanged = 0
    for count in range(1, 7):
        for lengths in itertools.product(range(1, 6), repeat=count):
            smiles = ".".join("C" * length for length in lengths)
            if trim_fragments(smiles) == smiles:
                unchanged += 1
            else:
                changed += 1
    assert changed > 1000 and unchanged > 50
