from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.enums.billing_enums import PaymentMethod
from app.core.exceptions import ConflictError
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)
from app.modules.billing.services import billing_service


def _patch_create_dependencies(monkeypatch):
    monkeypatch.setattr(
        billing_service,
        "_lock_clinic",
        lambda clinic_id: type(
            "ClinicStub",
            (),
            {"id": clinic_id},
        )(),
    )

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )


def _patch_payment_dependencies(monkeypatch):
    monkeypatch.setattr(
        billing_service,
        "_lock_clinic",
        lambda clinic_id: type(
            "ClinicStub",
            (),
            {"id": clinic_id},
        )(),
    )

    monkeypatch.setattr(
        billing_service,
        "ensure_clinic_active",
        lambda clinic_id: None,
    )

    monkeypatch.setattr(
        billing_service,
        "create_audit_log",
        lambda **kwargs: None,
    )


def test_create_invoice_idempotency_replays_and_conflicts(
    app,
    db_session,
    make_clinic,
    make_patient,
    make_user,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )
    user = make_user(
        clinic=clinic,
    )

    _patch_create_dependencies(
        monkeypatch
    )

    monkeypatch.setattr(
        billing_service,
        "_generate_invoice_number",
        lambda clinic_id: "INV-IDEMP-001",
    )

    payload = {
        "clinic_id": clinic.id,
        "patient_id": patient.id,
        "currency": "NGN",
        "items": [
            {
                "description": "Consultation",
                "quantity": 1,
                "unit_price": "100.00",
            }
        ],
        "idempotency_key": "billing-invoice-service-001",
        "idempotency_user_id": user.id,
    }

    with app.app_context():
        first = billing_service.create_invoice(
            **payload
        )

        second = billing_service.create_invoice(
            **payload
        )

        assert first.id == second.id

        with pytest.raises(
            ConflictError,
            match=(
                "Idempotency-Key was already used "
                "with a different request payload"
            ),
        ):
            billing_service.create_invoice(
                **{
                    **payload,
                    "items": [
                        {
                            "description": "Consultation",
                            "quantity": 2,
                            "unit_price": "100.00",
                        }
                    ],
                }
            )

        invoices = db_session.execute(
            select(Invoice).where(
                Invoice.clinic_id == clinic.id,
                Invoice.patient_id == patient.id,
            )
        ).scalars().all()

        assert len(invoices) == 1
        assert invoices[0].id == first.id

        records = db_session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation
                == "billing.invoice.create",
                IdempotencyRecord.idempotency_key
                == "billing-invoice-service-001",
            )
        ).scalars().all()

        assert len(records) == 1
        assert records[0].entity_type == "Invoice"
        assert records[0].entity_id == first.id


def test_record_payment_idempotency_replays_and_conflicts(
    app,
    db_session,
    make_clinic,
    make_patient,
    make_user,
    monkeypatch,
):
    clinic = make_clinic()
    patient = make_patient(
        clinic=clinic,
    )
    user = make_user(
        clinic=clinic,
    )

    _patch_create_dependencies(
        monkeypatch
    )

    _patch_payment_dependencies(
        monkeypatch
    )

    monkeypatch.setattr(
        billing_service,
        "_generate_invoice_number",
        lambda clinic_id: "INV-IDEMP-PAY-001",
    )

    with app.app_context():
        invoice = billing_service.create_invoice(
            clinic_id=clinic.id,
            patient_id=patient.id,
            currency="NGN",
            items=[
                {
                    "description": "Consultation",
                    "quantity": 1,
                    "unit_price": "100.00",
                }
            ],
        )

        payload = {
            "clinic_id": clinic.id,
            "invoice_id": invoice.id,
            "amount": "40.00",
            "method": PaymentMethod.CASH,
            "idempotency_key": "billing-payment-service-001",
            "idempotency_user_id": user.id,
        }

        first = billing_service.record_payment(
            **payload
        )

        second = billing_service.record_payment(
            **payload
        )

        assert first.id == second.id
        assert invoice.amount_paid == Decimal("40.00")

        with pytest.raises(
            ConflictError,
            match=(
                "Idempotency-Key was already used "
                "with a different request payload"
            ),
        ):
            billing_service.record_payment(
                **{
                    **payload,
                    "amount": "50.00",
                }
            )

        payments = db_session.execute(
            select(Payment).where(
                Payment.invoice_id == invoice.id,
            )
        ).scalars().all()

        assert len(payments) == 1
        assert payments[0].id == first.id

        records = db_session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.clinic_id == clinic.id,
                IdempotencyRecord.user_id == user.id,
                IdempotencyRecord.operation
                == "billing.payment.record",
                IdempotencyRecord.idempotency_key
                == "billing-payment-service-001",
            )
        ).scalars().all()

        assert len(records) == 1
        assert records[0].entity_type == "Payment"
        assert records[0].entity_id == first.id