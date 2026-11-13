from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import DomainError, ValidationError
from app.modules.dashboard.schemas.dashboard_schema import (
    DashboardQuerySchema,
)
from app.modules.dashboard.services.dashboard_service import (
    get_dashboard,
)


dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    url_prefix="/dashboard",
)


def _current_user_id() -> int:
    identity = get_jwt_identity()

    if isinstance(identity, bool):
        raise ValidationError(
            "Invalid authentication identity"
        )

    try:
        user_id = int(identity)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid authentication identity"
        ) from exc

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    return user_id


def _validation_error_response(
    exc: PydanticValidationError,
):
    details = []

    for error in exc.errors():
        item = dict(error)

        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            ctx = dict(ctx)

            if "error" in ctx:
                ctx["error"] = str(ctx["error"])

            item["ctx"] = ctx

        details.append(item)

    return (
        jsonify(
            {
                "success": False,
                "error": "Validation failed",
                "details": details,
            }
        ),
        422,
    )


def _domain_error_response(
    exc: DomainError,
):
    return (
        jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ),
        exc.status_code,
    )


@dashboard_bp.get("")
@jwt_required()
def get_dashboard_route():
    try:
        actor_id = _current_user_id()

        query_payload = {
            key: request.args.get(key)
            for key in (
                "date_from",
                "date_to",
            )
        }

        query_payload = {
            key: value
            for key, value in query_payload.items()
            if value is not None
        }

        query = DashboardQuerySchema.model_validate(
            query_payload
        )

        result = get_dashboard(
            actor_id=actor_id,
            query=query,
        )

        return (
            jsonify(
                {
                    "success": True,
                    "data": result.model_dump(
                        mode="json"
                    ),
                }
            ),
            200,
        )

    except PydanticValidationError as exc:
        return _validation_error_response(exc)

    except DomainError as exc:
        return _domain_error_response(exc)

# CE1-S11: authenticated, clinic-/assignment-scoped, GET-only Surgery reads.
# Registered by the existing dashboard blueprint, never by a new registry.
from app.core.enums.role_enums import Role as _SurgeryViewRole
from app.core.utils.decorators import (
    active_staff_required as _surgery_active_staff,
    get_current_clinic_id as _surgery_clinic_id,
    role_required as _surgery_role_required,
)
from app.modules.surgery.services.surgical_dashboard_read_service import (
    get_surgery_dashboard as _surgery_dashboard,
    get_surgery_case_read_model as _surgery_case_read,
)


@dashboard_bp.get('/surgery')
@_surgery_role_required(
    _SurgeryViewRole.ADMIN, _SurgeryViewRole.SUPER_ADMIN,
    _SurgeryViewRole.DOCTOR, _SurgeryViewRole.NURSE,
)
@_surgery_active_staff
def get_surgery_documentation_dashboard_route():
    try:
        data = _surgery_dashboard(
            actor_user_id=_current_user_id(),
            clinic_id=_surgery_clinic_id(required=True),
            limit=request.args.get('limit', '20'),
            offset=request.args.get('offset', '0'),
        )
        return jsonify({'success': True, 'data': data}), 200
    except DomainError as exc:
        return _domain_error_response(exc)


@dashboard_bp.get('/surgery/cases/<int:surgical_case_id>')
@_surgery_role_required(
    _SurgeryViewRole.ADMIN, _SurgeryViewRole.SUPER_ADMIN,
    _SurgeryViewRole.DOCTOR, _SurgeryViewRole.NURSE,
)
@_surgery_active_staff
def get_surgery_case_documentation_read_route(surgical_case_id: int):
    try:
        data = _surgery_case_read(
            actor_user_id=_current_user_id(),
            clinic_id=_surgery_clinic_id(required=True),
            case_id=surgical_case_id,
        )
        return jsonify({'success': True, 'data': data}), 200
    except DomainError as exc:
        return _domain_error_response(exc)
