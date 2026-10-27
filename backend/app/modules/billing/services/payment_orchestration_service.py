from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
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
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
from app.core.utils.decorators import transactional

from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
)
from app.modules.billing.services.billing_service import (
    MAX_PAYMENT_REFERENCE_LENGTH,
    _get_invoice,
    _lock_clinic,
    _normalize_payment_gateway,
    _normalize_payment_method,
    _to_decimal,
    _validate_gateway_method,
    _validate_positive_id,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayUnknownOutcomeError,
)
from app.modules.billing.services.gateways.factory import (
    get_payment_gateway,
)
from app.modules.billing.services.gateways.payment_circuit_breaker import (
    acquire_payment_circuit,
    call_payment_with_circuit,
    call_payment_with_permit,
)
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)
from app.modules.patient.models.patient_model import (
    Patient,
)


def _normalize_reference(
    reference,
    clinic_id,
):
    if reference is None:
        return (
            f"PAY-{clinic_id}-"
            f"{uuid4().hex[:20].upper()}"
        )

    if not isinstance(reference, str):
        raise ValidationError(
            "Payment reference must be a string"
        )

    reference = reference.strip()

    if not reference:
        raise ValidationError(
            "Payment reference cannot be blank"
        )

    if len(reference) > MAX_PAYMENT_REFERENCE_LENGTH:
        raise ValidationError(
            "Payment reference cannot exceed "
            f"{MAX_PAYMENT_REFERENCE_LENGTH} characters"
        )

    return reference


