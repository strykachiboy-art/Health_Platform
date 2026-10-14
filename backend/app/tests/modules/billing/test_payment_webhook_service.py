from decimal import Decimal

import pytest

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
    PaymentWebhookEvent,
)
from app.modules.billing.services import (
    payment_webhook_service as webhook_service,
)


class FakeWebhookGateway:
    def __init__(
        self,
        *,
        result=None,
        error=None,
    ):
        self.result = result or {
            "provider": "paystack",
            "event_id": "evt-1",
            "event_type": "charge.success",
            "transaction_id": "TX-1",
            "reference": "WEBHOOK-1",
            "amount": 100,
            "currency": "NGN",
            "status": "successful",
        }
        self.error = error
        self.calls = []

    def handle_webhook(
        self,
        *,
        payload,
        signature=None,
        headers=None,
    ):
        self.calls.append(
            {
                "payload": payload,
                "signature": signature,
                "headers": headers,
            }
        )

        if self.error is not None:
            raise self.error

        return self.result


def _make_invoice(
    db,
    clinic,
    patient,
    *,
    total=Decimal("100.00"),
    paid=Decimal("0.00"),
):
    invoice = Invoice(
        clinic_id=clinic.id,
        patient_id=patient.id,
        invoice_number=f"WEBHOOK-{clinic.id}-1",
        total_amount=total,
        amount_paid=paid,
        status=(
            InvoiceStatus.PARTIALLY_PAID
            if paid > 0
            else InvoiceStatus.ISSUED
        ),
    )

    db.session.add(invoice)
    db.session.flush()

    return invoice


def _make_pending_payment(
    db,
    invoice,
    *,
    reference="WEBHOOK-1",
    amount=Decimal("100.00"),
    gateway=PaymentGateway.PAYSTACK,
    currency="NGN",
):
    payment = Payment(
        invoice_id=invoice.id,
        amount=amount,
        currency=currency,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PENDING,
        gateway=gateway,
        reference=reference,
    )

    db.session.add(payment)
    db.session.flush()

    return payment


def test_success_webhook_reconciles_payment_and_invoice(
    app,
    db,
    clinic,
    patient,
    monkeypatch,
):
    invoice = _make_invoice(
        db,
        clinic,
        patient,
    )
    payment = _make_pending_payment(
        db,
        invoice,
    )

    gateway = FakeWebhookGateway()

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    result = webhook_service.process_payment_webhook(
        clinic_id=clinic.id,
        gateway="paystack",
        payload=b'{"event":"charge.success"}',
        signature="valid-signature",
    )

    assert result["status"] == "processed_successful"
    assert result["payment_id"] == payment.id

    db.session.refresh(payment)
    db.session.refresh(invoice)

    assert payment.status is PaymentStatus.SUCCESSFUL
    assert (
        payment.gateway_transaction_id
        == "TX-1"
    )
    assert payment.failure_reason is None
    assert payment.paid_at is not None

    assert invoice.amount_paid == Decimal("100.00")
    assert invoice.status is InvoiceStatus.PAID

    events = (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.clinic_id == clinic.id,
            PaymentWebhookEvent.gateway == "paystack",
            PaymentWebhookEvent.event_id == "evt-1",
        )
        .all()
    )

    assert len(events) == 1
    assert events[0].payment_id == payment.id
    assert events[0].transaction_id == "TX-1"


def test_duplicate_webhook_is_idempotent(
    app,
    db,
    clinic,
    patient,
    monkeypatch,
):
    invoice = _make_invoice(
        db,
        clinic,
        patient,
    )
    payment = _make_pending_payment(
        db,
        invoice,
    )

    gateway = FakeWebhookGateway()

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    first = webhook_service.process_payment_webhook(
        clinic_id=clinic.id,
        gateway="paystack",
        payload=b'{"event":"charge.success"}',
        signature="valid-signature",
    )

    db.session.expire_all()

    second = webhook_service.process_payment_webhook(
        clinic_id=clinic.id,
        gateway="paystack",
        payload=b'{"event":"charge.success"}',
        signature="valid-signature",
    )

    assert first["status"] == "processed_successful"
    assert second["status"] == "duplicate"
    assert second["event_id"] == "evt-1"

    refreshed_invoice = db.session.get(
        Invoice,
        invoice.id,
    )
    refreshed_payment = db.session.get(
        Payment,
        payment.id,
    )

    assert (
        refreshed_invoice.amount_paid
        == Decimal("100.00")
    )
    assert (
        refreshed_invoice.status
        is InvoiceStatus.PAID
    )
    assert (
        refreshed_payment.status
        is PaymentStatus.SUCCESSFUL
    )

    event_count = (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.clinic_id == clinic.id,
            PaymentWebhookEvent.gateway == "paystack",
            PaymentWebhookEvent.event_id == "evt-1",
        )
        .count()
    )

    assert event_count == 1
    assert len(gateway.calls) == 2


