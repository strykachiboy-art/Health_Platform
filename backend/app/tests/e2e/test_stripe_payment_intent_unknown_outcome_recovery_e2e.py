from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentStatus,
)
from app.core.enums.role_enums import Role
from app.modules.billing.models.billing_model import Invoice, Payment
from app.modules.billing.services import payment_orchestration_service
from app.modules.billing.services.gateways.base_gateway import (
    GatewayUnknownOutcomeError,
)
from app.modules.billing.services.gateways.stripe_gateway import StripeGateway


def test_stripe_payment_intent_unknown_outcome_recovers_one_settlement(
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
        email=f"stripe-unknown-admin-{uuid4().hex}@example.com",
    )
    headers = auth_headers_for(admin, role=Role.ADMIN)

    patient = make_patient(
        clinic,
        first_name="Stripe",
        last_name="UnknownOutcome",
        email=f"stripe-unknown-patient-{uuid4().hex}@example.com",
    )

    invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient.id,
            "currency": "NGN",
            "items": [
                {
                    "description": "Stripe unknown-outcome recovery",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        },
        headers=headers,
    )
    assert invoice_response.status_code == 201, invoice_response.get_json()
    invoice_id = invoice_response.get_json()["data"]["id"]

    operation_key = f"stripe-unknown-{uuid4().hex}"
    payment_reference = f"STRIPE-UNKNOWN-{uuid4().hex[:16].upper()}"
    payload = {
        "invoice_id": invoice_id,
        "amount": "100.00",
        "method": "card",
        "gateway": "stripe",
        "currency": "NGN",
        "reference": payment_reference,
    }
    request_headers = {**headers, "Idempotency-Key": operation_key}

    gateway = StripeGateway(
        credentials={
            "secret_key": "sk_test_unknown_outcome",
            "webhook_secret": "whsec_unknown_outcome",
        }
    )
    provider_state = {
        "intent": None,
        "first_request_params": None,
        "create_calls": [],
        "retrieve_calls": [],
    }

    def create_intent_with_lost_first_response(**params):
        request_params = {
            key: deepcopy(value)
            for key, value in params.items()
            if key != "api_key"
        }
        provider_state["create_calls"].append(request_params)

        if provider_state["intent"] is None:
            provider_state["first_request_params"] = deepcopy(request_params)
            metadata = {
                str(key): str(value)
                for key, value in request_params["metadata"].items()
            }
            provider_state["intent"] = SimpleNamespace(
                id="pi_stripe_unknown_outcome_e2e",
                status="succeeded",
                client_secret="pi_secret_unknown_outcome_e2e",
                amount=request_params["amount"],
                currency=request_params["currency"],
                metadata=metadata,
            )
            # Simulate Stripe having created/processed the intent while the
            # initiating server never receives the HTTP response.
            raise GatewayUnknownOutcomeError(
                "simulated Stripe response loss after PaymentIntent creation"
            )

        if request_params != provider_state["first_request_params"]:
            raise AssertionError(
                "Stripe idempotency replay did not preserve the original request parameters"
            )
        return provider_state["intent"]

    def retrieve_payment_intent(reference, *, api_key):
        provider_state["retrieve_calls"].append(reference)
        intent = provider_state["intent"]
        if intent is None or reference != intent.id:
            raise AssertionError("Stripe retrieval used an unexpected PaymentIntent ID")
        return intent

    monkeypatch.setattr(gateway, "_create_payment_intent", create_intent_with_lost_first_response)
    monkeypatch.setattr(gateway, "_retrieve_payment_intent", retrieve_payment_intent)
    monkeypatch.setattr(
        payment_orchestration_service,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    first_response = client.post(
        "/api/v1/billing/payments/initialize",
        json=payload,
        headers=request_headers,
    )
    assert first_response.status_code == 202, first_response.get_json()
    first_body = first_response.get_json()
    assert first_body["success"] is True
    assert first_body["data"]["outcome"] == "pending"
    assert len(provider_state["create_calls"]) == 1
    assert provider_state["retrieve_calls"] == []

    db.session.expire_all()
    pending_payments = list(
        db.session.scalars(
            db.select(Payment).where(Payment.invoice_id == invoice_id)
        )
    )
    assert len(pending_payments) == 1
    pending_payment = pending_payments[0]
    assert pending_payment.status is PaymentStatus.PENDING
    assert pending_payment.gateway is PaymentGateway.STRIPE
    assert pending_payment.gateway_transaction_id is None

    invoice_after_unknown = db.session.get(Invoice, invoice_id)
    assert invoice_after_unknown.amount_paid == Decimal("0.00")
    assert invoice_after_unknown.status is InvoiceStatus.ISSUED

    retry_response = client.post(
        "/api/v1/billing/payments/initialize",
        json=payload,
        headers=request_headers,
    )
    assert retry_response.status_code == 201, retry_response.get_json()
    retry_body = retry_response.get_json()
    assert retry_body["success"] is True
    assert retry_body["data"]["outcome"] == "successful"
    assert retry_body["data"]["payment"]["id"] == pending_payment.id
    assert retry_body["data"]["payment"]["status"] == "successful"

    assert len(provider_state["create_calls"]) == 2
    assert provider_state["create_calls"][0] == provider_state["create_calls"][1]
    assert provider_state["retrieve_calls"] == ["pi_stripe_unknown_outcome_e2e"]

    db.session.expire_all()
    final_payments = list(
        db.session.scalars(
            db.select(Payment).where(Payment.invoice_id == invoice_id)
        )
    )
    assert len(final_payments) == 1
    final_payment = final_payments[0]
    assert final_payment.id == pending_payment.id
    assert final_payment.status is PaymentStatus.SUCCESSFUL
    assert final_payment.gateway_transaction_id == "pi_stripe_unknown_outcome_e2e"
    assert final_payment.amount == Decimal("100.00")

    final_invoice = db.session.get(Invoice, invoice_id)
    assert final_invoice.amount_paid == Decimal("100.00")
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
