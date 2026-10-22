"""Legacy's fragment filter (decision D16), applied to ocsr's SMILES before RDKit.

DECIMER sometimes reads a drawing as several structures joined with dots. The legacy site
(`app/Python/decimer_predictor_server.py` in DECIMER_Web) keeps only the first fragment when

* there are more than four `.`-separated fragments, or
* the first fragment is shorter, in characters, than all the others together,

and the answer stays as it was otherwise. This is the same condition, on the same text.
"""

from __future__ import annotations

FRAGMENT_SEPARATOR = "."
MAX_FRAGMENTS = 4


def trim_fragments(smiles: str) -> str:
    parts = smiles.split(FRAGMENT_SEPARATOR)
    others = sum(len(part) for part in parts[1:])
    if len(parts) > MAX_FRAGMENTS or len(parts[0]) < others:
        return parts[0]
    return smiles
