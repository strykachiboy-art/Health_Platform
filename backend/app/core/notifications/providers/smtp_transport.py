from __future__ import annotations

import ipaddress
import math
import re
import smtplib
import socket
import ssl
from time import monotonic as _monotonic
from collections.abc import Iterable

import dns.exception
import dns.name
import dns.resolver
from flask import current_app, has_app_context

from app.core.exceptions import ValidationError


_DEFAULT_DNS_TIMEOUT_SECONDS = 2.0
_MAX_DNS_TIMEOUT_SECONDS = 5.0
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _normalize_smtp_hostname(value: object) -> str:
    """Return a canonical DNS hostname; URLs, IP literals and wildcards fail closed."""
    if not isinstance(value, str):
        raise ValidationError("SMTP destination hostname is invalid")

    hostname = value.strip()
    if (
        not hostname
        or len(hostname) > 254
        or hostname.endswith("..")
        or any(
            character.isspace()
            or ord(character) < 0x20
            or ord(character) == 0x7F
            for character in hostname
        )
    ):
        raise ValidationError("SMTP destination hostname is invalid")

    if hostname.endswith("."):
        hostname = hostname[:-1]

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValidationError("SMTP destination must use an approved DNS hostname")

    try:
        normalized = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise ValidationError("SMTP destination hostname is invalid") from exc

    if len(normalized) > 253:
        raise ValidationError("SMTP destination hostname is invalid")

    labels = normalized.split(".")
    if not labels or any(_HOST_LABEL.fullmatch(label) is None for label in labels):
        raise ValidationError("SMTP destination hostname is invalid")

    return normalized


def _config_list(value: object, *, name: str) -> tuple[object, ...]:
    if isinstance(value, str):
        if not value.strip():
            return ()
        items = tuple(value.split(","))
    elif isinstance(value, (tuple, list, set, frozenset)):
        items = tuple(value)
    else:
        raise ValidationError(f"SMTP destination {name} allowlist is invalid")

    if not items:
        return ()
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValidationError(f"SMTP destination {name} allowlist is invalid")
    return items


def _configured_allowed_hosts(value: object) -> frozenset[str]:
    items = _config_list(value, name="hostname")
    return frozenset(_normalize_smtp_hostname(item) for item in items)


def _configured_allowed_ports(value: object) -> frozenset[int]:
    if isinstance(value, str):
        if not value.strip():
            return frozenset()
        raw_ports: tuple[object, ...] = tuple(item.strip() for item in value.split(","))
        if any(not isinstance(item, str) or re.fullmatch(r"[0-9]+", item) is None for item in raw_ports):
            raise ValidationError("SMTP destination port allowlist is invalid")
        ports = tuple(int(item) for item in raw_ports)
    elif isinstance(value, (tuple, list, set, frozenset)):
        ports = tuple(value)
    else:
        raise ValidationError("SMTP destination port allowlist is invalid")

    if not ports:
        return frozenset()
    if any(
        isinstance(port, bool)
        or not isinstance(port, int)
        or not 1 <= port <= 65535
        for port in ports
    ):
        raise ValidationError("SMTP destination port allowlist is invalid")
    return frozenset(ports)


def _resolve_smtp_host_addresses(
    hostname: str,
    *,
    timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Resolve A and AAAA under one short, shared DNS deadline."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > _MAX_DNS_TIMEOUT_SECONDS
    ):
        raise ValidationError("SMTP DNS timeout configuration is invalid")

    timeout_budget = float(timeout_seconds)
    deadline = _monotonic() + timeout_budget

    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = min(float(resolver.timeout), timeout_budget)
        query_name = dns.name.from_text(hostname, origin=dns.name.root)
    except (dns.exception.DNSException, OSError, TypeError, ValueError) as exc:
        raise ValidationError("SMTP destination DNS configuration failed") from exc

    addresses: list[str] = []
    for record_type in ("A", "AAAA"):
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise ValidationError("SMTP destination DNS lookup timed out")

        try:
            answer = resolver.resolve(
                query_name,
                record_type,
                search=False,
                lifetime=remaining,
                raise_on_no_answer=False,
            )
        except dns.resolver.LifetimeTimeout as exc:
            raise ValidationError("SMTP destination DNS lookup timed out") from exc
        except dns.resolver.NXDOMAIN as exc:
            raise ValidationError("SMTP destination hostname does not resolve") from exc
        except (dns.exception.DNSException, OSError) as exc:
            raise ValidationError("SMTP destination DNS lookup failed") from exc

        if _monotonic() >= deadline:
            raise ValidationError("SMTP destination DNS lookup timed out")

        if getattr(answer, "rrset", None) is None:
            continue
        for record in answer:
            address = getattr(record, "address", None)
            if not isinstance(address, str) or not address:
                raise ValidationError("SMTP destination DNS answer is invalid")
            addresses.append(address)

    return tuple(addresses)


