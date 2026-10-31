from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import (
    ClinicalRecordStatus,
    ClinicalRecordType,
)
from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationDecision,
    MedicationReconciliationEvent,
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
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)
from app.modules.clinical_records.services import (
    clinical_record_service,
)
from app.modules.medication_reconciliation.models.medication_reconciliation_model import (
    MedicationReconciliation,
    MedicationReconciliationItem,
)
from app.modules.prescription.models.prescription_model import (
    PrescriptionItem,
)
from app.modules.staff.services.staff_service import (
    get_staff,
)
from app.modules.ward.models.ward_model import (
    Admission,
    WardTransfer,
)


_WRITE_ROLES = {
    Role.DOCTOR,
    Role.PHARMACIST,
}

_UNRESOLVED_DECISIONS = {
    MedicationReconciliationDecision.VERIFY,
    MedicationReconciliationDecision.UNKNOWN,
}

_ITEM_FIELDS = (
    "source_prescription_item_id",
    "medication_name",
    "decision",
    "replacement_medication_name",
    "dose_text",
    "route_text",
    "frequency_text",
    "instructions",
    "rationale",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _positive_id(
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


def _text(
    value,
    field_name: str,
    max_length: int,
    *,
    required: bool = False,
):
    if value is None:
        if required:
            raise ValidationError(
                f"{field_name} is required"
            )
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string or null"
        )

    value = value.strip()

    if not value:
        if required:
            raise ValidationError(
                f"{field_name} cannot be blank"
            )
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _event_type(
    value,
) -> MedicationReconciliationEvent:
    try:
        return MedicationReconciliationEvent(
            getattr(
                value,
                "value",
                value,
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid medication reconciliation event"
        ) from exc


def _decision(
    value,
) -> MedicationReconciliationDecision:
    try:
        return MedicationReconciliationDecision(
            getattr(
                value,
                "value",
                value,
            )
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid medication reconciliation decision"
        ) from exc


def _record_status(
    record: MedicationReconciliation,
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
            f"Medication reconciliation {record.id} "
            "has invalid clinical record state"
        ) from exc


def _actor(
    staff_id: int,
    clinic_id: int,
):
    staff = get_staff(
        _positive_id(
            staff_id,
            "actor_staff_id",
        ),
        clinic_id=clinic_id,
    )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Medication reconciliation actor must be active"
        )

    if (
        staff.user is None
        or not staff.user.is_active
    ):
        raise ValidationError(
            "Medication reconciliation actor user "
            "must be active"
        )

    if staff.user.role not in _WRITE_ROLES:
        raise ConflictError(
            "Medication reconciliation writes require "
            "a doctor or pharmacist"
        )

    return staff


def _admission(
    admission_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> Admission:
    stmt = (
        select(Admission)
        .where(
            Admission.id
            == _positive_id(
                admission_id,
                "admission_id",
            )
        )
    )

    if lock:
        stmt = stmt.with_for_update()

    admission = (
        db.session.execute(
            stmt
        )
        .scalar_one_or_none()
    )

    if admission is None:
        raise NotFoundError(
            "Admission not found"
        )

    if (
        admission.bed is None
        or admission.bed.ward is None
        or admission.bed.ward.clinic_id
        != clinic_id
    ):
        raise NotFoundError(
            "Admission not found"
        )

    return admission


def _event_identity(
    *,
    admission: Admission,
    event_type: MedicationReconciliationEvent,
    ward_transfer_id: int | None,
) -> tuple[str, WardTransfer | None]:
    if event_type == MedicationReconciliationEvent.TRANSFER:
        transfer_id = _positive_id(
            ward_transfer_id,
            "ward_transfer_id",
        )

        transfer = db.session.get(
            WardTransfer,
            transfer_id,
        )

        if (
            transfer is None
            or transfer.admission_id
            != admission.id
        ):
            raise ValidationError(
                "ward_transfer_id must belong "
                "to the admission"
            )

        return (
            f"transfer:{transfer.id}",
            transfer,
        )

    if ward_transfer_id is not None:
        raise ValidationError(
            "ward_transfer_id is only valid "
            "for transfer reconciliation"
        )

    if event_type == MedicationReconciliationEvent.ADMISSION:
        return (
            f"admission:{admission.id}",
            None,
        )

    return (
        f"discharge:{admission.id}",
        None,
    )


def _source_item(
    source_id: int,
    *,
    clinic_id: int,
    patient_id: int,
) -> PrescriptionItem:
    source = db.session.get(
        PrescriptionItem,
        _positive_id(
            source_id,
            "source_prescription_item_id",
        ),
    )

    if (
        source is None
        or source.prescription is None
        or source.prescription.clinic_id
        != clinic_id
        or source.prescription.patient_id
        != patient_id
    ):
        raise ValidationError(
            "source_prescription_item_id must belong "
            "to the reconciliation patient and clinic"
        )

    return source


def _normalize_item(
    item: dict,
    *,
    clinic_id: int,
    patient_id: int,
) -> dict:
    if not isinstance(item, dict):
        raise ValidationError(
            "Each reconciliation item must be an object"
        )

    if set(item) != set(_ITEM_FIELDS):
        raise ValidationError(
            "Each reconciliation item must contain "
            "the complete medication decision"
        )

    source_id = item[
        "source_prescription_item_id"
    ]

    if source_id is not None:
        _source_item(
            source_id,
            clinic_id=clinic_id,
            patient_id=patient_id,
        )

    decision = _decision(
        item["decision"]
    )

    replacement = _text(
        item["replacement_medication_name"],
        "replacement_medication_name",
        255,
    )

    if (
        decision
        == MedicationReconciliationDecision.REPLACE
    ):
        if replacement is None:
            raise ValidationError(
                "replacement_medication_name is required "
                "for replace decisions"
            )
    elif replacement is not None:
        raise ValidationError(
            "replacement_medication_name is only valid "
            "for replace decisions"
        )

    return {
        "source_prescription_item_id": source_id,
        "medication_name": _text(
            item["medication_name"],
            "medication_name",
            255,
            required=True,
        ),
        "decision": decision.value,
        "replacement_medication_name": replacement,
        "dose_text": _text(
            item["dose_text"],
            "dose_text",
            255,
        ),
        "route_text": _text(
            item["route_text"],
            "route_text",
            128,
        ),
        "frequency_text": _text(
            item["frequency_text"],
            "frequency_text",
            255,
        ),
        "instructions": _text(
            item["instructions"],
            "instructions",
            1000,
        ),
        "rationale": _text(
            item["rationale"],
            "rationale",
            1000,
        ),
    }


def _normalize_items(
    items,
    *,
    clinic_id: int,
    patient_id: int,
) -> list[dict]:
    if not isinstance(items, list):
        raise ValidationError(
            "items must be a list"
        )

    if len(items) > 500:
        raise ValidationError(
            "items cannot contain more than 500 medications"
        )

    normalized = [
        _normalize_item(
            item,
            clinic_id=clinic_id,
            patient_id=patient_id,
        )
        for item in items
    ]

    source_ids = [
        item["source_prescription_item_id"]
        for item in normalized
        if item["source_prescription_item_id"]
        is not None
    ]

    if len(source_ids) != len(set(source_ids)):
        raise ValidationError(
            "A source prescription item cannot appear "
            "more than once in one reconciliation"
        )

    normalized.sort(
        key=lambda item: (
            item["source_prescription_item_id"]
            if item["source_prescription_item_id"]
            is not None
            else 0,
            item["medication_name"].lower(),
            item["decision"],
        )
    )

    return normalized


def _normalize_document(
    *,
    clinic_id: int,
    patient_id: int,
    no_medications_reported,
    notes,
    items,
    require_resolved: bool,
) -> dict:
    if not isinstance(
        no_medications_reported,
        bool,
    ):
        raise ValidationError(
            "no_medications_reported must be boolean"
        )

    normalized_items = _normalize_items(
        items,
        clinic_id=clinic_id,
        patient_id=patient_id,
    )

    if (
        no_medications_reported
        and normalized_items
    ):
        raise ValidationError(
            "no_medications_reported cannot be true "
            "when medication items are present"
        )

    if require_resolved:
        if (
            not no_medications_reported
            and not normalized_items
        ):
            raise ValidationError(
                "Finalized medication reconciliation "
                "requires at least one medication or an "
                "explicit no-medications declaration"
            )

        unresolved = [
            item
            for item in normalized_items
            if MedicationReconciliationDecision(
                item["decision"]
            ) in _UNRESOLVED_DECISIONS
        ]

        if unresolved:
            raise ConflictError(
                "Medication reconciliation contains "
                "unresolved VERIFY or UNKNOWN decisions"
            )

    return {
        "no_medications_reported": (
            no_medications_reported
        ),
        "notes": _text(
            notes,
            "notes",
            10000,
        ),
        "items": normalized_items,
    }


def _event_document(
    record: MedicationReconciliation,
) -> dict:
    items = [
        {
            "source_prescription_item_id": (
                item.source_prescription_item_id
            ),
            "medication_name": item.medication_name,
            "decision": item.decision,
            "replacement_medication_name": (
                item.replacement_medication_name
            ),
            "dose_text": item.dose_text,
            "route_text": item.route_text,
            "frequency_text": item.frequency_text,
            "instructions": item.instructions,
            "rationale": item.rationale,
        }
        for item in record.items
    ]

    return _normalize_document(
        clinic_id=record.clinic_id,
        patient_id=record.patient_id,
        no_medications_reported=(
            record.no_medications_reported
        ),
        notes=record.notes,
        items=items,
        require_resolved=False,
    )


def get_medication_reconciliation_effective_document(
    record: MedicationReconciliation,
) -> dict:
    status = _record_status(
        record
    )

    if status != ClinicalRecordStatus.AMENDED:
        return _event_document(
            record
        )

    amendment = record.latest_amendment

    if amendment is None:
        raise ConflictError(
            f"Medication reconciliation {record.id} "
            "is amended but has no latest amendment"
        )

    if (
        amendment.clinic_id != record.clinic_id
        or amendment.patient_id != record.patient_id
        or amendment.source_record_type
        != ClinicalRecordType.MEDICATION_RECONCILIATION.value
        or amendment.source_record_id != record.id
        or amendment.new_version
        != record.record_version
    ):
        raise ConflictError(
            f"Medication reconciliation {record.id} "
            "latest amendment linkage is invalid"
        )

    return dict(
        amendment.new_content
    )


def _replace_draft_items(
    record: MedicationReconciliation,
    items: list[dict],
) -> None:
    record.items.clear()
    db.session.flush()

    for item in items:
        record.items.append(
            MedicationReconciliationItem(
                **item
            )
        )

    db.session.flush()


def _get_record(
    reconciliation_id: int,
    *,
    clinic_id: int,
    lock: bool = False,
) -> MedicationReconciliation:
    stmt = (
        select(MedicationReconciliation)
        .where(
            MedicationReconciliation.id
            == _positive_id(
                reconciliation_id,
                "reconciliation_id",
            ),
            MedicationReconciliation.clinic_id
            == clinic_id,
        )
    )

    if lock:
        stmt = stmt.with_for_update()

    record = (
        db.session.execute(
            stmt
        )
        .scalar_one_or_none()
    )

    if record is None:
        raise NotFoundError(
            "Medication reconciliation not found"
        )

    return record


@transactional
def create_medication_reconciliation(
    *,
    clinic_id: int,
    admission_id: int,
    event_type,
    actor_staff_id: int,
    ward_transfer_id: int | None = None,
    no_medications_reported: bool = False,
    notes: str | None = None,
    items: list[dict] | None = None,
) -> MedicationReconciliation:
    ensure_clinic_active(
        clinic_id
    )

    actor = _actor(
        actor_staff_id,
        clinic_id,
    )

    admission = _admission(
        admission_id,
        clinic_id,
        lock=True,
    )

    if admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError(
            "Medication reconciliation requires "
            "an active admission"
        )

    event_type = _event_type(
        event_type
    )

    event_identity, transfer = _event_identity(
        admission=admission,
        event_type=event_type,
        ward_transfer_id=ward_transfer_id,
    )

    existing = (
        db.session.execute(
            select(
                MedicationReconciliation.id
            ).where(
                MedicationReconciliation.clinic_id
                == clinic_id,
                MedicationReconciliation.event_identity
                == event_identity,
            )
        )
        .scalar_one_or_none()
    )

    if existing is not None:
        raise ConflictError(
            "Medication reconciliation already exists "
            "for this clinical event"
        )

    document = _normalize_document(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        no_medications_reported=(
            no_medications_reported
        ),
        notes=notes,
        items=list(items or []),
        require_resolved=False,
    )

    record = MedicationReconciliation(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        ward_transfer_id=(
            transfer.id
            if transfer is not None
            else None
        ),
        event_type=event_type.value,
        event_identity=event_identity,
        created_by_staff_id=actor.id,
        no_medications_reported=(
            document["no_medications_reported"]
        ),
        notes=document["notes"],
        record_status=(
            ClinicalRecordStatus.DRAFT.value
        ),
        record_version=1,
    )

    db.session.add(
        record
    )

    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            "Medication reconciliation already exists "
            "for this clinical event"
        ) from exc

    _replace_draft_items(
        record,
        document["items"],
    )

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="MedicationReconciliation",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        user_id=actor.user_id,
        description=(
            "Medication reconciliation draft created"
        ),
        new_value={
            "admission_id": record.admission_id,
            "event_type": record.event_type,
            "event_identity": record.event_identity,
            "document": document,
        },
    )

    return record


