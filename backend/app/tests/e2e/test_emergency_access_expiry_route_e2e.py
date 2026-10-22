from datetime import datetime, timedelta, timezone

from app.core.audit.models.audit_model import AuditLog
from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emergency_access_enums import EmergencyAccessStatus
from app.core.enums.role_enums import Role


def test_expired_emergency_grant_is_denied_by_patient_route(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    make_patient,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="finding4-expiry-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "finding4-expiry-doctor@test.com",
        },
    )

    patient = make_patient(clinic)

    admin_login = e2e_login("finding4-expiry-admin@test.com")
    doctor_login = e2e_login("finding4-expiry-doctor@test.com")

    request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient.id,
            "reason": "Verify expired emergency access is denied.",
            "purpose": "Emergency expiry regression test.",
            "scope": ["patient:read"],
        },
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
        },
    )

    assert request_response.status_code == 201, request_response.get_json()
    emergency_access_id = request_response.get_json()["data"]["id"]

    grant_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/grant",
        json={
            "duration_minutes": 20,
            "review_notes": "Temporary test grant for expiry enforcement.",
        },
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert grant_response.status_code == 200, grant_response.get_json()
    assert (
        grant_response.get_json()["data"]["status"]
        == EmergencyAccessStatus.ACTIVE.value
    )

    grant = db.session.get(EmergencyAccessGrant, emergency_access_id)
    assert grant is not None
    grant.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.session.flush()

    response = client.get(
        f"/api/v1/patients/{patient.id}",
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert response.status_code == 403, response.get_json()

    db.session.expire_all()
    view_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        )
        .scalars()
        .all()
    )

    assert view_audits == [], (
        "An expired emergency grant must not create a successful "
        "emergency-access view audit event."
    )
