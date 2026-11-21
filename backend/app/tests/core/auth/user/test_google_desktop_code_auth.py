"""Focused fail-closed desktop OAuth contract tests; no live Google calls."""
from unittest.mock import Mock

import pytest

from app.core.auth.user.routes import auth_routes
from app.core.auth.user.services import google_desktop_auth_service as desktop
from app.core.exceptions import ValidationError


CLIENT_ID = "1234567-desktopapp.apps.googleusercontent.com"
NONCE = "N" * 43
VERIFIER = "V" * 43
REDIRECT = "http://127.0.0.1:49152"
TOKENS = {
    "access_token": "app-access",
    "refresh_token": "app-refresh",
    "user_id": 99,
    "role": "patient",
}


def handoff(**changes):
    payload = dict(code="short-lived-code", code_verifier=VERIFIER,
                   redirect_uri=REDIRECT, nonce=NONCE)
    payload.update(changes)
    return payload


def configure(app):
    app.config["GOOGLE_WINDOWS_CLIENT_ID"] = CLIENT_ID
    app.config["GOOGLE_WINDOWS_CLIENT_SECRET"] = "server-test-only"


def test_desktop_code_route_success(client, app, monkeypatch):
    configure(app)
    service = Mock(return_value=TOKENS)
    monkeypatch.setattr(auth_routes, "authenticate_desktop_google_code", service)
    response = client.post("/api/v1/auth/google/desktop/code", json=handoff())
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "data": TOKENS}
    service.assert_called_once_with(**handoff())


@pytest.mark.parametrize("invalid", [
    {}, {"code": ""}, {"code_verifier": "short"},
    {"redirect_uri": "http://localhost:49152"},
    {"redirect_uri": "http://127.0.0.1:1"},
    {"nonce": "short"}, {"another_field": "forbidden"},
])
def test_desktop_code_route_rejects_invalid_handoff(client, monkeypatch, invalid):
    service = Mock()
    monkeypatch.setattr(auth_routes, "authenticate_desktop_google_code", service)
    body = handoff()
    body.update(invalid)
    if invalid == {}:
        body = {}
    response = client.post("/api/v1/auth/google/desktop/code", json=body)
    assert response.status_code == 400
    service.assert_not_called()


def test_desktop_code_route_does_not_enable_direct_id_token(client):
    response = client.post("/api/v1/auth/google/desktop", json={"id_token": "fake", "nonce": NONCE})
    assert response.status_code == 404


def test_desktop_code_route_domain_error(client, monkeypatch):
    service = Mock(side_effect=ValidationError("Google desktop nonce mismatch"))
    monkeypatch.setattr(auth_routes, "authenticate_desktop_google_code", service)
    response = client.post("/api/v1/auth/google/desktop/code", json=handoff())
    assert response.status_code == 422
    assert response.get_json()["success"] is False
    service.assert_called_once_with(**handoff())


def test_desktop_service_fail_closed_without_config(app, monkeypatch):
    app.config["GOOGLE_WINDOWS_CLIENT_ID"] = ""
    app.config["GOOGLE_WINDOWS_CLIENT_SECRET"] = ""
    post = Mock()
    monkeypatch.setattr(desktop.base.requests, "post", post)
    with pytest.raises(ValidationError, match="not configured"):
        desktop.authenticate_desktop_google_code(**handoff())
    post.assert_not_called()


def mock_exchange(monkeypatch, *, claims=None, id_token="signed-id-token", status=200):
    claims = claims if claims is not None else {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID,
        "sub": "google-user-123", "email": "USER@example.com",
        "email_verified": True, "nonce": NONCE,
    }
    provider = Mock(status_code=status, ok=status == 200)
    provider.json.return_value = {"id_token": id_token, "access_token": "google-access"}
    post = Mock(return_value=provider)
    verification = Mock(return_value=claims)
    login = Mock(return_value=TOKENS)
    monkeypatch.setattr(desktop.base.requests, "post", post)
    monkeypatch.setattr(desktop.google_id_token, "verify_oauth2_token", verification)
    monkeypatch.setattr(desktop.base, "_BoundedGoogleAuthRequest", Mock(return_value=object()))
    monkeypatch.setattr(desktop.base, "authenticate_google_user", login)
    monkeypatch.setattr(desktop, "call_google_auth_with_circuit", lambda *, operation, function: function())
    return post, verification, login


def test_desktop_service_exchanges_code_with_pkce_then_issues_tokens(app, monkeypatch):
    configure(app)
    post, verification, login = mock_exchange(monkeypatch)
    result = desktop.authenticate_desktop_google_code(**handoff())
    assert result == TOKENS
    data = post.call_args.kwargs["data"]
    assert data == {
        "code": "short-lived-code", "client_id": CLIENT_ID,
        "client_secret": "server-test-only", "code_verifier": VERIFIER,
        "redirect_uri": REDIRECT, "grant_type": "authorization_code",
    }
    assert post.call_args.kwargs["allow_redirects"] is False
    assert post.call_args.kwargs["timeout"] == 10
    verification.assert_called_once()
    assert verification.call_args.kwargs["audience"] == CLIENT_ID
    assert login.call_count == 1
    assert login.call_args.args[0].email == "user@example.com"


@pytest.mark.parametrize("overrides", [
    {"nonce": "wrong"}, {"email_verified": False}, {"sub": ""},
    {"azp": "unexpected-app"},
])
def test_desktop_service_rejects_untrusted_claims(app, monkeypatch, overrides):
    configure(app)
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID,
        "sub": "google-user-123", "email": "user@example.com",
        "email_verified": True, "nonce": NONCE,
    }
    claims.update(overrides)
    _, _, login = mock_exchange(monkeypatch, claims=claims)
    with pytest.raises(ValidationError):
        desktop.authenticate_desktop_google_code(**handoff())
    login.assert_not_called()


def test_desktop_service_rejects_signed_token_with_wrong_audience(app, monkeypatch):
    configure(app)
    _, verification, login = mock_exchange(monkeypatch)
    verification.side_effect = ValueError("wrong audience")
    with pytest.raises(ValidationError, match="Invalid Google desktop ID token"):
        desktop.authenticate_desktop_google_code(**handoff())
    login.assert_not_called()


def test_desktop_service_rejects_missing_id_token(app, monkeypatch):
    configure(app)
    _, _, login = mock_exchange(monkeypatch, id_token="")
    with pytest.raises(ValidationError, match="was not returned"):
        desktop.authenticate_desktop_google_code(**handoff())
    login.assert_not_called()


def test_desktop_service_rejects_untrusted_redirect(app, monkeypatch):
    configure(app)
    post = Mock()
    monkeypatch.setattr(desktop.base.requests, "post", post)
    for value in ("http://evil.example:49152", "http://127.0.0.1:65536", "http://127.0.0.1:00000"):
        with pytest.raises(ValidationError):
            desktop.authenticate_desktop_google_code(**handoff(redirect_uri=value))
    post.assert_not_called()


def test_desktop_service_rejects_google_redirect(app, monkeypatch):
    configure(app)
    post, _, login = mock_exchange(monkeypatch, status=302)
    with pytest.raises(ValidationError, match="authorization failed"):
        desktop.authenticate_desktop_google_code(**handoff())
    assert post.call_count == 1
    login.assert_not_called()
