from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.core.enums.billing_enums import (
    PaymentGateway,
    PaymentStatus,
)
from app.core.exceptions import ConflictError
from app.modules.billing.services import (
    payment_orchestration_service as orchestration,
)


class FakeGateway:
    def __init__(
        self,
        initialize_result=None,
        initialize_error=None,
        verify_result=None,
        verify_error=None,
    ):
        self.initialize_result = (
            initialize_result
            or {
                "provider": "paystack",
                "reference": "PAY-1",
                "transaction_id": "123",
                "status": "initialized",
            }
        )
        self.initialize_error = initialize_error
        self.verify_result = (
            verify_result
            or {
                "provider": "paystack",
                "reference": "PAY-1",
                "transaction_id": "123",
                "status": "pending",
                "paid": False,
                "amount": 100,
                "currency": "NGN",
            }
        )
        self.verify_error = verify_error
        self.initialize_calls = []
        self.verify_calls = []
        self.recovery_calls = []
        self.recovery_result = None
        self.recovery_error = None

    def initialize_payment(self, **kwargs):
        self.initialize_calls.append(kwargs)

        if self.initialize_error is not None:
            raise self.initialize_error

        return self.initialize_result

    def verify_payment(self, **kwargs):
        self.verify_calls.append(kwargs)

        if self.verify_error is not None:
            raise self.verify_error

        return self.verify_result

    def recover_payment_intent_id(self, **kwargs):
        self.recovery_calls.append(kwargs)

        if self.recovery_error is not None:
            raise self.recovery_error

        return self.recovery_result


def make_payment(
    *,
    status=PaymentStatus.PENDING,
    gateway=PaymentGateway.PAYSTACK,
    reference="PAY-1",
    transaction_id="123",
    currency="NGN",
):
    return SimpleNamespace(
        id=1,
        invoice_id=10,
        amount=100,
        currency=currency,
        method="card",
        gateway=gateway,
        reference=reference,
        gateway_transaction_id=transaction_id,
        status=status,
        failure_reason=None,
        invoice=SimpleNamespace(
            patient_id=50,
            patient=SimpleNamespace(
                email="patient@example.com",
            ),
        ),
    )


def test_initial_unknown_outcome_keeps_payment_pending(
    monkeypatch,
):
    from app.modules.billing.services.gateways.base_gateway import (
        GatewayUnknownOutcomeError,
    )

    payment = make_payment(
        transaction_id=None,
    )

    gateway = FakeGateway(
        initialize_error=GatewayUnknownOutcomeError(
            "provider timeout"
        )
    )

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            True,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="paystack",
        currency="NGN",
        idempotency_key="unknown-1",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert result["outcome"] == "pending"
    assert result["payment"] is payment
    assert len(gateway.initialize_calls) == 1
    assert gateway.verify_calls == []


