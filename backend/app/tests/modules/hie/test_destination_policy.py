from __future__ import annotations

from types import SimpleNamespace

import dns.exception

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
    def resolve(hostname: str, port: int, timeout_seconds: float):
        assert hostname == "fhir.partnerhealth.org"
        assert port == 443
        assert 0 < timeout_seconds <= 5.0
        return addresses

    return resolve


def test_resolves_and_normalizes_public_addresses_with_injected_resolver():
    calls = []

    def resolver(hostname: str, port: int, timeout_seconds: float):
        calls.append((hostname, port, timeout_seconds))
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
    assert calls == [("fhir.partnerhealth.org", 443, 2.0)]


class _FakeDNSAnswer:
    def __init__(self, *addresses):
        self.rrset = tuple(addresses) if addresses else None
        self._records = tuple(
            SimpleNamespace(address=address)
            for address in addresses
        )

    def __iter__(self):
        return iter(self._records)


def test_system_resolver_uses_absolute_a_and_aaaa_queries_with_shared_lifetime(
    monkeypatch,
):
    answers = {
        "A": _FakeDNSAnswer("8.8.8.8"),
        "AAAA": _FakeDNSAnswer("2606:4700:4700::1111"),
    }
    clock = {"value": 10.0}

    monkeypatch.setattr(
        destination_policy,
        "_monotonic",
        lambda: clock["value"],
    )

    class FakeResolver:
        def __init__(self, configure=True):
            assert configure is True
            self.timeout = 30.0
            self.calls = []

        def resolve(
            self,
            query_name,
            record_type,
            *,
            search,
            lifetime,
            raise_on_no_answer,
        ):
            self.calls.append(
                (
                    str(query_name),
                    record_type,
                    search,
                    lifetime,
                    raise_on_no_answer,
                )
            )
            if record_type == "A":
                clock["value"] = 10.75
            else:
                clock["value"] = 10.90
            return answers[record_type]

    fake_resolver = FakeResolver()
    monkeypatch.setattr(
        destination_policy.dns.resolver,
        "Resolver",
        lambda configure=True: fake_resolver,
    )

    result = resolve_hie_endpoint_ips(
        BASE_URL,
        allowed_hosts=ALLOWED_HOSTS,
        dns_timeout_seconds=1.25,
    )

    assert result == frozenset({"8.8.8.8", "2606:4700:4700::1111"})
    assert fake_resolver.timeout == 1.25
    assert [
        (call[0], call[1], call[2], call[4])
        for call in fake_resolver.calls
    ] == [
        ("fhir.partnerhealth.org.", "A", False, False),
        ("fhir.partnerhealth.org.", "AAAA", False, False),
    ]
    assert fake_resolver.calls[0][3] == pytest.approx(1.25)
    assert fake_resolver.calls[1][3] == pytest.approx(0.5)


def test_system_resolver_fails_closed_when_dns_lifetime_times_out(monkeypatch):
    class SimulatedLifetimeTimeout(dns.exception.Timeout):
        pass

    class FakeResolver:
        timeout = 2.0

        def resolve(
            self,
            query_name,
            record_type,
            *,
            search,
            lifetime,
            raise_on_no_answer,
        ):
            raise SimulatedLifetimeTimeout("simulated DNS timeout")

    monkeypatch.setattr(
        destination_policy.dns.resolver,
        "LifetimeTimeout",
        SimulatedLifetimeTimeout,
    )
    monkeypatch.setattr(
        destination_policy.dns.resolver,
        "Resolver",
        lambda configure=True: FakeResolver(),
    )

    with pytest.raises(
        HIEEndpointPolicyError,
        match="configured deadline",
    ):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            dns_timeout_seconds=1.0,
        )


def test_system_resolver_stops_when_first_query_consumes_shared_deadline(
    monkeypatch,
):
    clock = {"value": 0.0}
    calls = []

    monkeypatch.setattr(
        destination_policy,
        "_monotonic",
        lambda: clock["value"],
    )

    class FakeResolver:
        timeout = 2.0

        def resolve(
            self,
            query_name,
            record_type,
            *,
            search,
            lifetime,
            raise_on_no_answer,
        ):
            calls.append((record_type, lifetime))
            clock["value"] = 1.01
            return _FakeDNSAnswer("8.8.8.8")

    monkeypatch.setattr(
        destination_policy.dns.resolver,
        "Resolver",
        lambda configure=True: FakeResolver(),
    )

    with pytest.raises(
        HIEEndpointPolicyError,
        match="configured deadline",
    ):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            dns_timeout_seconds=1.0,
        )

    assert [call[0] for call in calls] == ["A"]
    assert calls[0][1] == pytest.approx(1.0)


def test_system_resolver_fails_closed_when_both_record_types_have_no_answer(
    monkeypatch,
):
    class FakeResolver:
        timeout = 2.0

        def resolve(
            self,
            query_name,
            record_type,
            *,
            search,
            lifetime,
            raise_on_no_answer,
        ):
            return _FakeDNSAnswer()

    monkeypatch.setattr(
        destination_policy.dns.resolver,
        "Resolver",
        lambda configure=True: FakeResolver(),
    )

    with pytest.raises(HIEEndpointPolicyError, match="no addresses"):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            dns_timeout_seconds=1.0,
        )


@pytest.mark.parametrize(
    "timeout_seconds",
    [
        True,
        0,
        -1,
        5.01,
        float("nan"),
        float("inf"),
        "2",
        None,
    ],
)
def test_rejects_invalid_dns_timeout_budget(timeout_seconds):
    with pytest.raises(ValueError, match="DNS timeout"):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=_fake_resolver("8.8.8.8"),
            dns_timeout_seconds=timeout_seconds,
        )


def test_accepts_maximum_dns_timeout_budget():
    result = resolve_hie_endpoint_ips(
        BASE_URL,
        allowed_hosts=ALLOWED_HOSTS,
        resolver=_fake_resolver("8.8.8.8"),
        dns_timeout_seconds=5.0,
    )

    assert result == frozenset({"8.8.8.8"})

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
    def resolver(hostname: str, port: int, timeout_seconds: float):
        raise OSError("simulated resolver failure")

    with pytest.raises(HIEEndpointPolicyError, match="DNS resolution failed"):
        resolve_hie_endpoint_ips(
            BASE_URL,
            allowed_hosts=ALLOWED_HOSTS,
            resolver=resolver,
        )


def test_rejects_non_allowlisted_hostname_before_running_resolver():
    called = []

    def resolver(hostname: str, port: int, timeout_seconds: float):
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