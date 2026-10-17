from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.extensions import db, celery

from app.modules.appointment.models.appointment_model import Appointment
from app.modules.patient.services.patient_service import get_patient
from app.modules.staff.services.staff_service import get_staff
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
    get_clinic,
)
from app.modules.clinic.models.clinic_model import Clinic

from app.core.enums.appointment_enums import (
    AppointmentStatus,
    AppointmentType,
)
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


# ============================================================================
# INTERNAL HELPERS
# ============================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _validate_pagination(
    page: int,
    per_page: int,
) -> None:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page <= 0
    ):
        raise ValidationError(
            "Per page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per page cannot exceed {MAX_PER_PAGE}"
        )


def _normalize_appointment_type(
    appointment_type,
) -> AppointmentType:
    if isinstance(
        appointment_type,
        AppointmentType,
    ):
        return appointment_type

    try:
        return AppointmentType(appointment_type)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid appointment type"
        )


def _get_appointment(
    appointment_id,
    clinic_id=None,
    lock=False,
):
    _validate_positive_id(
        appointment_id,
        "Appointment ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    query = Appointment.query.filter(
        Appointment.id == appointment_id,
    )

    if clinic_id is not None:
        query = query.filter(
            Appointment.clinic_id == clinic_id,
        )

    if lock:
        query = query.with_for_update()

    appointment = query.first()

    if appointment is None:
        raise NotFoundError(
            f"Appointment {appointment_id} not found"
        )

    return appointment


def _lock_clinic(
    clinic_id: int,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    clinic = (
        Clinic.query
        .filter(
            Clinic.id == clinic_id,
        )
        .with_for_update()
        .first()
    )

    if clinic is None:
        raise NotFoundError(
            f"Clinic {clinic_id} not found"
        )

    return clinic


def _validate_schedule_times(
    scheduled_start,
    scheduled_end,
):
    if (
        scheduled_start is None
        or scheduled_end is None
    ):
        raise ValidationError(
            "scheduled_start and scheduled_end are required"
        )

    if scheduled_end <= scheduled_start:
        raise ValidationError(
            "scheduled_end must be later than scheduled_start"
        )


def _validate_reschedule_times(
    new_start,
    new_end,
):
    if new_start is None or new_end is None:
        raise ValidationError(
            "new_start and new_end are required"
        )

    if new_end <= new_start:
        raise ValidationError(
            "new_end must be later than new_start"
        )


def _ensure_status(
    appointment,
    *allowed_statuses,
):
    if appointment.status not in allowed_statuses:
        allowed = ", ".join(
            status.value
            for status in allowed_statuses
        )

        raise ConflictError(
            f"Appointment {appointment.id} is currently "
            f"'{appointment.status.value}' and cannot perform this action. "
            f"Allowed status: {allowed}"
        )


def _validate_existing_appointment_patient_active(appointment):
    """
    Require an active patient before confirming or rescheduling an appointment.
    Preserve tenant ownership checks for the existing appointment.
    """
    patient = get_patient(appointment.patient_id)

    if patient.clinic_id != appointment.clinic_id:
        raise ConflictError(
            f"Patient {appointment.patient_id} does not belong "
            f"to clinic {appointment.clinic_id}"
        )

    if not patient.is_active:
        raise ValidationError(
            f"Patient {appointment.patient_id} is inactive"
        )

    return patient

def _validate_appointment_participants(
    clinic_id,
    patient_id,
    staff_id,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )
    _validate_positive_id(
        patient_id,
        "Patient ID",
    )
    _validate_positive_id(
        staff_id,
        "Staff ID",
    )

    clinic = get_clinic(
        clinic_id,
    )

    patient = get_patient(
        patient_id,
    )

    staff = get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
    )

    if patient.clinic_id != clinic.id:
        raise ConflictError(
            f"Patient {patient_id} does not belong to "
            f"clinic {clinic_id}"
        )

    if not patient.is_active:
        raise ValidationError(
            f"Patient {patient_id} is inactive"
        )

    return clinic, patient, staff


def _find_patient_overlap(
    patient_id,
    scheduled_start,
    scheduled_end,
    clinic_id=None,
    exclude_appointment_id=None,
):
    _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    query = Appointment.query.filter(
        Appointment.patient_id == patient_id,
        Appointment.status.in_(
            [
                AppointmentStatus.SCHEDULED,
                AppointmentStatus.CONFIRMED,
            ]
        ),
        Appointment.scheduled_start < scheduled_end,
        Appointment.scheduled_end > scheduled_start,
    )

    if clinic_id is not None:
        query = query.filter(
            Appointment.clinic_id == clinic_id,
        )

    if exclude_appointment_id is not None:
        query = query.filter(
            Appointment.id != exclude_appointment_id,
        )

    return query.first()


