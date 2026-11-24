"""CE1-S02 internal-only theatre scheduling, not surgical execution.

PostgreSQL uses theatre-row SELECT FOR UPDATE plus a DB exclusion constraint
(added in the S02 migration) to reject simultaneous overlapping reservations.
SQLite-focused tests verify business rules but are not concurrency proof.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.theatre_model import OperatingTheatre, TheatreBooking
from app.modules.surgery.services.surgical_case_service import _active_admission, _doctor, _positive


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _admin(actor_staff_id: int, clinic_id: int) -> Staff:
    actor = db.session.execute(select(Staff).where(
        Staff.id == _positive(actor_staff_id, "actor_staff_id"),
        Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if actor is None:
        raise NotFoundError("Theatre administrator not found")
    if (actor.status != StaffStatus.ACTIVE or actor.user is None
            or not actor.user.is_active or actor.user.role != Role.ADMIN):
        raise ValidationError("Active clinic administrator required")
    return actor


def _required_text(value: str, label: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must not be blank")
    result = value.strip()
    if len(result) > max_length:
        raise ValidationError(f"{label} exceeds {max_length} characters")
    return result


def _interval(starts_at: datetime, ends_at: datetime) -> tuple[datetime, datetime]:
    result = []
    for field, value in (("starts_at", starts_at), ("ends_at", ends_at)):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError(f"{field} must include a timezone offset")
        result.append(value.astimezone(timezone.utc).replace(tzinfo=None))
    start, end = result
    if start >= end:
        raise ValidationError("Theatre booking requires starts_at before ends_at")
    return start, end


def _lock_case(*, surgical_case_id: int, clinic_id: int) -> SurgicalCase:
    case = db.session.execute(select(SurgicalCase).where(
        SurgicalCase.id == _positive(surgical_case_id, "surgical_case_id"),
        SurgicalCase.clinic_id == clinic_id,
    ).with_for_update()).scalar_one_or_none()
    if case is None:
        raise NotFoundError("Surgical case not found")
    return case


def _require_plannable_case(case: SurgicalCase, clinic_id: int) -> None:
    if case.status != "planned":
        raise ConflictError("Only planned surgical cases may be scheduled")
    admission = _active_admission(case.admission_id, clinic_id)
    order = db.session.execute(select(ProcedureOrder).where(
        ProcedureOrder.id == case.procedure_order_id,
        ProcedureOrder.clinic_id == clinic_id,
    ).with_for_update()).scalar_one_or_none()
    if (order is None or order.patient_id != case.patient_id
            or case.patient_id != admission.patient_id
            or order.admission_id != case.admission_id
            or order.procedure_type != "surgical"):
        raise ConflictError("Surgical case, admission and order lineage mismatch")
    if order.status != "active" or order.consent_required is not True:
        raise ConflictError("Active consent-required surgical order is mandatory")


def _lock_theatre(*, theatre_id: int, clinic_id: int) -> OperatingTheatre:
    theatre = db.session.execute(select(OperatingTheatre).where(
        OperatingTheatre.id == _positive(theatre_id, "theatre_id"),
        OperatingTheatre.clinic_id == clinic_id,
    ).with_for_update()).scalar_one_or_none()
    if theatre is None:
        raise NotFoundError("Operating theatre not found")
    if not theatre.is_active:
        raise ConflictError("Inactive theatre cannot be scheduled")
    return theatre


def _assert_no_overlap(*, theatre_id: int, start: datetime, end: datetime,
                       exclude_booking_id: int | None = None) -> None:
    statement = select(TheatreBooking.id).where(
        TheatreBooking.theatre_id == theatre_id,
        TheatreBooking.status == "scheduled",
        TheatreBooking.starts_at < end,
        TheatreBooking.ends_at > start,
    )
    if exclude_booking_id is not None:
        statement = statement.where(TheatreBooking.id != exclude_booking_id)
    if db.session.execute(statement.limit(1)).scalar_one_or_none() is not None:
        raise ConflictError("Overlapping theatre booking exists")


def _booking_ref(*, booking_id: int, clinic_id: int) -> TheatreBooking:
    booking = db.session.execute(select(TheatreBooking).where(
        TheatreBooking.id == _positive(booking_id, "booking_id"),
        TheatreBooking.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if booking is None:
        raise NotFoundError("Theatre booking not found")
    return booking


def _version(value: int) -> int:
    return _positive(value, "expected_version")


@transactional
def create_operating_theatre(*, clinic_id: int, actor_staff_id: int,
                             code: str, display_name: str) -> OperatingTheatre:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _admin(actor_staff_id, clinic_id)
    clinic = db.session.execute(select(Clinic).where(Clinic.id == clinic_id)).scalar_one_or_none()
    if clinic is None or clinic.status != ClinicStatus.ACTIVE:
        raise ConflictError("Active clinic required for an operating theatre")
    code = _required_text(code, "code", 32).upper()
    display_name = _required_text(display_name, "display_name", 128)
    if db.session.execute(select(OperatingTheatre.id).where(
        OperatingTheatre.clinic_id == clinic_id, OperatingTheatre.code == code,
    )).scalar_one_or_none() is not None:
        raise ConflictError("Theatre code already exists in clinic")
    theatre = OperatingTheatre(clinic_id=clinic_id, code=code, display_name=display_name)
    db.session.add(theatre)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="OperatingTheatre", entity_id=theatre.id,
        description="Registered operating theatre; no execution authority",
        new_value={"code": code, "display_name": display_name},
        user_id=actor.user_id, clinic_id=clinic_id,
    )
    return theatre


@transactional
def schedule_theatre(*, clinic_id: int, actor_staff_id: int,
                     surgical_case_id: int, theatre_id: int,
                     starts_at: datetime, ends_at: datetime) -> TheatreBooking:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    start, end = _interval(starts_at, ends_at)
    case = _lock_case(surgical_case_id=surgical_case_id, clinic_id=clinic_id)
    _require_plannable_case(case, clinic_id)
    _lock_theatre(theatre_id=theatre_id, clinic_id=clinic_id)
    if db.session.execute(select(TheatreBooking.id).where(
        TheatreBooking.surgical_case_id == case.id,
        TheatreBooking.status == "scheduled",
    )).scalar_one_or_none() is not None:
        raise ConflictError("Surgical case already has an active theatre booking")
    _assert_no_overlap(theatre_id=theatre_id, start=start, end=end)
    booking = TheatreBooking(
        clinic_id=clinic_id, surgical_case_id=case.id, theatre_id=theatre_id,
        starts_at=start, ends_at=end, scheduled_by_staff_id=actor.id,
        status="scheduled", version=1,
    )
    db.session.add(booking)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type="TheatreBooking", entity_id=booking.id,
        description="Reserved theatre slot; not a surgical safety clearance",
        new_value={"surgical_case_id": case.id, "theatre_id": theatre_id,
                   "starts_at": start.isoformat(), "ends_at": end.isoformat(),
                   "status": booking.status, "version": booking.version},
        user_id=actor.user_id, clinic_id=clinic_id, patient_id=case.patient_id,
    )
    return booking


@transactional
def reschedule_theatre(*, booking_id: int, clinic_id: int, actor_staff_id: int,
                       starts_at: datetime, ends_at: datetime,
                       expected_version: int) -> TheatreBooking:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    start, end = _interval(starts_at, ends_at)
    reference = _booking_ref(booking_id=booking_id, clinic_id=clinic_id)
    case = _lock_case(surgical_case_id=reference.surgical_case_id, clinic_id=clinic_id)
    _require_plannable_case(case, clinic_id)
    _lock_theatre(theatre_id=reference.theatre_id, clinic_id=clinic_id)
    booking = db.session.execute(select(TheatreBooking).where(
        TheatreBooking.id == booking_id, TheatreBooking.clinic_id == clinic_id,
    ).with_for_update()).scalar_one_or_none()
    if booking is None or booking.status != "scheduled":
        raise ConflictError("Only active theatre bookings can be rescheduled")
    if booking.version != _version(expected_version):
        raise ConflictError("Theatre booking version changed")
    _assert_no_overlap(theatre_id=booking.theatre_id, start=start, end=end,
                       exclude_booking_id=booking.id)
    old = {"starts_at": booking.starts_at.isoformat(),
           "ends_at": booking.ends_at.isoformat(), "version": booking.version}
    booking.starts_at, booking.ends_at = start, end
    booking.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.UPDATE, entity_type="TheatreBooking", entity_id=booking.id,
        description="Rescheduled theatre time; no surgical execution transition",
        old_value=old,
        new_value={"starts_at": start.isoformat(), "ends_at": end.isoformat(),
                   "version": booking.version},
        user_id=actor.user_id, clinic_id=clinic_id, patient_id=case.patient_id,
    )
    return booking


@transactional
def cancel_theatre_booking(*, booking_id: int, clinic_id: int,
                           actor_staff_id: int, expected_version: int,
                           reason: str) -> TheatreBooking:
    clinic_id = _positive(clinic_id, "clinic_id")
    actor = _doctor(actor_staff_id, clinic_id)
    reason = _required_text(reason, "reason", 500)
    reference = _booking_ref(booking_id=booking_id, clinic_id=clinic_id)
    case = _lock_case(surgical_case_id=reference.surgical_case_id, clinic_id=clinic_id)
    _lock_theatre(theatre_id=reference.theatre_id, clinic_id=clinic_id)
    booking = db.session.execute(select(TheatreBooking).where(
        TheatreBooking.id == booking_id, TheatreBooking.clinic_id == clinic_id,
    ).with_for_update()).scalar_one_or_none()
    if booking is None or booking.status != "scheduled":
        raise ConflictError("Only active theatre bookings can be cancelled")
    if booking.version != _version(expected_version):
        raise ConflictError("Theatre booking version changed")
    booking.status = "cancelled"
    booking.cancelled_at = _now()
    booking.cancelled_by_staff_id = actor.id
    booking.cancellation_reason = reason
    booking.version += 1
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE, entity_type="TheatreBooking",
        entity_id=booking.id, description="Cancelled theatre reservation",
        old_value={"status": "scheduled", "version": expected_version},
        new_value={"status": "cancelled", "version": booking.version,
                   "reason": reason},
        user_id=actor.user_id, clinic_id=clinic_id, patient_id=case.patient_id,
    )
    return booking


def get_theatre_booking(*, booking_id: int, clinic_id: int) -> TheatreBooking:
    return _booking_ref(booking_id=booking_id, clinic_id=_positive(clinic_id, "clinic_id"))
