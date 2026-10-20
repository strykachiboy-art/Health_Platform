from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.extensions import db, limiter

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    DomainError,
    ValidationError,
)
from app.core.utils.decorators import get_current_clinic_id, role_required

from app.modules.billing.schemas.billing_schema import (
    CreateInvoiceRequest,
    InitializePaymentRequest,
    InvoiceResponse,
    OutstandingInvoiceQuery,
    OutstandingInvoiceResponse,
    PaymentResponse,
    RecordPaymentRequest,
)

from app.modules.billing.services.billing_service import (
    create_invoice,
    get_outstanding_invoices,
    mark_overdue_invoices,
    record_payment,
)
from app.modules.billing.services.payment_orchestration_service import (
    orchestrate_payment,
)

from app.modules.billing.services.payment_webhook_service import (
    process_payment_webhook,
)


billing_bp = Blueprint(
    "billing",
    __name__,
    url_prefix="/billing",
)


# ============================================================================
# Authentication / Clinic Helpers
# ============================================================================


def _current_user():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise ValidationError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _current_clinic_id():
    return get_current_clinic_id(required=True)


# ============================================================================
# Validation Helpers
# ============================================================================


def _sanitize_pydantic_errors(errors):
    sanitized = []

    for error in errors:
        item = dict(error)

        if (
            "ctx" in item
            and isinstance(item["ctx"], dict)
        ):
            item["ctx"] = {
                key: str(value)
                for key, value in item["ctx"].items()
            }

        sanitized.append(item)

    return sanitized


def _payload(schema):
    raw = request.get_json(
        silent=True
    )

    if raw is None:
        raw = {}

    if not isinstance(raw, dict):
        return (
            None,
            (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Request body must be "
                            "a JSON object"
                        ),
                    }
                ),
                422,
            ),
        )

    try:
        payload = schema.model_validate(
            raw
        )

        return payload, None

    except PydanticValidationError:
        return (
            None,
            (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Invalid request payload"
                        ),
                    }
                ),
                422,
            ),
        )


def _query_payload(schema):
    data = request.args.to_dict()

    try:
        if "page" in data:
            data["page"] = int(
                data["page"]
            )

        if "per_page" in data:
            data["per_page"] = int(
                data["per_page"]
            )

    except (TypeError, ValueError):
        return (
            None,
            (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Invalid query parameters"
                        ),
                    }
                ),
                422,
            ),
        )

    try:
        payload = schema.model_validate(
            data
        )

        return payload, None

    except PydanticValidationError:
        return (
            None,
            (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Invalid query parameters"
                        ),
                    }
                ),
                422,
            ),
        )


def _serialize_response(
    schema,
    value,
):
    return (
        schema.model_validate(
            value
        ).model_dump(
            mode="json"
        )
    )


def _serialize_page(
    page,
    schema,
):
    return {
        "items": [
            _serialize_response(
                schema,
                item,
            )
            for item in page.items
        ],
        "page": page.page,
        "per_page": page.per_page,
        "total": page.total,
        "pages": page.pages,
        "has_next": page.has_next,
        "has_prev": page.has_prev,
    }


def _domain_error_response(exc):
    return (
        jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ),
        exc.status_code,
    )


def _read_idempotency_key():
    raw = request.headers.get(
        "Idempotency-Key"
    )

    if raw is None:
        return None, None

    key = raw.strip()

    if not key:
        return (
            None,
            (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Idempotency-Key cannot be blank"
                        ),
                    }
                ),
                400,
            ),
        )

    return key, None


# ============================================================================
# Invoice Creation
# ============================================================================


