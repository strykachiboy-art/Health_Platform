from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.billing_enums import (
    InvoiceStatus,
    PaymentGateway,
    PaymentStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.modules.billing.models.billing_model import (
    Invoice,
    Payment,
    PaymentWebhookEvent,
)
from app.modules.billing.services.gateways.factory import (
    get_payment_gateway,
)


MAX_EVENT_ID_LENGTH = 255
MAX_EVENT_TYPE_LENGTH = 120
MAX_REFERENCE_LENGTH = 120
MAX_TRANSACTION_ID_LENGTH = 255


def _normalize_gateway(gateway) -> PaymentGateway:
    if isinstance(gateway, PaymentGateway):
        return gateway

    if not isinstance(gateway, str):
        raise ValidationError(
            "Payment gateway is required"
        )

    try:
        return PaymentGateway(
            gateway.strip().lower()
        )
    except ValueError as exc:
        raise ValidationError(
            f"Unsupported payment gateway: {gateway}"
        ) from exc


def _normalize_required_string(
    value,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} is required"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{field_name} is required"
        )

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _normalize_optional_string(
    value,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _validate_payload(payload: bytes) -> bytes:
    if not isinstance(payload, bytes):
        raise ValidationError(
            "Webhook payload must be bytes"
        )

    if not payload:
        raise ValidationError(
            "Webhook payload is required"
        )

    return payload


def _normalize_webhook_result(
    *,
    gateway: PaymentGateway,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValidationError(
            "Gateway returned an invalid webhook event"
        )

    provider = result.get("provider")

    if provider and provider != gateway.value:
        raise ConflictError(
            "Webhook provider does not match the route gateway"
        )

    event_id = _normalize_required_string(
        result.get("event_id"),
        "Webhook event ID",
        MAX_EVENT_ID_LENGTH,
    )

    event_type = _normalize_required_string(
        result.get("event_type"),
        "Webhook event type",
        MAX_EVENT_TYPE_LENGTH,
    )

    reference = _normalize_optional_string(
        result.get("reference"),
        "Webhook reference",
        MAX_REFERENCE_LENGTH,
    )

    transaction_id = _normalize_optional_string(
        result.get("transaction_id"),
        "Webhook transaction ID",
        MAX_TRANSACTION_ID_LENGTH,
    )

    currency = _normalize_optional_string(
        result.get("currency"),
        "Webhook currency",
        3,
    )

    if currency and (
        len(currency) != 3
        or not currency.isalpha()
    ):
        raise ValidationError(
            "Webhook currency must be a 3-letter code"
        )

    status = _normalize_required_string(
        result.get("status"),
        "Webhook status",
        32,
    ).lower()

    failure_reason = _normalize_optional_string(
        result.get("failure_reason"),
        "Webhook failure reason",
        255,
    )

    amount = result.get("amount")

    if amount is not None:
        try:
            amount = Decimal(str(amount))
        except (
            InvalidOperation,
            TypeError,
            ValueError,
        ) as exc:
            raise ValidationError(
                "Webhook amount is invalid"
            ) from exc

        if not amount.is_finite():
            raise ValidationError(
                "Webhook amount is invalid"
            )

    return {
        "event_id": event_id,
        "event_type": event_type,
        "reference": reference,
        "transaction_id": transaction_id,
        "currency": currency.upper() if currency else None,
        "amount": amount,
        "status": status,
        "failure_reason": failure_reason,
    }


def _find_payment_by_transaction(
    *,
    clinic_id: int,
    gateway: PaymentGateway,
    transaction_id: str,
):
    statement = (
        select(Payment)
        .join(
            Invoice,
            Payment.invoice_id == Invoice.id,
        )
        .where(
            Invoice.clinic_id == clinic_id,
            Payment.gateway == gateway,
            Payment.gateway_transaction_id
            == transaction_id,
        )
        .with_for_update()
    )

    return db.session.execute(
        statement
    ).scalar_one_or_none()


def _find_payment_by_reference(
    *,
    clinic_id: int,
    gateway: PaymentGateway,
    reference: str,
):
    statement = (
        select(Payment)
        .join(
            Invoice,
            Payment.invoice_id == Invoice.id,
        )
        .where(
            Invoice.clinic_id == clinic_id,
            Payment.gateway == gateway,
            Payment.reference == reference,
        )
        .order_by(
            Payment.id.desc()
        )
        .limit(2)
        .with_for_update()
    )

    payments = list(
        db.session.execute(
            statement
        ).scalars()
    )

    if len(payments) > 1:
        raise ConflictError(
            "Webhook reference matches multiple payments"
        )

    return payments[0] if payments else None


def _find_payment(
    *,
    clinic_id: int,
    gateway: PaymentGateway,
    transaction_id: str | None,
    reference: str | None,
):
    payment = None

    if transaction_id:
        payment = _find_payment_by_transaction(
            clinic_id=clinic_id,
            gateway=gateway,
            transaction_id=transaction_id,
        )

    if payment is not None:
        if (
            reference
            and payment.reference
            and payment.reference != reference
        ):
            raise ConflictError(
                "Webhook transaction and reference identify different payments"
            )

        return payment

    if reference:
        payment = _find_payment_by_reference(
            clinic_id=clinic_id,
            gateway=gateway,
            reference=reference,
        )

        if payment is not None:
            if (
                transaction_id
                and payment.gateway_transaction_id
                and payment.gateway_transaction_id
                != transaction_id
            ):
                raise ConflictError(
                    "Webhook transaction ID does not match the payment"
                )

            return payment

    raise NotFoundError(
        "Webhook does not identify a payment"
    )


def _validate_payment_match(
    *,
    payment: Payment,
    gateway: PaymentGateway,
    reference: str | None,
    transaction_id: str | None,
    amount: Decimal | None,
    currency: str | None,
    status: str,
):
    if payment.gateway != gateway:
        raise ConflictError(
            "Webhook gateway does not match the payment"
        )

    if (
        reference
        and payment.reference
        and reference != payment.reference
    ):
        raise ConflictError(
            "Webhook reference does not match the payment"
        )

    if (
        transaction_id
        and payment.gateway_transaction_id
        and transaction_id != payment.gateway_transaction_id
    ):
        raise ConflictError(
            "Webhook transaction ID does not match the payment"
        )

    successful = status in {
        "success",
        "successful",
        "succeeded",
    }

    expected_currency = getattr(payment, "currency", None)
    if isinstance(expected_currency, str) and expected_currency.strip():
        expected_currency = expected_currency.strip().upper()
    else:
        expected_currency = None

    if currency is not None:
        if not isinstance(currency, str) or not currency.strip():
            if successful:
                raise ConflictError(
                    "Successful webhook currency is missing"
                )
        else:
            normalized_currency = currency.strip().upper()
            if len(normalized_currency) != 3 or not normalized_currency.isalpha():
                raise ConflictError(
                    "Webhook currency is invalid"
                )
            if expected_currency and normalized_currency != expected_currency:
                raise ConflictError(
                    "Webhook currency does not match the payment"
                )
            if successful and expected_currency is None:
                raise ConflictError(
                    "Payment currency is unknown; cannot verify webhook currency"
                )
    elif successful:
        raise ConflictError(
            "Successful webhook currency is missing"
        )

    if successful and expected_currency is None:
        raise ConflictError(
            "Payment currency is unknown; cannot verify webhook currency"
        )

    if amount is None:
        if successful:
            raise ConflictError(
                "Successful webhook amount is missing"
            )
        return

    try:
        normalized_amount = Decimal(str(amount))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise ConflictError(
            "Webhook amount is invalid"
        ) from exc

    if not normalized_amount.is_finite():
        raise ConflictError(
            "Webhook amount is invalid"
        )

    expected_amount = Decimal(str(payment.amount))
    if normalized_amount != expected_amount:
        raise ConflictError(
            "Webhook amount does not match the payment"
        )


def _reserve_event(
    *,
    clinic_id: int,
    gateway: PaymentGateway,
    normalized: dict[str, Any],
    payment_id: int,
):
    event = PaymentWebhookEvent(
        clinic_id=clinic_id,
        payment_id=payment_id,
        gateway=gateway.value,
        event_id=normalized["event_id"],
        event_type=normalized["event_type"],
        reference=normalized["reference"],
        transaction_id=normalized["transaction_id"],
    )

    try:
        with db.session.begin_nested():
            db.session.add(event)
            db.session.flush()

    except IntegrityError:
        existing_statement = select(
            PaymentWebhookEvent
        ).where(
            PaymentWebhookEvent.clinic_id == clinic_id,
            PaymentWebhookEvent.gateway == gateway.value,
            PaymentWebhookEvent.event_id
            == normalized["event_id"],
        )

        existing = db.session.execute(
            existing_statement
        ).scalar_one_or_none()

        if existing is None:
            raise

        return existing, False

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="PaymentWebhookEvent",
        entity_id=event.id,
        clinic_id=clinic_id,
        user_id=None,
        description=(
            f"{gateway.value} webhook event "
            f"{event.event_id} accepted"
        ),
        new_value={
            "payment_id": payment_id,
            "event_type": event.event_type,
            "reference": event.reference,
            "transaction_id": event.transaction_id,
        },
    )

    return event, True


def _mark_payment_successful(
    *,
    clinic_id: int,
    payment: Payment,
    transaction_id: str | None,
):
    invoice_statement = (
        select(Invoice)
        .where(
            Invoice.id == payment.invoice_id,
            Invoice.clinic_id == clinic_id,
        )
        .with_for_update()
    )

    invoice = db.session.execute(
        invoice_statement
    ).scalar_one_or_none()

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
        invoice.currency.strip().upper()
        != payment.currency.strip().upper()
    ):
        raise ConflictError(
            "Payment currency does not match the invoice currency"
        )

    if invoice.status == InvoiceStatus.CANCELLED:
        raise ConflictError(
            "Cannot finalize a payment for a cancelled invoice"
        )

    amount_paid = Decimal(
        str(invoice.amount_paid or 0)
    )
    total_amount = Decimal(
        str(invoice.total_amount or 0)
    )
    payment_amount = Decimal(
        str(payment.amount)
    )

    if amount_paid + payment_amount > total_amount:
        raise ConflictError(
            "Webhook payment would exceed the remaining invoice balance"
        )

    if transaction_id:
        if (
            payment.gateway_transaction_id
            and payment.gateway_transaction_id
            != transaction_id
        ):
            raise ConflictError(
                "Webhook transaction ID does not match the payment"
            )

        payment.gateway_transaction_id = transaction_id

    elif (
        payment.gateway
        and not payment.gateway_transaction_id
    ):
        raise ConflictError(
            "Successful gateway webhook requires a provider transaction ID"
        )

    old_payment_status = payment.status.value
    old_invoice_status = invoice.status.value

    payment.status = PaymentStatus.SUCCESSFUL
    payment.failure_reason = None
    payment.paid_at = db.func.now()

    invoice.amount_paid = (
        amount_paid + payment_amount
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
        user_id=None,
        description=(
            f"Payment {payment.reference} "
            "confirmed by provider webhook"
        ),
        old_value={
            "payment_status": old_payment_status,
            "invoice_status": old_invoice_status,
            "amount_paid": str(amount_paid),
        },
        new_value={
            "payment_id": payment.id,
            "payment_status": payment.status.value,
            "invoice_status": invoice.status.value,
            "amount_paid": str(invoice.amount_paid),
            "gateway_transaction_id": (
                payment.gateway_transaction_id
            ),
        },
    )


