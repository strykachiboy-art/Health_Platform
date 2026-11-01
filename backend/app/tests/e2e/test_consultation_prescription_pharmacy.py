from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseItem,
    DispenseRecord,
    Drug,
    DrugBatch,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)


def test_consultation_prescription_pharmacy_e2e(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="e2e-rx-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-rx-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "e2e-rx-pharmacist@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="e2e-rx-receptionist@test.com",
    )

    admin_login = e2e_login(
        "e2e-rx-admin@test.com",
    )

    doctor_login = e2e_login(
        "e2e-rx-doctor@test.com",
    )

    pharmacist_login = e2e_login(
        "e2e-rx-pharmacist@test.com",
    )

    receptionist_login = e2e_login(
        "e2e-rx-receptionist@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert pharmacist_login["role"] == Role.PHARMACIST.value
    assert receptionist_login["role"] == Role.RECEPTIONIST.value

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Prescription",
            "last_name": "Patient",
        },
        headers={
            "Authorization": (
                f"Bearer {receptionist_login['access_token']}"
            ),
        },
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = patient_response.get_json()["data"]["id"]

    persisted_patient = db.session.get(
        Patient,
        patient_id,
    )

    assert persisted_patient is not None
    assert persisted_patient.clinic_id == clinic.id

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    scheduled_end = (
        scheduled_start
        + timedelta(minutes=30)
    )

    appointment_response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "appointment_type": "in_person",
            "reason": "Phase 8 prescription workflow",
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
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
            "chief_complaint": "Prescription workflow",
            "symptoms": "Phase 8 E2E",
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
    )

    assert consultation_response.status_code == 201, (
        consultation_response.get_json()
    )

    consultation_id = consultation_response.get_json()["data"]["id"]

    persisted_consultation = db.session.get(
        Consultation,
        consultation_id,
    )

    assert persisted_consultation is not None
    assert persisted_consultation.clinic_id == clinic.id
    assert persisted_consultation.patient_id == patient_id
    assert persisted_consultation.appointment_id == appointment_id

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Amoxicillin E2E",
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert drug_response.status_code == 201, (
        drug_response.get_json()
    )

    drug_body = drug_response.get_json()

    assert drug_body["success"] is True
    assert drug_body["data"]["clinic_id"] == clinic.id
    assert drug_body["data"]["name"] == "Amoxicillin E2E"

    drug_id = drug_body["data"]["id"]

    persisted_drug = db.session.get(
        Drug,
        drug_id,
    )

    assert persisted_drug is not None
    assert persisted_drug.clinic_id == clinic.id
    assert persisted_drug.is_active is True

    expiry_date = (
        datetime.now(timezone.utc).date()
        + timedelta(days=365)
    )

    batch_response = client.post(
        "/api/v1/pharmacy/batches",
        json={
            "drug_id": drug_id,
            "batch_number": "RX-E2E-001",
            "quantity_on_hand": 100,
            "expiry_date": expiry_date.isoformat(),
            "reorder_level": 10,
        },
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert batch_response.status_code == 201, (
        batch_response.get_json()
    )

    batch_body = batch_response.get_json()

    assert batch_body["success"] is True
    assert batch_body["data"]["clinic_id"] == clinic.id
    assert batch_body["data"]["drug_id"] == drug_id
    assert batch_body["data"]["quantity_on_hand"] == 100

    batch_id = batch_body["data"]["id"]

    persisted_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    assert persisted_batch is not None
    assert persisted_batch.clinic_id == clinic.id
    assert persisted_batch.drug_id == drug_id
    assert persisted_batch.quantity_on_hand == 100

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
            "notes": "Phase 8 prescription workflow",
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = prescription_response.get_json()

    assert prescription_body["success"] is True
    assert prescription_body["data"]["clinic_id"] == clinic.id
    assert prescription_body["data"]["patient_id"] == patient_id
    assert (
        prescription_body["data"]["consultation_id"]
        == consultation_id
    )
    assert (
        prescription_body["data"]["prescribed_by_id"]
        == doctor_staff.id
    )
    assert prescription_body["data"]["status"] == "active"
    assert len(prescription_body["data"]["items"]) == 1

    prescription_id = prescription_body["data"]["id"]
    prescription_item_id = (
        prescription_body["data"]["items"][0]["id"]
    )

    persisted_prescription = db.session.get(
        Prescription,
        prescription_id,
    )

    assert persisted_prescription is not None
    assert persisted_prescription.clinic_id == clinic.id
    assert persisted_prescription.patient_id == patient_id
    assert (
        persisted_prescription.consultation_id
        == consultation_id
    )
    assert (
        persisted_prescription.prescribed_by_id
        == doctor_staff.id
    )

    persisted_prescription_item = db.session.get(
        PrescriptionItem,
        prescription_item_id,
    )

    assert persisted_prescription_item is not None
    assert (
        persisted_prescription_item.prescription_id
        == prescription_id
    )
    assert persisted_prescription_item.drug_id == drug_id
    assert persisted_prescription_item.quantity == 10

    prescription_get_response = client.get(
        f"/api/v1/prescriptions/{prescription_id}",
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert prescription_get_response.status_code == 200, (
        prescription_get_response.get_json()
    )

    prescription_get_body = (
        prescription_get_response.get_json()
    )

    assert prescription_get_body["success"] is True
    assert (
        prescription_get_body["data"]["id"]
        == prescription_id
    )
    assert (
        prescription_get_body["data"]["items"][0]["quantity"]
        == 10
    )

    dispense_response = client.post(
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
            "notes": "Phase 8 pharmacy dispense",
        },
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert dispense_response.status_code == 201, (
        dispense_response.get_json()
    )

    dispense_body = dispense_response.get_json()

    assert dispense_body["success"] is True
    assert (
        dispense_body["data"]["prescription_id"]
        == prescription_id
    )
    assert (
        dispense_body["data"]["dispensed_by_id"]
        == pharmacist_staff.id
    )
    assert dispense_body["data"]["status"] == "dispensed"
    assert len(dispense_body["data"]["items"]) == 1

    dispense_record_id = dispense_body["data"]["id"]

    persisted_dispense = db.session.get(
        DispenseRecord,
        dispense_record_id,
    )

    assert persisted_dispense is not None
    assert (
        persisted_dispense.prescription_id
        == prescription_id
    )
    assert (
        persisted_dispense.dispensed_by_id
        == pharmacist_staff.id
    )
    assert persisted_dispense.status.value == "dispensed"
    assert persisted_dispense.dispensed_at is not None

    persisted_dispense_item = db.session.execute(
        db.select(DispenseItem)
        .where(
            DispenseItem.dispense_record_id
            == dispense_record_id
        )
    ).scalars().first()

    assert persisted_dispense_item is not None
    assert (
        persisted_dispense_item.prescription_item_id
        == prescription_item_id
    )
    assert persisted_dispense_item.batch_id == batch_id
    assert persisted_dispense_item.quantity_dispensed == 10

    persisted_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    assert persisted_batch is not None
    assert persisted_batch.quantity_on_hand == 90

    stock_response = client.get(
        f"/api/v1/pharmacy/drugs/{drug_id}/stock-summary",
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert stock_response.status_code == 200, (
        stock_response.get_json()
    )

    stock_body = stock_response.get_json()

    assert stock_body["success"] is True
    assert stock_body["data"]["clinic_id"] == clinic.id
    assert stock_body["data"]["drug_id"] == drug_id
    assert stock_body["data"]["quantity_on_hand"] == 90
    assert stock_body["data"]["batch_count"] == 1

    dispense_get_response = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record_id}",
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert dispense_get_response.status_code == 200, (
        dispense_get_response.get_json()
    )

    dispense_get_body = (
        dispense_get_response.get_json()
    )

    assert dispense_get_body["success"] is True
    assert (
        dispense_get_body["data"]["id"]
        == dispense_record_id
    )
    assert (
        dispense_get_body["data"]["status"]
        == "dispensed"
    )

    complete_response = client.post(
        f"/api/v1/prescriptions/{prescription_id}/complete",
        json={},
        headers={
            "Authorization": (
                f"Bearer {pharmacist_login['access_token']}"
            ),
        },
    )

    assert complete_response.status_code == 200, (
        complete_response.get_json()
    )

    complete_body = complete_response.get_json()

    assert complete_body["success"] is True
    assert complete_body["data"]["status"] == "completed"

    persisted_prescription = db.session.get(
        Prescription,
        prescription_id,
    )

    assert persisted_prescription is not None
    assert persisted_prescription.status.value == "completed"

    audit_rows = list(
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_id.in_(
                    [
                        drug_id,
                        batch_id,
                        prescription_id,
                        dispense_record_id,
                    ]
                )
            )
            .order_by(
                AuditLog.id.asc(),
            )
        ).scalars()
    )

    assert audit_rows
    assert all(
        row.clinic_id == clinic.id
        for row in audit_rows
    )

    assert any(
        row.entity_type == "Drug"
        and row.entity_id == drug_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_type == "DrugBatch"
        and row.entity_id == batch_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_type == "Prescription"
        and row.entity_id == prescription_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_type == "Prescription"
        and row.entity_id == prescription_id
        and row.action is AuditAction.STATUS_CHANGE
        for row in audit_rows
    )

    assert any(
        row.entity_type == "DispenseRecord"
        and row.entity_id == dispense_record_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    second_clinic = clinic.__class__(
        name="E2E Prescription Clinic 2",
        ai_credits=5,
        status=clinic.status,
    )

    db.session.add(second_clinic)
    db.session.flush()

    second_pharmacist_staff = make_staff(
        clinic=second_clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "e2e-rx-pharmacist-clinic-2@test.com",
        },
    )

    second_pharmacist_login = e2e_login(
        "e2e-rx-pharmacist-clinic-2@test.com",
    )

    assert (
        second_pharmacist_login["role"]
        == Role.PHARMACIST.value
    )

    cross_tenant_prescription_response = client.get(
        f"/api/v1/prescriptions/{prescription_id}",
        headers={
            "Authorization": (
                f"Bearer {second_pharmacist_login['access_token']}"
            ),
        },
    )

    assert cross_tenant_prescription_response.status_code in (
        400,
        404,
        422,
    )

    cross_tenant_dispense_response = client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": prescription_id,
            "items": [
                {
                    "prescription_item_id": prescription_item_id,
                    "batch_id": batch_id,
                    "quantity": 1,
                }
            ],
        },
        headers={
            "Authorization": (
                f"Bearer {second_pharmacist_login['access_token']}"
            ),
        },
    )

    assert cross_tenant_dispense_response.status_code == 404

    foreign_dispense_get_response = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record_id}",
        headers={
            "Authorization": (
                f"Bearer {second_pharmacist_login['access_token']}"
            ),
        },
    )

    assert foreign_dispense_get_response.status_code == 404

    print("PHASE8_E2E_CONSULTATION_PRESCRIPTION_PHARMACY=PASS")