def _normalize_currency(currency):
    if not isinstance(currency, str):
        raise ValidationError(
            "Currency must be a string"
        )

    currency = currency.strip().upper()

    if (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise ValidationError(
            "Currency must be a 3-letter code"
        )

    return currency


def _patient_email_for_invoice(invoice):
    patient = (
        invoice.patient
        if getattr(invoice, "patient", None) is not None
        else db.session.get(
            Patient,
            invoice.patient_id,
        )
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {invoice.patient_id} not found"
        )

    email = (
        patient.email.strip()
        if isinstance(patient.email, str)
        else ""
    )

    if not email:
        raise ValidationError(
            "Patient email is required for gateway payment"
        )

    return email


@transactional
def _get_or_create_pending_payment(
    *,
    clinic_id,
    invoice_id,
    amount,
    method,
    gateway,
    currency,
    reference,
    idempotency_key,
    idempotency_user_id,
    actor_user_id,
    before_provider_initialization=None,
):
    if idempotency_user_id is None:
        raise ValidationError(
            "Idempotency user ID is required"
        )

    idempotency_record, created = (
        reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation="billing.payment.initialize",
            idempotency_key=idempotency_key,
            request_payload={
                "invoice_id": invoice_id,
                "amount": amount,
                "method": method,
                "gateway": gateway,
                "currency": currency,
                "reference": reference,
            },
        )
    )

    if not created:
        if (
            idempotency_record.entity_type
            != "Payment"
            or idempotency_record.entity_id is None
        ):
            raise ConflictError(
                "Idempotency record does not reference "
                "a payment"
            )

        existing_payment = db.session.get(
            Payment,
            idempotency_record.entity_id,
        )

        if existing_payment is None:
            raise ConflictError(
                "Idempotency record references "
                "a missing payment"
            )

        existing_invoice = db.session.get(
            Invoice,
            existing_payment.invoice_id,
        )

        if existing_invoice is None:
            raise ConflictError(
                "Idempotency record references "
                "a payment with a missing invoice"
            )

        if existing_invoice.clinic_id != clinic_id:
            raise ConflictError(
                "Idempotency record references "
                "another clinic"
            )

        if (
            not isinstance(existing_invoice.currency, str)
            or not existing_invoice.currency.strip()
            or not isinstance(existing_payment.currency, str)
            or not existing_payment.currency.strip()
        ):
            raise ConflictError(
                "Invoice or payment currency is unknown; cannot replay payment"
            )

        invoice_currency = _normalize_currency(existing_invoice.currency)
        payment_currency = _normalize_currency(existing_payment.currency)

        if (
            invoice_currency != payment_currency
            or _normalize_currency(currency) != invoice_currency
        ):
            raise ConflictError(
                "Payment currency does not match the invoice currency"
            )

        return existing_payment, False

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        invoice_id,
        "Invoice ID",
    )

    _lock_clinic(clinic_id)
    ensure_clinic_active(clinic_id)

    invoice = _get_invoice(
        invoice_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if (
        not isinstance(invoice.currency, str)
        or not invoice.currency.strip()
    ):
        raise ConflictError(
            "Invoice currency is unknown; cannot initialize payment"
        )

    invoice_currency = _normalize_currency(invoice.currency)
    currency = _normalize_currency(currency)

    if currency != invoice_currency:
        raise ValidationError(
            "Payment currency must match the invoice currency"
        )

    if invoice.status == InvoiceStatus.CANCELLED:
        raise ConflictError(
            "Cannot initialize payment for "
            f"cancelled invoice {invoice.invoice_number}"
        )

    if invoice.status == InvoiceStatus.PAID:
        raise ConflictError(
            f"Invoice {invoice.invoice_number} "
            "is already fully paid"
        )

    amount = _to_decimal(
        amount,
        "payment amount",
    )

    if amount <= Decimal("0"):
        raise ValidationError(
            "Payment amount must be greater than zero"
        )

    method = _normalize_payment_method(method)
    gateway = _normalize_payment_gateway(gateway)

    if gateway is None:
        raise ValidationError(
            "Payment gateway is required"
        )

    _validate_gateway_method(
        method,
        gateway,
    )

    currency = _normalize_currency(currency)
    reference = _normalize_reference(
        reference,
        clinic_id,
    )

    total_amount = Decimal(
        invoice.total_amount or 0
    )
    amount_paid = Decimal(
        invoice.amount_paid or 0
    )
    remaining_balance = (
        total_amount - amount_paid
    )

    if amount > remaining_balance:
        raise ValidationError(
            "Payment amount exceeds the remaining "
            "invoice balance of "
            f"{remaining_balance}"
        )

    if before_provider_initialization is not None:
        before_provider_initialization(
            gateway=gateway,
            invoice=invoice,
        )

    payment = Payment(
        invoice_id=invoice.id,
        amount=amount,
        currency=currency,
        method=method,
        status=PaymentStatus.PENDING,
        gateway=gateway,
        reference=reference,
    )

    db.session.add(payment)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Payment",
        entity_id=payment.id,
        clinic_id=clinic_id,
        user_id=actor_user_id,
        description=(
            f"Payment intent {reference} created "
            f"for invoice {invoice.invoice_number}"
        ),
        new_value={
            "invoice_id": invoice.id,
            "amount": str(amount),
            "method": method.value,
            "gateway": gateway.value,
            "reference": reference,
            "currency": currency,
            "status": PaymentStatus.PENDING.value,
        },
    )

    idempotency_record.entity_type = "Payment"
    idempotency_record.entity_id = payment.id

    return payment, True


@transactional
def _persist_provider_transaction(
    *,
    clinic_id,
    payment_id,
    gateway_transaction_id,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        payment_id,
        "Payment ID",
    )

    payment = (
        Payment.query
        .join(
            Invoice,
            Payment.invoice_id == Invoice.id,
        )
        .filter(
            Payment.id == payment_id,
            Invoice.clinic_id == clinic_id,
        )
        .with_for_update()
        .first()
    )

    if payment is None:
        raise NotFoundError(
            f"Payment {payment_id} not found"
        )

    if payment.status != PaymentStatus.PENDING:
        return payment

    if gateway_transaction_id is None:
        return payment

    gateway_transaction_id = str(
        gateway_transaction_id
    ).strip()

    if not gateway_transaction_id:
        return payment

    if (
        payment.gateway_transaction_id
        and payment.gateway_transaction_id
        != gateway_transaction_id
    ):
        raise ConflictError(
            "Provider returned a different "
            "transaction ID for the same payment"
        )

    payment.gateway_transaction_id = (
        gateway_transaction_id
    )

    return payment


