from datetime import datetime, timezone

from sqlalchemy import or_

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.appointment_enums import AppointmentStatus
from app.core.enums.consultation_enums import (
    ConsultationStatus,
    ConsultationType,
)
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
from app.core.utils.decorators import transactional

from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.clinical_records.services import (
    clinical_record_service,
)

from app.modules.consultation.models.consultation_model import (
    Consultation,
    ConsultationTemplate,
)

from app.modules.patient.services.patient_service import get_patient
from app.modules.staff.services.staff_service import get_staff


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if page <= 0:
        raise ValidationError(
            "Page must be greater than 0"
        )

    if per_page <= 0:
        raise ValidationError(
            "per_page must be greater than 0"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"per_page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _pagination_metadata(
    pagination,
) -> dict:
    return {
        "page": pagination.page,
        "per_page": pagination.per_page,
        "total": pagination.total,
        "pages": pagination.pages,
        "has_next": pagination.has_next,
        "has_prev": pagination.has_prev,
        "next_page": (
            pagination.next_num
            if pagination.has_next
            else None
        ),
        "prev_page": (
            pagination.prev_num
            if pagination.has_prev
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------

def get_consultation(
    consultation_id: int,
    clinic_id: int | None = None,
    *,
    for_update: bool = False,
) -> Consultation:
    if consultation_id <= 0:
        raise ValidationError(
            "Consultation ID must be greater than 0"
        )

    query = Consultation.query.filter(
        Consultation.id == consultation_id
    )

    if clinic_id is not None:
        if clinic_id <= 0:
            raise ValidationError(
                "Clinic ID must be greater than 0"
            )

        query = query.filter(
            Consultation.clinic_id == clinic_id
        )

    if for_update:
        query = query.with_for_update()

    consultation = query.first()

    if consultation is None:
        raise NotFoundError(
            f"Consultation {consultation_id} not found"
        )

    return consultation


def get_consultation_template(
    template_id: int,
) -> ConsultationTemplate:
    if template_id <= 0:
        raise ValidationError(
            "Consultation template ID must be greater than 0"
        )

    template = db.session.get(
        ConsultationTemplate,
        template_id,
    )

    if template is None:
        raise NotFoundError(
            f"Consultation template {template_id} not found"
        )

    return template


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_consultation_participants(
    *,
    clinic_id: int,
    patient_id: int,
    staff_id: int,
):
    if clinic_id <= 0:
        raise ValidationError(
            "Clinic ID must be greater than 0"
        )

    if patient_id <= 0:
        raise ValidationError(
            "Patient ID must be greater than 0"
        )

    if staff_id <= 0:
        raise ValidationError(
            "Staff ID must be greater than 0"
        )

    clinic = ensure_clinic_active(clinic_id)

    patient = get_patient(patient_id)
    staff = get_staff(staff_id)

    if patient.clinic_id != clinic.id:
        raise ConflictError(
            f"Patient {patient_id} does not belong "
            f"to clinic {clinic_id}"
        )

    if staff.clinic_id != clinic.id:
        raise ConflictError(
            f"Staff {staff_id} does not belong "
            f"to clinic {clinic_id}"
        )

    if not patient.is_active:
        raise ValidationError(
            f"Patient {patient_id} is inactive"
        )

    return clinic, patient, staff


def _validate_template(
    *,
    clinic_id: int,
    template_id: int | None,
):
    if template_id is None:
        return None

    template = get_consultation_template(template_id)

    if not template.is_active:
        raise ValidationError(
            f"Consultation template {template_id} "
            "is not active"
        )

    if (
        template.clinic_id is not None
        and template.clinic_id != clinic_id
    ):
        raise ConflictError(
            f"Consultation template {template_id} "
            f"does not belong to clinic {clinic_id}"
        )

    return template


def _validate_appointment(
    *,
    clinic_id: int,
    patient_id: int,
    staff_id: int,
    appointment_id: int | None,
):
    if appointment_id is None:
        return None

    if appointment_id <= 0:
        raise ValidationError(
            "Appointment ID must be greater than 0"
        )

    from app.modules.appointment.models.appointment_model import (
        Appointment,
    )

    appointment = (
        Appointment.query
        .filter(Appointment.id == appointment_id)
        .with_for_update()
        .first()
    )

    if appointment is None:
        raise NotFoundError(
            f"Appointment {appointment_id} not found"
        )

    if appointment.clinic_id != clinic_id:
        raise ConflictError(
            f"Appointment {appointment_id} does not belong "
            f"to clinic {clinic_id}"
        )

    if appointment.patient_id != patient_id:
        raise ConflictError(
            f"Appointment {appointment_id} does not belong "
            f"to patient {patient_id}"
        )

    if appointment.staff_id != staff_id:
        raise ConflictError(
            f"Appointment {appointment_id} does not belong "
            f"to staff member {staff_id}"
        )

    if appointment.status not in (
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    ):
        raise ConflictError(
            f"Appointment {appointment_id} is currently "
            f"'{appointment.status.value}' and cannot start "
            "a consultation"
        )

    return appointment


def _validate_consultation_can_be_completed(
    consultation: Consultation,
):
    if consultation.status == ConsultationStatus.COMPLETED:
        raise ConflictError(
            f"Consultation {consultation.id} "
            "is already completed"
        )

    if consultation.status == ConsultationStatus.CANCELLED:
        raise ConflictError(
            f"Consultation {consultation.id} is cancelled "
            "and cannot be completed"
        )

    if consultation.status != ConsultationStatus.IN_PROGRESS:
        raise ConflictError(
            f"Consultation {consultation.id} is currently "
            f"'{consultation.status.value}' and cannot be completed"
        )


def _validate_consultation_can_be_cancelled(
    consultation: Consultation,
):
    if consultation.status == ConsultationStatus.COMPLETED:
        raise ConflictError(
            f"Consultation {consultation.id} is already "
            "completed and cannot be cancelled"
        )

    if consultation.status == ConsultationStatus.CANCELLED:
        raise ConflictError(
            f"Consultation {consultation.id} "
            "is already cancelled"
        )

    if consultation.status != ConsultationStatus.IN_PROGRESS:
        raise ConflictError(
            f"Consultation {consultation.id} is currently "
            f"'{consultation.status.value}' and cannot be cancelled"
        )


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

@transactional
def start_consultation(
    clinic_id: int,
    patient_id: int,
    staff_id: int,
    appointment_id: int | None = None,
    consultation_type: ConsultationType = ConsultationType.GENERAL,
    template_id: int | None = None,
    chief_complaint: str | None = None,
    symptoms: str | None = None,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> Consultation:
    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Authenticated user is required for Idempotency-Key"
            )

        idempotency_record, is_new = reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation="consultation.start",
            idempotency_key=idempotency_key,
            request_payload={
                "patient_id": patient_id,
                "staff_id": staff_id,
                "appointment_id": appointment_id,
                "consultation_type": consultation_type,
                "template_id": template_id,
                "chief_complaint": chief_complaint,
                "symptoms": symptoms,
            },
        )

        if not is_new:
            if (
                idempotency_record.entity_type != "Consultation"
                or idempotency_record.entity_id is None
            ):
                raise ConflictError(
                    "Idempotency operation has no reconciled Consultation"
                )

            existing_consultation = db.session.get(
                Consultation,
                idempotency_record.entity_id,
            )

            if (
                existing_consultation is None
                or existing_consultation.clinic_id != clinic_id
            ):
                raise ConflictError(
                    "Idempotency operation points to an invalid Consultation"
                )

            return existing_consultation

    clinic, patient, staff = (
        _validate_consultation_participants(
            clinic_id=clinic_id,
            patient_id=patient_id,
            staff_id=staff_id,
        )
    )

    _validate_template(
        clinic_id=clinic.id,
        template_id=template_id,
    )

    _validate_appointment(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        appointment_id=appointment_id,
    )

    consultation = Consultation(
        clinic_id=clinic.id,
        patient_id=patient.id,
        staff_id=staff.id,
        appointment_id=appointment_id,
        consultation_type=consultation_type,
        template_id=template_id,
        chief_complaint=chief_complaint,
        symptoms=symptoms,
        status=ConsultationStatus.IN_PROGRESS,
        record_status=ClinicalRecordStatus.DRAFT.value,
        record_version=1,
        started_at=_utcnow(),
    )

    db.session.add(consultation)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Consultation",
        clinic_id=clinic.id,
        entity_id=consultation.id,
        description=(
            f"Consultation started for patient "
            f"{patient.id} with staff {staff.id}"
        ),
        new_value={
            "clinic_id": clinic.id,
            "patient_id": patient.id,
            "staff_id": staff.id,
            "appointment_id": appointment_id,
            "template_id": template_id,
            "status": consultation.status.value,
            "consultation_type": (
                consultation.consultation_type.value
            ),
        },
    )

    if idempotency_record is not None:
        idempotency_record.entity_type = "Consultation"
        idempotency_record.entity_id = consultation.id

    return consultation




