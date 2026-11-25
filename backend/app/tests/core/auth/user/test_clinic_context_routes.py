from __future__ import annotations

from flask_jwt_extended import decode_token

from app.core.auth.user.routes import auth_routes
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.auth.user.services.user_service import issue_auth_tokens

def _platform_headers(user, clinic_id):
    return {"Authorization": "Bearer " + issue_auth_tokens(user, clinic_context_id=clinic_id)["access_token"]}



def test_get_clinic_context_requires_authentication(
    client,
):
    response = client.get(
        "/api/v1/auth/clinic-context",
    )

    assert response.status_code == 401


def test_get_clinic_context_returns_assigned_clinic_for_normal_user(
    client,
    user,
    clinic,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"] == {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": "assigned",
    }


def test_get_clinic_context_returns_system_for_super_admin_without_context(
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.get(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"] == {
        "clinic_id": None,
        "clinic_name": None,
        "source": "system",
    }


def test_get_clinic_context_returns_selected_clinic_for_super_admin(
    client,
    make_user,
    clinic,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.get(
        "/api/v1/auth/clinic-context",
        headers=_platform_headers(user, clinic.id),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"] == {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": "selected",
    }


def test_get_clinic_context_rejects_inactive_selected_clinic(
    client,
    make_user,
    make_clinic,
    auth_headers_for,
    db,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic(
        status=ClinicStatus.ACTIVE,
    )

    headers = _platform_headers(user, clinic.id)
    clinic.status = ClinicStatus.INACTIVE
    db.session.commit()
    response = client.get(
        "/api/v1/auth/clinic-context",
        headers=headers,
    )

    assert response.status_code == 401

    body = response.get_json()

    assert "data" not in body


def test_select_clinic_context_rejects_unauthenticated(
    client,
    clinic,
):
    response = client.post(
        "/api/v1/auth/clinic-context",
        json={
            "clinic_id": clinic.id,
        },
    )

    assert response.status_code == 401


def test_select_clinic_context_rejects_normal_user(
    client,
    user,
    clinic,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
        json={
            "clinic_id": clinic.id,
        },
    )

    assert response.status_code == 403

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Only a super administrator can select a clinic context"
    )


def test_select_clinic_context_rejects_invalid_payload(
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.post(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
        json={
            "clinic_id": "not-an-integer",
        },
    )

    assert response.status_code == 400

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Validation failed"


def test_select_clinic_context_rejects_unknown_clinic(
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.post(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
        json={
            "clinic_id": 999999,
        },
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Clinic 999999 not found"


def test_select_clinic_context_rejects_inactive_clinic(
    client,
    make_user,
    make_clinic,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic(
        status=ClinicStatus.INACTIVE,
    )

    response = client.post(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
        json={
            "clinic_id": clinic.id,
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == "Selected clinic is not active"


def test_select_clinic_context_returns_tokens_with_selected_context(
    client,
    make_user,
    clinic,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    monkeypatch.setattr(
        auth_routes,
        "create_audit_log",
        lambda **kwargs: None,
        raising=False,
    )

    response = client.post(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
        json={
            "clinic_id": clinic.id,
        },
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["user_id"] == user.id
    assert body["data"]["role"] == Role.SUPER_ADMIN.value
    assert body["data"]["clinic_context_id"] == clinic.id

    access_payload = decode_token(
        body["data"]["access_token"],
    )
    refresh_payload = decode_token(
        body["data"]["refresh_token"],
    )

    for claims in (access_payload, refresh_payload):
        assert claims["platform_clinic_id"] == clinic.id
        assert "tenant_id" not in claims and "clinic_context_id" not in claims


def test_clear_clinic_context_requires_authentication(
    client,
):
    response = client.delete(
        "/api/v1/auth/clinic-context",
    )

    assert response.status_code == 401


def test_clear_clinic_context_rejects_normal_user(
    client,
    user,
    auth_headers_for,
):
    response = client.delete(
        "/api/v1/auth/clinic-context",
        headers=auth_headers_for(user),
    )

    assert response.status_code == 403

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Only a super administrator can clear a clinic context"
    )


def test_clear_clinic_context_passes_previous_context_to_service(
    client,
    make_user,
    clinic,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    captured = {}

    def fake_clear_clinic_context(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        auth_routes,
        "clear_clinic_context",
        fake_clear_clinic_context,
    )

    response = client.delete(
        "/api/v1/auth/clinic-context",
        headers=_platform_headers(user, clinic.id),
    )

    assert response.status_code == 200
    assert captured == {
        "user_id": user.id,
        "clinic_id": clinic.id,
    }


def test_clear_clinic_context_returns_tokens_without_context(
    client,
    make_user,
    clinic,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    monkeypatch.setattr(
        auth_routes,
        "create_audit_log",
        lambda **kwargs: None,
        raising=False,
    )

    response = client.delete(
        "/api/v1/auth/clinic-context",
        headers=_platform_headers(user, clinic.id),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["success"] is True
    assert body["data"]["user_id"] == user.id
    assert body["data"]["role"] == Role.SUPER_ADMIN.value
    assert body["data"]["clinic_context_id"] is None

    access_payload = decode_token(
        body["data"]["access_token"],
    )
    refresh_payload = decode_token(
        body["data"]["refresh_token"],
    )

    assert "clinic_context_id" not in access_payload
    assert "clinic_context_id" not in refresh_payload

    assert "platform_clinic_id" not in access_payload
    assert "platform_clinic_id" not in refresh_payload
    assert "tenant_id" not in access_payload and "tenant_id" not in refresh_payload
