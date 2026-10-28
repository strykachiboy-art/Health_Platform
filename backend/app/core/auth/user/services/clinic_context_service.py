from __future__ import annotations

from sqlalchemy.orm import joinedload

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.core.auth.user.models.user_model import User
from app.modules.clinic.models.clinic_model import Clinic


CLINIC_CONTEXT_CLAIM = "clinic_context_id"


def _get_user(
    user_id: int,
) -> User:
    user = db.session.get(
        User,
        user_id,
        options=(
            joinedload(User.staff),
        ),
    )

    if user is None:
        raise NotFoundError(
            f"User {user_id} not found"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    return user


def _get_active_clinic(
    clinic_id: int,
) -> Clinic:
    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "Clinic ID must be a positive integer"
        )

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    if clinic.status is not ClinicStatus.ACTIVE:
        raise ValidationError(
            "Selected clinic is not active"
        )

    return clinic


def _resolve_assigned_clinic_id(
    clinic_id: int | None,
) -> int | None:
    if clinic_id is None:
        return None

    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "Invalid clinic context"
        )

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    if clinic.status is not ClinicStatus.ACTIVE:
        raise ValidationError(
            "Assigned clinic is not active"
        )

    return clinic.id


def resolve_effective_clinic_id(
    user_id: int,
    jwt_payload: dict,
) -> int | None:
    user = _get_user(user_id)

    if not isinstance(jwt_payload, dict):
        raise ValidationError(
            "Invalid clinic context"
        )

    if user.role is not Role.SUPER_ADMIN:
        return _resolve_assigned_clinic_id(
            user.clinic_id,
        )

    raw_context = jwt_payload.get(
        CLINIC_CONTEXT_CLAIM
    )

    if raw_context is None:
        return None

    if (
        isinstance(raw_context, bool)
        or not isinstance(raw_context, int)
        or raw_context <= 0
    ):
        raise ValidationError(
            "Invalid clinic context"
        )

    clinic = _get_active_clinic(
        raw_context
    )

    return clinic.id


def get_current_clinic_context(
    user_id: int,
    jwt_payload: dict,
) -> dict:
    user = _get_user(user_id)

    clinic_id = resolve_effective_clinic_id(
        user_id=user_id,
        jwt_payload=jwt_payload,
    )

    if clinic_id is None:
        return {
            "clinic_id": None,
            "clinic_name": None,
            "source": "system",
        }

    clinic = db.session.get(
        Clinic,
        clinic_id,
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    source = (
        "selected"
        if user.role is Role.SUPER_ADMIN
        else "assigned"
    )

    return {
        "clinic_id": clinic.id,
        "clinic_name": clinic.name,
        "source": source,
    }


@transactional
def select_clinic_context(
    user_id: int,
    clinic_id: int,
) -> Clinic:
    user = _get_user(
        user_id
    )

    if user.role is not Role.SUPER_ADMIN:
        raise ValidationError(
            "Only a super administrator can select a clinic context"
        )

    clinic = _get_active_clinic(
        clinic_id
    )

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        user_id=user.id,
        clinic_id=clinic.id,
        description=(
            f"Super administrator selected "
            f"clinic context '{clinic.name}'"
        ),
        new_value={
            "clinic_context_id": clinic.id,
        },
    )

    return clinic


@transactional
def clear_clinic_context(
    user_id: int,
    clinic_id: int | None = None,
) -> None:
    user = _get_user(
        user_id
    )

    if user.role is not Role.SUPER_ADMIN:
        raise ValidationError(
            "Only a super administrator can clear a clinic context"
        )

    if clinic_id is not None and (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
        or clinic_id <= 0
    ):
        raise ValidationError(
            "Invalid clinic context"
        )

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        user_id=user.id,
        clinic_id=clinic_id,
        description=(
            "Super administrator cleared clinic context"
        ),
        new_value={
            "clinic_context_id": None,
        },
    )
