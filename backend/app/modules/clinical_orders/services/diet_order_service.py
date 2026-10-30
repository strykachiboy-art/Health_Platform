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
from app.modules.clinical_orders.models.diet_order_model import DietOrder


_DIET_TYPES = {
    "regular",
    "clear_liquid",
    "full_liquid",
    "soft",
    "diabetic",
    "renal",
    "cardiac",
    "low_sodium",
    "high_protein",
    "npo",
    "enteral",
    "custom",
}

_FEEDING_ROUTES = {
    "oral",
    "enteral",
    "parenteral",
}

_TEXTURES = {
    "regular",
    "soft",
    "minced",
    "pureed",
    "liquid",
}

_ACTIVE_STATUSES = {
    ClinicalOrderLifecycleStatus.PENDING.value,
    ClinicalOrderLifecycleStatus.ACTIVE.value,
    ClinicalOrderLifecycleStatus.IN_PROGRESS.value,
    ClinicalOrderLifecycleStatus.ON_HOLD.value,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _positive_id(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _optional_positive_int(value, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(
            f"{field_name} must be a positive integer"
        )
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


def _normalize_choice(value, field_name: str, allowed: set[str]) -> str:
    value = _required_text(value, field_name, 64).lower()
    if value not in allowed:
        raise ValidationError(f"Invalid {field_name}")
    return value


def _normalize_priority(value) -> str:
    if value is None:
        return ClinicalOrderPriority.ROUTINE.value
    try:
        return ClinicalOrderPriority(value).value
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid diet order priority") from exc


def _get_doctor(staff_id: int, clinic_id: int) -> Staff:
    staff = db.session.execute(
        select(Staff).where(
            Staff.id == _positive_id(staff_id, "staff_id"),
            Staff.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(f"Staff {staff_id} not found")
    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError("Diet order actor must be active")
    if staff.user is None or not staff.user.is_active:
        raise ValidationError("Diet order actor user must be active")
    if staff.user.role != Role.DOCTOR:
        raise ValidationError("Diet order workflow requires doctor role")
    return staff


def _get_admission(
    admission_id: int,
    clinic_id: int,
    *,
    require_active: bool,
    lock: bool = False,
) -> Admission:
    statement = (
        select(Admission)
        .join(Bed, Admission.bed_id == Bed.id)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Admission.id == _positive_id(admission_id, "admission_id"),
            Ward.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    )

    if lock:
        statement = statement.with_for_update()

    admission = db.session.execute(statement).scalar_one_or_none()

    if admission is None:
        raise NotFoundError(f"Admission {admission_id} not found")
    if require_active and admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError("Diet order requires an active admission")
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
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id,
        )
    ).scalar_one_or_none()

    if consultation is None:
        raise NotFoundError(f"Consultation {consultation_id} not found")
    return consultation


def _get_order(
    diet_order_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> DietOrder:
    statement = select(DietOrder).where(
        DietOrder.id == _positive_id(
            diet_order_id,
            "diet_order_id",
        ),
        DietOrder.clinic_id == _positive_id(
            clinic_id,
            "clinic_id",
        ),
    )

    if lock:
        statement = statement.with_for_update()

    order = db.session.execute(statement).scalar_one_or_none()

    if order is None:
        raise NotFoundError(f"Diet order {diet_order_id} not found")

    admission = _get_admission(
        order.admission_id,
        clinic_id,
        require_active=False,
    )
    if order.patient_id != admission.patient_id:
        raise ConflictError(
            "Diet order patient/admission context mismatch"
        )
    return order


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    order: DietOrder,
    description: str,
    old_value=None,
    new_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type="DietOrder",
        entity_id=order.id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


@transactional
def create_diet_order(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    diet_type: str,
    consultation_id: int | None = None,
    custom_description: str | None = None,
    feeding_route: str | None = "oral",
    texture: str | None = None,
    fluid_restriction_ml_per_day: int | None = None,
    sodium_restriction_mg_per_day: int | None = None,
    calorie_target_kcal_per_day: int | None = None,
    instructions: str | None = None,
    priority=ClinicalOrderPriority.ROUTINE,
    start_at: datetime | None = None,
) -> DietOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=True,
        lock=True,
    )

    if consultation_id is not None:
        _get_consultation(
            consultation_id,
            clinic_id,
            admission.patient_id,
        )

    existing = db.session.execute(
        select(DietOrder).where(
            DietOrder.clinic_id == clinic_id,
            DietOrder.admission_id == admission.id,
            DietOrder.status.in_(_ACTIVE_STATUSES),
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise ConflictError(
            "Admission already has an active diet order; "
            "discontinue it before creating a new one"
        )

    diet_type = _normalize_choice(
        diet_type,
        "diet_type",
        _DIET_TYPES,
    )

    custom_description = _optional_text(
        custom_description,
        "custom_description",
        1000,
    )

    if diet_type == "custom" and custom_description is None:
        raise ValidationError(
            "custom_description is required for custom diet"
        )

    if diet_type == "npo":
        if feeding_route is not None:
            raise ValidationError(
                "NPO diet cannot have a feeding_route"
            )
        if texture is not None:
            raise ValidationError(
                "NPO diet cannot have a texture"
            )
        normalized_route = None
        normalized_texture = None
    else:
        if feeding_route is None:
            raise ValidationError(
                "feeding_route is required for non-NPO diet"
            )
        normalized_route = _normalize_choice(
            feeding_route,
            "feeding_route",
            _FEEDING_ROUTES,
        )
        normalized_texture = (
            None
            if texture is None
            else _normalize_choice(
                texture,
                "texture",
                _TEXTURES,
            )
        )

    if start_at is not None and not isinstance(start_at, datetime):
        raise ValidationError("start_at must be a datetime")

    order = DietOrder(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        consultation_id=consultation_id,
        ordered_by_staff_id=actor.id,
        diet_type=diet_type,
        custom_description=custom_description,
        feeding_route=normalized_route,
        texture=normalized_texture,
        fluid_restriction_ml_per_day=_optional_positive_int(
            fluid_restriction_ml_per_day,
            "fluid_restriction_ml_per_day",
        ),
        sodium_restriction_mg_per_day=_optional_positive_int(
            sodium_restriction_mg_per_day,
            "sodium_restriction_mg_per_day",
        ),
        calorie_target_kcal_per_day=_optional_positive_int(
            calorie_target_kcal_per_day,
            "calorie_target_kcal_per_day",
        ),
        instructions=_optional_text(
            instructions,
            "instructions",
            10000,
        ),
        priority=_normalize_priority(priority),
        status=ClinicalOrderLifecycleStatus.ACTIVE.value,
        start_at=start_at,
        ordered_at=_utcnow(),
        version=1,
    )

    db.session.add(order)
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        order=order,
        description="Created diet order",
        new_value={
            "diet_type": order.diet_type,
            "feeding_route": order.feeding_route,
            "texture": order.texture,
            "priority": order.priority,
            "status": order.status,
            "version": order.version,
        },
    )
    return order


def get_diet_order(
    diet_order_id: int,
    *,
    clinic_id: int,
) -> DietOrder:
    return _get_order(
        diet_order_id,
        clinic_id,
    )


def list_diet_orders(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[DietOrder]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )
    return list(
        db.session.execute(
            select(DietOrder)
            .where(
                DietOrder.clinic_id == clinic_id,
                DietOrder.patient_id == admission.patient_id,
                DietOrder.admission_id == admission.id,
            )
            .order_by(
                DietOrder.ordered_at.desc(),
                DietOrder.id.desc(),
            )
        ).scalars().all()
    )


@transactional
def complete_diet_order(
    diet_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    completion_note: str,
) -> DietOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(
        diet_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _ACTIVE_STATUSES:
        raise ConflictError(
            "Only active diet orders can be completed"
        )

    order.status = ClinicalOrderLifecycleStatus.COMPLETED.value
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
        order=order,
        description="Completed diet order",
        new_value={
            "status": order.status,
            "completion_note": order.completion_note,
            "version": order.version,
        },
    )
    return order


@transactional
def cancel_diet_order(
    diet_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> DietOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(
        diet_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _ACTIVE_STATUSES:
        raise ConflictError(
            "Only active diet orders can be cancelled"
        )

    order.status = ClinicalOrderLifecycleStatus.CANCELLED.value
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
        order=order,
        description="Cancelled diet order",
        new_value={
            "status": order.status,
            "cancellation_reason": order.cancellation_reason,
            "version": order.version,
        },
    )
    return order


def to_clinical_order_summary(order: DietOrder) -> dict:
    display_name = order.diet_type.replace("_", " ")
    if order.diet_type == "custom" and order.custom_description:
        display_name = order.custom_description

    return {
        "order_type": ClinicalOrderType.DIET,
        "source_entity_type": "DietOrder",
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
        "display_name": display_name,
        "instructions": order.instructions,
        "scheduled_at": order.start_at,
        "due_at": order.start_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "details": {
            "diet_type": order.diet_type,
            "custom_description": order.custom_description,
            "feeding_route": order.feeding_route,
            "texture": order.texture,
            "fluid_restriction_ml_per_day": (
                order.fluid_restriction_ml_per_day
            ),
            "sodium_restriction_mg_per_day": (
                order.sodium_restriction_mg_per_day
            ),
            "calorie_target_kcal_per_day": (
                order.calorie_target_kcal_per_day
            ),
            "version": order.version,
        },
    }