def _find_staff_overlap(
    staff_id,
    scheduled_start,
    scheduled_end,
    clinic_id=None,
    exclude_appointment_id=None,
):
    _validate_positive_id(
        staff_id,
        "Staff ID",
    )

    query = Appointment.query.filter(
        Appointment.staff_id == staff_id,
        Appointment.status.in_(
            [
                AppointmentStatus.SCHEDULED,
                AppointmentStatus.CONFIRMED,
            ]
        ),
        Appointment.scheduled_start < scheduled_end,
        Appointment.scheduled_end > scheduled_start,
    )

    if clinic_id is not None:
        query = query.filter(
            Appointment.clinic_id == clinic_id,
        )

    if exclude_appointment_id is not None:
        query = query.filter(
            Appointment.id != exclude_appointment_id,
        )

    return query.first()


def _ensure_no_schedule_conflict(
    patient_id,
    staff_id,
    scheduled_start,
    scheduled_end,
    clinic_id=None,
    exclude_appointment_id=None,
):
    patient_conflict = _find_patient_overlap(
        patient_id=patient_id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        clinic_id=clinic_id,
        exclude_appointment_id=exclude_appointment_id,
    )

    if patient_conflict is not None:
        raise ConflictError(
            f"Patient {patient_id} already has an appointment "
            f"overlapping this time period "
            f"(appointment {patient_conflict.id})",
            code="APPOINTMENT_PATIENT_SCHEDULE_CONFLICT",
        )

    staff_conflict = _find_staff_overlap(
        staff_id=staff_id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        clinic_id=clinic_id,
        exclude_appointment_id=exclude_appointment_id,
    )

    if staff_conflict is not None:
        raise ConflictError(
            f"Staff {staff_id} already has an appointment "
            f"overlapping this time period "
            f"(appointment {staff_conflict.id})",
            code="APPOINTMENT_STAFF_SCHEDULE_CONFLICT",
        )


# ============================================================================
# APPOINTMENT CREATION
# ============================================================================


@transactional
def create_appointment(
    clinic_id,
    patient_id,
    staff_id,
    scheduled_start,
    scheduled_end,
    appointment_type=AppointmentType.IN_PERSON,
    reason=None,
    notes=None,
    idempotency_key=None,
    idempotency_user_id=None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )
    _validate_positive_id(
        patient_id,
        "Patient ID",
    )
    _validate_positive_id(
        staff_id,
        "Staff ID",
    )

    _validate_schedule_times(
        scheduled_start,
        scheduled_end,
    )

    appointment_type = _normalize_appointment_type(
        appointment_type,
    )

    idempotency_record = None

    if idempotency_key is not None:
        if idempotency_user_id is None:
            raise ValidationError(
                "Idempotency user ID is required"
            )

        # Use the shared disposition guard before reserving the operation key.
        # Every future offline-capable endpoint adapter must call this guard
        # inside its transaction before attempting a side effect.
        from app.core.offline_operations.services.disposition_service import (
            assert_operation_not_disposed,
        )

        assert_operation_not_disposed(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation_type="appointment.create",
            idempotency_key=idempotency_key,
        )

        idempotency_record, created = reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation="appointment.create",
            idempotency_key=idempotency_key,
            request_payload={
                "patient_id": patient_id,
                "staff_id": staff_id,
                "scheduled_start": scheduled_start,
                "scheduled_end": scheduled_end,
                "appointment_type": appointment_type,
                "reason": reason,
                "notes": notes,
            },
        )

        if not created:
            if (
                idempotency_record.entity_type != "Appointment"
                or idempotency_record.entity_id is None
            ):
                raise ConflictError(
                    "Idempotency record does not reference "
                    "a completed appointment",
                    code="IDEMPOTENCY_RECORD_UNRESOLVED",
                )

            existing_appointment = db.session.get(
                Appointment,
                idempotency_record.entity_id,
            )

            if existing_appointment is None:
                raise ConflictError(
                    "Idempotency record references a missing appointment",
                    code="IDEMPOTENCY_RECORD_UNRESOLVED",
                )

            if existing_appointment.clinic_id != clinic_id:
                raise ConflictError(
                    "Idempotency record references another clinic",
                    code="IDEMPOTENCY_RECORD_UNRESOLVED",
                )

            return existing_appointment

    clinic = _lock_clinic(
        clinic_id,
    )

    ensure_clinic_active(
        clinic_id,
    )

    _validate_appointment_participants(
        clinic_id=clinic.id,
        patient_id=patient_id,
        staff_id=staff_id,
    )

    _ensure_no_schedule_conflict(
        patient_id=patient_id,
        staff_id=staff_id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        clinic_id=clinic.id,
    )

    appointment = Appointment(
        clinic_id=clinic.id,
        patient_id=patient_id,
        staff_id=staff_id,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        appointment_type=appointment_type,
        status=AppointmentStatus.SCHEDULED,
        reason=reason,
        notes = notes,
    )

    db.session.add(appointment)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Appointment",
        clinic_id=clinic.id,
        entity_id=appointment.id,
        description=(
            f"Appointment created for patient "
            f"{patient_id} with staff {staff_id}"
        ),
        new_value={
            "clinic_id": clinic.id,
            "patient_id": patient_id,
            "staff_id": staff_id,
            "scheduled_start": (
                scheduled_start.isoformat()
            ),
            "scheduled_end": (
                scheduled_end.isoformat()
            ),
            "appointment_type": (
                appointment.appointment_type.value
            ),
            "status": (
                appointment.status.value
            ),
            "reason": reason,
        },
    )

    if idempotency_record is not None:
        idempotency_record.entity_type = "Appointment"
        idempotency_record.entity_id = appointment.id

    return appointment