def _mark_payment_failed(
    *,
    clinic_id: int,
    payment: Payment,
    transaction_id: str | None,
    failure_reason: str | None,
):
    if transaction_id:
        if (
            payment.gateway_transaction_id
            and payment.gateway_transaction_id
            != transaction_id
        ):
            raise ConflictError(
                "Webhook transaction ID does not match the payment"
            )

        payment.gateway_transaction_id = transaction_id

    payment.status = PaymentStatus.FAILED
    payment.failure_reason = (
        failure_reason
        or "Payment provider reported failure"
    )[:255]
    payment.paid_at = None

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Payment",
        entity_id=payment.id,
        clinic_id=clinic_id,
        user_id=None,
        description=(
            f"Payment {payment.reference} "
            "failed by provider webhook"
        ),
        old_value={
            "status": PaymentStatus.PENDING.value,
        },
        new_value={
            "status": payment.status.value,
            "failure_reason": payment.failure_reason,
        },
    )


@transactional
def process_payment_webhook(
    *,
    clinic_id: int,
    gateway,
    payload: bytes,
    signature: str | None = None,
    headers: dict[str, str] | None = None,
):
    normalized_gateway = _normalize_gateway(
        gateway
    )

    payload = _validate_payload(payload)

    provider = get_payment_gateway(
        normalized_gateway,
        clinic_id=clinic_id,
    )

    try:
        provider_result = provider.handle_webhook(
            payload=payload,
            signature=signature,
            headers=headers,
        )
    except ValueError as exc:
        raise ValidationError(
            str(exc)
        ) from exc

    normalized = _normalize_webhook_result(
        gateway=normalized_gateway,
        result=provider_result,
    )

    payment = _find_payment(
        clinic_id=clinic_id,
        gateway=normalized_gateway,
        transaction_id=normalized["transaction_id"],
        reference=normalized["reference"],
    )

    _validate_payment_match(
        payment=payment,
        gateway=normalized_gateway,
        reference=normalized["reference"],
        transaction_id=normalized["transaction_id"],
        amount=normalized["amount"],
        currency=normalized["currency"],
        status=normalized["status"],
    )

    event, created = _reserve_event(
        clinic_id=clinic_id,
        gateway=normalized_gateway,
        normalized=normalized,
        payment_id=payment.id,
    )

    if not created:
        return {
            "status": "duplicate",
            "event_id": event.event_id,
            "payment_id": event.payment_id,
        }

    if payment.status != PaymentStatus.PENDING:
        return {
            "status": "ignored_terminal",
            "event_id": event.event_id,
            "payment_id": payment.id,
            "payment_status": payment.status.value,
        }

    if normalized["status"] in {
        "success",
        "successful",
        "succeeded",
    }:
        _mark_payment_successful(
            clinic_id=clinic_id,
            payment=payment,
            transaction_id=normalized["transaction_id"],
        )

        return {
            "status": "processed_successful",
            "event_id": event.event_id,
            "payment_id": payment.id,
            "payment_status": payment.status.value,
        }

    if normalized["status"] in {
        "failed",
        "abandoned",
        "reversed",
        "cancelled",
        "canceled",
        "declined",
    }:
        _mark_payment_failed(
            clinic_id=clinic_id,
            payment=payment,
            transaction_id=normalized["transaction_id"],
            failure_reason=normalized["failure_reason"],
        )

        return {
            "status": "processed_failed",
            "event_id": event.event_id,
            "payment_id": payment.id,
            "payment_status": payment.status.value,
        }

    return {
        "status": "pending",
        "event_id": event.event_id,
        "payment_id": payment.id,
        "payment_status": payment.status.value,
    }
