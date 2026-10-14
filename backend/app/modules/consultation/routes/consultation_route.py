from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.core.auth.user.models.user_model import User
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
from app.core.enums.role_enums import Role
from app.core.emergency_access.services.emergency_access_service import (
    assert_emergency_access,
)
from app.core.exceptions import ValidationError
from app.core.utils.decorators import (
    active_staff_required,
    emergency_aware_role_required,
    role_required,
)
from app.extensions import db

from app.modules.consultation.schemas.consultation_schema import (
    ConsultationCancelSchema,
    ConsultationCompleteSchema,
    ConsultationStartSchema,
    ConsultationTemplateCreateSchema,
    ConsultationUpdateSchema,
    PatientsSeenByStaffQuerySchema,
)

from app.modules.consultation.services.consultation_service import (
    cancel_consultation,
    complete_consultation,
    create_consultation_template,
    get_active_templates,
    get_consultation,
    get_consultations_for_patient,
    get_consultations_for_staff,
    get_patients_seen_by_staff,
    start_consultation,
    update_consultation_note,
)


consultation_bp = Blueprint(
    "consultation",
    __name__,
    url_prefix="/consultations",
)

CONSULTATION_READ_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

CONSULTATION_WRITE_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
)

CONSULTATION_TEMPLATE_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
)

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


def _payload(schema, data=None):
    try:
        payload_data = (
            request.get_json(silent=True)
            if data is None
            else data
        ) or {}

        return schema.model_validate(payload_data)

    except PydanticValidationError as exc:
        return jsonify({
            "success": False,
            "error": exc.errors(include_context=False),
        }), 422


def _get_current_user():
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(User, user_id)

    if user is None:
        raise ValidationError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _get_authenticated_clinic_id():
    user = _get_current_user()
    clinic_id = getattr(user, "clinic_id", None)

    if clinic_id is None:
        return None, (
            jsonify({
                "success": False,
                "error": (
                    "Authenticated user is not assigned "
                    "to a clinic"
                ),
            }),
            403,
        )

    return clinic_id, None


def _resolve_template_clinic_id():
    # Template creation requires an authenticated clinic scope.
    return _get_authenticated_clinic_id()


def _parse_positive_int(
    parameter_name: str,
    *,
    default: int | None = None,
    maximum: int | None = None,
):
    raw_value = request.args.get(parameter_name)

    if raw_value is None:
        return default

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": f"{parameter_name} must be an integer",
        }), 422

    if value <= 0:
        return jsonify({
            "success": False,
            "error": f"{parameter_name} must be greater than 0",
        }), 422

    if maximum is not None and value > maximum:
        return jsonify({
            "success": False,
            "error": (
                f"{parameter_name} must be less than "
                f"or equal to {maximum}"
            ),
        }), 422

    return value


def _pagination_params():
    page = _parse_positive_int(
        "page",
        default=DEFAULT_PAGE,
    )

    if not isinstance(page, int):
        return None, None, page

    per_page = _parse_positive_int(
        "per_page",
        default=DEFAULT_PER_PAGE,
        maximum=MAX_PER_PAGE,
    )

    if not isinstance(per_page, int):
        return None, None, per_page

    try:
        payload = PatientsSeenByStaffQuerySchema(
            page=page,
            per_page=per_page,
        )
    except PydanticValidationError as exc:
        return None, None, (
            jsonify({
                "success": False,
                "error": exc.errors(
                    include_context=False
                ),
            }),
            422,
        )

    return (
        payload.page,
        payload.per_page,
        None,
    )


def _query_status():
    raw_status = request.args.get("status")

    if raw_status is None:
        return None

    try:
        return ConsultationStatus(raw_status)

    except ValueError:
        return jsonify({
            "success": False,
            "error": (
                "Invalid consultation status. "
                "Allowed values: "
                "in_progress, completed, cancelled"
            ),
        }), 422


def _query_consultation_type():
    raw_type = request.args.get("consultation_type")

    if raw_type is None:
        return None

    try:
        return ConsultationType(raw_type)

    except ValueError:
        return jsonify({
            "success": False,
            "error": (
                "Invalid consultation type. "
                "Allowed values: "
                "general, follow_up, "
                "specialist, emergency"
            ),
        }), 422


def _parse_optional_clinic_id():
    raw_clinic_id = request.args.get("clinic_id")

    if raw_clinic_id is None:
        return None

    try:
        clinic_id = int(raw_clinic_id)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "clinic_id must be an integer",
        }), 422

    if clinic_id <= 0:
        return jsonify({
            "success": False,
            "error": "clinic_id must be greater than 0",
        }), 422

    return clinic_id


