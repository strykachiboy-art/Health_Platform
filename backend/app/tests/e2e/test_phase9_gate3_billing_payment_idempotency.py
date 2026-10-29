from __future__ import annotations

from decimal import Decimal

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)


def test_gate3_billing_payment_idempotency_replay_and_conflict(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate3-billing-idempotency-admin@test.com",
    )

    admin_login = e2e_login(
        "gate3-billing-idempotency-admin@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value

    patient = make_patient(
        clinic=clinic,
        first_name="Gate3",
        last_name="BillingIdempotency",
    )

    invoice_key = "gate3-billing-invoice-001"

    invoice_payload = {
        "patient_id": patient.id,
        "currency": "NGN",
        "items": [
            {
                "description": "Gate 3 consultation",
                "quantity": 1,
                "unit_price": "100.00",
            }
        ],
    }

    invoice_headers = {
        "Authorization": (
            f"Bearer {admin_login['access_token']}"
        ),
        "Idempotency-Key": invoice_key,
    }

    first_invoice = client.post(
        "/api/v1/billing/invoices",
        json=invoice_payload,
        headers=invoice_headers,
    )

    assert first_invoice.status_code == 201, (
        first_invoice.get_json()
    )

    first_invoice_id = (
        first_invoice.get_json()["data"]["id"]
    )

    second_invoice = client.post(
        "/api/v1/billing/invoices",
        json=invoice_payload,
        headers=invoice_headers,
    )

    assert second_invoice.status_code == 201, (
        second_invoice.get_json()
    )

    second_invoice_id = (
        second_invoice.get_json()["data"]["id"]
    )

    assert second_invoice_id == first_invoice_id

    conflicting_invoice_payload = {
        **invoice_payload,
        "items": [
            {
                "description": "Gate 3 consultation changed",
                "quantity": 1,
                "unit_price": "125.00",
            }
        ],
    }

    invoice_conflict = client.post(
        "/api/v1/billing/invoices",
        json=conflicting_invoice_payload,
        headers=invoice_headers,
    )

    assert invoice_conflict.status_code == 409, (
        invoice_conflict.get_json()
    )

    invoice_conflict_body = (
        invoice_conflict.get_json()
    )

    assert invoice_conflict_body["success"] is False
    assert invoice_conflict_body["error"] == (
        "Idempotency-Key was already used "
        "with a different request payload"
    )

    invoices = db.session.execute(
        db.select(Invoice).where(
            Invoice.clinic_id == clinic.id,
            Invoice.patient_id == patient.id,
        )
    ).scalars().all()

    assert len(invoices) == 1
    assert invoices[0].id == first_invoice_id
    assert invoices[0].total_amount == Decimal("100.00")

    invoice_create_audits = db.session.execute(
        db.select(AuditLog).where(
            AuditLog.clinic_id == clinic.id,
            AuditLog.entity_type == "Invoice",
            AuditLog.entity_id == first_invoice_id,
            AuditLog.action == AuditAction.CREATE,
        )
    ).scalars().all()

    assert len(invoice_create_audits) == 1

    invoice_records = db.session.execute(
        db.select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == clinic.id,
            IdempotencyRecord.user_id == admin.id,
            IdempotencyRecord.operation
            == "billing.invoice.create",
            IdempotencyRecord.idempotency_key == invoice_key,
        )
    ).scalars().all()

    assert len(invoice_records) == 1
    assert invoice_records[0].entity_type == "Invoice"
    assert invoice_records[0].entity_id == first_invoice_id

    payment_key = "gate3-billing-payment-001"

    payment_payload = {
        "invoice_id": first_invoice_id,
        "amount": "40.00",
        "method": "cash",
    }

    payment_headers = {
        "Authorization": (
            f"Bearer {admin_login['access_token']}"
        ),
        "Idempotency-Key": payment_key,
    }

    first_payment = client.post(
        "/api/v1/billing/payments",
        json=payment_payload,
        headers=payment_headers,
    )

    assert first_payment.status_code == 201, (
        first_payment.get_json()
    )

    first_payment_id = (
        first_payment.get_json()["data"]["id"]
    )

    second_payment = client.post(
        "/api/v1/billing/payments",
        json=payment_payload,
        headers=payment_headers,
    )

    assert second_payment.status_code == 201, (
        second_payment.get_json()
    )

    second_payment_id = (
        second_payment.get_json()["data"]["id"]
    )

    assert second_payment_id == first_payment_id

    conflicting_payment_payload = {
        **payment_payload,
        "amount": "50.00",
    }

    payment_conflict = client.post(
        "/api/v1/billing/payments",
        json=conflicting_payment_payload,
        headers=payment_headers,
    )

    assert payment_conflict.status_code == 409, (
        payment_conflict.get_json()
    )

    payment_conflict_body = (
        payment_conflict.get_json()
    )

    assert payment_conflict_body["success"] is False
    assert payment_conflict_body["error"] == (
        "Idempotency-Key was already used "
        "with a different request payload"
    )

    payments = db.session.execute(
        db.select(Payment).where(
            Payment.invoice_id == first_invoice_id,
        )
    ).scalars().all()

    assert len(payments) == 1
    assert payments[0].id == first_payment_id
    assert payments[0].amount == Decimal("40.00")

    persisted_invoice = db.session.get(
        Invoice,
        first_invoice_id,
    )

    assert persisted_invoice is not None
    assert persisted_invoice.amount_paid == Decimal("40.00")

    payment_audits = db.session.execute(
        db.select(AuditLog).where(
            AuditLog.clinic_id == clinic.id,
            AuditLog.entity_type == "Invoice",
            AuditLog.entity_id == first_invoice_id,
            AuditLog.action == AuditAction.PAYMENT,
        )
    ).scalars().all()

    assert len(payment_audits) == 1

    payment_records = db.session.execute(
        db.select(IdempotencyRecord).where(
            IdempotencyRecord.clinic_id == clinic.id,
            IdempotencyRecord.user_id == admin.id,
            IdempotencyRecord.operation
            == "billing.payment.record",
            IdempotencyRecord.idempotency_key == payment_key,
        )
    ).scalars().all()

    assert len(payment_records) == 1
    assert payment_records[0].entity_type == "Payment"
    assert payment_records[0].entity_id == first_payment_id

    print(
        "PHASE9_GATE3_BILLING_PAYMENT_IDEMPOTENCY=PASS"
    )
