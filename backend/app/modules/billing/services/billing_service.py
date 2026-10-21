from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.extensions import db, celery

from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
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
from app.core.utils.decorators import transactional

from app.modules.appointment.models.appointment_model import (
    Appointment,
)
from app.modules.billing.models.billing_model import (
    Invoice,
    InvoiceItem,
    Payment,
)
from app.modules.clinic.models.clinic_model import (
    Clinic,
)
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)
from app.modules.patient.services.patient_service import (
    get_patient,
)


# ============================================================================
# Constants
# ============================================================================


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500

MAX_INSURANCE_PROVIDER_LENGTH = 120
MAX_INVOICE_ITEM_DESCRIPTION_LENGTH = 255
MAX_PAYMENT_REFERENCE_LENGTH = 120
MAX_GATEWAY_TRANSACTION_ID_LENGTH = 255


# ============================================================================
# Utilities
# ============================================================================


def _utcnow():
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value,
    field_name,
):
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )


def _validate_pagination(
    page,
    per_page,
):
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page <= 0
    ):
        raise ValidationError(
            "Per page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per page cannot exceed {MAX_PER_PAGE}"
        )


def _to_decimal(
    value,
    field_name="amount",
):
    try:
        amount = Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        raise ValidationError(
            f"Invalid {field_name}"
        )

    if not amount.is_finite():
        raise ValidationError(
            f"Invalid {field_name}"
        )

    if amount < Decimal("0"):
        raise ValidationError(
            f"{field_name} cannot be negative"
        )

    return amount


def _normalize_optional_string(
    value,
    field_name,
    max_length=None,
):
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if (
        max_length is not None
        and len(value) > max_length
    ):
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_currency(value):
    if not isinstance(value, str):
        raise ValidationError(
            "Currency must be explicitly specified"
        )

    value = value.strip().upper()

    if (
        len(value) != 3
        or not value.isascii()
        or not value.isalpha()
    ):
        raise ValidationError(
            "Currency must be a 3-letter ASCII code"
        )

    return value


def _validate_boolean(
    value,
    field_name,
):
    if not isinstance(value, bool):
        raise ValidationError(
            f"{field_name} must be a boolean"
        )


# ============================================================================
# Invoice Helpers
# ============================================================================


def _get_invoice(
    invoice_id,
    clinic_id=None,
    lock=False,
):
    _validate_positive_id(
        invoice_id,
        "Invoice ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    query = Invoice.query.filter(
        Invoice.id == invoice_id
    )

    if clinic_id is not None:
        query = query.filter(
            Invoice.clinic_id == clinic_id
        )

    if lock:
        query = query.with_for_update()

    invoice = query.first()

    if invoice is None:
        raise NotFoundError(
            f"Invoice {invoice_id} not found"
        )

    return invoice