# ---------------------------------------------------------------------------
# Clinical record finalization / amendment
# ---------------------------------------------------------------------------

_CONSULTATION_DOCUMENT_FIELDS = (
    "icd10_code",
    "chief_complaint",
    "symptoms",
    "diagnosis",
    "treatment_plan",
    "notes",
    "voice_note_url",
    "transcribed_text",
)

_CONSULTATION_DOCUMENT_LIMITS = {
    "icd10_code": 10,
    "chief_complaint": 2000,
    "symptoms": 5000,
    "diagnosis": 5000,
    "treatment_plan": 5000,
    "notes": 10000,
    "voice_note_url": 255,
    "transcribed_text": 20000,
}


def _clinical_record_status(
    consultation: Consultation,
) -> ClinicalRecordStatus:
    try:
        return ClinicalRecordStatus(
            consultation.record_status
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ConflictError(
            f"Consultation {consultation.id} has invalid "
            "clinical record state"
        ) from exc


def _validate_expected_record_version(
    consultation: Consultation,
    expected_version: int,
) -> int:
    if (
        isinstance(
            expected_version,
            bool,
        )
        or not isinstance(
            expected_version,
            int,
        )
        or expected_version <= 0
    ):
        raise ValidationError(
            "expected_version must be a positive integer"
        )

    if consultation.record_version != expected_version:
        raise ConflictError(
            f"Consultation {consultation.id} record version mismatch: "
            f"expected {expected_version}, "
            f"current {consultation.record_version}"
        )

    return expected_version


def _validate_consultation_record_actor(
    consultation: Consultation,
    actor_staff_id: int,
):
    actor = get_staff(
        actor_staff_id,
        clinic_id=consultation.clinic_id,
    )

    if actor.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Consultation record actor must be active"
        )

    if (
        actor.user is None
        or not actor.user.is_active
    ):
        raise ValidationError(
            "Consultation record actor user must be active"
        )

    if actor.id != consultation.staff_id:
        raise ConflictError(
            "Only the assigned consultation author "
            "can finalize or amend this clinical record"
        )

    return actor


