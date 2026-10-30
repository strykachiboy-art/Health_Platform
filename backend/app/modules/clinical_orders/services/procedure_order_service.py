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
from app.modules.clinical_orders.models.procedure_order_model import ProcedureOrder


_PROCEDURE_TYPES = {
    "diagnostic",
    "therapeutic",
    "surgical",
    "bedside",
    "interventional",
    "other",
}

_MUTABLE_STATUSES = {
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


def _normalize_type(value) -> str:
    value = _required_text(value, "procedure_type", 32).lower()
    if value not in _PROCEDURE_TYPES:
        raise ValidationError("Invalid procedure type")
    return value


def _normalize_priority(value) -> str:
    if value is None:
        return ClinicalOrderPriority.ROUTINE.value
    try:
        return ClinicalOrderPriority(value).value
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid procedure order priority") from exc


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
        raise ValidationError("Procedure order actor must be active")
    if staff.user is None or not staff.user.is_active:
        raise ValidationError("Procedure order actor user must be active")
    if staff.user.role != Role.DOCTOR:
        raise ValidationError("Procedure order workflow requires doctor role")
    return staff


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
            Admission.id == _positive_id(admission_id, "admission_id"),
            Ward.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()

    if admission is None:
        raise NotFoundError(f"Admission {admission_id} not found")
    if require_active and admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError("Procedure order requires an active admission")
    return admission


def _get_consultation(
    consultation_id: int,
    clinic_id: int,
    patient_id: int,
) -> Consultation:
    consultation = db.session.execute(
        select(Consultation).where(
            Consultation.id == _positive_id(consultation_id, "consultation_id"),
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id,
        )
    ).scalar_one_or_none()

    if consultation is None:
        raise NotFoundError(f"Consultation {consultation_id} not found")
    return consultation


def _get_order(
    procedure_order_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> ProcedureOrder:
    statement = select(ProcedureOrder).where(
        ProcedureOrder.id == _positive_id(
            procedure_order_id, "procedure_order_id"
        ),
        ProcedureOrder.clinic_id == _positive_id(clinic_id, "clinic_id"),
    )

    if lock:
        statement = statement.with_for_update()

    order = db.session.execute(statement).scalar_one_or_none()

    if order is None:
        raise NotFoundError(f"Procedure order {procedure_order_id} not found")

    admission = _get_admission(
        order.admission_id,
        clinic_id,
        require_active=False,
    )
    if order.patient_id != admission.patient_id:
        raise ConflictError(
            "Procedure order patient/admission context mismatch"
        )
    return order


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    order: ProcedureOrder,
    description: str,
    old_value=None,
    new_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type="ProcedureOrder",
        entity_id=order.id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


@transactional
def create_procedure_order(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    procedure_name: str,
    procedure_type: str,
    indication: str,
    procedure_code: str | None = None,
    consultation_id: int | None = None,
    body_site: str | None = None,
    laterality: str | None = None,
    prep_instructions: str | None = None,
    anesthesia_type: str | None = None,
    consent_required: bool = False,
    priority=ClinicalOrderPriority.ROUTINE,
    scheduled_at: datetime | None = None,
) -> ProcedureOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
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

    if not isinstance(consent_required, bool):
        raise ValidationError("consent_required must be a boolean")
    if scheduled_at is not None and not isinstance(scheduled_at, datetime):
        raise ValidationError("scheduled_at must be a datetime")

    order = ProcedureOrder(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        consultation_id=consultation_id,
        ordered_by_staff_id=actor.id,
        procedure_code=_optional_text(
            procedure_code, "procedure_code", 64
        ),
        procedure_name=_required_text(
            procedure_name, "procedure_name", 255
        ),
        procedure_type=_normalize_type(procedure_type),
        body_site=_optional_text(body_site, "body_site", 255),
        laterality=_optional_text(laterality, "laterality", 32),
        indication=_required_text(indication, "indication", 10000),
        prep_instructions=_optional_text(
            prep_instructions, "prep_instructions", 10000
        ),
        anesthesia_type=_optional_text(
            anesthesia_type, "anesthesia_type", 128
        ),
        consent_required=consent_required,
        priority=_normalize_priority(priority),
        status=ClinicalOrderLifecycleStatus.ACTIVE.value,
        scheduled_at=scheduled_at,
        ordered_at=_utcnow(),
        version=1,
    )

    db.session.add(order)
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.CREATE,
        order=order,
        description="Created procedure order",
        new_value={
            "admission_id": order.admission_id,
            "patient_id": order.patient_id,
            "procedure_name": order.procedure_name,
            "procedure_type": order.procedure_type,
            "consent_required": order.consent_required,
            "priority": order.priority,
            "status": order.status,
            "version": order.version,
        },
    )
    return order


def get_procedure_order(
    procedure_order_id: int,
    *,
    clinic_id: int,
) -> ProcedureOrder:
    return _get_order(procedure_order_id, clinic_id)


def list_procedure_orders(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[ProcedureOrder]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )
    return list(
        db.session.execute(
            select(ProcedureOrder)
            .where(
                ProcedureOrder.clinic_id == clinic_id,
                ProcedureOrder.patient_id == admission.patient_id,
                ProcedureOrder.admission_id == admission.id,
            )
            .order_by(
                ProcedureOrder.ordered_at.desc(),
                ProcedureOrder.id.desc(),
            )
        ).scalars().all()
    )


@transactional
def verify_procedure_consent(
    procedure_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
) -> ProcedureOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(
        procedure_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Consent cannot be verified for a terminal procedure order"
        )
    if not order.consent_required:
        raise ConflictError("Procedure order does not require consent")
    if order.consent_verified_at is not None:
        return order

    order.consent_verified_at = _utcnow()
    order.consent_verified_by_staff_id = actor.id
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        order=order,
        description="Verified procedure consent",
        new_value={
            "consent_verified_at": order.consent_verified_at.isoformat(),
            "version": order.version,
        },
    )
    return order


