from __future__ import annotations

import http.client
import ipaddress
import ssl
from unittest.mock import Mock

import pytest

from app.modules.billing.services.gateways.response_utils import (
    MAX_PROVIDER_RESPONSE_BYTES,
    ProviderResponseTooLargeError,
)
from app.modules.billing.services.gateways import provider_transport as transport


PUBLIC_IP = "93.184.216.34"


@pytest.mark.parametrize(
    "provider,url,expected_host,target",
    [
        ("paystack", "https://api.paystack.co/transaction/initialize", "api.paystack.co", "/transaction/initialize"),
        ("flutterwave", "https://api.flutterwave.com/v3/transactions", "api.flutterwave.com", "/v3/transactions"),
        ("stripe", "https://api.stripe.com/v1/payment_intents?limit=2", "api.stripe.com", "/v1/payment_intents?limit=2"),
    ],
)
def test_provider_url_accepts_only_fixed_provider_host(provider, url, expected_host, target):
    hostname, port, request_target = transport._validate_provider_url(provider, url)
    assert (hostname, port, request_target) == (expected_host, 443, target)


@pytest.mark.parametrize(
    "provider,url",
    [
        ("paystack", "http://api.paystack.co/transaction/initialize"),
        ("paystack", "https://api.paystack.co.evil.example/transaction/initialize"),
        ("paystack", "https://api.paystack.co./transaction/initialize"),
        ("paystack", "https://api.paystack.co:444/transaction/initialize"),
        ("paystack", "https://user:secret@api.paystack.co/transaction/initialize"),
        ("paystack", "https://api.paystack.co/transaction/initialize#fragment"),
        ("stripe", "https://files.stripe.com/v1/files"),
        ("flutterwave", "https://127.0.0.1/v3/transactions"),
    ],
)
def test_provider_url_rejects_nonapproved_destinations(provider, url):
    with pytest.raises(transport.ProviderTransportError):
        transport._validate_provider_url(provider, url)


def test_provider_dns_answer_rejects_mixed_public_and_private_addresses():
    with pytest.raises(transport.ProviderTransportError, match="non-public IP"):
        transport._validate_provider_addresses([PUBLIC_IP, "127.0.0.1"])


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fc00::1", "fe80::1"])
def test_provider_dns_answer_rejects_nonpublic_addresses(address):
    with pytest.raises(transport.ProviderTransportError):
        transport._validate_provider_addresses([address])


def test_provider_dns_answer_is_canonical_and_deduplicated():
    assert transport._validate_provider_addresses(["93.184.216.34", "93.184.216.34"]) == (PUBLIC_IP,)


def test_transport_requires_tls_verification_and_rejects_explicit_proxy(monkeypatch):
    dns_mock = Mock(return_value=(PUBLIC_IP,))
    monkeypatch.setattr(transport, "resolve_provider_host_addresses", dns_mock)

    with pytest.raises(transport.ProviderTransportError, match="TLS verification"):
        transport.request_payment_provider(
            provider="paystack", method="GET", url="https://api.paystack.co/transaction/verify/X",
            headers={}, verify_ssl_certs=False,
        )
    dns_mock.assert_not_called()

    with pytest.raises(transport.ProviderTransportError, match="proxies"):
        transport.request_payment_provider(
            provider="paystack", method="GET", url="https://api.paystack.co/transaction/verify/X",
            headers={}, proxy={"https": "http://proxy.example:8080"},
        )
    dns_mock.assert_not_called()


class FakeRawResponse:
    def __init__(self, *, status=200, headers=None, body=b"{}"):
        self.status = status
        self.headers = dict(headers or {})
        self.body = body
        self.read_offsets = 0

    def getheader(self, name):
        return next((v for k, v in self.headers.items() if k.lower() == name.lower()), None)

    def getheaders(self):
        return list(self.headers.items())

    def read(self, amount):
        start = self.read_offsets
        end = min(len(self.body), start + amount)
        self.read_offsets = end
        return self.body[start:end]


class FakeConnection:
    def __init__(self, *, response, **kwargs):
        self.options = kwargs
        self.response = response
        self.connected = False
        self.closed = False
        self.request_args = None

    def connect(self):
        self.connected = True

    def request(self, method, target, *, body=None, headers=None):
        self.request_args = (method, target, body, dict(headers or {}))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def _patch_connection(monkeypatch, response):
    instances = []

    def factory(**kwargs):
        value = FakeConnection(response=response, **kwargs)
        instances.append(value)
        return value

    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", factory)
    monkeypatch.setattr(transport, "resolve_provider_host_addresses", Mock(return_value=(PUBLIC_IP,)))
    return instances


def test_transport_pins_selected_public_ip_and_never_follows_redirect(monkeypatch):
    response = FakeRawResponse(
        status=302,
        headers={"Location": "https://127.0.0.1/internal", "Content-Length": "0"},
        body=b"",
    )
    connections = _patch_connection(monkeypatch, response)

    result = transport.request_payment_provider(
        provider="paystack",
        method="GET",
        url="https://api.paystack.co/transaction/verify/PAY-001",
        headers={"Authorization": "Bearer synthetic", "Accept-Encoding": "gzip"},
        timeout=20,
    )

    assert result.status_code == 302
    assert result.headers["Location"] == "https://127.0.0.1/internal"
    assert len(connections) == 1
    assert connections[0].options["hostname"] == "api.paystack.co"
    assert connections[0].options["destination_ip"] == PUBLIC_IP
    assert connections[0].request_args[0:2] == ("GET", "/transaction/verify/PAY-001")
    assert connections[0].request_args[3]["Accept-Encoding"] == "identity"
    assert connections[0].request_args[3]["Connection"] == "close"
    assert "Host" not in {k.title() for k in connections[0].request_args[3]}
    assert connections[0].closed is True


