from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
)
from app.core.enums.role_enums import Role
from app.modules.appointment.models.appointment_model import Appointment
from app.modules.billing.models.billing_model import (
    Invoice,
    InvoiceItem,
    Payment,
)
from app.modules.patient.models.patient_model import Patient


def test_billing_e2e(
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
        email="e2e-billing-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-billing-doctor@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="e2e-billing-receptionist@test.com",
    )

    admin_login = e2e_login(
        "e2e-billing-admin@test.com",
    )

    doctor_login = e2e_login(
        "e2e-billing-doctor@test.com",
    )

    receptionist_login = e2e_login(
        "e2e-billing-receptionist@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert receptionist_login["role"] == Role.RECEPTIONIST.value

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Billing",
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
            "reason": "Phase 8 billing workflow",
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

    appointment_id = (
        appointment_response.get_json()["data"]["id"]
    )

    persisted_appointment = db.session.get(
        Appointment,
        appointment_id,
    )

    assert persisted_appointment is not None
    assert persisted_appointment.clinic_id == clinic.id
    assert persisted_appointment.patient_id == patient_id
    assert (
        persisted_appointment.staff_id
        == doctor_staff.id
    )

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
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                },
                {
                    "description": "Laboratory service",
                    "quantity": 1,
                    "unit_price": "25.00",
                },
            ],
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert invoice_response.status_code == 201, (
        invoice_response.get_json()
    )

    invoice_body = invoice_response.get_json()

    assert invoice_body["success"] is True
    assert invoice_body["data"]["clinic_id"] == clinic.id
    assert invoice_body["data"]["patient_id"] == patient_id
    assert (
        invoice_body["data"]["appointment_id"]
        == appointment_id
    )
    assert invoice_body["data"]["total_amount"] == "125.00"
    assert invoice_body["data"]["amount_paid"] == "0.00"
    assert invoice_body["data"]["status"] == "issued"
    assert len(invoice_body["data"]["items"]) == 2

    invoice_id = invoice_body["data"]["id"]

    persisted_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    assert persisted_invoice is not None
    assert persisted_invoice.clinic_id == clinic.id
    assert persisted_invoice.patient_id == patient_id
    assert (
        persisted_invoice.appointment_id
        == appointment_id
    )
    assert persisted_invoice.total_amount == Decimal("125.00")
    assert persisted_invoice.amount_paid == Decimal("0.00")
    assert persisted_invoice.status is InvoiceStatus.ISSUED

    invoice_items = list(
        db.session.execute(
            db.select(InvoiceItem)
            .where(
                InvoiceItem.invoice_id == invoice_id
            )
            .order_by(InvoiceItem.id.asc())
        ).scalars()
    )

    assert len(invoice_items) == 2
    assert invoice_items[0].subtotal == Decimal("100.00")
    assert invoice_items[1].subtotal == Decimal("25.00")

    outstanding_response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert outstanding_response.status_code == 200

    outstanding_body = outstanding_response.get_json()

    assert outstanding_body["success"] is True
    assert outstanding_body["data"]["total"] == 1
    assert (
        outstanding_body["data"]["items"][0]["id"]
        == invoice_id
    )
    assert (
        outstanding_body["data"]["items"][0]["clinic_id"]
        == clinic.id
    )

    partial_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "50.00",
            "method": "cash",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert partial_payment_response.status_code == 201

    partial_payment_body = (
        partial_payment_response.get_json()
    )

    assert partial_payment_body["success"] is True
    assert partial_payment_body["data"]["invoice_id"] == invoice_id
    assert partial_payment_body["data"]["amount"] == "50.00"
    assert partial_payment_body["data"]["method"] == "cash"
    assert partial_payment_body["data"]["status"] == "successful"

    partial_payment_id = (
        partial_payment_body["data"]["id"]
    )

    persisted_payment = db.session.get(
        Payment,
        partial_payment_id,
    )

    assert persisted_payment is not None
    assert persisted_payment.invoice_id == invoice_id
    assert persisted_payment.amount == Decimal("50.00")
    assert persisted_payment.status.value == "successful"

    db.session.refresh(persisted_invoice)

    assert persisted_invoice.amount_paid == Decimal("50.00")
    assert (
        persisted_invoice.status
        is InvoiceStatus.PARTIALLY_PAID
    )

    gateway_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "75.00",
            "method": "card",
            "gateway": "paystack",
            "reference": "PHASE8-BILLING-001",
            "gateway_transaction_id": "TXN-PHASE8-BILLING-001",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert gateway_payment_response.status_code == 201

    gateway_payment_body = (
        gateway_payment_response.get_json()
    )

    assert gateway_payment_body["success"] is True
    assert (
        gateway_payment_body["data"]["invoice_id"]
        == invoice_id
    )
    assert gateway_payment_body["data"]["gateway"] == "paystack"
    assert (
        gateway_payment_body["data"]["gateway_transaction_id"]
        == "TXN-PHASE8-BILLING-001"
    )
    assert gateway_payment_body["data"]["status"] == "successful"

    second_payment_id = (
        gateway_payment_body["data"]["id"]
    )

    persisted_second_payment = db.session.get(
        Payment,
        second_payment_id,
    )

    assert persisted_second_payment is not None
    assert (
        persisted_second_payment.gateway
        is PaymentGateway.PAYSTACK
    )
    assert (
        persisted_second_payment.gateway_transaction_id
        == "TXN-PHASE8-BILLING-001"
    )

    db.session.refresh(persisted_invoice)

    assert persisted_invoice.amount_paid == Decimal("125.00")
    assert persisted_invoice.status is InvoiceStatus.PAID

    duplicate_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "1.00",
            "method": "card",
            "gateway": "paystack",
            "reference": "PHASE8-BILLING-DUP",
            "gateway_transaction_id": "TXN-PHASE8-BILLING-001",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert duplicate_payment_response.status_code == 409

    overdue_invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient_id,
            "currency": "NGN",
            "due_date": (
                date.today()
                - timedelta(days=1)
            ).isoformat(),
            "items": [
                {
                    "description": "Overdue consultation",
                    "quantity": 1,
                    "unit_price": "40.00",
                }
            ],
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert overdue_invoice_response.status_code == 201

    overdue_invoice_id = (
        overdue_invoice_response.get_json()["data"]["id"]
    )

    overdue_response = client.post(
        "/api/v1/billing/invoices/mark-overdue",
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert overdue_response.status_code == 200

    overdue_body = overdue_response.get_json()

    assert overdue_body["success"] is True
    assert overdue_body["data"]["updated_count"] == 1

    persisted_overdue_invoice = db.session.get(
        Invoice,
        overdue_invoice_id,
    )

    assert persisted_overdue_invoice is not None
    assert (
        persisted_overdue_invoice.status
        is InvoiceStatus.OVERDUE
    )

    audit_rows = list(
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_type == "Invoice",
                AuditLog.entity_id.in_(
                    [
                        invoice_id,
                        overdue_invoice_id,
                    ]
                ),
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
        row.entity_id == invoice_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_id == invoice_id
        and row.action is AuditAction.PAYMENT
        for row in audit_rows
    )

    assert any(
        row.entity_id == overdue_invoice_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_id == overdue_invoice_id
        and row.action is AuditAction.STATUS_CHANGE
        for row in audit_rows
    )

    second_clinic = clinic.__class__(
        name="E2E Billing Clinic 2",
        ai_credits=5,
        status=clinic.status,
    )

    db.session.add(second_clinic)
    db.session.flush()

    second_admin = make_user(
        clinic=second_clinic,
        role=Role.ADMIN,
        email="e2e-billing-admin-clinic-2@test.com",
    )

    second_admin_login = e2e_login(
        "e2e-billing-admin-clinic-2@test.com",
    )

    assert (
        second_admin_login["role"]
        == Role.ADMIN.value
    )

    foreign_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "1.00",
            "method": "cash",
        },
        headers={
            "Authorization": (
                f"Bearer {second_admin_login['access_token']}"
            ),
        },
    )

    assert foreign_payment_response.status_code == 404

    second_outstanding_response = client.get(
        "/api/v1/billing/invoices/outstanding",
        headers={
            "Authorization": (
                f"Bearer {second_admin_login['access_token']}"
            ),
        },
    )

    assert second_outstanding_response.status_code == 200

    second_outstanding_body = (
        second_outstanding_response.get_json()
    )

    assert second_outstanding_body["success"] is True
    assert second_outstanding_body["data"]["total"] == 0

    assert (
        db.session.execute(
            db.select(Payment)
            .where(
                Payment.invoice_id == invoice_id
            )
        ).scalars().all()
    )

    print("PHASE8_E2E_BILLING=PASS")
