from decimal import Decimal

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
    PaymentWebhookEvent,
)


class FakeWebhookGateway:
    def __init__(self, result):
        self.result = result
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

        return self.result


def _make_invoice(
    db,
    clinic,
    patient,
):
    invoice = Invoice(
        clinic_id=clinic.id,
        patient_id=patient.id,
        currency="NGN",
        invoice_number=(
            f"E2E-WEBHOOK-{clinic.id}-001"
        ),
        total_amount=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        status=InvoiceStatus.ISSUED,
    )

    db.session.add(invoice)
    db.session.flush()

    return invoice


def _make_pending_payment(
    db,
    invoice,
    reference,
    currency="NGN",
):
    payment = Payment(
        invoice_id=invoice.id,
        amount=Decimal("100.00"),
        currency=currency,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PENDING,
        gateway=PaymentGateway.PAYSTACK,
        reference=reference,
    )

    db.session.add(payment)
    db.session.flush()

    return payment


def test_phase9_gate4_billing_webhook_success_and_duplicate_delivery(
    client,
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
        reference="E2E-WEBHOOK-001",
    )

    gateway = FakeWebhookGateway(
        {
            "provider": "paystack",
            "event_id": "evt-e2e-success-1",
            "event_type": "charge.success",
            "transaction_id": "TX-E2E-001",
            "reference": "E2E-WEBHOOK-001",
            "amount": 100,
            "currency": "NGN",
            "status": "successful",
        }
    )

    monkeypatch.setattr(
        "app.modules.billing.services.payment_webhook_service.get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    payload = (
        b'{"event":"charge.success","reference":"E2E-WEBHOOK-001"}'
    )

    db.session.commit()

    first_response = client.post(
        f"/api/v1/billing/webhooks/{clinic.id}/paystack",
        data=payload,
        headers={
            "X-Paystack-Signature": "e2e-signature",
        },
    )

    assert first_response.status_code == 200

    first_body = first_response.get_json()

    assert first_body["success"] is True
    assert (
        first_body["data"]["status"]
        == "processed_successful"
    )
    assert (
        first_body["data"]["event_id"]
        == "evt-e2e-success-1"
    )
    assert (
        first_body["data"]["payment_id"]
        == payment.id
    )

    persisted_payment = db.session.get(
        Payment,
        payment.id,
    )
    persisted_invoice = db.session.get(
        Invoice,
        invoice.id,
    )

    assert persisted_payment is not None
    assert persisted_invoice is not None

    assert (
        persisted_payment.status
        is PaymentStatus.SUCCESSFUL
    )
    assert (
        persisted_payment.gateway_transaction_id
        == "TX-E2E-001"
    )
    assert persisted_invoice.amount_paid == Decimal(
        "100.00"
    )
    assert persisted_invoice.status is InvoiceStatus.PAID

    event_rows = (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.clinic_id == clinic.id,
            PaymentWebhookEvent.gateway == "paystack",
            PaymentWebhookEvent.event_id
            == "evt-e2e-success-1",
        )
        .all()
    )

    assert len(event_rows) == 1
    assert event_rows[0].payment_id == payment.id

    second_response = client.post(
        f"/api/v1/billing/webhooks/{clinic.id}/paystack",
        data=payload,
        headers={
            "X-Paystack-Signature": "e2e-signature",
        },
    )

    assert second_response.status_code == 200

    second_body = second_response.get_json()

    assert second_body["success"] is True
    assert second_body["data"]["status"] == "duplicate"
    assert (
        second_body["data"]["event_id"]
        == "evt-e2e-success-1"
    )
    assert (
        second_body["data"]["payment_id"]
        == payment.id
    )

    persisted_payment = db.session.get(
        Payment,
        payment.id,
    )
    persisted_invoice = db.session.get(
        Invoice,
        invoice.id,
    )

    assert (
        persisted_payment.status
        is PaymentStatus.SUCCESSFUL
    )
    assert persisted_invoice.amount_paid == Decimal(
        "100.00"
    )
    assert persisted_invoice.status is InvoiceStatus.PAID

    assert (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.clinic_id == clinic.id,
            PaymentWebhookEvent.gateway == "paystack",
            PaymentWebhookEvent.event_id
            == "evt-e2e-success-1",
        )
        .count()
        == 1
    )

    assert len(gateway.calls) == 2


def test_phase9_gate4_billing_webhook_is_tenant_scoped(
    client,
    db,
    clinic,
    make_clinic,
    patient,
    monkeypatch,
):
    foreign_clinic = make_clinic(
        name="E2E Webhook Foreign Clinic",
    )

    foreign_patient = patient.__class__(
        clinic_id=foreign_clinic.id,
        first_name="Foreign",
        last_name="Patient",
        patient_number="E2E-WEBHOOK-FOREIGN-MRN",
    )

    db.session.add(foreign_patient)
    db.session.flush()

    invoice = _make_invoice(
        db,
        foreign_clinic,
        foreign_patient,
    )

    payment = _make_pending_payment(
        db,
        invoice,
        reference="E2E-WEBHOOK-FOREIGN",
    )

    gateway = FakeWebhookGateway(
        {
            "provider": "paystack",
            "event_id": "evt-e2e-foreign-1",
            "event_type": "charge.success",
            "transaction_id": "TX-E2E-FOREIGN-1",
            "reference": "E2E-WEBHOOK-FOREIGN",
            "amount": 100,
            "currency": "NGN",
            "status": "successful",
        }
    )

    monkeypatch.setattr(
        "app.modules.billing.services.payment_webhook_service.get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    db.session.commit()

    response = client.post(
        f"/api/v1/billing/webhooks/{clinic.id}/paystack",
        data=b'{"event":"charge.success"}',
        headers={
            "X-Paystack-Signature": "e2e-signature",
        },
    )

    assert response.status_code == 404

    body = response.get_json()

    assert body["success"] is False
    assert body["error"] == (
        "Webhook does not identify a payment"
    )

    persisted_payment = db.session.get(
        Payment,
        payment.id,
    )
    persisted_invoice = db.session.get(
        Invoice,
        invoice.id,
    )

    assert persisted_payment is not None
    assert persisted_invoice is not None

    assert (
        persisted_payment.status
        is PaymentStatus.PENDING
    )
    assert persisted_invoice.amount_paid == Decimal(
        "0.00"
    )
    assert persisted_invoice.status is InvoiceStatus.ISSUED

    assert (
        db.session.query(
            PaymentWebhookEvent
        )
        .filter(
            PaymentWebhookEvent.event_id
            == "evt-e2e-foreign-1"
        )
        .count()
        == 0
    )

    assert len(gateway.calls) == 1