def _serialize_consultation(consultation):
    return {
        "id": consultation.id,
        "clinic_id": consultation.clinic_id,
        "patient_id": consultation.patient_id,
        "staff_id": consultation.staff_id,
        "appointment_id": consultation.appointment_id,
        "icd10_code": consultation.icd10_code,
        "consultation_type": consultation.consultation_type.value,
        "status": consultation.status.value,
        "chief_complaint": consultation.chief_complaint,
        "symptoms": consultation.symptoms,
        "diagnosis": consultation.diagnosis,
        "treatment_plan": consultation.treatment_plan,
        "notes": consultation.notes,
        "voice_note_url": consultation.voice_note_url,
        "transcribed_text": consultation.transcribed_text,
        "template_id": consultation.template_id,
        "started_at": (
            consultation.started_at.isoformat()
            if consultation.started_at
            else None
        ),
        "ended_at": (
            consultation.ended_at.isoformat()
            if consultation.ended_at
            else None
        ),
        "created_at": (
            consultation.created_at.isoformat()
            if consultation.created_at
            else None
        ),
        "updated_at": (
            consultation.updated_at.isoformat()
            if consultation.updated_at
            else None
        ),
    }


def _serialize_patient_summary(patient):
    return {
        "id": patient.id,
        "patient_number": patient.patient_number,
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "gender": (
            patient.gender.value
            if patient.gender
            else None
        ),
        "date_of_birth": (
            patient.date_of_birth.isoformat()
            if patient.date_of_birth
            else None
        ),
        "phone": patient.phone,
        "email": patient.email,
        "is_active": patient.is_active,
    }


def _serialize_template(template):
    return {
        "id": template.id,
        "clinic_id": template.clinic_id,
        "name": template.name,
        "specialty": template.specialty,
        "structure": template.structure,
        "is_active": template.is_active,
        "created_at": (
            template.created_at.isoformat()
            if template.created_at
            else None
        ),
    }


def _paginated_response(items, pagination, serializer):
    return jsonify({
        "success": True,
        "data": [
            serializer(item)
            for item in items
        ],
        "pagination": pagination,
    }), 200


@consultation_bp.post("/")
@role_required(*CONSULTATION_WRITE_ROLES)
@active_staff_required
def start():
    raw_payload = request.get_json(silent=True) or {}
    raw_payload.pop("clinic_id", None)

    payload = _payload(
        ConsultationStartSchema,
        raw_payload,
    )

    if isinstance(payload, tuple):
        return payload

    idempotency_key = request.headers.get("Idempotency-Key")
    idempotency_user_id = None

    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()

        if not idempotency_key:
            return jsonify({
                "success": False,
                "error": "Idempotency-Key cannot be blank",
            }), 400

        idempotency_user_id = _get_current_user().id

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultation = start_consultation(
        clinic_id=clinic_id,
        **payload.model_dump(),
        idempotency_key=idempotency_key,
        idempotency_user_id=idempotency_user_id,
    )

    return jsonify({
        "success": True,
        "data": _serialize_consultation(consultation),
    }), 201


@consultation_bp.get("/<int:consultation_id>")
@emergency_aware_role_required(*CONSULTATION_READ_ROLES)
@active_staff_required
def get(consultation_id: int):
    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
    )

    emergency_access_id_header = request.headers.get(
        "X-Emergency-Access-ID"
    )

    if emergency_access_id_header is not None:
        try:
            emergency_access_id = int(
                emergency_access_id_header.strip()
            )
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Emergency access is not authorized for this resource",
            }), 403

        if emergency_access_id <= 0:
            return jsonify({
                "success": False,
                "error": "Emergency access is not authorized for this resource",
            }), 403

        allowed = assert_emergency_access(
            actor_id=_get_current_user().id,
            patient_id=consultation.patient_id,
            resource_type="consultation",
            resource_id=consultation.id,
            action="read",
            emergency_access_id=emergency_access_id,
        )

        if not allowed:
            return jsonify({
                "success": False,
                "error": "Emergency access is not authorized for this resource",
            }), 403

    return jsonify({
        "success": True,
        "data": _serialize_consultation(consultation),
    }), 200


@consultation_bp.patch("/<int:consultation_id>")
@role_required(*CONSULTATION_WRITE_ROLES)
@active_staff_required
def update(consultation_id: int):
    raw_payload = request.get_json(silent=True) or {}

    payload = _payload(
        ConsultationUpdateSchema,
        raw_payload,
    )

    if isinstance(payload, tuple):
        return payload

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    fields = payload.model_dump(
        exclude_unset=True
    )

    consultation = update_consultation_note(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        **fields,
    )

    return jsonify({
        "success": True,
        "data": _serialize_consultation(consultation),
    }), 200


