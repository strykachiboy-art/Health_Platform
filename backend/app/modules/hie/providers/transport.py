from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import ssl
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from urllib.parse import urlencode, unquote, urlsplit

from app.modules.hie.providers.destination_policy import (
    AddressResolver,
    DEFAULT_DNS_TIMEOUT_SECONDS,
    resolve_hie_endpoint_ips,
    validate_hie_dns_timeout,
)

from app.modules.hie.providers.endpoint_policy import (
    HIEEndpointPolicyError,
    validate_hie_endpoint_url,
)


_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "connection",
        "content-length",
        "transfer-encoding",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "te",
        "trailer",
        "expect",
        "accept-encoding",
    }
)

_MAX_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_PATH_LENGTH = 4096
_MAX_QUERY_LENGTH = 8192


class HIETransportError(RuntimeError):
    """Raised when a safe HIE HTTPS transport operation cannot complete."""


class HIERedirectRejectedError(HIETransportError):
    """Raised when an HIE endpoint responds with an HTTP redirect."""


@dataclass(frozen=True)
class HIEHTTPResponse:
    """Bounded HTTP response metadata and body from the pinned transport."""

    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    connected_ip: str


def _validate_timeout(value: object, *, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        or value > _MAX_TIMEOUT_SECONDS
    ):
        raise ValueError(
            f"{name} must be greater than zero and at most "
            f"{_MAX_TIMEOUT_SECONDS:g} seconds"
        )

    return float(value)


def _contains_encoded_dot_segments(path: str) -> bool:
    """Reject literal and repeatedly percent-encoded dot segments."""
    decoded = path

    # A small bounded number of decoding rounds avoids unbounded work on
    # deliberately nested encodings while rejecting ambiguous paths closed.
    for _ in range(8):
        if any(segment in {".", ".."} for segment in decoded.split("/")):
            return True

        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return False

        decoded = next_decoded

    return True


def _normalize_request_path(path: object) -> str:
    if not isinstance(path, str):
        raise HIEEndpointPolicyError("HIE request path must be a string")

    if len(path) > _MAX_PATH_LENGTH:
        raise HIEEndpointPolicyError("HIE request path is too long")

    if any(
        character.isspace()
        or ord(character) < 0x20
        or ord(character) == 0x7F
        for character in path
    ) or "\\" in path:
        raise HIEEndpointPolicyError(
            "HIE request path contains invalid characters"
        )

    if "?" in path or "#" in path:
        raise HIEEndpointPolicyError(
            "Supply HIE search parameters separately from the request path"
        )

    if path.startswith("//"):
        raise HIEEndpointPolicyError(
            "Scheme-relative HIE request paths are not permitted"
        )

    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise HIEEndpointPolicyError(
            "HIE request path must be relative to the configured endpoint"
        )

    if _contains_encoded_dot_segments(path):
        raise HIEEndpointPolicyError(
            "HIE request path must not contain dot segments"
        )

    return path.lstrip("/")


def _normalize_query_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, str):
        if any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise HIEEndpointPolicyError(
                "HIE query parameter contains control characters"
            )
        return value

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float) and math.isfinite(value):
        return str(value)

    raise HIEEndpointPolicyError(
        "HIE query parameters must use string or finite numeric values"
    )


def _normalize_query_params(
    params: Mapping[str, object] | None,
) -> dict[str, str | list[str]]:
    if params is None:
        return {}

    if not isinstance(params, Mapping):
        raise HIEEndpointPolicyError(
            "HIE query parameters must be a mapping"
        )

    normalized: dict[str, str | list[str]] = {}

    for key, value in params.items():
        if (
            not isinstance(key, str)
            or not key
            or any(
                character.isspace()
                or ord(character) < 0x20
                or ord(character) == 0x7F
                for character in key
            )
        ):
            raise HIEEndpointPolicyError(
                "HIE query parameter name is invalid"
            )

        if value is None:
            continue

        if isinstance(value, (list, tuple)):
            normalized[key] = [
                _normalize_query_scalar(item) for item in value
            ]
        else:
            normalized[key] = _normalize_query_scalar(value)

    return normalized


