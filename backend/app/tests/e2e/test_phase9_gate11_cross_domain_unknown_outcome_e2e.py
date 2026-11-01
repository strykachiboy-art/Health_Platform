from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.prescription.routes import prescription_routes


def _auth(login: dict) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


def test_gate11_cross_domain_unknown_outcome_replays_one_prescription(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
    monkeypatch,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email=(
            f"gate11-s11-admin-{uuid4().hex}"
            "@example.com"
        ),
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": (
                f"gate11-s11-doctor-{uuid4().hex}"
                "@example.com"
            ),
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": (
                f"gate11-s11-pharmacist-{uuid4().hex}"
                "@example.com"
            ),
        },
    )

    admin_login = e2e_login(
        admin.email,
    )

    doctor_login = e2e_login(
        doctor_staff.user.email,
    )

    pharmacist_login = e2e_login(
        pharmacist_staff.user.email,
    )

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate11",
            "last_name": "UnknownOutcome",
        },
        headers=_auth(admin_login),
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = (
        patient_response.get_json()["data"]["id"]
    )

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    appointment_response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "scheduled_start": (
                scheduled_start.isoformat()
            ),
            "scheduled_end": (
                scheduled_start
                + timedelta(minutes=30)
            ).isoformat(),
            "appointment_type": "in_person",
            "reason": (
                "Gate 11 Slice 11 unknown outcome"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert appointment_response.status_code == 201, (
        appointment_response.get_json()
    )

    appointment_id = (
        appointment_response.get_json()["data"]["id"]
    )

    consultation_response = client.post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "appointment_id": appointment_id,
            "consultation_type": "general",
            "chief_complaint": (
                "Gate 11 Slice 11 reconciliation"
            ),
            "symptoms": (
                "Client lost response after "
                "server commit"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert consultation_response.status_code == 201, (
        consultation_response.get_json()
    )

    consultation_id = (
        consultation_response.get_json()["data"]["id"]
    )

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": (
                f"Gate11 S11 Drug "
                f"{uuid4().hex[:8]}"
            ),
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers=_auth(pharmacist_login),
    )

    assert drug_response.status_code == 201, (
        drug_response.get_json()
    )

    drug_id = (
        drug_response.get_json()["data"]["id"]
    )

    operation_key = (
        f"gate11-s11-prescription-{uuid4().hex}"
    )

    payload = {
        "patient_id": patient_id,
        "consultation_id": consultation_id,
        "items": [
            {
                "drug_id": drug_id,
                "dosage": "500 mg",
                "frequency": "twice daily",
                "duration": "5 days",
                "quantity": 10,
                "instructions": "Take after meals",
            }
        ],
        "notes": (
            "Gate 11 Slice 11 unknown outcome"
        ),
    }

    headers = {
        **_auth(doctor_login),
        "Idempotency-Key": operation_key,
    }

    original_jsonify = (
        prescription_routes.jsonify
    )

    response_loss = {
        "injected": False,
    }

    def drop_response_once(
        *args,
        **kwargs,
    ):
        if not response_loss["injected"]:
            response_loss["injected"] = True
            raise ConnectionError(
                "Gate 11 Slice 11 synthetic "
                "client response loss"
            )

        return original_jsonify(
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        prescription_routes,
        "jsonify",
        drop_response_once,
    )

    first_response = client.post(
        "/api/v1/prescriptions",
        json=payload,
        headers=headers,
    )

    assert response_loss["injected"] is True
    assert first_response.status_code == 500, (
        first_response.get_json()
    )

    monkeypatch.setattr(
        prescription_routes,
        "jsonify",
        original_jsonify,
    )

    db.session.expire_all()

    prescriptions_after_unknown = list(
        db.session.scalars(
            db.select(Prescription).where(
                Prescription.clinic_id == clinic.id,
                Prescription.patient_id == patient_id,
                Prescription.consultation_id
                == consultation_id,
            )
        )
    )

    assert len(
        prescriptions_after_unknown
    ) == 1

    committed_prescription = (
        prescriptions_after_unknown[0]
    )

    assert (
        committed_prescription.status.value
        == "active"
    )
    assert (
        committed_prescription.prescribed_by_id
        == doctor_staff.id
    )
    assert len(
        committed_prescription.items
    ) == 1
    assert (
        committed_prescription.items[0].drug_id
        == drug_id
    )
    assert (
        committed_prescription.items[0].quantity
        == 10
    )

    idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id
                == clinic.id,
                IdempotencyRecord.user_id
                == doctor_staff.user_id,
                IdempotencyRecord.operation
                == "prescription.create",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    assert len(
        idempotency_records
    ) == 1

    assert (
        idempotency_records[0].entity_type
        == "Prescription"
    )

    assert (
        idempotency_records[0].entity_id
        == committed_prescription.id
    )

    audits_after_unknown = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type
                == "Prescription",
                AuditLog.entity_id
                == committed_prescription.id,
                AuditLog.action
                == AuditAction.CREATE,
            )
        )
    )

    assert len(
        audits_after_unknown
    ) == 1

    persisted_patient = db.session.get(
        Patient,
        patient_id,
    )

    persisted_appointment = db.session.get(
        Appointment,
        appointment_id,
    )

    persisted_consultation = db.session.get(
        Consultation,
        consultation_id,
    )

    assert persisted_patient is not None
    assert persisted_appointment is not None
    assert persisted_consultation is not None

    baseline = {
        "patient_id": persisted_patient.id,
        "appointment_status": (
            persisted_appointment.status
        ),
        "consultation_status": (
            persisted_consultation.status
        ),
        "prescription_id": (
            committed_prescription.id
        ),
    }

    retry_response = client.post(
        "/api/v1/prescriptions",
        json=payload,
        headers=headers,
    )

    assert retry_response.status_code == 201, (
        retry_response.get_json()
    )

    retry_body = retry_response.get_json()

    assert retry_body["success"] is True
    assert retry_body["data"]["id"] == (
        committed_prescription.id
    )

    db.session.expire_all()

    final_prescriptions = list(
        db.session.scalars(
            db.select(Prescription).where(
                Prescription.clinic_id == clinic.id,
                Prescription.patient_id == patient_id,
                Prescription.consultation_id
                == consultation_id,
            )
        )
    )

    final_idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id
                == clinic.id,
                IdempotencyRecord.user_id
                == doctor_staff.user_id,
                IdempotencyRecord.operation
                == "prescription.create",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    final_audits = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type
                == "Prescription",
                AuditLog.entity_id
                == committed_prescription.id,
                AuditLog.action
                == AuditAction.CREATE,
            )
        )
    )

    assert len(final_prescriptions) == 1
    assert (
        len(final_idempotency_records) == 1
    )
    assert len(final_audits) == 1

    final_appointment = db.session.get(
        Appointment,
        appointment_id,
    )

    final_consultation = db.session.get(
        Consultation,
        consultation_id,
    )

    assert final_appointment is not None
    assert final_consultation is not None

    assert {
        "patient_id": patient_id,
        "appointment_status": (
            final_appointment.status
        ),
        "consultation_status": (
            final_consultation.status
        ),
        "prescription_id": (
            final_prescriptions[0].id
        ),
    } == baseline
