from datetime import datetime, timedelta, timezone

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.appointment_enums import AppointmentType
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import IdempotencyRecord
from app.core.offline_operations.models.offline_operation_disposition_model import (
    OfflineOperationDisposition,
)
from app.modules.appointment.services.appointment_service import create_appointment


ENDPOINT = "/api/v1/offline-operations/{operation_id}/disposition"


def _payload(*, operation_type="appointment.create", key="offline-disposition-key-001", reason_code="manual_reconciliation_no_effect"):
    return {
        "operation_type": operation_type,
        "idempotency_key": key,
        "confirmed_no_effect": True,
        "reason_code": reason_code,
    }


def _post(client, headers, operation_id="offline-operation-001", payload=None):
    return client.post(
        ENDPOINT.format(operation_id=operation_id),
        json=_payload() if payload is None else payload,
        headers=headers,
    )


def test_disposition_framework_records_policy_version_audit_and_idempotently_reuses_record(
    client, db, clinic, make_user, auth_headers_for
):
    user = make_user(clinic=clinic, role=Role.DOCTOR, email="offline-disposition-doctor@test.com")
    headers = auth_headers_for(user)

    first = _post(client, headers)
    assert first.status_code == 201, first.get_json()
    first_data = first.get_json()["data"]
    assert first_data["resolution"] == "confirmed_no_effect"
    assert first_data["operation_id"] == "offline-operation-001"
    assert first_data["policy_version"] == "1"

    second = _post(
        client,
        headers,
        payload=_payload(reason_code="verified_not_sent"),
    )
    assert second.status_code == 200, second.get_json()
    second_data = second.get_json()["data"]
    assert second_data["id"] == first_data["id"]
    assert second_data["reason_code"] == "manual_reconciliation_no_effect"

    dispositions = list(db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.user_id == user.id,
            OfflineOperationDisposition.clinic_id == clinic.id,
        )
    ).scalars())
    assert len(dispositions) == 1

    audits = list(db.session.execute(
        db.select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.clinic_id == clinic.id,
            AuditLog.entity_type == "OfflineOperationDisposition",
            AuditLog.entity_id == first_data["id"],
            AuditLog.action == AuditAction.CREATE,
        )
    ).scalars())
    assert len(audits) == 1
    assert audits[0].new_value == {
        "operation_type": "appointment.create",
        "resolution": "confirmed_no_effect",
        "reason_code": "manual_reconciliation_no_effect",
        "policy_version": "1",
    }


def test_operation_without_registered_policy_is_rejected_without_recording_disposition(
    client, db, clinic, make_user, auth_headers_for
):
    user = make_user(clinic=clinic, role=Role.DOCTOR, email="offline-disposition-unregistered@test.com")
    response = _post(
        client,
        auth_headers_for(user),
        payload=_payload(operation_type="prescription.create"),
    )
    assert response.status_code == 422, response.get_json()
    assert response.get_json()["code"] == "OFFLINE_OPERATION_POLICY_UNAVAILABLE"
    assert db.session.execute(db.select(OfflineOperationDisposition)).scalars().all() == []


def test_disposition_is_rejected_when_server_idempotency_record_exists(
    client, db, clinic, make_user, auth_headers_for
):
    user = make_user(clinic=clinic, role=Role.DOCTOR, email="offline-disposition-prior-attempt@test.com")
    db.session.add(IdempotencyRecord(
        clinic_id=clinic.id,
        user_id=user.id,
        operation="appointment.create",
        idempotency_key="offline-disposition-key-001",
        request_hash="a" * 64,
    ))
    db.session.flush()

    response = _post(client, auth_headers_for(user))
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == "OFFLINE_OPERATION_OUTCOME_REQUIRES_RECONCILIATION"
    assert db.session.execute(db.select(OfflineOperationDisposition)).scalars().all() == []


