from __future__ import annotations

import dns.exception
import dns.name
import dns.resolver
import ipaddress
import math
from collections.abc import Callable, Iterable
from time import monotonic as _monotonic
from urllib.parse import unquote, urljoin, urlsplit

from app.modules.hie.providers.endpoint_policy import (
    HIEEndpointPolicyError,
    validate_hie_endpoint_url,
)


AddressResolver = Callable[[str, int, float], Iterable[str]]

DEFAULT_DNS_TIMEOUT_SECONDS = 2.0
MAX_DNS_TIMEOUT_SECONDS = 5.0


def validate_hie_dns_timeout(timeout_seconds: object) -> float:
    """Validate a finite, positive DNS timeout no greater than five seconds."""
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise ValueError(
            "DNS timeout must be greater than zero and at most "
            f"{MAX_DNS_TIMEOUT_SECONDS:g} seconds"
        )

    try:
        timeout = float(timeout_seconds)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(
            "DNS timeout must be greater than zero and at most "
            f"{MAX_DNS_TIMEOUT_SECONDS:g} seconds"
        ) from exc

    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_DNS_TIMEOUT_SECONDS:
        raise ValueError(
            "DNS timeout must be greater than zero and at most "
            f"{MAX_DNS_TIMEOUT_SECONDS:g} seconds"
        )

    return timeout


def _system_dns_resolver(
    hostname: str,
    port: int,
    timeout_seconds: float,
) -> tuple[str, ...]:
    """Resolve absolute A/AAAA queries under one shared monotonic deadline.

    dnspython applies each query's remaining lifetime to its DNS work. A
    monotonic deadline is shared across the IPv4 and IPv6 lookups, so the
    second query cannot receive a fresh copy of the original time budget.
    This performs DNS resolution only; it does not open an HTTPS connection.
    """
    if port != 443:
        raise HIEEndpointPolicyError(
            "HIE DNS resolver only permits the default HTTPS port"
        )

    timeout_budget = validate_hie_dns_timeout(timeout_seconds)
    deadline = _monotonic() + timeout_budget

    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = min(float(resolver.timeout), timeout_budget)
        # An absolute DNS name avoids search-domain expansion.
        query_name = dns.name.from_text(hostname, origin=dns.name.root)
    except (dns.exception.DNSException, OSError, ValueError, TypeError) as exc:
        raise HIEEndpointPolicyError(
            "DNS resolver configuration failed for the approved HIE endpoint"
        ) from exc

    addresses: list[str] = []

    for record_type in ("A", "AAAA"):
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise HIEEndpointPolicyError(
                "DNS resolution exceeded the configured deadline"
            )

        try:
            answer = resolver.resolve(
                query_name,
                record_type,
                search=False,
                lifetime=remaining,
                raise_on_no_answer=False,
            )
        except dns.resolver.LifetimeTimeout as exc:
            raise HIEEndpointPolicyError(
                "DNS resolution exceeded the configured deadline"
            ) from exc
        except dns.resolver.NXDOMAIN as exc:
            raise HIEEndpointPolicyError(
                "Approved HIE endpoint hostname does not resolve"
            ) from exc
        except (dns.exception.DNSException, OSError) as exc:
            raise HIEEndpointPolicyError(
                "DNS resolution failed for the approved HIE endpoint"
            ) from exc

        # Defend against resolver implementations that return after the
        # advertised lifetime rather than raising a timeout themselves.
        if _monotonic() >= deadline:
            raise HIEEndpointPolicyError(
                "DNS resolution exceeded the configured deadline"
            )

        if getattr(answer, "rrset", None) is None:
            # A hostname can legitimately have no AAAA records.
            continue

        for record in answer:
            address = getattr(record, "address", None)
            if not isinstance(address, str) or not address:
                raise HIEEndpointPolicyError(
                    "DNS resolver returned an invalid IP address"
                )
            addresses.append(address)

    return tuple(addresses)


