from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentStatus,
)
from app.core.enums.role_enums import Role
from app.extensions import db
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)
from app.modules.billing.services import billing_service
from app.modules.pharmacy.models.pharmacy_model import (
    DispenseRecord,
    DrugBatch,
)


def _auth(login: dict) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


def test_gate11_cross_domain_billing_payment_failure_rolls_back_and_recovers(
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
        email="gate11-slice5-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate11-slice5-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "gate11-slice5-pharmacist@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="gate11-slice5-receptionist@test.com",
    )

    admin_login = e2e_login(
        "gate11-slice5-admin@test.com",
    )

    doctor_login = e2e_login(
        "gate11-slice5-doctor@test.com",
    )

    pharmacist_login = e2e_login(
        "gate11-slice5-pharmacist@test.com",
    )

    receptionist_login = e2e_login(
        "gate11-slice5-receptionist@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert pharmacist_login["role"] == Role.PHARMACIST.value
    assert receptionist_login["role"] == Role.RECEPTIONIST.value

    # ============================================================
    # PATIENT
    # ============================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate11",
            "last_name": "Slice5 Billing Patient",
        },
        headers=_auth(receptionist_login),
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = patient_response.get_json()["data"]["id"]

    # ============================================================
    # APPOINTMENT
    # ============================================================

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
            "reason": (
                "Gate 11 Slice 5 billing failure"
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

    # ============================================================
    # CONSULTATION
    # ============================================================

    consultation_response = client.post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "appointment_id": appointment_id,
            "consultation_type": "general",
            "chief_complaint": (
                "Gate 11 Slice 5 billing workflow"
            ),
            "symptoms": (
                "Cross-domain payment failure test"
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

    # ============================================================
    # PHARMACY
    # ============================================================

    drug_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate11 Slice5 Amoxicillin",
            "generic_name": "Amoxicillin",
            "dosage_form": "capsule",
            "strength": "500 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers=_auth(admin_login),
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
            "batch_number": "GATE11-S5-001",
            "quantity_on_hand": 100,
            "expiry_date": expiry_date.isoformat(),
            "reorder_level": 10,
        },
        headers=_auth(admin_login),
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
            "notes": (
                "Gate 11 Slice 5 billing workflow"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert prescription_response.status_code == 201, (
        prescription_response.get_json()
    )

    prescription_body = prescription_response.get_json()["data"]

    prescription_id = prescription_body["id"]
    prescription_item_id = prescription_body["items"][0]["id"]

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
            "notes": (
                "Gate 11 Slice 5 completed pharmacy step"
            ),
        },
        headers=_auth(pharmacist_login),
    )

    assert dispense_response.status_code == 201, (
        dispense_response.get_json()
    )

    dispense_id = (
        dispense_response.get_json()["data"]["id"]
    )

    # ============================================================
    # BILLING INVOICE
    # ============================================================

    invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient_id,
            "currency": "NGN",
            "appointment_id": appointment_id,
            "due_date": (
                date.today()
                + timedelta(days=7)
            ).isoformat(),
            "items": [
                {
                    "description": (
                        "Gate 11 Slice 5 consultation"
                    ),
                    "quantity": 1,
                    "unit_price": "100.00",
                },
                {
                    "description": (
                        "Gate 11 Slice 5 pharmacy"
                    ),
                    "quantity": 1,
                    "unit_price": "50.00",
                },
            ],
        },
        headers=_auth(admin_login),
    )

    assert invoice_response.status_code == 201, (
        invoice_response.get_json()
    )

    invoice_body = invoice_response.get_json()

    assert invoice_body["success"] is True
    assert invoice_body["data"]["clinic_id"] == clinic.id
    assert invoice_body["data"]["patient_id"] == patient_id
    assert invoice_body["data"]["appointment_id"] == appointment_id
    assert invoice_body["data"]["total_amount"] == "150.00"
    assert invoice_body["data"]["amount_paid"] == "0.00"
    assert invoice_body["data"]["status"] == "issued"

    invoice_id = invoice_body["data"]["id"]

    persisted_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    persisted_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    persisted_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    assert persisted_invoice is not None
    assert persisted_invoice.clinic_id == clinic.id
    assert persisted_invoice.patient_id == patient_id
    assert persisted_invoice.appointment_id == appointment_id
    assert persisted_invoice.total_amount == Decimal("150.00")
    assert persisted_invoice.amount_paid == Decimal("0.00")
    assert persisted_invoice.status is InvoiceStatus.ISSUED

    assert persisted_batch is not None
    assert persisted_batch.clinic_id == clinic.id
    assert persisted_batch.quantity_on_hand == 90

    assert persisted_dispense is not None
    assert persisted_dispense.prescription_id == prescription_id
    assert persisted_dispense.dispensed_by_id == pharmacist_staff.id

    # ============================================================
    # PAYMENT FAILURE INJECTION
    # ============================================================

    original_commit = billing_service.db.session.commit
    commit_state = {
        "failed": False,
    }

    def fail_once():
        if not commit_state["failed"]:
            commit_state["failed"] = True

            raise RuntimeError(
                "Gate 11 simulated billing payment commit failure"
            )

        return original_commit()

    monkeypatch.setattr(
        billing_service.db.session,
        "commit",
        fail_once,
    )

    failed_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "50.00",
            "method": "cash",
            "reference": "GATE11-S5-FAILURE",
        },
        headers=_auth(admin_login),
    )

    assert failed_payment_response.status_code == 500

    failed_payment_body = (
        failed_payment_response.get_json()
    )

    assert failed_payment_body == {
        "success": False,
        "error": "Internal server error",
    }

    monkeypatch.setattr(
        billing_service.db.session,
        "commit",
        original_commit,
    )

    # ============================================================
    # PAYMENT TRANSACTION MUST ROLLBACK
    # ============================================================

    db.session.expire_all()

    failed_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    failed_payments = (
        db.session.execute(
            db.select(Payment)
            .where(
                Payment.invoice_id == invoice_id,
            )
            .order_by(
                Payment.id.asc(),
            )
        )
        .scalars()
        .all()
    )

    failed_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    failed_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    assert failed_invoice is not None
    assert failed_invoice.clinic_id == clinic.id
    assert failed_invoice.amount_paid == Decimal("0.00")
    assert failed_invoice.status is InvoiceStatus.ISSUED

    assert failed_payments == []

    assert failed_batch is not None
    assert failed_batch.quantity_on_hand == 90

    assert failed_dispense is not None
    assert failed_dispense.prescription_id == prescription_id

    # ============================================================
    # RECOVERY
    # ============================================================

    recovered_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "50.00",
            "method": "cash",
            "reference": "GATE11-S5-RECOVERED",
        },
        headers=_auth(admin_login),
    )

    assert recovered_payment_response.status_code == 201, (
        recovered_payment_response.get_json()
    )

    recovered_payment_body = (
        recovered_payment_response.get_json()
    )

    assert recovered_payment_body["success"] is True
    assert (
        recovered_payment_body["data"]["invoice_id"]
        == invoice_id
    )
    assert (
        recovered_payment_body["data"]["amount"]
        == "50.00"
    )
    assert (
        recovered_payment_body["data"]["status"]
        == PaymentStatus.SUCCESSFUL.value
    )

    payment_id = recovered_payment_body["data"]["id"]

    db.session.expire_all()

    final_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    final_payment = db.session.get(
        Payment,
        payment_id,
    )

    final_payments = (
        db.session.execute(
            db.select(Payment)
            .where(
                Payment.invoice_id == invoice_id,
            )
            .order_by(
                Payment.id.asc(),
            )
        )
        .scalars()
        .all()
    )

    final_batch = db.session.get(
        DrugBatch,
        batch_id,
    )

    final_dispense = db.session.get(
        DispenseRecord,
        dispense_id,
    )

    assert final_invoice is not None
    assert final_invoice.clinic_id == clinic.id
    assert final_invoice.total_amount == Decimal("150.00")
    assert final_invoice.amount_paid == Decimal("50.00")
    assert final_invoice.status is InvoiceStatus.PARTIALLY_PAID

    assert final_payment is not None
    assert final_payment.invoice_id == invoice_id
    assert final_payment.amount == Decimal("50.00")
    assert final_payment.status is PaymentStatus.SUCCESSFUL

    assert len(final_payments) == 1
    assert final_payments[0].id == payment_id

    assert final_batch is not None
    assert final_batch.clinic_id == clinic.id
    assert final_batch.quantity_on_hand == 90

    assert final_dispense is not None
    assert final_dispense.prescription_id == prescription_id
    assert final_dispense.dispensed_by_id == pharmacist_staff.id