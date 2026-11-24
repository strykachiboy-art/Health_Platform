from flask import Blueprint, current_app, g, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.extensions import db
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.auth.user.models.user_model import User
from app.core.utils.decorators import role_required

from app.modules.clinic.schemas.clinic_schema import (
    ClinicAICreditsUpdateSchema,
    ClinicBranchConfigurationSchema,
    ClinicBranchCreateSchema,
    ClinicCreateSchema,
    ClinicStatusUpdateSchema,
    ClinicUpdateSchema,
)

from app.modules.clinic.services.clinic_operating_modes import operating_status
from app.modules.clinic.services.clinic_service import (
    add_ai_credits,
    change_status,
    create_branch,
    create_clinic,
    get_clinic,
    list_branches,
    list_clinics,
    regenerate_api_token,
    update_branch_configuration,
    update_clinic,
)


# ============================================================================
# BLUEPRINT
# ============================================================================

clinic_bp = Blueprint(
    "clinic",
    __name__,
    url_prefix="/clinics",
)


# ============================================================================
# ROLES
# ============================================================================

CLINIC_VIEW_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.PARAMEDIC,
    Role.EMT,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
)

# Root-clinic registry reads are system-visible to SUPER_ADMIN;
# branch listing keeps its existing role boundary.
CLINIC_ROOT_VIEW_ROLES = (
    Role.SUPER_ADMIN,
    *CLINIC_VIEW_ROLES,
)

CLINIC_MANAGEMENT_ROLES = (
    Role.ADMIN,
)


# ============================================================================
# SERIALIZATION
# ============================================================================

def _serialize_clinic(clinic):
    """
    Serialize a clinic without exposing sensitive internal fields.
    """

    record = {
        "id": clinic.id,
        "name": clinic.name,
        "clinic_type": clinic.clinic_type.value,
        "status": clinic.status.value,
        "parent_clinic_id": clinic.parent_clinic_id,
        "is_headquarters": clinic.is_headquarters,
        "address": clinic.address,
        "city": clinic.city,
        "country": clinic.country,
        "phone": clinic.phone,
        "email": clinic.email,
        "timezone": clinic.timezone,
        "opening_time": (
            clinic.opening_time.isoformat()
            if clinic.opening_time
            else None
        ),
        "closing_time": (
            clinic.closing_time.isoformat()
            if clinic.closing_time
            else None
        ),
        "ai_credits": clinic.ai_credits,
        "ai_requests_this_month": clinic.ai_requests_this_month,
        "created_at": (
            clinic.created_at.isoformat()
            if clinic.created_at
            else None
        ),
        "updated_at": (
            clinic.updated_at.isoformat()
            if clinic.updated_at
            else None
        ),
    }
    if current_app.config.get("CLINIC_EXTENDED_OPERATING_HOURS_ENABLED", False) is True:
        record.update(operating_status(
            clinic.opening_time, clinic.closing_time, clinic.timezone,
        ))
    return record


# ============================================================================
# AUTHORIZATION HELPERS
# ============================================================================

def _is_system_clinic_reader():
    role = getattr(g, "current_user_role", None)

    if isinstance(role, Role):
        return role in (Role.ADMIN, Role.SUPER_ADMIN)

    return role in (Role.ADMIN.value, Role.SUPER_ADMIN.value)


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


def _get_authorized_clinic(clinic_id: int):
    """
    Enforce clinic tenant isolation.

    ADMIN and SUPER_ADMIN may read any clinic.
    Other authorized roles may read only their assigned clinic.
    """

    if clinic_id <= 0:
        return (
            jsonify(
                {
                    "error": "Invalid clinic ID",
                }
            ),
            400,
        )

    user = _get_current_user()

    if _is_system_clinic_reader():
        return None

    user_clinic_id = getattr(user, "clinic_id", None)

    if user_clinic_id is None:
        return (
            jsonify(
                {
                    "error": "User is not assigned to a clinic",
                }
            ),
            403,
        )

    if user_clinic_id != clinic_id:
        return (
            jsonify(
                {
                    "error": "You do not have access to this clinic",
                }
            ),
            403,
        )

    return None


