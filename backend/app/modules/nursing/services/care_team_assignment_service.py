"""Admission-scoped primary clinician assignment operations.

CT1B2: explicit, audited assignments only. No endpoint, automatic inference,
chat membership changes, or handoff ownership transfer in this slice.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.auth.user.models.user_model import User
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services.nursing_service import _get_admission
from app.modules.staff.models.staff_model import Staff


PRIMARY_DOCTOR = "primary_doctor"
PRIMARY_NURSE = "primary_nurse"
_ASSIGNMENT_ROLES = {
    PRIMARY_DOCTOR: Role.DOCTOR,
    PRIMARY_NURSE: Role.NURSE,
}


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _positive_id(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field} must be a positive integer")
    return value


def _kind(value: str) -> str:
    if not isinstance(value, str) or value not in _ASSIGNMENT_ROLES:
        raise ValidationError("Invalid primary clinician assignment kind")
    return value


def _reason(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} is required")
    value = value.strip()
    if not value or len(value) > 500:
        raise ValidationError(f"{field} must be 1 to 500 characters")
    return value


def _require_assignment_admin(actor_user_id: int, clinic_id: int) -> User:
    _positive_id(actor_user_id, "actor_user_id")
    actor = db.session.get(User, actor_user_id)
    if actor is None or actor.clinic_id != clinic_id:
        raise NotFoundError("Assignment actor not found")
    if not actor.is_active or actor.role != Role.ADMIN:
        raise ValidationError("An active clinic administrator is required")
    if actor.staff is not None:
        if (
            actor.staff.clinic_id != clinic_id
            or actor.staff.user_id != actor.id
            or actor.staff.status != StaffStatus.ACTIVE
        ):
            raise ValidationError("Assignment actor staff account is not active")
    return actor


def _require_clinician(staff_id: int, clinic_id: int, kind: str) -> Staff:
    _positive_id(staff_id, "staff_id")
    staff = db.session.execute(
        db.select(Staff).where(
            Staff.id == staff_id,
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if staff is None:
        raise NotFoundError("Clinician not found")
    if (
        staff.status != StaffStatus.ACTIVE
        or staff.user is None
        or staff.user.id != staff.user_id
        or staff.user.clinic_id != clinic_id
        or not staff.user.is_active
    ):
        raise ValidationError("Clinician is not active in this clinic")
    if staff.user.role != _ASSIGNMENT_ROLES[kind]:
        raise ValidationError("Clinician role does not match assignment kind")
    return staff


def _active_assignment(
    *,
    admission_id: int,
    patient_id: int,
    clinic_id: int,
    assignment_kind: str,
    lock: bool = False,
) -> AdmissionCareAssignment | None:
    statement = db.select(AdmissionCareAssignment).where(
        AdmissionCareAssignment.clinic_id == clinic_id,
        AdmissionCareAssignment.admission_id == admission_id,
        AdmissionCareAssignment.patient_id == patient_id,
        AdmissionCareAssignment.assignment_kind == assignment_kind,
        AdmissionCareAssignment.ended_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    return db.session.execute(statement).scalar_one_or_none()


def _check_expected(
    current: AdmissionCareAssignment | None,
    expected_current_assignment_id: int | None,
) -> None:
    if expected_current_assignment_id is not None:
        _positive_id(expected_current_assignment_id, "expected_current_assignment_id")
    if current is None and expected_current_assignment_id is not None:
        raise ConflictError("Expected assignment is no longer active")
    if current is not None and expected_current_assignment_id != current.id:
        raise ConflictError("Current primary assignment changed; refresh first")


def get_primary_assignments(*, admission_id: int, clinic_id: int) -> dict:
    """Return authoritative active rows; caller must enforce read authorization."""
    admission = _get_admission(admission_id, clinic_id, require_active=False)
    rows = db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == clinic_id,
            AdmissionCareAssignment.admission_id == admission.id,
            AdmissionCareAssignment.patient_id == admission.patient_id,
            AdmissionCareAssignment.ended_at.is_(None),
        )
    ).scalars().all()
    return {
        "primary_doctor": next(
            (item for item in rows if item.assignment_kind == PRIMARY_DOCTOR), None
        ),
        "primary_nurse": next(
            (item for item in rows if item.assignment_kind == PRIMARY_NURSE), None
        ),
    }


def list_assignment_history(*, admission_id: int, clinic_id: int) -> list:
    """Preserve historical assignment evidence; caller authorizes disclosure."""
    admission = _get_admission(admission_id, clinic_id, require_active=False)
    return list(db.session.execute(
        db.select(AdmissionCareAssignment)
        .where(
            AdmissionCareAssignment.clinic_id == clinic_id,
            AdmissionCareAssignment.admission_id == admission.id,
            AdmissionCareAssignment.patient_id == admission.patient_id,
        )
        .order_by(
            AdmissionCareAssignment.assigned_at.desc(),
            AdmissionCareAssignment.id.desc(),
        )
    ).scalars().all())


@transactional
def assign_primary_clinician(
    *,
    clinic_id: int,
    admission_id: int,
    actor_user_id: int,
    staff_id: int,
    assignment_kind: str,
    assignment_reason: str,
    expected_current_assignment_id: int | None = None,
) -> AdmissionCareAssignment:
    """Initial assignment or compare-and-swap reassignment.

    For initial assignment, expected_current_assignment_id must be None.
    For reassignment, it must match the active row exactly. PostgreSQL
    admission row locking serializes competing assignments; the partial
    unique index remains the final enforcement at the database layer.
    """
    clinic_id = _positive_id(clinic_id, "clinic_id")
    assignment_kind = _kind(assignment_kind)
    assignment_reason = _reason(assignment_reason, "assignment_reason")
    actor = _require_assignment_admin(actor_user_id, clinic_id)
    admission = _get_admission(admission_id, clinic_id, lock=True, require_active=True)
    staff = _require_clinician(staff_id, clinic_id, assignment_kind)

    current = _active_assignment(
        clinic_id=clinic_id,
        admission_id=admission.id,
        patient_id=admission.patient_id,
        assignment_kind=assignment_kind,
        lock=True,
    )
    _check_expected(current, expected_current_assignment_id)
    if current is not None and current.staff_id == staff.id:
        raise ConflictError("Clinician is already the active primary assignee")

    now = _utcnow_naive()
    if current is not None:
        now = max(now, current.assigned_at)
        current.ended_at = now
        current.ended_by_user_id = actor.id
        current.end_reason = assignment_reason
        db.session.flush()  # End the previous row before inserting its successor.

    successor = AdmissionCareAssignment(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        staff_id=staff.id,
        assignment_kind=assignment_kind,
        assigned_by_user_id=actor.id,
        assignment_reason=assignment_reason,
        assigned_at=now,
    )
    db.session.add(successor)
    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError("A concurrent primary assignment was detected") from exc

    create_audit_log(
        action=AuditAction.CREATE if current is None else AuditAction.UPDATE,
        entity_type="AdmissionCareAssignment",
        entity_id=successor.id,
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        user_id=actor.id,
        description="Primary clinician assigned" if current is None else "Primary clinician reassigned",
        reason=assignment_reason,
        old_value=(
            {"assignment_id": current.id, "staff_id": current.staff_id}
            if current is not None else None
        ),
        new_value={
            "assignment_id": successor.id,
            "staff_id": successor.staff_id,
            "assignment_kind": assignment_kind,
            "admission_id": admission.id,
        },
    )
    return successor


@transactional
def end_primary_clinician_assignment(
    *,
    clinic_id: int,
    admission_id: int,
    actor_user_id: int,
    assignment_kind: str,
    expected_current_assignment_id: int,
    end_reason: str,
) -> AdmissionCareAssignment:
    """Explicitly end an active primary assignment without a successor."""
    clinic_id = _positive_id(clinic_id, "clinic_id")
    assignment_kind = _kind(assignment_kind)
    end_reason = _reason(end_reason, "end_reason")
    _positive_id(expected_current_assignment_id, "expected_current_assignment_id")
    actor = _require_assignment_admin(actor_user_id, clinic_id)
    admission = _get_admission(admission_id, clinic_id, lock=True, require_active=True)
    current = _active_assignment(
        clinic_id=clinic_id,
        admission_id=admission.id,
        patient_id=admission.patient_id,
        assignment_kind=assignment_kind,
        lock=True,
    )
    _check_expected(current, expected_current_assignment_id)
    assert current is not None  # Guaranteed by _check_expected.
    current.ended_at = max(_utcnow_naive(), current.assigned_at)
    current.ended_by_user_id = actor.id
    current.end_reason = end_reason
    db.session.flush()
    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="AdmissionCareAssignment",
        entity_id=current.id,
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        user_id=actor.id,
        description="Primary clinician assignment ended",
        reason=end_reason,
        old_value={"assignment_id": current.id, "staff_id": current.staff_id},
        new_value={"ended_at": current.ended_at.isoformat(), "assignment_kind": assignment_kind},
    )
    return current
