from __future__ import annotations

import socket

import pytest

from app.modules.hie.providers import destination_policy
from app.modules.hie.providers.destination_policy import (
    resolve_hie_endpoint_ips,
    validate_hie_redirect_target,
)
from app.modules.hie.providers.endpoint_policy import HIEEndpointPolicyError


BASE_URL = "https://fhir.partnerhealth.org/fhir/r4/"
ALLOWED_HOSTS = frozenset({"fhir.partnerhealth.org", "api.epic.com"})


def _fake_resolver(*addresses: str):
    def resolve(hostname: str, port: int):
        assert hostname == "fhir.partnerhealth.org"
        assert port == 443
        return addresses

    return resolve


def test_resolves_and_normalizes_public_addresses_with_injected_resolver():
    calls = []

    def resolver(hostname: str, port: int):
        calls.append((hostname, port))
        return (
            "8.8.8.8",
            "2606:4700:4700::1111",
            "8.8.8.8",
        )

    result = resolve_hie_endpoint_ips(
        BASE_URL,
        allowed_hosts=ALLOWED_HOSTS,
        resolver=resolver,
    )

    assert result == frozenset({"8.8.8.8", "2606:4700:4700::1111"})
    assert calls == [("fhir.partnerhealth.org", 443)]


def test_system_resolver_uses_getaddrinfo_without_opening_a_connection(monkeypatch):
    calls = []

    def fake_getaddrinfo(
        hostname,
        port,
        family=0,
        type=0,
        proto=0,
        flags=0,
    ):
        calls.append((hostname, port, family, type, proto))
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("8.8.8.8", 443),
            ),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("2606:4700:4700::1111", 443, 0, 0),
            ),
        ]

    monkeypatch.setattr(
        destination_policy.socket,
        "getaddrinfo",
        fake_getaddrinfo,
    )

    result = resolve_hie_endpoint_ips(
        BASE_URL,
        allowed_hosts=ALLOWED_HOSTS,
    )

    assert result == frozenset({"8.8.8.8", "2606:4700:4700::1111"})
    assert calls == [
        (
            "fhir.partnerhealth.org",
            443,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
        )
    ]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "255.255.255.255",
        "224.0.0.1",
        "192.0.2.1",
        "198.51.100.2",
        "203.0.113.1",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:8.8.8.8",
        "fe80::1%eth0",
        "not-an-ip",
    ],
)
def test_rejects_unsafe_or_malformed_dns_answers(address):
    with pytest.raises(HIEEndpointPolicyError):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=_fake_resolver(address),
        )


def test_rejects_the_entire_dns_answer_set_if_one_address_is_unsafe():
    with pytest.raises(HIEEndpointPolicyError):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=_fake_resolver("8.8.8.8", "127.0.0.1"),
        )


def test_rejects_empty_dns_answer_set():
    with pytest.raises(HIEEndpointPolicyError, match="no addresses"):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=_fake_resolver(),
        )


def test_fails_closed_when_dns_resolver_raises():
    def resolver(hostname: str, port: int):
        raise OSError("simulated resolver failure")

    with pytest.raises(HIEEndpointPolicyError, match="DNS resolution failed"):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=resolver,
        )


def test_rejects_non_allowlisted_hostname_before_running_resolver():
    called = []

    def resolver(hostname: str, port: int):
        called.append((hostname, port))
        return ("8.8.8.8",)

    with pytest.raises(HIEEndpointPolicyError):
        resolve_hie_endpoint_ips(
            "https://attacker.example/fhir/r4",
            allowed_hosts=ALLOWED_HOSTS,
            resolver=resolver,
        )

    assert called == []


def test_accepts_same_origin_absolute_redirect_and_normalizes_host():
    result = validate_hie_redirect_target(
        BASE_URL,
        "https://FHIR.PartnerHealth.org:443/fhir/r4/metadata",
        allowed_hosts=ALLOWED_HOSTS,
    )

    assert result == "https://fhir.partnerhealth.org/fhir/r4/metadata"


def test_accepts_same_origin_relative_redirect():
    result = validate_hie_redirect_target(
        BASE_URL,
        "metadata",
        allowed_hosts=ALLOWED_HOSTS,
    )

    assert result == "https://fhir.partnerhealth.org/fhir/r4/metadata"


def test_accepts_same_origin_root_relative_redirect():
    result = validate_hie_redirect_target(
        BASE_URL,
        "/fhir/r4/metadata",
        allowed_hosts=ALLOWED_HOSTS,
    )

    assert result == "https://fhir.partnerhealth.org/fhir/r4/metadata"


def test_accepts_generator_allowlist_for_redirect():
    allowed_hosts = (host for host in ALLOWED_HOSTS)

    result = validate_hie_redirect_target(
        BASE_URL,
        "metadata",
        allowed_hosts=allowed_hosts,
    )

    assert result == "https://fhir.partnerhealth.org/fhir/r4/metadata"

def test_rejects_cross_origin_redirect_even_when_both_hosts_are_allowlisted():
    with pytest.raises(HIEEndpointPolicyError, match="original origin"):
        validate_hie_redirect_target(
            BASE_URL,
            "https://api.epic.com/fhir/r4",
            allowed_hosts=ALLOWED_HOSTS,
        )


@pytest.mark.parametrize(
    "location",
    [
        "http://fhir.partnerhealth.org/fhir/r4/metadata",
        "//fhir.partnerhealth.org/fhir/r4/metadata",
        "https:metadata",
        "https://user:password@fhir.partnerhealth.org/metadata",
        "https://fhir.partnerhealth.org:8443/metadata",
        "/fhir/r4?access_token=secret",
        "/fhir/r4#fragment",
        "../admin",
        "/fhir/%2e%2e/admin",
        "/fhir/%252e%252e/admin",
        " https://fhir.partnerhealth.org/metadata",
        "https://fhir.partnerhealth.org/metadata ",
        "https://fhir.partnerhealth.org\\@attacker.example/metadata",
        "",
        None,
    ],
)
def test_rejects_unsafe_redirect_targets(location):
    with pytest.raises(HIEEndpointPolicyError):
        validate_hie_redirect_target(
            BASE_URL,
            location,
            allowed_hosts=ALLOWED_HOSTS,
        )