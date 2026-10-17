from __future__ import annotations

import pytest

from app.modules.hie.providers.endpoint_policy import (
    HIEEndpointPolicyError,
    validate_hie_endpoint_url,
)


ALLOWED_HOSTS = frozenset({"fhir.partnerhealth.org", "api.epic.com"})


def test_accepts_approved_https_endpoint_and_normalizes_hostname():
    assert validate_hie_endpoint_url(
        "https://FHIR.PartnerHealth.org/fhir/r4",
        allowed_hosts=ALLOWED_HOSTS,
    ) == "https://fhir.partnerhealth.org/fhir/r4"


def test_accepts_default_https_port_and_removes_explicit_port():
    assert validate_hie_endpoint_url(
        "https://fhir.partnerhealth.org:443/fhir/r4",
        allowed_hosts=ALLOWED_HOSTS,
    ) == "https://fhir.partnerhealth.org/fhir/r4"


def test_accepts_root_endpoint():
    assert validate_hie_endpoint_url(
        "https://api.epic.com",
        allowed_hosts=ALLOWED_HOSTS,
    ) == "https://api.epic.com"


@pytest.mark.parametrize(
    "endpoint_url",
    [
        None,
        "",
        "   ",
        "http://fhir.partnerhealth.org/fhir/r4",
        "ftp://fhir.partnerhealth.org/fhir/r4",
        "//fhir.partnerhealth.org/fhir/r4",
        "https:///fhir/r4",
        "https://fhir.partnerhealth.org?token=secret",
        "https://fhir.partnerhealth.org#fragment",
        "https://user:password@fhir.partnerhealth.org/fhir/r4",
        "https://fhir.partnerhealth.org@attacker.example/fhir/r4",
        "https://fhir.partnerhealth.org:8443/fhir/r4",
        "https://fhir.partnerhealth.org:/fhir/r4",
        "https://fhir.partnerhealth.org/fhir/../admin",
        "https://fhir.partnerhealth.org/fhir/%2e%2e/admin",
        "https://fhir.partnerhealth.org\\@attacker.example/fhir/r4",
        "https://fhir.partnerhealth.org/fhir/r4 ",
        "https://fhir.partnerhealth.org\n/fhir/r4",
    ],
)
def test_rejects_malformed_or_unsafe_endpoint_url(endpoint_url):
    with pytest.raises(HIEEndpointPolicyError):
        validate_hie_endpoint_url(endpoint_url, allowed_hosts=ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "endpoint_url",
    [
        "https://attacker.net/fhir/r4",
        "https://sub.fhir.partnerhealth.org/fhir/r4",
        "https://fhir.partnerhealth.org.evil.net/fhir/r4",
    ],
)
def test_requires_exact_approved_hostname(endpoint_url):
    with pytest.raises(HIEEndpointPolicyError, match="not explicitly approved"):
        validate_hie_endpoint_url(endpoint_url, allowed_hosts=ALLOWED_HOSTS)


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "192.168.1.10",
        "[::1]",
        "2130706433",
        "127.1",
        "0x7f000001",
        "0x7f.1",
        "localhost",
        "internal",
        "service.local",
        "service.internal",
        "service.lan",
        "service.home",
        "service.test",
        "service.invalid",
        "service.example",
        "fhir.partnerhealth.org.",
        "*.partnerhealth.org",
        "https://fhir.partnerhealth.org",
        "fhir.partnerhealth.org/path",
    ],
)
def test_rejects_noncanonical_or_nonpublic_allowlist_host(host):
    with pytest.raises(HIEEndpointPolicyError):
        validate_hie_endpoint_url(
            f"https://{host}/fhir/r4" if not host.startswith("https://") else host,
            allowed_hosts={host},
        )


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        [],
        (),
        "fhir.partnerhealth.org",
        None,
        ["https://fhir.partnerhealth.org"],
        ["*.partnerhealth.org"],
        ["localhost"],
        ["127.0.0.1"],
    ],
)
def test_fails_closed_for_missing_or_malformed_allowlist(allowed_hosts):
    with pytest.raises(HIEEndpointPolicyError):
        validate_hie_endpoint_url(
            "https://fhir.partnerhealth.org/fhir/r4",
            allowed_hosts=allowed_hosts,
        )


def test_requires_allowlist_even_for_well_formed_https_url():
    with pytest.raises(HIEEndpointPolicyError, match="not explicitly approved"):
        validate_hie_endpoint_url(
            "https://fhir.partnerhealth.org/fhir/r4",
            allowed_hosts={"api.epic.com"},
        )
