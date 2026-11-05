"""Read-only care context from existing hospital records.

This is NOT an authoritative care-team assignment system.
"""

from app.core.enums.consultation_enums import ConsultationStatus
from app.core.enums.nursing_enums import (
    NursingCareTaskStatus,
    NursingHandoffStatus,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.nursing.models.nursing_model import (
    NursingCareTask,
    NursingHandoff,
)
from app.modules.nursing.services.nursing_service import _get_admission
from app.modules.staff.models.staff_model import Staff


def _active_staff_cards(
    staff_ids: set[int],
    *,
    clinic_id: int,
    role: Role,
) -> list[dict]:
    if not staff_ids:
        return []

    records = db.session.execute(
        db.select(Staff)
        .join(User, User.id == Staff.user_id)
        .where(
            Staff.id.in_(staff_ids),
            Staff.clinic_id == clinic_id,
            User.clinic_id == clinic_id,
            Staff.status == StaffStatus.ACTIVE,
            User.is_active.is_(True),
            User.role == role,
        )
        .order_by(Staff.id.asc())
    ).scalars().all()

    return [
        {
            "staff_id": staff.id,
            "user_id": staff.user_id,
            "display_name": (
                f"{staff.first_name} {staff.last_name}".strip()
            ),
        }
        for staff in records
    ]


def _handoff_evidence(handoff: NursingHandoff | None) -> dict | None:
    if handoff is None:
        return None

    return {
        "handoff_id": handoff.id,
        "status": handoff.status.value,
        "outgoing_staff_id": handoff.outgoing_staff_id,
        "incoming_staff_id": handoff.incoming_staff_id,
        "ready_at": (
            handoff.ready_at.isoformat()
            if handoff.ready_at else None
        ),
        "acknowledged_at": (
            handoff.acknowledged_at.isoformat()
            if handoff.acknowledged_at else None
        ),
    }


def get_admission_care_context(
    *,
    admission_id: int,
    clinic_id: int,
) -> dict:
    """Read scoped evidence without guessing clinical responsibility.

    Evidence returned here must not authorize chat or patient access.
    CT1B will supply authoritative primary assignments.
    """

    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    nurse_ids = set(
        db.session.scalars(
            db.select(NursingCareTask.assigned_to_staff_id)
            .where(
                NursingCareTask.clinic_id == clinic_id,
                NursingCareTask.admission_id == admission.id,
                NursingCareTask.patient_id == admission.patient_id,
                NursingCareTask.assigned_to_staff_id.is_not(None),
                NursingCareTask.status.in_((
                    NursingCareTaskStatus.PENDING,
                    NursingCareTaskStatus.IN_PROGRESS,
                )),
            )
            .distinct()
        ).all()
    )

    doctor_ids = set(
        db.session.scalars(
            db.select(Consultation.staff_id)
            .where(
                Consultation.clinic_id == clinic_id,
                Consultation.patient_id == admission.patient_id,
                Consultation.status != ConsultationStatus.CANCELLED,
            )
            .distinct()
        ).all()
    )

    pending = db.session.scalars(
        db.select(NursingHandoff)
        .where(
            NursingHandoff.clinic_id == clinic_id,
            NursingHandoff.admission_id == admission.id,
            NursingHandoff.patient_id == admission.patient_id,
            NursingHandoff.status == NursingHandoffStatus.READY,
        )
        .order_by(
            NursingHandoff.ready_at.desc(),
            NursingHandoff.id.desc(),
        )
        .limit(1)
    ).first()

    acknowledged = db.session.scalars(
        db.select(NursingHandoff)
        .where(
            NursingHandoff.clinic_id == clinic_id,
            NursingHandoff.admission_id == admission.id,
            NursingHandoff.patient_id == admission.patient_id,
            NursingHandoff.status == NursingHandoffStatus.ACKNOWLEDGED,
        )
        .order_by(
            NursingHandoff.acknowledged_at.desc(),
            NursingHandoff.id.desc(),
        )
        .limit(1)
    ).first()

    return {
        "clinic_id": clinic_id,
        "patient_id": admission.patient_id,
        "admission_id": admission.id,
        "admission_status": admission.status.value,
        "responsibility": {
            "primary_doctor_staff_id": None,
            "primary_nurse_staff_id": None,
            "confirmed": False,
        },
        "involvement_evidence": {
            "historical_consultation_doctors": _active_staff_cards(
                doctor_ids,
                clinic_id=clinic_id,
                role=Role.DOCTOR,
            ),
            "active_nursing_task_assignees": _active_staff_cards(
                nurse_ids,
                clinic_id=clinic_id,
                role=Role.NURSE,
            ),
        },
        "handoff_evidence": {
            "pending_ready": _handoff_evidence(pending),
            "latest_acknowledged": _handoff_evidence(acknowledged),
        },
        "authoritative_assignment_available": False,
    }