def _get_authorized_user_clinic():
    """
    Return the authenticated user's clinic ID.

    ADMIN users may not have a clinic_id because they operate
    at the system level.
    """

    user = _get_current_user()

    clinic_id = getattr(user, "clinic_id", None)

    if clinic_id is None:
        return (
            jsonify(
                {
                    "error": "User is not assigned to a clinic",
                }
            ),
            403,
        )

    return clinic_id


# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def _sanitize_pydantic_errors(errors):
    """
    Convert Pydantic errors into JSON-safe dictionaries.
    """

    sanitized = []

    for error in errors:
        item = dict(error)

        if "ctx" in item and isinstance(item["ctx"], dict):
            item["ctx"] = {
                key: str(value)
                for key, value in item["ctx"].items()
            }

        sanitized.append(item)

    return sanitized


def _validate_json(schema):
    """
    Validate request JSON with the supplied Pydantic schema.
    """

    try:
        payload = schema.model_validate(
            request.get_json(silent=True) or {}
        )

        return payload, None

    except PydanticValidationError as exc:
        return (
            None,
            (
                jsonify(
                    {
                        "error": "Validation error",
                        "details": _sanitize_pydantic_errors(
                            exc.errors()
                        ),
                    }
                ),
                422,
            ),
        )


def _parse_status(raw_status=None):
    """
    Parse a clinic status from the query string.
    """

    if raw_status is None:
        raw_status = request.args.get("status")

    if raw_status is None:
        return None

    try:
        return ClinicStatus(raw_status)

    except ValueError:
        return (
            jsonify(
                {
                    "error": (
                        f"Invalid clinic status '{raw_status}'"
                    ),
                }
            ),
            400,
        )


# ============================================================================
# CLINICS
# ============================================================================

@clinic_bp.post("")
@role_required(Role.SUPER_ADMIN)
def create_clinic_route():
    payload, error = _validate_json(
        ClinicCreateSchema
    )

    if error:
        return error

    try:
        clinic = create_clinic(
            name=payload.name,
            clinic_type=payload.clinic_type,
            parent_clinic_id=payload.parent_clinic_id,
            is_headquarters=payload.is_headquarters,
            address=payload.address,
            city=payload.city,
            country=payload.country,
            phone=payload.phone,
            email=payload.email,
            timezone=payload.timezone,
            opening_time=payload.opening_time,
            closing_time=payload.closing_time,
            operating_mode=payload.operating_mode,
        )

        return jsonify(
            {
                "message": "Clinic created successfully",
                "data": _serialize_clinic(clinic),
            }
        ), 201

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


