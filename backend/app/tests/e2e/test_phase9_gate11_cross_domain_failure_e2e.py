from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.enums.role_enums import Role
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseRecord,
    DrugBatch,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
)
from app.modules.pharmacy.services import pharmacy_service


def test_gate11_cross_domain_db_failure_rolls_back_pharmacy_and_recovers(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
    monkeypatch,
):
    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="gate11-receptionist@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate11-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "gate11-pharmacist@test.com",
        },
    )

    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate11-admin@test.com",
    )

    receptionist_login = e2e_login(
        "gate11-receptionist@test.com",
    )
    doctor_login = e2e_login(
        "gate11-doctor@test.com",
    )
    pharmacist_login = e2e_login(
        "gate11-pharmacist@test.com",
    )
    admin_login = e2e_login(
        "gate11-admin@test.com",
    )

    assert receptionist_login["role"] == Role.RECEPTIONIST.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert pharmacist_login["role"] == Role.PHARMACIST.value
    assert admin_login["role"] == Role.ADMIN.value

    auth = lambda login: {
        "Authorization": f"Bearer {login['access_token']}"
    }

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate11",
            "last_name": "Patient",
        },
        headers=auth(receptionist_login),
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = patient_response.get_json()["data"]["id"]

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    scheduled_end = scheduled_start + timedelta(
        minutes=30
    )

    appointment_response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "appointment_type": "in_person",
            "reason": "Gate 11 cross-domain failure E2E",
        },
        headers=auth(doctor_login),
    )

    assert appointment_response.status_code == 201, (
        appointment_response.get_json()
    )

    appointment_id = appointment_response.get_json()["data"]["id"]

    consultation_response = client.post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "appointment_id": appointment_id,
            "consultation_type": "general",
            "chief_complaint": "Gate 11 workflow",
            "symptoms": "Cross-domain failure E2E",
        },
        headers=auth(doctor_login),
    )

    assert consultation_response.status_code == 201, (
        consultation_response.get_json()
    )

    consultation_id = consultation_response.get_json()["data"]["id"]

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate11 Amoxicillin",
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers=auth(pharmacist_login),
    )

    assert drug_response.status_code == 201, (
        drug_response.get_json()
    )

    drug_id = drug_response.get_json()["data"]["id"]

    expiry_date = (
        datetime.now(timezone.utc).date()
        + timedelta(days=365)
    )

    batch_response = client.post(
        "/api/v1/pharmacy/batches",
        json={
            "drug_id": drug_id,
            "batch_number": "GATE11-001",
            "quantity_on_hand": 100,
            "expiry_date": expiry_date.isoformat(),
            "reorder_level": 10,
        },
        headers=auth(pharmacist_login),
    )

    assert batch_response.status_code == 201, (
        batch_response.get_json()
    )

    batch_id = batch_response.get_json()["data"]["id"]

    prescription_response = client.post(
        "/api/v1/prescriptions",
        json={
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
            "notes": "Gate 11 cross-domain workflow",
        },
        headers=auth(doctor_login),
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = prescription_response.get_json()["data"]

    prescription_id = prescription_body["id"]
    prescription_item_id = prescription_body["items"][0]["id"]

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
    persisted_prescription = db.session.get(
        Prescription,
        prescription_id,
    )
    persisted_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    assert persisted_patient is not None
    assert persisted_patient.clinic_id == clinic.id

    assert persisted_appointment is not None
    assert persisted_appointment.clinic_id == clinic.id
    assert persisted_appointment.patient_id == patient_id

    assert persisted_consultation is not None
    assert persisted_consultation.clinic_id == clinic.id
    assert persisted_consultation.patient_id == patient_id
    assert persisted_consultation.appointment_id == appointment_id

    assert persisted_prescription is not None
    assert persisted_prescription.clinic_id == clinic.id
    assert persisted_prescription.patient_id == patient_id
    assert persisted_prescription.consultation_id == consultation_id

    assert persisted_batch is not None
    assert persisted_batch.clinic_id == clinic.id
    assert persisted_batch.quantity_on_hand == 100

    original_commit = pharmacy_service.db.session.commit
    commit_state = {"failed": False}

    def fail_once():
        if not commit_state["failed"]:
            commit_state["failed"] = True

            raise RuntimeError(
                "Gate 11 simulated database commit failure"
            )

        return original_commit()

    monkeypatch.setattr(
        pharmacy_service.db.session,
        "commit",
        fail_once,
    )

    failed_response = client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": prescription_id,
            "items": [
                {
                    "prescription_item_id": prescription_item_id,
                    "batch_id": batch_id,
                    "quantity": 10,
                }
            ],
            "notes": "Gate 11 failure injection",
        },
        headers=auth(pharmacist_login),
    )

    assert failed_response.status_code == 500

    failed_body = failed_response.get_json()

    assert failed_body == {
        "success": False,
        "error": "Internal server error",
    }

    monkeypatch.setattr(
        pharmacy_service.db.session,
        "commit",
        original_commit,
    )

    db.session.expire_all()

    failed_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    failed_prescription = db.session.get(
        Prescription,
        prescription_id,
    )

    failed_dispenses = (
        db.session.execute(
            db.select(DispenseRecord).where(
                DispenseRecord.prescription_id
                == prescription_id,
            )
        )
        .scalars()
        .all()
    )

    assert failed_batch is not None
    assert failed_batch.quantity_on_hand == 100

    assert failed_prescription is not None
    assert failed_prescription.clinic_id == clinic.id

    assert failed_dispenses == []

    recovered_response = client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": prescription_id,
            "items": [
                {
                    "prescription_item_id": prescription_item_id,
                    "batch_id": batch_id,
                    "quantity": 10,
                }
            ],
            "notes": "Gate 11 recovered dispense",
        },
        headers=auth(pharmacist_login),
    )

    assert recovered_response.status_code == 201, (
        recovered_response.get_json()
    )

    recovered_body = recovered_response.get_json()

    assert recovered_body["success"] is True
    assert recovered_body["data"]["prescription_id"] == (
        prescription_id
    )
    assert recovered_body["data"]["dispensed_by_id"] == (
        pharmacist_staff.id
    )
    assert recovered_body["data"]["status"] == "dispensed"

    dispense_id = recovered_body["data"]["id"]

    db.session.expire_all()

    final_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    final_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    final_dispenses = (
        db.session.execute(
            db.select(DispenseRecord).where(
                DispenseRecord.prescription_id
                == prescription_id,
            )
        )
        .scalars()
        .all()
    )

    assert final_batch is not None
    assert final_batch.quantity_on_hand == 90

    assert final_dispense is not None
    assert final_dispense.prescription_id == prescription_id
    assert final_dispense.dispensed_by_id == pharmacist_staff.id
    assert len(final_dispense.items) == 1
    assert final_dispense.items[0].batch_id == batch_id
    assert final_dispense.items[0].prescription_item_id == prescription_item_id
    assert final_dispense.items[0].quantity_dispensed == 10
    assert final_dispense.prescription_id == prescription_id

    assert len(final_dispenses) == 1
    assert final_dispenses[0].id == dispense_id