def _normalize_consultation_document(
    document: dict,
) -> dict:
    if not isinstance(
        document,
        dict,
    ):
        raise ValidationError(
            "document must be an object"
        )

    keys = set(
        document
    )
    expected = set(
        _CONSULTATION_DOCUMENT_FIELDS
    )

    if keys != expected:
        missing = sorted(
            expected - keys
        )
        extra = sorted(
            keys - expected
        )

        detail = []

        if missing:
            detail.append(
                "missing: "
                + ", ".join(
                    missing
                )
            )

        if extra:
            detail.append(
                "unknown: "
                + ", ".join(
                    extra
                )
            )

        raise ValidationError(
            "Consultation amendment must contain "
            "the complete clinical document"
            + (
                " ("
                + "; ".join(
                    detail
                )
                + ")"
                if detail
                else ""
            )
        )

    normalized = {}

    for field_name in _CONSULTATION_DOCUMENT_FIELDS:
        value = document[
            field_name
        ]

        if value is None:
            normalized[
                field_name
            ] = None
            continue

        if not isinstance(
            value,
            str,
        ):
            raise ValidationError(
                f"{field_name} must be a string or null"
            )

        value = value.strip()

        if not value:
            raise ValidationError(
                f"{field_name} cannot be blank"
            )

        maximum = _CONSULTATION_DOCUMENT_LIMITS[
            field_name
        ]

        if len(value) > maximum:
            raise ValidationError(
                f"{field_name} cannot exceed "
                f"{maximum} characters"
            )

        normalized[
            field_name
        ] = value

    return normalized