@transactional
def update_medication_reconciliation_draft(
    reconciliation_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    no_medications_reported: bool,
    notes: str | None,
    items: list[dict],
) -> MedicationReconciliation:
    record = _get_record(
        reconciliation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    actor = _actor(
        actor_staff_id,
        clinic_id,
    )

    if record.created_by_staff_id != actor.id:
        raise ConflictError(
            "Only the reconciliation author can edit "
            "the draft"
        )

    if (
        _record_status(record)
        != ClinicalRecordStatus.DRAFT
    ):
        raise ConflictError(
            "Finalized medication reconciliation is "
            "immutable; use the amendment workflow"
        )

    old_document = _event_document(
        record
    )

    new_document = _normalize_document(
        clinic_id=record.clinic_id,
        patient_id=record.patient_id,
        no_medications_reported=(
            no_medications_reported
        ),
        notes=notes,
        items=items,
        require_resolved=False,
    )

    record.no_medications_reported = (
        new_document["no_medications_reported"]
    )
    record.notes = new_document["notes"]

    _replace_draft_items(
        record,
        new_document["items"],
    )

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="MedicationReconciliation",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        user_id=actor.user_id,
        description=(
            "Medication reconciliation draft updated"
        ),
        old_value={
            "document": old_document,
        },
        new_value={
            "document": new_document,
        },
    )

    return record


