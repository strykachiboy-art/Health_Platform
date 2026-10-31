from __future__ import annotations

from app.core.audit.integrity import (
    verify_audit_log_integrity,
)
from app.core.audit.models.audit_model import AuditLog
from app.core.exceptions import ValidationError
from app.extensions import db


MAX_VERIFY_ROWS = 10000


def _positive_id(
    value,
    field_name: str,
):
    if value is None:
        return None

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def verify_audit_integrity(
    *,
    clinic_id: int,
    start_id: int | None = None,
    end_id: int | None = None,
    limit: int = MAX_VERIFY_ROWS,
) -> dict:
    clinic_id = _positive_id(
        clinic_id,
        "Clinic ID",
    )

    start_id = _positive_id(
        start_id,
        "Start audit ID",
    )

    end_id = _positive_id(
        end_id,
        "End audit ID",
    )

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
        or limit > MAX_VERIFY_ROWS
    ):
        raise ValidationError(
            f"limit must be between 1 and "
            f"{MAX_VERIFY_ROWS}"
        )

    if (
        start_id is not None
        and end_id is not None
        and start_id > end_id
    ):
        raise ValidationError(
            "Start audit ID cannot exceed end audit ID"
        )

    statement = db.select(
        AuditLog
    ).where(
        AuditLog.clinic_id == clinic_id
    )

    if start_id is not None:
        statement = statement.where(
            AuditLog.id >= start_id
        )

    if end_id is not None:
        statement = statement.where(
            AuditLog.id <= end_id
        )

    rows = list(
        db.session.execute(
            statement
            .order_by(
                AuditLog.id.asc()
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )

    invalid_ids = [
        row.id
        for row in rows
        if not verify_audit_log_integrity(
            row
        )
    ]

    checked = len(
        rows
    )

    invalid = len(
        invalid_ids
    )

    return {
        "clinic_id": clinic_id,
        "start_id": start_id,
        "end_id": end_id,
        "limit": limit,
        "checked": checked,
        "valid": checked - invalid,
        "invalid": invalid,
        "invalid_ids": invalid_ids,
        "complete": checked < limit,
    }