def test_disposed_idempotency_key_cannot_be_replayed(
    client, db, clinic, make_user, auth_headers_for
):
    user = make_user(clinic=clinic, role=Role.DOCTOR, email="offline-disposition-replay-block@test.com")
    response = _post(client, auth_headers_for(user))
    assert response.status_code == 201, response.get_json()

    now = datetime.now(timezone.utc)
    try:
        create_appointment(
            clinic_id=clinic.id,
            patient_id=20,
            staff_id=30,
            scheduled_start=now,
            scheduled_end=now + timedelta(minutes=30),
            appointment_type=AppointmentType.IN_PERSON,
            idempotency_key="offline-disposition-key-001",
            idempotency_user_id=user.id,
        )
        raise AssertionError("Disposed idempotency key unexpectedly replayed")
    except ConflictError as exc:
        assert exc.code == "OFFLINE_OPERATION_DISPOSED"

    assert db.session.execute(db.select(IdempotencyRecord).where(
        IdempotencyRecord.clinic_id == clinic.id,
        IdempotencyRecord.user_id == user.id,
        IdempotencyRecord.operation == "appointment.create",
        IdempotencyRecord.idempotency_key == "offline-disposition-key-001",
    )).scalar_one_or_none() is None


def test_completed_server_appointment_cannot_be_disposed_as_no_effect(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    make_staff,
    auth_headers_for,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
        email="offline-disposition-existing-effect@test.com",
    )
    patient = make_patient(
        clinic,
        first_name="Disposition",
        last_name="Completed",
    )
    staff = make_staff(clinic=clinic, role=Role.DOCTOR)
    key = "offline-disposition-key-completed"
    start = datetime.now(timezone.utc) + timedelta(days=3)
    appointment = create_appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        scheduled_start=start,
        scheduled_end=start + timedelta(minutes=30),
        appointment_type=AppointmentType.IN_PERSON,
        idempotency_key=key,
        idempotency_user_id=user.id,
    )

    response = _post(
        client,
        auth_headers_for(user),
        operation_id="offline-operation-completed",
        payload=_payload(key=key),
    )
    assert response.status_code == 409, response.get_json()
    assert response.get_json()["code"] == (
        "OFFLINE_OPERATION_OUTCOME_REQUIRES_RECONCILIATION"
    )
    assert db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.user_id == user.id,
            OfflineOperationDisposition.clinic_id == clinic.id,
        )
    ).scalars().all() == []
    assert appointment.id is not None


def test_disposition_rejects_missing_confirmation_and_extra_fields(
    client, make_user, clinic, auth_headers_for
):
    user = make_user(clinic=clinic, role=Role.DOCTOR, email="offline-disposition-invalid@test.com")
    headers = auth_headers_for(user)
    missing_confirmation = _payload()
    missing_confirmation["confirmed_no_effect"] = False
    assert _post(client, headers, payload=missing_confirmation).status_code == 422

    unexpected_field = _payload()
    unexpected_field["patient_data"] = {"patient_name": "must not be accepted"}
    assert _post(client, headers, payload=unexpected_field).status_code == 422


def test_patient_role_cannot_record_disposition(client, clinic, make_user, auth_headers_for):
    user = make_user(clinic=clinic, role=Role.PATIENT, email="offline-disposition-patient@test.com")
    response = _post(client, auth_headers_for(user))
    assert response.status_code == 403, response.get_json()

def test_audit_write_failure_rolls_back_disposition_and_audit(
    client,
    db,
    clinic,
    make_user,
    auth_headers_for,
    monkeypatch,
):
    user = make_user(
        clinic=clinic,
        role=Role.DOCTOR,
        email="offline-disposition-audit-failure@test.com",
    )

    def fail_audit_write(**kwargs):
        raise RuntimeError("injected audit write failure")

    monkeypatch.setattr(
        "app.core.offline_operations.services.disposition_service.create_audit_log",
        fail_audit_write,
    )

    response = _post(client, auth_headers_for(user))

    assert response.status_code == 500, response.get_json()
    remaining_dispositions = db.session.execute(
        db.select(OfflineOperationDisposition).where(
            OfflineOperationDisposition.user_id == user.id,
            OfflineOperationDisposition.clinic_id == clinic.id,
        )
    ).scalars().all()
    assert remaining_dispositions == []

    remaining_audits = db.session.execute(
        db.select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.clinic_id == clinic.id,
            AuditLog.entity_type == "OfflineOperationDisposition",
        )
    ).scalars().all()
    assert remaining_audits == []