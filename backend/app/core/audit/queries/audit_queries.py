from __future__ import annotations

from typing import Optional

from app.extensions import db

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role


def _normalize_role(role) -> Role | None:
    if isinstance(role, Role):
        return role

    if role is None:
        return None

    try:
        return Role(role)
    except (TypeError, ValueError):
        return None


def _validate_positive_id(
    value,
    field_name: str,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(
            f"{field_name} must be a positive integer"
        )


def build_audit_query(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    target_clinic_id: Optional[int] = None,
    user_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
):
    role = _normalize_role(actor_role)

    if role not in {
        Role.ADMIN,
        Role.SUPER_ADMIN,
    }:
        raise PermissionError(
            "Insufficient audit permissions"
        )

    if actor_clinic_id is not None:
        _validate_positive_id(
            actor_clinic_id,
            "Actor clinic ID",
        )

    if target_clinic_id is not None:
        _validate_positive_id(
            target_clinic_id,
            "Target clinic ID",
        )

    if user_id is not None:
        _validate_positive_id(
            user_id,
            "User ID",
        )

    if patient_id is not None:
        _validate_positive_id(
            patient_id,
            "Patient ID",
        )

    if entity_id is not None:
        _validate_positive_id(
            entity_id,
            "Entity ID",
        )

    statement = db.select(AuditLog)

    if role is Role.ADMIN:
        if actor_clinic_id is None:
            raise PermissionError(
                "Audit administrator must belong to a clinic"
            )

        statement = statement.where(
            AuditLog.clinic_id == actor_clinic_id
        )

        if (
            target_clinic_id is not None
            and target_clinic_id != actor_clinic_id
        ):
            raise PermissionError(
                "Audit clinic scope is not permitted"
            )

    elif role is Role.SUPER_ADMIN:
        if target_clinic_id is not None:
            statement = statement.where(
                AuditLog.clinic_id == target_clinic_id
            )

    if user_id is not None:
        statement = statement.where(
            AuditLog.user_id == user_id
        )

    if patient_id is not None:
        statement = statement.where(
            AuditLog.patient_id == patient_id
        )

    if action is not None:
        statement = statement.where(
            AuditLog.action == action
        )

    if entity_type is not None:
        statement = statement.where(
            AuditLog.entity_type == entity_type
        )

    if entity_id is not None:
        statement = statement.where(
            AuditLog.entity_id == entity_id
        )

    return statement.order_by(
        AuditLog.created_at.desc(),
        AuditLog.id.desc(),
    )


def list_audit_logs(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    target_clinic_id: Optional[int] = None,
    user_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 20,
):
    statement = build_audit_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        user_id=user_id,
        patient_id=patient_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
    )

    return db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )


def get_audit_log(
    *,
    audit_log_id: int,
    actor_role,
    actor_clinic_id: Optional[int],
):
    _validate_positive_id(
        audit_log_id,
        "Audit log ID",
    )

    statement = build_audit_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
    ).where(
        AuditLog.id == audit_log_id
    )

    return db.session.execute(
        statement
    ).scalar_one_or_none()


def count_audit_logs(
    *,
    actor_role,
    actor_clinic_id: Optional[int],
    target_clinic_id: Optional[int] = None,
    user_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    action: Optional[AuditAction] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> int:
    statement = build_audit_query(
        actor_role=actor_role,
        actor_clinic_id=actor_clinic_id,
        target_clinic_id=target_clinic_id,
        user_id=user_id,
        patient_id=patient_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
    ).order_by(None)

    count_statement = db.select(
        db.func.count()
    ).select_from(
        statement.subquery()
    )

    return int(
        db.session.execute(
            count_statement
        ).scalar_one()
    )
