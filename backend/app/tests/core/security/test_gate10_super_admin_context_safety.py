from __future__ import annotations

from flask_jwt_extended import decode_token

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.auth.user.routes import auth_routes


CLINIC_CONTEXT_ENDPOINT = (
    "/api/v1/auth/clinic-context"
)


def test_super_admin_context_selection_and_clear_are_explicit(
    client,
    make_user,
    make_clinic,
    auth_headers_for,
    monkeypatch,
):
    super_admin = make_user(
        role=Role.SUPER_ADMIN,
    )

    clinic = make_clinic(
        name="Gate 10 Selected Clinic",
        status=ClinicStatus.ACTIVE,
    )

    monkeypatch.setattr(
        auth_routes,
        "create_audit_log",
        lambda **kwargs: None,
        raising=False,
    )

    select_response = client.post(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(super_admin),
        json={
            "clinic_id": clinic.id,
        },
    )

    assert select_response.status_code == 200

    selected_body = select_response.get_json()

    assert selected_body["success"] is True
    assert selected_body["data"]["user_id"] == (
        super_admin.id
    )
    assert selected_body["data"]["role"] == (
        Role.SUPER_ADMIN.value
    )
    assert selected_body["data"]["clinic_context_id"] == (
        clinic.id
    )

    selected_access = decode_token(
        selected_body["data"]["access_token"],
    )

    selected_headers = {
        "Authorization": (
            "Bearer "
            + selected_body["data"]["access_token"]
        )
    }

    context_response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=selected_headers,
    )

    assert context_response.status_code == 200

    context_body = context_response.get_json()

    assert context_body["success"] is True
    assert context_body["data"] == {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": "selected",
    }

    assert selected_access["platform_clinic_id"] == (
        clinic.id
    )

    assert "tenant_id" not in selected_access and "clinic_context_id" not in selected_access
    clear_response = client.delete(
        CLINIC_CONTEXT_ENDPOINT,
        headers=selected_headers,
    )

    assert clear_response.status_code == 200

    cleared_body = clear_response.get_json()

    assert cleared_body["success"] is True
    assert cleared_body["data"]["clinic_context_id"] is None

    cleared_access = decode_token(
        cleared_body["data"]["access_token"],
    )

    assert "clinic_context_id" not in cleared_access
    assert "platform_clinic_id" not in cleared_access
    assert "tenant_id" not in cleared_access

    cleared_headers = {
        "Authorization": (
            "Bearer "
            + cleared_body["data"]["access_token"]
        )
    }

    system_response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=cleared_headers,
    )

    assert system_response.status_code == 200

    system_body = system_response.get_json()

    assert system_body["success"] is True
    assert system_body["data"] == {
        "clinic_id": None,
        "clinic_name": None,
        "source": "system",
    }


def test_super_admin_cannot_select_inactive_clinic_as_context(
    client,
    make_user,
    make_clinic,
    auth_headers_for,
):
    super_admin = make_user(
        role=Role.SUPER_ADMIN,
    )

    inactive_clinic = make_clinic(
        name="Gate 10 Inactive Context Clinic",
        status=ClinicStatus.INACTIVE,
    )

    response = client.post(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(super_admin),
        json={
            "clinic_id": inactive_clinic.id,
        },
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Selected clinic is not active"
    )


def test_normal_user_cannot_select_super_admin_context(
    client,
    user,
    clinic,
    auth_headers_for,
):
    response = client.post(
        CLINIC_CONTEXT_ENDPOINT,
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