def _validate_provider_result(
    payment,
    result,
):
    if not isinstance(result, dict):
        raise GatewayUnknownOutcomeError(
            "Provider returned an invalid "
            "verification response"
        )

    provider = result.get("provider")

    if (
        provider
        and provider != payment.gateway.value
    ):
        raise ConflictError(
            "Provider verification returned "
            "the wrong gateway"
        )

    result_reference = result.get(
        "reference"
    )

    if (
        result_reference
        and result_reference != payment.reference
    ):
        raise ConflictError(
            "Provider verification returned "
            "the wrong payment reference"
        )

    transaction_id = result.get(
        "transaction_id"
    )

    if (
        transaction_id is not None
        and payment.gateway_transaction_id
        and str(transaction_id)
        != str(
            payment.gateway_transaction_id
        )
    ):
        raise ConflictError(
            "Provider verification returned "
            "the wrong transaction ID"
        )

    provider_amount = result.get("amount")

    if provider_amount is None:
        raise ConflictError(
            "Provider verification amount is missing"
        )

    try:
        provider_amount = Decimal(str(provider_amount))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise ConflictError(
            "Provider verification amount is invalid"
        ) from exc

    if not provider_amount.is_finite():
        raise ConflictError(
            "Provider verification amount is invalid"
        )

    expected_amount = Decimal(str(payment.amount))

    if provider_amount != expected_amount:
        raise ConflictError(
            "Provider verification amount does not match the pending payment"
        )

    provider_currency = result.get("currency")

    if (
        not isinstance(provider_currency, str)
        or not provider_currency.strip()
    ):
        raise ConflictError(
            "Provider verification currency is missing"
        )

    provider_currency = provider_currency.strip().upper()

    if len(provider_currency) != 3 or not provider_currency.isalpha():
        raise ConflictError(
            "Provider verification currency is invalid"
        )

    expected_currency = getattr(payment, "currency", None)

    if (
        not isinstance(expected_currency, str)
        or not expected_currency.strip()
    ):
        raise ConflictError(
            "Pending payment currency is unknown; cannot verify provider currency"
        )

    expected_currency = expected_currency.strip().upper()

    if provider_currency != expected_currency:
        raise ConflictError(
            "Provider verification currency does not match the pending payment"
        )

    result["amount"] = provider_amount
    result["currency"] = provider_currency

    return result


@transactional
def _finalize_payment_success(
    *,
    clinic_id,
    payment_id,
    actor_user_id,
    gateway_transaction_id=None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        payment_id,
        "Payment ID",
    )

    payment = (
        Payment.query
        .join(
            Invoice,
            Payment.invoice_id == Invoice.id,
        )
        .filter(
            Payment.id == payment_id,
            Invoice.clinic_id == clinic_id,
        )
        .with_for_update()
        .first()
    )

    if payment is None:
        raise NotFoundError(
            f"Payment {payment_id} not found"
        )

    if payment.status == PaymentStatus.SUCCESSFUL:
        return payment

    if payment.status != PaymentStatus.PENDING:
        raise ConflictError(
            "Only a pending payment can be "
            "finalized successfully"
        )

    invoice = (
        Invoice.query
        .filter(
            Invoice.id == payment.invoice_id,
            Invoice.clinic_id == clinic_id,
        )
        .with_for_update()
        .first()
    )

    if invoice is None:
        raise NotFoundError(
            f"Invoice {payment.invoice_id} not found"
        )

    if (
        not isinstance(invoice.currency, str)
        or not invoice.currency.strip()
        or not isinstance(payment.currency, str)
        or not payment.currency.strip()
    ):
        raise ConflictError(
            "Invoice or payment currency is unknown; cannot finalize payment"
        )

    if (
        _normalize_currency(invoice.currency)
        != _normalize_currency(payment.currency)
    ):
        raise ConflictError(
            "Payment currency does not match the invoice currency"
        )

    amount_paid = Decimal(
        invoice.amount_paid or 0
    )
    total_amount = Decimal(
        invoice.total_amount or 0
    )

    if (
        amount_paid + Decimal(payment.amount)
        > total_amount
    ):
        raise ConflictError(
            "Payment would exceed the remaining "
            "invoice balance"
        )

    if gateway_transaction_id is not None:
        gateway_transaction_id = str(
            gateway_transaction_id
        ).strip() or None

    if gateway_transaction_id:
        if (
            payment.gateway_transaction_id
            and payment.gateway_transaction_id
            != gateway_transaction_id
        ):
            raise ConflictError(
                "Provider returned a different "
                "transaction ID for the same payment"
            )

        payment.gateway_transaction_id = (
            gateway_transaction_id
        )

    if (
        payment.gateway
        and not payment.gateway_transaction_id
    ):
        raise ConflictError(
            "Successful gateway payment requires "
            "a provider transaction ID"
        )

    old_status = payment.status.value
    old_invoice_status = invoice.status.value

    payment.status = PaymentStatus.SUCCESSFUL
    payment.failure_reason = None
    payment.paid_at = (
        db.func.now()
    )

    invoice.amount_paid = (
        amount_paid
        + Decimal(payment.amount)
    )

    if invoice.amount_paid >= total_amount:
        invoice.status = InvoiceStatus.PAID
    elif invoice.amount_paid > Decimal("0"):
        invoice.status = InvoiceStatus.PARTIALLY_PAID
    else:
        invoice.status = InvoiceStatus.ISSUED

    db.session.flush()

    create_audit_log(
        action=AuditAction.PAYMENT,
        entity_type="Invoice",
        entity_id=invoice.id,
        clinic_id=clinic_id,
        user_id=actor_user_id,
        description=(
            f"Payment of {payment.amount} "
            f"confirmed via {payment.method.value}"
        ),
        old_value={
            "payment_status": old_status,
            "invoice_status": old_invoice_status,
            "amount_paid": str(
                amount_paid
            ),
        },
        new_value={
            "payment_id": payment.id,
            "payment_status": payment.status.value,
            "invoice_status": invoice.status.value,
            "amount_paid": str(
                invoice.amount_paid
            ),
            "gateway_transaction_id": (
                payment.gateway_transaction_id
            ),
        },
    )

    return payment


