from unittest.mock import Mock

import pytest

from app.core.auth.user.routes import auth_routes
from app.core.auth.user.schema.user_schema import GoogleUserInfoSchema
from app.core.auth.user.services import google_auth_service
from app.core.exceptions import ValidationError


def valid_claims(**overrides):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "server-client-id",
        "sub": "google-subject-123",
        "email": "Person@Example.com",
        "email_verified": True,
        "given_name": "Person",
        "family_name": "Example",
        "picture": "https://example.com/avatar.png",
    }
    claims.update(overrides)
    return claims


def test_verify_google_id_token_uses_configured_audience(
    app,
    monkeypatch,
):
    app.config["GOOGLE_CLIENT_ID"] = "server-client-id"
    claims = valid_claims()
    verifier = Mock(return_value=claims)
    request_object = object()
    request_factory = Mock(return_value=request_object)

    monkeypatch.setattr(
        google_auth_service.google_id_token,
        "verify_oauth2_token",
        verifier,
    )
    monkeypatch.setattr(
        google_auth_service.google_auth_requests,
        "Request",
        request_factory,
    )

    result = google_auth_service.verify_google_id_token("signed-token")

    verifier.assert_called_once_with(
        "signed-token",
        request_object,
        audience="server-client-id",
    )
    assert result.provider_user_id == "google-subject-123"
    assert result.email == "person@example.com"
    assert result.email_verified is True
    assert result.first_name == "Person"


def test_verify_google_id_token_fails_closed_without_client_id(
    app,
    monkeypatch,
):
    app.config["GOOGLE_CLIENT_ID"] = None
    verifier = Mock()
    monkeypatch.setattr(
        google_auth_service.google_id_token,
        "verify_oauth2_token",
        verifier,
    )

    with pytest.raises(
        ValidationError,
        match="Google OAuth client ID is not configured",
    ):
        google_auth_service.verify_google_id_token("signed-token")

    verifier.assert_not_called()


def test_verify_google_id_token_rejects_invalid_token(
    app,
    monkeypatch,
):
    app.config["GOOGLE_CLIENT_ID"] = "server-client-id"
    verifier = Mock(side_effect=ValueError("invalid token"))
    monkeypatch.setattr(
        google_auth_service.google_id_token,
        "verify_oauth2_token",
        verifier,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid Google ID token",
    ):
        google_auth_service.verify_google_id_token("bad-token")


def test_verify_google_id_token_requires_verified_email(
    app,
    monkeypatch,
):
    app.config["GOOGLE_CLIENT_ID"] = "server-client-id"
    monkeypatch.setattr(
        google_auth_service.google_id_token,
        "verify_oauth2_token",
        Mock(return_value=valid_claims(email_verified=False)),
    )

    with pytest.raises(
        ValidationError,
        match="verified email",
    ):
        google_auth_service.verify_google_id_token("signed-token")


def test_verify_google_id_token_requires_subject(
    app,
    monkeypatch,
):
    app.config["GOOGLE_CLIENT_ID"] = "server-client-id"
    monkeypatch.setattr(
        google_auth_service.google_id_token,
        "verify_oauth2_token",
        Mock(return_value=valid_claims(sub="")),
    )

    with pytest.raises(
        ValidationError,
        match="user ID",
    ):
        google_auth_service.verify_google_id_token("signed-token")


def test_authenticate_google_id_token_delegates_to_existing_authentication(
    monkeypatch,
):
    google_user = GoogleUserInfoSchema(
        provider_user_id="google-subject-123",
        email="person@example.com",
        email_verified=True,
    )
    tokens = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "user_id": 42,
        "role": "patient",
    }
    verify = Mock(return_value=google_user)
    authenticate = Mock(return_value=tokens)

    monkeypatch.setattr(
        google_auth_service,
        "verify_google_id_token",
        verify,
    )
    monkeypatch.setattr(
        google_auth_service,
        "authenticate_google_user",
        authenticate,
    )

    result = google_auth_service.authenticate_google_id_token(
        "signed-token"
    )

    assert result == tokens
    verify.assert_called_once_with("signed-token")
    authenticate.assert_called_once_with(google_user)


def test_google_mobile_login_returns_application_tokens(
    client,
    monkeypatch,
):
    tokens = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "user_id": 42,
        "role": "patient",
    }
    authenticate = Mock(return_value=tokens)
    monkeypatch.setattr(
        auth_routes,
        "authenticate_google_id_token",
        authenticate,
    )

    response = client.post(
        "/api/v1/auth/google/mobile",
        json={"id_token": "signed-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "success": True,
        "data": tokens,
    }
    authenticate.assert_called_once_with(id_token="signed-token")


def test_google_mobile_login_rejects_missing_id_token(
    client,
    monkeypatch,
):
    authenticate = Mock()
    monkeypatch.setattr(
        auth_routes,
        "authenticate_google_id_token",
        authenticate,
    )

    response = client.post(
        "/api/v1/auth/google/mobile",
        json={},
    )

    assert response.status_code == 400
    assert response.get_json()["success"] is False
    authenticate.assert_not_called()


def test_google_mobile_login_returns_domain_error_status(
    client,
    monkeypatch,
):
    authenticate = Mock(
        side_effect=ValidationError("Invalid Google ID token")
    )
    monkeypatch.setattr(
        auth_routes,
        "authenticate_google_id_token",
        authenticate,
    )

    response = client.post(
        "/api/v1/auth/google/mobile",
        json={"id_token": "invalid-token"},
    )

    assert response.status_code == 422
    assert response.get_json()["success"] is False
    assert response.get_json()["error"] == "Invalid Google ID token"
    authenticate.assert_called_once_with(id_token="invalid-token")
