from __future__ import annotations

from ipaddress import ip_network

import pytest

from decimer_api.clientip import fairness_key, real_client_ip

PROXY = [ip_network("10.0.0.0/8")]


def test_untrusted_peer_cannot_set_the_client_ip() -> None:
    # A direct client sends a forged header; it is ignored.
    assert real_client_ip("203.0.113.7", ["6.6.6.6"], PROXY) == "203.0.113.7"


def test_no_trusted_proxy_configured_means_header_is_never_honoured() -> None:
    assert real_client_ip("10.0.0.1", ["6.6.6.6"], []) == "10.0.0.1"


def test_trusted_peer_gives_the_rightmost_untrusted_entry() -> None:
    # "forged, real, internal hop": the client controls everything left of what the proxy added.
    assert real_client_ip("10.0.0.1", ["6.6.6.6, 198.51.100.5, 10.0.0.2"], PROXY) == "198.51.100.5"


def test_leftmost_entry_is_not_used_even_when_untrusted() -> None:
    assert real_client_ip("10.0.0.1", ["6.6.6.6, 198.51.100.5"], PROXY) == "198.51.100.5"


def test_several_header_lines_are_read_in_order() -> None:
    assert real_client_ip("10.0.0.1", ["6.6.6.6", "198.51.100.5"], PROXY) == "198.51.100.5"


def test_trusted_peer_without_header_is_its_own_client() -> None:
    assert real_client_ip("10.0.0.1", [], PROXY) == "10.0.0.1"


def test_all_entries_trusted_gives_the_leftmost() -> None:
    assert real_client_ip("10.0.0.1", ["10.9.9.9, 10.0.0.2"], PROXY) == "10.9.9.9"


def test_garbage_entry_falls_back_to_the_peer() -> None:
    assert real_client_ip("10.0.0.1", ["198.51.100.5, not-an-ip"], PROXY) == "10.0.0.1"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("198.51.100.5:4711", "198.51.100.5"),
        ("[2001:db8::5]:4711", "2001:db8::5"),
        ("2001:db8::5", "2001:db8::5"),
        ("::ffff:198.51.100.5", "198.51.100.5"),
    ],
)
def test_entries_with_ports_and_mapped_addresses(entry: str, expected: str) -> None:
    assert real_client_ip("10.0.0.1", [entry], PROXY) == expected


def test_unparseable_or_missing_peer_is_none() -> None:
    assert real_client_ip(None, ["198.51.100.5"], PROXY) is None
    assert real_client_ip("garbage", ["198.51.100.5"], PROXY) is None


def test_ipv6_clients_share_a_slot_per_64_prefix() -> None:
    assert fairness_key("2001:db8:1:2:aaaa::1") == fairness_key("2001:db8:1:2:bbbb::9")
    assert fairness_key("2001:db8:1:2::1") != fairness_key("2001:db8:1:3::1")


def test_ipv4_key_is_the_address() -> None:
    assert fairness_key("198.51.100.5") == "198.51.100.5"
    assert fairness_key(None) == "unknown"
