from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl

import pytest

from app.modules.hie.providers import transport as transport_module
from app.modules.hie.providers.destination_policy import (
    resolve_hie_endpoint_ips,
)
from app.modules.hie.providers.endpoint_policy import HIEEndpointPolicyError
from app.modules.hie.providers.transport import (
    HIEHTTPTransport,
    HIERedirectRejectedError,
    HIETransportError,
    _PinnedHTTPSConnection,
)


BASE_URL = "https://fhir.partnerhealth.org/fhir/r4"
ALLOWED_HOSTS = frozenset({"fhir.partnerhealth.org", "api.epic.com"})


class FakeResponse:
    def __init__(self, status=200, headers=(), body=b'{"resourceType":"Bundle"}'):
        self.status = status
        self._headers = list(headers)
        self._body = body
        self._offset = 0

    def getheaders(self):
        return self._headers

    def read(self, amount=-1):
        if amount is None or amount < 0:
            amount = len(self._body) - self._offset

        chunk = self._body[self._offset:self._offset + amount]
        self._offset += len(chunk)
        return chunk


def _resolver(*addresses):
    calls = []

    def resolve(hostname, port, timeout_seconds):
        assert 0 < timeout_seconds <= 5.0
        calls.append((hostname, port, timeout_seconds))
        return addresses

    return resolve, calls


class FakeTLSContext:
    def __init__(self):
        self.server_hostname = None
        self.wrapped_socket = None

    def wrap_socket(self, raw_socket, *, server_hostname):
        self.server_hostname = server_hostname
        self.wrapped_socket = FakeTLSSocket()
        return self.wrapped_socket


class FakeRawSocket:
    def __init__(self, peer_ip):
        self.peer_ip = peer_ip
        self.closed = False

    def getpeername(self):
        return (self.peer_ip, 443)

    def close(self):
        self.closed = True


class FakeTLSSocket:
    def __init__(self):
        self.timeout = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


class FakeConnection:
    instances = []
    response = FakeResponse()
    connect_fail_ips = set()
    request_error = None

    def __init__(
        self,
        *,
        hostname,
        destination_ip,
        connect_timeout,
        read_timeout,
    ):
        self.hostname = hostname
        self.destination_ip = destination_ip
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.connect_calls = 0
        self.request_calls = []
        self.closed = False
        type(self).instances.append(self)

    def connect(self):
        self.connect_calls += 1
        if self.destination_ip in type(self).connect_fail_ips:
            raise OSError("simulated connect failure")

    def request(self, method, target, *, headers):
        self.request_calls.append((method, target, headers))
        if type(self).request_error is not None:
            raise type(self).request_error

    def getresponse(self):
        return type(self).response

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_connection(monkeypatch):
    FakeConnection.instances = []
    FakeConnection.response = FakeResponse()
    FakeConnection.connect_fail_ips = set()
    FakeConnection.request_error = None
    monkeypatch.setattr(
        transport_module,
        "_PinnedHTTPSConnection",
        FakeConnection,
    )


def test_pinned_connection_uses_numeric_ip_and_original_tls_hostname(monkeypatch):
    raw_socket = FakeRawSocket("8.8.8.8")
    calls = []

    def fake_create_connection(address, *, timeout, source_address):
        calls.append((address, timeout, source_address))
        return raw_socket

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        fake_create_connection,
    )

    connection = _PinnedHTTPSConnection(
        hostname="fhir.partnerhealth.org",
        destination_ip="8.8.8.8",
        connect_timeout=2.0,
        read_timeout=6.0,
    )

    assert connection._context.check_hostname is True
    assert connection._context.verify_mode == ssl.CERT_REQUIRED

    fake_context = FakeTLSContext()
    connection._context = fake_context
    connection.connect()

    assert calls == [(("8.8.8.8", 443), 2.0, None)]
    assert fake_context.server_hostname == "fhir.partnerhealth.org"
    assert fake_context.wrapped_socket.timeout == 6.0

    connection.close()


def test_pinned_connection_rejects_peer_mismatch_before_tls(monkeypatch):
    raw_socket = FakeRawSocket("127.0.0.1")
    calls = []

    def fake_create_connection(address, *, timeout, source_address):
        return raw_socket

    monkeypatch.setattr(
        transport_module.socket,
        "create_connection",
        fake_create_connection,
    )

    connection = _PinnedHTTPSConnection(
        hostname="fhir.partnerhealth.org",
        destination_ip="8.8.8.8",
        connect_timeout=2.0,
        read_timeout=6.0,
    )

    context = FakeTLSContext()
    connection._context = context

    with pytest.raises(HIETransportError, match="did not match"):
        connection.connect()

    assert context.server_hostname is None
    assert raw_socket.closed is True


def test_read_only_transport_pins_validated_ip_and_encodes_query(monkeypatch):
    resolver, calls = _resolver("8.8.8.8")
    transport = HIEHTTPTransport(
        ALLOWED_HOSTS,
        resolver=resolver,
    )

    result = transport.request(
        endpoint_url=BASE_URL,
        path="Patient",
        params={"identifier": "abc+12", "_count": 10},
        headers={"Authorization": "Bearer test-token"},
    )

    assert calls == [("fhir.partnerhealth.org", 443, 2.0)]
    assert len(FakeConnection.instances) == 1

    connection = FakeConnection.instances[0]
    assert connection.hostname == "fhir.partnerhealth.org"
    assert connection.destination_ip == "8.8.8.8"
    assert connection.request_calls[0][0] == "GET"
    assert connection.request_calls[0][1] == (
        "/fhir/r4/Patient?identifier=abc%2B12&_count=10"
    )
    assert connection.closed is True

    request_headers = {
        key.lower(): value
        for key, value in connection.request_calls[0][2].items()
    }
    assert request_headers["authorization"] == "Bearer test-token"
    assert request_headers["accept-encoding"] == "identity"
    assert request_headers["connection"] == "close"

    assert result.status_code == 200
    assert result.body == b'{"resourceType":"Bundle"}'
    assert result.connected_ip == "8.8.8.8"