@transactional
def _finalize_payment_failure(
    *,
    clinic_id,
    payment_id,
    actor_user_id,
    failure_reason,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        payment_id,
        "Payment ID",
    )

    payment = (
        Payment.query
        .join(
            Invoice,
            Payment.invoice_id == Invoice.id,
        )
        .filter(
            Payment.id == payment_id,
            Invoice.clinic_id == clinic_id,
        )
        .with_for_update()
        .first()
    )

    if payment is None:
        raise NotFoundError(
            f"Payment {payment_id} not found"
        )

    if payment.status == PaymentStatus.SUCCESSFUL:
        return payment

    if payment.status == PaymentStatus.FAILED:
        return payment

    reason = str(
        failure_reason
        or "Payment provider rejected the payment"
    ).strip()

    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason[:255]
    payment.paid_at = None

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Payment",
        entity_id=payment.id,
        clinic_id=clinic_id,
        user_id=actor_user_id,
        description=(
            f"Payment {payment.reference} "
            "failed at payment provider"
        ),
        old_value={
            "status": PaymentStatus.PENDING.value,
        },
        new_value={
            "status": PaymentStatus.FAILED.value,
            "failure_reason": payment.failure_reason,
        },
    )

    return payment


def _outcome(
    payment,
    outcome,
    reason=None,
):
    return {
        "payment": payment,
        "outcome": outcome,
        "reason": reason,
    }


def _verification_reference(payment):
    if payment.gateway == PaymentGateway.PAYSTACK:
        return payment.reference

    if payment.gateway_transaction_id:
        return payment.gateway_transaction_id

    raise GatewayUnknownOutcomeError(
        "Payment has no provider transaction ID "
        "for reconciliation"
    )


def _is_successful_provider_result(result):
    status = str(
        result.get("status") or ""
    ).strip().lower()

    return (
        result.get("paid") is True
        or status in {
            "success",
            "successful",
            "succeeded",
        }
    )


def _is_failed_provider_result(result):
    status = str(
        result.get("status") or ""
    ).strip().lower()

    return status in {
        "failed",
        "abandoned",
        "reversed",
        "cancelled",
        "canceled",
        "declined",
    }


STRIPE_IDEMPOTENCY_REPLAY_SAFE_WINDOW = timedelta(hours=22)


def _stripe_idempotency_replay_is_safe(payment, *, now=None):
    """Conservatively bound Stripe POST replay by local payment age.

    Stripe may prune idempotency keys after at least 24 hours. The 22-hour
    window leaves margin for clock skew and for the payment row being created
    before the first request reaches Stripe. A missing or future timestamp
    fails closed and permits only non-mutating recovery/search.
    """
    created_at = getattr(payment, "created_at", None)
    if not isinstance(created_at, datetime):
        return False

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    age = current_time.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)
    return timedelta(0) <= age < STRIPE_IDEMPOTENCY_REPLAY_SAFE_WINDOW


