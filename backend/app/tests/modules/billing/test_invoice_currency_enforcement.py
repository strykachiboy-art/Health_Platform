from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.core.exceptions import ConflictError, ValidationError
from app.extensions import db
from app.modules.billing.models.billing_model import Invoice, Payment
from app.modules.billing.services import billing_service
from app.modules.billing.services import (
    payment_orchestration_service as orchestration,
)
from app.modules.billing.services import (
    payment_webhook_service as webhooks,
)


def _persist_invoice_and_payment(
    clinic,
    patient,
    *,
    invoice_currency,
    payment_currency="NGN",
):
    token = uuid4().hex.upper()

    invoice = Invoice(
        clinic_id=clinic.id,
        patient_id=patient.id,
        invoice_number=f"F2-CURRENCY-{token}",
        currency=invoice_currency,
        total_amount=Decimal("100.00"),
        amount_paid=Decimal("0.00"),
        status=InvoiceStatus.ISSUED,
    )
    db.session.add(invoice)
    db.session.flush()

    payment = Payment(
        invoice_id=invoice.id,
        amount=Decimal("100.00"),
        currency=payment_currency,
        method=PaymentMethod.CARD,
        status=PaymentStatus.PENDING,
        gateway=PaymentGateway.PAYSTACK,
        reference=f"F2-PAY-{token}",
    )
    db.session.add(payment)
    db.session.flush()

    return invoice, payment


@pytest.mark.parametrize(
    ("invoice_currency", "expected_error"),
    [
        ("USD", ValidationError),
        (None, ConflictError),
    ],
)
def test_gateway_initialization_rejects_mismatch_or_unknown_invoice_currency(
    app,
    monkeypatch,
    invoice_currency,
    expected_error,
):
    invoice = SimpleNamespace(
        currency=invoice_currency,
        status=InvoiceStatus.ISSUED,
    )

    monkeypatch.setattr(
        orchestration,
        "reserve_idempotency_operation",
        lambda **kwargs: (SimpleNamespace(), True),
    )
    monkeypatch.setattr(
        orchestration,
        "_lock_clinic",
        lambda clinic_id: None,
    )
    monkeypatch.setattr(
        orchestration,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )
    monkeypatch.setattr(
        orchestration,
        "_get_invoice",
        lambda *args, **kwargs: invoice,
    )

    with app.app_context():
        with pytest.raises(expected_error, match="currency"):
            orchestration._get_or_create_pending_payment(
                clinic_id=1,
                invoice_id=10,
                amount=Decimal("25.00"),
                method=PaymentMethod.CARD,
                gateway=PaymentGateway.PAYSTACK,
                currency="NGN",
                reference="F2-INIT-001",
                idempotency_key="f2-init-currency-001",
                idempotency_user_id=1,
                actor_user_id=1,
            )


@pytest.mark.parametrize("invoice_currency", ["USD", None])
def test_orchestration_finalization_rejects_mismatch_or_unknown_invoice_currency(
    app,
    db,
    clinic,
    patient,
    invoice_currency,
):
    invoice, payment = _persist_invoice_and_payment(
        clinic,
        patient,
        invoice_currency=invoice_currency,
    )
    db.session.commit()

    try:
        with pytest.raises(ConflictError, match="currency"):
            orchestration._finalize_payment_success(
                clinic_id=clinic.id,
                payment_id=payment.id,
                actor_user_id=None,
            )
    finally:
        db.session.rollback()


@pytest.mark.parametrize("invoice_currency", ["USD", None])
def test_webhook_finalization_rejects_mismatch_or_unknown_invoice_currency(
    app,
    db,
    clinic,
    patient,
    invoice_currency,
):
    invoice, payment = _persist_invoice_and_payment(
        clinic,
        patient,
        invoice_currency=invoice_currency,
    )
    db.session.commit()

    try:
        with pytest.raises(ConflictError, match="currency"):
            webhooks._mark_payment_successful(
                clinic_id=clinic.id,
                payment=payment,
                transaction_id="F2-TX-001",
            )
    finally:
        db.session.rollback()


def test_manual_payment_rejects_unknown_legacy_invoice_currency(
    app,
    db,
    clinic,
    patient,
):
    invoice, _ = _persist_invoice_and_payment(
        clinic,
        patient,
        invoice_currency=None,
    )
    db.session.commit()

    try:
        with pytest.raises(
            ConflictError,
            match="Invoice currency is unknown",
        ):
            billing_service.record_payment(
                clinic_id=clinic.id,
                invoice_id=invoice.id,
                amount=Decimal("25.00"),
                method=PaymentMethod.CASH,
            )
    finally:
        db.session.rollback()