def test_failed_webhook_marks_pending_payment_failed(
    app,
    db,
    clinic,
    patient,
    monkeypatch,
):
    invoice = _make_invoice(
        db,
        clinic,
        patient,
    )
    payment = _make_pending_payment(
        db,
        invoice,
        reference="WEBHOOK-FAIL-1",
    )

    gateway = FakeWebhookGateway(
        result={
            "provider": "paystack",
            "event_id": "evt-fail-1",
            "event_type": "charge.failed",
            "transaction_id": "TX-FAIL-1",
            "reference": "WEBHOOK-FAIL-1",
            "amount": 100,
            "currency": "NGN",
            "status": "failed",
            "failure_reason": "Bank declined",
        }
    )

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    result = webhook_service.process_payment_webhook(
        clinic_id=clinic.id,
        gateway="paystack",
        payload=b'{"event":"charge.failed"}',
        signature="valid-signature",
    )

    assert result["status"] == "processed_failed"

    db.session.refresh(payment)
    db.session.refresh(invoice)

    assert payment.status is PaymentStatus.FAILED
    assert payment.failure_reason == "Bank declined"
    assert payment.paid_at is None

    assert invoice.amount_paid == Decimal("0.00")
    assert invoice.status is InvoiceStatus.ISSUED


def test_invalid_webhook_signature_rolls_back_without_event(
    app,
    db,
    clinic,
    patient,
    monkeypatch,
):
    invoice = _make_invoice(
        db,
        clinic,
        patient,
    )
    payment = _make_pending_payment(
        db,
        invoice,
    )

    gateway = FakeWebhookGateway(
        error=ValueError(
            "Invalid webhook signature"
        )
    )

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    with pytest.raises(
        ValidationError,
        match="Invalid webhook signature",
    ):
        webhook_service.process_payment_webhook(
            clinic_id=clinic.id,
            gateway="paystack",
            payload=b"invalid",
            signature="bad-signature",
        )

    db.session.expire_all()

    assert (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.clinic_id == clinic.id
        )
        .count()
        == 0
    )

    refreshed_payment = db.session.get(
        Payment,
        payment.id,
    )

    assert (
        refreshed_payment.status
        is PaymentStatus.PENDING
    )


def test_webhook_cannot_cross_clinic_boundary(
    app,
    db,
    clinic,
    make_clinic,
    patient,
    monkeypatch,
):
    foreign_clinic = make_clinic(
        name="Webhook Foreign Clinic",
    )

    invoice = _make_invoice(
        db,
        foreign_clinic,
        patient,
    )

    # Re-home the patient to the foreign clinic safely
    # for this isolated tenant-boundary fixture.
    patient.clinic_id = foreign_clinic.id
    db.session.flush()

    payment = _make_pending_payment(
        db,
        invoice,
        reference="FOREIGN-WEBHOOK-1",
    )

    gateway = FakeWebhookGateway(
        result={
            "provider": "paystack",
            "event_id": "evt-foreign-1",
            "event_type": "charge.success",
            "transaction_id": "TX-FOREIGN-1",
            "reference": "FOREIGN-WEBHOOK-1",
            "amount": 100,
            "currency": "NGN",
            "status": "successful",
        }
    )

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    with pytest.raises(
        NotFoundError,
        match="Webhook does not identify a payment",
    ):
        webhook_service.process_payment_webhook(
            clinic_id=clinic.id,
            gateway="paystack",
            payload=b'{"event":"charge.success"}',
            signature="valid-signature",
        )

    db.session.expire_all()

    refreshed_payment = db.session.get(
        Payment,
        payment.id,
    )

    assert (
        refreshed_payment.status
        is PaymentStatus.PENDING
    )

    assert (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.event_id
            == "evt-foreign-1"
        )
        .count()
        == 0
    )


def test_webhook_amount_mismatch_is_rejected(
    app,
    db,
    clinic,
    patient,
    monkeypatch,
):
    invoice = _make_invoice(
        db,
        clinic,
        patient,
    )
    payment = _make_pending_payment(
        db,
        invoice,
        reference="WEBHOOK-AMOUNT-1",
    )

    gateway = FakeWebhookGateway(
        result={
            "provider": "paystack",
            "event_id": "evt-amount-1",
            "event_type": "charge.success",
            "transaction_id": "TX-AMOUNT-1",
            "reference": "WEBHOOK-AMOUNT-1",
            "amount": 90,
            "currency": "NGN",
            "status": "successful",
        }
    )

    monkeypatch.setattr(
        webhook_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()
    with pytest.raises(
        ConflictError,
        match="Webhook amount does not match",
    ):
        webhook_service.process_payment_webhook(
            clinic_id=clinic.id,
            gateway="paystack",
            payload=b'{"event":"charge.success"}',
            signature="valid-signature",
        )

    db.session.expire_all()

    assert (
        db.session.get(
            Payment,
            payment.id,
        ).status
        is PaymentStatus.PENDING
    )

    assert (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.event_id
            == "evt-amount-1"
        )
        .count()
        == 0
    )
