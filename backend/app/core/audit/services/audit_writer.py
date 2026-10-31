from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

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


_ENTITY_PATIENT_TABLES = {
    "PatientAllergy": "patient_allergies",
    "Prescription": "prescriptions",
    "ClinicalAlert": "clinical_alerts",
    "MedicationReconciliation": "medication_reconciliations",
    "MedicationAdministration": "medication_administrations",
    "NursingCarePlan": "nursing_care_plans",
    "NursingCareTask": "nursing_care_tasks",
    "NursingNote": "nursing_notes",
    "Consultation": "consultations",
    "ClinicalRecordAmendment": "clinical_record_amendments",
    "DischargeSummary": "discharge_summaries",
    "EmergencyAccessGrant": "emergency_access_grants",
}


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


def _payload_patient_id(
    payload,
    *,
    depth: int = 0,
):
    if payload is None or depth > 6:
        return None

    if isinstance(payload, dict):
        if "patient_id" in payload:
            value = payload["patient_id"]

            if value is not None:
                return _normalize_optional_id(
                    value,
                    "Patient ID",
                )

        for value in payload.values():
            result = _payload_patient_id(
                value,
                depth=depth + 1,
            )

            if result is not None:
                return result

        return None

    if isinstance(payload, (list, tuple)):
        for value in payload:
            result = _payload_patient_id(
                value,
                depth=depth + 1,
            )

            if result is not None:
                return result

    return None


def _entity_patient_id(
    *,
    entity_type: str,
    entity_id: int,
):
    if entity_type in {
        "Patient",
        "patient",
    }:
        # Entity-based inference is deliberately best-effort.
        # A legacy/synthetic audit row may identify a Patient
        # resource ID that is not present in this transaction.
        # Explicit or payload patient_id values remain strict.
        return db.session.execute(
            text(
                "SELECT id "
                "FROM patients "
                "WHERE id = :entity_id"
            ),
            {
                "entity_id": entity_id,
            },
        ).scalar_one_or_none()

    table_name = _ENTITY_PATIENT_TABLES.get(
        entity_type
    )

    if table_name is None:
        return None

    # Table names come only from the static mapping above.
    statement = text(
        f"SELECT patient_id "
        f"FROM {table_name} "
        f"WHERE id = :entity_id"
    )

    return db.session.execute(
        statement,
        {
            "entity_id": entity_id,
        },
    ).scalar_one_or_none()


def _validate_patient_scope(
    *,
    patient_id: int,
    clinic_id: int | None,
) -> int:
    patient_clinic_id = db.session.execute(
        text(
            "SELECT clinic_id "
            "FROM patients "
            "WHERE id = :patient_id"
        ),
        {
            "patient_id": patient_id,
        },
    ).scalar_one_or_none()

    if patient_clinic_id is None:
        raise ValidationError(
            "Audit patient could not be resolved"
        )

    if (
        clinic_id is not None
        and patient_clinic_id != clinic_id
    ):
        raise ValidationError(
            "Audit patient does not belong "
            "to the audit clinic"
        )

    return patient_clinic_id


def _resolve_patient_scope(
    *,
    explicit_patient_id,
    entity_type: str,
    entity_id: int,
    old_value,
    new_value,
    clinic_id: int | None,
) -> tuple[int | None, int | None]:
    patient_id = _normalize_optional_id(
        explicit_patient_id,
        "Patient ID",
    )

    if patient_id is None:
        for payload in (
            new_value,
            old_value,
        ):
            patient_id = _payload_patient_id(
                payload
            )

            if patient_id is not None:
                break

    if patient_id is None:
        patient_id = _entity_patient_id(
            entity_type=entity_type,
            entity_id=entity_id,
        )

    if patient_id is None:
        return None, clinic_id

    patient_clinic_id = _validate_patient_scope(
        patient_id=patient_id,
        clinic_id=clinic_id,
    )

    return (
        patient_id,
        (
            clinic_id
            if clinic_id is not None
            else patient_clinic_id
        ),
    )


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
    patient_id: Optional[int] = None,
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

    (
        resolved_patient_id,
        resolved_clinic_id,
    ) = _resolve_patient_scope(
        explicit_patient_id=patient_id,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        clinic_id=resolved_clinic_id,
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
        patient_id=resolved_patient_id,
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
