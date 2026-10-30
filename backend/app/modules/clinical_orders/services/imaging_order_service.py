
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

from app.modules.consultation.models.consultation_model import (
    Consultation,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import (
    Admission,
    Bed,
    Ward,
)
from app.modules.clinical_orders.models.imaging_order_model import (
    ImagingOrder,
)


_MODALITIES = {
    "xray",
    "ct",
    "mri",
    "ultrasound",
    "mammography",
    "fluoroscopy",
    "nuclear_medicine",
    "other",
}

_MUTABLE_STATUSES = {
    ClinicalOrderLifecycleStatus.PENDING.value,
    ClinicalOrderLifecycleStatus.ACTIVE.value,
    ClinicalOrderLifecycleStatus.IN_PROGRESS.value,
    ClinicalOrderLifecycleStatus.ON_HOLD.value,
}


def _utcnow() -> datetime:
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def _positive_id(
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


def _required_text(
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


def _optional_text(
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


def _normalize_modality(
    value,
) -> str:
    value = _required_text(
        value,
        "modality",
        32,
    ).lower()

    if value not in _MODALITIES:
        raise ValidationError(
            "Invalid imaging modality"
        )

    return value


def _normalize_priority(
    value,
) -> str:
    if value is None:
        return ClinicalOrderPriority.ROUTINE.value

    try:
        return ClinicalOrderPriority(
            value
        ).value
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(
            "Invalid imaging order priority"
        ) from exc


def _get_doctor(
    staff_id: int,
    clinic_id: int,
) -> Staff:
    staff_id = _positive_id(
        staff_id,
        "staff_id",
    )
    clinic_id = _positive_id(
        clinic_id,
        "clinic_id",
    )

    staff = db.session.execute(
        select(Staff).where(
            Staff.id == staff_id,
            Staff.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(
            f"Staff {staff_id} not found"
        )

    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError(
            "Imaging order actor must be active"
        )

    if (
        staff.user is None
        or not staff.user.is_active
    ):
        raise ValidationError(
            "Imaging order actor user must be active"
        )

    if staff.user.role != Role.DOCTOR:
        raise ValidationError(
            "Imaging order workflow requires doctor role"
        )

    return staff


def _get_admission(
    admission_id: int,
    clinic_id: int,
    *,
    require_active: bool,
) -> Admission:
    admission_id = _positive_id(
        admission_id,
        "admission_id",
    )
    clinic_id = _positive_id(
        clinic_id,
        "clinic_id",
    )

    admission = db.session.execute(
        select(Admission)
        .join(
            Bed,
            Admission.bed_id == Bed.id,
        )
        .join(
            Ward,
            Bed.ward_id == Ward.id,
        )
        .where(
            Admission.id == admission_id,
            Ward.clinic_id == clinic_id,
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
            "Imaging order requires an active admission"
        )

    return admission


def _get_consultation(
    consultation_id: int,
    clinic_id: int,
    patient_id: int,
) -> Consultation:
    consultation = db.session.execute(
        select(Consultation).where(
            Consultation.id
            == _positive_id(
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
    imaging_order_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> ImagingOrder:
    imaging_order_id = _positive_id(
        imaging_order_id,
        "imaging_order_id",
    )
    clinic_id = _positive_id(
        clinic_id,
        "clinic_id",
    )

    statement = select(
        ImagingOrder
    ).where(
        ImagingOrder.id
        == imaging_order_id,
        ImagingOrder.clinic_id
        == clinic_id,
    )

    if lock:
        statement = statement.with_for_update()

    order = db.session.execute(
        statement
    ).scalar_one_or_none()

    if order is None:
        raise NotFoundError(
            f"Imaging order {imaging_order_id} not found"
        )

    admission = _get_admission(
        order.admission_id,
        clinic_id,
        require_active=False,
    )

    if order.patient_id != admission.patient_id:
        raise ConflictError(
            "Imaging order patient/admission context mismatch"
        )

    return order


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    order: ImagingOrder,
    description: str,
    old_value=None,
    new_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type="ImagingOrder",
        entity_id=order.id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


@transactional
def create_imaging_order(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    study_name: str,
    modality: str,
    body_site: str,
    indication: str,
    study_code: str | None = None,
    consultation_id: int | None = None,
    laterality: str | None = None,
    contrast_required: bool = False,
    contrast_type: str | None = None,
    instructions: str | None = None,
    priority=ClinicalOrderPriority.ROUTINE,
    scheduled_at: datetime | None = None,
) -> ImagingOrder:
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

    if not isinstance(
        contrast_required,
        bool,
    ):
        raise ValidationError(
            "contrast_required must be a boolean"
        )

    contrast_type = _optional_text(
        contrast_type,
        "contrast_type",
        128,
    )

    if (
        not contrast_required
        and contrast_type is not None
    ):
        raise ValidationError(
            "contrast_type requires contrast_required=true"
        )

    if (
        scheduled_at is not None
        and not isinstance(
            scheduled_at,
            datetime,
        )
    ):
        raise ValidationError(
            "scheduled_at must be a datetime"
        )

    order = ImagingOrder(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        consultation_id=consultation_id,
        ordered_by_staff_id=actor.id,
        study_code=_optional_text(
            study_code,
            "study_code",
            64,
        ),
        study_name=_required_text(
            study_name,
            "study_name",
            255,
        ),
        modality=_normalize_modality(
            modality
        ),
        body_site=_required_text(
            body_site,
            "body_site",
            255,
        ),
        laterality=_optional_text(
            laterality,
            "laterality",
            32,
        ),
        contrast_required=contrast_required,
        contrast_type=contrast_type,
        indication=_required_text(
            indication,
            "indication",
            10000,
        ),
        instructions=_optional_text(
            instructions,
            "instructions",
            10000,
        ),
        priority=_normalize_priority(
            priority
        ),
        status=ClinicalOrderLifecycleStatus.ACTIVE.value,
        scheduled_at=scheduled_at,
        ordered_at=_utcnow(),
        version=1,
    )

    db.session.add(
        order
    )
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        order=order,
        description="Created imaging order",
        new_value={
            "admission_id": order.admission_id,
            "patient_id": order.patient_id,
            "study_name": order.study_name,
            "modality": order.modality,
            "priority": order.priority,
            "status": order.status,
            "version": order.version,
        },
    )

    return order


def get_imaging_order(
    imaging_order_id: int,
    *,
    clinic_id: int,
) -> ImagingOrder:
    return _get_order(
        imaging_order_id,
        clinic_id,
    )


def list_imaging_orders(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[ImagingOrder]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )

    return list(
        db.session.execute(
            select(ImagingOrder)
            .where(
                ImagingOrder.clinic_id
                == clinic_id,
                ImagingOrder.patient_id
                == admission.patient_id,
                ImagingOrder.admission_id
                == admission.id,
            )
            .order_by(
                ImagingOrder.ordered_at.desc(),
                ImagingOrder.id.desc(),
            )
        ).scalars().all()
    )


@transactional
def cancel_imaging_order(
    imaging_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> ImagingOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )

    order = _get_order(
        imaging_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active imaging orders can be cancelled"
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
        order=order,
        description="Cancelled imaging order",
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


@transactional
def complete_imaging_order(
    imaging_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    report_text: str,
    result_summary: str | None = None,
    performed_at: datetime | None = None,
) -> ImagingOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )

    order = _get_order(
        imaging_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active imaging orders can be completed"
        )

    if performed_at is None:
        performed_at = _utcnow()
    elif not isinstance(
        performed_at,
        datetime,
    ):
        raise ValidationError(
            "performed_at must be a datetime"
        )

    old_status = order.status

    order.status = (
        ClinicalOrderLifecycleStatus.COMPLETED.value
    )
    order.completed_by_staff_id = actor.id
    order.performed_at = performed_at
    order.result_summary = _optional_text(
        result_summary,
        "result_summary",
        10000,
    )
    order.report_text = _required_text(
        report_text,
        "report_text",
        20000,
    )
    order.completed_at = _utcnow()
    order.version += 1

    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        order=order,
        description="Completed imaging order",
        old_value={
            "status": old_status,
        },
        new_value={
            "status": order.status,
            "performed_at": (
                order.performed_at.isoformat()
                if order.performed_at is not None
                else None
            ),
            "version": order.version,
        },
    )

    return order


def to_clinical_order_summary(
    order: ImagingOrder,
) -> dict:
    return {
        "order_type": ClinicalOrderType.IMAGING,
        "source_entity_type": "ImagingOrder",
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
        "display_name": order.study_name,
        "instructions": order.instructions,
        "scheduled_at": order.scheduled_at,
        "due_at": order.scheduled_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "details": {
            "study_code": order.study_code,
            "modality": order.modality,
            "body_site": order.body_site,
            "laterality": order.laterality,
            "contrast_required": (
                order.contrast_required
            ),
            "contrast_type": order.contrast_type,
            "indication": order.indication,
            "performed_at": order.performed_at,
            "result_summary": order.result_summary,
            "report_text": order.report_text,
            "version": order.version,
        },
    }
