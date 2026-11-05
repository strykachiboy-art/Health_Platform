"""CT3A: read-only preconditions for a future atomic nurse handoff transfer.

This is NOT the ownership-transfer operation. The CT3B acknowledgement write
MUST re-evaluate these constraints while holding the admission row lock.
READY is never a transfer or a chat-access grant. No chat side effects here.
"""

from __future__ import annotations

from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, ValidationError
from app.extensions import db
from app.modules.nursing.services.care_team_assignment_service import (
    PRIMARY_NURSE,
    get_primary_assignments,
)
from app.modules.nursing.services.nursing_service import (
    _get_admission,
    _get_handoff,
    _get_nurse,
)


def get_handoff_primary_transfer_preconditions(
    *, handoff_id: int, clinic_id: int, incoming_staff_id: int,
) -> dict:
    """Validate current clinical authority without mutating it.

    The caller is responsible for authenticating and authorizing disclosure.
    This method is an eligibility guard, not a permission grant and not a
    substitute for revalidation within the CT3B locked transaction.
    """
    handoff = _get_handoff(handoff_id, clinic_id, lock=False)
    admission = _get_admission(
        handoff.admission_id, clinic_id, lock=False, require_active=True,
    )
    if handoff.patient_id != admission.patient_id:
        raise ConflictError("Handoff patient/admission mismatch")
    if handoff.status != NursingHandoffStatus.READY:
        raise ConflictError("Only READY handoffs are eligible for transfer")
    if (
        handoff.ready_at is None
        or handoff.acknowledged_at is not None
        or handoff.acknowledged_by_staff_id is not None
    ):
        raise ConflictError("Handoff readiness evidence is inconsistent")
    if (
        isinstance(incoming_staff_id, bool)
        or not isinstance(incoming_staff_id, int)
        or incoming_staff_id <= 0
    ):
        raise ValidationError("Incoming staff ID must be a positive integer")
    if handoff.incoming_staff_id != incoming_staff_id:
        raise ConflictError("Only the designated incoming nurse may take over")
    if handoff.outgoing_staff_id == incoming_staff_id:
        raise ConflictError("Outgoing and incoming nurses must differ")

    incoming = _get_nurse(incoming_staff_id, clinic_id)
    if (
        incoming.clinic_id != clinic_id
        or incoming.status != StaffStatus.ACTIVE
        or incoming.user is None
        or incoming.user_id is None
        or incoming.user.id != incoming.user_id
        or incoming.user.clinic_id != clinic_id
        or not incoming.user.is_active
        or incoming.user.role != Role.NURSE
    ):
        raise ValidationError("Incoming nurse account is not active in this clinic")

    current = get_primary_assignments(
        admission_id=admission.id, clinic_id=clinic_id,
    )[PRIMARY_NURSE]
    if current is None:
        raise ConflictError("No explicit current primary nurse to hand over")
    if (
        current.clinic_id != clinic_id
        or current.admission_id != admission.id
        or current.patient_id != admission.patient_id
        or current.assignment_kind != PRIMARY_NURSE
        or current.ended_at is not None
    ):
        raise ConflictError("Current primary nurse evidence is inconsistent")
    if current.staff_id != handoff.outgoing_staff_id:
        raise ConflictError("Outgoing nurse is not the current primary nurse")

    return {
        "clinic_id": clinic_id,
        "admission_id": admission.id,
        "patient_id": admission.patient_id,
        "handoff_id": handoff.id,
        "handoff_status": handoff.status.value,
        "current_primary_nurse_assignment_id": current.id,
        "outgoing_primary_nurse_staff_id": current.staff_id,
        "incoming_primary_nurse_staff_id": incoming.id,
        "incoming_nurse_user_id": incoming.user_id,
        "eligible_for_acknowledged_transfer": True,
        "ownership_transferred": False,
        "chat_membership_changed": False,
    }