def test_transport_rejects_oversized_declared_body_and_closes_connection(monkeypatch):
    response = FakeRawResponse(
        status=200,
        headers={"Content-Length": str(MAX_PROVIDER_RESPONSE_BYTES + 1)},
        body=b"",
    )
    connections = _patch_connection(monkeypatch, response)

    with pytest.raises(ProviderResponseTooLargeError):
        transport.request_payment_provider(
            provider="flutterwave",
            method="GET",
            url="https://api.flutterwave.com/v3/transactions/123/verify",
            headers={"Authorization": "Bearer synthetic"},
        )

    assert connections[0].closed is True
    assert response.read_offsets == 0


def test_transport_rejects_streamed_body_overflow_and_closes_connection(monkeypatch):
    response = FakeRawResponse(status=200, headers={}, body=b"x" * (MAX_PROVIDER_RESPONSE_BYTES + 1))
    connections = _patch_connection(monkeypatch, response)

    with pytest.raises(ProviderResponseTooLargeError):
        transport.request_payment_provider(
            provider="stripe", method="GET", url="https://api.stripe.com/v1/payment_intents", headers={},
        )

    assert connections[0].closed is True
    assert response.read_offsets > 0


def test_transport_uses_one_connection_attempt_after_request_transmission(monkeypatch):
    class FailingAfterRequest(FakeConnection):
        def request(self, method, target, *, body=None, headers=None):
            super().request(method, target, body=body, headers=headers)
            raise OSError("simulated connection drop after request transmission")

    response = FakeRawResponse()
    instances = []

    def factory(**kwargs):
        conn = FailingAfterRequest(response=response, **kwargs)
        instances.append(conn)
        return conn

    monkeypatch.setattr(transport, "_PinnedHTTPSConnection", factory)
    monkeypatch.setattr(transport, "resolve_provider_host_addresses", Mock(return_value=(PUBLIC_IP, "8.8.8.8")))

    with pytest.raises(transport.ProviderTransportError, match="HTTPS request failed"):
        transport.request_payment_provider(
            provider="flutterwave", method="POST", url="https://api.flutterwave.com/v3/payments",
            headers={"Content-Type": "application/json"}, data=b'{"amount":"100"}',
        )

    assert len(instances) == 1
    assert instances[0].closed is True


def test_pinned_connection_uses_numeric_ip_and_original_hostname_for_sni(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.closed = False
            self.timeout = None

        def getpeername(self):
            return (PUBLIC_IP, 443)

        def close(self):
            self.closed = True

        def settimeout(self, value):
            self.timeout = value

    class FakeContext:
        check_hostname = True
        verify_mode = ssl.CERT_REQUIRED

        def __init__(self):
            self.server_hostname = None
            self.wrapped = None

        def wrap_socket(self, raw, *, server_hostname):
            self.server_hostname = server_hostname
            self.wrapped = raw
            return raw

    raw = FakeSocket()
    context = FakeContext()
    create_connection = Mock(return_value=raw)
    monkeypatch.setattr(transport.socket, "create_connection", create_connection)
    monkeypatch.setattr(transport.ssl, "create_default_context", Mock(return_value=context))

    connection = transport._PinnedHTTPSConnection(
        hostname="api.stripe.com", destination_ip=PUBLIC_IP, port=443,
        connect_timeout=3, read_timeout=20,
    )
    connection.connect()

    create_connection.assert_called_once_with((PUBLIC_IP, 443), timeout=3, source_address=None)
    assert context.server_hostname == "api.stripe.com"
    assert connection.sock is raw
    assert raw.timeout == 20


def test_pinned_connection_fails_closed_on_peer_ip_mismatch(monkeypatch):
    class FakeSocket:
        closed = False

        def getpeername(self):
            return ("127.0.0.1", 443)

        def close(self):
            self.closed = True

    raw = FakeSocket()
    context = Mock()
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    monkeypatch.setattr(transport.socket, "create_connection", Mock(return_value=raw))
    monkeypatch.setattr(transport.ssl, "create_default_context", Mock(return_value=context))

    connection = transport._PinnedHTTPSConnection(
        hostname="api.paystack.co", destination_ip=PUBLIC_IP, port=443,
        connect_timeout=3, read_timeout=20,
    )
    with pytest.raises(transport.ProviderTransportError, match="does not match"):
        connection.connect()
    assert raw.closed is True


def test_requests_compatibility_facade_converts_size_error_to_request_exception(monkeypatch):
    monkeypatch.setattr(
        transport,
        "request_payment_provider",
        Mock(side_effect=ProviderResponseTooLargeError("too large")),
    )
    facade = transport.requests_for_provider("paystack")

    with pytest.raises(transport.ProviderTransportError, match="exceeded the byte limit"):
        facade.post(
            "https://api.paystack.co/transaction/initialize",
            headers={"Content-Type": "application/json"},
            json={"email": "patient@example.com"},
            timeout=30,
            stream=True,
            allow_redirects=False,
        )
