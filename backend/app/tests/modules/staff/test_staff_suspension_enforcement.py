from __future__ import annotations

import pytest
from flask_jwt_extended import decode_token

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.modules.staff.services.staff_service import change_staff_status


CLINICAL_READ_PATHS = (
    "/api/v1/patients/{patient_id}",
    "/api/v1/consultations/patient/{patient_id}",
    "/api/v1/appointments/patient/{patient_id}",
)


@pytest.mark.parametrize(
    ("method", "path_template", "json_body"),
    [
        ("GET", "/api/v1/patients/{patient_id}", None),
        ("POST", "/api/v1/consultations/", {}),
        ("POST", "/api/v1/appointments/", {}),
    ],
)
@pytest.mark.parametrize(
    "inactive_status",
    [
        StaffStatus.SUSPENDED,
        StaffStatus.TERMINATED,
    ],
)
def test_inactive_staff_with_fresh_jwt_cannot_access_clinical_routes(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    auth_headers_for,
    method,
    path_template,
    json_body,
    inactive_status,
):
    staff, _old_headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    patient = make_patient(clinic)

    change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=inactive_status,
    )

    # Issue a NEW token after the status change: token-version revocation
    # alone cannot be the reason this request is denied.
    fresh_headers = auth_headers_for(
        staff.user,
        role=Role.DOCTOR,
    )
    path = path_template.format(patient_id=patient.id)

    response = client.open(
        path,
        method=method,
        headers=fresh_headers,
        json=json_body,
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == (
        "Staff account is suspended or terminated"
    )


@pytest.mark.parametrize(
    "path_template",
    CLINICAL_READ_PATHS,
)
def test_active_staff_can_still_access_clinical_read_routes(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
    path_template,
):
    _staff, headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )
    patient = make_patient(clinic)

    response = client.get(
        path_template.format(patient_id=patient.id),
        headers=headers,
    )

    assert response.status_code == 200


def test_user_without_linked_staff_record_keeps_existing_role_access(
    client,
    clinic,
    make_user,
    auth_headers_for,
    make_patient,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
    )
    patient = make_patient(clinic)
    headers = auth_headers_for(
        user,
        role=Role.DOCTOR,
    )

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=headers,
    )

    assert response.status_code == 200


def test_status_change_invalidates_existing_jwt(
    client,
    clinic,
    make_authenticated_staff,
    make_patient,
):
    staff, old_headers = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
        status=StaffStatus.ACTIVE,
    )
    patient = make_patient(clinic)

    raw_token = old_headers["Authorization"].split(" ", 1)[1]
    old_claims = decode_token(raw_token)
    original_token_version = old_claims["token_version"]

    change_staff_status(
        staff_id=staff.id,
        clinic_id=clinic.id,
        new_status=StaffStatus.SUSPENDED,
    )

    assert staff.user.token_version == original_token_version + 1

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers=old_headers,
    )

    assert response.status_code == 401
