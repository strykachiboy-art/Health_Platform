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
from app.core.enums.patient_enums import BloodType
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
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)


_PRODUCT_TYPES = {
    "packed_red_blood_cells",
    "whole_blood",
    "platelets",
    "fresh_frozen_plasma",
    "cryoprecipitate",
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


def _positive_units(value, field_name: str) -> int:
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


def _normalize_product_type(value) -> str:
    value = _required_text(value, "product_type", 40).lower()
    if value not in _PRODUCT_TYPES:
        raise ValidationError("Invalid blood product type")
    return value


def _normalize_priority(value) -> str:
    if value is None:
        return ClinicalOrderPriority.ROUTINE.value
    try:
        return ClinicalOrderPriority(value).value
    except (TypeError, ValueError) as exc:
        raise ValidationError("Invalid blood product order priority") from exc


def _blood_type_value(value) -> str:
    return getattr(value, "value", value)


def _get_staff(
    staff_id: int,
    clinic_id: int,
    *,
    allowed_roles: tuple[Role, ...],
    role_message: str,
) -> Staff:
    staff = db.session.execute(
        select(Staff).where(
            Staff.id == _positive_id(staff_id, "staff_id"),
            Staff.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()

    if staff is None:
        raise NotFoundError(f"Staff {staff_id} not found")
    if staff.status != StaffStatus.ACTIVE:
        raise ValidationError("Blood product actor must be active")
    if staff.user is None or not staff.user.is_active:
        raise ValidationError("Blood product actor user must be active")
    if staff.user.role not in allowed_roles:
        raise ValidationError(role_message)
    return staff


def _get_doctor(staff_id: int, clinic_id: int) -> Staff:
    return _get_staff(
        staff_id,
        clinic_id,
        allowed_roles=(Role.DOCTOR,),
        role_message="Blood product ordering requires doctor role",
    )


def _get_lab_technician(staff_id: int, clinic_id: int) -> Staff:
    return _get_staff(
        staff_id,
        clinic_id,
        allowed_roles=(Role.LAB_TECHNICIAN,),
        role_message=(
            "Blood compatibility verification requires lab technician role"
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
            Admission.id == _positive_id(admission_id, "admission_id"),
            Ward.clinic_id == _positive_id(clinic_id, "clinic_id"),
        )
    ).scalar_one_or_none()

    if admission is None:
        raise NotFoundError(f"Admission {admission_id} not found")
    if require_active and admission.status != AdmissionStatus.ADMITTED:
        raise ConflictError("Blood product order requires an active admission")
    return admission


def _get_patient(patient_id: int, clinic_id: int) -> Patient:
    patient = db.session.execute(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(f"Patient {patient_id} not found")
    return patient


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
    blood_product_order_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> BloodProductOrder:
    statement = select(BloodProductOrder).where(
        BloodProductOrder.id == _positive_id(
            blood_product_order_id,
            "blood_product_order_id",
        ),
        BloodProductOrder.clinic_id == _positive_id(clinic_id, "clinic_id"),
    )

    if lock:
        statement = statement.with_for_update()

    order = db.session.execute(statement).scalar_one_or_none()

    if order is None:
        raise NotFoundError(
            f"Blood product order {blood_product_order_id} not found"
        )

    admission = _get_admission(
        order.admission_id,
        clinic_id,
        require_active=False,
    )
    if order.patient_id != admission.patient_id:
        raise ConflictError(
            "Blood product order patient/admission context mismatch"
        )
    return order


def _audit(
    *,
    actor: Staff,
    action: AuditAction,
    order: BloodProductOrder,
    description: str,
    old_value=None,
    new_value=None,
) -> None:
    create_audit_log(
        action=action,
        entity_type="BloodProductOrder",
        entity_id=order.id,
        description=description,
        old_value=old_value,
        new_value=new_value,
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


@transactional
def create_blood_product_order(
    *,
    admission_id: int,
    clinic_id: int,
    actor_staff_id: int,
    product_type: str,
    units_requested: int,
    indication: str,
    consultation_id: int | None = None,
    special_requirements: str | None = None,
    priority=ClinicalOrderPriority.ROUTINE,
    scheduled_at: datetime | None = None,
) -> BloodProductOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=True,
    )
    patient = _get_patient(
        admission.patient_id,
        clinic_id,
    )

    if consultation_id is not None:
        _get_consultation(
            consultation_id,
            clinic_id,
            patient.id,
        )

    if scheduled_at is not None and not isinstance(scheduled_at, datetime):
        raise ValidationError("scheduled_at must be a datetime")

    order = BloodProductOrder(
        clinic_id=clinic_id,
        patient_id=patient.id,
        admission_id=admission.id,
        consultation_id=consultation_id,
        ordered_by_staff_id=actor.id,
        product_type=_normalize_product_type(product_type),
        units_requested=_positive_units(
            units_requested,
            "units_requested",
        ),
        units_issued=0,
        indication=_required_text(
            indication,
            "indication",
            10000,
        ),
        special_requirements=_optional_text(
            special_requirements,
            "special_requirements",
            10000,
        ),
        patient_blood_type_at_order=_blood_type_value(
            patient.blood_type
        ),
        compatibility_status="pending",
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
        description="Created blood product order",
        new_value={
            "product_type": order.product_type,
            "units_requested": order.units_requested,
            "patient_blood_type_at_order": (
                order.patient_blood_type_at_order
            ),
            "priority": order.priority,
            "status": order.status,
            "version": order.version,
        },
    )
    return order


def get_blood_product_order(
    blood_product_order_id: int,
    *,
    clinic_id: int,
) -> BloodProductOrder:
    return _get_order(
        blood_product_order_id,
        clinic_id,
    )


def list_blood_product_orders(
    *,
    admission_id: int,
    clinic_id: int,
) -> list[BloodProductOrder]:
    admission = _get_admission(
        admission_id,
        clinic_id,
        require_active=False,
    )
    return list(
        db.session.execute(
            select(BloodProductOrder)
            .where(
                BloodProductOrder.clinic_id == clinic_id,
                BloodProductOrder.patient_id == admission.patient_id,
                BloodProductOrder.admission_id == admission.id,
            )
            .order_by(
                BloodProductOrder.ordered_at.desc(),
                BloodProductOrder.id.desc(),
            )
        ).scalars().all()
    )


@transactional
def verify_blood_product_compatibility(
    blood_product_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    emergency_release: bool = False,
    crossmatch_reference: str | None = None,
    product_blood_type: BloodType | str | None = None,
    notes: str | None = None,
    emergency_release_reason: str | None = None,
) -> BloodProductOrder:
    actor = _get_lab_technician(
        actor_staff_id,
        clinic_id,
    )
    order = _get_order(
        blood_product_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Compatibility cannot be verified for a terminal blood product order"
        )
    if order.compatibility_status != "pending":
        raise ConflictError(
            "Blood product compatibility has already been verified"
        )
    if not isinstance(emergency_release, bool):
        raise ValidationError("emergency_release must be a boolean")

    patient = _get_patient(
        order.patient_id,
        clinic_id,
    )
    current_blood_type = _blood_type_value(
        patient.blood_type
    )

    normalized_product_type = None
    if product_blood_type is not None:
        try:
            normalized_product_type = BloodType(
                _blood_type_value(product_blood_type)
            ).value
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "Invalid product blood type"
            ) from exc

    if emergency_release:
        if emergency_release_reason is None:
            raise ValidationError(
                "emergency_release_reason is required"
            )

        reason = _required_text(
            emergency_release_reason,
            "emergency_release_reason",
            10000,
        )
        order.compatibility_status = "emergency_release"
        order.emergency_release_reason = reason
        order.crossmatch_reference = _optional_text(
            crossmatch_reference,
            "crossmatch_reference",
            128,
        )
    else:
        if current_blood_type == BloodType.UNKNOWN.value:
            raise ConflictError(
                "Patient blood type must be known before compatibility verification"
            )
        order.crossmatch_reference = _required_text(
            crossmatch_reference,
            "crossmatch_reference",
            128,
        )
        order.compatibility_status = "compatible"
        order.emergency_release_reason = None

    order.patient_blood_type_verified = current_blood_type
    order.product_blood_type = normalized_product_type
    order.compatibility_notes = _optional_text(
        notes,
        "notes",
        10000,
    )
    order.compatibility_verified_by_staff_id = actor.id
    order.compatibility_verified_at = _utcnow()
    order.version += 1

    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        order=order,
        description="Verified blood product compatibility",
        new_value={
            "compatibility_status": order.compatibility_status,
            "patient_blood_type_verified": (
                order.patient_blood_type_verified
            ),
            "product_blood_type": order.product_blood_type,
            "crossmatch_reference": order.crossmatch_reference,
            "version": order.version,
        },
    )
    return order


@transactional
def fulfill_blood_product_order(
    blood_product_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    units_issued: int,
    fulfillment_note: str,
) -> BloodProductOrder:
    actor = _get_lab_technician(
        actor_staff_id,
        clinic_id,
    )
    order = _get_order(
        blood_product_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active blood product orders can be fulfilled"
        )
    if order.compatibility_status == "pending":
        raise ConflictError(
            "Blood product compatibility must be verified before fulfillment"
        )

    units_issued = _positive_units(
        units_issued,
        "units_issued",
    )
    if units_issued > order.units_requested:
        raise ValidationError(
            "units_issued cannot exceed units_requested"
        )

    order.units_issued = units_issued
    order.fulfilled_by_staff_id = actor.id
    order.fulfillment_note = _required_text(
        fulfillment_note,
        "fulfillment_note",
        10000,
    )
    order.status = ClinicalOrderLifecycleStatus.COMPLETED.value
    order.completed_at = _utcnow()
    order.version += 1

    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.STATUS_CHANGE,
        order=order,
        description="Fulfilled blood product order",
        new_value={
            "status": order.status,
            "units_issued": order.units_issued,
            "compatibility_status": order.compatibility_status,
            "version": order.version,
        },
    )
    return order


@transactional
def cancel_blood_product_order(
    blood_product_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reason: str,
) -> BloodProductOrder:
    actor = _get_doctor(
        actor_staff_id,
        clinic_id,
    )
    order = _get_order(
        blood_product_order_id,
        clinic_id,
        lock=True,
    )

    if order.status not in _MUTABLE_STATUSES:
        raise ConflictError(
            "Only active blood product orders can be cancelled"
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
        description="Cancelled blood product order",
        new_value={
            "status": order.status,
            "cancellation_reason": order.cancellation_reason,
            "version": order.version,
        },
    )
    return order


def to_clinical_order_summary(order: BloodProductOrder) -> dict:
    return {
        "order_type": ClinicalOrderType.BLOOD_PRODUCT,
        "source_entity_type": "BloodProductOrder",
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
        "display_name": order.product_type.replace("_", " "),
        "instructions": order.special_requirements,
        "scheduled_at": order.scheduled_at,
        "due_at": order.scheduled_at,
        "completed_at": order.completed_at,
        "cancelled_at": order.cancelled_at,
        "details": {
            "product_type": order.product_type,
            "units_requested": order.units_requested,
            "units_issued": order.units_issued,
            "indication": order.indication,
            "patient_blood_type_at_order": (
                order.patient_blood_type_at_order
            ),
            "patient_blood_type_verified": (
                order.patient_blood_type_verified
            ),
            "product_blood_type": order.product_blood_type,
            "compatibility_status": order.compatibility_status,
            "crossmatch_reference": order.crossmatch_reference,
            "emergency_release_reason": (
                order.emergency_release_reason
            ),
            "version": order.version,
        },
    }