@transactional
def finalize_medication_reconciliation(
    reconciliation_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    expected_version: int,
) -> MedicationReconciliation:
    record = _get_record(
        reconciliation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    actor = _actor(
        actor_staff_id,
        clinic_id,
    )

    if record.created_by_staff_id != actor.id:
        raise ConflictError(
            "Only the reconciliation author can finalize "
            "the record"
        )

    if (
        record.record_version
        != _positive_id(
            expected_version,
            "expected_version",
        )
    ):
        raise ConflictError(
            f"Medication reconciliation {record.id} "
            "record version mismatch"
        )

    clinical_record_service.validate_finalization_transition(
        _record_status(
            record
        )
    )

    document = _normalize_document(
        clinic_id=record.clinic_id,
        patient_id=record.patient_id,
        no_medications_reported=(
            record.no_medications_reported
        ),
        notes=record.notes,
        items=_event_document(
            record
        )["items"],
        require_resolved=True,
    )

    record.record_status = (
        ClinicalRecordStatus.FINAL.value
    )
    record.record_version = 1
    record.finalized_by_staff_id = actor.id
    record.finalized_at = _utcnow()
    record.latest_amendment_id = None

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="MedicationReconciliation",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        user_id=actor.user_id,
        description=(
            "Medication reconciliation finalized"
        ),
        old_value={
            "record_status": (
                ClinicalRecordStatus.DRAFT.value
            ),
            "record_version": 1,
        },
        new_value={
            "record_status": (
                ClinicalRecordStatus.FINAL.value
            ),
            "record_version": 1,
            "document": document,
        },
    )

    return record


