from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emar_enums import (
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
from app.core.enums.prescription_enums import (
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import db

from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission


AUTHORIZED_EMAR_ROLES = (
    Role.NURSE,
)

_PENDING_STATUSES = {
    MedicationAdministrationStatus.SCHEDULED,
    MedicationAdministrationStatus.DUE,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(
    value: str | None,
    *,
    field_name: str,
    max_length: int,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise ValidationError(
                f"{field_name} is required"
            )
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        if required:
            raise ValidationError(
                f"{field_name} is required"
            )
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _validate_positive_id(
    value: int,
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


def _normalize_event_time(
    value: datetime | None,
    *,
    field_name: str,
    default_now: bool = True,
) -> datetime:
    if value is None:
        if default_now:
            return _utcnow()

        raise ValidationError(
            f"{field_name} is required"
        )

    if not isinstance(value, datetime):
        raise ValidationError(
            f"{field_name} must be a datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            f"{field_name} must include timezone information"
        )

    return value.astimezone(
        timezone.utc
    )


def _db_comparable(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value

    return (
        value.astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def _get_patient(
    patient_id: int,
    clinic_id: int,
) -> Patient:
    _validate_positive_id(
        patient_id,
        "patient_id",
    )

    patient = db.session.get(
        Patient,
        patient_id,
    )

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    if patient.clinic_id != clinic_id:
        raise ValidationError(
            "Patient does not belong to the clinic"
        )

    if not patient.is_active:
        raise ValidationError(
            "Patient is inactive"
        )

    return patient


def _get_active_admission(
    admission_id: int,
    *,
    clinic_id: int,
    patient_id: int,
    lock: bool = False,
) -> Admission:
    _validate_positive_id(
        admission_id,
        "admission_id",
    )

    query = Admission.query.filter(
        Admission.id
        == admission_id
    )

    if lock:
        query = query.with_for_update()

    admission = query.first()

    if admission is None:
        raise NotFoundError(
            f"Admission {admission_id} not found"
        )

    if admission.patient_id != patient_id:
        raise ValidationError(
            "Admission does not belong to the patient"
        )

    if (
        admission.patient is None
        or admission.patient.clinic_id != clinic_id
    ):
        raise ValidationError(
            "Admission does not belong to the clinic"
        )

    if (
        admission.status
        != AdmissionStatus.ADMITTED
        or admission.discharged_at is not None
    ):
        raise ConflictError(
            "Medication administration requires "
            "an active admission"
        )

    return admission


def _get_authorized_actor(
    staff_id: int,
    *,
    clinic_id: int,
) -> Staff:
    _validate_positive_id(
        staff_id,
        "actor_staff_id",
    )

    staff = db.session.get(
        Staff,
        staff_id,
    )

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if staff.clinic_id != clinic_id:
        raise ValidationError(
            "Medication administration actor "
            "does not belong to the clinic"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Medication administration actor "
            "must be active staff"
        )

    if staff.user is None:
        raise ValidationError(
            "Medication administration actor "
            "must be linked to a user account"
        )

    if not staff.user.is_active:
        raise ValidationError(
            "Medication administration actor "
            "user account is inactive"
        )

    if staff.user.role not in AUTHORIZED_EMAR_ROLES:
        allowed = ", ".join(
            role.value
            for role in AUTHORIZED_EMAR_ROLES
        )

        raise ValidationError(
            "Medication administration actor "
            f"must have one of these roles: {allowed}"
        )

    return staff

def _get_order_context(
    prescription_item_id: int,
    *,
    clinic_id: int,
    patient_id: int,
    event_at: datetime,
    lock: bool = False,
) -> tuple[Prescription, PrescriptionItem]:
    _validate_positive_id(
        prescription_item_id,
        "prescription_item_id",
    )

    item_query = PrescriptionItem.query.filter(
        PrescriptionItem.id
        == prescription_item_id
    )

    if lock:
        item_query = (
            item_query.with_for_update()
        )

    item = item_query.first()

    if item is None:
        raise NotFoundError(
            "Prescription item "
            f"{prescription_item_id} not found"
        )

    prescription_query = (
        Prescription.query.filter(
            Prescription.id
            == item.prescription_id
        )
    )

    if lock:
        prescription_query = (
            prescription_query.with_for_update()
        )

    prescription = (
        prescription_query.first()
    )

    if prescription is None:
        raise ValidationError(
            "Prescription item is not linked "
            "to a prescription"
        )

    if (
        item.prescription_id
        != prescription.id
    ):
        raise ValidationError(
            "Prescription item does not belong "
            "to the prescription"
        )

    if prescription.clinic_id != clinic_id:
        raise ValidationError(
            "Medication order does not belong "
            "to the clinic"
        )

    if prescription.patient_id != patient_id:
        raise ValidationError(
            "Medication order does not belong "
            "to the patient"
        )

    if (
        prescription.status
        != PrescriptionStatus.ACTIVE
    ):
        raise ConflictError(
            "Medication order prescription "
            "is not active"
        )

    event_db = _db_comparable(
        event_at
    )

    expires_at = _db_comparable(
        prescription.expires_at
    )

    if (
        expires_at is not None
        and event_db is not None
        and expires_at <= event_db
    ):
        raise ConflictError(
            "Medication order prescription "
            "has expired"
        )

    if (
        item.order_type
        != MedicationOrderType.INPATIENT
    ):
        raise ValidationError(
            "eMAR requires an inpatient "
            "medication order"
        )

    start_at = _db_comparable(
        item.start_at
    )

    if (
        start_at is not None
        and event_db is not None
        and event_db < start_at
    ):
        raise ConflictError(
            "Medication order has not started"
        )

    end_at = _db_comparable(
        item.end_at
    )

    if (
        end_at is not None
        and event_db is not None
        and event_db > end_at
    ):
        raise ConflictError(
            "Medication order has ended"
        )

    if (
        item.dose_value is None
        or item.dose_unit is None
        or item.route_code is None
    ):
        raise ValidationError(
            "eMAR requires structured dose, "
            "dose unit, and route"
        )

    return (
        prescription,
        item,
    )


def _get_context_for_event(
    *,
    clinic_id: int,
    patient_id: int,
    admission_id: int,
    prescription_item_id: int,
    actor_staff_id: int,
    event_at: datetime,
    lock: bool = False,
) -> tuple[
    Patient,
    Admission,
    Staff,
    Prescription,
    PrescriptionItem,
]:
    _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    ensure_clinic_active(
        clinic_id
    )

    patient = _get_patient(
        patient_id,
        clinic_id,
    )

    admission = _get_active_admission(
        admission_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        lock=lock,
    )

    actor = _get_authorized_actor(
        actor_staff_id,
        clinic_id=clinic_id,
    )

    prescription, item = _get_order_context(
        prescription_item_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        event_at=event_at,
        lock=lock,
    )

    return (
        patient,
        admission,
        actor,
        prescription,
        item,
    )

def _audit_user_id(
    actor: Staff,
) -> int | None:
    return actor.user_id


def _audit_status_change(
    *,
    record: MedicationAdministration,
    actor: Staff,
    old_status: MedicationAdministrationStatus | None,
    description: str,
) -> None:
    create_audit_log(
        action=(
            AuditAction.CREATE
            if old_status is None
            else AuditAction.STATUS_CHANGE
        ),
        entity_type="MedicationAdministration",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        description=description,
        old_value=(
            None
            if old_status is None
            else {
                "status": old_status.value,
            }
        ),
        new_value={
            "status": record.status.value,
            "patient_id": record.patient_id,
            "admission_id": record.admission_id,
            "prescription_id": record.prescription_id,
            "prescription_item_id": (
                record.prescription_item_id
            ),
            "scheduled_at": (
                record.scheduled_at.isoformat()
                if record.scheduled_at is not None
                else None
            ),
            "source_order_version": (
                record.source_order_version
            ),
        },
        user_id=_audit_user_id(
            actor
        ),
    )


def _source_order_version(
    item: PrescriptionItem,
) -> int:
    # Structured medication orders do not yet carry an explicit
    # revision counter. Gate 5 snapshots the initial order as version 1.
    # Later order-amendment work can evolve this contract.
    del item
    return 1


def _copy_order_snapshot(
    item: PrescriptionItem,
) -> dict:
    return {
        "dose_value": item.dose_value,
        "dose_unit": item.dose_unit,
        "route_code": item.route_code,
        "source_order_version": (
            _source_order_version(
                item
            )
        ),
    }


def _load_administration(
    administration_id: int,
    *,
    clinic_id: int,
    lock: bool = False,
) -> MedicationAdministration:
    _validate_positive_id(
        administration_id,
        "administration_id",
    )

    query = (
        MedicationAdministration.query
        .filter(
            MedicationAdministration.id
            == administration_id,
            MedicationAdministration.clinic_id
            == clinic_id,
        )
    )

    if lock:
        query = query.with_for_update()

    record = query.first()

    if record is None:
        raise NotFoundError(
            "Medication administration "
            f"{administration_id} not found"
        )

    return record


_TERMINAL_EMAR_STATUSES = {
    MedicationAdministrationStatus.ADMINISTERED,
    MedicationAdministrationStatus.HELD,
    MedicationAdministrationStatus.REFUSED,
    MedicationAdministrationStatus.MISSED,
    MedicationAdministrationStatus.CANCELLED,
}

_EMAR_AMENDMENT_DOCUMENT_FIELDS = (
    "status",
    "scheduled_at",
    "administered_at",
    "administered_by_staff_id",
    "held_at",
    "held_by_staff_id",
    "hold_reason",
    "refused_at",
    "refused_recorded_by_staff_id",
    "refusal_reason",
    "missed_at",
    "missed_reason",
    "cancelled_at",
    "cancel_reason",
    "notes",
)


def _finalize_medication_administration_record(
    record: MedicationAdministration,
    *,
    actor: Staff,
    finalized_at: datetime,
) -> None:
    clinical_record_service.validate_finalization_transition(
        record.record_status
    )

    record.record_status = (
        ClinicalRecordStatus.FINAL.value
    )
    record.record_version = 1
    record.finalized_by_staff_id = actor.id
    record.finalized_at = finalized_at
    record.latest_amendment_id = None


def _emar_record_status(
    record: MedicationAdministration,
) -> ClinicalRecordStatus:
    try:
        return ClinicalRecordStatus(
            record.record_status
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ConflictError(
            f"Medication administration {record.id} "
            "has invalid clinical record state"
        ) from exc


def _validate_emar_record_version(
    record: MedicationAdministration,
    expected_version: int,
) -> None:
    expected_version = _validate_positive_id(
        expected_version,
        "expected_version",
    )

    if record.record_version != expected_version:
        raise ConflictError(
            f"Medication administration {record.id} "
            "record version mismatch: "
            f"expected {expected_version}, "
            f"current {record.record_version}"
        )


def _document_datetime(
    value,
    field_name: str,
):
    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value
            )
        except ValueError as exc:
            raise ValidationError(
                f"{field_name} must be an ISO-8601 datetime"
            ) from exc

    normalized = _normalize_event_time(
        value,
        field_name=field_name,
        default_now=False,
    )

    return normalized.isoformat()


def _document_positive_id(
    value,
    field_name: str,
):
    if value is None:
        return None

    return _validate_positive_id(
        value,
        field_name,
    )


def _normalize_medication_administration_document(
    record: MedicationAdministration,
    document: dict,
) -> dict:
    if not isinstance(
        document,
        dict,
    ):
        raise ValidationError(
            "document must be an object"
        )

    if set(document) != set(
        _EMAR_AMENDMENT_DOCUMENT_FIELDS
    ):
        raise ValidationError(
            "Medication administration amendment must contain "
            "the complete terminal evidence document"
        )

    try:
        status = MedicationAdministrationStatus(
            getattr(
                document["status"],
                "value",
                document["status"],
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid medication administration status"
        ) from exc

    if status != record.status:
        raise ValidationError(
            "Medication administration terminal outcome "
            "cannot be changed by amendment"
        )

    if status not in _TERMINAL_EMAR_STATUSES:
        raise ConflictError(
            "Only terminal medication administration "
            "records can be amended"
        )

    return {
        "status": status.value,
        "scheduled_at": _document_datetime(
            document["scheduled_at"],
            "scheduled_at",
        ),
        "administered_at": _document_datetime(
            document["administered_at"],
            "administered_at",
        ),
        "administered_by_staff_id": _document_positive_id(
            document["administered_by_staff_id"],
            "administered_by_staff_id",
        ),
        "held_at": _document_datetime(
            document["held_at"],
            "held_at",
        ),
        "held_by_staff_id": _document_positive_id(
            document["held_by_staff_id"],
            "held_by_staff_id",
        ),
        "hold_reason": _normalize_text(
            document["hold_reason"],
            field_name="hold_reason",
            max_length=500,
        ),
        "refused_at": _document_datetime(
            document["refused_at"],
            "refused_at",
        ),
        "refused_recorded_by_staff_id": _document_positive_id(
            document["refused_recorded_by_staff_id"],
            "refused_recorded_by_staff_id",
        ),
        "refusal_reason": _normalize_text(
            document["refusal_reason"],
            field_name="refusal_reason",
            max_length=500,
        ),
        "missed_at": _document_datetime(
            document["missed_at"],
            "missed_at",
        ),
        "missed_reason": _normalize_text(
            document["missed_reason"],
            field_name="missed_reason",
            max_length=500,
        ),
        "cancelled_at": _document_datetime(
            document["cancelled_at"],
            "cancelled_at",
        ),
        "cancel_reason": _normalize_text(
            document["cancel_reason"],
            field_name="cancel_reason",
            max_length=500,
        ),
        "notes": _normalize_text(
            document["notes"],
            field_name="notes",
            max_length=4000,
        ),
    }


def get_medication_administration_original_document(
    record: MedicationAdministration,
) -> dict:
    def iso(value):
        if value is None:
            return None

        # SQLAlchemy/SQLite test round-trips can strip tzinfo from
        # DateTime(timezone=True) values. These timestamps originated
        # from validated UTC-aware clinical events, so restore UTC only
        # at this internal DB serialization boundary. External amendment
        # input remains subject to the strict timezone validator.
        if (
            value.tzinfo is None
            or value.utcoffset() is None
        ):
            value = value.replace(
                tzinfo=timezone.utc
            )

        return value.isoformat()

    return {
        "status": record.status.value,
        "scheduled_at": iso(record.scheduled_at),
        "administered_at": iso(record.administered_at),
        "administered_by_staff_id": (
            record.administered_by_staff_id
        ),
        "held_at": iso(record.held_at),
        "held_by_staff_id": (
            record.held_by_staff_id
        ),
        "hold_reason": record.hold_reason,
        "refused_at": iso(record.refused_at),
        "refused_recorded_by_staff_id": (
            record.refused_recorded_by_staff_id
        ),
        "refusal_reason": record.refusal_reason,
        "missed_at": iso(record.missed_at),
        "missed_reason": record.missed_reason,
        "cancelled_at": iso(record.cancelled_at),
        "cancel_reason": record.cancel_reason,
        "notes": record.notes,
    }


def get_medication_administration_effective_document(
    record: MedicationAdministration,
) -> dict:
    status = _emar_record_status(
        record
    )

    if status != ClinicalRecordStatus.AMENDED:
        return (
            get_medication_administration_original_document(
                record
            )
        )

    amendment = record.latest_amendment

    if amendment is None:
        raise ConflictError(
            f"Medication administration {record.id} "
            "is amended but has no latest amendment"
        )

    if (
        amendment.clinic_id != record.clinic_id
        or amendment.patient_id != record.patient_id
        or amendment.source_record_type
        != ClinicalRecordType.MEDICATION_ADMINISTRATION.value
        or amendment.source_record_id != record.id
        or amendment.new_version != record.record_version
    ):
        raise ConflictError(
            f"Medication administration {record.id} "
            "latest amendment linkage is invalid"
        )

    return dict(
        amendment.new_content
    )


@transactional
def amend_medication_administration(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    expected_version: int,
    reason: str,
    document: dict,
):
    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
        lock=True,
    )

    actor = _get_authorized_actor(
        actor_staff_id,
        clinic_id=clinic_id,
    )

    current_status = _emar_record_status(
        record
    )

    clinical_record_service.validate_amendment_transition(
        current_status
    )

    if record.status not in _TERMINAL_EMAR_STATUSES:
        raise ConflictError(
            "Only terminal medication administration "
            "records can be amended"
        )

    _validate_emar_record_version(
        record,
        expected_version,
    )

    normalized = (
        _normalize_medication_administration_document(
            record,
            document,
        )
    )

    effective_before = (
        get_medication_administration_effective_document(
            record
        )
    )

    if normalized == effective_before:
        raise ValidationError(
            "Medication administration amendment "
            "must change the effective clinical document"
        )

    amendment = (
        clinical_record_service
        .append_clinical_record_amendment(
            clinic_id=record.clinic_id,
            patient_id=record.patient_id,
            source_record_type=(
                ClinicalRecordType.MEDICATION_ADMINISTRATION
            ),
            source_record_id=record.id,
            current_status=current_status,
            previous_version=record.record_version,
            amended_by_staff_id=actor.id,
            reason=reason,
            new_content=normalized,
        )
    )

    old_record_status = record.record_status
    old_record_version = record.record_version

    record.record_status = (
        ClinicalRecordStatus.AMENDED.value
    )
    record.record_version = amendment.new_version
    record.latest_amendment_id = amendment.id
    record.latest_amendment = amendment

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="MedicationAdministration",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        description=(
            "Medication administration clinical record amended"
        ),
        old_value={
            "record_status": old_record_status,
            "record_version": old_record_version,
        },
        new_value={
            "record_status": record.record_status,
            "record_version": record.record_version,
            "latest_amendment_id": amendment.id,
            "reason": amendment.reason,
            "new_content_sha256": (
                amendment.new_content_sha256
            ),
        },
        user_id=actor.user_id,
    )

    return (
        record,
        amendment,
    )


def get_medication_administration_amendments(
    administration_id: int,
    *,
    clinic_id: int,
):
    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    return (
        clinical_record_service
        .get_clinical_record_amendments(
            clinic_id=record.clinic_id,
            source_record_type=(
                ClinicalRecordType.MEDICATION_ADMINISTRATION
            ),
            source_record_id=record.id,
        )
    )

def _resolve_idempotent_administration_replay(
    *,
    idempotency_record,
    clinic_id: int,
    administration_id: int,
) -> MedicationAdministration:
    if (
        idempotency_record.entity_type
        != "MedicationAdministration"
        or idempotency_record.entity_id
        is None
    ):
        raise ConflictError(
            "Idempotency record does not reference "
            "a completed medication administration",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        idempotency_record.entity_id
        != administration_id
    ):
        raise ConflictError(
            "Idempotency record references a different "
            "medication administration",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    existing = _load_administration(
        idempotency_record.entity_id,
        clinic_id=clinic_id,
    )

    if (
        existing.status
        != MedicationAdministrationStatus.ADMINISTERED
    ):
        raise ConflictError(
            "Idempotency record does not reference "
            "a completed medication administration",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    return existing

def _resolve_idempotent_emar_terminal_replay(
    *,
    idempotency_record,
    clinic_id: int,
    expected_status: MedicationAdministrationStatus,
    administration_id: int | None = None,
    patient_id: int | None = None,
    admission_id: int | None = None,
    prescription_item_id: int | None = None,
) -> MedicationAdministration:
    if (
        idempotency_record.entity_type
        != "MedicationAdministration"
        or idempotency_record.entity_id is None
    ):
        raise ConflictError(
            "Idempotency record does not reference "
            "a completed medication administration",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        administration_id is not None
        and idempotency_record.entity_id != administration_id
    ):
        raise ConflictError(
            "Idempotency record references a different "
            "medication administration",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    existing = _load_administration(
        idempotency_record.entity_id,
        clinic_id=clinic_id,
    )

    if existing.status != expected_status:
        raise ConflictError(
            "Idempotency record does not reference "
            "the completed requested medication action",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        patient_id is not None
        and existing.patient_id != patient_id
    ):
        raise ConflictError(
            "Idempotency record references another patient",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        admission_id is not None
        and existing.admission_id != admission_id
    ):
        raise ConflictError(
            "Idempotency record references another admission",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        prescription_item_id is not None
        and existing.prescription_item_id
        != prescription_item_id
    ):
        raise ConflictError(
            "Idempotency record references another medication order",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    return existing


def _validate_idempotency_actor(
    actor: Staff,
    idempotency_user_id: int | None,
) -> None:
    if (
        idempotency_user_id is not None
        and actor.user_id != idempotency_user_id
    ):
        raise ValidationError(
            "Idempotency user does not match "
            "the medication administration actor"
        )


def _bind_emar_idempotency(
    idempotency_record,
    record: MedicationAdministration,
) -> None:
    if idempotency_record is None:
        return

    idempotency_record.entity_type = "MedicationAdministration"
    idempotency_record.entity_id = record.id


def _validate_pending_transition(
    record: MedicationAdministration,
) -> None:
    if record.status not in _PENDING_STATUSES:
        raise ConflictError(
            "Medication administration "
            f"is already {record.status.value}"
        )


def _revalidate_record_context(
    record: MedicationAdministration,
    *,
    actor_staff_id: int,
    event_at: datetime,
) -> tuple[Staff, PrescriptionItem]:
    _, _, actor, prescription, item = (
        _get_context_for_event(
            clinic_id=record.clinic_id,
            patient_id=record.patient_id,
            admission_id=record.admission_id,
            prescription_item_id=(
                record.prescription_item_id
            ),
            actor_staff_id=actor_staff_id,
            event_at=event_at,
            lock=True,
        )
    )

    if (
        record.prescription_id
        != prescription.id
    ):
        raise ValidationError(
            "eMAR record prescription does not "
            "match the medication order"
        )

    return (
        actor,
        item,
    )

@transactional
def schedule_medication_administration(
    *,
    clinic_id: int,
    patient_id: int,
    admission_id: int,
    prescription_item_id: int,
    scheduled_at: datetime,
    actor_staff_id: int,
) -> MedicationAdministration:
    scheduled_at = _normalize_event_time(
        scheduled_at,
        field_name="scheduled_at",
        default_now=False,
    )

    (
        _patient,
        admission,
        actor,
        prescription,
        item,
    ) = _get_context_for_event(
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        prescription_item_id=prescription_item_id,
        actor_staff_id=actor_staff_id,
        event_at=scheduled_at,
    )

    if item.is_prn:
        raise ValidationError(
            "PRN medication cannot be created "
            "as a fixed scheduled dose"
        )

    existing = db.session.execute(
        select(
            MedicationAdministration
        ).where(
            MedicationAdministration.admission_id
            == admission.id,
            MedicationAdministration.prescription_item_id
            == item.id,
            MedicationAdministration.scheduled_at
            == scheduled_at,
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            "Medication administration slot "
            "already exists"
        )

    record = MedicationAdministration(
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=scheduled_at,
        status=(
            MedicationAdministrationStatus
            .SCHEDULED
        ),
        record_status=ClinicalRecordStatus.DRAFT.value,
        record_version=1,
        **_copy_order_snapshot(
            item
        ),
    )

    db.session.add(
        record
    )

    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "Medication administration slot "
            "already exists"
        ) from exc

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=None,
        description=(
            "Medication administration "
            f"{record.id} scheduled"
        ),
    )

    return record


@transactional
def mark_medication_administration_due(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    due_at: datetime | None = None,
) -> MedicationAdministration:
    due_at = _normalize_event_time(
        due_at,
        field_name="due_at",
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    if (
        record.status
        != MedicationAdministrationStatus.SCHEDULED
    ):
        raise ConflictError(
            "Only a scheduled medication "
            "administration can become due"
        )

    actor, _item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=due_at,
    )

    old_status = record.status
    record.status = (
        MedicationAdministrationStatus
        .DUE
    )

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} marked due"
        ),
    )

    return record


def _load_barcode_verification_event(
    *,
    verification_event_id: int,
    clinic_id: int,
) -> MedicationAdministrationEvent:
    _validate_positive_id(
        verification_event_id,
        "barcode_verification_event_id",
    )

    event = db.session.execute(
        select(MedicationAdministrationEvent).where(
            MedicationAdministrationEvent.id == verification_event_id,
            MedicationAdministrationEvent.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if event is None:
        raise ConflictError(
            "Barcode verification event could not be resolved in the clinic"
        )

    return event


def _latest_barcode_event_id(
    administration_id: int,
) -> int | None:
    return db.session.execute(
        select(MedicationAdministrationEvent.id)
        .where(
            MedicationAdministrationEvent.medication_administration_id
            == administration_id
        )
        .order_by(
            MedicationAdministrationEvent.scan_timestamp.desc(),
            MedicationAdministrationEvent.id.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _validate_barcode_verification_binding(
    *,
    record: MedicationAdministration,
    item: PrescriptionItem,
    actor: Staff,
    verification_event_id: int,
    administered_at: datetime,
) -> MedicationAdministrationEvent:
    event = _load_barcode_verification_event(
        verification_event_id=verification_event_id,
        clinic_id=record.clinic_id,
    )

    if (
        event.verification_result
        != BarcodeVerificationResult.BARCODE_VERIFIED
        or event.failure_code is not None
        or event.failure_reason is not None
    ):
        raise ConflictError(
            "Medication administration requires a successful barcode verification"
        )

    if not all(
        (
            event.patient_match,
            event.medication_match,
            event.dose_match,
            event.route_match,
            event.time_window_match,
        )
    ):
        raise ConflictError(
            "Barcode verification evidence is incomplete"
        )

    if not (
        event.medication_administration_id == record.id
        and event.patient_id == record.patient_id
        and event.admission_id == record.admission_id
        and event.prescription_id == record.prescription_id
        and event.prescription_item_id == record.prescription_item_id
    ):
        raise ConflictError(
            "Barcode verification does not match the medication administration"
        )

    if event.actor_staff_id != actor.id:
        raise ConflictError(
            "Barcode verification actor does not match the medication administration actor"
        )

    if _latest_barcode_event_id(record.id) != event.id:
        raise ConflictError(
            "Barcode verification is no longer the latest verification attempt"
        )

    if (
        record.verification_result
        != BarcodeVerificationResult.BARCODE_VERIFIED
        or _db_comparable(record.scan_timestamp)
        != _db_comparable(event.scan_timestamp)
        or record.patient_barcode != event.patient_barcode
        or record.medication_barcode != event.medication_barcode
        or record.scan_device != event.scan_device
        or record.barcode_verified_by_staff_id != event.actor_staff_id
    ):
        raise ConflictError(
            "Barcode verification is stale relative to the current eMAR verification snapshot"
        )

    administered_db = _db_comparable(administered_at)
    event_scan_db = _db_comparable(event.scan_timestamp)
    window_end_db = _db_comparable(event.verification_window_end)

    if (
        event_scan_db is None
        or administered_db is None
        or administered_db < event_scan_db
    ):
        raise ConflictError(
            "Medication cannot be administered before barcode verification"
        )

    if window_end_db is None or administered_db > window_end_db:
        raise ConflictError(
            "Barcode verification is outside the permitted administration window"
        )

    if (
        event.source_order_version != record.source_order_version
        or event.expected_drug_id != item.drug_id
        or event.expected_dose_value != item.dose_value
        or event.expected_dose_unit != item.dose_unit
        or event.expected_route_code != item.route_code
    ):
        raise ConflictError(
            "Medication order changed after barcode verification"
        )

    return event


def administer_barcode_verified_medication(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    barcode_verification_event_id: int,
    administered_at: datetime | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> MedicationAdministration:
    _validate_positive_id(
        barcode_verification_event_id,
        "barcode_verification_event_id",
    )

    return administer_medication(
        administration_id=administration_id,
        clinic_id=clinic_id,
        actor_staff_id=actor_staff_id,
        administered_at=administered_at,
        notes=notes,
        idempotency_key=idempotency_key,
        idempotency_user_id=idempotency_user_id,
        barcode_verification_event_id=barcode_verification_event_id,
    )


@transactional
def administer_medication(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    administered_at: datetime | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
    barcode_verification_event_id: int | None = None,
) -> MedicationAdministration:
    requested_administered_at = administered_at

    administered_at = _normalize_event_time(
        administered_at,
        field_name="administered_at",
    )

    notes = _normalize_text(
        notes,
        field_name="notes",
        max_length=4000,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        request_payload = {
            "administration_id": administration_id,
            "actor_staff_id": actor_staff_id,
            "administered_at": requested_administered_at,
            "notes": notes,
        }

        if barcode_verification_event_id is not None:
            request_payload["barcode_verification_event_id"] = (
                barcode_verification_event_id
            )

        idempotency_record, created = (
            reserve_idempotency_operation(
                clinic_id=clinic_id,
                user_id=idempotency_user_id,
                operation="emar.administer",
                idempotency_key=idempotency_key,
                request_payload=request_payload,
            )
        )

        if not created:
            return _resolve_idempotent_administration_replay(
                idempotency_record=idempotency_record,
                clinic_id=clinic_id,
                administration_id=administration_id,
            )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
        lock=True,
    )

    _validate_pending_transition(
        record
    )

    actor, item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=administered_at,
    )

    if (
        idempotency_user_id is not None
        and actor.user_id != idempotency_user_id
    ):
        raise ValidationError(
            "Idempotency user does not match "
            "the medication administration actor"
        )

    barcode_verification_event = None

    if barcode_verification_event_id is not None:
        barcode_verification_event = _validate_barcode_verification_binding(
            record=record,
            item=item,
            actor=actor,
            verification_event_id=barcode_verification_event_id,
            administered_at=administered_at,
        )

    snapshot = _copy_order_snapshot(
        item
    )

    if (
        record.source_order_version
        != snapshot["source_order_version"]
        or record.dose_value
        != snapshot["dose_value"]
        or record.dose_unit
        != snapshot["dose_unit"]
        or record.route_code
        != snapshot["route_code"]
    ):
        raise ConflictError(
            "Medication order changed after "
            "the eMAR slot was created"
        )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus.ADMINISTERED
    )
    record.administered_at = administered_at
    record.administered_by_staff_id = actor.id
    record.notes = notes

    _finalize_medication_administration_record(
        record,
        actor=actor,
        finalized_at=administered_at,
    )

    if barcode_verification_event is not None:
        record.barcode_verification_event_id = barcode_verification_event.id

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} administered"
        ),
    )

    if idempotency_record is not None:
        idempotency_record.entity_type = (
            "MedicationAdministration"
        )
        idempotency_record.entity_id = record.id

    return record

@transactional
def administer_prn_medication(
    *,
    clinic_id: int,
    patient_id: int,
    admission_id: int,
    prescription_item_id: int,
    actor_staff_id: int,
    administered_at: datetime | None = None,
    notes: str | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> MedicationAdministration:
    requested_administered_at = administered_at

    notes = _normalize_text(
        notes,
        field_name="notes",
        max_length=4000,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = (
            reserve_idempotency_operation(
                clinic_id=clinic_id,
                user_id=idempotency_user_id,
                operation="emar.prn_administer",
                idempotency_key=idempotency_key,
                request_payload={
                    "patient_id": patient_id,
                    "admission_id": admission_id,
                    "prescription_item_id": prescription_item_id,
                    "actor_staff_id": actor_staff_id,
                    "administered_at": requested_administered_at,
                    "notes": notes,
                },
            )
        )

        if not created:
            return _resolve_idempotent_emar_terminal_replay(
                idempotency_record=idempotency_record,
                clinic_id=clinic_id,
                expected_status=MedicationAdministrationStatus.ADMINISTERED,
                patient_id=patient_id,
                admission_id=admission_id,
                prescription_item_id=prescription_item_id,
            )

    administered_at = _normalize_event_time(
        administered_at,
        field_name="administered_at",
    )

    (
        _patient,
        admission,
        actor,
        prescription,
        item,
    ) = _get_context_for_event(
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        prescription_item_id=prescription_item_id,
        actor_staff_id=actor_staff_id,
        event_at=administered_at,
    )

    if not item.is_prn:
        raise ValidationError(
            "PRN administration requires "
            "a PRN medication order"
        )

    _validate_idempotency_actor(
        actor,
        idempotency_user_id,
    )

    record = MedicationAdministration(
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission.id,
        prescription_id=prescription.id,
        prescription_item_id=item.id,
        scheduled_at=None,
        status=(
            MedicationAdministrationStatus
            .ADMINISTERED
        ),
        record_status=ClinicalRecordStatus.FINAL.value,
        record_version=1,
        finalized_by_staff_id=actor.id,
        finalized_at=administered_at,
        administered_at=administered_at,
        administered_by_staff_id=actor.id,
        notes=notes,
        **_copy_order_snapshot(
            item
        ),
    )

    db.session.add(
        record
    )
    db.session.flush()

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=None,
        description=(
            "PRN medication administration "
            f"{record.id} administered"
        ),
    )

    _bind_emar_idempotency(
        idempotency_record,
        record,
    )

    return record


@transactional
def hold_medication(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
    held_at: datetime | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> MedicationAdministration:
    requested_held_at = held_at

    reason = _normalize_text(
        reason,
        field_name="hold_reason",
        max_length=500,
        required=True,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = (
            reserve_idempotency_operation(
                clinic_id=clinic_id,
                user_id=idempotency_user_id,
                operation="emar.hold",
                idempotency_key=idempotency_key,
                request_payload={
                    "administration_id": administration_id,
                    "actor_staff_id": actor_staff_id,
                    "reason": reason,
                    "held_at": requested_held_at,
                },
            )
        )

        if not created:
            return _resolve_idempotent_emar_terminal_replay(
                idempotency_record=idempotency_record,
                clinic_id=clinic_id,
                expected_status=MedicationAdministrationStatus.HELD,
                administration_id=administration_id,
            )

    held_at = _normalize_event_time(
        held_at,
        field_name="held_at",
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    _validate_pending_transition(
        record
    )

    actor, _item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=held_at,
    )

    _validate_idempotency_actor(
        actor,
        idempotency_user_id,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .HELD
    )
    record.held_at = held_at
    record.held_by_staff_id = actor.id
    record.hold_reason = reason

    _finalize_medication_administration_record(
        record,
        actor=actor,
        finalized_at=held_at,
    )

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} held"
        ),
    )

    _bind_emar_idempotency(
        idempotency_record,
        record,
    )

    return record


@transactional
def record_medication_refusal(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
    refused_at: datetime | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> MedicationAdministration:
    requested_refused_at = refused_at

    reason = _normalize_text(
        reason,
        field_name="refusal_reason",
        max_length=500,
        required=True,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = (
            reserve_idempotency_operation(
                clinic_id=clinic_id,
                user_id=idempotency_user_id,
                operation="emar.refuse",
                idempotency_key=idempotency_key,
                request_payload={
                    "administration_id": administration_id,
                    "actor_staff_id": actor_staff_id,
                    "reason": reason,
                    "refused_at": requested_refused_at,
                },
            )
        )

        if not created:
            return _resolve_idempotent_emar_terminal_replay(
                idempotency_record=idempotency_record,
                clinic_id=clinic_id,
                expected_status=MedicationAdministrationStatus.REFUSED,
                administration_id=administration_id,
            )

    refused_at = _normalize_event_time(
        refused_at,
        field_name="refused_at",
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    _validate_pending_transition(
        record
    )

    actor, _item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=refused_at,
    )

    _validate_idempotency_actor(
        actor,
        idempotency_user_id,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .REFUSED
    )
    record.refused_at = refused_at
    record.refused_recorded_by_staff_id = (
        actor.id
    )
    record.refusal_reason = reason

    _finalize_medication_administration_record(
        record,
        actor=actor,
        finalized_at=refused_at,
    )

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} refused"
        ),
    )

    _bind_emar_idempotency(
        idempotency_record,
        record,
    )

    return record


@transactional
def mark_medication_missed(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
    missed_at: datetime | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> MedicationAdministration:
    requested_missed_at = missed_at

    reason = _normalize_text(
        reason,
        field_name="missed_reason",
        max_length=500,
        required=True,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        idempotency_record, created = (
            reserve_idempotency_operation(
                clinic_id=clinic_id,
                user_id=idempotency_user_id,
                operation="emar.miss",
                idempotency_key=idempotency_key,
                request_payload={
                    "administration_id": administration_id,
                    "actor_staff_id": actor_staff_id,
                    "reason": reason,
                    "missed_at": requested_missed_at,
                },
            )
        )

        if not created:
            return _resolve_idempotent_emar_terminal_replay(
                idempotency_record=idempotency_record,
                clinic_id=clinic_id,
                expected_status=MedicationAdministrationStatus.MISSED,
                administration_id=administration_id,
            )

    missed_at = _normalize_event_time(
        missed_at,
        field_name="missed_at",
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    _validate_pending_transition(
        record
    )

    actor, _item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=missed_at,
    )

    _validate_idempotency_actor(
        actor,
        idempotency_user_id,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .MISSED
    )
    record.missed_at = missed_at
    record.missed_reason = reason

    _finalize_medication_administration_record(
        record,
        actor=actor,
        finalized_at=missed_at,
    )

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} marked missed"
        ),
    )

    _bind_emar_idempotency(
        idempotency_record,
        record,
    )

    return record


@transactional
def cancel_medication_administration(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
    cancelled_at: datetime | None = None,
) -> MedicationAdministration:
    cancelled_at = _normalize_event_time(
        cancelled_at,
        field_name="cancelled_at",
    )

    reason = _normalize_text(
        reason,
        field_name="cancel_reason",
        max_length=500,
        required=True,
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    _validate_pending_transition(
        record
    )

    actor, _item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=cancelled_at,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .CANCELLED
    )
    record.cancelled_at = cancelled_at
    record.cancel_reason = reason

    _finalize_medication_administration_record(
        record,
        actor=actor,
        finalized_at=cancelled_at,
    )

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} cancelled"
        ),
    )

    return record
def _validate_emar_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page < 1
    ):
        raise ValidationError(
            "page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page < 1
        or per_page > 500
    ):
        raise ValidationError(
            "per_page must be between 1 and 500"
        )

    return (
        page,
        per_page,
    )


