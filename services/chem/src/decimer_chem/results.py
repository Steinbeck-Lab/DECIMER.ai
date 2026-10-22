"""What the service says about one structure, and the rule that decides when it shows a name.

Owner decision D14: the service ships Orthonym's `valid` tier, labelled, and never `best_effort`.
`result_from_row` is the gate on every row the engine returns. It is fail-closed twice over: the
tier must be on the tier allow-list, and the way the name was checked (`verified`) must be on the
round-trip allow-list. A name that fails either is withheld and never shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

#: The tiers that may be shown (Orthonym `README.md`, "Output tiers"). `best_effort` is the
#: last-resort tier of the widest settings and `abstain` is "no name"; neither is ever shown.
SHIPPED_TIERS = frozenset({"pin_verified", "pin_unverified", "systematic_verified"})

#: The values of Orthonym's `verified` field that mean the name was checked against the structure:
#: `opsin` OPSIN read the whole name back to the same structure; `opsin_split` the same, without the
#: dative bonds; `opsin_constitution` OPSIN confirmed the connectivity and each stereodescriptor
#: was checked against its CIP label; `opsin_twin` an equivalent spelling of the name was read back;
#: `opsin_acids` the component acids of an anhydride name were read back; `identity` an exact
#: match in a curated list. `unverified`, a missing value or anything unknown is not on it.
ROUND_TRIPPED = frozenset(
    {"opsin", "opsin_split", "opsin_constitution", "opsin_twin", "opsin_acids", "identity"}
)

TIMEOUT_REASON = "timeout"
INVALID_SMILES_REASON = "invalid_smiles"
ENGINE_ERROR_REASON = "engine_error"
ENGINE_UNAVAILABLE_REASON = "engine_unavailable"
NOT_ROUND_TRIPPED_REASON = "not_round_tripped"
NOT_ACCEPTED_REASON = "tier_not_accepted"
NO_NAME_REASON = "no_verified_name"


@dataclass(frozen=True)
class NameResult:
    """One answer. `name` is None when there is no verified name; `reason` then says why.

    `tier` is Orthonym's own tier id (`pin_verified`, ... or `abstain`), None when the engine never
    answered (a timeout, an unreadable SMILES). `verified` says how the name was checked and is
    None without a name.
    """

    name: str | None
    tier: str | None
    verified: str | None
    reason: str | None

    def as_dict(self) -> dict[str, str | None]:
        """The four fields as plain `str` or None, whatever they are made of."""
        return {key: _plain(value) for key, value in asdict(self).items()}


def _plain(value: object) -> str | None:
    """`value` as an exact `str`: Orthonym's names can be `str` subclasses (`PrefixName` carries a
    derivation object), and an object that carries its module tree must not travel any further."""
    return None if value is None else str.__str__(value)


def _text(value: object) -> str | None:
    return _plain(value) if isinstance(value, str) and value.strip() else None


def result_from_row(row: Mapping[str, object]) -> NameResult:
    """Turn an `Orthonym.name_tiered` row into a `NameResult` (never raises on odd rows)."""
    tier = row.get("tier")
    name = _text(row.get("name"))
    verified = row.get("verified")
    reported_tier = _text(tier)
    code = _text(row.get("limit_code")) or NO_NAME_REASON
    shipped_tier = isinstance(tier, str) and tier in SHIPPED_TIERS
    if shipped_tier and name is not None:
        if isinstance(verified, str) and verified in ROUND_TRIPPED:
            return NameResult(name, _plain(tier), _plain(verified), None)
        return NameResult(None, reported_tier, None, NOT_ROUND_TRIPPED_REASON)
    if name is None or tier == "abstain" or shipped_tier:
        # A decline: the row's "name" is then a label ("unknown organic compound"), not a name.
        return NameResult(None, reported_tier, None, code)
    # A name in a tier that is not shipped: withhold it.
    return NameResult(None, reported_tier, None, NOT_ACCEPTED_REASON)
