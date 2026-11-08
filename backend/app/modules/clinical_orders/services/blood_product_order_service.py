from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderPriority,
    ClinicalOrderType,
)
from app.core.enums.patient_enums import BloodType
from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag, SampleType
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
from app.modules.clinical_orders.models.blood_product_issued_unit_model import (
    BloodProductIssuedUnit,
)
from app.modules.clinical_orders.models.blood_product_lab_followup_model import (
    BloodProductLabFollowup,
)
from app.modules.lab.models.lab_model import LabOrder, LabOrderItem, LabTest


_PRODUCT_TYPES = {
    "packed_red_blood_cells",
    "whole_blood",
    "platelets",
    "fresh_frozen_plasma",
    "cryoprecipitate",
}

# Phase E S01 supports explicitly requested emergency RED CELL issue only.
# Other component types require separate blood-bank policy review.
_EMERGENCY_RED_CELL_TYPES = {"packed_red_blood_cells", "whole_blood"}

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
    emergency_release_requested: bool = False,
    emergency_request_reason: str | None = None,
    emergency_risk_acknowledged: bool = False,
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

    if type(emergency_release_requested) is not bool or type(emergency_risk_acknowledged) is not bool:
        raise ValidationError("Emergency request flags must be booleans")
    emergency_intent_reason = None
    if emergency_release_requested:
        if current_app.config.get("EMERGENCY_BLOOD_RELEASE_ENABLED") is not True:
            raise ConflictError("Emergency blood release remains disabled")
        if not emergency_risk_acknowledged:
            raise ValidationError("Explicit physician emergency risk acknowledgment is required")
        if _normalize_priority(priority) != ClinicalOrderPriority.STAT.value:
            raise ValidationError("Emergency blood request must have stat priority")
        if _normalize_product_type(product_type) not in _EMERGENCY_RED_CELL_TYPES:
            raise ValidationError("Emergency request currently supports red-cell orders only")
        emergency_intent_reason = _required_text(
            emergency_request_reason, "emergency_request_reason", 10000
        )
    elif emergency_request_reason is not None or emergency_risk_acknowledged:
        raise ValidationError("Emergency request evidence is not allowed on standard orders")

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
        emergency_release_requested=emergency_release_requested,
        emergency_request_reason=emergency_intent_reason,
        emergency_requested_by_staff_id=(
            actor.id if emergency_release_requested else None
        ),
        emergency_requested_at=(_utcnow() if emergency_release_requested else None),
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
            "emergency_release_requested": order.emergency_release_requested,
            "emergency_requested_by_staff_id": order.emergency_requested_by_staff_id,
            "emergency_request_reason": order.emergency_request_reason,
            "emergency_risk_acknowledged": emergency_risk_acknowledged,
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
        if current_app.config.get("EMERGENCY_BLOOD_RELEASE_ENABLED") is not True:
            raise ConflictError("Emergency blood release remains disabled")
        if not order.emergency_release_requested or order.emergency_requested_by_staff_id is None:
            raise ConflictError("A signed physician emergency request is required")
        # Revalidate the originating physician and the current clinical context.
        _get_doctor(order.emergency_requested_by_staff_id, clinic_id)
        _get_admission(order.admission_id, clinic_id, require_active=True)
        if order.priority != ClinicalOrderPriority.STAT.value or order.product_type not in _EMERGENCY_RED_CELL_TYPES:
            raise ConflictError("Emergency red-cell request evidence is invalid")
        if normalized_product_type is None or normalized_product_type == BloodType.UNKNOWN.value:
            raise ValidationError("Product blood type must be recorded for emergency red-cell issue")
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
        if emergency_release_reason is not None:
            raise ValidationError("Emergency reason is not permitted in standard verification")
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
        description=(
            "Recorded emergency red-cell release without completed crossmatch"
            if emergency_release else "Verified blood product compatibility"
        ),
        new_value={
            "compatibility_status": order.compatibility_status,
            "patient_blood_type_verified": (
                order.patient_blood_type_verified
            ),
            "product_blood_type": order.product_blood_type,
            "crossmatch_reference": order.crossmatch_reference,
            "emergency_requested_by_staff_id": order.emergency_requested_by_staff_id,
            "emergency_request_reason": (
                order.emergency_request_reason if emergency_release else None
            ),
            "emergency_release_reason": (
                order.emergency_release_reason if emergency_release else None
            ),
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
    issued_units: list[dict] | None = None,
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

    if order.compatibility_status == "emergency_release":
        if current_app.config.get("EMERGENCY_BLOOD_RELEASE_ENABLED") is not True:
            raise ConflictError("Emergency blood release remains disabled")
        if (not order.emergency_release_requested
                or order.emergency_requested_by_staff_id is None
                or order.emergency_requested_at is None):
            raise ConflictError("A signed physician emergency request is required")
        _get_doctor(order.emergency_requested_by_staff_id, clinic_id)
        _get_admission(order.admission_id, clinic_id, require_active=True)
        if not order.product_blood_type or order.product_blood_type == BloodType.UNKNOWN.value:
            raise ConflictError("Emergency release product blood type is missing")

    units_issued = _positive_units(
        units_issued,
        "units_issued",
    )
    if units_issued > order.units_requested:
        raise ValidationError(
            "units_issued cannot exceed units_requested"
        )

    if order.compatibility_status == "emergency_release":
        if not isinstance(issued_units, list) or len(issued_units) != units_issued:
            raise ValidationError("Every emergency issued unit requires an identifier and blood group")
        normalized = []
        for unit in issued_units:
            if not isinstance(unit, dict) or set(unit) != {"unit_identifier", "product_blood_type"}:
                raise ValidationError("Each issued unit needs an identifier and product blood type")
            identifier = _required_text(unit["unit_identifier"], "unit_identifier", 128).upper()
            if _blood_type_value(unit["product_blood_type"]) != order.product_blood_type:
                raise ValidationError("Issued unit blood type must match the recorded verification")
            normalized.append(identifier)
        if len(set(normalized)) != len(normalized):
            raise ValidationError("Emergency unit identifiers must be distinct")
        issued_at = _utcnow()
        db.session.add_all([
            BloodProductIssuedUnit(
                clinic_id=order.clinic_id,
                patient_id=order.patient_id,
                admission_id=order.admission_id,
                order_id=order.id,
                unit_identifier=identifier,
                product_blood_type=order.product_blood_type,
                issued_by_staff_id=actor.id,
                issued_at=issued_at,
            )
            for identifier in normalized
        ])
    elif issued_units is not None:
        raise ValidationError("Identified units require emergency verification")

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

    try:
        db.session.flush()
    except IntegrityError as exc:
        # Database unique index enforces per-clinic one-time unit issuance
        # across concurrent orders; transactional decorator rolls back.
        raise ConflictError("Blood unit identifier has already been issued") from exc

    if order.compatibility_status == "emergency_release":
        db.session.add_all([
            BloodProductLabFollowup(
                clinic_id=unit.clinic_id,
                patient_id=unit.patient_id,
                admission_id=unit.admission_id,
                order_id=unit.order_id,
                issued_unit_id=unit.id,
                status="pending",
                created_at=unit.issued_at,
            )
            for unit in order.issued_units
        ])
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
            "unit_identifiers": (
                normalized if order.compatibility_status == "emergency_release" else []
            ),
            "patient_id": order.patient_id,
            "admission_id": order.admission_id,
            "version": order.version,
        },
    )
    return order


