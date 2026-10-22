from __future__ import annotations

import http.client
import ssl
from unittest.mock import Mock

import pytest

from app.core.auth.user.services import google_oauth_transport as transport

PUBLIC_IP = "93.184.216.34"


@pytest.mark.parametrize(
    "method,url,expected_host,target",
    [
        ("POST", "https://oauth2.googleapis.com/token", "oauth2.googleapis.com", "/token"),
        ("GET", "https://openidconnect.googleapis.com/v1/userinfo", "openidconnect.googleapis.com", "/v1/userinfo"),
        ("GET", "https://www.googleapis.com/oauth2/v1/certs", "www.googleapis.com", "/oauth2/v1/certs"),
    ],
)
def test_google_endpoint_allowlist_accepts_exact_endpoints(method, url, expected_host, target):
    assert transport._validate_google_url(method, url) == (expected_host, 443, target)


@pytest.mark.parametrize(
    "method,url",
    [
        ("POST", "http://oauth2.googleapis.com/token"),
        ("POST", "https://oauth2.googleapis.com.evil.example/token"),
        ("POST", "https://oauth2.googleapis.com:444/token"),
        ("POST", "https://user:secret@oauth2.googleapis.com/token"),
        ("POST", "https://oauth2.googleapis.com/token?next=https://evil.example"),
        ("POST", "https://oauth2.googleapis.com/token#fragment"),
        ("GET", "https://www.googleapis.com/other/path"),
        ("GET", "https://127.0.0.1/oauth2/v1/certs"),
        ("DELETE", "https://oauth2.googleapis.com/token"),
    ],
)
def test_google_endpoint_allowlist_rejects_other_urls(method, url):
    with pytest.raises(transport.GoogleOAuthTransportError):
        transport._validate_google_url(method, url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fc00::1", "fe80::1"])
def test_dns_validation_rejects_nonpublic_addresses(address):
    with pytest.raises(transport.GoogleOAuthTransportError):
        transport._validate_addresses([address])


def test_dns_validation_rejects_mixed_public_private_answers():
    with pytest.raises(transport.GoogleOAuthTransportError, match="non-public IP"):
        transport._validate_addresses([PUBLIC_IP, "127.0.0.1"])


def test_dns_validation_canonicalizes_and_deduplicates_answers():
    assert transport._validate_addresses([PUBLIC_IP, PUBLIC_IP]) == (PUBLIC_IP,)


class FakeRawResponse:
    def __init__(self, *, status=200, headers=None, body=b"{}"):
        self.status = status
        self.headers = dict(headers or {})
        self.body = body
        self.offset = 0

    def getheader(self, name):
        return next((value for key, value in self.headers.items() if key.lower() == name.lower()), None)

    def getheaders(self):
        return list(self.headers.items())

    def read(self, amount):
        start = self.offset
        end = min(len(self.body), start + amount)
        self.offset = end
        return self.body[start:end]


class FakeConnection:
    def __init__(self, *, response, **kwargs):
        self.options = kwargs
        self.response = response
        self.closed = False
        self.request_args = None

    def connect(self):
        pass

    def request(self, method, target, *, body=None, headers=None):
        self.request_args = (method, target, body, dict(headers or {}))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def patch_connection(monkeypatch, response):
    instances = []

    def factory(**kwargs):
        connection = FakeConnection(response=response, **kwargs)
        instances.append(connection)
        return connection

    monkeypatch.setattr(transport, "_PinnedGoogleHTTPSConnection", factory)
    monkeypatch.setattr(transport, "resolve_google_host_addresses", Mock(return_value=(PUBLIC_IP,)))
    return instances


def test_transport_pins_ip_and_returns_redirect_without_following(monkeypatch):
    raw = FakeRawResponse(status=302, headers={"Location": "https://127.0.0.1/internal", "Content-Length": "0"}, body=b"")
    connections = patch_connection(monkeypatch, raw)

    result = transport.request_google_oauth(
        method="GET", url="https://www.googleapis.com/oauth2/v1/certs", headers={"Accept-Encoding": "gzip"}
    )

    assert result.status == 302
    assert result.headers["Location"] == "https://127.0.0.1/internal"
    assert len(connections) == 1
    assert connections[0].options["hostname"] == "www.googleapis.com"
    assert connections[0].options["destination_ip"] == PUBLIC_IP
    assert connections[0].request_args[0:2] == ("GET", "/oauth2/v1/certs")
    assert connections[0].request_args[3]["Accept-Encoding"] == "identity"
    assert connections[0].request_args[3]["Connection"] == "close"
    assert connections[0].closed is True


def test_transport_rejects_declared_oversize_and_closes_connection(monkeypatch):
    raw = FakeRawResponse(headers={"Content-Length": str(transport.MAX_GOOGLE_OAUTH_RESPONSE_BYTES + 1)})
    connections = patch_connection(monkeypatch, raw)
    with pytest.raises(transport.GoogleOAuthResponseTooLargeError):
        transport.request_google_oauth(method="GET", url="https://www.googleapis.com/oauth2/v1/certs")
    assert connections[0].closed is True
    assert raw.offset == 0


def test_transport_rejects_streamed_oversize_and_closes_connection(monkeypatch):
    raw = FakeRawResponse(body=b"x" * (transport.MAX_GOOGLE_OAUTH_RESPONSE_BYTES + 1))
    connections = patch_connection(monkeypatch, raw)
    with pytest.raises(transport.GoogleOAuthResponseTooLargeError):
        transport.request_google_oauth(method="GET", url="https://www.googleapis.com/oauth2/v1/certs")
    assert connections[0].closed is True
    assert raw.offset > 0


def test_transport_rejects_compressed_response(monkeypatch):
    raw = FakeRawResponse(headers={"Content-Encoding": "gzip"}, body=b"compressed")
    connections = patch_connection(monkeypatch, raw)
    with pytest.raises(transport.GoogleOAuthTransportError, match="Compressed"):
        transport.request_google_oauth(method="GET", url="https://www.googleapis.com/oauth2/v1/certs")
    assert connections[0].closed is True


def test_transport_does_not_retry_after_post_transmission(monkeypatch):
    class FailingAfterRequest(FakeConnection):
        def request(self, method, target, *, body=None, headers=None):
            super().request(method, target, body=body, headers=headers)
            raise OSError("simulated reset after request transmission")

    instances = []

    def factory(**kwargs):
        value = FailingAfterRequest(response=FakeRawResponse(), **kwargs)
        instances.append(value)
        return value

    monkeypatch.setattr(transport, "_PinnedGoogleHTTPSConnection", factory)
    monkeypatch.setattr(transport, "resolve_google_host_addresses", Mock(return_value=(PUBLIC_IP, "8.8.8.8")))

    with pytest.raises(transport.GoogleOAuthTransportError, match="HTTPS request failed"):
        transport.request_google_oauth(
            method="POST", url="https://oauth2.googleapis.com/token", body=b"code=synthetic"
        )
    assert len(instances) == 1
    assert instances[0].closed is True


def test_pinned_connection_uses_numeric_ip_and_original_host_for_sni(monkeypatch):
    class FakeSocket:
        closed = False
        timeout = None

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

        def wrap_socket(self, raw, *, server_hostname):
            self.server_hostname = server_hostname
            return raw

    raw = FakeSocket()
    context = FakeContext()
    create_connection = Mock(return_value=raw)
    monkeypatch.setattr(transport.socket, "create_connection", create_connection)
    monkeypatch.setattr(transport.ssl, "create_default_context", Mock(return_value=context))

    connection = transport._PinnedGoogleHTTPSConnection(
        hostname="www.googleapis.com", destination_ip=PUBLIC_IP, port=443,
        connect_timeout=4, read_timeout=10,
    )
    connection.connect()
    create_connection.assert_called_once_with((PUBLIC_IP, 443), timeout=4, source_address=None)
    assert context.server_hostname == "www.googleapis.com"
    assert raw.timeout == 10


def test_pinned_connection_rejects_peer_ip_mismatch(monkeypatch):
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

    connection = transport._PinnedGoogleHTTPSConnection(
        hostname="oauth2.googleapis.com", destination_ip=PUBLIC_IP, port=443,
        connect_timeout=4, read_timeout=10,
    )
    with pytest.raises(transport.GoogleOAuthTransportError, match="differs"):
        connection.connect()
    assert raw.closed is True


def test_facade_encodes_token_form_and_uses_pinned_request(monkeypatch):
    response = transport.GoogleOAuthHTTPResponse(status_code=200, headers={"Content-Type": "application/json"}, body=b'{"access_token":"synthetic"}')
    request_mock = Mock(return_value=response)
    monkeypatch.setattr(transport, "request_google_oauth", request_mock)
    facade = transport.requests_for_google_oauth()

    result = facade.post(
        "https://oauth2.googleapis.com/token",
        data={"code": "safe-code", "grant_type": "authorization_code"},
        timeout=10,
        allow_redirects=False,
    )

    assert result.json() == {"access_token": "synthetic"}
    args = request_mock.call_args.kwargs
    assert args["method"] == "POST"
    assert args["url"] == "https://oauth2.googleapis.com/token"
    assert args["body"] == b"code=safe-code&grant_type=authorization_code"
    assert args["timeout"] == 10
    assert args["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_facade_rejects_redirect_following_and_unknown_options():
    facade = transport.requests_for_google_oauth()
    with pytest.raises(transport.GoogleOAuthTransportError, match="redirects are disabled"):
        facade.get("https://www.googleapis.com/oauth2/v1/certs", allow_redirects=True)
    with pytest.raises(transport.GoogleOAuthTransportError, match="Unsupported"):
        facade.get("https://www.googleapis.com/oauth2/v1/certs", proxies={"https": "http://proxy.invalid"})


def test_google_auth_request_returns_google_transport_response(monkeypatch):
    expected = transport.GoogleOAuthHTTPResponse(status_code=200, headers={}, body=b'{"keys": []}')
    request_mock = Mock(return_value=expected)
    monkeypatch.setattr(transport, "request_google_oauth", request_mock)

    result = transport.GoogleOAuthAuthRequest()("https://www.googleapis.com/oauth2/v1/certs")

    assert result.status == 200
    assert result.data == b'{"keys": []}'
    request_mock.assert_called_once_with(
        method="GET", url="https://www.googleapis.com/oauth2/v1/certs",
        headers=None, body=None, timeout=10,
    )


def test_google_auth_request_wraps_transport_error(monkeypatch):
    monkeypatch.setattr(
        transport, "request_google_oauth",
        Mock(side_effect=transport.GoogleOAuthTransportError("synthetic network failure")),
    )
    with pytest.raises(google_auth_transport_error_type()):
        transport.GoogleOAuthAuthRequest()("https://www.googleapis.com/oauth2/v1/certs")


def google_auth_transport_error_type():
    from google.auth import exceptions as auth_exceptions
    return auth_exceptions.TransportError
