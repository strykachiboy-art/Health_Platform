"""CT2A: read-only, admission-scoped case-chat membership proposal.

No conversation is created, no participant is added, and this function is
NOT an authorization grant. CT2B must bind an admission to a canonical
conversation and enforce chat policy and security before any mutation.
"""

from __future__ import annotations

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.extensions import db
from app.modules.nursing.services.care_team_assignment_service import (
    PRIMARY_DOCTOR,
    PRIMARY_NURSE,
    get_primary_assignments,
)
from app.modules.nursing.services.nursing_service import _get_admission
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


_EXPECTED_ROLES = {
    PRIMARY_DOCTOR: Role.DOCTOR,
    PRIMARY_NURSE: Role.NURSE,
}


def build_case_chat_membership_plan(*, clinic_id: int, admission_id: int) -> dict:
    """Propose eligible members from explicit active primary assignments.

    The caller must authorize the operation before presenting this result.
    This method does not check chat settings or grant chat access.
    """
    admission = _get_admission(
        admission_id, clinic_id, require_active=True,
    )
    patient = db.session.execute(
        db.select(Patient).where(
            Patient.id == admission.patient_id,
            Patient.clinic_id == clinic_id,
            Patient.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if patient is None:
        from app.core.exceptions import ConflictError
        raise ConflictError("Case chat requires an active patient")

    active = get_primary_assignments(
        admission_id=admission.id, clinic_id=clinic_id,
    )
    eligible = []
    missing = []
    excluded = []
    for kind in (PRIMARY_DOCTOR, PRIMARY_NURSE):
        row = active[kind]
        if row is None:
            missing.append(kind)
            continue

        candidate = db.session.execute(
            db.select(Staff, User)
            .join(User, User.id == Staff.user_id)
            .where(
                Staff.id == row.staff_id,
                Staff.clinic_id == clinic_id,
                Staff.status == StaffStatus.ACTIVE,
                User.clinic_id == clinic_id,
                User.is_active.is_(True),
                User.role == _EXPECTED_ROLES[kind],
            )
        ).one_or_none()
        if candidate is None:
            excluded.append(kind)
            continue

        staff, user = candidate
        eligible.append({
            "assignment_id": row.id,
            "assignment_kind": kind,
            "staff_id": staff.id,
            "user_id": user.id,
        })

    eligible_user_ids = sorted({item["user_id"] for item in eligible})
    return {
        "clinic_id": clinic_id,
        "admission_id": admission.id,
        "patient_id": patient.id,
        "eligible_members": eligible,
        "eligible_user_ids": eligible_user_ids,
        "missing_primary_assignment_kinds": missing,
        "excluded_inactive_or_invalid_kinds": excluded,
        "has_eligible_member": bool(eligible_user_ids),
        "plan_only": True,
        "chat_policy_checked": False,
        "conversation_created": False,
        "participants_changed": False,
    }
