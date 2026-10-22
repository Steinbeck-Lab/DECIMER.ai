"""The policy that turns an Orthonym row into what the service ships (owner decision D14).

The expected sets below are written out literally, on purpose: the sets in `decimer_chem.results`
are the code under test and must not be their own oracle.
"""

from __future__ import annotations

import itertools

import pytest

from decimer_chem import results
from decimer_chem.results import NameResult, result_from_row

# What may be shown: Orthonym's `valid` tier, labelled; never `best_effort`, never a bare name.
TIERS = {"pin_verified", "pin_unverified", "systematic_verified"}
ROUND_TRIPPED = {
    "opsin",  # OPSIN read the whole name back to the same structure
    "opsin_split",  # read back without dative bonds
    "opsin_constitution",  # connectivity by OPSIN, stereo by CIP labelling
    "opsin_twin",  # an equivalent spelling read back
    "opsin_acids",  # the component acids read back
    "identity",  # exact match in a curated list
}


def row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "ethanol",
        "tier": "pin_verified",
        "verified": "opsin",
        "limit_code": None,
    }
    base.update(overrides)
    return base


def test_the_allow_lists_are_exactly_the_decided_ones() -> None:
    assert set(results.SHIPPED_TIERS) == TIERS
    assert set(results.ROUND_TRIPPED) == ROUND_TRIPPED


@pytest.mark.parametrize(("tier", "verified"), sorted(itertools.product(TIERS, ROUND_TRIPPED)))
def test_every_allowed_tier_and_check_pair_is_shipped_with_both_labels(
    tier: str, verified: str
) -> None:
    assert result_from_row(row(tier=tier, verified=verified)) == NameResult(
        "ethanol", tier, verified, None
    )


@pytest.mark.parametrize("tier", sorted(TIERS))
@pytest.mark.parametrize(
    "verified",
    ["unverified", None, "", "OPSIN", "opsin_partial", "verified", 7, ["opsin"]],
    ids=["unverified", "none", "empty", "upper", "unknown", "verified-word", "int", "list"],
)
def test_a_name_whose_check_is_not_an_allowed_one_is_withheld_as_not_round_tripped(
    tier: str, verified: object
) -> None:
    withheld = result_from_row(row(tier=tier, verified=verified))
    assert withheld.name is None
    assert withheld.verified is None
    assert withheld.reason == "not_round_tripped"
    assert withheld.tier == tier  # the label of what was withheld still comes back


def test_a_row_without_a_verified_key_is_withheld() -> None:
    bare = {"name": "ethanol", "tier": "pin_verified"}
    assert result_from_row(bare).reason == "not_round_tripped"


def test_pin_unverified_is_shipped_only_with_an_allowed_check() -> None:
    assert result_from_row(row(tier="pin_unverified", verified="opsin")).name == "ethanol"
    withheld = result_from_row(row(tier="pin_unverified", verified="unverified"))
    assert (withheld.name, withheld.reason) == (None, "not_round_tripped")


# The three carve-out rows the review found at Orthonym's default tier: shipped as pin_unverified,
# "unverified", with no round trip. (Copied from real `name_tiered` output.)
REAL_CARVE_OUT_ROWS = [
    {"name": "scyllo-inositol", "tier": "pin_unverified", "verified": "unverified"},
    {
        "name": "1,4(1,4)-dibenzenacyclohexaphane",
        "tier": "pin_unverified",
        "verified": "unverified",
    },
    {"name": "(η⁶-benzene)iron", "tier": "pin_unverified", "verified": "unverified"},
]


@pytest.mark.parametrize("carve_out", REAL_CARVE_OUT_ROWS, ids=lambda r: str(r["name"]))
def test_the_reviewers_carve_out_rows_are_withheld(carve_out: dict[str, object]) -> None:
    withheld = result_from_row(carve_out)
    assert (withheld.name, withheld.reason) == (None, "not_round_tripped")


@pytest.mark.parametrize("tier", ["best_effort", "BEST_EFFORT", "general", "valid", "", None, 7])
def test_a_name_in_any_other_tier_is_withheld_even_when_opsin_checked(tier: object) -> None:
    withheld = result_from_row(row(tier=tier, verified="opsin"))
    assert withheld.name is None
    assert withheld.verified is None
    assert withheld.reason == "tier_not_accepted"


