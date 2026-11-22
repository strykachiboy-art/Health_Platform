"""CT3B: clinical portion of an acknowledged nursing handoff transfer.

Internal helper ONLY. Call from nursing_service.acknowledge_handoff while its
@transactional transaction owns the admission and handoff row locks. Do not
expose this helper as an independently callable public write or add a second
commit. Chat/outbox dispatch is deliberately deferred to CT3C.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.nursing_enums import NursingHandoffStatus
from app.core.exceptions import ConflictError
from app.extensions import db
from app.modules.nursing.models.admission_care_assignment_model import (
    AdmissionCareAssignment,
)
from app.modules.nursing.services.handoff_primary_transfer_preconditions import (
    get_handoff_primary_transfer_preconditions,
)


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def transfer_primary_nurse_for_acknowledgement(*, handoff, admission, actor):
    """Move *existing* primary nurse to the acknowledged incoming nurse.

    Lock ownership is held by the caller. Preconditions are re-evaluated under
    that lock. The original row is preserved, and the successor is linked by
    assignment_reason to the audited handoff. No chat membership is mutated.
    """
    if (
        handoff.status != NursingHandoffStatus.READY
        or handoff.admission_id != admission.id
        or handoff.patient_id != admission.patient_id
        or handoff.clinic_id != actor.clinic_id
        or handoff.incoming_staff_id != actor.id
    ):
        raise ConflictError("Handoff transfer context has changed")

    context = get_handoff_primary_transfer_preconditions(
        handoff_id=handoff.id,
        clinic_id=handoff.clinic_id,
        incoming_staff_id=actor.id,
    )
    current = db.session.execute(
        db.select(AdmissionCareAssignment)
        .where(
            AdmissionCareAssignment.id
            == context["current_primary_nurse_assignment_id"],
            AdmissionCareAssignment.clinic_id == handoff.clinic_id,
            AdmissionCareAssignment.admission_id == handoff.admission_id,
            AdmissionCareAssignment.patient_id == handoff.patient_id,
            AdmissionCareAssignment.assignment_kind == "primary_nurse",
            AdmissionCareAssignment.ended_at.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if current is None or current.staff_id != handoff.outgoing_staff_id:
        raise ConflictError("Primary nurse changed during handoff transfer")

    now = max(_now_naive(), current.assigned_at)
    reason = f"Acknowledged nursing handoff {handoff.id}"
    current.ended_at = now
    current.ended_by_user_id = actor.user_id
    current.end_reason = reason
    db.session.flush()  # Remove active predecessor before inserting successor.

    successor = AdmissionCareAssignment(
        clinic_id=handoff.clinic_id,
        patient_id=handoff.patient_id,
        admission_id=handoff.admission_id,
        staff_id=actor.id,
        assignment_kind="primary_nurse",
        assigned_by_user_id=actor.user_id,
        assignment_reason=reason,
        assigned_at=now,
    )
    db.session.add(successor)
    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError("Primary nurse changed during handoff transfer") from exc

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="AdmissionCareAssignment",
        entity_id=current.id,
        clinic_id=handoff.clinic_id,
        patient_id=handoff.patient_id,
        user_id=actor.user_id,
        description="Primary nurse ended by acknowledged nursing handoff",
        reason=reason,
        old_value={"staff_id": current.staff_id, "assignment_id": current.id},
        new_value={"ended_at": now.isoformat(), "handoff_id": handoff.id},
    )
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="AdmissionCareAssignment",
        entity_id=successor.id,
        clinic_id=handoff.clinic_id,
        patient_id=handoff.patient_id,
        user_id=actor.user_id,
        description="Primary nurse transferred by acknowledged nursing handoff",
        reason=reason,
        old_value={"assignment_id": current.id, "staff_id": current.staff_id},
        new_value={
            "assignment_id": successor.id,
            "staff_id": successor.staff_id,
            "handoff_id": handoff.id,
            "assignment_kind": "primary_nurse",
        },
    )
    return successor
