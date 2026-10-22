from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentStatus,
)
from app.core.enums.role_enums import Role
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)
from app.modules.billing.routes import billing_route
from app.modules.billing.services import (
    payment_orchestration_service,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayUnknownOutcomeError,
)


class Gate12Slice3ChaosGateway:
    def __init__(self):
        self.initialize_calls = []
        self.verify_calls = []
        self.provider_processed = False

    def initialize_payment(self, **kwargs):
        self.initialize_calls.append(kwargs)
        self.provider_processed = True

        raise GatewayUnknownOutcomeError(
            "Gate 12 Slice 3 provider accepted payment "
            "but response timed out"
        )

    def verify_payment(self, **kwargs):
        self.verify_calls.append(kwargs)

        if not self.provider_processed:
            raise GatewayUnknownOutcomeError(
                "Provider has no known processed payment"
            )

        return {
            "provider": "paystack",
            "reference": kwargs["reference"],
            "transaction_id": "TX-G12-S3-001",
            "status": "successful",
            "paid": True,
            "amount": 100,
            "currency": "NGN",
        }


def test_gate12_slice3_payment_timeout_unknown_outcome_reconciles_one_settlement(
    client,
    db,
    clinic,
    make_user,
    make_patient,
    auth_headers_for,
    monkeypatch,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email=(
            f"gate12-s3-admin-{uuid4().hex}"
            "@example.com"
        ),
    )

    admin_headers = auth_headers_for(
        admin,
        role=Role.ADMIN,
    )

    patient = make_patient(
        clinic,
        first_name="Gate12",
        last_name="PaymentChaos",
        email=(
            f"gate12-s3-patient-{uuid4().hex}"
            "@example.com"
        ),
    )

    invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient.id,
            "currency": "NGN",
            "items": [
                {
                    "description": (
                        "Gate 12 Slice 3 hospital payment"
                    ),
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
        headers=admin_headers,
    )

    assert invoice_response.status_code == 201, (
        invoice_response.get_json()
    )

    invoice_id = invoice_response.get_json()["data"]["id"]

    operation_key = (
        f"gate12-s3-payment-{uuid4().hex}"
    )

    payment_payload = {
        "invoice_id": invoice_id,
        "amount": "100.00",
        "method": "card",
        "gateway": "paystack",
        "currency": "NGN",
        "reference": (
            f"G12-S3-{uuid4().hex[:16].upper()}"
        ),
    }

    headers = {
        **admin_headers,
        "Idempotency-Key": operation_key,
    }

    gateway = Gate12Slice3ChaosGateway()

    monkeypatch.setattr(
        payment_orchestration_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    original_jsonify = billing_route.jsonify
    response_loss = {
        "injected": False,
    }

    def lose_payment_response(
        *args,
        **kwargs,
    ):
        if not response_loss["injected"]:
            response_loss["injected"] = True
            raise ConnectionError(
                "Gate 12 Slice 3 simulated "
                "client response loss"
            )

        return original_jsonify(
            *args,
            **kwargs,
        )

    monkeypatch.setattr(
        billing_route,
        "jsonify",
        lose_payment_response,
    )

    first_response = client.post(
        "/api/v1/billing/payments/initialize",
        json=payment_payload,
        headers=headers,
    )

    assert response_loss["injected"] is True
    assert first_response.status_code == 500
    assert first_response.get_json() == {
        "success": False,
        "error": "Internal server error",
    }

    assert len(gateway.initialize_calls) == 1
    assert gateway.initialize_calls[0]["reference"] == (
        payment_payload["reference"]
    )
    assert gateway.initialize_calls[0]["idempotency_key"] == (
        operation_key
    )
    assert gateway.verify_calls == []

    monkeypatch.setattr(
        billing_route,
        "jsonify",
        original_jsonify,
    )

    db.session.expire_all()

    pending_payments = list(
        db.session.scalars(
            db.select(Payment).where(
                Payment.id.in_(
                    db.select(
                        IdempotencyRecord.entity_id
                    ).where(
                        IdempotencyRecord.clinic_id
                        == clinic.id,
                        IdempotencyRecord.user_id
                        == admin.id,
                        IdempotencyRecord.operation
                        == "billing.payment.initialize",
                        IdempotencyRecord.idempotency_key
                        == operation_key,
                    )
                )
            )
        )
    )

    assert len(pending_payments) == 1

    pending_payment = pending_payments[0]

    assert pending_payment.status is PaymentStatus.PENDING
    assert pending_payment.amount == Decimal("100.00")
    assert pending_payment.reference == (
        payment_payload["reference"]
    )
    assert pending_payment.gateway_transaction_id is None

    invoice_after_unknown = db.session.get(
        Invoice,
        invoice_id,
    )

    assert invoice_after_unknown is not None
    assert invoice_after_unknown.amount_paid == Decimal(
        "0.00"
    )
    assert invoice_after_unknown.status is InvoiceStatus.ISSUED

    payment_create_audits = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "Payment",
                AuditLog.entity_id == pending_payment.id,
                AuditLog.action == AuditAction.CREATE,
            )
        )
    )

    assert len(payment_create_audits) == 1

    idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id == admin.id,
                IdempotencyRecord.operation
                == "billing.payment.initialize",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    assert len(idempotency_records) == 1
    assert idempotency_records[0].entity_type == "Payment"
    assert idempotency_records[0].entity_id == (
        pending_payment.id
    )

    retry_response = client.post(
        "/api/v1/billing/payments/initialize",
        json=payment_payload,
        headers=headers,
    )

    assert retry_response.status_code == 201, (
        retry_response.get_json()
    )

    retry_body = retry_response.get_json()

    assert retry_body["success"] is True
    assert retry_body["data"]["outcome"] == "successful"
    assert retry_body["data"]["payment"]["id"] == (
        pending_payment.id
    )
    assert retry_body["data"]["payment"]["status"] == (
        "successful"
    )

    assert len(gateway.initialize_calls) == 1
    assert len(gateway.verify_calls) == 1
    assert gateway.verify_calls[0]["reference"] == (
        pending_payment.reference
    )

    db.session.expire_all()

    final_payments = list(
        db.session.scalars(
            db.select(Payment).where(
                Payment.invoice_id == invoice_id,
            )
        )
    )

    assert len(final_payments) == 1

    final_payment = final_payments[0]

    assert final_payment.id == pending_payment.id
    assert final_payment.status is PaymentStatus.SUCCESSFUL
    assert final_payment.gateway_transaction_id == (
        "TX-G12-S3-001"
    )
    assert final_payment.amount == Decimal("100.00")

    final_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    assert final_invoice is not None
    assert final_invoice.amount_paid == Decimal(
        "100.00"
    )
    assert final_invoice.status is InvoiceStatus.PAID

    payment_audits = list(
        db.session.scalars(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "Invoice",
                AuditLog.entity_id == invoice_id,
                AuditLog.action == AuditAction.PAYMENT,
            )
        )
    )

    assert len(payment_audits) == 1

    final_idempotency_records = list(
        db.session.scalars(
            db.select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id == admin.id,
                IdempotencyRecord.operation
                == "billing.payment.initialize",
                IdempotencyRecord.idempotency_key
                == operation_key,
            )
        )
    )

    assert len(final_idempotency_records) == 1
    assert final_idempotency_records[0].entity_type == (
        "Payment"
    )
    assert final_idempotency_records[0].entity_id == (
        final_payment.id
    )

    all_invoice_payments = list(
        db.session.scalars(
            db.select(Payment).where(
                Payment.invoice_id == invoice_id,
            )
        )
    )

    assert len(all_invoice_payments) == 1
    assert sum(
        (
            Decimal(payment.amount)
            for payment in all_invoice_payments
            if payment.status is PaymentStatus.SUCCESSFUL
        ),
        Decimal("0.00"),
    ) == Decimal("100.00")