def _reconcile_pending_payment(
    *,
    clinic_id,
    payment,
    actor_user_id,
    idempotency_key=None,
):
    gateway = get_payment_gateway(
        payment.gateway,
        clinic_id=clinic_id,
    )

    recovered_verification_reference = None

    if (
        payment.gateway == PaymentGateway.STRIPE
        and not payment.gateway_transaction_id
    ):
        recover_payment_intent_id = getattr(
            gateway,
            "recover_payment_intent_id",
            None,
        )

        if callable(recover_payment_intent_id):
            customer_email = None
            invoice = getattr(payment, "invoice", None)

            if invoice is None:
                try:
                    invoice = db.session.get(
                        Invoice,
                        payment.invoice_id,
                    )
                except Exception:
                    invoice = None

            if invoice is not None:
                try:
                    customer_email = _patient_email_for_invoice(invoice)
                except Exception:
                    customer_email = None

            try:
                recovered_transaction_id = recover_payment_intent_id(
                    reference=payment.reference,
                    amount=payment.amount,
                    currency=payment.currency,
                    customer_email=customer_email,
                    idempotency_key=idempotency_key,
                    clinic_id=clinic_id,
                    invoice_id=payment.invoice_id,
                    payment_id=payment.id,
                    allow_idempotent_replay=(
                        _stripe_idempotency_replay_is_safe(payment)
                    ),
                )
            except Exception:
                recovered_transaction_id = None

            if (
                not isinstance(recovered_transaction_id, str)
                or not recovered_transaction_id.strip()
            ):
                return _outcome(
                    payment,
                    "pending",
                    "Stripe PaymentIntent could not yet be recovered; payment remains pending",
                )

            # Do not persist or settle from recovery alone. This ID is only the
            # locator for the subsequent, authoritative Stripe retrieval.
            recovered_verification_reference = (
                recovered_transaction_id.strip()
            )

    try:
        verification_reference = (
            recovered_verification_reference
            or _verification_reference(payment)
        )

        if payment.gateway in {
            PaymentGateway.PAYSTACK,
            PaymentGateway.FLUTTERWAVE,
        }:
            result = call_payment_with_circuit(
                provider=payment.gateway.value,
                operation="verify",
                function=gateway.verify_payment,
                kwargs={
                    "reference": verification_reference,
                },
            )
        else:
            result = gateway.verify_payment(
                reference=verification_reference,
            )
    except GatewayUnknownOutcomeError as exc:
        return _outcome(
            payment,
            "pending",
            str(exc),
        )
    except Exception as exc:
        return _outcome(
            payment,
            "pending",
            "Provider outcome still requires reconciliation",
        )

    result = _validate_provider_result(
        payment,
        result,
    )

    transaction_id = result.get(
        "transaction_id"
    )

    if transaction_id is not None:
        payment = _persist_provider_transaction(
            clinic_id=clinic_id,
            payment_id=payment.id,
            gateway_transaction_id=transaction_id,
        )

    if _is_successful_provider_result(
        result
    ):
        if not payment.gateway_transaction_id:
            return _outcome(
                payment,
                "pending",
                "Provider reported success without "
                "a transaction ID",
            )

        payment = _finalize_payment_success(
            clinic_id=clinic_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            gateway_transaction_id=(
                payment.gateway_transaction_id
            ),
        )

        return _outcome(
            payment,
            "successful",
        )

    if _is_failed_provider_result(
        result
    ):
        payment = _finalize_payment_failure(
            clinic_id=clinic_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            failure_reason=(
                result.get("failure_reason")
                or "Payment failed at provider"
            ),
        )

        return _outcome(
            payment,
            "failed",
            payment.failure_reason,
        )

    return _outcome(
        payment,
        "pending",
        "Payment remains pending at provider",
    )