@transactional
def cancel_procedure_order(
    procedure_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> ProcedureOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(
        procedure_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active procedure orders can be cancelled"
        )

    old_status = order.status
    order.status = ClinicalOrderLifecycleStatus.CANCELLED.value
    order.cancelled_by_staff_id = actor.id
    order.cancelled_at = _utcnow()
    order.cancellation_reason = _required_text(
        reason, "reason", 10000
    )
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        order=order,
        description="Cancelled procedure order",
        old_value={"status": old_status},
        new_value={
            "status": order.status,
            "cancellation_reason": order.cancellation_reason,
            "version": order.version,
        },
    )
    return order


@transactional
def complete_procedure_order(
    procedure_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    procedure_note: str,
    outcome_summary: str | None = None,
    performed_at: datetime | None = None,
) -> ProcedureOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(
        procedure_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active procedure orders can be completed"
        )
    if order.consent_required and order.consent_verified_at is None:
        raise ConflictError(
            "Required procedure consent has not been verified"
        )

    if performed_at is None:
        performed_at = _utcnow()
    elif not isinstance(performed_at, datetime):
        raise ValidationError("performed_at must be a datetime")

    old_status = order.status
    order.status = ClinicalOrderLifecycleStatus.COMPLETED.value
    order.completed_by_staff_id = actor.id
    order.performed_at = performed_at
    order.outcome_summary = _optional_text(
        outcome_summary, "outcome_summary", 10000
    )
    order.procedure_note = _required_text(
        procedure_note, "procedure_note", 20000
    )
    order.completed_at = _utcnow()
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        order=order,
        description="Completed procedure order",
        old_value={"status": old_status},
        new_value={
            "status": order.status,
            "performed_at": order.performed_at.isoformat(),
            "version": order.version,
        },
    )
    return order


def to_clinical_order_summary(order: ProcedureOrder) -> dict:
    return {
        "order_type": ClinicalOrderType.PROCEDURE,
        "source_entity_type": "ProcedureOrder",
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
        "display_name": order.procedure_name,
        "instructions": order.prep_instructions,
        "scheduled_at": order.scheduled_at,
        "due_at": order.scheduled_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "details": {
            "procedure_code": order.procedure_code,
            "procedure_type": order.procedure_type,
            "body_site": order.body_site,
            "laterality": order.laterality,
            "indication": order.indication,
            "anesthesia_type": order.anesthesia_type,
            "consent_required": order.consent_required,
            "consent_verified_at": order.consent_verified_at,
            "performed_at": order.performed_at,
            "outcome_summary": order.outcome_summary,
            "procedure_note": order.procedure_note,
            "version": order.version,
        },
    }