# ============================================================================
# RESCHEDULING
# ============================================================================


@transactional
def reschedule_appointment(
    appointment_id,
    new_start,
    new_end,
    clinic_id=None,
):
    _validate_positive_id(
        appointment_id,
        "Appointment ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        ensure_clinic_active(
            clinic_id,
        )

    appointment = _get_appointment(
        appointment_id=appointment_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if clinic_id is None:
        ensure_clinic_active(
            appointment.clinic_id,
        )

    _ensure_status(
        appointment,
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    )

    _validate_reschedule_times(
        new_start,
        new_end,
    )

    _validate_existing_appointment_patient_active(
        appointment
    )

    _ensure_no_schedule_conflict(
        patient_id=appointment.patient_id,
        staff_id=appointment.staff_id,
        scheduled_start=new_start,
        scheduled_end=new_end,
        clinic_id=appointment.clinic_id,
        exclude_appointment_id=appointment.id,
    )

    old_value = {
        "scheduled_start": (
            appointment.scheduled_start.isoformat()
        ),
        "scheduled_end": (
            appointment.scheduled_end.isoformat()
        ),
    }

    appointment.scheduled_start = new_start
    appointment.scheduled_end = new_end
    appointment.reminder_sent = False

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Appointment",
        entity_id=appointment.id,
        description="Appointment rescheduled",
        old_value=old_value,
        new_value={
            "scheduled_start": (
                new_start.isoformat()
            ),
            "scheduled_end": (
                new_end.isoformat()
            ),
        },
    )

    return appointment


# ============================================================================
# APPOINTMENT STATUS TRANSITIONS
# ============================================================================


@transactional
def confirm_appointment(
    appointment_id,
    clinic_id=None,
):
    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        ensure_clinic_active(
            clinic_id,
        )

    appointment = _get_appointment(
        appointment_id=appointment_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if clinic_id is None:
        ensure_clinic_active(
            appointment.clinic_id,
        )

    _ensure_status(
        appointment,
        AppointmentStatus.SCHEDULED,
    )

    _validate_existing_appointment_patient_active(
        appointment
    )

    old_status = appointment.status.value

    appointment.status = AppointmentStatus.CONFIRMED

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Appointment",
        entity_id=appointment.id,
        description="Appointment confirmed",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": appointment.status.value,
        },
    )

    return appointment


@transactional
def cancel_appointment(
    appointment_id,
    reason=None,
    clinic_id=None,
):
    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        ensure_clinic_active(
            clinic_id,
        )

    appointment = _get_appointment(
        appointment_id=appointment_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if clinic_id is None:
        ensure_clinic_active(
            appointment.clinic_id,
        )

    _ensure_status(
        appointment,
        AppointmentStatus.SCHEDULED,
        AppointmentStatus.CONFIRMED,
    )

    old_status = appointment.status.value

    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancelled_at = _utcnow()
    appointment.cancellation_reason = reason

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Appointment",
        entity_id=appointment.id,
        description="Appointment cancelled",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": appointment.status.value,
            "reason": reason,
        },
    )

    return appointment


