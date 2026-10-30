from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
    ClinicalOrderType,
)
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.clinical_orders.models.nursing_order_model import (
    NursingOrder,
    NursingOrderExecution,
)


_CATEGORIES = {
    "monitoring",
    "activity",
    "positioning",
    "wound_care",
    "intake_output",
    "safety",
    "specimen",
    "respiratory",
    "catheter",
    "hygiene",
    "communication",
    "other",
}

_FREQUENCIES = {
    "once",
    "q1h",
    "q2h",
    "q4h",
    "q6h",
    "q8h",
    "q12h",
    "daily",
    "prn",
    "continuous",
    "custom",
}

_EXECUTABLE_STATUSES = {
    ClinicalOrderLifecycleStatus.ACTIVE.value,
    ClinicalOrderLifecycleStatus.IN_PROGRESS.value,
}

_TERMINABLE_STATUSES = {
    ClinicalOrderLifecycleStatus.ACTIVE.value,
    ClinicalOrderLifecycleStatus.IN_PROGRESS.value,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_datetime(
    value: datetime | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None

    if not isinstance(value, datetime):
        raise ValidationError(
            f"{field_name} must be a datetime"
        )

    if value.tzinfo is not None:
        value = (
            value
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    return value


def _positive_id(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _required_text(value, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")

    value = value.strip()

    if not value:
        raise ValidationError(f"{field_name} is required")

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _optional_text(value, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )

    return value


def _normalize_choice(
    value,
    field_name: str,
    allowed: set[str],
) -> str:
    value = _required_text(
        value,
        field_name,
        64,
    ).lower()

    if value not in allowed:
        raise ValidationError(
            f"Invalid {field_name}"
        )

    return value


def _normalize_priority(value) -> str:
    if value is None:
        return ClinicalOrderPriority.ROUTINE.value

    try:
        return ClinicalOrderPriority(value).value
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "Invalid nursing order priority"
        ) from exc


def _get_staff(
    staff_id: int,
    clinic_id: int,
    *,
    required_role: Role,
    role_message: str,
) -> Staff:
    staff = db.session.execute(
        select(Staff).where(
            Staff.id == _positive_id(
                staff_id,
                "staff_id",
            ),
            Staff.clinic_id == _positive_id(
                clinic_id,
                "clinic_id",
            ),
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Nursing order actor must be active"
        )

    if staff.user is None or not staff.user.is_active:
        raise ValidationError(
            "Nursing order actor user must be active"
        )

    if staff.user.role != required_role:
        raise ValidationError(role_message)

    return staff


def _get_doctor(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    return _get_staff(
        staff_id,
        clinic_id,
        required_role=Role.DOCTOR,
        role_message=(
            "Nursing order prescribing requires doctor role"
        ),
    )


def _get_nurse(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    return _get_staff(
        staff_id,
        clinic_id,
        required_role=Role.NURSE,
        role_message=(
            "Nursing order execution requires nurse role"
        ),
    )


def _get_admission(
    admission_id: int,
    clinic_id: int,
    *,
    require_active: bool,
) -> Admission:
    admission = db.session.execute(
        select(Admission)
        .join(Bed, Admission.bed_id == Bed.id)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Admission.id == _positive_id(
                admission_id,
                "admission_id",
            ),
            Ward.clinic_id == _positive_id(
                clinic_id,
                "clinic_id",
            ),
        )
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
            "Nursing order requires an active admission"
        )

    return admission


def _get_consultation(
    consultation_id: int,
    clinic_id: int,
    patient_id: int,
) -> Consultation:
    consultation = db.session.execute(
        select(Consultation).where(
            Consultation.id == _positive_id(
                consultation_id,
                "consultation_id",
            ),
            Consultation.clinic_id
            == clinic_id,
            Consultation.patient_id
            == patient_id,
        )
    ).scalar_one_or_none()

    if consultation is None:
        raise NotFoundError(
            f"Consultation {consultation_id} not found"
        )

    return consultation


def _get_order(
    nursing_order_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> NursingOrder:
    statement = select(
        NursingOrder
    ).where(
        NursingOrder.id
        == _positive_id(
            nursing_order_id,
            "nursing_order_id",
        ),
        NursingOrder.clinic_id
        == _positive_id(
            clinic_id,
            "clinic_id",
        ),
    )

    if lock:
        statement = statement.with_for_update()

    order = db.session.execute(
        statement
    ).scalar_one_or_none()

    if order is None:
        raise NotFoundError(
            f"Nursing order {nursing_order_id} not found"
        )

    admission = _get_admission(
        order.admission_id,
        clinic_id,
        require_active=False,
    )

    if order.patient_id != admission.patient_id:
        raise ConflictError(
            "Nursing order patient/admission context mismatch"
        )

    return order


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    entity_type: str,
    entity_id: int,
    description: str,
    old_value=None,
    new_value=None,
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


@transactional
def create_nursing_order(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    category: str,
    order_text: str,
    frequency: str,
    consultation_id: int | None = None,
    instructions: str | None = None,
    custom_frequency: str | None = None,
    assigned_to_staff_id: int | None = None,
    priority=ClinicalOrderPriority.ROUTINE,
    scheduled_at: datetime | None = None,
) -> NursingOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )

    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=True,
    )

    if consultation_id is not None:
        _get_consultation(
            consultation_id,
            clinic_id,
            admission.patient_id,
        )

    assignee = None
    if assigned_to_staff_id is not None:
        assignee = _get_nurse(
            assigned_to_staff_id,
            clinic_id,
        )

    category = _normalize_choice(
        category,
        "category",
        _CATEGORIES,
    )
    frequency = _normalize_choice(
        frequency,
        "frequency",
        _FREQUENCIES,
    )
    custom_frequency = _optional_text(
        custom_frequency,
        "custom_frequency",
        255,
    )

    if (
        frequency == "custom"
        and custom_frequency is None
    ):
        raise ValidationError(
            "custom_frequency is required for custom frequency"
        )

    if (
        frequency != "custom"
        and custom_frequency is not None
    ):
        raise ValidationError(
            "custom_frequency is only allowed "
            "for custom frequency"
        )

    order = NursingOrder(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        consultation_id=consultation_id,
        ordered_by_staff_id=actor.id,
        assigned_to_staff_id=(
            assignee.id
            if assignee is not None
            else None
        ),
        category=category,
        order_text=_required_text(
            order_text,
            "order_text",
            10000,
        ),
        instructions=_optional_text(
            instructions,
            "instructions",
            10000,
        ),
        frequency=frequency,
        custom_frequency=custom_frequency,
        priority=_normalize_priority(
            priority
        ),
        status=ClinicalOrderLifecycleStatus.ACTIVE.value,
        scheduled_at=_normalize_datetime(
            scheduled_at,
            "scheduled_at",
        ),
        ordered_at=_utcnow(),
        version=1,
    )

    db.session.add(order)
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingOrder",
        entity_id=order.id,
        description="Created nursing order",
        new_value={
            "category": order.category,
            "frequency": order.frequency,
            "priority": order.priority,
            "status": order.status,
            "assigned_to_staff_id": (
                order.assigned_to_staff_id
            ),
            "version": order.version,
        },
    )

    return order


def get_nursing_order(
    nursing_order_id: int,
    *,
    clinic_id: int,
) -> NursingOrder:
    return _get_order(
        nursing_order_id,
        clinic_id,
    )


def list_nursing_orders(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[NursingOrder]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    return list(
        db.session.execute(
            select(NursingOrder)
            .where(
                NursingOrder.clinic_id
                == clinic_id,
                NursingOrder.patient_id
                == admission.patient_id,
                NursingOrder.admission_id
                == admission.id,
            )
            .order_by(
                NursingOrder.scheduled_at.asc(),
                NursingOrder.ordered_at.desc(),
                NursingOrder.id.desc(),
            )
        ).scalars().all()
    )


def list_nursing_order_executions(
    nursing_order_id: int,
    *,
    clinic_id: int,
) -> list[NursingOrderExecution]:
    order = _get_order(
        nursing_order_id,
        clinic_id,
    )

    return list(
        db.session.execute(
            select(NursingOrderExecution)
            .where(
                NursingOrderExecution.nursing_order_id
                == order.id,
                NursingOrderExecution.clinic_id
                == clinic_id,
                NursingOrderExecution.patient_id
                == order.patient_id,
                NursingOrderExecution.admission_id
                == order.admission_id,
            )
            .order_by(
                NursingOrderExecution.performed_at.asc(),
                NursingOrderExecution.id.asc(),
            )
        ).scalars().all()
    )


@transactional
def record_nursing_order_execution(
    nursing_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    result_note: str,
    performed_at: datetime | None = None,
) -> NursingOrderExecution:
    actor = _get_nurse(
        actor_staff_id,
        clinic_id,
    )

    order = _get_order(
        nursing_order_id,
        clinic_id,
        lock=True,
    )

    _get_admission(
        order.admission_id,
        clinic_id,
        require_active=True,
    )

    if order.status not in _EXECUTABLE_STATUSES:
        raise ConflictError(
            "Nursing order is not executable"
        )

    if (
        order.assigned_to_staff_id is not None
        and order.assigned_to_staff_id != actor.id
    ):
        raise ConflictError(
            "Nursing order is assigned to a different nurse"
        )

    performed_at = (
        _normalize_datetime(
            performed_at,
            "performed_at",
        )
        or _utcnow()
    )

    execution = NursingOrderExecution(
        nursing_order_id=order.id,
        clinic_id=order.clinic_id,
        patient_id=order.patient_id,
        admission_id=order.admission_id,
        performed_by_staff_id=actor.id,
        performed_at=performed_at,
        result_note=_required_text(
            result_note,
            "result_note",
            10000,
        ),
    )

    db.session.add(execution)
    db.session.flush()

    old_status = order.status

    if order.frequency == "once":
        order.status = (
            ClinicalOrderLifecycleStatus.COMPLETED.value
        )
        order.completed_by_staff_id = actor.id
        order.completed_at = performed_at
        order.completion_note = (
            "Completed through nursing execution"
        )
    elif (
        order.status
        == ClinicalOrderLifecycleStatus.ACTIVE.value
    ):
        order.status = (
            ClinicalOrderLifecycleStatus.IN_PROGRESS.value
        )

    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="NursingOrderExecution",
        entity_id=execution.id,
        description="Recorded nursing order execution",
        new_value={
            "nursing_order_id": order.id,
            "performed_at": (
                execution.performed_at.isoformat()
            ),
            "order_status_before": old_status,
            "order_status_after": order.status,
            "order_version": order.version,
        },
    )

    return execution


@transactional
def complete_nursing_order(
    nursing_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    completion_note: str,
) -> NursingOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )

    order = _get_order(
        nursing_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _TERMINABLE_STATUSES:
        raise ConflictError(
            "Only active nursing orders can be completed"
        )

    if order.frequency == "once":
        raise ConflictError(
            "One-time nursing orders complete "
            "through nurse execution"
        )

    old_status = order.status

    order.status = (
        ClinicalOrderLifecycleStatus.COMPLETED.value
    )
    order.completed_by_staff_id = actor.id
    order.completion_note = _required_text(
        completion_note,
        "completion_note",
        10000,
    )
    order.completed_at = _utcnow()
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingOrder",
        entity_id=order.id,
        description="Completed nursing order",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": order.status,
            "completion_note": order.completion_note,
            "version": order.version,
        },
    )

    return order


@transactional
def cancel_nursing_order(
    nursing_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> NursingOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )

    order = _get_order(
        nursing_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _TERMINABLE_STATUSES:
        raise ConflictError(
            "Only active nursing orders can be cancelled"
        )

    old_status = order.status

    order.status = (
        ClinicalOrderLifecycleStatus.CANCELLED.value
    )
    order.cancelled_by_staff_id = actor.id
    order.cancelled_at = _utcnow()
    order.cancellation_reason = _required_text(
        reason,
        "reason",
        10000,
    )
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        entity_type="NursingOrder",
        entity_id=order.id,
        description="Cancelled nursing order",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": order.status,
            "cancellation_reason": (
                order.cancellation_reason
            ),
            "version": order.version,
        },
    )

    return order


def to_clinical_order_summary(
    order: NursingOrder,
) -> dict:
    return {
        "order_type": ClinicalOrderType.NURSING,
        "source_entity_type": "NursingOrder",
        "source_entity_id": order.id,
        "clinic_id": order.clinic_id,
        "patient_id": order.patient_id,
        "admission_id": order.admission_id,
        "consultation_id": order.consultation_id,
        "ordered_by_staff_id": order.ordered_by_staff_id,
        "ordered_at": order.ordered_at,
        "priority": order.priority,
        "status": order.status,
        "source_status": order.status,
        "display_name": order.order_text,
        "instructions": order.instructions,
        "scheduled_at": order.scheduled_at,
        "due_at": order.scheduled_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "details": {
            "category": order.category,
            "frequency": order.frequency,
            "custom_frequency": (
                order.custom_frequency
            ),
            "assigned_to_staff_id": (
                order.assigned_to_staff_id
            ),
            "version": order.version,
        },
    }