@transactional
def amend_medication_reconciliation(
    reconciliation_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    expected_version: int,
    reason: str,
    document: dict,
):
    record = _get_record(
        reconciliation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    actor = _actor(
        actor_staff_id,
        clinic_id,
    )

    if record.created_by_staff_id != actor.id:
        raise ConflictError(
            "Only the reconciliation author can amend "
            "the record"
        )

    expected_version = _positive_id(
        expected_version,
        "expected_version",
    )

    if record.record_version != expected_version:
        raise ConflictError(
            f"Medication reconciliation {record.id} "
            "record version mismatch"
        )

    current_status = _record_status(
        record
    )

    clinical_record_service.validate_amendment_transition(
        current_status
    )

    if not isinstance(document, dict):
        raise ValidationError(
            "document must be an object"
        )

    if set(document) != {
        "no_medications_reported",
        "notes",
        "items",
    }:
        raise ValidationError(
            "Medication reconciliation amendment "
            "must contain the complete clinical document"
        )

    normalized = _normalize_document(
        clinic_id=record.clinic_id,
        patient_id=record.patient_id,
        no_medications_reported=(
            document["no_medications_reported"]
        ),
        notes=document["notes"],
        items=document["items"],
        require_resolved=True,
    )

    effective_before = (
        get_medication_reconciliation_effective_document(
            record
        )
    )

    if normalized == effective_before:
        raise ValidationError(
            "Medication reconciliation amendment "
            "must change the effective clinical document"
        )

    amendment = (
        clinical_record_service
        .append_clinical_record_amendment(
            clinic_id=record.clinic_id,
            patient_id=record.patient_id,
            source_record_type=(
                ClinicalRecordType.MEDICATION_RECONCILIATION
            ),
            source_record_id=record.id,
            current_status=current_status,
            previous_version=record.record_version,
            amended_by_staff_id=actor.id,
            reason=reason,
            new_content=normalized,
        )
    )

    old_status = record.record_status
    old_version = record.record_version

    record.record_status = (
        ClinicalRecordStatus.AMENDED.value
    )
    record.record_version = (
        amendment.new_version
    )
    record.latest_amendment_id = (
        amendment.id
    )
    record.latest_amendment = (
        amendment
    )

    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="MedicationReconciliation",
        entity_id=record.id,
        clinic_id=record.clinic_id,
        user_id=actor.user_id,
        description=(
            "Medication reconciliation amended"
        ),
        old_value={
            "record_status": old_status,
            "record_version": old_version,
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
    )

    return (
        record,
        amendment,
    )


def get_medication_reconciliation(
    reconciliation_id: int,
    *,
    clinic_id: int,
) -> MedicationReconciliation:
    return _get_record(
        reconciliation_id,
        clinic_id=clinic_id,
    )


def get_medication_reconciliation_amendments(
    reconciliation_id: int,
    *,
    clinic_id: int,
):
    record = _get_record(
        reconciliation_id,
        clinic_id=clinic_id,
    )

    return (
        clinical_record_service
        .get_clinical_record_amendments(
            clinic_id=record.clinic_id,
            source_record_type=(
                ClinicalRecordType.MEDICATION_RECONCILIATION
            ),
            source_record_id=record.id,
        )
    )


def get_reconciliation_for_event(
    *,
    clinic_id: int,
    admission_id: int,
    event_type,
    ward_transfer_id: int | None = None,
) -> MedicationReconciliation:
    admission = _admission(
        admission_id,
        clinic_id,
    )

    event_type = _event_type(
        event_type
    )

    event_identity, _ = _event_identity(
        admission=admission,
        event_type=event_type,
        ward_transfer_id=ward_transfer_id,
    )

    record = (
        db.session.execute(
            select(
                MedicationReconciliation
            ).where(
                MedicationReconciliation.clinic_id
                == clinic_id,
                MedicationReconciliation.event_identity
                == event_identity,
            )
        )
        .scalar_one_or_none()
    )

    if record is None:
        raise NotFoundError(
            "Medication reconciliation not found "
            "for clinical event"
        )

    return record


def get_finalized_reconciliation_for_event(
    *,
    clinic_id: int,
    admission_id: int,
    event_type,
    ward_transfer_id: int | None = None,
) -> MedicationReconciliation:
    record = get_reconciliation_for_event(
        clinic_id=clinic_id,
        admission_id=admission_id,
        event_type=event_type,
        ward_transfer_id=ward_transfer_id,
    )

    if _record_status(
        record
    ) not in {
        ClinicalRecordStatus.FINAL,
        ClinicalRecordStatus.AMENDED,
    }:
        raise ConflictError(
            "Medication reconciliation for clinical event "
            "is not finalized"
        )

    # Revalidate the effective content so downstream workflows,
    # especially Gate 12 discharge, cannot consume unresolved data.
    effective = (
        get_medication_reconciliation_effective_document(
            record
        )
    )

    _normalize_document(
        clinic_id=record.clinic_id,
        patient_id=record.patient_id,
        no_medications_reported=(
            effective["no_medications_reported"]
        ),
        notes=effective["notes"],
        items=effective["items"],
        require_resolved=True,
    )

    return record
