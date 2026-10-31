from __future__ import annotations

from typing import Optional

from flask import (
    has_request_context,
    request,
)

from app.core.audit.models.audit_model import AuditLog
from app.core.audit.services.audit_writer import write_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ValidationError


MAX_IP_ADDRESS_LENGTH = 45
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


def _normalize_action(action) -> AuditAction:
    if isinstance(action, AuditAction):
        return action

    try:
        return AuditAction(action)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid audit action"
        )


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


def _normalize_ip_address(
    value,
):
    return _normalize_optional_string(
        value,
        "IP address",
        MAX_IP_ADDRESS_LENGTH,
    )


def _payload_reason(
    payload,
) -> str | None:
    if not isinstance(payload, dict):
        return None

    value = payload.get(
        "reason"
    )

    if not isinstance(value, str):
        return None

    return _normalize_optional_string(
        value,
        "Audit reason",
        MAX_REASON_LENGTH,
    )


def _resolve_reason(
    *,
    reason,
    description,
    old_value,
    new_value,
) -> str | None:
    explicit = _normalize_optional_string(
        reason,
        "Audit reason",
        MAX_REASON_LENGTH,
    )

    if explicit is not None:
        return explicit

    for payload in (
        new_value,
        old_value,
    ):
        candidate = _payload_reason(
            payload
        )

        if candidate is not None:
            return candidate

    return _normalize_optional_string(
        description,
        "Audit reason",
        MAX_REASON_LENGTH,
    )


def _request_operation_id() -> str | None:
    if not has_request_context():
        return None

    for header in (
        "X-Operation-Id",
        "Idempotency-Key",
    ):
        value = request.headers.get(
            header
        )

        normalized = _normalize_optional_string(
            value,
            "Audit operation ID",
            MAX_OPERATION_ID_LENGTH,
        )

        if normalized is not None:
            return normalized

    return None


def _resolve_operation_id(
    value,
) -> str | None:
    explicit = _normalize_optional_string(
        value,
        "Audit operation ID",
        MAX_OPERATION_ID_LENGTH,
    )

    if explicit is not None:
        return explicit

    return _request_operation_id()


def create_audit_log(
    *,
    action: AuditAction,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    description: Optional[str] = None,
    reason: Optional[str] = None,
    operation_id: Optional[str] = None,
    old_value=None,
    new_value=None,
    user_id: Optional[int] = None,
    clinic_id: Optional[int] = None,
    patient_id: Optional[int] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    action = _normalize_action(action)

    resolved_entity_type = _normalize_optional_string(
        entity_type,
        "Audit entity type",
        max_length=80,
    )

    if resolved_entity_type is None:
        raise ValidationError(
            "Audit entity type is required"
        )

    _validate_positive_id(
        entity_id,
        "Audit entity ID",
    )

    user_id = _normalize_optional_id(
        user_id,
        "User ID",
    )

    clinic_id = _normalize_optional_id(
        clinic_id,
        "Clinic ID",
    )

    patient_id = _normalize_optional_id(
        patient_id,
        "Patient ID",
    )

    description = _normalize_optional_string(
        description,
        "Audit description",
        max_length=255,
    )

    ip_address = _normalize_ip_address(
        ip_address,
    )

    resolved_reason = _resolve_reason(
        reason=reason,
        description=description,
        old_value=old_value,
        new_value=new_value,
    )

    resolved_operation_id = (
        _resolve_operation_id(
            operation_id
        )
    )

    return write_audit_log(
        action=action,
        entity_type=resolved_entity_type,
        entity_id=entity_id,
        description=description,
        reason=resolved_reason,
        operation_id=resolved_operation_id,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        ip_address=ip_address,
    )