def test_best_effort_is_never_shown_whatever_else_the_row_says() -> None:
    for verified in sorted(ROUND_TRIPPED) + ["unverified", None]:
        assert result_from_row(row(tier="best_effort", verified=verified)).name is None


def test_a_withheld_name_still_reports_the_tier_the_engine_gave_it() -> None:
    assert result_from_row(row(tier="best_effort")).tier == "best_effort"


def test_a_decline_has_no_name_and_carries_orthonyms_tier_and_reason() -> None:
    declined = result_from_row(
        {
            "name": "unknown organic compound",  # a label, not a name
            "tier": "abstain",
            "verified": "unverified",
            "limit_code": "UNNAMEABLE",
        }
    )
    assert declined == NameResult(None, "abstain", None, "UNNAMEABLE")


def test_an_abstain_row_is_a_decline_even_if_it_carries_a_name_like_label() -> None:
    declined = result_from_row(row(tier="abstain", limit_code=None))
    assert (declined.name, declined.reason) == (None, "no_verified_name")


@pytest.mark.parametrize("name", [None, "", "   ", 3])
def test_an_allowed_tier_without_a_usable_name_is_a_decline(name: object) -> None:
    declined = result_from_row(row(name=name))
    assert declined.name is None
    assert declined.reason == "no_verified_name"


@pytest.mark.parametrize("bad", [{}, {"name": "x"}, {"tier": "pin_verified"}])
def test_a_row_missing_keys_is_a_decline_not_an_exception(bad: dict[str, object]) -> None:
    assert result_from_row(bad).name is None


def test_the_reason_falls_back_to_a_fixed_word_when_orthonym_gave_no_code() -> None:
    assert result_from_row({"name": "x", "tier": "abstain"}).reason == "no_verified_name"


def test_as_dict_always_has_the_four_keys() -> None:
    assert NameResult(None, None, None, "timeout").as_dict() == {
        "name": None,
        "tier": None,
        "verified": None,
        "reason": "timeout",
    }


def test_the_reason_words_the_service_uses() -> None:
    assert results.TIMEOUT_REASON == "timeout"
    assert results.INVALID_SMILES_REASON == "invalid_smiles"
    assert results.ENGINE_ERROR_REASON == "engine_error"
    assert results.ENGINE_UNAVAILABLE_REASON == "engine_unavailable"
    assert results.NOT_ROUND_TRIPPED_REASON == "not_round_tripped"
    assert results.NOT_ACCEPTED_REASON == "tier_not_accepted"
    assert results.NO_NAME_REASON == "no_verified_name"


class Tagged(str):
    """What Orthonym's `PrefixName` is: a str that carries an object."""

    derivation = object()


def test_every_field_of_a_result_is_a_plain_str_even_when_the_row_holds_subclasses() -> None:
    shipped = result_from_row(
        {
            "name": Tagged("ethanol"),
            "tier": Tagged("pin_verified"),
            "verified": Tagged("opsin"),
            "limit_code": None,
        }
    )
    assert (shipped.name, shipped.tier, shipped.verified) == ("ethanol", "pin_verified", "opsin")
    assert type(shipped.name) is str
    assert type(shipped.tier) is str
    assert type(shipped.verified) is str


def test_declines_and_withheld_names_are_plain_strs_too() -> None:
    declined = result_from_row(
        {"name": Tagged("label"), "tier": Tagged("abstain"), "limit_code": Tagged("UNNAMEABLE")}
    )
    withheld = result_from_row(row(tier=Tagged("best_effort"), verified=Tagged("opsin")))
    unverified = result_from_row(row(verified=Tagged("unverified")))
    for result in (declined, withheld, unverified):
        assert result.name is None
        assert type(result.tier) is str
        assert type(result.reason) is str


def test_as_dict_is_plain_strs_whatever_the_result_holds() -> None:
    result = NameResult(Tagged("n"), Tagged("t"), Tagged("v"), Tagged("r"))  # built by hand
    for value in result.as_dict().values():
        assert type(value) is str
