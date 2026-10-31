from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.core.auth.user.models.user_model import User
from app.core.audit.filters.audit_filters import (
    split_audit_filters,
)
from app.core.audit.queries.audit_queries import (
    get_audit_log,
    list_audit_logs,
)
from app.core.audit.schema.audit_filter import (
    AuditLogFilterSchema,
)
from app.core.audit.security.audit_permissions import (
    can_read_audit_logs,
)
from app.core.audit.serialization.audit_serializer import (
    serialize_audit_log,
    serialize_audit_page,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import role_required
from app.extensions import db


audit_bp = Blueprint(
    "audit",
    __name__,
    url_prefix="/audit-logs",
)


AUDIT_READ_ROLES = (
    Role.ADMIN,
    Role.SUPER_ADMIN,
)


def _get_current_user() -> User:
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


def _coerce_query_parameters() -> dict:
    params = request.args.to_dict(
        flat=True
    )

    for field_name in (
        "user_id",
        "patient_id",
        "entity_id",
        "page",
        "per_page",
    ):
        if field_name not in params:
            continue

        raw_value = params[field_name]

        try:
            params[field_name] = int(
                raw_value
            )
        except (TypeError, ValueError):
            pass

    return params


def _get_filters() -> AuditLogFilterSchema:
    return AuditLogFilterSchema.model_validate(
        _coerce_query_parameters()
    )


def _authorize_audit_reader(user: User) -> None:
    if not can_read_audit_logs(
        actor_role=user.role,
        actor_clinic_id=user.clinic_id,
        target_clinic_id=user.clinic_id,
    ):
        raise ValidationError(
            "Insufficient audit permissions"
        )

    if (
        user.role is Role.ADMIN
        and user.clinic_id is None
    ):
        raise ValidationError(
            "Audit administrator must belong to a clinic"
        )


@audit_bp.get("")
@role_required(*AUDIT_READ_ROLES)
def get_audit_logs():
    user = _get_current_user()

    _authorize_audit_reader(user)

    filters = _get_filters()

    query_filters, pagination = split_audit_filters(
        filters
    )

    result = list_audit_logs(
        actor_role=user.role,
        actor_clinic_id=user.clinic_id,
        **query_filters,
        **pagination,
    )

    return jsonify(
        {
            "success": True,
            "data": serialize_audit_page(
                result
            ),
        }
    ), 200


@audit_bp.get("/<int:log_id>")
@role_required(*AUDIT_READ_ROLES)
def get_single_audit_log(log_id: int):
    user = _get_current_user()

    _authorize_audit_reader(user)

    audit_log = get_audit_log(
        audit_log_id=log_id,
        actor_role=user.role,
        actor_clinic_id=user.clinic_id,
    )

    if audit_log is None:
        raise NotFoundError(
            "Audit log not found"
        )

    return jsonify(
        {
            "success": True,
            "data": serialize_audit_log(
                audit_log
            ),
        }
    ), 200