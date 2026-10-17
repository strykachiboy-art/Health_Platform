from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import unquote, urljoin, urlsplit

from app.modules.hie.providers.endpoint_policy import (
    HIEEndpointPolicyError,
    validate_hie_endpoint_url,
)


AddressResolver = Callable[[str, int], Iterable[str]]


def _system_dns_resolver(hostname: str, port: int) -> tuple[str, ...]:
    """Return socket addresses from the system resolver.

    This performs DNS resolution only. It does not open a socket or send
    a network request to the returned addresses.
    """
    try:
        records = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise HIEEndpointPolicyError(
            "DNS resolution failed for the approved HIE endpoint"
        ) from exc

    addresses: list[str] = []

    for record in records:
        try:
            family = record[0]
            sockaddr = record[4]
            address = sockaddr[0]
        except (IndexError, TypeError) as exc:
            raise HIEEndpointPolicyError(
                "DNS resolver returned a malformed address record"
            ) from exc

        if family not in (socket.AF_INET, socket.AF_INET6):
            raise HIEEndpointPolicyError(
                "DNS resolver returned an unsupported address family"
            )

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
) -> frozenset[str]:
    """Resolve an approved HIE endpoint and reject unsafe DNS answers.

    Every returned address must be a globally routable IP address. One
    unsafe or malformed answer rejects the entire DNS response.

    A resolver can be injected for deterministic offline tests.

    SECURITY LIMITATION:
    This is DNS preflight validation, not connection-time destination
    enforcement. A later HTTP client that independently resolves the
    hostname could connect to a different IP. Do not use this result as
    proof that a future connection is safe or as a substitute for transport
    controls that validate or pin the actual connected destination.
    """
    normalized_url = validate_hie_endpoint_url(
        endpoint_url,
        allowed_hosts=allowed_hosts,
    )

    hostname = urlsplit(normalized_url).hostname
    if hostname is None:
        # The endpoint validator should make this unreachable. Retain
        # the fail-closed check at the boundary.
        raise HIEEndpointPolicyError(
            "Approved HIE endpoint is missing a hostname"
        )

    resolve = _system_dns_resolver if resolver is None else resolver

    try:
        raw_addresses = tuple(resolve(hostname, 443))
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

        # Scoped IPv6 addresses are not acceptable as public destinations.
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