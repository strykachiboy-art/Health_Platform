from __future__ import annotations

import os
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import select

from app import create_app, extensions
from app.config import config_by_name
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentMethod,
    PaymentStatus,
)
from app.core.enums.clinic_enums import ClinicStatus
from app.extensions import db
from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)
from app.modules.billing.services import (
    payment_orchestration_service,
)
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.patient.models.patient_model import Patient


def test_payment_success_transition_is_single_economic_effect_under_concurrency(
    monkeypatch,
):
    database_url = os.getenv(
        "CONCURRENCY_TEST_DATABASE_URL"
    )

    if not database_url:
        pytest.fail(
            "CONCURRENCY_TEST_DATABASE_URL is required"
        )

    original_database_url = (
        config_by_name["testing"].SQLALCHEMY_DATABASE_URI
    )

    original_redis_client = (
        extensions.redis_client
    )

    config_by_name["testing"].SQLALCHEMY_DATABASE_URI = (
        database_url
    )

    app = create_app("testing")

    extensions.redis_client = (
        original_redis_client
    )

    monkeypatch.setattr(
        payment_orchestration_service,
        "create_audit_log",
        lambda *args, **kwargs: None,
    )

    gate = Barrier(2)

    harness = None

    try:
        with app.app_context():
            from app import models_registry  # noqa: F401

            db.drop_all()
            db.create_all()

            clinic = Clinic(
                name="Gate 9 Billing Clinic",
                status=ClinicStatus.ACTIVE,
                ai_credits=5,
            )

            db.session.add(
                clinic
            )
            db.session.flush()

            patient = Patient(
                clinic_id=clinic.id,
                first_name="Gate",
                last_name="Billing",
                patient_number="G9-BILL-MRN-001",
            )

            db.session.add(
                patient
            )
            db.session.flush()

            invoice = Invoice(
                clinic_id=clinic.id,
                patient_id=patient.id,
                currency="NGN",
                invoice_number="G9-BILL-INV-001",
                total_amount=Decimal("100.00"),
                amount_paid=Decimal("0.00"),
                status=InvoiceStatus.ISSUED,
            )

            db.session.add(
                invoice
            )
            db.session.flush()

            payment = Payment(
                invoice_id=invoice.id,
                amount=Decimal("100.00"),
                currency="NGN",
                method=PaymentMethod.CARD,
                status=PaymentStatus.PENDING,
                gateway=PaymentGateway.PAYSTACK,
                reference="G9-BILL-PAY-001",
                gateway_transaction_id="G9-TX-001",
            )

            db.session.add(
                payment
            )
            db.session.commit()

            clinic_id = clinic.id
            payment_id = payment.id
            invoice_id = invoice.id

        from app.tests.core.concurrency.concurrency_harness import (
            PostgresConcurrencyHarness,
        )

        harness = PostgresConcurrencyHarness(
            database_url
        )

        def finalize(worker_index):
            try:
                gate.wait(
                    timeout=15
                )

                result = (
                    payment_orchestration_service
                    ._finalize_payment_success(
                        clinic_id=clinic_id,
                        payment_id=payment_id,
                        actor_user_id=None,
                        gateway_transaction_id="G9-TX-001",
                    )
                )

                return {
                    "worker_index": worker_index,
                    "outcome": "returned",
                    "payment_id": result.id,
                    "status": result.status.value,
                }

            except Exception as exc:
                return {
                    "worker_index": worker_index,
                    "outcome": "rejected",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }

        results = harness.run_flask(
            app,
            finalize,
        )

        assert len(results) == 2

        assert all(
            result.error is None
            for result in results
        )

        backend_pids = {
            result.backend_pid
            for result in results
        }

        assert len(backend_pids) == 2

        values = [
            result.value
            for result in results
        ]

        assert all(
            value["outcome"] == "returned"
            for value in values
        )

        assert all(
            value["payment_id"] == payment_id
            for value in values
        )

        assert all(
            value["status"]
            == PaymentStatus.SUCCESSFUL.value
            for value in values
        )

        with app.app_context():
            final_payment = db.session.execute(
                select(Payment).where(
                    Payment.id == payment_id
                )
            ).scalar_one()

            final_invoice = db.session.execute(
                select(Invoice).where(
                    Invoice.id == invoice_id
                )
            ).scalar_one()

            assert final_payment.status == (
                PaymentStatus.SUCCESSFUL
            )

            assert final_invoice.amount_paid == (
                Decimal("100.00")
            )

            assert final_invoice.status == (
                InvoiceStatus.PAID
            )

            successful_payment_count = db.session.execute(
                select(
                    Payment
                ).where(
                    Payment.invoice_id == invoice_id,
                    Payment.status == PaymentStatus.SUCCESSFUL,
                )
            ).scalars().all()

            assert len(
                successful_payment_count
            ) == 1

    finally:
        if harness is not None:
            harness.close()

        with app.app_context():
            db.session.rollback()
            db.drop_all()
            db.session.remove()

            try:
                db.engine.dispose()
            except Exception:
                pass

        config_by_name["testing"].SQLALCHEMY_DATABASE_URI = (
            original_database_url
        )

        extensions.redis_client = (
            original_redis_client
        )
