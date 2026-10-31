from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.services.audit_redaction import (
    redact_audit_payload,
)
from app.core.auth.user.models.user_model import User
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ValidationError
from app.extensions import db


MAX_REASON_LENGTH = 1000
MAX_OPERATION_ID_LENGTH = 255


def _validate_positive_id(
    value,
    field_name: str,
) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )


def _normalize_optional_id(
    value,
    field_name: str,
):
    if value is None:
        return None

    _validate_positive_id(
        value,
        field_name,
    )

    return value


def _normalize_optional_string(
    value,
    field_name: str,
    max_length: int,
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

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _resolve_clinic_id(
    *,
    user_id: Optional[int],
    clinic_id: Optional[int],
) -> Optional[int]:
    clinic_id = _normalize_optional_id(
        clinic_id,
        "Clinic ID",
    )

    if user_id is None:
        return clinic_id

    user_clinic_id = db.session.execute(
        db.select(User.clinic_id).where(
            User.id == user_id
        )
    ).scalar_one_or_none()

    if (
        clinic_id is not None
        and user_clinic_id is not None
        and clinic_id != user_clinic_id
    ):
        raise ValidationError(
            "Audit clinic does not match the audit actor"
        )

    if clinic_id is not None:
        return clinic_id

    return user_clinic_id


def write_audit_log(
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: int,
    description: Optional[str] = None,
    reason: Optional[str] = None,
    operation_id: Optional[str] = None,
    old_value=None,
    new_value=None,
    user_id: Optional[int] = None,
    clinic_id: Optional[int] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    _validate_positive_id(
        entity_id,
        "Audit entity ID",
    )

    user_id = _normalize_optional_id(
        user_id,
        "User ID",
    )

    resolved_clinic_id = _resolve_clinic_id(
        user_id=user_id,
        clinic_id=clinic_id,
    )

    reason = _normalize_optional_string(
        (
            reason
            if reason is not None
            else description
        ),
        "Audit reason",
        MAX_REASON_LENGTH,
    )

    operation_id = _normalize_optional_string(
        operation_id,
        "Audit operation ID",
        MAX_OPERATION_ID_LENGTH,
    )

    log = AuditLog(
        user_id=user_id,
        clinic_id=resolved_clinic_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        reason=reason,
        operation_id=operation_id,
        old_value=redact_audit_payload(
            old_value
        ),
        new_value=redact_audit_payload(
            new_value
        ),
        ip_address=ip_address,
        created_at=datetime.now(
            timezone.utc
        ),
    )

    db.session.add(log)

    return log