def get_consultation_original_document(
    consultation: Consultation,
) -> dict:
    return {
        field_name: getattr(
            consultation,
            field_name,
        )
        for field_name in (
            _CONSULTATION_DOCUMENT_FIELDS
        )
    }


def get_consultation_effective_document(
    consultation: Consultation,
) -> dict:
    status = _clinical_record_status(
        consultation
    )

    if status != ClinicalRecordStatus.AMENDED:
        return (
            get_consultation_original_document(
                consultation
            )
        )

    amendment = (
        consultation.latest_amendment
    )

    if amendment is None:
        raise ConflictError(
            f"Consultation {consultation.id} "
            "is amended but has no latest amendment"
        )

    if (
        amendment.clinic_id
        != consultation.clinic_id
        or amendment.patient_id
        != consultation.patient_id
        or amendment.source_record_type
        != ClinicalRecordType.CONSULTATION_NOTE.value
        or amendment.source_record_id
        != consultation.id
        or amendment.new_version
        != consultation.record_version
    ):
        raise ConflictError(
            f"Consultation {consultation.id} "
            "latest amendment linkage is invalid"
        )

    return dict(
        amendment.new_content
    )


@transactional
def finalize_consultation_note(
    *,
    consultation_id: int,
    clinic_id: int,
    finalized_by_staff_id: int,
    expected_version: int,
) -> Consultation:
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        consultation.clinic_id
    )

    if (
        consultation.status
        != ConsultationStatus.COMPLETED
    ):
        raise ConflictError(
            f"Consultation {consultation.id} "
            "must be completed before finalization"
        )

    actor = (
        _validate_consultation_record_actor(
            consultation,
            finalized_by_staff_id,
        )
    )

    _validate_expected_record_version(
        consultation,
        expected_version,
    )

    current_status = (
        _clinical_record_status(
            consultation
        )
    )

    clinical_record_service.validate_finalization_transition(
        current_status
    )

    original = (
        get_consultation_original_document(
            consultation
        )
    )

    consultation.record_status = (
        ClinicalRecordStatus.FINAL.value
    )
    consultation.record_version = 1
    consultation.finalized_by_staff_id = (
        actor.id
    )
    consultation.finalized_at = _utcnow()
    consultation.latest_amendment_id = None

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Consultation",
        entity_id=consultation.id,
        description=(
            "Consultation clinical record finalized"
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
            "finalized_by_staff_id": (
                actor.id
            ),
            "finalized_at": (
                consultation.finalized_at.isoformat()
            ),
            "document": original,
        },
        user_id=actor.user_id,
        clinic_id=consultation.clinic_id,
    )

    return consultation


