"""CT3C internal, provenance-bound authority for handoff case-chat delivery.

Never an API permission grant. A nurse can deliver *only* the request tied to
her own ACKNOWLEDGED handoff while she remains the confirmed primary nurse.
Ordinary administrator-originated requests use CT2's existing admin guard.
"""
from __future__ import annotations

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import NotFoundError
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.modules.staff.models.staff_model import Staff
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services.nursing_service import _get_admission


def require_acknowledged_handoff_sync_authority(
    *, clinic_id: int, admission_id: int, actor_user_id: int, handoff_id: int,
) -> User:
    """Fail closed on stale, forged, foreign or revoked handoff evidence.

    Caller of a write must already own the admission lock. The service only
    validates authority: it never assigns chat membership or mutates ownership.
    """
    for field, value in (
        ("clinic_id", clinic_id), ("admission_id", admission_id),
        ("actor_user_id", actor_user_id), ("handoff_id", handoff_id),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise NotFoundError("Case chat synchronization not authorized")

    admission = _get_admission(admission_id, clinic_id, require_active=True)
    user = db.session.execute(
        db.select(User).where(
            User.id == actor_user_id, User.clinic_id == clinic_id,
            User.is_active.is_(True), User.role == Role.NURSE,
        )
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("Case chat synchronization not authorized")
    staff = db.session.execute(
        db.select(Staff).where(
            Staff.user_id == actor_user_id,
            Staff.clinic_id == clinic_id,
            Staff.status == StaffStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if staff is None or staff.user_id != user.id:
        raise NotFoundError("Case chat synchronization not authorized")
    handoff = db.session.execute(
        db.select(NursingHandoff).where(
            NursingHandoff.id == handoff_id,
            NursingHandoff.clinic_id == clinic_id,
            NursingHandoff.admission_id == admission_id,
            NursingHandoff.patient_id == admission.patient_id,
            NursingHandoff.status == NursingHandoffStatus.ACKNOWLEDGED,
            NursingHandoff.incoming_staff_id == staff.id,
            NursingHandoff.acknowledged_by_staff_id == staff.id,
            NursingHandoff.acknowledged_at.is_not(None),
        )
    ).scalar_one_or_none()
    if handoff is None or handoff.outgoing_staff_id == staff.id:
        raise NotFoundError("Case chat synchronization not authorized")

    current = db.session.execute(
        db.select(AdmissionCareAssignment).where(
            AdmissionCareAssignment.clinic_id == clinic_id,
            AdmissionCareAssignment.admission_id == admission_id,
            AdmissionCareAssignment.patient_id == admission.patient_id,
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
            AdmissionCareAssignment.ended_at.is_(None),
            AdmissionCareAssignment.staff_id == staff.id,
            AdmissionCareAssignment.assigned_by_user_id == actor_user_id,
            AdmissionCareAssignment.assignment_reason
            == f"Acknowledged nursing handoff {handoff_id}",
        )
    ).scalar_one_or_none()
    if current is None:
        raise NotFoundError("Case chat synchronization not authorized")
    return user
