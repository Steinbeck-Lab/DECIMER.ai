"""Who is the client? The rule behind the per-IP fairness limit.

`X-Forwarded-For` is a list that every proxy appends to; everything to the left of the entry
added by our own proxy was written by the client. So:

* the header is honoured only when the direct TCP peer is inside TRUSTED_PROXY_CIDR;
* then it is read from the right, skipping our own proxies, and the first address that is not
  inside TRUSTED_PROXY_CIDR is the client.

uvicorn must run with `proxy_headers=False` (see `__main__`), so `request.client` is always the
TCP peer and nothing rewrites it before this code runs.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

IPAddress = IPv4Address | IPv6Address
Network = IPv4Network | IPv6Network

UNKNOWN = "unknown"


def _parse(raw: str) -> IPAddress | None:
    """An address with an optional port or brackets; IPv4-mapped IPv6 becomes IPv4."""
    text = raw.strip()
    if text.startswith("[") and "]" in text:
        text = text[1 : text.index("]")]
    elif text.count(":") == 1:  # "1.2.3.4:5678"
        text = text.split(":", 1)[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _trusted(address: IPAddress, networks: Sequence[Network]) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def real_client_ip(
    peer: str | None, forwarded_for: Sequence[str], trusted: Sequence[Network]
) -> str | None:
    """The address that fairness limits apply to, or None when the peer is unknown."""
    peer_address = _parse(peer) if peer else None
    if peer_address is None:
        return None
    if not _trusted(peer_address, trusted):
        return str(peer_address)

    entries = [item.strip() for line in forwarded_for for item in line.split(",") if item.strip()]
    leftmost: IPAddress | None = None
    for entry in reversed(entries):
        address = _parse(entry)
        if address is None:
            # Our own proxy never writes garbage; do not guess, use the proxy itself.
            return str(peer_address)
        if not _trusted(address, trusted):
            return str(address)
        leftmost = address
    # Every hop is one of ours (or there is no header): the client is inside the trusted network.
    return str(leftmost if leftmost is not None else peer_address)


def fairness_key(address: str | None) -> str:
    """The bucket a client counts in. IPv6 clients are bucketed by /64, because a single
    subscriber normally holds a whole /64 and could otherwise rotate addresses freely."""
    if address is None:
        return UNKNOWN
    parsed = _parse(address)
    if parsed is None:
        return UNKNOWN
    if isinstance(parsed, IPv6Address):
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False).network_address)
    return str(parsed)
