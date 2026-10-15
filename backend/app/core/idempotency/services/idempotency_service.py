from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.core.idempotency.models.idempotency_model import (
    IdempotencyRecord,
)
from app.extensions import db


MAX_KEY_LENGTH = 255
MAX_OPERATION_LENGTH = 120


def _validate_positive_id(
    value: int,
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


def normalize_idempotency_key(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            "Idempotency-Key must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            "Idempotency-Key cannot be blank"
        )

    if len(value) > MAX_KEY_LENGTH:
        raise ValidationError(
            f"Idempotency-Key cannot exceed "
            f"{MAX_KEY_LENGTH} characters"
        )

    return value


def normalize_operation_name(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            "Idempotency operation must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            "Idempotency operation cannot be blank"
        )

    if len(value) > MAX_OPERATION_LENGTH:
        raise ValidationError(
            "Idempotency operation name is too long"
        )

    return value


def _json_default(value: Any):
    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return str(value)

    raise TypeError(
        f"Object of type {type(value).__name__} "
        "is not JSON serializable"
    )


def request_fingerprint(
    payload: Any,
) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def reserve_idempotency_operation(
    *,
    clinic_id: int,
    user_id: int,
    operation: str,
    idempotency_key: str,
    request_payload: Any,
) -> tuple[IdempotencyRecord, bool]:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    operation = normalize_operation_name(
        operation
    )

    idempotency_key = normalize_idempotency_key(
        idempotency_key
    )

    request_hash = request_fingerprint(
        request_payload
    )

    savepoint = db.session.begin_nested()

    record = IdempotencyRecord(
        clinic_id=clinic_id,
        user_id=user_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )

    db.session.add(record)

    try:
        db.session.flush()
    except IntegrityError:
        savepoint.rollback()

        existing = db.session.execute(
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.clinic_id == clinic_id,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key
                == idempotency_key,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing is None:
            raise ConflictError(
                "Idempotency operation could not be reconciled",
                code="IDEMPOTENCY_OUTCOME_UNRESOLVED",
            )

        if existing.request_hash != request_hash:
            raise ConflictError(
                "Idempotency-Key was already used "
                "with a different request payload",
                code="IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
            )

        return existing, False

    savepoint.commit()

    return record, True