def _lock_clinic(clinic_id):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    clinic = (
        Clinic.query
        .filter(Clinic.id == clinic_id)
        .with_for_update()
        .first()
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    return clinic


def _generate_invoice_number(
    clinic_id,
):
    today = date.today().strftime(
        "%Y%m%d"
    )

    unique_suffix = (
        uuid4().hex[:8].upper()
    )

    return (
        f"INV-{clinic_id}-"
        f"{today}-"
        f"{unique_suffix}"
    )


def _calculate_invoice_status(
    invoice,
):
    amount_paid = Decimal(
        invoice.amount_paid or 0
    )

    total_amount = Decimal(
        invoice.total_amount or 0
    )

    if amount_paid >= total_amount:
        return InvoiceStatus.PAID

    if amount_paid > Decimal("0"):
        return InvoiceStatus.PARTIALLY_PAID

    return InvoiceStatus.ISSUED


def _validate_invoice_items(
    items,
):
    if not isinstance(items, list) or not items:
        raise ValidationError(
            "Invoice must contain at least one item"
        )

    normalized_items = []

    for item in items:
        if not isinstance(item, dict):
            raise ValidationError(
                "Each invoice item must be an object"
            )

        description = item.get(
            "description"
        )

        if not isinstance(
            description,
            str,
        ):
            raise ValidationError(
                "Each invoice item requires "
                "a description"
            )

        description = description.strip()

        if not description:
            raise ValidationError(
                "Each invoice item requires "
                "a description"
            )

        if len(description) > (
            MAX_INVOICE_ITEM_DESCRIPTION_LENGTH
        ):
            raise ValidationError(
                "Invoice item description cannot "
                "exceed 255 characters"
            )

        quantity = item.get(
            "quantity",
            1,
        )

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ValidationError(
                "Invoice item quantity must be "
                "a positive integer"
            )

        unit_price = _to_decimal(
            item.get("unit_price"),
            "unit price",
        )

        normalized_items.append(
            {
                "description": description,
                "quantity": quantity,
                "unit_price": unit_price,
            }
        )

    return normalized_items


def _validate_invoice_relationships(
    clinic_id,
    patient_id,
    appointment_id=None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    if appointment_id is not None:
        _validate_positive_id(
            appointment_id,
            "Appointment ID",
        )

    patient = get_patient(
        patient_id
    )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            f"Patient {patient_id} does not belong "
            f"to clinic {clinic_id}"
        )

    appointment = None

    if appointment_id is not None:
        appointment = db.session.get(
            Appointment,
            appointment_id,
        )

        if appointment is None:
            raise NotFoundError(
                f"Appointment {appointment_id} not found"
            )

        if appointment.clinic_id != clinic_id:
            raise ValidationError(
                f"Appointment {appointment_id} does not "
                f"belong to clinic {clinic_id}"
            )

        if appointment.patient_id != patient_id:
            raise ValidationError(
                "Appointment does not belong "
                "to the specified patient"
            )

    return patient, appointment


# ============================================================================
# Create Invoice
# ============================================================================


@transactional
def create_invoice(
    clinic_id,
    patient_id,
    items: list[dict],
    appointment_id=None,
    actor_user_id=None,
    due_date=None,
    is_insurance_claim=False,
    insurance_provider=None,
    idempotency_key=None,
    idempotency_user_id=None,
    currency=None,
):
    currency = _normalize_currency(currency)
    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation="billing.invoice.create",
            idempotency_key=idempotency_key,
            request_payload={
                "patient_id": patient_id,
                "items": items,
                "appointment_id": appointment_id,
                "due_date": due_date,
                "is_insurance_claim": is_insurance_claim,
                "insurance_provider": insurance_provider,
                "currency": currency,
            },
        )

        if not created:
            if (
                idempotency_record.entity_type != "Invoice"
                or idempotency_record.entity_id is None
            ):
                raise ConflictError(
                    "Idempotency record does not reference a completed invoice"
                )

            existing_invoice = db.session.get(
                Invoice,
                idempotency_record.entity_id,
            )

            if existing_invoice is None:
                raise ConflictError(
                    "Idempotency record references a missing invoice"
                )

            if existing_invoice.clinic_id != clinic_id:
                raise ConflictError(
                    "Idempotency record references another clinic"
                )

            return existing_invoice

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    if appointment_id is not None:
        _validate_positive_id(
            appointment_id,
            "Appointment ID",
        )

    _validate_boolean(
        is_insurance_claim,
        "Insurance claim flag",
    )

    _lock_clinic(clinic_id)
    ensure_clinic_active(clinic_id)

    patient, appointment = (
        _validate_invoice_relationships(
            clinic_id=clinic_id,
            patient_id=patient_id,
            appointment_id=appointment_id,
        )
    )

    normalized_items = (
        _validate_invoice_items(items)
    )

    insurance_provider = (
        _normalize_optional_string(
            insurance_provider,
            "Insurance provider",
            max_length=(
                MAX_INSURANCE_PROVIDER_LENGTH
            ),
        )
    )

    if (
        is_insurance_claim
        and not insurance_provider
    ):
        raise ValidationError(
            "Insurance provider is required "
            "for an insurance claim"
        )

    if not is_insurance_claim:
        insurance_provider = None

    invoice = Invoice(
        clinic_id=clinic_id,
        patient_id=patient.id,
        appointment_id=(
            appointment.id
            if appointment is not None
            else None
        ),
        invoice_number=(
            _generate_invoice_number(
                clinic_id
            )
        ),
        currency=currency,
        status=InvoiceStatus.ISSUED,
        due_date=due_date,
        is_insurance_claim=(
            is_insurance_claim
        ),
        insurance_provider=(
            insurance_provider
        ),
        total_amount=Decimal("0"),
        amount_paid=Decimal("0"),
    )

    db.session.add(invoice)
    db.session.flush()

    total = Decimal("0")

    for item in normalized_items:
        subtotal = (
            item["unit_price"]
            * item["quantity"]
        )

        total += subtotal

        db.session.add(
            InvoiceItem(
                invoice_id=invoice.id,
                description=item["description"],
                quantity=item["quantity"],
                unit_price=item["unit_price"],
                subtotal=subtotal,
            )
        )

    invoice.total_amount = total
    invoice.status = (
        _calculate_invoice_status(
            invoice
        )
    )

    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Invoice",
        entity_id=invoice.id,
        clinic_id=clinic_id,
        user_id=actor_user_id,
        description=(
            f"Invoice {invoice.invoice_number} "
            f"created for patient {patient.id}"
        ),
        new_value={
            "clinic_id": clinic_id,
            "patient_id": patient.id,
            "appointment_id": (
                appointment.id
                if appointment is not None
                else None
            ),
            "total_amount": str(total),
            "currency": currency,
            "amount_paid": str(
                invoice.amount_paid
            ),
            "status": invoice.status.value,
            "is_insurance_claim": (
                is_insurance_claim
            ),
            "insurance_provider": (
                insurance_provider
            ),
        },
    )

    if idempotency_record is not None:
        idempotency_record.entity_type = "Invoice"
        idempotency_record.entity_id = invoice.id

    return invoice


