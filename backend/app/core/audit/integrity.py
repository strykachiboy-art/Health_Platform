from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


def _action_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)

    text = str(value)

    if "." in text:
        text = text.rsplit(".", 1)[-1]

    return text.lower()


def _datetime_value(value: Any) -> str | None:
    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value

    if not isinstance(value, datetime):
        return str(value)

    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.isoformat(timespec="microseconds")


def _json_default(value: Any):
    if isinstance(value, Enum):
        return value.value

    if isinstance(value, datetime):
        return _datetime_value(value)

    if isinstance(value, date):
        return value.isoformat()

    return str(value)


def build_audit_evidence_payload(audit_log) -> dict[str, Any]:
    return {
        "user_id": audit_log.user_id,
        "clinic_id": audit_log.clinic_id,
        "patient_id": audit_log.patient_id,
        "action": _action_value(audit_log.action),
        "entity_type": audit_log.entity_type,
        "entity_id": audit_log.entity_id,
        "description": audit_log.description,
        "reason": audit_log.reason,
        "operation_id": audit_log.operation_id,
        "old_value": audit_log.old_value,
        "new_value": audit_log.new_value,
        "ip_address": audit_log.ip_address,
        "created_at": _datetime_value(
            audit_log.created_at
        ),
    }


def calculate_audit_evidence_sha256(audit_log) -> str:
    canonical = json.dumps(
        build_audit_evidence_payload(
            audit_log
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def verify_audit_log_integrity(audit_log) -> bool:
    expected = calculate_audit_evidence_sha256(
        audit_log
    )

    actual = audit_log.evidence_sha256

    if not isinstance(actual, str):
        return False

    return hmac.compare_digest(
        actual,
        expected,
    )
