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
from urllib.parse import urlencode, urlsplit
from typing import Any

import dns.exception
import dns.name
import dns.resolver
import requests as _requests_library
from google.auth import exceptions as google_auth_exceptions
from google.auth import transport as google_auth_transport


MAX_GOOGLE_OAUTH_RESPONSE_BYTES = 1024 * 1024
_DEFAULT_DNS_TIMEOUT_SECONDS = 2.0
_MAX_DNS_TIMEOUT_SECONDS = 5.0
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_TIMEOUT_SECONDS = 30.0
_MAX_ADDRESSES = 16
_CHUNK_SIZE = 64 * 1024
_HEADER_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_FORBIDDEN_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-authorization",
        "proxy-connection",
        "upgrade",
        "te",
        "trailer",
        "expect",
    }
)
_ALLOWED_ENDPOINTS = frozenset(
    {
        ("POST", "oauth2.googleapis.com", "/token"),
        ("GET", "openidconnect.googleapis.com", "/v1/userinfo"),
        ("GET", "www.googleapis.com", "/oauth2/v1/certs"),
    }
)
_MISSING = object()


class GoogleOAuthTransportError(_requests_library.RequestException):
    """A Google OAuth request failed or violated the pinned transport policy."""


class GoogleOAuthResponseTooLargeError(GoogleOAuthTransportError):
    """A Google OAuth response exceeded the strict response-byte limit."""


class GoogleOAuthHTTPResponse(google_auth_transport.Response):
    """Small bounded response adapter for both Requests-like callers and google-auth."""

    def __init__(self, *, status_code: int, headers: Mapping[str, str], body: bytes):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.closed = False
        self._status = status_code
        self._headers = dict(headers)
        self._data = bytes(body)

    @property
    def status(self) -> int:
        return self._status

    @property
    def headers(self) -> Mapping[str, str]:
        return self._headers

    @property
    def data(self) -> bytes:
        return self._data

    @property
    def body(self) -> bytes:
        return self._data

    def json(self) -> Any:
        return json.loads(self._data)

    def close(self) -> None:
        # The low-level connection is closed before the response is returned.
        self.closed = True


class _PinnedGoogleHTTPSConnection(http.client.HTTPSConnection):
    """Direct TCP connect to a validated IP, with original-host TLS/SNI checks."""

    def __init__(
        self,
        *,
        hostname: str,
        destination_ip: str,
        port: int,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        self._destination_ip = ipaddress.ip_address(destination_ip)
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        try:
            context = ssl.create_default_context()
        except (OSError, ssl.SSLError) as exc:
            raise GoogleOAuthTransportError("Google OAuth TLS trust configuration is invalid") from exc
        if context.check_hostname is not True or context.verify_mode != ssl.CERT_REQUIRED:
            raise GoogleOAuthTransportError(
                "Google OAuth TLS certificate and hostname verification are required"
            )
        super().__init__(host=hostname, port=port, timeout=connect_timeout, context=context)

    def connect(self) -> None:
        if self._tunnel_host:
            raise GoogleOAuthTransportError("Google OAuth proxy tunneling is prohibited")

        raw_socket: socket.socket | None = None
        tls_socket: ssl.SSLSocket | None = None
        try:
            raw_socket = socket.create_connection(
                (self._destination_ip.compressed, self.port),
                timeout=self._connect_timeout,
                source_address=self.source_address,
            )
            peer = raw_socket.getpeername()
            if not isinstance(peer, tuple) or not peer:
                raise GoogleOAuthTransportError("Google OAuth connected peer could not be verified")
            try:
                peer_ip = ipaddress.ip_address(peer[0])
            except ValueError as exc:
                raise GoogleOAuthTransportError("Google OAuth connected peer could not be verified") from exc
            if peer_ip != self._destination_ip:
                raise GoogleOAuthTransportError("Google OAuth connected peer differs from the validated IP")

            tls_socket = self._context.wrap_socket(raw_socket, server_hostname=self.host)
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
    if value is None:
        value = _DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, tuple):
        if len(value) != 2:
            raise GoogleOAuthTransportError("Google OAuth timeout is invalid")
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
        raise GoogleOAuthTransportError("Google OAuth timeout must be positive and bounded")

    return min(float(connect_value), 5.0), min(float(read_value), _MAX_TIMEOUT_SECONDS)


