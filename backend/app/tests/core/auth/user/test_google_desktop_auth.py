"""Focused WD01 Google desktop audience + nonce isolation checks."""
from unittest.mock import Mock

import pytest

from app.core.auth.user.services import google_auth_service
from app.core.auth.user.schema.user_schema import GoogleUserInfoSchema
from app.core.exceptions import ValidationError


GOOD_NONCE = "N" * 43
DESKTOP_ID = "761971100065-h4bkpmaujns0pgqc14t9qp5q9su9gabv.apps.googleusercontent.com"


def _claims(nonce=GOOD_NONCE):
    return {
        "iss": "https://accounts.google.com",
        "aud": DESKTOP_ID,
        "sub": "desktop-user-1",
        "email": "patient@example.com",
        "email_verified": True,
        "nonce": nonce,
    }


def test_desktop_validates_exact_configured_audience_and_nonce(app, monkeypatch):
    app.config["GOOGLE_CLIENT_ID"] = "server-web-client-id"
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = DESKTOP_ID
    verifier = Mock(return_value=_claims())
    monkeypatch.setattr(google_auth_service.google_id_token, "verify_oauth2_token", verifier)
    with app.app_context():
        identity = google_auth_service.verify_google_id_token(
            "signed-token", audience=DESKTOP_ID, expected_nonce=GOOD_NONCE
        )
    assert identity.provider_user_id == "desktop-user-1"
    assert identity.email_verified is True
    assert verifier.call_args.kwargs["audience"] == DESKTOP_ID


def test_desktop_rejects_invalid_nonce_after_verified_signature(app, monkeypatch):
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = DESKTOP_ID
    verifier = Mock(return_value=_claims(nonce="A" * 43))
    monkeypatch.setattr(google_auth_service.google_id_token, "verify_oauth2_token", verifier)
    with app.app_context():
        with pytest.raises(ValidationError, match="nonce"):
            google_auth_service.authenticate_google_desktop_id_token(
                id_token="signed-token", nonce=GOOD_NONCE,
            )


def test_desktop_missing_config_fails_before_google_exchange(app, monkeypatch):
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = None
    verifier = Mock()
    monkeypatch.setattr(google_auth_service.google_id_token, "verify_oauth2_token", verifier)
    with app.app_context():
        with pytest.raises(ValidationError, match="not configured"):
            google_auth_service.authenticate_google_desktop_id_token(
                id_token="signed-token", nonce=GOOD_NONCE,
            )
    verifier.assert_not_called()


def test_desktop_delegates_to_existing_patient_and_role_pathway(app, monkeypatch):
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = DESKTOP_ID
    google_user = GoogleUserInfoSchema(
        provider_user_id="desktop-user-1",
        email="patient@example.com",
        email_verified=True,
    )
    verifier = Mock(return_value=google_user)
    authenticate = Mock(return_value={"access_token": "jwt", "refresh_token": "jwt2"})
    monkeypatch.setattr(google_auth_service, "verify_google_id_token", verifier)
    monkeypatch.setattr(google_auth_service, "authenticate_google_user", authenticate)
    cache = Mock()
    cache.set.return_value = True
    monkeypatch.setattr(google_auth_service.extensions, "redis_client", cache)
    with app.app_context():
        result = google_auth_service.authenticate_google_desktop_id_token(
            id_token="signed-token", nonce=GOOD_NONCE,
        )
    assert result["access_token"] == "jwt"
    verifier.assert_called_once_with(
        "signed-token", audience=DESKTOP_ID, expected_nonce=GOOD_NONCE,
    )
    authenticate.assert_called_once_with(google_user)


def test_desktop_route_valid_request_returns_flask_session(client, monkeypatch):
    from app.core.auth.user.routes import auth_routes
    tokens = {
        "access_token": "flask-access",
        "refresh_token": "flask-refresh",
        "user_id": 42,
        "role": "PATIENT",
        "clinic_context_id": None,
    }
    exchange = Mock(return_value=tokens)
    monkeypatch.setattr(auth_routes, "authenticate_google_desktop_id_token", exchange)
    response = client.post(
        "/api/v1/auth/google/desktop",
        json={"id_token": "google-id-token", "nonce": GOOD_NONCE},
    )
    assert response.status_code == 200
    assert response.get_json() == {"success": True, "data": tokens}
    exchange.assert_called_once_with(id_token="google-id-token", nonce=GOOD_NONCE)


def test_desktop_route_bad_nonce_rejected_before_auth(client, monkeypatch):
    from app.core.auth.user.routes import auth_routes
    exchange = Mock()
    monkeypatch.setattr(auth_routes, "authenticate_google_desktop_id_token", exchange)
    response = client.post(
        "/api/v1/auth/google/desktop",
        json={"id_token": "google-id-token", "nonce": "too-short"},
    )
    assert response.status_code == 400
    exchange.assert_not_called()


def test_desktop_replay_is_denied_before_creating_another_session(app, monkeypatch):
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = DESKTOP_ID
    user = GoogleUserInfoSchema(
        provider_user_id="desktop-user-1", email="patient@example.com", email_verified=True,
    )
    monkeypatch.setattr(google_auth_service, "verify_google_id_token", Mock(return_value=user))
    authenticate = Mock(return_value={"access_token": "one"})
    monkeypatch.setattr(google_auth_service, "authenticate_google_user", authenticate)
    cache = Mock()
    cache.set.side_effect = [True, None]
    monkeypatch.setattr(google_auth_service.extensions, "redis_client", cache)
    with app.app_context():
        google_auth_service.authenticate_google_desktop_id_token(
            id_token="signed-token", nonce=GOOD_NONCE,
        )
        with pytest.raises(ValidationError, match="consumed"):
            google_auth_service.authenticate_google_desktop_id_token(
                id_token="signed-token", nonce=GOOD_NONCE,
            )
    assert authenticate.call_count == 1
    assert cache.set.call_count == 2
    args, kwargs = cache.set.call_args
    assert args[0].startswith("wd01:google:desktop:used:")
    assert "signed-token" not in args[0]
    assert kwargs == {"nx": True, "ex": 7200}


def test_desktop_redis_unavailable_fails_closed(app, monkeypatch):
    app.config["GOOGLE_DESKTOP_CLIENT_ID"] = DESKTOP_ID
    user = GoogleUserInfoSchema(
        provider_user_id="desktop-user-1", email="patient@example.com", email_verified=True,
    )
    monkeypatch.setattr(google_auth_service, "verify_google_id_token", Mock(return_value=user))
    authenticate = Mock()
    monkeypatch.setattr(google_auth_service, "authenticate_google_user", authenticate)
    monkeypatch.setattr(google_auth_service.extensions, "redis_client", None)
    with app.app_context():
        with pytest.raises(ValidationError, match="storage unavailable"):
            google_auth_service.authenticate_google_desktop_id_token(
                id_token="signed-token", nonce=GOOD_NONCE,
            )
    authenticate.assert_not_called()
