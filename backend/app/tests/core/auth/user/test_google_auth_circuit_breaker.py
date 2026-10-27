from unittest.mock import MagicMock, Mock
import pytest
from google.auth import exceptions as google_auth_exceptions

from app import extensions
from app.core.auth.user.services import google_auth_service
from app.core.auth.user.services.google_auth_circuit_breaker import (
    CircuitProtectedGoogleAuthRequest,
    GOOGLE_AUTH_PROVIDER_TIMEOUT_SECONDS,
    GoogleAuthTransientError,
    call_google_auth_with_circuit,
    classify_google_auth_circuit_failure,
    get_google_auth_circuit,
    is_transient_google_http_status,
)
from app.core.auth.user.services.google_oauth_transport import GoogleOAuthTransportError
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker import CircuitBreakerOpenError
from app.core.resilience.circuit_breaker_store import CircuitState, RedisCircuitBreakerStore


def _configure(app):
    app.config.update(
        CIRCUIT_BREAKER_ENABLED=True,
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        CIRCUIT_BREAKER_OPEN_SECONDS=30,
        CIRCUIT_BREAKER_PROBE_LEASE_SECONDS=60,
        CIRCUIT_BREAKER_STATE_TTL_SECONDS=3600,
    )


def _store(operation):
    return RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=f"google:{operation}",
    )


@pytest.fixture
def clean_google_auth_circuits(app):
    ops = ("token_exchange", "userinfo", "id_token_transport")
    with app.app_context():
        for op in ops:
            _store(op).delete()
    yield
    with app.app_context():
        for op in ops:
            _store(op).delete()


def test_transient_google_http_status_contract():
    for status in (408, 425, 429, 500, 503):
        assert is_transient_google_http_status(status)
    for status in (200, 302, 400, 401, 403, 404):
        assert not is_transient_google_http_status(status)


def test_invalid_authorization_code_does_not_open_circuit(app, clean_google_auth_circuits):
    with app.app_context():
        _configure(app)
        with pytest.raises(ValidationError):
            call_google_auth_with_circuit(
                operation="token_exchange",
                function=Mock(side_effect=ValidationError("bad code")),
            )
        snap = _store("token_exchange").snapshot()
        assert snap.state is CircuitState.CLOSED
        assert snap.failure_count == 0


def test_separate_google_auth_circuits(app, clean_google_auth_circuits):
    with app.app_context():
        _configure(app)
        with pytest.raises(GoogleAuthTransientError):
            call_google_auth_with_circuit(
                operation="token_exchange",
                function=Mock(side_effect=GoogleAuthTransientError("temporary")),
            )
        assert _store("token_exchange").snapshot().state is CircuitState.OPEN
        assert _store("userinfo").snapshot().state is CircuitState.CLOSED


def test_id_token_transport_open_short_circuits_certificate_fetch(
    app, clean_google_auth_circuits, monkeypatch
):
    error = google_auth_exceptions.TransportError("transport")
    error.__cause__ = GoogleOAuthTransportError("network")
    request_mock = Mock(side_effect=error)
    monkeypatch.setattr(
        "app.core.auth.user.services.google_auth_circuit_breaker."
        "GoogleOAuthAuthRequest.__call__",
        request_mock,
    )
    with app.app_context():
        _configure(app)
        request = CircuitProtectedGoogleAuthRequest()
        with pytest.raises(google_auth_exceptions.TransportError):
            request("https://www.googleapis.com/oauth2/v1/certs")
        with pytest.raises(CircuitBreakerOpenError):
            request("https://www.googleapis.com/oauth2/v1/certs")
    assert request_mock.call_count == 1


def test_google_auth_redis_failure_fails_open(app, monkeypatch):
    class BrokenRedis:
        def eval(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")
        def lpush(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

    provider_call = Mock(return_value={"ok": True})
    with app.app_context():
        _configure(app)
        monkeypatch.setattr(extensions, "redis_client", BrokenRedis())
        assert call_google_auth_with_circuit(
            operation="userinfo",
            function=provider_call,
        ) == {"ok": True}
    provider_call.assert_called_once()


def test_google_auth_probe_lease_must_exceed_provider_timeout(app):
    with app.app_context():
        _configure(app)
        app.config["CIRCUIT_BREAKER_PROBE_LEASE_SECONDS"] = GOOGLE_AUTH_PROVIDER_TIMEOUT_SECONDS
        with pytest.raises(ValidationError, match="must exceed Google auth provider timeout"):
            get_google_auth_circuit(operation="token_exchange")


def test_exchange_google_code_500_counts_and_second_call_short_circuits(
    app, clean_google_auth_circuits, monkeypatch
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "client-secret"
    app.config["GOOGLE_REDIRECT_URI"] = "http://localhost/callback"
    response = MagicMock()
    response.status_code = 503
    response.ok = False
    post = Mock(return_value=response)
    monkeypatch.setattr(google_auth_service.requests, "post", post)

    with app.app_context():
        _configure(app)
        with pytest.raises(ValidationError, match="temporarily unavailable"):
            google_auth_service.exchange_google_code("code")
        with pytest.raises(CircuitBreakerOpenError):
            google_auth_service.exchange_google_code("code")
    assert post.call_count == 1


def test_exchange_google_code_400_does_not_open(
    app, clean_google_auth_circuits, monkeypatch
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "client-secret"
    app.config["GOOGLE_REDIRECT_URI"] = "http://localhost/callback"
    response = MagicMock()
    response.status_code = 400
    response.ok = False
    post = Mock(return_value=response)
    monkeypatch.setattr(google_auth_service.requests, "post", post)

    with app.app_context():
        _configure(app)
        for _ in range(2):
            with pytest.raises(ValidationError, match="code exchange failed"):
                google_auth_service.exchange_google_code("bad-code")
    assert post.call_count == 2