def _validate_google_url(method: object, url: object) -> tuple[str, int, str]:
    if not isinstance(method, str):
        raise GoogleOAuthTransportError("Google OAuth HTTP method is invalid")
    normalized_method = method.upper()
    if not isinstance(url, str) or not url or url != url.strip():
        raise GoogleOAuthTransportError("Google OAuth URL is invalid")
    if "\\" in url or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise GoogleOAuthTransportError("Google OAuth URL contains invalid characters")

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise GoogleOAuthTransportError("Google OAuth URL is malformed") from exc

    hostname = (parsed.hostname or "").lower()
    endpoint = (normalized_method, hostname, parsed.path)
    if endpoint not in _ALLOWED_ENDPOINTS:
        raise GoogleOAuthTransportError("Google OAuth endpoint is not approved")
    if parsed.scheme.lower() != "https" or not parsed.netloc or port not in (None, 443):
        raise GoogleOAuthTransportError("Google OAuth endpoint must use HTTPS on port 443")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise GoogleOAuthTransportError("Google OAuth URL must not contain user information")
    if parsed.netloc.endswith(":") or parsed.query or parsed.fragment:
        raise GoogleOAuthTransportError("Google OAuth URL contains an unsupported authority or suffix")
    return hostname, 443, parsed.path


def _validate_addresses(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or values is None:
        raise GoogleOAuthTransportError("Google OAuth DNS answer is invalid")
    try:
        candidates = tuple(values)
    except TypeError as exc:
        raise GoogleOAuthTransportError("Google OAuth DNS answer is invalid") from exc
    if not candidates:
        raise GoogleOAuthTransportError("Google OAuth hostname returned no addresses")
    if len(candidates) > _MAX_ADDRESSES:
        raise GoogleOAuthTransportError("Google OAuth DNS answer has too many addresses")

    parsed: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for value in candidates:
        if not isinstance(value, str) or not value or value != value.strip() or "%" in value:
            raise GoogleOAuthTransportError("Google OAuth DNS answer is invalid")
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise GoogleOAuthTransportError("Google OAuth DNS answer is invalid") from exc
        if (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None)
        ):
            raise GoogleOAuthTransportError("Google OAuth DNS answer contains a non-public IP address")
        parsed.add(address)

    return tuple(address.compressed for address in sorted(parsed, key=lambda item: (item.version, int(item))))