def resolve_hie_endpoint_ips(
    endpoint_url: object,
    *,
    allowed_hosts: Iterable[str],
    resolver: AddressResolver | None = None,
    dns_timeout_seconds: float = DEFAULT_DNS_TIMEOUT_SECONDS,
) -> frozenset[str]:
    """Resolve an approved HIE endpoint and reject unsafe DNS answers.

    The default resolver uses dnspython and one monotonic deadline shared
    across A and AAAA. An injected resolver receives the requested timeout
    budget and is responsible for honoring it. Injection is intended for
    controlled integrations and deterministic offline tests.

    Every returned address must be globally routable. One unsafe or malformed
    answer rejects the entire set.

    This remains DNS preflight, not connection-time destination enforcement.
    Transport code must connect to a validated numeric IP and preserve TLS
    hostname verification rather than resolving the hostname again.
    """
    timeout_budget = validate_hie_dns_timeout(dns_timeout_seconds)

    normalized_url = validate_hie_endpoint_url(
        endpoint_url,
        allowed_hosts=allowed_hosts,
    )

    hostname = urlsplit(normalized_url).hostname
    if hostname is None:
        raise HIEEndpointPolicyError(
            "Approved HIE endpoint is missing a hostname"
        )

    resolve = _system_dns_resolver if resolver is None else resolver

    try:
        raw_addresses = tuple(resolve(hostname, 443, timeout_budget))
    except HIEEndpointPolicyError:
        raise
    except Exception as exc:
        raise HIEEndpointPolicyError(
            "DNS resolution failed for the approved HIE endpoint"
        ) from exc

    if not raw_addresses:
        raise HIEEndpointPolicyError(
            "DNS resolver returned no addresses for the HIE endpoint"
        )

    validated_addresses: set[str] = set()

    for value in raw_addresses:
        if not isinstance(value, str) or not value or value != value.strip():
            raise HIEEndpointPolicyError(
                "DNS resolver returned an invalid IP address"
            )

        if "%" in value:
            raise HIEEndpointPolicyError(
                "Scoped IP addresses are not permitted for HIE endpoints"
            )

        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise HIEEndpointPolicyError(
                "DNS resolver returned an invalid IP address"
            ) from exc

        if (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
        ):
            raise HIEEndpointPolicyError(
                "IPv4-mapped IPv6 addresses are not permitted"
            )

        if not address.is_global or address.is_multicast:
            raise HIEEndpointPolicyError(
                "DNS answer contains an IP address that is not globally routable"
            )

        validated_addresses.add(address.compressed)

    return frozenset(validated_addresses)


def _contains_dot_segments(path: str) -> bool:
    """Detect literal or repeatedly percent-encoded dot segments."""
    decoded = path

    while True:
        if any(segment in {".", ".."} for segment in decoded.split("/")):
            return True

        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return False

        decoded = next_decoded


def validate_hie_redirect_target(
    current_url: object,
    redirect_location: object,
    *,
    allowed_hosts: Iterable[str],
) -> str:
    """Validate one redirect target and require it to remain same-origin.

    Absolute HTTPS URLs and same-origin relative paths are supported.
    Scheme-relative redirects are rejected. Query strings, fragments,
    unsafe ports, credentials and dot-segment paths are rejected by policy.

    This function does not perform an HTTP request. A future HTTP adapter
    should disable automatic redirects and either reject redirects entirely
    or validate every hop using this function with a strict hop limit.
    """
    try:
        approved_hosts = tuple(allowed_hosts)
    except TypeError as exc:
        raise HIEEndpointPolicyError(
            "Allowed hosts must be an explicit collection of hostnames"
        ) from exc

    current = validate_hie_endpoint_url(
        current_url,
        allowed_hosts=approved_hosts,
    )

    if (
        not isinstance(redirect_location, str)
        or not redirect_location
        or redirect_location != redirect_location.strip()
        or any(
            character.isspace()
            or ord(character) < 0x20
            or ord(character) == 0x7F
            for character in redirect_location
        )
        or "\\" in redirect_location
    ):
        raise HIEEndpointPolicyError(
            "HIE redirect target is empty or contains invalid characters"
        )

    if "?" in redirect_location or "#" in redirect_location:
        raise HIEEndpointPolicyError(
            "HIE redirect target must not contain a query or fragment"
        )

    try:
        parsed_location = urlsplit(redirect_location)
    except ValueError as exc:
        raise HIEEndpointPolicyError(
            "HIE redirect target is malformed"
        ) from exc

    if parsed_location.scheme and parsed_location.scheme.lower() != "https":
        raise HIEEndpointPolicyError(
            "HIE redirect target must use HTTPS"
        )

    if parsed_location.scheme and not parsed_location.netloc:
        raise HIEEndpointPolicyError(
            "HIE redirect target has a malformed absolute URL"
        )

    if not parsed_location.scheme and parsed_location.netloc:
        raise HIEEndpointPolicyError(
            "Scheme-relative HIE redirects are not permitted"
        )

    if _contains_dot_segments(parsed_location.path):
        raise HIEEndpointPolicyError(
            "HIE redirect target path must not contain dot segments"
        )

    try:
        resolved_target = urljoin(current, redirect_location)
    except ValueError as exc:
        raise HIEEndpointPolicyError(
            "HIE redirect target is malformed"
        ) from exc

    target = validate_hie_endpoint_url(
        resolved_target,
        allowed_hosts=approved_hosts,
    )

    current_parts = urlsplit(current)
    target_parts = urlsplit(target)

    current_origin = (
        current_parts.scheme.lower(),
        current_parts.hostname,
        current_parts.port or 443,
    )
    target_origin = (
        target_parts.scheme.lower(),
        target_parts.hostname,
        target_parts.port or 443,
    )

    if target_origin != current_origin:
        raise HIEEndpointPolicyError(
            "HIE redirect target must remain on the original origin"
        )

    return target