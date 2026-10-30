from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select

from app.extensions import db

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_safety_enums import (
    ClinicalAlertStatus,
    ClinicalRuleSeverity,
)
from app.core.enums.consultation_enums import ConsultationStatus
from app.core.enums.emar_enums import MedicationAdministrationStatus
from app.core.enums.lab_enums import LabOrderStatus
from app.core.enums.patient_enums import (
    AllergyClinicalStatus,
    AllergyVerificationStatus,
)
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.idempotency.services.idempotency_service import (
    reserve_idempotency_operation,
)
from app.core.utils.decorators import transactional

from app.core.clinical_safety.models.clinical_alert_model import ClinicalAlert
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.emar.models.emar_model import MedicationAdministration
from app.modules.lab.models.lab_model import LabOrder
from app.modules.patient.models.patient_model import Patient, PatientAllergy
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingHandoff,
    NursingNote,
)


_ALLOWED_PLAN_MUTABLE = {
    NursingCarePlanStatus.ACTIVE,
    NursingCarePlanStatus.ON_HOLD,
}

_ALLOWED_TASK_MUTABLE = {
    NursingCareTaskStatus.PENDING,
    NursingCareTaskStatus.IN_PROGRESS,
}

_ALLOWED_HANDOFF_MUTABLE = {
    NursingHandoffStatus.DRAFT,
    NursingHandoffStatus.READY,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _db_now() -> datetime:
    return _utcnow().replace(
        tzinfo=None
    )


def _validate_positive_id(
    value,
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


def _normalize_datetime(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    if not isinstance(
        value,
        datetime,
    ):
        raise ValidationError(
            f"{field_name} must be a datetime"
        )

    if value.tzinfo is not None:
        value = (
            value
            .astimezone(
                timezone.utc
            )
            .replace(
                tzinfo=None
            )
        )

    return value


def _normalize_required_text(
    value,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{field_name} is required"
        )

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_optional_text(
    value,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(
        value,
        str,
    ):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _normalize_plan_status(
    value,
) -> NursingCarePlanStatus:
    if isinstance(
        value,
        NursingCarePlanStatus,
    ):
        return value

    try:
        return NursingCarePlanStatus(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid nursing care plan status"
        ) from exc


def _normalize_task_priority(
    value,
) -> NursingCareTaskPriority:
    if value is None:
        return NursingCareTaskPriority.ROUTINE

    if isinstance(
        value,
        NursingCareTaskPriority,
    ):
        return value

    try:
        return NursingCareTaskPriority(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid nursing task priority"
        ) from exc


def _normalize_note_type(
    value,
) -> NursingNoteType:
    if value is None:
        return NursingNoteType.PROGRESS

    if isinstance(
        value,
        NursingNoteType,
    ):
        return value

    try:
        return NursingNoteType(
            value
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid nursing note type"
        ) from exc


def _get_nurse(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    staff_id = _validate_positive_id(
        staff_id,
        "staff_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    staff = db.session.execute(
        select(
            Staff
        ).where(
            Staff.id
            == staff_id,
            Staff.clinic_id
            == clinic_id,
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if (
        staff.status
        != StaffStatus.ACTIVE
    ):
        raise ValidationError(
            "Nursing actor must be active"
        )

    if (
        staff.user is None
        or not staff.user.is_active
    ):
        raise ValidationError(
            "Nursing actor user must be active"
        )

    if (
        staff.user.role
        != Role.NURSE
    ):
        raise ValidationError(
            "Nursing workflow requires nurse role"
        )

    return staff


def _get_admission(
    admission_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
    require_active: bool = False,
) -> Admission:
    admission_id = _validate_positive_id(
        admission_id,
        "admission_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = (
        select(
            Admission
        )
        .join(
            Bed,
            Admission.bed_id
            == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id
            == Ward.id,
        )
        .where(
            Admission.id
            == admission_id,
            Ward.clinic_id
            == clinic_id,
        )
    )

    if lock:
        statement = (
            statement
            .with_for_update()
        )

    admission = db.session.execute(
        statement
    ).scalar_one_or_none()

    if admission is None:
        raise NotFoundError(
            f"Admission {admission_id} not found"
        )

    if (
        require_active
        and admission.status
        != AdmissionStatus.ADMITTED
    ):
        raise ConflictError(
            "Nursing workflow requires "
            "an active admission"
        )

    return admission


def _ensure_plan_context(
    plan: NursingCarePlan,
    *,
    require_active_admission: bool = False,
) -> Admission:
    admission = _get_admission(
        plan.admission_id,
        plan.clinic_id,
        require_active=(
            require_active_admission
        ),
    )

    if (
        plan.patient_id
        != admission.patient_id
    ):
        raise ConflictError(
            "Care plan patient/admission "
            "context mismatch"
        )

    return admission


def _ensure_task_context(
    task: NursingCareTask,
    *,
    require_active_admission: bool = False,
) -> tuple[
    NursingCarePlan,
    Admission,
]:
    plan = _get_care_plan(
        task.care_plan_id,
        task.clinic_id,
        lock=False,
    )

    admission = _ensure_plan_context(
        plan,
        require_active_admission=(
            require_active_admission
        ),
    )

    if (
        task.patient_id
        != plan.patient_id
        or task.admission_id
        != plan.admission_id
    ):
        raise ConflictError(
            "Nursing task context mismatch"
        )

    return (
        plan,
        admission,
    )


def _get_care_plan(
    care_plan_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> NursingCarePlan:
    care_plan_id = _validate_positive_id(
        care_plan_id,
        "care_plan_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = select(
        NursingCarePlan
    ).where(
        NursingCarePlan.id
        == care_plan_id,
        NursingCarePlan.clinic_id
        == clinic_id,
    )

    if lock:
        statement = (
            statement
            .with_for_update()
        )

    plan = db.session.execute(
        statement
    ).scalar_one_or_none()

    if plan is None:
        raise NotFoundError(
            f"Nursing care plan "
            f"{care_plan_id} not found"
        )

    _ensure_plan_context(
        plan,
        require_active_admission=False,
    )

    return plan


def _get_care_task(
    task_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> NursingCareTask:
    task_id = _validate_positive_id(
        task_id,
        "task_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = select(
        NursingCareTask
    ).where(
        NursingCareTask.id
        == task_id,
        NursingCareTask.clinic_id
        == clinic_id,
    )

    if lock:
        statement = (
            statement
            .with_for_update()
        )

    task = db.session.execute(
        statement
    ).scalar_one_or_none()

    if task is None:
        raise NotFoundError(
            f"Nursing care task "
            f"{task_id} not found"
        )

    _ensure_task_context(
        task,
        require_active_admission=False,
    )

    return task


def _get_note(
    note_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> NursingNote:
    note_id = _validate_positive_id(
        note_id,
        "note_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = select(
        NursingNote
    ).where(
        NursingNote.id
        == note_id,
        NursingNote.clinic_id
        == clinic_id,
    )

    if lock:
        statement = (
            statement
            .with_for_update()
        )

    note = db.session.execute(
        statement
    ).scalar_one_or_none()

    if note is None:
        raise NotFoundError(
            f"Nursing note {note_id} not found"
        )

    admission = _get_admission(
        note.admission_id,
        clinic_id,
        require_active=False,
    )

    if (
        note.patient_id
        != admission.patient_id
    ):
        raise ConflictError(
            "Nursing note context mismatch"
        )

    return note


def _get_handoff(
    handoff_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> NursingHandoff:
    handoff_id = _validate_positive_id(
        handoff_id,
        "handoff_id",
    )
    clinic_id = _validate_positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = select(
        NursingHandoff
    ).where(
        NursingHandoff.id
        == handoff_id,
        NursingHandoff.clinic_id
        == clinic_id,
    )

    if lock:
        statement = (
            statement
            .with_for_update()
        )

    handoff = db.session.execute(
        statement
    ).scalar_one_or_none()

    if handoff is None:
        raise NotFoundError(
            f"Nursing handoff "
            f"{handoff_id} not found"
        )

    admission = _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=False,
    )

    if (
        handoff.patient_id
        != admission.patient_id
    ):
        raise ConflictError(
            "Nursing handoff context mismatch"
        )

    return handoff


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    entity_type: str,
    entity_id: int,
    description: str,
    new_value=None,
    old_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


def _resolve_idempotent_nursing_replay(
    *,
    idempotency_record,
    clinic_id: int,
    entity_type: str,
    entity_id: int,
    loader,
    expected_status,
):
    if (
        idempotency_record.entity_type
        != entity_type
        or idempotency_record.entity_id
        is None
    ):
        raise ConflictError(
            "Idempotency record does not reference "
            "a completed nursing transition",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    if (
        idempotency_record.entity_id
        != entity_id
    ):
        raise ConflictError(
            "Idempotency record references "
            "a different nursing entity",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    existing = loader(
        entity_id,
        clinic_id,
        lock=False,
    )

    if (
        existing.status
        != expected_status
    ):
        raise ConflictError(
            "Idempotency record does not reference "
            "the expected completed nursing state",
            code="IDEMPOTENCY_RECORD_UNRESOLVED",
        )

    return existing


def _reserve_nursing_transition(
    *,
    clinic_id: int,
    actor: Staff,
    operation: str,
    idempotency_key: str | None,
    idempotency_user_id: int | None,
    request_payload: dict,
    entity_type: str,
    entity_id: int,
    loader,
    expected_status,
):
    if idempotency_key is None:
        return (
            None,
            None,
        )

    if idempotency_user_id is None:
        raise ValidationError(
            "Idempotency user ID is required"
        )

    if (
        actor.user_id
        != idempotency_user_id
    ):
        raise ValidationError(
            "Idempotency user does not match "
            "the nursing transition actor"
        )

    record, created = (
        reserve_idempotency_operation(
            clinic_id=clinic_id,
            user_id=idempotency_user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_payload=request_payload,
        )
    )

    if not created:
        replay = (
            _resolve_idempotent_nursing_replay(
                idempotency_record=record,
                clinic_id=clinic_id,
                entity_type=entity_type,
                entity_id=entity_id,
                loader=loader,
                expected_status=expected_status,
            )
        )

        return (
            record,
            replay,
        )

    return (
        record,
        None,
    )


@transactional
def create_care_plan(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    title: str,
    problem: str,
    goal: str,
    target_at: datetime | None = None,
) -> NursingCarePlan:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    admission = _get_admission(
        admission_id,
        clinic_id,
        lock=True,
        require_active=True,
    )

    title = _normalize_required_text(
        title,
        "title",
        200,
    )
    problem = _normalize_required_text(
        problem,
        "problem",
        10000,
    )
    goal = _normalize_required_text(
        goal,
        "goal",
        10000,
    )

    started_at = _db_now()
    target_at = _normalize_datetime(
        target_at,
        "target_at",
    )

    if (
        target_at is not None
        and target_at < started_at
    ):
        raise ValidationError(
            "target_at cannot be before "
            "care plan start"
        )

    plan = NursingCarePlan(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        created_by_staff_id=actor.id,
        title=title,
        problem=problem,
        goal=goal,
        status=(
            NursingCarePlanStatus.ACTIVE
        ),
        started_at=started_at,
        target_at=target_at,
        version=1,
    )

    db.session.add(
        plan
    )
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingCarePlan",
        entity_id=plan.id,
        description=(
            "Created nursing care plan"
        ),
        new_value={
            "admission_id": plan.admission_id,
            "patient_id": plan.patient_id,
            "status": plan.status.value,
            "version": plan.version,
        },
    )

    return plan


def get_care_plan(
    care_plan_id: int,
    *,
    clinic_id: int,
) -> NursingCarePlan:
    return _get_care_plan(
        care_plan_id,
        clinic_id,
    )


def list_care_plans(
    *,
    admission_id: int,
    clinic_id: int,
    status=None,
) -> list[NursingCarePlan]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    statement = (
        select(
            NursingCarePlan
        )
        .where(
            NursingCarePlan.clinic_id
            == clinic_id,
            NursingCarePlan.admission_id
            == admission.id,
            NursingCarePlan.patient_id
            == admission.patient_id,
        )
        .order_by(
            NursingCarePlan.created_at.desc(),
            NursingCarePlan.id.desc(),
        )
    )

    if status is not None:
        statement = statement.where(
            NursingCarePlan.status
            == _normalize_plan_status(
                status
            )
        )

    return list(
        db.session.execute(
            statement
        ).scalars().all()
    )


@transactional
def hold_care_plan(
    care_plan_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingCarePlan:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    plan = _get_care_plan(
        care_plan_id,
        clinic_id,
        lock=True,
    )
    _ensure_plan_context(
        plan,
        require_active_admission=True,
    )

    if (
        plan.status
        != NursingCarePlanStatus.ACTIVE
    ):
        raise ConflictError(
            "Only active care plans "
            "can be placed on hold"
        )

    old_status = plan.status.value
    plan.status = (
        NursingCarePlanStatus.ON_HOLD
    )
    plan.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCarePlan",
        entity_id=plan.id,
        description=(
            "Placed nursing care plan on hold"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": plan.status.value,
            "version": plan.version,
        },
    )

    return plan


@transactional
def resume_care_plan(
    care_plan_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingCarePlan:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    plan = _get_care_plan(
        care_plan_id,
        clinic_id,
        lock=True,
    )
    _ensure_plan_context(
        plan,
        require_active_admission=True,
    )

    if (
        plan.status
        != NursingCarePlanStatus.ON_HOLD
    ):
        raise ConflictError(
            "Only held care plans "
            "can be resumed"
        )

    old_status = plan.status.value
    plan.status = (
        NursingCarePlanStatus.ACTIVE
    )
    plan.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCarePlan",
        entity_id=plan.id,
        description=(
            "Resumed nursing care plan"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": plan.status.value,
            "version": plan.version,
        },
    )

    return plan


@transactional
def complete_care_plan(
    care_plan_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> NursingCarePlan:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    idempotency_record, replay = (
        _reserve_nursing_transition(
            clinic_id=clinic_id,
            actor=actor,
            operation="nursing.care_plan.complete",
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
            request_payload={
                "care_plan_id": care_plan_id,
                "actor_staff_id": actor_staff_id,
            },
            entity_type="NursingCarePlan",
            entity_id=care_plan_id,
            loader=_get_care_plan,
            expected_status=NursingCarePlanStatus.COMPLETED,
        )
    )

    if replay is not None:
        return replay
    plan = _get_care_plan(
        care_plan_id,
        clinic_id,
        lock=True,
    )
    _ensure_plan_context(
        plan,
        require_active_admission=True,
    )

    if plan.status not in _ALLOWED_PLAN_MUTABLE:
        raise ConflictError(
            "Care plan is already terminal"
        )

    pending_task = (
        db.session.execute(
            select(
                NursingCareTask.id
            ).where(
                NursingCareTask.care_plan_id
                == plan.id,
                NursingCareTask.status.in_(
                    (
                        NursingCareTaskStatus.PENDING,
                        NursingCareTaskStatus.IN_PROGRESS,
                    )
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
    )

    if pending_task is not None:
        raise ConflictError(
            "Care plan cannot be completed "
            "while tasks remain open"
        )

    old_status = plan.status.value
    plan.status = (
        NursingCarePlanStatus.COMPLETED
    )
    plan.completed_at = _db_now()
    plan.cancelled_at = None
    plan.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCarePlan",
        entity_id=plan.id,
        description=(
            "Completed nursing care plan"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": plan.status.value,
            "completed_at": (
                plan.completed_at.isoformat()
            ),
            "version": plan.version,
        },
    )


    if idempotency_record is not None:
        idempotency_record.entity_type = (
            "NursingCarePlan"
        )
        idempotency_record.entity_id = (
            plan.id
        )

    return plan


@transactional
def cancel_care_plan(
    care_plan_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingCarePlan:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    plan = _get_care_plan(
        care_plan_id,
        clinic_id,
        lock=True,
    )
    _ensure_plan_context(
        plan,
        require_active_admission=True,
    )

    if plan.status not in _ALLOWED_PLAN_MUTABLE:
        raise ConflictError(
            "Care plan is already terminal"
        )

    open_task = (
        db.session.execute(
            select(
                NursingCareTask.id
            ).where(
                NursingCareTask.care_plan_id
                == plan.id,
                NursingCareTask.status.in_(
                    (
                        NursingCareTaskStatus.PENDING,
                        NursingCareTaskStatus.IN_PROGRESS,
                    )
                ),
            )
            .limit(1)
        ).scalar_one_or_none()
    )

    if open_task is not None:
        raise ConflictError(
            "Open nursing tasks must be "
            "cancelled before the plan"
        )

    old_status = plan.status.value
    plan.status = (
        NursingCarePlanStatus.CANCELLED
    )
    plan.cancelled_at = _db_now()
    plan.completed_at = None
    plan.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCarePlan",
        entity_id=plan.id,
        description=(
            "Cancelled nursing care plan"
        ),
        old_value={
            "status": old_status,
        },
        new_value={
            "status": plan.status.value,
            "cancelled_at": (
                plan.cancelled_at.isoformat()
            ),
            "version": plan.version,
        },
    )

    return plan


@transactional
def create_care_task(
    *,
    care_plan_id: int,
    clinic_id: int,
    actor_staff_id: int,
    description: str,
    instructions: str | None = None,
    priority=None,
    assigned_to_staff_id: int | None = None,
    due_at: datetime | None = None,
) -> NursingCareTask:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    plan = _get_care_plan(
        care_plan_id,
        clinic_id,
        lock=True,
    )

    _ensure_plan_context(
        plan,
        require_active_admission=True,
    )

    if (
        plan.status
        != NursingCarePlanStatus.ACTIVE
    ):
        raise ConflictError(
            "Tasks can only be added "
            "to active care plans"
        )

    assignee = None

    if assigned_to_staff_id is not None:
        assignee = _get_nurse(
            assigned_to_staff_id,
            clinic_id,
        )

    description = _normalize_required_text(
        description,
        "description",
        10000,
    )
    instructions = _normalize_optional_text(
        instructions,
        "instructions",
        10000,
    )
    priority = _normalize_task_priority(
        priority
    )
    due_at = _normalize_datetime(
        due_at,
        "due_at",
    )

    task = NursingCareTask(
        care_plan_id=plan.id,
        clinic_id=plan.clinic_id,
        patient_id=plan.patient_id,
        admission_id=plan.admission_id,
        created_by_staff_id=actor.id,
        assigned_to_staff_id=(
            assignee.id
            if assignee is not None
            else None
        ),
        description=description,
        instructions=instructions,
        priority=priority,
        status=(
            NursingCareTaskStatus.PENDING
        ),
        due_at=due_at,
        version=1,
    )

    db.session.add(
        task
    )
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingCareTask",
        entity_id=task.id,
        description=(
            "Created nursing care task"
        ),
        new_value={
            "care_plan_id": task.care_plan_id,
            "admission_id": task.admission_id,
            "patient_id": task.patient_id,
            "status": task.status.value,
            "priority": task.priority.value,
            "assigned_to_staff_id": (
                task.assigned_to_staff_id
            ),
            "version": task.version,
        },
    )

    return task


def get_care_task(
    task_id: int,
    *,
    clinic_id: int,
) -> NursingCareTask:
    return _get_care_task(
        task_id,
        clinic_id,
    )


def list_care_tasks(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[NursingCareTask]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    return list(
        db.session.execute(
            select(
                NursingCareTask
            )
            .where(
                NursingCareTask.clinic_id
                == clinic_id,
                NursingCareTask.admission_id
                == admission.id,
                NursingCareTask.patient_id
                == admission.patient_id,
            )
            .order_by(
                NursingCareTask.due_at.asc(),
                NursingCareTask.id.asc(),
            )
        ).scalars().all()
    )


@transactional
def start_care_task(
    task_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingCareTask:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    task = _get_care_task(
        task_id,
        clinic_id,
        lock=True,
    )
    plan, _admission = (
        _ensure_task_context(
            task,
            require_active_admission=True,
        )
    )

    if (
        plan.status
        != NursingCarePlanStatus.ACTIVE
    ):
        raise ConflictError(
            "Care plan must be active"
        )

    if (
        task.status
        != NursingCareTaskStatus.PENDING
    ):
        raise ConflictError(
            "Only pending tasks can be started"
        )

    task.status = (
        NursingCareTaskStatus.IN_PROGRESS
    )
    task.started_at = _db_now()
    task.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCareTask",
        entity_id=task.id,
        description=(
            "Started nursing care task"
        ),
        new_value={
            "status": task.status.value,
            "started_at": (
                task.started_at.isoformat()
            ),
            "version": task.version,
        },
    )

    return task


@transactional
def complete_care_task(
    task_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> NursingCareTask:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    idempotency_record, replay = (
        _reserve_nursing_transition(
            clinic_id=clinic_id,
            actor=actor,
            operation="nursing.care_task.complete",
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
            request_payload={
                "task_id": task_id,
                "actor_staff_id": actor_staff_id,
            },
            entity_type="NursingCareTask",
            entity_id=task_id,
            loader=_get_care_task,
            expected_status=NursingCareTaskStatus.COMPLETED,
        )
    )

    if replay is not None:
        return replay
    task = _get_care_task(
        task_id,
        clinic_id,
        lock=True,
    )
    plan, _admission = (
        _ensure_task_context(
            task,
            require_active_admission=True,
        )
    )

    if (
        plan.status
        != NursingCarePlanStatus.ACTIVE
    ):
        raise ConflictError(
            "Care plan must be active"
        )

    if task.status not in _ALLOWED_TASK_MUTABLE:
        raise ConflictError(
            "Nursing task is already terminal"
        )

    task.status = (
        NursingCareTaskStatus.COMPLETED
    )
    task.completed_at = _db_now()
    task.completed_by_staff_id = (
        actor.id
    )
    task.cancelled_at = None
    task.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCareTask",
        entity_id=task.id,
        description=(
            "Completed nursing care task"
        ),
        new_value={
            "status": task.status.value,
            "completed_by_staff_id": (
                task.completed_by_staff_id
            ),
            "completed_at": (
                task.completed_at.isoformat()
            ),
            "version": task.version,
        },
    )


    if idempotency_record is not None:
        idempotency_record.entity_type = (
            "NursingCareTask"
        )
        idempotency_record.entity_id = (
            task.id
        )

    return task


@transactional
def cancel_care_task(
    task_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> NursingCareTask:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    task = _get_care_task(
        task_id,
        clinic_id,
        lock=True,
    )
    _ensure_task_context(
        task,
        require_active_admission=True,
    )

    if task.status not in _ALLOWED_TASK_MUTABLE:
        raise ConflictError(
            "Nursing task is already terminal"
        )

    reason = _normalize_required_text(
        reason,
        "reason",
        10000,
    )

    task.status = (
        NursingCareTaskStatus.CANCELLED
    )
    task.cancelled_at = _db_now()
    task.cancellation_reason = reason
    task.completed_at = None
    task.completed_by_staff_id = None
    task.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingCareTask",
        entity_id=task.id,
        description=(
            "Cancelled nursing care task"
        ),
        new_value={
            "status": task.status.value,
            "cancelled_at": (
                task.cancelled_at.isoformat()
            ),
            "cancellation_reason": (
                task.cancellation_reason
            ),
            "version": task.version,
        },
    )

    return task


@transactional
def create_nursing_note(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    note_text: str,
    note_type=None,
    observed_at: datetime | None = None,
) -> NursingNote:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    admission = _get_admission(
        admission_id,
        clinic_id,
        lock=True,
        require_active=True,
    )

    note_text = _normalize_required_text(
        note_text,
        "note_text",
        20000,
    )
    note_type = _normalize_note_type(
        note_type
    )
    observed_at = (
        _normalize_datetime(
            observed_at,
            "observed_at",
        )
        or _db_now()
    )

    note = NursingNote(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        author_staff_id=actor.id,
        note_type=note_type,
        status=NursingNoteStatus.DRAFT,
        note_text=note_text,
        observed_at=observed_at,
        version=1,
    )

    db.session.add(
        note
    )
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingNote",
        entity_id=note.id,
        description=(
            "Created nursing note draft"
        ),
        new_value={
            "admission_id": note.admission_id,
            "patient_id": note.patient_id,
            "note_type": note.note_type.value,
            "status": note.status.value,
            "version": note.version,
        },
    )

    return note


def get_nursing_note(
    note_id: int,
    *,
    clinic_id: int,
) -> NursingNote:
    return _get_note(
        note_id,
        clinic_id,
    )


def list_nursing_notes(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[NursingNote]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    return list(
        db.session.execute(
            select(
                NursingNote
            )
            .where(
                NursingNote.clinic_id
                == clinic_id,
                NursingNote.admission_id
                == admission.id,
                NursingNote.patient_id
                == admission.patient_id,
            )
            .order_by(
                NursingNote.observed_at.desc(),
                NursingNote.id.desc(),
            )
        ).scalars().all()
    )


@transactional
def update_nursing_note_draft(
    note_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    note_text: str,
    note_type=None,
    observed_at: datetime | None = None,
) -> NursingNote:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    note = _get_note(
        note_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        note.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        note.author_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the note author can edit "
            "a nursing note draft"
        )

    if (
        note.status
        != NursingNoteStatus.DRAFT
    ):
        raise ConflictError(
            "Signed nursing notes are immutable"
        )

    note.note_text = _normalize_required_text(
        note_text,
        "note_text",
        20000,
    )

    if note_type is not None:
        note.note_type = _normalize_note_type(
            note_type
        )

    if observed_at is not None:
        note.observed_at = _normalize_datetime(
            observed_at,
            "observed_at",
        )

    note.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="NursingNote",
        entity_id=note.id,
        description=(
            "Updated nursing note draft"
        ),
        new_value={
            "note_type": note.note_type.value,
            "status": note.status.value,
            "version": note.version,
        },
    )

    return note


@transactional
def sign_nursing_note(
    note_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> NursingNote:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    idempotency_record, replay = (
        _reserve_nursing_transition(
            clinic_id=clinic_id,
            actor=actor,
            operation="nursing.note.sign",
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
            request_payload={
                "note_id": note_id,
                "actor_staff_id": actor_staff_id,
            },
            entity_type="NursingNote",
            entity_id=note_id,
            loader=_get_note,
            expected_status=NursingNoteStatus.SIGNED,
        )
    )

    if replay is not None:
        return replay
    note = _get_note(
        note_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        note.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        note.author_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the note author can sign "
            "the nursing note"
        )

    if (
        note.status
        != NursingNoteStatus.DRAFT
    ):
        raise ConflictError(
            "Nursing note is already signed"
        )

    note.status = (
        NursingNoteStatus.SIGNED
    )
    note.signed_at = _db_now()
    note.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingNote",
        entity_id=note.id,
        description=(
            "Signed nursing note"
        ),
        new_value={
            "status": note.status.value,
            "signed_at": (
                note.signed_at.isoformat()
            ),
            "version": note.version,
        },
    )


    if idempotency_record is not None:
        idempotency_record.entity_type = (
            "NursingNote"
        )
        idempotency_record.entity_id = (
            note.id
        )

    return note


@transactional
def create_handoff(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    shift_start_at: datetime,
    shift_end_at: datetime,
    incoming_staff_id: int | None = None,
    situation: str | None = None,
    background: str | None = None,
    assessment: str | None = None,
    recommendation: str | None = None,
    safety_concerns: str | None = None,
    pending_actions: str | None = None,
) -> NursingHandoff:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    admission = _get_admission(
        admission_id,
        clinic_id,
        lock=True,
        require_active=True,
    )

    incoming = None

    if incoming_staff_id is not None:
        incoming = _get_nurse(
            incoming_staff_id,
            clinic_id,
        )

        if incoming.id == actor.id:
            raise ValidationError(
                "Outgoing and incoming nurse "
                "must be different"
            )

    shift_start_at = _normalize_datetime(
        shift_start_at,
        "shift_start_at",
    )
    shift_end_at = _normalize_datetime(
        shift_end_at,
        "shift_end_at",
    )

    if (
        shift_start_at is None
        or shift_end_at is None
    ):
        raise ValidationError(
            "Shift start and end are required"
        )

    if (
        shift_end_at
        <= shift_start_at
    ):
        raise ValidationError(
            "shift_end_at must be after "
            "shift_start_at"
        )

    handoff = NursingHandoff(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        outgoing_staff_id=actor.id,
        incoming_staff_id=(
            incoming.id
            if incoming is not None
            else None
        ),
        status=NursingHandoffStatus.DRAFT,
        shift_start_at=shift_start_at,
        shift_end_at=shift_end_at,
        situation=_normalize_optional_text(
            situation,
            "situation",
            10000,
        ),
        background=_normalize_optional_text(
            background,
            "background",
            10000,
        ),
        assessment=_normalize_optional_text(
            assessment,
            "assessment",
            10000,
        ),
        recommendation=_normalize_optional_text(
            recommendation,
            "recommendation",
            10000,
        ),
        safety_concerns=_normalize_optional_text(
            safety_concerns,
            "safety_concerns",
            10000,
        ),
        pending_actions=_normalize_optional_text(
            pending_actions,
            "pending_actions",
            10000,
        ),
        version=1,
    )

    db.session.add(
        handoff
    )
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingHandoff",
        entity_id=handoff.id,
        description=(
            "Created nursing handoff draft"
        ),
        new_value={
            "admission_id": handoff.admission_id,
            "patient_id": handoff.patient_id,
            "incoming_staff_id": (
                handoff.incoming_staff_id
            ),
            "status": handoff.status.value,
            "version": handoff.version,
        },
    )

    return handoff


def get_handoff(
    handoff_id: int,
    *,
    clinic_id: int,
) -> NursingHandoff:
    return _get_handoff(
        handoff_id,
        clinic_id,
    )


def list_handoffs(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[NursingHandoff]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    return list(
        db.session.execute(
            select(
                NursingHandoff
            )
            .where(
                NursingHandoff.clinic_id
                == clinic_id,
                NursingHandoff.admission_id
                == admission.id,
                NursingHandoff.patient_id
                == admission.patient_id,
            )
            .order_by(
                NursingHandoff.shift_end_at.desc(),
                NursingHandoff.id.desc(),
            )
        ).scalars().all()
    )


def _enum_value(value):
    if value is None:
        return None
    return getattr(value, "value", value)


def _highest_task_priority(
    tasks: list[NursingCareTask],
):
    rank = {
        NursingCareTaskPriority.ROUTINE: 1,
        NursingCareTaskPriority.HIGH: 2,
        NursingCareTaskPriority.URGENT: 3,
    }

    if not tasks:
        return None

    return max(
        tasks,
        key=lambda item: rank[item.priority],
    ).priority.value


def get_handoff_summary(
    handoff_id: int,
    *,
    clinic_id: int,
) -> dict:
    handoff = _get_handoff(
        handoff_id,
        clinic_id,
    )

    admission = _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=False,
    )

    patient = db.session.execute(
        select(Patient).where(
            Patient.id == handoff.patient_id,
            Patient.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if patient is None:
        raise ConflictError(
            "Nursing handoff patient context mismatch"
        )

    bed = db.session.get(
        Bed,
        admission.bed_id,
    )

    if bed is None:
        raise ConflictError(
            "Admission bed could not be resolved"
        )

    ward = db.session.get(
        Ward,
        bed.ward_id,
    )

    if (
        ward is None
        or ward.clinic_id != clinic_id
    ):
        raise ConflictError(
            "Admission ward context mismatch"
        )

    diagnoses = list(
        db.session.execute(
            select(Consultation)
            .where(
                Consultation.clinic_id == clinic_id,
                Consultation.patient_id == patient.id,
                Consultation.status
                != ConsultationStatus.CANCELLED,
                Consultation.diagnosis.is_not(None),
                Consultation.diagnosis != "",
            )
            .order_by(
                Consultation.started_at.desc(),
                Consultation.id.desc(),
            )
            .limit(10)
        ).scalars().all()
    )

    allergies = list(
        db.session.execute(
            select(PatientAllergy)
            .where(
                PatientAllergy.clinic_id == clinic_id,
                PatientAllergy.patient_id == patient.id,
                PatientAllergy.clinical_status
                == AllergyClinicalStatus.ACTIVE.value,
                PatientAllergy.verification_status
                != AllergyVerificationStatus.REFUTED.value,
            )
            .order_by(
                PatientAllergy.recorded_at.desc(),
                PatientAllergy.id.desc(),
            )
        ).scalars().all()
    )

    medication_rows = list(
        db.session.execute(
            select(
                Prescription,
                PrescriptionItem,
                Drug,
            )
            .join(
                PrescriptionItem,
                PrescriptionItem.prescription_id
                == Prescription.id,
            )
            .join(
                Drug,
                Drug.id
                == PrescriptionItem.drug_id,
            )
            .where(
                Prescription.clinic_id == clinic_id,
                Prescription.patient_id == patient.id,
                Prescription.status
                == PrescriptionStatus.ACTIVE,
            )
            .order_by(
                PrescriptionItem.ordered_at.desc(),
                PrescriptionItem.id.desc(),
            )
        ).all()
    )

    administration_rows = list(
        db.session.execute(
            select(
                MedicationAdministration,
                PrescriptionItem,
                Drug,
            )
            .join(
                PrescriptionItem,
                PrescriptionItem.id
                == MedicationAdministration.prescription_item_id,
            )
            .join(
                Drug,
                Drug.id
                == PrescriptionItem.drug_id,
            )
            .where(
                MedicationAdministration.clinic_id
                == clinic_id,
                MedicationAdministration.patient_id
                == patient.id,
                MedicationAdministration.admission_id
                == admission.id,
                or_(
                    MedicationAdministration.status
                    == MedicationAdministrationStatus.DUE,
                    and_(
                        MedicationAdministration.status
                        == MedicationAdministrationStatus.SCHEDULED,
                        MedicationAdministration.scheduled_at
                        >= handoff.shift_start_at,
                        MedicationAdministration.scheduled_at
                        <= handoff.shift_end_at,
                    ),
                ),
            )
            .order_by(
                MedicationAdministration.scheduled_at.asc(),
                MedicationAdministration.id.asc(),
            )
        ).all()
    )

    pending_lab_orders = list(
        db.session.execute(
            select(LabOrder)
            .where(
                LabOrder.clinic_id == clinic_id,
                LabOrder.patient_id == patient.id,
                LabOrder.status.in_(
                    (
                        LabOrderStatus.ORDERED,
                        LabOrderStatus.SAMPLE_COLLECTED,
                        LabOrderStatus.IN_PROGRESS,
                    )
                ),
            )
            .order_by(
                LabOrder.created_at.desc(),
                LabOrder.id.desc(),
            )
            .limit(20)
        ).scalars().all()
    )

    open_tasks = list(
        db.session.execute(
            select(NursingCareTask)
            .where(
                NursingCareTask.clinic_id == clinic_id,
                NursingCareTask.patient_id == patient.id,
                NursingCareTask.admission_id == admission.id,
                NursingCareTask.status.in_(
                    (
                        NursingCareTaskStatus.PENDING,
                        NursingCareTaskStatus.IN_PROGRESS,
                    )
                ),
            )
            .order_by(
                NursingCareTask.due_at.asc(),
                NursingCareTask.id.asc(),
            )
        ).scalars().all()
    )

    care_plans = list(
        db.session.execute(
            select(NursingCarePlan)
            .where(
                NursingCarePlan.clinic_id == clinic_id,
                NursingCarePlan.patient_id == patient.id,
                NursingCarePlan.admission_id == admission.id,
                NursingCarePlan.status.in_(
                    (
                        NursingCarePlanStatus.ACTIVE,
                        NursingCarePlanStatus.ON_HOLD,
                    )
                ),
            )
            .order_by(
                NursingCarePlan.started_at.desc(),
                NursingCarePlan.id.desc(),
            )
        ).scalars().all()
    )

    tasks_by_plan: dict[int, list[NursingCareTask]] = {}

    for task in open_tasks:
        tasks_by_plan.setdefault(
            task.care_plan_id,
            [],
        ).append(task)

    critical_alerts = list(
        db.session.execute(
            select(ClinicalAlert)
            .where(
                ClinicalAlert.clinic_id == clinic_id,
                ClinicalAlert.patient_id == patient.id,
                ClinicalAlert.status.in_(
                    (
                        ClinicalAlertStatus.OPEN,
                        ClinicalAlertStatus.ACKNOWLEDGED,
                    )
                ),
                ClinicalAlert.severity.in_(
                    (
                        ClinicalRuleSeverity.HIGH,
                        ClinicalRuleSeverity.CRITICAL,
                    )
                ),
            )
            .order_by(
                ClinicalAlert.generated_at.desc(),
                ClinicalAlert.id.desc(),
            )
            .limit(20)
        ).scalars().all()
    )

    recent_notes = list(
        db.session.execute(
            select(NursingNote)
            .where(
                NursingNote.clinic_id == clinic_id,
                NursingNote.patient_id == patient.id,
                NursingNote.admission_id == admission.id,
                NursingNote.status
                == NursingNoteStatus.SIGNED,
            )
            .order_by(
                NursingNote.observed_at.desc(),
                NursingNote.id.desc(),
            )
            .limit(10)
        ).scalars().all()
    )

    return {
        "generated_at": _utcnow(),
        "handoff": handoff,
        "patient": {
            "id": patient.id,
            "patient_number": patient.patient_number,
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "date_of_birth": patient.date_of_birth,
            "gender": _enum_value(patient.gender),
        },
        "admission": {
            "id": admission.id,
            "status": _enum_value(admission.status),
            "admitted_at": admission.admitted_at,
            "ward_id": ward.id,
            "ward_name": ward.name,
            "ward_type": _enum_value(ward.ward_type),
            "bed_id": bed.id,
            "bed_number": bed.bed_number,
        },
        "active_diagnoses": [
            {
                "consultation_id": item.id,
                "icd10_code": item.icd10_code,
                "diagnosis": item.diagnosis,
                "consultation_status": _enum_value(
                    item.status
                ),
                "started_at": item.started_at,
            }
            for item in diagnoses
        ],
        "allergies": [
            {
                "id": item.id,
                "allergen_code": item.allergen_code,
                "allergen_name": item.allergen_name,
                "allergen_type": item.allergen_type,
                "reaction": item.reaction,
                "severity": item.severity,
                "verification_status": (
                    item.verification_status
                ),
            }
            for item in allergies
        ],
        "active_medication_orders": [
            {
                "prescription_id": prescription.id,
                "prescription_item_id": item.id,
                "drug_id": drug.id,
                "drug_name": drug.name,
                "generic_name": drug.generic_name,
                "dose_value": item.dose_value,
                "dose_unit": item.dose_unit,
                "route_code": item.route_code,
                "frequency_code": item.frequency_code,
                "order_priority": item.order_priority,
                "order_type": item.order_type,
                "is_prn": item.is_prn,
                "start_at": item.start_at,
                "end_at": item.end_at,
                "ordered_at": item.ordered_at,
            }
            for (
                prescription,
                item,
                drug,
            ) in medication_rows
        ],
        "due_medication_administrations": [
            {
                "id": administration.id,
                "prescription_item_id": (
                    administration.prescription_item_id
                ),
                "drug_id": drug.id,
                "drug_name": drug.name,
                "scheduled_at": administration.scheduled_at,
                "dose_value": administration.dose_value,
                "dose_unit": administration.dose_unit,
                "route_code": administration.route_code,
                "status": _enum_value(
                    administration.status
                ),
            }
            for (
                administration,
                _item,
                drug,
            ) in administration_rows
        ],
        "pending_lab_orders": [
            {
                "id": order.id,
                "status": _enum_value(order.status),
                "created_at": order.created_at,
                "results": [
                    {
                        "item_id": item.id,
                        "test_id": item.test_id,
                        "test_name": item.test.name,
                        "result_value": item.result_value,
                        "flag": _enum_value(item.flag),
                        "resulted_at": item.resulted_at,
                    }
                    for item in sorted(
                        order.items,
                        key=lambda row: row.id,
                    )
                ],
            }
            for order in pending_lab_orders
        ],
        "care_plan_priorities": [
            {
                "id": plan.id,
                "title": plan.title,
                "problem": plan.problem,
                "goal": plan.goal,
                "status": _enum_value(plan.status),
                "highest_open_task_priority": (
                    _highest_task_priority(
                        tasks_by_plan.get(
                            plan.id,
                            [],
                        )
                    )
                ),
                "open_task_count": len(
                    tasks_by_plan.get(
                        plan.id,
                        [],
                    )
                ),
            }
            for plan in care_plans
        ],
        "critical_alerts": [
            {
                "id": alert.id,
                "severity": _enum_value(
                    alert.severity
                ),
                "action": _enum_value(
                    alert.action
                ),
                "status": _enum_value(
                    alert.status
                ),
                "title": alert.title,
                "message": alert.message,
                "generated_at": alert.generated_at,
            }
            for alert in critical_alerts
        ],
        "tasks": [
            {
                "id": task.id,
                "care_plan_id": task.care_plan_id,
                "description": task.description,
                "priority": _enum_value(task.priority),
                "status": _enum_value(task.status),
                "due_at": task.due_at,
                "assigned_to_staff_id": (
                    task.assigned_to_staff_id
                ),
            }
            for task in open_tasks
        ],
        "recent_significant_notes": [
            {
                "id": note.id,
                "note_type": _enum_value(
                    note.note_type
                ),
                "note_text": note.note_text,
                "observed_at": note.observed_at,
                "signed_at": note.signed_at,
                "author_staff_id": (
                    note.author_staff_id
                ),
            }
            for note in recent_notes
        ],
    }


@transactional
def update_handoff_draft(
    handoff_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    incoming_staff_id: int | None = None,
    situation: str | None = None,
    background: str | None = None,
    assessment: str | None = None,
    recommendation: str | None = None,
    safety_concerns: str | None = None,
    pending_actions: str | None = None,
) -> NursingHandoff:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    handoff = _get_handoff(
        handoff_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        handoff.outgoing_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the outgoing nurse can "
            "edit the handoff draft"
        )

    if (
        handoff.status
        != NursingHandoffStatus.DRAFT
    ):
        raise ConflictError(
            "Only draft handoffs can be edited"
        )

    if incoming_staff_id is not None:
        incoming = _get_nurse(
            incoming_staff_id,
            clinic_id,
        )

        if incoming.id == actor.id:
            raise ValidationError(
                "Outgoing and incoming nurse "
                "must be different"
            )

        handoff.incoming_staff_id = (
            incoming.id
        )

    updates = {
        "situation": situation,
        "background": background,
        "assessment": assessment,
        "recommendation": recommendation,
        "safety_concerns": safety_concerns,
        "pending_actions": pending_actions,
    }

    for field_name, value in updates.items():
        if value is not None:
            setattr(
                handoff,
                field_name,
                _normalize_optional_text(
                    value,
                    field_name,
                    10000,
                ),
            )

    handoff.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="NursingHandoff",
        entity_id=handoff.id,
        description=(
            "Updated nursing handoff draft"
        ),
        new_value={
            "incoming_staff_id": (
                handoff.incoming_staff_id
            ),
            "status": handoff.status.value,
            "version": handoff.version,
        },
    )

    return handoff


@transactional
def mark_handoff_ready(
    handoff_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingHandoff:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    handoff = _get_handoff(
        handoff_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        handoff.outgoing_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the outgoing nurse can "
            "mark the handoff ready"
        )

    if (
        handoff.status
        != NursingHandoffStatus.DRAFT
    ):
        raise ConflictError(
            "Only draft handoffs can "
            "be marked ready"
        )

    if (
        handoff.incoming_staff_id
        is None
    ):
        raise ValidationError(
            "Incoming nurse is required "
            "before handoff readiness"
        )

    for field_name in (
        "situation",
        "assessment",
        "recommendation",
    ):
        if not getattr(
            handoff,
            field_name,
        ):
            raise ValidationError(
                f"{field_name} is required "
                "before handoff readiness"
            )

    handoff.status = (
        NursingHandoffStatus.READY
    )
    handoff.ready_at = _db_now()
    handoff.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingHandoff",
        entity_id=handoff.id,
        description=(
            "Marked nursing handoff ready"
        ),
        new_value={
            "status": handoff.status.value,
            "ready_at": (
                handoff.ready_at.isoformat()
            ),
            "version": handoff.version,
        },
    )

    return handoff


@transactional
def acknowledge_handoff(
    handoff_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    idempotency_key: str | None = None,
    idempotency_user_id: int | None = None,
) -> NursingHandoff:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    idempotency_record, replay = (
        _reserve_nursing_transition(
            clinic_id=clinic_id,
            actor=actor,
            operation="nursing.handoff.acknowledge",
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
            request_payload={
                "handoff_id": handoff_id,
                "actor_staff_id": actor_staff_id,
            },
            entity_type="NursingHandoff",
            entity_id=handoff_id,
            loader=_get_handoff,
            expected_status=NursingHandoffStatus.ACKNOWLEDGED,
        )
    )

    if replay is not None:
        return replay
    handoff = _get_handoff(
        handoff_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        handoff.status
        != NursingHandoffStatus.READY
    ):
        raise ConflictError(
            "Only ready handoffs can "
            "be acknowledged"
        )

    if (
        handoff.incoming_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the assigned incoming nurse "
            "can acknowledge the handoff"
        )

    handoff.status = (
        NursingHandoffStatus.ACKNOWLEDGED
    )
    handoff.acknowledged_at = (
        _db_now()
    )
    handoff.acknowledged_by_staff_id = (
        actor.id
    )
    handoff.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingHandoff",
        entity_id=handoff.id,
        description=(
            "Acknowledged nursing handoff"
        ),
        new_value={
            "status": handoff.status.value,
            "acknowledged_by_staff_id": (
                handoff
                .acknowledged_by_staff_id
            ),
            "acknowledged_at": (
                handoff
                .acknowledged_at
                .isoformat()
            ),
            "version": handoff.version,
        },
    )


    if idempotency_record is not None:
        idempotency_record.entity_type = (
            "NursingHandoff"
        )
        idempotency_record.entity_id = (
            handoff.id
        )

    return handoff


@transactional
def cancel_handoff(
    handoff_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> NursingHandoff:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )
    handoff = _get_handoff(
        handoff_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        handoff.admission_id,
        clinic_id,
        require_active=True,
    )

    if (
        handoff.outgoing_staff_id
        != actor.id
    ):
        raise ConflictError(
            "Only the outgoing nurse can "
            "cancel the handoff"
        )

    if (
        handoff.status
        not in _ALLOWED_HANDOFF_MUTABLE
    ):
        raise ConflictError(
            "Acknowledged handoffs are immutable"
        )

    handoff.status = (
        NursingHandoffStatus.CANCELLED
    )
    handoff.cancelled_at = _db_now()
    handoff.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingHandoff",
        entity_id=handoff.id,
        description=(
            "Cancelled nursing handoff"
        ),
        new_value={
            "status": handoff.status.value,
            "cancelled_at": (
                handoff.cancelled_at.isoformat()
            ),
            "version": handoff.version,
        },
    )

    return handoff