def test_known_provider_rejection_marks_payment_failed(
    monkeypatch,
):
    from app.modules.billing.services.gateways.base_gateway import (
        GatewayRejectedError,
    )

    payment = make_payment(
        transaction_id=None,
    )

    gateway = FakeGateway(
        initialize_error=GatewayRejectedError(
            "provider rejected"
        )
    )

    failed_payment = make_payment(
        status=PaymentStatus.FAILED,
        transaction_id=None,
    )
    failed_payment.failure_reason = "provider rejected"

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            True,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    monkeypatch.setattr(
        orchestration,
        "_finalize_payment_failure",
        lambda **kwargs: failed_payment,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="paystack",
        currency="NGN",
        idempotency_key="reject-1",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert result["outcome"] == "failed"
    assert result["payment"].status == PaymentStatus.FAILED
    assert result["payment"].failure_reason == "provider rejected"


def test_retry_of_existing_pending_payment_reconciles_without_initialize(
    monkeypatch,
):
    payment = make_payment(
        transaction_id="123",
    )

    gateway = FakeGateway(
        verify_result={
            "provider": "paystack",
            "reference": "PAY-1",
            "transaction_id": "123",
            "status": "success",
            "paid": True,
            "amount": 100,
            "currency": "NGN",
        }
    )

    successful_payment = make_payment(
        status=PaymentStatus.SUCCESSFUL,
        transaction_id="123",
    )

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            False,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    monkeypatch.setattr(
        orchestration,
        "_persist_provider_transaction",
        lambda **kwargs: payment,
    )

    monkeypatch.setattr(
        orchestration,
        "_finalize_payment_success",
        lambda **kwargs: successful_payment,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="paystack",
        currency="NGN",
        idempotency_key="retry-1",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert result["outcome"] == "successful"
    assert result["payment"].status == PaymentStatus.SUCCESSFUL
    assert len(gateway.initialize_calls) == 0
    assert len(gateway.verify_calls) == 1
    assert gateway.verify_calls[0]["reference"] == "PAY-1"


def test_retry_pending_without_provider_transaction_stays_pending(
    monkeypatch,
):
    from app.modules.billing.services.gateways.base_gateway import (
        GatewayUnknownOutcomeError,
    )

    payment = make_payment(
        gateway=PaymentGateway.STRIPE,
        transaction_id=None,
    )

    gateway = FakeGateway()

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            False,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="stripe",
        currency="NGN",
        idempotency_key="pending-no-id-1",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert result["outcome"] == "pending"
    assert result["payment"] is payment
    assert len(gateway.initialize_calls) == 0
    assert gateway.verify_calls == []


def test_initialization_forwards_idempotency_key(
    monkeypatch,
):
    payment = make_payment(
        transaction_id=None,
    )

    successful_payment = make_payment(
        status=PaymentStatus.SUCCESSFUL,
        transaction_id="123",
    )

    gateway = FakeGateway(
        initialize_result={
            "provider": "paystack",
            "reference": "PAY-1",
            "transaction_id": "123",
            "status": "success",
            "paid": True,
            "amount": 100,
            "currency": "NGN",
        }
    )

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            True,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    monkeypatch.setattr(
        orchestration,
        "_persist_provider_transaction",
        lambda **kwargs: payment,
    )

    monkeypatch.setattr(
        orchestration,
        "_finalize_payment_success",
        lambda **kwargs: successful_payment,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="paystack",
        currency="NGN",
        idempotency_key="provider-idempotency-1",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert len(gateway.initialize_calls) == 1
    assert gateway.initialize_calls[0]["idempotency_key"] == (
        "provider-idempotency-1"
    )


def test_stripe_idempotency_replay_window_fails_closed_for_old_or_missing_age():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    payment = make_payment(
        gateway=PaymentGateway.STRIPE,
        transaction_id=None,
    )
    payment.created_at = now - timedelta(hours=21)
    assert orchestration._stripe_idempotency_replay_is_safe(payment, now=now)

    payment.created_at = now - timedelta(hours=22)
    assert not orchestration._stripe_idempotency_replay_is_safe(payment, now=now)

    payment.created_at = None
    assert not orchestration._stripe_idempotency_replay_is_safe(payment, now=now)

    payment.created_at = now + timedelta(seconds=1)
    assert not orchestration._stripe_idempotency_replay_is_safe(payment, now=now)


def test_retry_pending_stripe_payment_recovers_id_then_verifies_before_settlement(
    monkeypatch,
):
    payment = make_payment(
        gateway=PaymentGateway.STRIPE,
        reference="PAY-STRIPE-RECOVERY",
        transaction_id=None,
    )
    now = datetime.now(timezone.utc)
    payment.created_at = now - timedelta(minutes=5)

    gateway = FakeGateway(
        verify_result={
            "provider": "stripe",
            "reference": "PAY-STRIPE-RECOVERY",
            "transaction_id": "pi_recovered_123",
            "status": "succeeded",
            "paid": True,
            "amount": 100,
            "currency": "NGN",
        }
    )
    gateway.recovery_result = "pi_recovered_123"

    successful_payment = make_payment(
        status=PaymentStatus.SUCCESSFUL,
        gateway=PaymentGateway.STRIPE,
        reference="PAY-STRIPE-RECOVERY",
        transaction_id="pi_recovered_123",
    )

    def persist_transaction(**kwargs):
        payment.gateway_transaction_id = kwargs["gateway_transaction_id"]
        return payment

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (payment, False),
    )
    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )
    monkeypatch.setattr(
        orchestration,
        "_persist_provider_transaction",
        persist_transaction,
    )
    monkeypatch.setattr(
        orchestration,
        "_finalize_payment_success",
        lambda **kwargs: successful_payment,
    )

    result = orchestration.orchestrate_payment(
        clinic_id=1,
        invoice_id=10,
        amount=100,
        method="card",
        gateway="stripe",
        currency="NGN",
        reference="PAY-STRIPE-RECOVERY",
        idempotency_key="stripe-recovery-key",
        idempotency_user_id=1,
        actor_user_id=1,
    )

    assert result["outcome"] == "successful"
    assert result["payment"] is successful_payment
    assert len(gateway.recovery_calls) == 1
    recovery_call = gateway.recovery_calls[0]
    assert recovery_call["idempotency_key"] == "stripe-recovery-key"
    assert recovery_call["allow_idempotent_replay"] is True
    assert recovery_call["payment_id"] == payment.id
    assert gateway.verify_calls == [{"reference": "pi_recovered_123"}]
    assert payment.gateway_transaction_id == "pi_recovered_123"


def test_provider_reference_mismatch_is_rejected_safely(
    monkeypatch,
):
    payment = make_payment()

    gateway = FakeGateway(
        verify_result={
            "provider": "paystack",
            "reference": "WRONG-REFERENCE",
            "transaction_id": "123",
            "status": "success",
            "paid": True,
            "amount": 100,
            "currency": "NGN",
        }
    )

    monkeypatch.setattr(
        orchestration,
        "_get_or_create_pending_payment",
        lambda **kwargs: (
            payment,
            False,
        ),
    )

    monkeypatch.setattr(
        orchestration,
        "get_payment_gateway",
        lambda *args, **kwargs: gateway,
    )

    try:
        orchestration.orchestrate_payment(
            clinic_id=1,
            invoice_id=10,
            amount=100,
            method="card",
            gateway="paystack",
            currency="NGN",
            idempotency_key="mismatch-1",
            idempotency_user_id=1,
            actor_user_id=1,
        )
    except ConflictError as exc:
        assert "wrong payment reference" in str(exc)
    else:
        raise AssertionError(
            "Expected provider reference mismatch"
        )