def orchestrate_payment(
    *,
    clinic_id,
    invoice_id,
    amount,
    method,
    gateway,
    currency,
    reference=None,
    idempotency_key=None,
    idempotency_user_id=None,
    actor_user_id=None,
):
    if idempotency_key is None:
        raise ValidationError(
            "Idempotency-Key is required for "
            "gateway payment orchestration"
        )

    currency = _normalize_currency(
        currency
    )

    initialization_context = {}

    def _prepare_provider_initialization(
        *,
        gateway,
        invoice,
    ):
        gateway_client = get_payment_gateway(
            gateway,
            clinic_id=clinic_id,
        )
        customer_email = _patient_email_for_invoice(
            invoice
        )

        context = {
            "gateway_client": gateway_client,
            "customer_email": customer_email,
            "breaker": None,
            "permit": None,
        }

        if gateway in {
            PaymentGateway.PAYSTACK,
            PaymentGateway.FLUTTERWAVE,
        }:
            breaker, permit = acquire_payment_circuit(
                provider=gateway.value,
                operation="initialize",
            )
            context["breaker"] = breaker
            context["permit"] = permit

        initialization_context.update(context)

    payment, created = (
        _get_or_create_pending_payment(
            clinic_id=clinic_id,
            invoice_id=invoice_id,
            amount=amount,
            method=method,
            gateway=gateway,
            currency=currency,
            reference=reference,
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
            actor_user_id=actor_user_id,
            before_provider_initialization=(
                _prepare_provider_initialization
            ),
        )
    )

    if not created:
        if payment.status != PaymentStatus.PENDING:
            outcome = (
                "successful"
                if payment.status
                == PaymentStatus.SUCCESSFUL
                else "failed"
            )

            return _outcome(
                payment,
                outcome,
                payment.failure_reason,
            )

        return _reconcile_pending_payment(
            clinic_id=clinic_id,
            payment=payment,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
        )

    gateway_client = initialization_context[
        "gateway_client"
    ]
    customer_email = initialization_context[
        "customer_email"
    ]

    initialization_kwargs = {
        "reference": payment.reference,
        "amount": payment.amount,
        "currency": currency,
        "customer_email": customer_email,
        "idempotency_key": idempotency_key,
    }

    if payment.gateway == PaymentGateway.STRIPE:
        # Stable local identity makes Search API fallback unambiguous. These
        # fields contain internal numeric identifiers, not patient data.
        initialization_kwargs["metadata"] = {
            "clinic_id": clinic_id,
            "clinic_payment_id": payment.id,
            "invoice_id": payment.invoice_id,
        }

    try:
        breaker = initialization_context[
            "breaker"
        ]
        permit = initialization_context[
            "permit"
        ]

        if breaker is not None and permit is not None:
            result = call_payment_with_permit(
                breaker,
                permit,
                gateway_client.initialize_payment,
                **initialization_kwargs,
            )
        else:
            result = gateway_client.initialize_payment(
                **initialization_kwargs,
            )
    except GatewayRejectedError as exc:
        payment = _finalize_payment_failure(
            clinic_id=clinic_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            failure_reason=str(exc),
        )

        return _outcome(
            payment,
            "failed",
            payment.failure_reason,
        )
    except GatewayUnknownOutcomeError:
        return _outcome(
            payment,
            "pending",
            "Provider outcome requires reconciliation",
        )
    except Exception:
        return _outcome(
            payment,
            "pending",
            "Provider outcome requires reconciliation",
        )

    # Initialization must not finalize a payment from an unverified success-shaped response.
    if _is_successful_provider_result(result):
        result = _validate_provider_result(
            payment,
            result,
        )

    transaction_id = result.get(
        "transaction_id"
    )

    if transaction_id is not None:
        payment = _persist_provider_transaction(
            clinic_id=clinic_id,
            payment_id=payment.id,
            gateway_transaction_id=transaction_id,
        )

    if _is_successful_provider_result(
        result
    ):
        if not payment.gateway_transaction_id:
            return _outcome(
                payment,
                "pending",
                "Provider reported success without "
                "a transaction ID",
            )

        payment = _finalize_payment_success(
            clinic_id=clinic_id,
            payment_id=payment.id,
            actor_user_id=actor_user_id,
            gateway_transaction_id=(
                payment.gateway_transaction_id
            ),
        )

        return _outcome(
            payment,
            "successful",
        )

    return _outcome(
        payment,
        "pending",
        "Payment initialized; provider confirmation required",
    )
