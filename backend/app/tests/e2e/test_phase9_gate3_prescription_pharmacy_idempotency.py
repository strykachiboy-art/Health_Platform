from __future__ import annotations

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseItem,
    DispenseRecord,
    DrugBatch,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
)


def _setup_gate3_flow(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    make_staff,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate3-rx-pharmacy-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate3-rx-pharmacy-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "gate3-rx-pharmacy-pharmacist@test.com",
        },
    )

    admin_login = e2e_login(
        "gate3-rx-pharmacy-admin@test.com",
    )

    doctor_login = e2e_login(
        "gate3-rx-pharmacy-doctor@test.com",
    )

    pharmacist_login = e2e_login(
        "gate3-rx-pharmacy-pharmacist@test.com",
    )

    patient = make_patient(
        clinic,
        first_name="Gate3",
        last_name="RxPharmacy",
    )

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate3 Amoxicillin",
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

    drug_id = drug_response.get_json()["data"]["id"]

    batch_response = client.post(
        "/api/v1/pharmacy/batches",
        json={
            "drug_id": drug_id,
            "batch_number": "GATE3-RX-001",
            "expiry_date": "2099-12-31",
            "quantity_on_hand": 100,
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

    batch_id = batch_response.get_json()["data"]["id"]

    return {
        "admin": admin,
        "doctor_staff": doctor_staff,
        "pharmacist_staff": pharmacist_staff,
        "doctor_login": doctor_login,
        "pharmacist_login": pharmacist_login,
        "patient": patient,
        "drug_id": drug_id,
        "batch_id": batch_id,
    }


def test_gate3_prescription_idempotency_replay_and_conflict(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    make_staff,
    e2e_login,
):
    context = _setup_gate3_flow(
        client=client,
        db=db,
        clinic=clinic,
        make_user=make_user,
        make_patient=make_patient,
        make_staff=make_staff,
        e2e_login=e2e_login,
    )

    headers = {
        "Authorization": (
            f"Bearer {context['doctor_login']['access_token']}"
        ),
        "Idempotency-Key": "gate3-prescription-001",
    }

    payload = {
        "patient_id": context["patient"].id,
        "items": [
            {
                "drug_id": context["drug_id"],
                "dosage": "500 mg",
                "frequency": "twice daily",
                "duration": "5 days",
                "quantity": 10,
                "instructions": "Take after meals",
            }
        ],
        "notes": "Gate 3 prescription replay",
    }

    first = client.post(
        "/api/v1/prescriptions",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 201, first.get_json()

    second = client.post(
        "/api/v1/prescriptions",
        json=payload,
        headers=headers,
    )

    assert second.status_code == 201, second.get_json()

    first_id = first.get_json()["data"]["id"]
    second_id = second.get_json()["data"]["id"]

    assert first_id == second_id

    conflicting_payload = {
        **payload,
        "notes": "Gate 3 conflicting payload",
    }

    conflict = client.post(
        "/api/v1/prescriptions",
        json=conflicting_payload,
        headers=headers,
    )

    assert conflict.status_code == 409, conflict.get_json()

    conflict_body = conflict.get_json()

    assert conflict_body["success"] is False
    assert conflict_body["error"] == (
        "Idempotency-Key was already used "
        "with a different request payload"
    )

    prescriptions = list(
        db.session.execute(
            db.select(Prescription).where(
                Prescription.clinic_id == clinic.id,
                Prescription.patient_id
                == context["patient"].id,
            )
        ).scalars()
    )

    assert len(prescriptions) == 1

    audits = list(
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "Prescription",
                AuditLog.entity_id == first_id,
                AuditLog.action == AuditAction.CREATE,
            )
        ).scalars()
    )

    assert len(audits) == 1

    records = list(
        db.session.execute(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.operation
                == "prescription.create",
                IdempotencyRecord.idempotency_key
                == "gate3-prescription-001",
            )
        ).scalars()
    )

    assert len(records) == 1
    assert records[0].entity_type == "Prescription"
    assert records[0].entity_id == first_id


