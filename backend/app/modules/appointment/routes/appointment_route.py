from __future__ import annotations

from flask import Blueprint, jsonify, request

from flask_jwt_extended import get_jwt_identity

from pydantic import ValidationError as PydanticValidationError

from app.extensions import db

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import DomainError, ValidationError
from app.core.utils.decorators import active_staff_required, get_current_clinic_id, role_required

from app.modules.appointment.schemas.appointment_schema import (
    AppointmentCancelSchema,
    AppointmentCompleteSchema,
    AppointmentCreateSchema,
    AppointmentRescheduleSchema,
    AppointmentStaffScheduleQuerySchema,
)

from app.modules.appointment.services.appointment_service import (
    cancel_appointment,
    complete_appointment,
    confirm_appointment,
    create_appointment,
    get_appointments_for_patient,
    get_appointments_for_staff,
    mark_no_show,
    reschedule_appointment,
)


appointment_bp = Blueprint(
    "appointment",
    __name__,
    url_prefix="/appointments",
)


APPOINTMENT_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.RECEPTIONIST,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


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


def _current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _payload(schema):
    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Request body must be a JSON object",
                }
            ),
            422,
        )

    try:
        return schema.model_validate(payload)

    except PydanticValidationError as exc:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid request payload",
                    "details": exc.errors(),
                }
            ),
            422,
        )


def _query_payload(schema_class):
    try:
        data = request.args.to_dict()

        if "page" in data:
            data["page"] = int(data["page"])

        if "per_page" in data:
            data["per_page"] = int(data["per_page"])

        return schema_class.model_validate(data)

    except (PydanticValidationError, ValueError, TypeError):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid query parameters",
                }
            ),
            422,
        )


def _get_int_query_param(
    name: str,
    *,
    default: int,
) -> int:
    raw_value = request.args.get(name)

    if raw_value is None:
        return default

    raw_value = raw_value.strip()

    if not raw_value:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    if value <= 0:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    return value


def _pagination_params():
    page = _get_int_query_param(
        "page",
        default=DEFAULT_PAGE,
    )

    per_page = _get_int_query_param(
        "per_page",
        default=DEFAULT_PER_PAGE,
    )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _serialize_appointment(appointment):
    return {
        "id": appointment.id,
        "clinic_id": appointment.clinic_id,
        "patient_id": appointment.patient_id,
        "staff_id": appointment.staff_id,
        "scheduled_start": (
            appointment.scheduled_start.isoformat()
        ),
        "scheduled_end": (
            appointment.scheduled_end.isoformat()
        ),
        "status": appointment.status.value,
        "appointment_type": (
            appointment.appointment_type.value
        ),
        "reason": appointment.reason,
        "notes": appointment.notes,
        "google_calendar_event_id": (
            appointment.google_calendar_event_id
        ),
        "reminder_sent": appointment.reminder_sent,
        "created_at": (
            appointment.created_at.isoformat()
            if appointment.created_at
            else None
        ),
        "updated_at": (
            appointment.updated_at.isoformat()
            if appointment.updated_at
            else None
        ),
        "cancelled_at": (
            appointment.cancelled_at.isoformat()
            if appointment.cancelled_at
            else None
        ),
        "cancellation_reason": (
            appointment.cancellation_reason
        ),
    }


def _serialize_page(pagination):
    return {
        "items": [
            _serialize_appointment(item)
            for item in pagination.items
        ],
        "total": pagination.total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "pages": pagination.pages,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
    }


# ============================================================================
# CREATE
# ============================================================================


@appointment_bp.post("/")
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def create():
    payload = _payload(
        AppointmentCreateSchema
    )

    if isinstance(payload, tuple):
        return payload

    idempotency_key = request.headers.get("Idempotency-Key")
    idempotency_user_id = None

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()

        if not idempotency_key:
            return jsonify(
                {
                    "success": False,
                    "error": "Idempotency-Key cannot be blank",
                }
            ), 400

        idempotency_user_id = _current_user().id

    try:
        clinic_id = _current_clinic_id()

        appointment_kwargs = {
            "clinic_id": clinic_id,
            "patient_id": payload.patient_id,
            "staff_id": payload.staff_id,
            "scheduled_start": payload.scheduled_start,
            "scheduled_end": payload.scheduled_end,
            "appointment_type": payload.appointment_type,
            "reason": payload.reason,
            "notes": payload.notes,
        }

        if idempotency_key is not None:
            appointment_kwargs.update(
                {
                    "idempotency_key": idempotency_key,
                    "idempotency_user_id": idempotency_user_id,
                }
            )

        appointment = create_appointment(
            **appointment_kwargs
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 201

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# RESCHEDULE
# ============================================================================


@appointment_bp.post(
    "/<int:appointment_id>/reschedule"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def reschedule(
    appointment_id: int,
):
    payload = _payload(
        AppointmentRescheduleSchema
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        appointment = reschedule_appointment(
            appointment_id=appointment_id,
            clinic_id=clinic_id,
            new_start=payload.scheduled_start,
            new_end=payload.scheduled_end,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# CONFIRM
# ============================================================================


@appointment_bp.post(
    "/<int:appointment_id>/confirm"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def confirm(
    appointment_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        appointment = confirm_appointment(
            appointment_id=appointment_id,
            clinic_id=clinic_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# CANCEL
# ============================================================================


@appointment_bp.post(
    "/<int:appointment_id>/cancel"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def cancel(
    appointment_id: int,
):
    payload = _payload(
        AppointmentCancelSchema
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        appointment = cancel_appointment(
            appointment_id=appointment_id,
            clinic_id=clinic_id,
            reason=payload.cancellation_reason,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# COMPLETE
# ============================================================================


@appointment_bp.post(
    "/<int:appointment_id>/complete"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def complete(
    appointment_id: int,
):
    payload = _payload(
        AppointmentCompleteSchema
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        appointment = complete_appointment(
            appointment_id=appointment_id,
            clinic_id=clinic_id,
            notes=payload.notes,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# NO-SHOW
# ============================================================================


@appointment_bp.post(
    "/<int:appointment_id>/no-show"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def no_show(
    appointment_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        appointment = mark_no_show(
            appointment_id=appointment_id,
            clinic_id=clinic_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_appointment(
                    appointment
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# PATIENT APPOINTMENT HISTORY
# ============================================================================


@appointment_bp.get(
    "/patient/<int:patient_id>"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def patient_appointments(
    patient_id: int,
):
    try:
        clinic_id = _current_clinic_id()

        page, per_page = _pagination_params()

        pagination = get_appointments_for_patient(
            patient_id=patient_id,
            clinic_id=clinic_id,
            page=page,
            per_page=per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_page(
                    pagination
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code


# ============================================================================
# STAFF APPOINTMENT SCHEDULE
# ============================================================================


@appointment_bp.get(
    "/staff/<int:staff_id>"
)
@role_required(*APPOINTMENT_ROLES)
@active_staff_required
def staff_appointments(
    staff_id: int,
):
    payload = _query_payload(
        AppointmentStaffScheduleQuerySchema
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()

        page, per_page = _pagination_params()

        pagination = get_appointments_for_staff(
            staff_id=staff_id,
            clinic_id=clinic_id,
            date_=payload.date_,
            page=page,
            per_page=per_page,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_page(
                    pagination
                ),
            }
        ), 200

    except DomainError as exc:
        body = {
            "success": False,
            "error": str(exc),
        }
        if exc.code is not None:
            body["code"] = exc.code
        return jsonify(body), exc.status_code