@transactional
def record_emergency_blood_lab_followup(
    blood_product_order_id: int,
    *,
    issued_unit_id: int,
    clinic_id: int,
    actor_staff_id: int,
    lab_order_item_id: int,
    outcome: str,
    evidence_note: str,
    escalation_note: str | None = None,
) -> BloodProductOrder:
    """Record verified lab evidence, NEVER compatibility clearance.

    This retrospective path remains permitted after emergency release is
    disabled, so turning off new emergency issue does not hide obligations.
    """
    actor = _get_lab_technician(actor_staff_id, clinic_id)
    order = _get_order(blood_product_order_id, clinic_id, lock=True)
    if (order.compatibility_status != "emergency_release"
            or order.status != ClinicalOrderLifecycleStatus.COMPLETED.value):
        raise ConflictError("Laboratory follow-up requires a completed emergency release")

    unit = db.session.execute(
        select(BloodProductIssuedUnit).where(
            BloodProductIssuedUnit.id == _positive_id(issued_unit_id, "issued_unit_id"),
            BloodProductIssuedUnit.order_id == order.id,
            BloodProductIssuedUnit.clinic_id == clinic_id,
            BloodProductIssuedUnit.patient_id == order.patient_id,
            BloodProductIssuedUnit.admission_id == order.admission_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError("Issued blood unit not found for this order and clinic")

    record = db.session.execute(
        select(BloodProductLabFollowup).where(
            BloodProductLabFollowup.issued_unit_id == unit.id,
            BloodProductLabFollowup.order_id == order.id,
            BloodProductLabFollowup.clinic_id == clinic_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if record is None:
        raise ConflictError("Issued unit has no pending laboratory follow-up")
    if record.status != "pending":
        raise ConflictError("Laboratory follow-up has already been recorded")

    if outcome not in ("evidence_recorded", "discrepancy_escalated"):
        raise ValidationError("Invalid laboratory follow-up outcome")
    note = _required_text(evidence_note, "evidence_note", 10000)
    if outcome == "discrepancy_escalated":
        escalation = _required_text(escalation_note, "escalation_note", 10000)
    elif escalation_note is not None:
        raise ValidationError("Escalation note is permitted only for discrepancy")
    else:
        escalation = None

    configured = current_app.config.get("EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES", ())
    if not isinstance(configured, (tuple, list, set)) or not configured:
        raise ConflictError("Approved laboratory follow-up test codes are not configured")
    approved_codes = {
        x.strip() for x in configured if isinstance(x, str) and x.strip()
    }
    if not approved_codes:
        raise ConflictError("Approved laboratory follow-up test codes are not configured")

    item = db.session.execute(
        select(LabOrderItem)
        .join(LabOrder, LabOrderItem.order_id == LabOrder.id)
        .join(LabTest, LabOrderItem.test_id == LabTest.id)
        .where(
            LabOrderItem.id == _positive_id(lab_order_item_id, "lab_order_item_id"),
            LabOrder.clinic_id == clinic_id,
            LabOrder.patient_id == order.patient_id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise NotFoundError("Laboratory evidence not found for this patient and clinic")

    lab = item.order
    if (lab.status != LabOrderStatus.COMPLETED
            or lab.verified_by_id is None or lab.verified_at is None
            or lab.completed_at is None
            or lab.sample_collected_at is None
            or lab.processed_at is None
            or item.resulted_at is None
            or not isinstance(item.result_value, str)
            or not item.result_value.strip()):
        raise ConflictError("Laboratory evidence must be fully verified and completed")
    if item.test.code not in approved_codes or item.test.sample_type != SampleType.BLOOD:
        raise ConflictError("Laboratory test is not an approved blood follow-up test")
    if item.resulted_at < unit.issued_at:
        raise ConflictError("Laboratory follow-up result predates emergency unit issue")
    if item.flag in (LabResultFlag.ABNORMAL, LabResultFlag.CRITICAL) and outcome != "discrepancy_escalated":
        raise ConflictError("Abnormal or critical laboratory evidence requires escalation")

    already_linked = db.session.execute(
        select(BloodProductLabFollowup.id).where(
            BloodProductLabFollowup.lab_order_item_id == item.id,
            BloodProductLabFollowup.issued_unit_id != unit.id,
        )
    ).scalar_one_or_none()
    if already_linked is not None:
        raise ConflictError("Laboratory evidence already linked to another issued unit")

    record.status = outcome
    record.lab_order_item_id = item.id
    record.recorded_by_staff_id = actor.id
    record.recorded_at = _utcnow()
    record.evidence_note = note
    record.escalation_note = escalation
    order.version += 1
    try:
        db.session.flush()
    except IntegrityError as exc:
        # PostgreSQL uniqueness is authoritative if two orders race to
        # attach the same verified lab item. Transactional rolls back.
        raise ConflictError(
            "Laboratory evidence already linked to another issued unit"
        ) from exc
    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        order=order,
        description="Post-release laboratory evidence linked to issued blood unit",
        new_value={
            "issued_unit_id": unit.id,
            "unit_identifier": unit.unit_identifier,
            "lab_order_item_id": item.id,
            "lab_order_id": lab.id,
            "lab_verified_by_id": lab.verified_by_id,
            "outcome": outcome,
            "escalation_note": escalation,
            "compatibility_clearance_certified": False,
            "version": order.version,
        },
    )
    return order


@transactional
def review_emergency_blood_release(
    blood_product_order_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    outcome: str,
    note: str,
) -> BloodProductOrder:
    actor = _get_doctor(actor_staff_id, clinic_id)
    order = _get_order(blood_product_order_id, clinic_id, lock=True)

    if order.compatibility_status != "emergency_release":
        raise ConflictError("Post-release review applies to emergency release only")
    if order.status != ClinicalOrderLifecycleStatus.COMPLETED.value:
        raise ConflictError("Emergency unit issue must be completed before review")
    if order.emergency_review_outcome is not None:
        raise ConflictError("Emergency blood issue has already been reviewed")
    if actor.id == order.emergency_requested_by_staff_id:
        raise ConflictError("Independent physician review is required")
    if outcome not in ("reviewed", "escalated"):
        raise ValidationError("Invalid emergency review outcome")

    note = _required_text(note, "note", 10000)
    if len(order.issued_units) != order.units_issued:
        raise ConflictError("Emergency blood unit trace is incomplete")
    order.emergency_review_outcome = outcome
    order.emergency_review_note = note
    order.emergency_reviewed_by_staff_id = actor.id
    order.emergency_reviewed_at = _utcnow()
    order.version += 1
    db.session.flush()

    _audit(
        actor=actor,
        action=AuditAction.UPDATE,
        order=order,
        description="Independent post-release emergency blood issue review",
        new_value={
            "outcome": outcome,
            "reviewed_by_staff_id": actor.id,
            "emergency_requested_by_staff_id": order.emergency_requested_by_staff_id,
            "issued_unit_identifiers": [u.unit_identifier for u in order.issued_units],
            "followup_testing_is_not_certified": True,
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