# ============================================================================
# Payment Helpers
# ============================================================================


def _normalize_payment_method(
    method,
):
    if isinstance(
        method,
        PaymentMethod,
    ):
        return method

    try:
        return PaymentMethod(method)
    except (
        ValueError,
        TypeError,
    ):
        raise ValidationError(
            "Invalid payment method"
        )


def _normalize_payment_gateway(
    gateway,
):
    if gateway is None:
        return None

    if isinstance(
        gateway,
        PaymentGateway,
    ):
        return gateway

    try:
        return PaymentGateway(gateway)
    except (
        ValueError,
        TypeError,
    ):
        raise ValidationError(
            "Invalid payment gateway"
        )


def _validate_gateway_method(
    method,
    gateway,
):
    electronic_methods = {
        PaymentMethod.CARD,
        PaymentMethod.BANK_TRANSFER,
        PaymentMethod.MOBILE_MONEY,
    }

    if (
        gateway is not None
        and method not in electronic_methods
    ):
        raise ValidationError(
            "Payment gateway cannot be used "
            f"with payment method "
            f"'{method.value}'"
        )


def _find_duplicate_gateway_payment(
    invoice_id,
    gateway_transaction_id,
):
    if not gateway_transaction_id:
        return None

    return (
        Payment.query
        .filter(
            Payment.invoice_id == invoice_id,
            Payment.gateway_transaction_id
            == gateway_transaction_id,
            Payment.status
            == PaymentStatus.SUCCESSFUL,
        )
        .first()
    )


# ============================================================================
# Record Payment
# ============================================================================