def _normalize_headers(
    headers: Mapping[str, str] | None,
) -> dict[str, str]:
    normalized: dict[str, tuple[str, str]] = {
        "accept": ("Accept", "application/fhir+json, application/json"),
        "accept-encoding": ("Accept-Encoding", "identity"),
        "connection": ("Connection", "close"),
    }

    if headers is None:
        return {name: value for name, value in normalized.values()}

    if not isinstance(headers, Mapping):
        raise HIEEndpointPolicyError("HIE request headers must be a mapping")

    seen: set[str] = set()

    for name, value in headers.items():
        if not isinstance(name, str) or _HEADER_NAME.fullmatch(name) is None:
            raise HIEEndpointPolicyError("HIE request header name is invalid")

        key = name.lower()

        if key in seen:
            raise HIEEndpointPolicyError(
                "Duplicate HIE request header names are not permitted"
            )
        seen.add(key)

        if key in _FORBIDDEN_HEADERS:
            raise HIEEndpointPolicyError(
                "HIE request cannot override a transport-controlled header"
            )

        if not isinstance(value, str) or any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        ):
            raise HIEEndpointPolicyError(
                "HIE request header value contains invalid characters"
            )

        normalized[key] = (name, value)

    return {name: value for name, value in normalized.values()}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Direct HTTPS connection to a validated numeric IP.

    The URL hostname remains the HTTP host and TLS server_hostname. Only the
    TCP destination is pinned to the selected, validated IP address. No proxy
    or hostname-based connection is used by this class.
    """

    def __init__(
        self,
        *,
        hostname: str,
        destination_ip: str,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        self._destination_ip = ipaddress.ip_address(destination_ip)
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout

        context = ssl.create_default_context()

        if (
            context.check_hostname is not True
            or context.verify_mode != ssl.CERT_REQUIRED
        ):
            raise HIETransportError(
                "TLS certificate and hostname verification must be enabled"
            )

        super().__init__(
            host=hostname,
            port=443,
            timeout=connect_timeout,
            context=context,
        )

    def connect(self) -> None:
        if self._tunnel_host:
            raise HIETransportError(
                "Proxy tunneling is not supported by the pinned HIE transport"
            )

        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None

        try:
            # The destination here is a numeric, previously validated IP.
            # The hostname is not used for a second DNS lookup.
            raw_socket = socket.create_connection(
                (self._destination_ip.compressed, self.port),
                timeout=self._connect_timeout,
                source_address=self.source_address,
            )

            peer = raw_socket.getpeername()
            actual_peer = ipaddress.ip_address(peer[0])

            if actual_peer != self._destination_ip:
                raise HIETransportError(
                    "Connected peer did not match the validated HIE destination"
                )

            # Preserve original-host TLS SNI and certificate hostname checking.
            tls_socket = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
            tls_socket.settimeout(self._read_timeout)
            self.sock = tls_socket

        except Exception:
            if tls_socket is not None:
                tls_socket.close()
            elif raw_socket is not None:
                raw_socket.close()

            self.sock = None
            raise


class HIEHTTPTransport:
    """Strict read-only HTTPS transport for a trusted HIE host allowlist.

    This primitive is not automatically registered as an HIE provider.
    Only GET and HEAD are supported. HTTP redirects are rejected rather than
    followed. Connection attempts are pinned to validated numeric IPs while
    TLS authenticates the configured hostname.

    The allowed host list must be supplied from trusted operator/deployment
    configuration, never from a tenant-controlled request.
    """

    def __init__(
        self,
        allowed_hosts: Iterable[str],
        *,
        resolver: AddressResolver | None = None,
        dns_timeout_seconds: float = DEFAULT_DNS_TIMEOUT_SECONDS,
        connect_timeout: float = 3.0,

        read_timeout: float = 10.0,
        max_response_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        if allowed_hosts is None or isinstance(allowed_hosts, (str, bytes)):
            raise HIEEndpointPolicyError(
                "Allowed hosts must be an explicit collection of hostnames"
            )

        try:
            self._allowed_hosts = tuple(allowed_hosts)
        except TypeError as exc:
            raise HIEEndpointPolicyError(
                "Allowed hosts must be an explicit collection of hostnames"
            ) from exc

        if not self._allowed_hosts:
            raise HIEEndpointPolicyError(
                "At least one approved HIE endpoint host is required"
            )

        self._resolver = resolver
        self._dns_timeout_seconds = validate_hie_dns_timeout(dns_timeout_seconds)
        self._connect_timeout = _validate_timeout(
            connect_timeout,
            name="connect_timeout",
        )
        self._read_timeout = _validate_timeout(
            read_timeout,
            name="read_timeout",
        )

        if (
            isinstance(max_response_bytes, bool)
            or not isinstance(max_response_bytes, int)
            or not 1 <= max_response_bytes <= _MAX_RESPONSE_BYTES
        ):
            raise ValueError(
                f"max_response_bytes must be between 1 and "
                f"{_MAX_RESPONSE_BYTES} bytes"
            )

        self._max_response_bytes = max_response_bytes

    def request(
        self,
        *,
        endpoint_url: object,
        path: str = "",
        params: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
        method: str = "GET",
    ) -> HIEHTTPResponse:
        """Make one bounded, read-only HTTPS request.

        No retries occur after request transmission begins. Redirect responses
        are rejected and never automatically followed.
        """
        if not isinstance(method, str) or method.upper() not in {"GET", "HEAD"}:
            raise HIEEndpointPolicyError(
                "Pinned HIE transport currently supports GET and HEAD only"
            )
        normalized_method = method.upper()

        normalized_url = validate_hie_endpoint_url(
            endpoint_url,
            allowed_hosts=self._allowed_hosts,
        )
        url_parts = urlsplit(normalized_url)
        hostname = url_parts.hostname

        if hostname is None:
            raise HIEEndpointPolicyError(
                "Approved HIE endpoint is missing a hostname"
            )

        relative_path = _normalize_request_path(path)
        base_path = url_parts.path

        if relative_path:
            request_path = (
                f"{base_path.rstrip('/')}/{relative_path}"
                if base_path
                else f"/{relative_path}"
            )
        else:
            request_path = base_path or "/"

        if not request_path.startswith("/"):
            request_path = f"/{request_path}"

        if _contains_encoded_dot_segments(request_path):
            raise HIEEndpointPolicyError(
                "Combined HIE request path must not contain dot segments"
            )

        query_params = _normalize_query_params(params)
        query = urlencode(query_params, doseq=True)

        if len(query) > _MAX_QUERY_LENGTH:
            raise HIEEndpointPolicyError(
                "Encoded HIE query string exceeds the configured limit"
            )

        request_target = (
            f"{request_path}?{query}" if query else request_path
        )
        request_headers = _normalize_headers(headers)

        resolved_ips = resolve_hie_endpoint_ips(
            normalized_url,
            allowed_hosts=self._allowed_hosts,
            resolver=self._resolver,
            dns_timeout_seconds=self._dns_timeout_seconds,
        )


        # Deterministically prefer IPv4, then IPv6. All returned addresses
        # were required to pass validation before any connection is attempted.
        candidates = sorted(
            resolved_ips,
            key=lambda value: (
                ipaddress.ip_address(value).version != 4,
                ipaddress.ip_address(value).packed,
            ),
        )

        if not candidates:
            raise HIEEndpointPolicyError(
                "No validated HIE destination addresses were returned"
            )

        connection: _PinnedHTTPSConnection | None = None
        connected_ip: str | None = None
        last_connect_error: Exception | None = None

        # Retry only TCP/TLS establishment failures. No request has been
        # transmitted yet when this loop runs.
        for candidate_ip in candidates:
            candidate = _PinnedHTTPSConnection(
                hostname=hostname,
                destination_ip=candidate_ip,
                connect_timeout=self._connect_timeout,
                read_timeout=self._read_timeout,
            )

            try:
                candidate.connect()
            except (OSError, ssl.SSLError, HIETransportError) as exc:
                candidate.close()
                last_connect_error = exc
                continue

            connection = candidate
            connected_ip = candidate_ip
            break

        if connection is None or connected_ip is None:
            raise HIETransportError(
                "Could not establish a verified TLS connection to an "
                "approved HIE destination"
            ) from last_connect_error

        try:
            connection.request(
                normalized_method,
                request_target,
                headers=request_headers,
            )

            response = connection.getresponse()

            if 300 <= response.status < 400:
                raise HIERedirectRejectedError(
                    "HIE transport rejected an HTTP redirect; redirects are "
                    "not followed automatically"
                )

            chunks: list[bytes] = []
            total = 0

            while True:
                chunk = response.read(
                    min(64 * 1024, self._max_response_bytes - total + 1)
                )

                if not chunk:
                    break

                total += len(chunk)

                if total > self._max_response_bytes:
                    raise HIETransportError(
                        "HIE response exceeded the configured body-size limit"
                    )

                chunks.append(chunk)

            return HIEHTTPResponse(
                status_code=response.status,
                headers=tuple(
                    (str(name), str(value))
                    for name, value in response.getheaders()
                ),
                body=b"".join(chunks),
                connected_ip=connected_ip,
            )

        except HIETransportError:
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            raise HIETransportError("HIE HTTP request failed") from exc
        finally:
            connection.close()