def test_gate3_pharmacy_dispense_idempotency_replay_and_conflict(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    make_staff,
    e2e_login,
):
    context = _setup_gate3_flow(
        client=client,
        db=db,
        clinic=clinic,
        make_user=make_user,
        make_patient=make_patient,
        make_staff=make_staff,
        e2e_login=e2e_login,
    )

    prescription_headers = {
        "Authorization": (
            f"Bearer {context['doctor_login']['access_token']}"
        ),
    }

    prescription_payload = {
        "patient_id": context["patient"].id,
        "items": [
            {
                "drug_id": context["drug_id"],
                "dosage": "500 mg",
                "frequency": "twice daily",
                "duration": "5 days",
                "quantity": 10,
                "instructions": "Take after meals",
            }
        ],
        "notes": "Gate 3 pharmacy prescription",
    }

    prescription_response = client.post(
        "/api/v1/prescriptions",
        json=prescription_payload,
        headers=prescription_headers,
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = prescription_response.get_json()
    prescription_id = prescription_body["data"]["id"]
    prescription_item_id = (
        prescription_body["data"]["items"][0]["id"]
    )

    headers = {
        "Authorization": (
            f"Bearer {context['pharmacist_login']['access_token']}"
        ),
        "Idempotency-Key": "gate3-pharmacy-dispense-001",
    }

    dispense_payload = {
        "prescription_id": prescription_id,
        "items": [
            {
                "prescription_item_id": prescription_item_id,
                "batch_id": context["batch_id"],
                "quantity": 10,
            }
        ],
        "notes": "Gate 3 pharmacy replay",
    }

    first = client.post(
        "/api/v1/pharmacy/dispense",
        json=dispense_payload,
        headers=headers,
    )

    assert first.status_code == 201, first.get_json()

    second = client.post(
        "/api/v1/pharmacy/dispense",
        json=dispense_payload,
        headers=headers,
    )

    assert second.status_code == 201, second.get_json()

    first_id = first.get_json()["data"]["id"]
    second_id = second.get_json()["data"]["id"]

    assert first_id == second_id

    conflicting_payload = {
        **dispense_payload,
        "items": [
            {
                "prescription_item_id": prescription_item_id,
                "batch_id": context["batch_id"],
                "quantity": 5,
            }
        ],
    }

    conflict = client.post(
        "/api/v1/pharmacy/dispense",
        json=conflicting_payload,
        headers=headers,
    )

    assert conflict.status_code == 409, conflict.get_json()

    conflict_body = conflict.get_json()

    assert conflict_body["success"] is False
    assert conflict_body["error"] == (
        "Idempotency-Key was already used "
        "with a different request payload"
    )

    dispense_records = list(
        db.session.execute(
            db.select(DispenseRecord).where(
                DispenseRecord.id == first_id,
            )
        ).scalars()
    )

    assert len(dispense_records) == 1

    dispense_items = list(
        db.session.execute(
            db.select(DispenseItem).where(
                DispenseItem.dispense_record_id == first_id,
            )
        ).scalars()
    )

    assert len(dispense_items) == 1
    assert dispense_items[0].quantity_dispensed == 10

    batch = db.session.get(
        DrugBatch,
        context["batch_id"],
    )

    assert batch is not None
    assert batch.quantity_on_hand == 90

    audits = list(
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "DispenseRecord",
                AuditLog.entity_id == first_id,
                AuditLog.action == AuditAction.CREATE,
            )
        ).scalars()
    )

    assert len(audits) == 1

    records = list(
        db.session.execute(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.operation
                == "pharmacy.dispense",
                IdempotencyRecord.idempotency_key
                == "gate3-pharmacy-dispense-001",
            )
        ).scalars()
    )

    assert len(records) == 1
    assert records[0].entity_type == "DispenseRecord"
    assert records[0].entity_id == first_id