@consultation_bp.post("/<int:consultation_id>/complete")
@role_required(*CONSULTATION_WRITE_ROLES)
@active_staff_required
def complete(consultation_id: int):
    raw_payload = request.get_json(silent=True) or {}

    payload = _payload(
        ConsultationCompleteSchema,
        raw_payload,
    )

    if isinstance(payload, tuple):
        return payload

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultation = complete_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        **payload.model_dump(
            exclude_unset=True
        ),
    )

    return jsonify({
        "success": True,
        "data": _serialize_consultation(consultation),
    }), 200


@consultation_bp.post("/<int:consultation_id>/cancel")
@role_required(*CONSULTATION_WRITE_ROLES)
@active_staff_required
def cancel(consultation_id: int):
    payload = _payload(
        ConsultationCancelSchema
    )

    if isinstance(payload, tuple):
        return payload

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultation = cancel_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        **payload.model_dump(
            exclude_unset=True
        ),
    )

    return jsonify({
        "success": True,
        "data": _serialize_consultation(consultation),
    }), 200


@consultation_bp.get("/patient/<int:patient_id>")
@role_required(*CONSULTATION_READ_ROLES)
@active_staff_required
def patient_consultations(patient_id: int):
    page, per_page, error = _pagination_params()

    if error is not None:
        return error

    consultation_type = _query_consultation_type()

    if isinstance(consultation_type, tuple):
        return consultation_type

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultations, pagination = get_consultations_for_patient(
        patient_id=patient_id,
        clinic_id=clinic_id,
        consultation_type=consultation_type,
        page=page,
        per_page=per_page,
    )

    return _paginated_response(
        consultations,
        pagination,
        _serialize_consultation,
    )


@consultation_bp.get("/my-patients")
@role_required(*CONSULTATION_READ_ROLES)
@active_staff_required
def my_patients():
    user = _get_current_user()

    staff = getattr(user, "staff", None)

    if staff is None:
        return jsonify({
            "success": False,
            "error": (
                "Authenticated user is not linked "
                "to a staff record"
            ),
        }), 403

    page, per_page, error = _pagination_params()

    if error is not None:
        return error

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    if staff.clinic_id != clinic_id:
        return jsonify({
            "success": False,
            "error": "Access denied",
        }), 403

    patients, pagination = get_patients_seen_by_staff(
        staff_id=staff.id,
        clinic_id=clinic_id,
        page=page,
        per_page=per_page,
    )

    return jsonify({
        "success": True,
        "data": [
            _serialize_patient_summary(patient)
            for patient in patients
        ],
        "pagination": pagination,
    }), 200


@consultation_bp.get("/staff/<int:staff_id>")
@role_required(*CONSULTATION_READ_ROLES)
@active_staff_required
def staff_consultations(staff_id: int):
    status = _query_status()

    if isinstance(status, tuple):
        return status

    consultation_type = _query_consultation_type()

    if isinstance(consultation_type, tuple):
        return consultation_type

    page, per_page, error = _pagination_params()

    if error is not None:
        return error

    clinic_id, error = _get_authenticated_clinic_id()

    if error is not None:
        return error

    consultations, pagination = get_consultations_for_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
        status=status,
        consultation_type=consultation_type,
        page=page,
        per_page=per_page,
    )

    return _paginated_response(
        consultations,
        pagination,
        _serialize_consultation,
    )


@consultation_bp.post("/templates")
@role_required(*CONSULTATION_TEMPLATE_ROLES)
@active_staff_required
def create_template():
    payload = _payload(
        ConsultationTemplateCreateSchema
    )

    if isinstance(payload, tuple):
        return payload

    clinic_id, error = _resolve_template_clinic_id()

    if error is not None:
        return error

    template = create_consultation_template(
        clinic_id=clinic_id,
        **payload.model_dump(),
    )

    return jsonify({
        "success": True,
        "data": _serialize_template(template),
    }), 201


@consultation_bp.get("/templates")
@role_required(*CONSULTATION_READ_ROLES)
@active_staff_required
def active_templates():
    raw_clinic_id = _parse_optional_clinic_id()

    if isinstance(raw_clinic_id, tuple):
        return raw_clinic_id

    page, per_page, error = _pagination_params()

    if error is not None:
        return error

    authenticated_clinic_id, error = (
        _get_authenticated_clinic_id()
    )

    if error is not None:
        return error

    # A query parameter cannot broaden the authenticated clinic scope.
    if (
        raw_clinic_id is not None
        and raw_clinic_id != authenticated_clinic_id
    ):
        return jsonify({
            "success": False,
            "error": "Access denied",
        }), 403

    clinic_id = authenticated_clinic_id

    templates, pagination = get_active_templates(
        clinic_id=clinic_id,
        page=page,
        per_page=per_page,
    )

    return _paginated_response(
        templates,
        pagination,
        _serialize_template,
    )