@billing_bp.route(
    "/invoices",
    methods=["POST"],
)
@role_required(Role.ADMIN)
def create_invoice_route():
    payload, error = _payload(
        CreateInvoiceRequest
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        idempotency_key, idempotency_error = (
            _read_idempotency_key()
        )

        if idempotency_error:
            return idempotency_error

        if idempotency_key is None:
            invoice = create_invoice(
                clinic_id=clinic_id,
                patient_id=payload.patient_id,
                actor_user_id=_current_user().id,
                appointment_id=payload.appointment_id,
                due_date=payload.due_date,
                is_insurance_claim=(
                    payload.is_insurance_claim
                ),
                insurance_provider=(
                    payload.insurance_provider
                ),
                items=[
                    item.model_dump()
                    for item in payload.items
                ],
            )

        else:
            current_user = _current_user()

            invoice = create_invoice(
                clinic_id=clinic_id,
                patient_id=payload.patient_id,
                actor_user_id=current_user.id,
                appointment_id=payload.appointment_id,
                due_date=payload.due_date,
                is_insurance_claim=(
                    payload.is_insurance_claim
                ),
                insurance_provider=(
                    payload.insurance_provider
                ),
                items=[
                    item.model_dump()
                    for item in payload.items
                ],
                idempotency_key=idempotency_key,
                idempotency_user_id=current_user.id,
            )

        return (
            jsonify(
                {
                    "success": True,
                    "data": _serialize_response(
                        InvoiceResponse,
                        invoice,
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


# ============================================================================
# Outstanding Invoices
# ============================================================================


@billing_bp.route(
    "/invoices/outstanding",
    methods=["GET"],
)
@role_required(Role.ADMIN)
def get_outstanding_invoices_route():
    payload, error = _query_payload(
        OutstandingInvoiceQuery
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        invoices = get_outstanding_invoices(
            clinic_id=clinic_id,
            page=payload.page,
            per_page=payload.per_page,
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": _serialize_page(
                        invoices,
                        OutstandingInvoiceResponse,
                    ),
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


# ============================================================================
# Payment Orchestration
# ============================================================================


@billing_bp.route(
    "/payments/initialize",
    methods=["POST"],
)
@role_required(Role.ADMIN)
def initialize_payment_route():
    payload, error = _payload(
        InitializePaymentRequest
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        idempotency_key, idempotency_error = (
            _read_idempotency_key()
        )

        if idempotency_error:
            return idempotency_error

        if idempotency_key is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            "Idempotency-Key is required "
                            "for gateway payment "
                            "orchestration"
                        ),
                    }
                ),
                400,
            )

        current_user = _current_user()

        result = orchestrate_payment(
            clinic_id=clinic_id,
            invoice_id=payload.invoice_id,
            amount=payload.amount,
            method=payload.method,
            gateway=payload.gateway,
            currency=payload.currency,
            reference=payload.reference,
            idempotency_key=idempotency_key,
            idempotency_user_id=current_user.id,
            actor_user_id=current_user.id,
        )

        payment = result["payment"]
        outcome = result["outcome"]

        data = {
            "payment": _serialize_response(
                PaymentResponse,
                payment,
            ),
            "outcome": outcome,
        }

        if outcome == "failed":
            return (
                jsonify(
                    {
                        "success": False,
                        "error": (
                            payment.failure_reason
                            or "Payment failed"
                        ),
                        "data": data,
                    }
                ),
                422,
            )

        if outcome == "pending":
            return (
                jsonify(
                    {
                        "success": True,
                        "data": data,
                    }
                ),
                202,
            )

        return (
            jsonify(
                {
                    "success": True,
                    "data": data,
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


# ============================================================================
# ============================================================================
# Payment Provider Webhooks
# ============================================================================


@billing_bp.route(
    "/webhooks/<int:clinic_id>/<gateway>",
    methods=["POST"],
)
@limiter.limit(
    lambda: current_app.config.get(
        "PAYMENT_WEBHOOK_RATE_LIMIT",
        "300 per minute",
    )
)
def payment_webhook_route(
    clinic_id,
    gateway,
):
    payload = request.get_data(
        cache=True,
    )

    try:
        result = process_payment_webhook(
            clinic_id=clinic_id,
            gateway=gateway,
            payload=payload,
            headers=dict(request.headers),
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": result,
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)

# Payment Recording
# ============================================================================


@billing_bp.route(
    "/payments",
    methods=["POST"],
)
@role_required(Role.ADMIN)
def record_payment_route():
    payload, error = _payload(
        RecordPaymentRequest
    )

    if error:
        return error

    try:
        clinic_id = _current_clinic_id()

        idempotency_key, idempotency_error = (
            _read_idempotency_key()
        )

        if idempotency_error:
            return idempotency_error

        if idempotency_key is None:
            payment = record_payment(
                clinic_id=clinic_id,
                invoice_id=payload.invoice_id,
                amount=payload.amount,
                method=payload.method,
                gateway=payload.gateway,
                reference=payload.reference,
                gateway_transaction_id=(
                    payload.gateway_transaction_id
                ),
            )

        else:
            current_user = _current_user()

            payment = record_payment(
                clinic_id=clinic_id,
                invoice_id=payload.invoice_id,
                amount=payload.amount,
                method=payload.method,
                gateway=payload.gateway,
                reference=payload.reference,
                gateway_transaction_id=(
                    payload.gateway_transaction_id
                ),
                idempotency_key=idempotency_key,
                idempotency_user_id=current_user.id,
            )

        return (
            jsonify(
                {
                    "success": True,
                    "data": _serialize_response(
                        PaymentResponse,
                        payment,
                    ),
                }
            ),
            201,
        )

    except DomainError as exc:
        return _domain_error_response(exc)


# ============================================================================
# Mark Overdue Invoices
# ============================================================================


@billing_bp.route(
    "/invoices/mark-overdue",
    methods=["POST"],
)
@role_required(Role.ADMIN)
def mark_overdue_invoices_route():
    try:
        clinic_id = _current_clinic_id()

        updated_count = mark_overdue_invoices(
            clinic_id=clinic_id
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": {
                        "updated_count": updated_count,
                    },
                }
            ),
            200,
        )

    except DomainError as exc:
        return _domain_error_response(exc)