def _validate_smtp_destination_ips(values: Iterable[str]) -> tuple[str, ...]:
    try:
        raw_addresses = tuple(values)
    except TypeError as exc:
        raise ValidationError("SMTP destination DNS answer is invalid") from exc

    if not raw_addresses:
        raise ValidationError("SMTP destination hostname returned no addresses")

    parsed: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in raw_addresses:
        if not isinstance(value, str) or not value or value != value.strip() or "%" in value:
            raise ValidationError("SMTP destination DNS answer is invalid")

        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValidationError("SMTP destination DNS answer is invalid") from exc

        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            raise ValidationError("SMTP destination DNS answer contains a forbidden IP address")

        if (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
        ):
            # Reject the full set if even one address is unsafe. Never select
            # only the public result from a mixed public/private answer.
            raise ValidationError(
                "SMTP destination DNS answer contains an IP that is not globally routable"
            )
        parsed.add(address)

    return tuple(
        address.compressed
        for address in sorted(parsed, key=lambda item: (item.version, int(item)))
    )


def resolve_approved_smtp_destination(*, host: object, port: object) -> tuple[str, str]:
    """Enforce operator host/port policy, resolve once, and return a hostname/IP pair.

    Hostnames and ports are sourced exclusively from trusted application config.
    Clinic integration credentials cannot expand these allowlists.
    """
    if not has_app_context():
        raise ValidationError("SMTP destination policy requires application configuration")

    normalized_host = _normalize_smtp_hostname(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValidationError("SMTP destination port is invalid")

    configured_hosts = _configured_allowed_hosts(current_app.config.get("SMTP_ALLOWED_HOSTS", ""))
    configured_ports = _configured_allowed_ports(current_app.config.get("SMTP_ALLOWED_PORTS", ""))
    if not configured_hosts or not configured_ports:
        raise ValidationError("SMTP destination policy is not configured")
    if normalized_host not in configured_hosts:
        raise ValidationError("SMTP destination hostname is not approved")
    if port not in configured_ports:
        raise ValidationError("SMTP destination port is not approved")

    raw_addresses = _resolve_smtp_host_addresses(
        normalized_host,
        timeout_seconds=_DEFAULT_DNS_TIMEOUT_SECONDS,
    )
    validated_addresses = _validate_smtp_destination_ips(raw_addresses)
    # Deterministic single-IP selection; socket creation below never resolves
    # the hostname again, preventing DNS rebinding between policy and connect.
    return normalized_host, validated_addresses[0]


def _require_public_ip(value: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        if isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
            address = value
        elif isinstance(value, str) and "%" not in value:
            address = ipaddress.ip_address(value)
        else:
            raise ValueError("invalid address")
    except ValueError as exc:
        raise OSError("SMTP destination IP is invalid") from exc

    if (
        not address.is_global
        or address.is_multicast
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
    ):
        raise OSError("SMTP destination IP is not globally routable")
    return address


def _open_pinned_socket(
    destination_ip: object,
    port: int,
    timeout: object,
    source_address: tuple[str, int] | None,
) -> socket.socket:
    """Open TCP directly to a numeric IP and verify the connected peer."""
    address = _require_public_ip(destination_ip)
    raw_socket = socket.create_connection(
        (address.compressed, port),
        timeout=timeout,
        source_address=source_address,
    )
    try:
        peer = raw_socket.getpeername()
        if not isinstance(peer, tuple) or not peer:
            raise OSError("SMTP connected peer address could not be verified")
        try:
            peer_ip = ipaddress.ip_address(peer[0])
        except ValueError as exc:
            raise OSError("SMTP connected peer address could not be verified") from exc
        if peer_ip != address:
            raise OSError("SMTP connected peer did not match the validated destination IP")
        return raw_socket
    except Exception:
        raw_socket.close()
        raise


class PinnedSMTP(smtplib.SMTP):
    """SMTP client whose TCP connection is pinned to a validated numeric IP."""

    def __init__(self, host: str, port: int = 0, *, destination_ip: str, timeout: float = 30.0):
        self._destination_ip = _require_public_ip(destination_ip)
        super().__init__(host=host, port=port, timeout=timeout)

    def _get_socket(self, host, port, timeout):
        return _open_pinned_socket(
            self._destination_ip,
            port,
            timeout,
            self.source_address,
        )


class PinnedSMTP_SSL(smtplib.SMTP_SSL):
    """Implicit-TLS SMTP client pinned to a validated IP with hostname verification."""

    def __init__(
        self,
        host: str,
        port: int = 0,
        *,
        destination_ip: str,
        timeout: float = 30.0,
        context: ssl.SSLContext | None = None,
    ):
        self._destination_ip = _require_public_ip(destination_ip)
        super().__init__(host=host, port=port, timeout=timeout, context=context)

    def _get_socket(self, host, port, timeout):
        raw_socket = _open_pinned_socket(
            self._destination_ip,
            port,
            timeout,
            self.source_address,
        )
        try:
            # Keep the configured SMTP hostname for SNI and certificate checking.
            return self.context.wrap_socket(
                raw_socket,
                server_hostname=self._host,
            )
        except Exception:
            raw_socket.close()
            raise