def get_medication_administration(
    administration_id: int,
    *,
    clinic_id: int,
) -> MedicationAdministration:
    _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    return _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )


def list_medication_administrations_for_admission(
    *,
    admission_id: int,
    clinic_id: int,
    status: MedicationAdministrationStatus | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    _validate_positive_id(
        admission_id,
        "admission_id",
    )

    page, per_page = (
        _validate_emar_pagination(
            page,
            per_page,
        )
    )

    admission = db.session.get(
        Admission,
        admission_id,
    )

    if admission is None:
        raise NotFoundError(
            f"Admission {admission_id} not found"
        )

    if (
        admission.patient is None
        or admission.patient.clinic_id != clinic_id
    ):
        raise ValidationError(
            "Admission does not belong to the clinic"
        )

    query = (
        MedicationAdministration.query
        .filter(
            MedicationAdministration.clinic_id
            == clinic_id,
            MedicationAdministration.admission_id
            == admission_id,
        )
    )

    if status is not None:
        if not isinstance(
            status,
            MedicationAdministrationStatus,
        ):
            try:
                status = (
                    MedicationAdministrationStatus(
                        status
                    )
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise ValidationError(
                    "Invalid medication administration status"
                ) from exc

        query = query.filter(
            MedicationAdministration.status
            == status
        )

    total = query.count()

    items = (
        query
        .order_by(
            MedicationAdministration.created_at.desc(),
            MedicationAdministration.id.desc(),
        )
        .limit(
            per_page
        )
        .offset(
            (page - 1)
            * per_page
        )
        .all()
    )

    return {
        "items": items,
        "total": int(
            total
            or 0
        ),
        "page": page,
        "per_page": per_page,
    }
