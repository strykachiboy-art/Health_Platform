from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emar_enums import MedicationAdministrationStatus
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
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission


AUTHORIZED_EMAR_ROLES = (
    Role.DOCTOR,
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
) -> Admission:
    _validate_positive_id(
        admission_id,
        "admission_id",
    )

    admission = db.session.get(
        Admission,
        admission_id,
    )

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
) -> tuple[Prescription, PrescriptionItem]:
    _validate_positive_id(
        prescription_item_id,
        "prescription_item_id",
    )

    item = db.session.get(
        PrescriptionItem,
        prescription_item_id,
    )

    if item is None:
        raise NotFoundError(
            "Prescription item "
            f"{prescription_item_id} not found"
        )

    prescription = item.prescription

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
) -> MedicationAdministration:
    _validate_positive_id(
        administration_id,
        "administration_id",
    )

    record = db.session.get(
        MedicationAdministration,
        administration_id,
    )

    if record is None:
        raise NotFoundError(
            "Medication administration "
            f"{administration_id} not found"
        )

    if record.clinic_id != clinic_id:
        raise ValidationError(
            "Medication administration does not "
            "belong to the clinic"
        )

    return record


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


@transactional
def administer_medication(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    administered_at: datetime | None = None,
    notes: str | None = None,
) -> MedicationAdministration:
    administered_at = _normalize_event_time(
        administered_at,
        field_name="administered_at",
    )

    notes = _normalize_text(
        notes,
        field_name="notes",
        max_length=4000,
    )

    record = _load_administration(
        administration_id,
        clinic_id=clinic_id,
    )

    _validate_pending_transition(
        record
    )

    actor, item = _revalidate_record_context(
        record,
        actor_staff_id=actor_staff_id,
        event_at=administered_at,
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
        MedicationAdministrationStatus
        .ADMINISTERED
    )
    record.administered_at = (
        administered_at
    )
    record.administered_by_staff_id = (
        actor.id
    )
    record.notes = notes

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} administered"
        ),
    )

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
) -> MedicationAdministration:
    administered_at = _normalize_event_time(
        administered_at,
        field_name="administered_at",
    )

    notes = _normalize_text(
        notes,
        field_name="notes",
        max_length=4000,
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

    return record


@transactional
def hold_medication(
    *,
    administration_id: int,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
    held_at: datetime | None = None,
) -> MedicationAdministration:
    held_at = _normalize_event_time(
        held_at,
        field_name="held_at",
    )

    reason = _normalize_text(
        reason,
        field_name="hold_reason",
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
        event_at=held_at,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .HELD
    )
    record.held_at = held_at
    record.held_by_staff_id = actor.id
    record.hold_reason = reason

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} held"
        ),
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
) -> MedicationAdministration:
    refused_at = _normalize_event_time(
        refused_at,
        field_name="refused_at",
    )

    reason = _normalize_text(
        reason,
        field_name="refusal_reason",
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
        event_at=refused_at,
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

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} refused"
        ),
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
) -> MedicationAdministration:
    missed_at = _normalize_event_time(
        missed_at,
        field_name="missed_at",
    )

    reason = _normalize_text(
        reason,
        field_name="missed_reason",
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
        event_at=missed_at,
    )

    old_status = record.status

    record.status = (
        MedicationAdministrationStatus
        .MISSED
    )
    record.missed_at = missed_at
    record.missed_reason = reason

    _audit_status_change(
        record=record,
        actor=actor,
        old_status=old_status,
        description=(
            "Medication administration "
            f"{record.id} marked missed"
        ),
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
