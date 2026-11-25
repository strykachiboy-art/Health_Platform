from __future__ import annotations

import pytest

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from flask_jwt_extended import create_access_token

def _forged(user, **claims):
    token = create_access_token(identity=str(user.id), additional_claims={"role": user.role.value, "token_version": user.token_version, **claims})
    return {"Authorization": "Bearer " + token}



CLINIC_CONTEXT_ENDPOINT = (
    "/api/v1/auth/clinic-context"
)


def test_normal_user_cannot_switch_clinic_with_forged_jwt_context(
    client,
    make_clinic,
    make_user,
    auth_headers_for,
):
    assigned_clinic = make_clinic(
        name="Gate 10 Assigned Clinic",
    )

    foreign_clinic = make_clinic(
        name="Gate 10 Foreign Clinic",
    )

    user = make_user(
        clinic=assigned_clinic,
        role=Role.ADMIN,
    )

    response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(
            user,
            clinic_context_id=foreign_clinic.id,
        ),
    )

    assert response.status_code == 401
    assert "data" not in response.get_json()


@pytest.mark.parametrize(
    "tampered_context",
    [
        "not-an-integer",
        True,
        False,
        0,
        -1,
    ],
)
def test_super_admin_rejects_malformed_jwt_clinic_context(
    client,
    make_user,
    auth_headers_for,
    tampered_context,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(
            user,
            clinic_context_id=tampered_context,
        ),
    )

    assert response.status_code == 401
    assert "data" not in response.get_json()
    response = client.get(CLINIC_CONTEXT_ENDPOINT, headers=_forged(user, platform_clinic_id=tampered_context))
    assert response.status_code == 401
    assert "data" not in response.get_json()


def test_super_admin_rejects_nonexistent_jwt_clinic_context(
    client,
    make_user,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(
            user,
            clinic_context_id=999999,
        ),
    )

    assert response.status_code == 401
    assert "data" not in response.get_json()
    response = client.get(CLINIC_CONTEXT_ENDPOINT, headers=_forged(user, platform_clinic_id=999999))
    assert response.status_code == 401
    assert "data" not in response.get_json()


def test_super_admin_rejects_inactive_jwt_clinic_context(
    client,
    make_user,
    make_clinic,
    auth_headers_for,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )

    inactive_clinic = make_clinic(
        name="Gate 10 Inactive Clinic",
        status=ClinicStatus.INACTIVE,
    )

    response = client.get(
        CLINIC_CONTEXT_ENDPOINT,
        headers=auth_headers_for(
            user,
            clinic_context_id=inactive_clinic.id,
        ),
    )

    assert response.status_code == 401
    assert "data" not in response.get_json()
    response = client.get(CLINIC_CONTEXT_ENDPOINT, headers=_forged(user, platform_clinic_id=inactive_clinic.id))
    assert response.status_code == 401
    assert "data" not in response.get_json()
