from __future__ import annotations

from unittest.mock import Mock

from app.core.auth.user.routes import (
    password_security_routes as routes,
)
from app.core.auth.user.services import (
    password_security_service as service,
)
from app.core.exceptions import (
    ServiceUnavailableError,
    ValidationError,
)


def test_password_reset_request_returns_generic_accepted_response(
    client,
    monkeypatch,
):
    service = Mock()

    monkeypatch.setattr(
        routes,
        "request_password_reset",
        service,
    )

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={
            "email": "user@example.com",
        },
    )

    assert response.status_code == 202

    body = response.get_json()

    assert body == {
        "success": True,
        "message": (
            "If an eligible account exists for that email, "
            "password reset instructions will be sent."
        ),
    }

    service.assert_called_once_with(
        email="user@example.com"
    )


def test_password_reset_request_rejects_invalid_email_without_calling_service(
    client,
    monkeypatch,
):
    service = Mock()

    monkeypatch.setattr(
        routes,
        "request_password_reset",
        service,
    )

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={
            "email": "not-an-email",
        },
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert (
        body["error"]
        == "Validation failed"
    )

    service.assert_not_called()


def test_password_reset_request_surfaces_platform_unavailability_without_account_detail(
    client,
    monkeypatch,
):
    service = Mock(
        side_effect=ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        )
    )

    monkeypatch.setattr(
        routes,
        "request_password_reset",
        service,
    )

    response = client.post(
        "/api/v1/auth/password-reset/request",
        json={
            "email": "user@example.com",
        },
    )

    assert response.status_code == 503

    body = response.get_json()

    assert body == {
        "success": False,
        "error": (
            "Password reset service is temporarily unavailable"
        ),
    }


def test_password_reset_confirm_success(
    client,
    monkeypatch,
):
    service = Mock()

    monkeypatch.setattr(
        routes,
        "confirm_password_reset",
        service,
    )

    token = "t" * 64

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": token,
            "new_password": (
                "ResetStrongPass123!"
            ),
            "confirm_password": (
                "ResetStrongPass123!"
            ),
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert (
        body["data"][
            "reauthentication_required"
        ]
        is True
    )

    service.assert_called_once_with(
        token=token,
        new_password="ResetStrongPass123!",
        confirm_password="ResetStrongPass123!",
        recovery_factor=None,
        ip_address="127.0.0.1",
    )


def test_password_reset_confirm_rejects_short_token_before_service(
    client,
    monkeypatch,
):
    service = Mock()

    monkeypatch.setattr(
        routes,
        "confirm_password_reset",
        service,
    )

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": "short",
            "new_password": (
                "ResetStrongPass123!"
            ),
            "confirm_password": (
                "ResetStrongPass123!"
            ),
        },
    )

    assert response.status_code == 400
    service.assert_not_called()


def test_password_reset_confirm_returns_generic_invalid_token_error(
    client,
    monkeypatch,
):
    service = Mock(
        side_effect=ValidationError(
            "Invalid or expired password reset token"
        )
    )

    monkeypatch.setattr(
        routes,
        "confirm_password_reset",
        service,
    )

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": "t" * 64,
            "new_password": (
                "ResetStrongPass123!"
            ),
            "confirm_password": (
                "ResetStrongPass123!"
            ),
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body == {
        "success": False,
        "error": (
            "Invalid or expired password reset token"
        ),
    }




def test_password_reset_confirm_passes_optional_recovery_factor_as_secret(
    client,
    monkeypatch,
):
    handler = Mock()

    monkeypatch.setattr(
        routes,
        "confirm_password_reset",
        handler,
    )

    token = "t" * 64
    factor = "123456"

    response = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": token,
            "new_password": (
                "ResetStrongPass123!"
            ),
            "confirm_password": (
                "ResetStrongPass123!"
            ),
            "recovery_factor": factor,
        },
    )

    assert response.status_code == 200

    handler.assert_called_once_with(
        token=token,
        new_password="ResetStrongPass123!",
        confirm_password="ResetStrongPass123!",
        recovery_factor=factor,
        ip_address="127.0.0.1",
    )

    assert factor not in response.get_data(
        as_text=True
    )


def test_change_password_requires_authentication(
    client,
):
    response = client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": (
                "supersecret"
            ),
            "new_password": (
                "NewStrongPass123!"
            ),
            "confirm_password": (
                "NewStrongPass123!"
            ),
        },
    )

    assert response.status_code == 401


def test_change_password_success(
    client,
    user,
    auth_headers_for,
    monkeypatch,
):
    service = Mock()
    notice = Mock()

    monkeypatch.setattr(
        routes,
        "change_password",
        service,
    )
    monkeypatch.setattr(
        routes,
        "queue_password_security_notice",
        notice,
    )

    response = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers_for(
            user
        ),
        json={
            "current_password": (
                "supersecret"
            ),
            "new_password": (
                "NewStrongPass123!"
            ),
            "confirm_password": (
                "NewStrongPass123!"
            ),
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert (
        body["data"][
            "reauthentication_required"
        ]
        is True
    )

    service.assert_called_once_with(
        user_id=user.id,
        current_password="supersecret",
        new_password="NewStrongPass123!",
        confirm_password="NewStrongPass123!",
        ip_address="127.0.0.1",
    )

    notice.assert_called_once_with(
        user_id=user.id,
        event="changed",
    )


def test_change_password_rejects_mismatched_confirmation(
    client,
    user,
    auth_headers_for,
    monkeypatch,
):
    service = Mock(
        side_effect=ValidationError(
            "Password confirmation does not match"
        )
    )

    monkeypatch.setattr(
        routes,
        "change_password",
        service,
    )

    response = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers_for(
            user
        ),
        json={
            "current_password": (
                "supersecret"
            ),
            "new_password": (
                "NewStrongPass123!"
            ),
            "confirm_password": (
                "DifferentStrong123!"
            ),
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert (
        body["error"]
        == (
            "Password confirmation does not match"
        )
    )


def test_change_password_validation_does_not_echo_secrets(
    client,
    user,
    auth_headers_for,
):
    secret = "TopSecretPassword123!"

    response = client.post(
        "/api/v1/auth/change-password",
        headers=auth_headers_for(
            user
        ),
        json={
            "current_password": "",
            "new_password": secret,
            "confirm_password": secret,
        },
    )

    assert response.status_code == 400

    body_text = response.get_data(
        as_text=True
    )

    assert secret not in body_text


def test_change_password_invalidates_the_access_token_used_for_the_change(
    client,
    user,
    auth_headers_for,
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_assert_change_password_attempt_allowed",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_record_change_password_failure",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_clear_change_password_failures",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "create_audit_log",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        routes,
        "queue_password_security_notice",
        lambda **kwargs: None,
    )

    headers = auth_headers_for(
        user
    )

    first = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": "supersecret",
            "new_password": "NewStrongPass123!",
            "confirm_password": "NewStrongPass123!",
        },
    )

    assert first.status_code == 200

    second = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": "NewStrongPass123!",
            "new_password": "AnotherStrongPass123!",
            "confirm_password": "AnotherStrongPass123!",
        },
    )

    assert second.status_code == 401
