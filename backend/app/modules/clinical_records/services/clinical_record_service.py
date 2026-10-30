from __future__ import annotations

import hashlib
import json

from sqlalchemy import select

from app.extensions import db
from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.clinical_records.models.clinical_record_amendment_model import (
    ClinicalRecordAmendment,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


def _enum_value(value) -> str:
    return getattr(
        value,
        "value",
        value,
    )


def _positive_int(
    value,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )

    return value


def _required_text(
    value,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{field_name} is required"
        )

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_record_type(
    value,
) -> ClinicalRecordType:
    try:
        return ClinicalRecordType(
            _enum_value(value)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid clinical record type"
        ) from exc


def _normalize_record_status(
    value,
) -> ClinicalRecordStatus:
    try:
        return ClinicalRecordStatus(
            _enum_value(value)
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid clinical record status"
        ) from exc


def _canonical_content(
    value,
) -> tuple[
    dict,
    str,
]:
    if not isinstance(
        value,
        dict,
    ):
        raise ValidationError(
            "new_content must be an object"
        )

    if not value:
        raise ValidationError(
            "new_content must not be empty"
        )

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
            ensure_ascii=False,
            default=str,
        ).encode(
            "utf-8"
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "new_content must be JSON serializable"
        ) from exc

    return (
        value,
        hashlib.sha256(
            encoded
        ).hexdigest(),
    )


def _get_patient(
    patient_id: int,
    clinic_id: int,
) -> Patient:
    patient_id = _positive_int(
        patient_id,
        "patient_id",
    )
    clinic_id = _positive_int(
        clinic_id,
        "clinic_id",
    )

    patient = db.session.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_active_actor(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    staff_id = _positive_int(
        staff_id,
        "staff_id",
    )
    clinic_id = _positive_int(
        clinic_id,
        "clinic_id",
    )

    staff = db.session.execute(
        select(Staff).where(
            Staff.id == staff_id,
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Clinical record actor must be active"
        )

    if (
        staff.user is None
        or not staff.user.is_active
    ):
        raise ValidationError(
            "Clinical record actor user must be active"
        )

    return staff


def validate_finalization_transition(
    current_status,
) -> ClinicalRecordStatus:
    status = _normalize_record_status(
        current_status
    )

    if status != ClinicalRecordStatus.DRAFT:
        raise ConflictError(
            "Only draft clinical records "
            "can be finalized"
        )

    return ClinicalRecordStatus.FINAL


def validate_amendment_transition(
    current_status,
) -> ClinicalRecordStatus:
    status = _normalize_record_status(
        current_status
    )

    if status not in {
        ClinicalRecordStatus.FINAL,
        ClinicalRecordStatus.AMENDED,
    }:
        raise ConflictError(
            "Only final or amended clinical records "
            "can receive an amendment"
        )

    return ClinicalRecordStatus.AMENDED


def get_clinical_record_amendments(
    *,
    clinic_id: int,
    source_record_type,
    source_record_id: int,
) -> list[
    ClinicalRecordAmendment
]:
    clinic_id = _positive_int(
        clinic_id,
        "clinic_id",
    )
    record_type = _normalize_record_type(
        source_record_type
    )
    source_record_id = _positive_int(
        source_record_id,
        "source_record_id",
    )

    return list(
        db.session.execute(
            select(
                ClinicalRecordAmendment
            )
            .where(
                ClinicalRecordAmendment.clinic_id
                == clinic_id,
                ClinicalRecordAmendment.source_record_type
                == record_type.value,
                ClinicalRecordAmendment.source_record_id
                == source_record_id,
            )
            .order_by(
                ClinicalRecordAmendment.new_version.asc(),
                ClinicalRecordAmendment.id.asc(),
            )
        ).scalars().all()
    )


def append_clinical_record_amendment(
    *,
    clinic_id: int,
    patient_id: int,
    source_record_type,
    source_record_id: int,
    current_status,
    previous_version: int,
    amended_by_staff_id: int,
    reason: str,
    new_content: dict,
) -> ClinicalRecordAmendment:
    clinic_id = _positive_int(
        clinic_id,
        "clinic_id",
    )
    patient = _get_patient(
        patient_id,
        clinic_id,
    )
    actor = _get_active_actor(
        amended_by_staff_id,
        clinic_id,
    )

    record_type = _normalize_record_type(
        source_record_type
    )
    source_record_id = _positive_int(
        source_record_id,
        "source_record_id",
    )
    previous_version = _positive_int(
        previous_version,
        "previous_version",
    )

    validate_amendment_transition(
        current_status
    )

    reason = _required_text(
        reason,
        "reason",
        10000,
    )

    (
        normalized_content,
        content_hash,
    ) = _canonical_content(
        new_content
    )

    latest = db.session.execute(
        select(
            ClinicalRecordAmendment
        )
        .where(
            ClinicalRecordAmendment.clinic_id
            == clinic_id,
            ClinicalRecordAmendment.source_record_type
            == record_type.value,
            ClinicalRecordAmendment.source_record_id
            == source_record_id,
        )
        .order_by(
            ClinicalRecordAmendment.new_version.desc(),
            ClinicalRecordAmendment.id.desc(),
        )
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    if latest is None:
        if previous_version != 1:
            raise ConflictError(
                "First amendment must reference "
                "record version 1"
            )

        previous_amendment_id = None
    else:
        if previous_version != latest.new_version:
            raise ConflictError(
                "Amendment version chain is stale"
            )

        previous_amendment_id = latest.id

    amendment = ClinicalRecordAmendment(
        clinic_id=clinic_id,
        patient_id=patient.id,
        source_record_type=record_type.value,
        source_record_id=source_record_id,
        previous_version=previous_version,
        new_version=previous_version + 1,
        previous_amendment_id=previous_amendment_id,
        amended_by_staff_id=actor.id,
        reason=reason,
        new_content=normalized_content,
        new_content_sha256=content_hash,
    )

    db.session.add(
        amendment
    )
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="ClinicalRecordAmendment",
        entity_id=amendment.id,
        description="Created clinical record amendment",
        new_value={
            "source_record_type": (
                amendment.source_record_type
            ),
            "source_record_id": (
                amendment.source_record_id
            ),
            "previous_version": (
                amendment.previous_version
            ),
            "new_version": (
                amendment.new_version
            ),
            "previous_amendment_id": (
                amendment.previous_amendment_id
            ),
            "new_content_sha256": (
                amendment.new_content_sha256
            ),
        },
        user_id=actor.user_id,
        clinic_id=clinic_id,
    )

    return amendment