@transactional
def amend_consultation_note(
    *,
    consultation_id: int,
    clinic_id: int,
    amended_by_staff_id: int,
    expected_version: int,
    reason: str,
    document: dict,
):
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        consultation.clinic_id
    )

    if (
        consultation.status
        != ConsultationStatus.COMPLETED
    ):
        raise ConflictError(
            f"Consultation {consultation.id} "
            "must be completed before amendment"
        )

    actor = (
        _validate_consultation_record_actor(
            consultation,
            amended_by_staff_id,
        )
    )

    _validate_expected_record_version(
        consultation,
        expected_version,
    )

    current_status = (
        _clinical_record_status(
            consultation
        )
    )

    clinical_record_service.validate_amendment_transition(
        current_status
    )

    normalized_document = (
        _normalize_consultation_document(
            document
        )
    )

    effective_before = (
        get_consultation_effective_document(
            consultation
        )
    )

    if normalized_document == effective_before:
        raise ValidationError(
            "Consultation amendment must change "
            "the effective clinical document"
        )

    amendment = (
        clinical_record_service
        .append_clinical_record_amendment(
            clinic_id=consultation.clinic_id,
            patient_id=consultation.patient_id,
            source_record_type=(
                ClinicalRecordType.CONSULTATION_NOTE
            ),
            source_record_id=consultation.id,
            current_status=current_status,
            previous_version=(
                consultation.record_version
            ),
            amended_by_staff_id=actor.id,
            reason=reason,
            new_content=normalized_document,
        )
    )

    old_record_status = (
        consultation.record_status
    )
    old_record_version = (
        consultation.record_version
    )

    consultation.record_status = (
        ClinicalRecordStatus.AMENDED.value
    )
    consultation.record_version = (
        amendment.new_version
    )
    consultation.latest_amendment_id = (
        amendment.id
    )
    consultation.latest_amendment = (
        amendment
    )

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Consultation",
        entity_id=consultation.id,
        description=(
            "Consultation clinical record amended"
        ),
        old_value={
            "record_status": (
                old_record_status
            ),
            "record_version": (
                old_record_version
            ),
        },
        new_value={
            "record_status": (
                consultation.record_status
            ),
            "record_version": (
                consultation.record_version
            ),
            "latest_amendment_id": (
                amendment.id
            ),
            "reason": amendment.reason,
            "new_content_sha256": (
                amendment.new_content_sha256
            ),
        },
        user_id=actor.user_id,
        clinic_id=consultation.clinic_id,
    )

    return (
        consultation,
        amendment,
    )


def get_consultation_amendments(
    *,
    consultation_id: int,
    clinic_id: int,
):
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
    )

    return (
        clinical_record_service
        .get_clinical_record_amendments(
            clinic_id=consultation.clinic_id,
            source_record_type=(
                ClinicalRecordType.CONSULTATION_NOTE
            ),
            source_record_id=consultation.id,
        )
    )

# ---------------------------------------------------------------------------
# Update notes
# ---------------------------------------------------------------------------

@transactional
def update_consultation_note(
    consultation_id: int,
    clinic_id: int,
    **fields,
) -> Consultation:
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        consultation.clinic_id
    )

    if consultation.status == ConsultationStatus.CANCELLED:
        raise ConflictError(
            f"Consultation {consultation.id} is cancelled "
            "and cannot be updated"
        )

    if (
        consultation.record_status
        != ClinicalRecordStatus.DRAFT.value
    ):
        raise ConflictError(
            f"Consultation {consultation.id} clinical record "
            "is finalized and immutable; use the amendment workflow"
        )

    updatable = {
        "icd10_code",
        "chief_complaint",
        "symptoms",
        "diagnosis",
        "treatment_plan",
        "notes",
        "voice_note_url",
        "transcribed_text",
    }

    unknown = set(fields) - updatable

    if unknown:
        raise ValidationError(
            "Unknown consultation field(s): "
            + ", ".join(sorted(unknown))
        )

    old_value = {}
    new_value = {}

    for key, value in fields.items():
        if value is None:
            continue

        if isinstance(value, str):
            value = value.strip()

        current_value = getattr(
            consultation,
            key,
        )

        if current_value == value:
            continue

        old_value[key] = current_value
        new_value[key] = value

        setattr(
            consultation,
            key,
            value,
        )

    if not new_value:
        return consultation

    description = "Consultation draft note updated"

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Consultation",
        entity_id=consultation.id,
        description=description,
        old_value=old_value,
        new_value=new_value,
    )

    return consultation