def resolve_google_host_addresses(
    hostname: str,
    *,
    timeout_seconds: float = _DEFAULT_DNS_TIMEOUT_SECONDS,
) -> tuple[str, ...]:
    """Resolve A/AAAA records under one short deadline and reject unsafe results."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > _MAX_DNS_TIMEOUT_SECONDS
    ):
        raise GoogleOAuthTransportError("Google OAuth DNS timeout is invalid")

    deadline = time.monotonic() + float(timeout_seconds)
    try:
        resolver = dns.resolver.Resolver(configure=True)
        resolver.timeout = min(float(resolver.timeout), float(timeout_seconds))
        query_name = dns.name.from_text(hostname, origin=dns.name.root)
    except (dns.exception.DNSException, OSError, TypeError, ValueError) as exc:
        raise GoogleOAuthTransportError("Google OAuth DNS configuration failed") from exc

    addresses: list[str] = []
    for record_type in ("A", "AAAA"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GoogleOAuthTransportError("Google OAuth DNS lookup timed out")
        try:
            answer = resolver.resolve(
                query_name,
                record_type,
                search=False,
                lifetime=remaining,
                raise_on_no_answer=False,
            )
        except dns.resolver.NXDOMAIN as exc:
            raise GoogleOAuthTransportError("Google OAuth hostname does not resolve") from exc
        except dns.resolver.LifetimeTimeout as exc:
            raise GoogleOAuthTransportError("Google OAuth DNS lookup timed out") from exc
        except (dns.exception.DNSException, OSError) as exc:
            raise GoogleOAuthTransportError("Google OAuth DNS lookup failed") from exc
        if time.monotonic() > deadline:
            raise GoogleOAuthTransportError("Google OAuth DNS lookup timed out")
        if getattr(answer, "rrset", None) is None:
            continue
        for record in answer:
            address = getattr(record, "address", None)
            if not isinstance(address, str) or not address:
                raise GoogleOAuthTransportError("Google OAuth DNS answer is invalid")
            addresses.append(address)
            if len(addresses) > _MAX_ADDRESSES:
                raise GoogleOAuthTransportError("Google OAuth DNS answer has too many addresses")

    return _validate_addresses(addresses)


def _normalize_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        supplied: dict[str, str] = {}
    elif isinstance(headers, Mapping):
        supplied = dict(headers)
    else:
        raise GoogleOAuthTransportError("Google OAuth request headers are invalid")

    normalized: dict[str, tuple[str, str]] = {}
    for name, value in supplied.items():
        if not isinstance(name, str) or _HEADER_NAME.fullmatch(name) is None:
            raise GoogleOAuthTransportError("Google OAuth request header name is invalid")
        if not isinstance(value, str) or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
            raise GoogleOAuthTransportError("Google OAuth request header value is invalid")
        lowered = name.lower()
        if lowered in normalized:
            raise GoogleOAuthTransportError("Duplicate Google OAuth request headers are not allowed")
        if lowered in _FORBIDDEN_HEADERS:
            raise GoogleOAuthTransportError("Google OAuth request overrides a transport-controlled header")
        if lowered == "accept-encoding":
            continue
        normalized[lowered] = (name, value)

    result = {original_name: value for original_name, value in normalized.values()}
    result["Accept-Encoding"] = "identity"
    result["Connection"] = "close"
    if not any(name.lower() == "accept" for name in result):
        result["Accept"] = "application/json"
    return result


def _read_response_body(response: http.client.HTTPResponse) -> bytes:
    raw_length = response.getheader("Content-Length")
    if raw_length is not None:
        try:
            declared_length = int(raw_length.strip())
        except (AttributeError, TypeError, ValueError, OverflowError):
            declared_length = None
        if declared_length is not None and declared_length > MAX_GOOGLE_OAUTH_RESPONSE_BYTES:
            raise GoogleOAuthResponseTooLargeError("Google OAuth response exceeded the byte limit")

    encoding = response.getheader("Content-Encoding")
    if encoding and encoding.strip().lower() not in {"identity"}:
        raise GoogleOAuthTransportError("Compressed Google OAuth responses are not accepted")

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(_CHUNK_SIZE, MAX_GOOGLE_OAUTH_RESPONSE_BYTES - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_GOOGLE_OAUTH_RESPONSE_BYTES:
            raise GoogleOAuthResponseTooLargeError("Google OAuth response exceeded the byte limit")
        chunks.append(chunk)
    return b"".join(chunks)


def request_google_oauth(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | str | None = None,
    timeout: object = _DEFAULT_TIMEOUT_SECONDS,
) -> GoogleOAuthHTTPResponse:
    """Make one pinned HTTPS request to an exact Google OAuth endpoint.

    DNS results are validated in full, sockets connect to numeric IPs, and TLS
    uses the approved hostname for SNI/certificate verification. Redirects and
    proxies are never followed, and requests are never retried after transmission.
    """
    hostname, port, target = _validate_google_url(method, url)
    connect_timeout, read_timeout = _validate_timeout(timeout)
    if body is None:
        payload = None
    elif isinstance(body, str):
        payload = body.encode("utf-8")
    elif isinstance(body, (bytes, bytearray, memoryview)):
        payload = bytes(body)
    else:
        raise GoogleOAuthTransportError("Google OAuth request body is invalid")
    if method.upper() == "GET" and payload not in (None, b""):
        raise GoogleOAuthTransportError("Google OAuth GET requests cannot contain a body")

    request_headers = _normalize_headers(headers)
    if payload is not None and not any(name.lower() == "content-type" for name in request_headers):
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"

    addresses = resolve_google_host_addresses(hostname)
    connection: _PinnedGoogleHTTPSConnection | None = None
    last_connect_error: BaseException | None = None
    # Only failures before HTTP transmission may advance to another validated IP.
    for address in addresses:
        candidate = _PinnedGoogleHTTPSConnection(
            hostname=hostname,
            destination_ip=address,
            port=port,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        try:
            candidate.connect()
        except GoogleOAuthTransportError:
            candidate.close()
            raise
        except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
            candidate.close()
            last_connect_error = exc
            continue
        connection = candidate
        break

    if connection is None:
        raise GoogleOAuthTransportError("Could not establish TLS to an approved Google OAuth destination") from last_connect_error

    try:
        connection.request(method.upper(), target, body=payload, headers=request_headers)
        response = connection.getresponse()
        body_bytes = _read_response_body(response)
        response_headers = {str(name): str(value) for name, value in response.getheaders()}
        return GoogleOAuthHTTPResponse(
            status_code=response.status,
            headers=response_headers,
            body=body_bytes,
        )
    except GoogleOAuthTransportError:
        raise
    except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
        # A token-exchange POST may have been processed once request transmission starts.
        raise GoogleOAuthTransportError("Google OAuth HTTPS request failed") from exc
    finally:
        connection.close()


class GoogleOAuthAuthRequest(google_auth_transport.Request):
    """google-auth request adapter backed exclusively by the pinned HTTPS transport."""

    def __call__(self, url, method="GET", body=None, headers=None, timeout=10, **kwargs):
        if kwargs:
            raise google_auth_exceptions.TransportError("Unsupported Google auth transport options")
        try:
            return request_google_oauth(
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
            )
        except GoogleOAuthTransportError as exc:
            raise google_auth_exceptions.TransportError("Google auth certificate request failed") from exc


class GoogleOAuthRequestsFacade:
    """Small compatibility surface for the fixed Google OAuth request call sites."""

    def __init__(self, requests_module: Any = _requests_library):
        self.RequestException = requests_module.RequestException
        self.Response = GoogleOAuthHTTPResponse
        self.exceptions = requests_module.exceptions

    def get(self, url: str, **kwargs: Any) -> GoogleOAuthHTTPResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> GoogleOAuthHTTPResponse:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> GoogleOAuthHTTPResponse:
        headers = kwargs.pop("headers", None)
        timeout = kwargs.pop("timeout", _DEFAULT_TIMEOUT_SECONDS)
        allow_redirects = kwargs.pop("allow_redirects", False)
        data = kwargs.pop("data", None)
        if kwargs:
            raise GoogleOAuthTransportError("Unsupported Google OAuth request options")
        if allow_redirects is not False:
            raise GoogleOAuthTransportError("Google OAuth redirects are disabled")

        body: bytes | None
        if data is None:
            body = None
        elif isinstance(data, Mapping):
            try:
                body = urlencode(data).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise GoogleOAuthTransportError("Google OAuth form data is invalid") from exc
            header_map = dict(headers or {})
            if not any(isinstance(name, str) and name.lower() == "content-type" for name in header_map):
                header_map["Content-Type"] = "application/x-www-form-urlencoded"
            headers = header_map
        elif isinstance(data, str):
            body = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray, memoryview)):
            body = bytes(data)
        else:
            raise GoogleOAuthTransportError("Google OAuth request data is invalid")

        try:
            return request_google_oauth(
                method=method,
                url=url,
                headers=headers,
                body=body,
                timeout=timeout,
            )
        except GoogleOAuthTransportError:
            raise


def requests_for_google_oauth(requests_module: Any = _requests_library) -> GoogleOAuthRequestsFacade:
    """Return a compatible facade that cannot fall back to ordinary Requests networking."""
    return GoogleOAuthRequestsFacade(requests_module)
