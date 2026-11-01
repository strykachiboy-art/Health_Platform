from __future__ import annotations

from uuid import uuid4

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
from app.modules.pharmacy.routes import pharmacy_routes
from app.modules.prescription.models.prescription_model import (
    Prescription,
)


def test_gate12_slice2_pharmacy_network_unknown_outcome_reconciles_one_dispense(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    make_staff,
    auth_headers_for,
    monkeypatch,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email=(
            f"gate12-s2-admin-{uuid4().hex}"
            "@example.com"
        ),
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": (
                f"gate12-s2-doctor-{uuid4().hex}"
                "@example.com"
            ),
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": (
                f"gate12-s2-pharmacist-{uuid4().hex}"
                "@example.com"
            ),
        },
    )

    admin_headers = auth_headers_for(
        admin,
        role=Role.ADMIN,
    )

    doctor_headers = auth_headers_for(
        doctor_staff.user,
        role=Role.DOCTOR,
    )

    pharmacist_headers = auth_headers_for(
        pharmacist_staff.user,
        role=Role.PHARMACIST,
    )

    patient = make_patient(
        clinic,
        first_name="Gate12",
        last_name="PharmacyChaos",
    )

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": (
                f"Gate12 Amoxicillin {uuid4().hex[:8]}"
            ),
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers=pharmacist_headers,
    )

    assert drug_response.status_code == 201, (
        drug_response.get_json()
    )

    drug_id = drug_response.get_json()["data"]["id"]

    batch_response = client.post(
        "/api/v1/pharmacy/batches",
        json={
            "drug_id": drug_id,
            "batch_number": (
                f"GATE12-S2-{uuid4().hex[:10]}"
            ),
            "expiry_date": "2099-12-31",
            "quantity_on_hand": 50,
            "reorder_level": 10,
        },
        headers=pharmacist_headers,
    )

    assert batch_response.status_code == 201, (
        batch_response.get_json()
    )

    batch_id = batch_response.get_json()["data"]["id"]

    prescription_response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": patient.id,
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
            "notes": "Gate 12 Slice 2 pharmacy chaos",
        },
        headers=doctor_headers,
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = prescription_response.get_json()
    prescription_id = prescription_body["data"]["id"]
    prescription_item_id = (
        prescription_body["data"]["items"][0]["id"]
    )

    operation_key = (
        f"gate12-s2-pharmacy-dispense-{uuid4().hex}"
    )

    dispense_payload = {
        "prescription_id": prescription_id,
        "items": [
            {
                "prescription_item_id": prescription_item_id,
                "batch_id": batch_id,
                "quantity": 10,
            }
        ],
        "notes": "Gate 12 Slice 2 network outage",
    }

    headers = {
        **pharmacist_headers,
        "Idempotency-Key": operation_key,
    }

    original_jsonify = pharmacy_routes.jsonify
    response_loss = {
        "injected": False,
    }

    def lose_pharmacy_response(
        *args,
        **kwargs,
    ):
        if not response_loss["injected"]:
            response_loss["injected"] = True
            raise ConnectionError(
                "Gate 12 Slice 2 simulated "
                "pharmacy network response loss"
            )

        return original_jsonify(
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        pharmacy_routes,
        "jsonify",
        lose_pharmacy_response,
    )

    first_response = client.post(
        "/api/v1/pharmacy/dispense",
        json=dispense_payload,
        headers=headers,
    )

    assert response_loss["injected"] is True
    assert first_response.status_code == 500
    assert first_response.get_json() == {
        "success": False,
        "error": "Internal server error",
    }

    monkeypatch.setattr(
        pharmacy_routes,
        "jsonify",
        original_jsonify,
    )

    db.session.expire_all()

    committed_dispenses = list(
        db.session.scalars(
            db.select(DispenseRecord).where(
                DispenseRecord.prescription_id
                == prescription_id,
            )
        )
    )

    assert len(committed_dispenses) == 1

    committed = committed_dispenses[0]

    assert committed.prescription_id == prescription_id
    assert committed.dispensed_by_id == pharmacist_staff.id
    assert committed.status.value == "dispensed"

    committed_items = list(
        db.session.scalars(
            db.select(DispenseItem).where(
                DispenseItem.dispense_record_id
                == committed.id,
            )
        )
    )

    assert len(committed_items) == 1
    assert committed_items[0].prescription_item_id == (
        prescription_item_id
    )
    assert committed_items[0].batch_id == batch_id
    assert committed_items[0].quantity_dispensed == 10

    batch_after_unknown = db.session.get(
        DrugBatch,
        batch_id,
    )

    assert batch_after_unknown is not None
    assert batch_after_unknown.quantity_on_hand == 40

    idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id
                == pharmacist_staff.user_id,
                IdempotencyRecord.operation
                == "pharmacy.dispense",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    assert len(idempotency_records) == 1

    idempotency_record = idempotency_records[0]

    assert idempotency_record.entity_type == "DispenseRecord"
    assert idempotency_record.entity_id == committed.id

    audits = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "DispenseRecord",
                AuditLog.entity_id == committed.id,
                AuditLog.action == AuditAction.CREATE,
            )
        )
    )

    assert len(audits) == 1

    retry_response = client.post(
        "/api/v1/pharmacy/dispense",
        json=dispense_payload,
        headers=headers,
    )

    assert retry_response.status_code == 201, (
        retry_response.get_json()
    )

    retry_body = retry_response.get_json()

    assert retry_body["success"] is True
    assert retry_body["data"]["id"] == committed.id
    assert retry_body["data"]["prescription_id"] == (
        prescription_id
    )
    assert retry_body["data"]["status"] == "dispensed"

    db.session.expire_all()

    final_dispenses = list(
        db.session.scalars(
            db.select(DispenseRecord).where(
                DispenseRecord.prescription_id
                == prescription_id,
            )
        )
    )

    final_items = list(
        db.session.scalars(
            db.select(DispenseItem).where(
                DispenseItem.dispense_record_id
                == committed.id,
            )
        )
    )

    final_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    final_idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id
                == pharmacist_staff.user_id,
                IdempotencyRecord.operation
                == "pharmacy.dispense",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    final_audits = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "DispenseRecord",
                AuditLog.entity_id == committed.id,
                AuditLog.action == AuditAction.CREATE,
            )
        )
    )

    assert len(final_dispenses) == 1
    assert len(final_items) == 1
    assert final_batch is not None
    assert final_batch.quantity_on_hand == 40
    assert len(final_idempotency_records) == 1
    assert len(final_audits) == 1

    prescription = db.session.get(
        Prescription,
        prescription_id,
    )

    assert prescription is not None
    assert prescription.clinic_id == clinic.id
    assert prescription.patient_id == patient.id