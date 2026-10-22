from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import dns.exception
import dns.name
import dns.resolver
import requests as _requests_library

from app.modules.billing.services.gateways.response_utils import (
    MAX_PROVIDER_RESPONSE_BYTES,
    ProviderResponseTooLargeError,
)


_PROVIDER_HOSTS = {
    "paystack": "api.paystack.co",
    "flutterwave": "api.flutterwave.com",
    "stripe": "api.stripe.com",
}
_DEFAULT_DNS_TIMEOUT_SECONDS = 2.0
_MAX_DNS_TIMEOUT_SECONDS = 5.0
_DEFAULT_CONNECT_TIMEOUT_SECONDS = 5.0
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_PROVIDER_ADDRESSES = 16
_CHUNK_SIZE = 64 * 1024
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "te",
        "trailer",
        "expect",
    }
)
_MISSING = object()


class ProviderTransportError(_requests_library.RequestException):
    """A payment-provider request was refused or could not be safely completed."""


class ProviderHTTPResponse:
    """Requests-like response over a body already read under a strict byte cap."""

    def __init__(self, *, status_code: int, headers: Mapping[str, str], body: bytes):
        self.status_code = status_code
        self.headers = dict(headers)
        self.body = body
        self.ok = 200 <= status_code < 400
        self.closed = False

    def iter_content(self, chunk_size: int = _CHUNK_SIZE):
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size <= 0:
            raise ValueError("Response chunk size must be a positive integer")
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def json(self) -> Any:
        return json.loads(self.body)

    def close(self) -> None:
        self.closed = True


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS with numeric-IP TCP connect and original-host TLS validation."""

    def __init__(
        self,
        *,
        hostname: str,
        destination_ip: str,
        port: int,
        connect_timeout: float,
        read_timeout: float,
        ca_bundle_path: str | None = None,
    ) -> None:
        self._destination_ip = ipaddress.ip_address(destination_ip)
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        try:
            context = ssl.create_default_context(cafile=ca_bundle_path)
        except (OSError, ssl.SSLError, TypeError, ValueError) as exc:
            raise ProviderTransportError("Payment-provider TLS trust configuration is invalid") from exc
        if context.check_hostname is not True or context.verify_mode != ssl.CERT_REQUIRED:
            raise ProviderTransportError(
                "Payment-provider TLS certificate and hostname verification are required"
            )
        super().__init__(
            host=hostname,
            port=port,
            timeout=connect_timeout,
            context=context,
        )

    def connect(self) -> None:
        if self._tunnel_host:
            raise ProviderTransportError("Proxy tunneling is not supported")

        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            # Numeric IP only: this socket call cannot perform a second DNS lookup.
            raw_socket = socket.create_connection(
                (self._destination_ip.compressed, self.port),
                timeout=self._connect_timeout,
                source_address=self.source_address,
            )
            peer = raw_socket.getpeername()
            if not isinstance(peer, tuple) or not peer:
                raise ProviderTransportError("Connected peer address could not be verified")
            try:
                peer_ip = ipaddress.ip_address(peer[0])
            except ValueError as exc:
                raise ProviderTransportError("Connected peer address could not be verified") from exc
            if peer_ip != self._destination_ip:
                raise ProviderTransportError(
                    "Connected peer does not match the validated payment-provider IP"
                )

            # TLS SNI and certificate hostname verification use the provider hostname,
            # not the numeric address used for the TCP connection.
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


def _validate_timeout(value: object) -> tuple[float, float]:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise ProviderTransportError("Payment-provider timeout is invalid")
        connect_value, read_value = value
    else:
        connect_value = read_value = value

    values = (connect_value, read_value)
    if any(
        isinstance(item, bool)
        or not isinstance(item, (int, float))
        or not math.isfinite(item)
        or item <= 0
        or item > _MAX_TIMEOUT_SECONDS
        for item in values
    ):
        raise ProviderTransportError("Payment-provider timeout must be positive and bounded")

    return min(float(connect_value), _DEFAULT_CONNECT_TIMEOUT_SECONDS), min(
        float(read_value), _MAX_TIMEOUT_SECONDS
    )


def _validate_provider_url(provider: str, url: object) -> tuple[str, int, str]:
    expected_host = _PROVIDER_HOSTS.get(provider)
    if expected_host is None:
        raise ProviderTransportError("Unknown payment provider transport")
    if not isinstance(url, str) or not url or url != url.strip():
        raise ProviderTransportError("Payment-provider URL is invalid")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url) or "\\" in url:
        raise ProviderTransportError("Payment-provider URL contains invalid characters")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ProviderTransportError("Payment-provider URL is malformed") from exc

    if parsed.scheme.lower() != "https":
        raise ProviderTransportError("Payment-provider URL must use HTTPS")
    if not parsed.netloc or not parsed.hostname:
        raise ProviderTransportError("Payment-provider URL must include a hostname")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ProviderTransportError("Payment-provider URL must not contain user information")
    if parsed.netloc.endswith(":") or port not in (None, 443):
        raise ProviderTransportError("Payment-provider URL must use the default HTTPS port")
    if parsed.hostname.lower() != expected_host:
        raise ProviderTransportError("Payment-provider hostname is not approved")
    if parsed.fragment:
        raise ProviderTransportError("Payment-provider URL must not contain a fragment")

    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return expected_host, 443, target


def _validate_provider_addresses(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise ProviderTransportError("Payment-provider DNS answer is invalid")
    try:
        candidates = tuple(values)
    except TypeError as exc:
        raise ProviderTransportError("Payment-provider DNS answer is invalid") from exc
    if not candidates:
        raise ProviderTransportError("Payment-provider hostname returned no addresses")
    if len(candidates) > _MAX_PROVIDER_ADDRESSES:
        raise ProviderTransportError("Payment-provider DNS answer has too many addresses")

    parsed: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in candidates:
        if not isinstance(value, str) or not value or value != value.strip() or "%" in value:
            raise ProviderTransportError("Payment-provider DNS answer is invalid")
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise ProviderTransportError("Payment-provider DNS answer is invalid") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
        ):
            # Reject every answer if even one destination is unsafe; do not pick
            # a public IP from a mixed public/private DNS response.
            raise ProviderTransportError(
                "Payment-provider DNS answer contains a non-public IP address"
            )
        parsed.add(address)

    return tuple(
        address.compressed
        for address in sorted(parsed, key=lambda item: (item.version, int(item)))
    )


def resolve_provider_host_addresses(
    hostname: str,
    *,
    timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Resolve A/AAAA under a short shared DNS deadline and reject unsafe answers."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > _MAX_DNS_TIMEOUT_SECONDS
    ):
        raise ProviderTransportError("Payment-provider DNS timeout is invalid")

    deadline = time.monotonic() + float(timeout_seconds)
    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = min(float(resolver.timeout), float(timeout_seconds))
        query_name = dns.name.from_text(hostname, origin=dns.name.root)
    except (dns.exception.DNSException, OSError, TypeError, ValueError) as exc:
        raise ProviderTransportError("Payment-provider DNS configuration failed") from exc

    addresses: list[str] = []
    for record_type in ("A", "AAAA"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderTransportError("Payment-provider DNS lookup timed out")
        try:
            answer = resolver.resolve(
                query_name,
                record_type,
                search=False,
                lifetime=remaining,
                raise_on_no_answer=False,
            )
        except dns.resolver.NoAnswer:
            continue
        except dns.resolver.NXDOMAIN as exc:
            raise ProviderTransportError("Payment-provider hostname does not resolve") from exc
        except dns.resolver.LifetimeTimeout as exc:
            raise ProviderTransportError("Payment-provider DNS lookup timed out") from exc
        except (dns.exception.DNSException, OSError) as exc:
            raise ProviderTransportError("Payment-provider DNS lookup failed") from exc

        if time.monotonic() > deadline:
            raise ProviderTransportError("Payment-provider DNS lookup timed out")
        if getattr(answer, "rrset", None) is None:
            continue
        for record in answer:
            address = getattr(record, "address", None)
            if not isinstance(address, str) or not address:
                raise ProviderTransportError("Payment-provider DNS answer is invalid")
            addresses.append(address)
            if len(addresses) > _MAX_PROVIDER_ADDRESSES:
                raise ProviderTransportError("Payment-provider DNS answer has too many addresses")

    return _validate_provider_addresses(addresses)


def _normalize_request_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        candidates: dict[str, str] = {}
    elif isinstance(headers, Mapping):
        candidates = dict(headers)
    else:
        raise ProviderTransportError("Payment-provider request headers are invalid")

    normalized: dict[str, tuple[str, str]] = {}
    for name, value in candidates.items():
        if not isinstance(name, str) or _HEADER_NAME.fullmatch(name) is None:
            raise ProviderTransportError("Payment-provider request header name is invalid")
        if not isinstance(value, str) or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in value
        ):
            raise ProviderTransportError("Payment-provider request header value is invalid")
        lowered = name.lower()
        if lowered in normalized:
            raise ProviderTransportError("Duplicate payment-provider request headers are not allowed")
        if lowered in _FORBIDDEN_HEADERS:
            raise ProviderTransportError("Payment-provider request overrides a transport-controlled header")
        # The transport deliberately asks for identity encoding; it bounds the decoded
        # bytes and does not delegate decompression to a proxy/session.
        if lowered == "accept-encoding":
            continue
        if lowered == "connection":
            raise ProviderTransportError("Payment-provider Connection header is transport-controlled")
        normalized[lowered] = (name, value)

    result = {original_name: value for original_name, value in normalized.values()}
    result["Accept-Encoding"] = "identity"
    result["Connection"] = "close"
    return result


def _read_limited_body(response: http.client.HTTPResponse) -> bytes:
    raw_length = response.getheader("Content-Length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length.strip())
        except (AttributeError, TypeError, ValueError, OverflowError):
            declared_length = None
        if declared_length is not None and declared_length > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderResponseTooLargeError("Provider response exceeded the byte limit")

    content_encoding = response.getheader("Content-Encoding")
    if content_encoding and content_encoding.strip().lower() not in {"identity"}:
        raise ProviderTransportError("Compressed provider responses are not accepted")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(_CHUNK_SIZE, MAX_PROVIDER_RESPONSE_BYTES - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderResponseTooLargeError("Provider response exceeded the byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def request_payment_provider(
    *,
    provider: str,
    method: str,
    url: str,
    headers: Mapping[str, str] | None,
    data: object = None,
    timeout: object = 30,
    verify_ssl_certs: bool = True,
    proxy: object = None,
    ca_bundle_path: str | None = None,
) -> ProviderHTTPResponse:
    """Issue one non-proxied HTTPS request to a fixed, DNS-pinned provider host.

    Only GET and POST are supported. DNS results are checked in full, TCP connects
    to a numeric approved public IP, and TLS verifies the original provider hostname.
    No redirects or post-transmission request retries are followed.
    """
    hostname, port, target = _validate_provider_url(provider, url)
    method = method.upper() if isinstance(method, str) else ""
    if method not in {"GET", "POST"}:
        raise ProviderTransportError("Payment-provider HTTP method is not supported")
    if not verify_ssl_certs:
        raise ProviderTransportError("Payment-provider TLS verification cannot be disabled")
    if proxy:
        raise ProviderTransportError("Explicit payment-provider proxies are not supported")
    if ca_bundle_path is not None and not isinstance(ca_bundle_path, str):
        raise ProviderTransportError("Payment-provider TLS trust path is invalid")
    connect_timeout, read_timeout = _validate_timeout(timeout)

    if data is None:
        body = None
    elif isinstance(data, str):
        body = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        body = bytes(data)
    else:
        raise ProviderTransportError("Payment-provider request body must be encoded text or bytes")
    if method == "GET" and body not in (None, b""):
        raise ProviderTransportError("GET requests cannot contain a request body")

    request_headers = _normalize_request_headers(headers)
    addresses = resolve_provider_host_addresses(hostname)

    connection: _PinnedHTTPSConnection | None = None
    last_connect_error: BaseException | None = None
    # Alternate addresses are tried only while establishing the TLS connection,
    # before any HTTP request bytes are transmitted.
    for address in addresses:
        candidate = _PinnedHTTPSConnection(
            hostname=hostname,
            destination_ip=address,
            port=port,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            ca_bundle_path=ca_bundle_path,
        )
        try:
            candidate.connect()
        except ProviderTransportError:
            candidate.close()
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            candidate.close()
            last_connect_error = exc
            continue
        connection = candidate
        break

    if connection is None:
        raise ProviderTransportError(
            "Could not establish TLS to an approved payment-provider destination"
        ) from last_connect_error

    try:
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = _read_limited_body(response)
        response_headers = {str(name): str(value) for name, value in response.getheaders()}
        return ProviderHTTPResponse(
            status_code=response.status,
            headers=response_headers,
            body=response_body,
        )
    except ProviderResponseTooLargeError:
        raise
    except ProviderTransportError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        # Once request transmission starts, never retry: a POST may have succeeded
        # remotely even when reading its response fails.
        raise ProviderTransportError("Payment-provider HTTPS request failed") from exc
    finally:
        connection.close()


class _ProviderRequestsFacade:
    """Compatibility surface for existing gateway call sites and monkeypatches."""

    def __init__(self, provider: str, requests_module: Any):
        self._provider = provider
        self.RequestException = requests_module.RequestException
        self.Response = requests_module.Response
        self.exceptions = requests_module.exceptions

    def get(self, url: str, **kwargs: Any) -> ProviderHTTPResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> ProviderHTTPResponse:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> ProviderHTTPResponse:
        headers = kwargs.pop("headers", None)
        timeout = kwargs.pop("timeout", 30)
        stream = kwargs.pop("stream", True)
        allow_redirects = kwargs.pop("allow_redirects", False)
        body = kwargs.pop("data", None)
        json_body = kwargs.pop("json", _MISSING)

        if kwargs:
            raise ProviderTransportError("Unsupported payment-provider request options")
        if stream is not True:
            raise ProviderTransportError("Payment-provider responses must use bounded streaming")
        if allow_redirects is not False:
            raise ProviderTransportError("Payment-provider redirects are disabled")
        if body is not None and json_body is not _MISSING:
            raise ProviderTransportError("Supply either data or JSON, not both")
        if json_body is not _MISSING:
            try:
                body = json.dumps(
                    json_body,
                    separators=(",", ":"),
                    ensure_ascii=True,
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise ProviderTransportError("Payment-provider JSON body is invalid") from exc
            header_names = {name.lower() for name in (headers or {}) if isinstance(name, str)}
            if "content-type" not in header_names:
                headers = dict(headers or {})
                headers["Content-Type"] = "application/json"

        try:
            return request_payment_provider(
                provider=self._provider,
                method=method,
                url=url,
                headers=headers,
                data=body,
                timeout=timeout,
            )
        except ProviderResponseTooLargeError as exc:
            # Requests-compatible callers classify this as an unknown outcome
            # because the provider may have processed the request already.
            raise ProviderTransportError("Payment-provider response exceeded the byte limit") from exc


def requests_for_provider(provider: str, requests_module: Any = _requests_library) -> _ProviderRequestsFacade:
    if provider not in _PROVIDER_HOSTS:
        raise ValueError("Unknown payment provider")
    return _ProviderRequestsFacade(provider, requests_module)