# ---------------------------------------------------------------------------
# Complete
# ---------------------------------------------------------------------------

@transactional
def complete_consultation(
    consultation_id: int,
    clinic_id: int,
    diagnosis: str,
    treatment_plan: str | None = None,
    notes: str | None = None,
) -> Consultation:
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        consultation.clinic_id
    )

    _validate_consultation_can_be_completed(
        consultation
    )

    if not diagnosis or not diagnosis.strip():
        raise ValidationError(
            "Diagnosis is required to complete "
            "a consultation"
        )

    old_status = consultation.status.value

    consultation.diagnosis = diagnosis.strip()

    if treatment_plan is not None:
        consultation.treatment_plan = treatment_plan.strip()

    if notes is not None:
        consultation.notes = notes.strip()

    consultation.status = ConsultationStatus.COMPLETED
    consultation.ended_at = _utcnow()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Consultation",
        entity_id=consultation.id,
        description="Consultation completed",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": consultation.status.value,
            "diagnosis": consultation.diagnosis,
        },
    )

    return consultation


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------

@transactional
def cancel_consultation(
    consultation_id: int,
    clinic_id: int,
    reason: str | None = None,
) -> Consultation:
    consultation = get_consultation(
        consultation_id=consultation_id,
        clinic_id=clinic_id,
        for_update=True,
    )

    ensure_clinic_active(
        consultation.clinic_id
    )

    _validate_consultation_can_be_cancelled(
        consultation
    )

    old_status = consultation.status.value

    consultation.status = ConsultationStatus.CANCELLED
    consultation.ended_at = _utcnow()

    cleaned_reason = None

    if reason is not None:
        cleaned_reason = reason.strip()

        if cleaned_reason:
            existing_notes = (
                consultation.notes or ""
            ).strip()

            cancellation_note = (
                f"[Cancelled: {cleaned_reason}]"
            )

            consultation.notes = (
                f"{existing_notes}\n{cancellation_note}"
                if existing_notes
                else cancellation_note
            )

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Consultation",
        entity_id=consultation.id,
        description="Consultation cancelled",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": consultation.status.value,
            "reason": cleaned_reason,
        },
    )

    return consultation


# ---------------------------------------------------------------------------
# Patient history
# ---------------------------------------------------------------------------