@transactional
def complete_appointment(
    appointment_id,
    notes=None,
    clinic_id=None,
):
    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        ensure_clinic_active(
            clinic_id,
        )

    appointment = _get_appointment(
        appointment_id=appointment_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if clinic_id is None:
        ensure_clinic_active(
            appointment.clinic_id,
        )

    _ensure_status(
        appointment,
        AppointmentStatus.CONFIRMED,
    )

    old_status = appointment.status.value

    appointment.status = AppointmentStatus.COMPLETED

    if notes is not None:
        appointment.notes = notes

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Appointment",
        entity_id=appointment.id,
        description="Appointment marked completed",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": appointment.status.value,
        },
    )

    return appointment


@transactional
def mark_no_show(
    appointment_id,
    clinic_id=None,
):
    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )
        ensure_clinic_active(
            clinic_id,
        )

    appointment = _get_appointment(
        appointment_id=appointment_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if clinic_id is None:
        ensure_clinic_active(
            appointment.clinic_id,
        )

    _ensure_status(
        appointment,
        AppointmentStatus.CONFIRMED,
    )

    old_status = appointment.status.value

    appointment.status = AppointmentStatus.NO_SHOW

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Appointment",
        entity_id=appointment.id,
        description="Appointment marked as no-show",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": appointment.status.value,
        },
    )

    return appointment


# ============================================================================
# APPOINTMENT QUERIES
# ============================================================================


def get_appointments_for_patient(
    patient_id,
    clinic_id=None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    if clinic_id is not None:
        _validate_positive_id(
            clinic_id,
            "Clinic ID",
        )

    _validate_pagination(
        page,
        per_page,
    )

    patient = get_patient(
        patient_id,
    )

    if clinic_id is not None:
        if patient.clinic_id != clinic_id:
            raise NotFoundError(
                f"Patient {patient_id} not found"
            )

    query = Appointment.query.filter(
        Appointment.patient_id == patient_id,
    )

    if clinic_id is not None:
        query = query.filter(
            Appointment.clinic_id == clinic_id,
        )

    return (
        query
        .order_by(
            Appointment.scheduled_start.desc(),
            Appointment.id.desc(),
        )
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
    )


def get_appointments_for_staff(
    clinic_id,
    staff_id,
    date_=None,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )
    _validate_positive_id(
        staff_id,
        "Staff ID",
    )

    _validate_pagination(
        page,
        per_page,
    )

    get_staff(
        staff_id=staff_id,
        clinic_id=clinic_id,
    )

    query = Appointment.query.filter(
        Appointment.clinic_id == clinic_id,
        Appointment.staff_id == staff_id,
    )

    if date_ is not None:
        query = query.filter(
            db.func.date(
                Appointment.scheduled_start
            ) == date_
        )

    return (
        query
        .order_by(
            Appointment.scheduled_start.asc(),
            Appointment.id.asc(),
        )
        .paginate(
            page=page,
            per_page=per_page,
            error_out=False,
        )
    )


# ============================================================================
# CELERY REMINDERS
# ============================================================================


@celery.task(
    name="send_appointment_reminder"
)
def send_appointment_reminder(
    clinic_id: int,
    appointment_id: int,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        appointment_id,
        "Appointment ID",
    )

    appointment = db.session.get(
        Appointment,
        appointment_id,
    )

    if appointment is None:
        return

    if appointment.clinic_id != clinic_id:
        return

    if appointment.reminder_sent:
        return

    appointment.reminder_sent = True

    db.session.commit()
@celery.task(
    name="check_upcoming_appointments"
)
def check_upcoming_appointments():
    now = _utcnow()

    tomorrow = now + timedelta(
        days=1,
    )

    reminder_window_end = tomorrow + timedelta(
        hours=1,
    )

    upcoming = (
        Appointment.query
        .filter(
            Appointment.scheduled_start.between(
                tomorrow,
                reminder_window_end,
            ),
            Appointment.reminder_sent.is_(False),
            Appointment.status.in_(
                [
                    AppointmentStatus.SCHEDULED,
                    AppointmentStatus.CONFIRMED,
                ]
            ),
        )
        .order_by(
            Appointment.scheduled_start.asc(),
            Appointment.id.asc(),
        )
        .all()
    )

    for appointment in upcoming:
        send_appointment_reminder.delay(
            appointment.clinic_id,
            appointment.id,
        )