@transactional
def record_payment(
    clinic_id,
    invoice_id,
    amount,
    method,
    reference=None,
    gateway=None,
    gateway_transaction_id=None,
    idempotency_key=None,
    idempotency_user_id=None,
):
    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation="billing.payment.record",
            idempotency_key=idempotency_key,
            request_payload={
                "invoice_id": invoice_id,
                "amount": amount,
                "method": method,
                "reference": reference,
                "gateway": gateway,
                "gateway_transaction_id": gateway_transaction_id,
            },
        )

        if not created:
            if (
                idempotency_record.entity_type != "Payment"
                or idempotency_record.entity_id is None
            ):
                raise ConflictError(
                    "Idempotency record does not reference a completed payment"
                )

            existing_payment = db.session.get(
                Payment,
                idempotency_record.entity_id,
            )

            if existing_payment is None:
                raise ConflictError(
                    "Idempotency record references a missing payment"
                )

            existing_invoice = db.session.get(
                Invoice,
                existing_payment.invoice_id,
            )

            if existing_invoice is None:
                raise ConflictError(
                    "Idempotency record references a payment "
                    "with a missing invoice"
                )

            if existing_invoice.clinic_id != clinic_id:
                raise ConflictError(
                    "Idempotency record references another clinic"
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

            if (
                _normalize_currency(existing_invoice.currency)
                != _normalize_currency(existing_payment.currency)
            ):
                raise ConflictError(
                    "Payment currency does not match the invoice currency"
                )

            return existing_payment

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
            "Invoice currency is unknown; cannot record payment"
        )

    currency = _normalize_currency(invoice.currency)

    if (
        invoice.status
        == InvoiceStatus.CANCELLED
    ):
        raise ConflictError(
            "Cannot record payment for "
            f"cancelled invoice "
            f"{invoice.invoice_number}"
        )

    if (
        invoice.status
        == InvoiceStatus.PAID
    ):
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
            "Payment amount must be greater "
            "than zero"
        )

    method = _normalize_payment_method(
        method
    )

    gateway = _normalize_payment_gateway(
        gateway
    )

    _validate_gateway_method(
        method,
        gateway,
    )

    reference = _normalize_optional_string(
        reference,
        "Payment reference",
        max_length=(
            MAX_PAYMENT_REFERENCE_LENGTH
        ),
    )

    gateway_transaction_id = (
        _normalize_optional_string(
            gateway_transaction_id,
            "Gateway transaction ID",
            max_length=(
                MAX_GATEWAY_TRANSACTION_ID_LENGTH
            ),
        )
    )

    if (
        gateway is not None
        and not gateway_transaction_id
    ):
        raise ValidationError(
            "Gateway transaction ID is required "
            "for gateway payments"
        )

    duplicate_payment = (
        _find_duplicate_gateway_payment(
            invoice.id,
            gateway_transaction_id,
        )
    )

    if duplicate_payment is not None:
        raise ConflictError(
            "A successful payment with this "
            "gateway transaction ID has already "
            "been recorded"
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

    old_status = invoice.status.value
    old_amount_paid = amount_paid

    payment = Payment(
        invoice_id=invoice.id,
        amount=amount,
        currency=currency,
        method=method,
        status=PaymentStatus.SUCCESSFUL,
        gateway=gateway,
        reference=reference,
        gateway_transaction_id=(
            gateway_transaction_id
        ),
    )

    payment.paid_at = _utcnow()

    db.session.add(payment)

    invoice.amount_paid = (
        amount_paid + amount
    )

    invoice.status = (
        _calculate_invoice_status(
            invoice
        )
    )

    db.session.flush()

    create_audit_log(
        action=AuditAction.PAYMENT,
        entity_type="Invoice",
        entity_id=invoice.id,
        clinic_id=clinic_id,
        description=(
            f"Payment of {amount} recorded "
            f"via {method.value}"
        ),
        old_value={
            "status": old_status,
            "amount_paid": str(
                old_amount_paid
            ),
        },
        new_value={
            "amount_paid": str(
                invoice.amount_paid
            ),
            "currency": currency,
            "status": invoice.status.value,
            "payment_method": method.value,
            "payment_gateway": (
                gateway.value
                if gateway
                else None
            ),
            "gateway_transaction_id": (
                gateway_transaction_id
            ),
        },
    )

    if idempotency_record is not None:
        idempotency_record.entity_type = "Payment"
        idempotency_record.entity_id = payment.id

    return payment


# ============================================================================
# Outstanding Invoices
# ============================================================================


def get_outstanding_invoices(
    clinic_id=None,
    page=DEFAULT_PAGE,
    per_page=DEFAULT_PER_PAGE,
):
    _validate_pagination(
        page,
        per_page,
    )

    query = Invoice.query.filter(
        Invoice.status.in_(
            [
                InvoiceStatus.ISSUED,
                InvoiceStatus.PARTIALLY_PAID,
                InvoiceStatus.OVERDUE,
            ]
        )
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

        query = query.filter(
            Invoice.clinic_id == clinic_id
        )

    return (
        query
        .order_by(
            Invoice.due_date.asc(),
            Invoice.created_at.asc(),
            Invoice.id.asc(),
        )
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
    )


# ============================================================================
# Overdue Invoice Processing
# ============================================================================


def _mark_overdue_invoices(
    clinic_id=None,
):
    today = date.today()

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    query = Invoice.query.filter(
        Invoice.due_date.isnot(None),
        Invoice.due_date < today,
        Invoice.status.in_(
            [
                InvoiceStatus.ISSUED,
                InvoiceStatus.PARTIALLY_PAID,
            ]
        ),
    )

    if clinic_id is not None:
        query = query.filter(
            Invoice.clinic_id == clinic_id
        )

    overdue = query.all()

    for invoice in overdue:
        old_status = invoice.status.value

        invoice.status = (
            InvoiceStatus.OVERDUE
        )

        audit_clinic_id = (
            clinic_id
            if clinic_id is not None
            else getattr(
                invoice,
                "clinic_id",
                None,
            )
        )

        create_audit_log(
            action=AuditAction.STATUS_CHANGE,
            entity_type="Invoice",
            entity_id=invoice.id,
            clinic_id=audit_clinic_id,
            description=(
                "Invoice marked overdue"
                + (
                    " (automated)"
                    if clinic_id is None
                    else ""
                )
            ),
            old_value={
                "status": old_status,
            },
            new_value={
                "status": (
                    InvoiceStatus.OVERDUE.value
                ),
            },
        )

    return len(overdue)


@transactional
def mark_overdue_invoices(
    clinic_id=None,
):
    if clinic_id is None:
        raise ValidationError(
            "Clinic ID is required"
        )

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    ensure_clinic_active(clinic_id)

    return _mark_overdue_invoices(
        clinic_id=clinic_id
    )


@celery.task(name="mark_overdue_invoices")
def mark_overdue_invoices_task():
    try:
        updated_count = (
            _mark_overdue_invoices()
        )

        db.session.commit()

        return updated_count

    except Exception:
        db.session.rollback()
        raise