def get_consultations_for_patient(
    patient_id: int,
    clinic_id: int,
    *,
    consultation_type: ConsultationType | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list[Consultation], dict]:
    if patient_id <= 0:
        raise ValidationError(
            "Patient ID must be greater than 0"
        )

    if clinic_id <= 0:
        raise ValidationError(
            "Clinic ID must be greater than 0"
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    patient = get_patient(patient_id)

    if patient.clinic_id != clinic_id:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    statement = (
        db.select(Consultation)
        .where(
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id,
        )
    )

    if consultation_type is not None:
        statement = statement.where(
            Consultation.consultation_type
            == consultation_type
        )

    statement = statement.order_by(
        Consultation.started_at.desc(),
        Consultation.id.desc(),
    )

    pagination = db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return (
        pagination.items,
        _pagination_metadata(pagination),
    )


# ---------------------------------------------------------------------------
# Patients seen by staff
# ---------------------------------------------------------------------------

def get_patients_seen_by_staff(
    staff_id: int,
    clinic_id: int,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list, dict]:
    if staff_id <= 0:
        raise ValidationError(
            "Staff ID must be greater than 0"
        )

    if clinic_id <= 0:
        raise ValidationError(
            "Clinic ID must be greater than 0"
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    staff = get_staff(staff_id)

    if staff.clinic_id != clinic_id:
        raise NotFoundError(
            f"Staff member {staff_id} not found"
        )

    from app.modules.patient.models.patient_model import Patient

    statement = (
        db.select(Patient)
        .join(
            Consultation,
            Consultation.patient_id == Patient.id,
        )
        .where(
            Consultation.staff_id == staff_id,
            Consultation.clinic_id == clinic_id,
            Patient.clinic_id == clinic_id,
        )
        .distinct()
        .order_by(
            Patient.last_name.asc(),
            Patient.first_name.asc(),
            Patient.id.asc(),
        )
    )

    pagination = db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    patients = list(pagination.items)

    return patients, _pagination_metadata(pagination)


# ---------------------------------------------------------------------------
# Staff history
# ---------------------------------------------------------------------------

def get_consultations_for_staff(
    staff_id: int,
    clinic_id: int,
    status: ConsultationStatus | None = None,
    *,
    consultation_type: ConsultationType | None = None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list[Consultation], dict]:
    if staff_id <= 0:
        raise ValidationError(
            "Staff ID must be greater than 0"
        )

    if clinic_id <= 0:
        raise ValidationError(
            "Clinic ID must be greater than 0"
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    staff = get_staff(staff_id)

    if staff.clinic_id != clinic_id:
        raise NotFoundError(
            f"Staff member {staff_id} not found"
        )

    statement = (
        db.select(Consultation)
        .where(
            Consultation.clinic_id == clinic_id,
            Consultation.staff_id == staff_id,
        )
    )

    if status is not None:
        statement = statement.where(
            Consultation.status == status
        )

    if consultation_type is not None:
        statement = statement.where(
            Consultation.consultation_type
            == consultation_type
        )

    statement = statement.order_by(
        Consultation.started_at.desc(),
        Consultation.id.desc(),
    )

    pagination = db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return (
        pagination.items,
        _pagination_metadata(pagination),
    )


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@transactional
def create_consultation_template(
    name: str,
    structure: dict,
    clinic_id: int | None = None,
    specialty: str | None = None,
    is_active: bool = True,
) -> ConsultationTemplate:
    if not name or not name.strip():
        raise ValidationError(
            "Template name is required"
        )

    if not isinstance(structure, dict):
        raise ValidationError(
            "Template structure must be an object"
        )

    if not structure:
        raise ValidationError(
            "Template structure cannot be empty"
        )

    if clinic_id is not None:
        if clinic_id <= 0:
            raise ValidationError(
                "Clinic ID must be greater than 0"
            )

        ensure_clinic_active(
            clinic_id
        )

    cleaned_specialty = (
        specialty.strip()
        if isinstance(specialty, str)
        else specialty
    )

    template = ConsultationTemplate(
        clinic_id=clinic_id,
        name=name.strip(),
        specialty=cleaned_specialty,
        structure=structure,
        is_active=is_active,
    )

    db.session.add(template)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="ConsultationTemplate",
        entity_id=template.id,
        description=(
            f"Consultation template "
            f"'{template.name}' created"
        ),
        new_value={
            "clinic_id": clinic_id,
            "name": template.name,
            "specialty": cleaned_specialty,
            "is_active": is_active,
        },
    )

    return template


def get_active_templates(
    clinic_id: int | None = None,
    *,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
) -> tuple[list[ConsultationTemplate], dict]:
    if clinic_id is None:
        raise ValidationError(
            "Clinic ID is required for consultation template lookup"
        )

    if (
        isinstance(clinic_id, bool)
        or not isinstance(clinic_id, int)
    ):
        raise ValidationError(
            "Clinic ID must be a positive integer"
        )

    if clinic_id <= 0:
        raise ValidationError(
            "Clinic ID must be greater than 0"
        )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    statement = (
        db.select(ConsultationTemplate)
        .where(
            ConsultationTemplate.is_active.is_(True)
        )
    )

    # Return shared templates and this clinic's templates only.
    statement = statement.where(
        or_(
            ConsultationTemplate.clinic_id.is_(None),
            ConsultationTemplate.clinic_id == clinic_id,
        )
    )

    statement = statement.order_by(
        ConsultationTemplate.name.asc(),
        ConsultationTemplate.id.asc(),
    )

    pagination = db.paginate(
        statement,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return (
        pagination.items,
        _pagination_metadata(pagination),
    )
