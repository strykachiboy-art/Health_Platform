from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


_DNS_LABEL = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_NUMERIC_HOST = re.compile(r"[0-9.]+")
_NUMERIC_OR_HEX_IP_LABEL = re.compile(r"(?:0x[0-9a-f]+|[0-9]+)")
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".test",
    ".invalid",
    ".example",
)
_BLOCKED_SINGLE_LABELS = frozenset(
    {
        "localhost",
        "local",
        "internal",
        "lan",
        "home",
        "test",
        "invalid",
        "example",
    }
)


class HIEEndpointPolicyError(ValueError):
    """Raised when an HIE endpoint violates the outbound endpoint policy."""


def _normalize_dns_hostname(
    value: Any,
    *,
    description: str,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise HIEEndpointPolicyError(
            f"{description} must be a DNS hostname"
        )

    host = value.lower()
    if any(
        character.isspace()
        or ord(character) < 0x20
        or ord(character) == 0x7F
        for character in host
    ):
        raise HIEEndpointPolicyError(
            f"{description} contains invalid characters"
        )
    if host.endswith("."):
        raise HIEEndpointPolicyError(
            f"{description} must not have a trailing dot"
        )

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise HIEEndpointPolicyError(
            f"{description} must not be an IP literal"
        )

    # Reject non-canonical numeric IP forms (for example, 127.1 or a single
    # decimal integer) before treating a value as a DNS name.
    if _NUMERIC_HOST.fullmatch(host) or all(
        _NUMERIC_OR_HEX_IP_LABEL.fullmatch(label)
        for label in host.split(".")
    ):
        raise HIEEndpointPolicyError(
            f"{description} must not be an encoded IP address"
        )

    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise HIEEndpointPolicyError(
            f"{description} is not a valid DNS hostname"
        ) from exc

    if len(ascii_host) > 253 or "." not in ascii_host:
        raise HIEEndpointPolicyError(
            f"{description} must be a fully qualified DNS hostname"
        )

    labels = ascii_host.split(".")
    if any(
        len(label) > 63 or _DNS_LABEL.fullmatch(label) is None
        for label in labels
    ):
        raise HIEEndpointPolicyError(
            f"{description} is not a valid DNS hostname"
        )

    if ascii_host in _BLOCKED_SINGLE_LABELS or any(
        ascii_host.endswith(suffix) for suffix in _BLOCKED_SUFFIXES
    ):
        raise HIEEndpointPolicyError(
            f"{description} uses a non-public hostname suffix"
        )

    return ascii_host


def _normalize_allowed_hosts(
    allowed_hosts: Iterable[str],
) -> frozenset[str]:
    if isinstance(allowed_hosts, (str, bytes)) or allowed_hosts is None:
        raise HIEEndpointPolicyError(
            "Allowed hosts must be an explicit collection of hostnames"
        )

    try:
        candidates = tuple(allowed_hosts)
    except TypeError as exc:
        raise HIEEndpointPolicyError(
            "Allowed hosts must be an explicit collection of hostnames"
        ) from exc

    normalized = frozenset(
        _normalize_dns_hostname(host, description="Allowed host")
        for host in candidates
    )
    if not normalized:
        raise HIEEndpointPolicyError(
            "At least one approved HIE endpoint host is required"
        )
    return normalized


def validate_hie_endpoint_url(
    endpoint_url: Any,
    *,
    allowed_hosts: Iterable[str],
) -> str:
    """Validate and normalize an HIE HTTPS base URL against an exact host allowlist.

    This is a URL/configuration policy primitive, not a complete SSRF defense.
    It performs no DNS lookup and no HTTP request. Any future HTTP adapter must
    also constrain DNS resolution/connected IPs, disable redirects or revalidate
    every redirect target, apply timeouts/TLS verification, and use network egress
    controls before sending a request or credential.
    """
    approved_hosts = _normalize_allowed_hosts(allowed_hosts)

    if not isinstance(endpoint_url, str) or not endpoint_url:
        raise HIEEndpointPolicyError("HIE endpoint URL is required")
    if endpoint_url != endpoint_url.strip() or any(
        character.isspace()
        or ord(character) < 0x20
        or ord(character) == 0x7F
        for character in endpoint_url
    ):
        raise HIEEndpointPolicyError(
            "HIE endpoint URL contains whitespace or control characters"
        )
    if "\\" in endpoint_url:
        raise HIEEndpointPolicyError("HIE endpoint URL must not contain backslashes")
    if "?" in endpoint_url or "#" in endpoint_url:
        raise HIEEndpointPolicyError(
            "HIE endpoint URL must not contain a query or fragment"
        )

    try:
        parsed = urlsplit(endpoint_url)
        port = parsed.port
    except ValueError as exc:
        raise HIEEndpointPolicyError("HIE endpoint URL is malformed") from exc

    if parsed.scheme.lower() != "https":
        raise HIEEndpointPolicyError("HIE endpoint URL must use HTTPS")
    if not parsed.netloc or not parsed.hostname:
        raise HIEEndpointPolicyError("HIE endpoint URL must include a hostname")
    if (
        parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
    ):
        raise HIEEndpointPolicyError("HIE endpoint URL must not contain user information")
    if parsed.netloc.endswith(":"):
        raise HIEEndpointPolicyError("HIE endpoint URL contains an invalid port")
    if port not in (None, 443):
        raise HIEEndpointPolicyError("HIE endpoint URL must use the default HTTPS port")

    hostname = _normalize_dns_hostname(
        parsed.hostname,
        description="HIE endpoint host",
    )
    if hostname not in approved_hosts:
        raise HIEEndpointPolicyError(
            "HIE endpoint host is not explicitly approved"
        )

    # Dot-segments may be interpreted differently by intermediaries. Endpoint
    # paths are fixed configuration, so reject them rather than canonicalizing.
    decoded_path = unquote(parsed.path)
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise HIEEndpointPolicyError(
            "HIE endpoint path must not contain dot segments"
        )

    # Return a canonical HTTPS URL with a normalized host and no explicit :443.
    return urlunsplit(("https", hostname, parsed.path, "", ""))