def test_connection_failures_retry_only_before_request_transmission():
    resolver, _ = _resolver("8.8.8.8", "8.8.4.4")
    FakeConnection.connect_fail_ips = {"8.8.4.4"}

    transport = HIEHTTPTransport(
        ALLOWED_HOSTS,
        resolver=resolver,
    )

    result = transport.request(endpoint_url=BASE_URL, path="metadata")

    assert [item.destination_ip for item in FakeConnection.instances] == [
        "8.8.4.4",
        "8.8.8.8",
    ]
    assert FakeConnection.instances[0].request_calls == []
    assert len(FakeConnection.instances[1].request_calls) == 1
    assert result.connected_ip == "8.8.8.8"


def test_request_failure_after_transmission_does_not_retry_another_ip():
    resolver, _ = _resolver("8.8.8.8", "8.8.4.4")
    FakeConnection.request_error = http.client.RemoteDisconnected(
        "simulated disconnect"
    )

    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIETransportError, match="request failed"):
        transport.request(endpoint_url=BASE_URL, path="Patient")

    assert len(FakeConnection.instances) == 1
    assert len(FakeConnection.instances[0].request_calls) == 1
    assert FakeConnection.instances[0].closed is True


def test_transport_rejects_redirect_without_following_it():
    resolver, calls = _resolver("8.8.8.8")
    FakeConnection.response = FakeResponse(
        status=302,
        headers=[("Location", "http://127.0.0.1/admin")],
        body=b"",
    )

    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIERedirectRejectedError):
        transport.request(endpoint_url=BASE_URL, path="Patient")

    assert calls == [("fhir.partnerhealth.org", 443, 2.0)]
    assert len(FakeConnection.instances) == 1
    assert len(FakeConnection.instances[0].request_calls) == 1
    assert FakeConnection.instances[0].closed is True


def test_transport_rejects_body_over_configured_limit():
    resolver, _ = _resolver("8.8.8.8")
    FakeConnection.response = FakeResponse(body=b"12345")

    transport = HIEHTTPTransport(
        ALLOWED_HOSTS,
        resolver=resolver,
        max_response_bytes=4,
    )

    with pytest.raises(HIETransportError, match="body-size limit"):
        transport.request(endpoint_url=BASE_URL, path="Patient")

    assert FakeConnection.instances[0].closed is True


def test_transport_accepts_body_at_configured_limit():
    resolver, _ = _resolver("8.8.8.8")
    FakeConnection.response = FakeResponse(body=b"1234")

    transport = HIEHTTPTransport(
        ALLOWED_HOSTS,
        resolver=resolver,
        max_response_bytes=4,
    )

    result = transport.request(endpoint_url=BASE_URL, path="Patient")

    assert result.body == b"1234"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "TRACE"])
def test_transport_rejects_non_read_only_methods_before_dns(method):
    resolver, calls = _resolver("8.8.8.8")
    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIEEndpointPolicyError, match="GET and HEAD only"):
        transport.request(
            endpoint_url=BASE_URL,
            path="Patient",
            method=method,
        )

    assert calls == []
    assert FakeConnection.instances == []


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example"},
        {"Connection": "keep-alive"},
        {"Proxy-Authorization": "secret"},
        {"Accept-Encoding": "gzip"},
        {"X-Test": "header\r\nInjected: yes"},
    ],
)
def test_transport_rejects_unsafe_headers_before_dns(headers):
    resolver, calls = _resolver("8.8.8.8")
    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIEEndpointPolicyError):
        transport.request(
            endpoint_url=BASE_URL,
            path="Patient",
            headers=headers,
        )

    assert calls == []
    assert FakeConnection.instances == []


def test_all_dns_answers_must_pass_before_any_connection():
    resolver, calls = _resolver("8.8.8.8", "127.0.0.1")
    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIEEndpointPolicyError):
        transport.request(endpoint_url=BASE_URL, path="Patient")

    assert calls == [("fhir.partnerhealth.org", 443, 2.0)]
    assert FakeConnection.instances == []


@pytest.mark.parametrize(
    "path",
    [
        "//attacker.example/path",
        "https://attacker.example/path",
        "../admin",
        "/Patient/%2e%2e/admin",
        "/Patient/%252e%252e/admin",
        "Patient?token=secret",
        "Patient#fragment",
        "Patient\\admin",
    ],
)
def test_transport_rejects_unsafe_request_paths_before_dns(path):
    resolver, calls = _resolver("8.8.8.8")
    transport = HIEHTTPTransport(ALLOWED_HOSTS, resolver=resolver)

    with pytest.raises(HIEEndpointPolicyError):
        transport.request(endpoint_url=BASE_URL, path=path)

    assert calls == []
    assert FakeConnection.instances == []


def test_transport_rejects_invalid_timeout_configuration():
    with pytest.raises(ValueError):
        HIEHTTPTransport(ALLOWED_HOSTS, connect_timeout=0)

    with pytest.raises(ValueError):
        HIEHTTPTransport(ALLOWED_HOSTS, read_timeout=31)

    with pytest.raises(ValueError):
        HIEHTTPTransport(ALLOWED_HOSTS, max_response_bytes=0)
    with pytest.raises(ValueError, match="DNS timeout"):
        HIEHTTPTransport(ALLOWED_HOSTS, dns_timeout_seconds=0)

    with pytest.raises(ValueError, match="DNS timeout"):
        HIEHTTPTransport(ALLOWED_HOSTS, dns_timeout_seconds=5.1)