@clinic_bp.get("")
@role_required(*CLINIC_ROOT_VIEW_ROLES)
def list_clinics_route():
    parsed_status = _parse_status()

    if isinstance(parsed_status, tuple):
        return parsed_status

    try:
        if _is_system_clinic_reader():
            clinics = list_clinics(
                status=parsed_status,
            )

        else:
            clinic_id = _get_authorized_user_clinic()

            if isinstance(clinic_id, tuple):
                return clinic_id

            clinic = get_clinic(clinic_id)

            if (
                parsed_status is not None
                and clinic.status != parsed_status
            ):
                clinics = []
            else:
                clinics = [clinic]

        return jsonify(
            {
                "data": [
                    _serialize_clinic(clinic)
                    for clinic in clinics
                ],
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409


@clinic_bp.get("/<int:clinic_id>")
@role_required(*CLINIC_ROOT_VIEW_ROLES)
def get_clinic_route(clinic_id: int):
    authorization_error = _get_authorized_clinic(
        clinic_id
    )

    if authorization_error:
        return authorization_error

    try:
        clinic = get_clinic(clinic_id)

        return jsonify(
            {
                "data": _serialize_clinic(clinic),
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


# ============================================================================
# BRANCHES
# ============================================================================

@clinic_bp.get("/<int:clinic_id>/branches")
@role_required(*CLINIC_VIEW_ROLES)
def list_clinic_branches_route(clinic_id: int):
    authorization_error = _get_authorized_clinic(
        clinic_id
    )

    if authorization_error:
        return authorization_error

    try:
        branches = list_branches(
            clinic_id=clinic_id,
        )

        return jsonify(
            {
                "data": [
                    _serialize_clinic(branch)
                    for branch in branches
                ],
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


@clinic_bp.post("/<int:clinic_id>/branches")
@role_required(*CLINIC_MANAGEMENT_ROLES)
def create_clinic_branch_route(clinic_id: int):
    payload, error = _validate_json(
        ClinicBranchCreateSchema
    )

    if error:
        return error

    try:
        branch = create_branch(
            parent_clinic_id=clinic_id,
            name=payload.name,
            clinic_type=payload.clinic_type,
            address=payload.address,
            city=payload.city,
            country=payload.country,
            phone=payload.phone,
            email=payload.email,
            timezone=payload.timezone,
            opening_time=payload.opening_time,
            closing_time=payload.closing_time,
            operating_mode=payload.operating_mode,
        )

        return jsonify(
            {
                "message": (
                    "Clinic branch created successfully"
                ),
                "data": _serialize_clinic(branch),
            }
        ), 201

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


@clinic_bp.patch(
    "/<int:clinic_id>/branch-configuration"
)
@role_required(*CLINIC_MANAGEMENT_ROLES)
def update_clinic_branch_configuration_route(
    clinic_id: int,
):
    payload, error = _validate_json(
        ClinicBranchConfigurationSchema
    )

    if error:
        return error

    try:
        fields = payload.model_dump(
            exclude_unset=True,
        )

        clinic = update_branch_configuration(
            clinic_id=clinic_id,
            **fields,
        )

        return jsonify(
            {
                "message": (
                    "Clinic branch configuration "
                    "updated successfully"
                ),
                "data": _serialize_clinic(clinic),
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


# ============================================================================
# CLINIC UPDATE
# ============================================================================

@clinic_bp.patch("/<int:clinic_id>")
@role_required(*CLINIC_MANAGEMENT_ROLES)
def update_clinic_route(clinic_id: int):
    payload, error = _validate_json(
        ClinicUpdateSchema
    )

    if error:
        return error

    try:
        fields = payload.model_dump(
            exclude_unset=True,
        )

        clinic = update_clinic(
            clinic_id=clinic_id,
            **fields,
        )

        return jsonify(
            {
                "message": "Clinic updated successfully",
                "data": _serialize_clinic(clinic),
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


# ============================================================================
# STATUS
# ============================================================================

@clinic_bp.patch("/<int:clinic_id>/status")
@role_required(*CLINIC_MANAGEMENT_ROLES)
def update_clinic_status_route(clinic_id: int):
    payload, error = _validate_json(
        ClinicStatusUpdateSchema
    )

    if error:
        return error

    try:
        clinic = change_status(
            clinic_id=clinic_id,
            new_status=payload.status,
        )

        return jsonify(
            {
                "message": "Clinic status updated successfully",
                "data": _serialize_clinic(clinic),
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


# ============================================================================
# AI CREDITS
# ============================================================================

@clinic_bp.patch("/<int:clinic_id>/ai-credits")
@role_required(*CLINIC_MANAGEMENT_ROLES)
def update_clinic_ai_credits_route(clinic_id: int):
    payload, error = _validate_json(
        ClinicAICreditsUpdateSchema
    )

    if error:
        return error

    try:
        clinic = add_ai_credits(
            clinic_id=clinic_id,
            amount=payload.amount,
        )

        return jsonify(
            {
                "message": (
                    "Clinic AI credits updated successfully"
                ),
                "data": _serialize_clinic(clinic),
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422


# ============================================================================
# API TOKEN
# ============================================================================

@clinic_bp.post(
    "/<int:clinic_id>/api-token/regenerate"
)
@role_required(*CLINIC_MANAGEMENT_ROLES)
def regenerate_clinic_api_token_route(
    clinic_id: int,
):
    try:
        token = regenerate_api_token(
            clinic_id=clinic_id,
        )

        return jsonify(
            {
                "message": (
                    "Clinic API token regenerated successfully"
                ),
                "data": {
                    "api_token": token,
                },
            }
        ), 200

    except NotFoundError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 404

    except ConflictError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 409

    except ValidationError as exc:
        return jsonify(
            {
                "error": str(exc),
            }
        ), 422