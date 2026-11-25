from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.blood_bank_enums import (
    BloodBarcodeFailureCode,
    BloodUnitStatus,
    TransfusionReactionSeverity,
    TransfusionReactionType,
    TransfusionStatus,
    TtiScreeningStatus,
)
from app.core.enums.clinical_order_enums import ClinicalOrderLifecycleStatus
from app.core.enums.emar_enums import BarcodeVerificationResult
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.blood_bank.models.blood_bank_model import (
    BloodTransfusion,
    BloodTransfusionBarcodeEvent,
    BloodUnit,
)
from app.modules.blood_bank.services.blood_bank_service import (
    is_blood_unit_compatible,
)
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward


BARCODE_VERIFICATION_POLICY_VERSION = 1

_LOOKBACK_REACTION_TYPES = {
    TransfusionReactionType.ACUTE_HEMOLYTIC.value,
    TransfusionReactionType.DELAYED_HEMOLYTIC.value,
    TransfusionReactionType.TRALI.value,
    TransfusionReactionType.SEPSIS_CONTAMINATION.value,
}
_LOOKBACK_SEVERITIES = {
    TransfusionReactionSeverity.SEVERE.value,
    TransfusionReactionSeverity.LIFE_THREATENING.value,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _positive_id(value, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


def _required_text(value, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError(f"{field_name} is required")
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )
    return cleaned


def _optional_text(value, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed {max_length} characters"
        )
    return cleaned


def _normalize_barcode(value: str | None) -> str:
    if value is None or not isinstance(value, str):
        return ""
    return value.strip()


def _barcode_is_malformed(value: str) -> bool:
    return not value or len(value) > 128


def _normalize_scan_timestamp(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError("scan_timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(
            "scan_timestamp must include timezone information"
        )
    return value.astimezone(timezone.utc)


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
        raise ValidationError("Blood transfusion actor must be active")
    if staff.user is None or not staff.user.is_active:
        raise ValidationError("Blood transfusion actor user must be active")
    if staff.user.role not in allowed_roles:
        raise ValidationError(role_message)
    return staff


def _same_scan_request(
    event: BloodTransfusionBarcodeEvent,
    *,
    actor_staff_id: int,
    patient_barcode: str,
    unit_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    blood_product_order_id: int | None,
) -> bool:
    return (
        event.actor_staff_id == actor_staff_id
        and event.patient_barcode == patient_barcode
        and event.unit_barcode == unit_barcode
        and _as_utc(event.scan_timestamp) == scan_timestamp
        and event.scan_device == scan_device
        and (
            blood_product_order_id is None
            or event.blood_product_order_id == blood_product_order_id
        )
    )


def _existing_operation(
    *,
    clinic_id: int,
    operation_identity: str,
    actor_staff_id: int,
    patient_barcode: str,
    unit_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    blood_product_order_id: int | None,
) -> BloodTransfusionBarcodeEvent | None:
    existing = db.session.execute(
        select(BloodTransfusionBarcodeEvent)
        .where(
            BloodTransfusionBarcodeEvent.clinic_id == clinic_id,
            BloodTransfusionBarcodeEvent.operation_identity == operation_identity,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is None:
        return None
    if not _same_scan_request(
        existing,
        actor_staff_id=actor_staff_id,
        patient_barcode=patient_barcode,
        unit_barcode=unit_barcode,
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        blood_product_order_id=blood_product_order_id,
    ):
        raise ConflictError(
            "Blood barcode operation identity was already used for a different scan request",
            code="BARCODE_OPERATION_PAYLOAD_MISMATCH",
        )
    return existing


def _audit_barcode_event(
    event: BloodTransfusionBarcodeEvent,
    *,
    actor: Staff,
) -> None:
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="BloodTransfusionBarcodeEvent",
        entity_id=event.id,
        description=(
            f"Blood transfusion barcode verification ({event.verification_result.value})"
        ),
        new_value={
            "verification_result": event.verification_result.value,
            "failure_code": (
                event.failure_code.value if event.failure_code else None
            ),
            "blood_product_order_id": event.blood_product_order_id,
            "blood_unit_id": event.blood_unit_id,
            "patient_id": event.patient_id,
            "operation_identity": event.operation_identity,
        },
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )


def _create_barcode_event(
    *,
    clinic_id: int,
    actor: Staff,
    patient_barcode: str,
    unit_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    operation_identity: str,
    verification_result: BarcodeVerificationResult,
    failure_code: BloodBarcodeFailureCode | None,
    failure_reason: str | None,
    patient_match: bool,
    unit_match: bool,
    blood_type_match: bool,
    expiry_match: bool,
    order_status_match: bool,
    scanned_patient: Patient | None = None,
    scanned_unit: BloodUnit | None = None,
    order: BloodProductOrder | None = None,
    blood_product_order_id_override: int | None = None,
    expected_patient_blood_type: str | None = None,
) -> BloodTransfusionBarcodeEvent:
    event = BloodTransfusionBarcodeEvent(
        clinic_id=clinic_id,
        patient_id=order.patient_id if order is not None else None,
        admission_id=order.admission_id if order is not None else None,
        blood_product_order_id=(
            order.id if order is not None else blood_product_order_id_override
        ),
        blood_unit_id=scanned_unit.id if scanned_unit is not None else None,
        actor_staff_id=actor.id,
        scanned_patient_id=(
            scanned_patient.id if scanned_patient is not None else None
        ),
        scanned_unit_id=scanned_unit.id if scanned_unit is not None else None,
        patient_barcode=patient_barcode,
        unit_barcode=unit_barcode,
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        verification_result=verification_result,
        failure_code=failure_code,
        failure_reason=failure_reason,
        patient_match=patient_match,
        unit_match=unit_match,
        blood_type_match=blood_type_match,
        expiry_match=expiry_match,
        order_status_match=order_status_match,
        expected_patient_blood_type=expected_patient_blood_type,
        scanned_unit_blood_type=(
            scanned_unit.blood_type if scanned_unit is not None else None
        ),
        source_order_version=order.version if order is not None else None,
        verification_policy_version=BARCODE_VERIFICATION_POLICY_VERSION,
        operation_identity=operation_identity,
    )
    savepoint = db.session.begin_nested()
    db.session.add(event)
    try:
        db.session.flush()
    except IntegrityError:
        savepoint.rollback()
        existing = db.session.execute(
            select(BloodTransfusionBarcodeEvent)
            .where(
                BloodTransfusionBarcodeEvent.clinic_id == clinic_id,
                BloodTransfusionBarcodeEvent.operation_identity == operation_identity,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if existing is None:
            raise ConflictError(
                "Blood barcode operation could not be reconciled",
                code="BARCODE_OPERATION_OUTCOME_UNRESOLVED",
            )
        if not _same_scan_request(
            existing,
            actor_staff_id=actor.id,
            patient_barcode=patient_barcode,
            unit_barcode=unit_barcode,
            scan_timestamp=scan_timestamp,
            scan_device=scan_device,
            blood_product_order_id=(
                order.id if order is not None else blood_product_order_id_override
            ),
        ):
            raise ConflictError(
                "Blood barcode operation identity was already used for a different scan request",
                code="BARCODE_OPERATION_PAYLOAD_MISMATCH",
            )
        return existing

    savepoint.commit()
    _audit_barcode_event(event, actor=actor)
    return event


def _find_patient(
    *,
    clinic_id: int,
    patient_barcode: str,
) -> tuple[Patient | None, BloodBarcodeFailureCode | None]:
    patient = db.session.execute(
        select(Patient).where(
            Patient.clinic_id == clinic_id,
            Patient.barcode == patient_barcode,
        )
    ).scalar_one_or_none()
    if patient is not None:
        return patient, None

    other = db.session.execute(
        select(Patient.id)
        .where(
            Patient.barcode == patient_barcode,
            Patient.clinic_id != clinic_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    if other is not None:
        return None, BloodBarcodeFailureCode.CROSS_CLINIC_PATIENT_BARCODE
    return None, BloodBarcodeFailureCode.UNKNOWN_PATIENT_BARCODE


def _find_blood_unit(
    *,
    clinic_id: int,
    unit_barcode: str,
) -> tuple[BloodUnit | None, BloodBarcodeFailureCode | None]:
    unit = db.session.execute(
        select(BloodUnit).where(
            BloodUnit.clinic_id == clinic_id,
            BloodUnit.unit_barcode == unit_barcode,
        )
    ).scalar_one_or_none()
    if unit is not None:
        return unit, None

    other = db.session.execute(
        select(BloodUnit.id)
        .where(
            BloodUnit.unit_barcode == unit_barcode,
            BloodUnit.clinic_id != clinic_id,
        )
        .limit(1)
    ).scalar_one_or_none()
    if other is not None:
        return None, BloodBarcodeFailureCode.CROSS_CLINIC_UNIT_BARCODE
    return None, BloodBarcodeFailureCode.UNKNOWN_UNIT_BARCODE


@transactional
def verify_blood_transfusion_barcode(
    *,
    clinic_id: int,
    actor_staff_id: int,
    patient_barcode: str,
    unit_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    operation_identity: str,
    blood_product_order_id: int | None = None,
) -> BloodTransfusionBarcodeEvent:
    clinic_id = _positive_id(clinic_id, "clinic_id")
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.DOCTOR),
        role_message="Bedside blood barcode verification requires nurse or doctor role",
    )
    norm_patient = _normalize_barcode(patient_barcode)
    norm_unit = _normalize_barcode(unit_barcode)
    ts = _normalize_scan_timestamp(scan_timestamp)
    device = _required_text(scan_device, "scan_device", 255)
    op_id = _required_text(operation_identity, "operation_identity", 255)
    if blood_product_order_id is not None:
        _positive_id(blood_product_order_id, "blood_product_order_id")

    existing = _existing_operation(
        clinic_id=clinic_id,
        operation_identity=op_id,
        actor_staff_id=actor.id,
        patient_barcode=norm_patient,
        unit_barcode=norm_unit,
        scan_timestamp=ts,
        scan_device=device,
        blood_product_order_id=blood_product_order_id,
    )
    if existing is not None:
        return existing

    def fail(
        code: BloodBarcodeFailureCode,
        reason: str,
        *,
        patient_match: bool = False,
        unit_match: bool = False,
        blood_type_match: bool = False,
        expiry_match: bool = False,
        order_status_match: bool = False,
        scanned_patient: Patient | None = None,
        scanned_unit: BloodUnit | None = None,
        order: BloodProductOrder | None = None,
        expected_bt: str | None = None,
    ) -> BloodTransfusionBarcodeEvent:
        return _create_barcode_event(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=norm_patient or "<empty>",
            unit_barcode=norm_unit or "<empty>",
            scan_timestamp=ts,
            scan_device=device,
            operation_identity=op_id,
            verification_result=BarcodeVerificationResult.NOT_BARCODE_VERIFIED,
            failure_code=code,
            failure_reason=reason,
            patient_match=patient_match,
            unit_match=unit_match,
            blood_type_match=blood_type_match,
            expiry_match=expiry_match,
            order_status_match=order_status_match,
            scanned_patient=scanned_patient,
            scanned_unit=scanned_unit,
            order=order,
            blood_product_order_id_override=blood_product_order_id,
            expected_patient_blood_type=expected_bt,
        )

    if _barcode_is_malformed(norm_patient):
        return fail(
            BloodBarcodeFailureCode.MALFORMED_PATIENT_BARCODE,
            "Patient barcode is empty or exceeds 128 characters",
        )
    if _barcode_is_malformed(norm_unit):
        return fail(
            BloodBarcodeFailureCode.MALFORMED_UNIT_BARCODE,
            "Blood unit barcode is empty or exceeds 128 characters",
        )

    patient, patient_err = _find_patient(
        clinic_id=clinic_id,
        patient_barcode=norm_patient,
    )
    if patient_err is not None:
        return fail(
            patient_err,
            "Patient barcode could not be resolved in clinic",
        )

    unit, unit_err = _find_blood_unit(
        clinic_id=clinic_id,
        unit_barcode=norm_unit,
    )
    if unit_err is not None:
        return fail(
            unit_err,
            "Blood unit barcode could not be resolved in clinic",
            scanned_patient=patient,
        )

    resolved_order_id = blood_product_order_id or unit.issued_to_order_id
    if resolved_order_id is None:
        return fail(
            BloodBarcodeFailureCode.UNIT_NOT_ISSUED_TO_ORDER,
            "Scanned blood unit has not been issued to any order",
            scanned_patient=patient,
            scanned_unit=unit,
        )

    order = db.session.execute(
        select(BloodProductOrder).where(
            BloodProductOrder.id == resolved_order_id,
            BloodProductOrder.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if order is None:
        return fail(
            BloodBarcodeFailureCode.ORDER_NOT_VERIFIED,
            f"Blood product order {resolved_order_id} not found in clinic",
            scanned_patient=patient,
            scanned_unit=unit,
        )

    expected_bt = (
        order.patient_blood_type_verified or order.patient_blood_type_at_order
    )

    if order.patient_id != patient.id:
        return fail(
            BloodBarcodeFailureCode.WRONG_PATIENT,
            "Scanned patient wristband does not match blood product order patient",
            patient_match=False,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    admission = db.session.execute(
        select(Admission)
        .join(Bed, Admission.bed_id == Bed.id)
        .join(Ward, Bed.ward_id == Ward.id)
        .where(
            Admission.id == order.admission_id,
            Ward.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if admission is None or admission.status != AdmissionStatus.ADMITTED:
        return fail(
            BloodBarcodeFailureCode.INACTIVE_ADMISSION,
            "Blood product order admission is not active",
            patient_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    if order.status == ClinicalOrderLifecycleStatus.CANCELLED.value:
        return fail(
            BloodBarcodeFailureCode.INACTIVE_ORDER,
            "Blood product order has been cancelled",
            patient_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    if order.compatibility_status not in {"compatible", "emergency_release"}:
        return fail(
            BloodBarcodeFailureCode.ORDER_NOT_VERIFIED,
            "Blood product order compatibility has not been verified by lab",
            patient_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    if (
        unit.status != BloodUnitStatus.ISSUED.value
        or unit.issued_to_order_id != order.id
    ):
        return fail(
            BloodBarcodeFailureCode.UNIT_NOT_ISSUED_TO_ORDER,
            f"Blood unit {unit.unit_barcode} is not issued to order {order.id}",
            patient_match=True,
            order_status_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    if unit.tti_screening_status != TtiScreeningStatus.CLEARED.value:
        return fail(
            BloodBarcodeFailureCode.UNIT_NOT_CLEARED,
            "Blood unit has not passed TTI screening",
            patient_match=True,
            unit_match=True,
            order_status_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    if _as_utc(unit.expires_at) <= ts:
        return fail(
            BloodBarcodeFailureCode.UNIT_EXPIRED,
            f"Blood unit {unit.unit_barcode} is expired",
            patient_match=True,
            unit_match=True,
            order_status_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    emergency = order.compatibility_status == "emergency_release"
    if not is_blood_unit_compatible(
        unit_blood_type=unit.blood_type,
        recipient_blood_type=expected_bt,
        product_type=order.product_type,
        emergency_release=emergency,
    ):
        return fail(
            BloodBarcodeFailureCode.INCOMPATIBLE_BLOOD_TYPE,
            f"Unit blood type {unit.blood_type} is incompatible with patient ({expected_bt})",
            patient_match=True,
            unit_match=True,
            expiry_match=True,
            order_status_match=True,
            scanned_patient=patient,
            scanned_unit=unit,
            order=order,
            expected_bt=expected_bt,
        )

    return _create_barcode_event(
        clinic_id=clinic_id,
        actor=actor,
        patient_barcode=norm_patient,
        unit_barcode=norm_unit,
        scan_timestamp=ts,
        scan_device=device,
        operation_identity=op_id,
        verification_result=BarcodeVerificationResult.BARCODE_VERIFIED,
        failure_code=None,
        failure_reason=None,
        patient_match=True,
        unit_match=True,
        blood_type_match=True,
        expiry_match=True,
        order_status_match=True,
        scanned_patient=patient,
        scanned_unit=unit,
        order=order,
        expected_patient_blood_type=expected_bt,
    )


def list_blood_barcode_events(
    *,
    clinic_id: int,
    blood_product_order_id: int | None = None,
) -> list[BloodTransfusionBarcodeEvent]:
    stmt = select(BloodTransfusionBarcodeEvent).where(
        BloodTransfusionBarcodeEvent.clinic_id
        == _positive_id(clinic_id, "clinic_id")
    )
    if blood_product_order_id is not None:
        stmt = stmt.where(
            BloodTransfusionBarcodeEvent.blood_product_order_id
            == _positive_id(blood_product_order_id, "blood_product_order_id")
        )
    return list(
        db.session.execute(
            stmt.order_by(
                BloodTransfusionBarcodeEvent.scan_timestamp.desc(),
                BloodTransfusionBarcodeEvent.id.desc(),
            )
        ).scalars().all()
    )


# ---------------------------------------------------------------------------
# Transfusion Administration & Hemovigilance Lookback
# ---------------------------------------------------------------------------


@transactional
def start_blood_transfusion(
    *,
    clinic_id: int,
    actor_staff_id: int,
    blood_product_order_id: int,
    blood_unit_id: int,
    barcode_verification_event_id: int,
    pre_vitals: dict | None = None,
    notes: str | None = None,
) -> BloodTransfusion:
    clinic_id = _positive_id(clinic_id, "clinic_id")
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.DOCTOR),
        role_message="Starting blood transfusion requires nurse or doctor role",
    )
    order = db.session.execute(
        select(BloodProductOrder)
        .where(
            BloodProductOrder.id
            == _positive_id(blood_product_order_id, "blood_product_order_id"),
            BloodProductOrder.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError(
            f"Blood product order {blood_product_order_id} not found"
        )

    unit = db.session.execute(
        select(BloodUnit)
        .where(
            BloodUnit.id == _positive_id(blood_unit_id, "blood_unit_id"),
            BloodUnit.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError(f"Blood unit {blood_unit_id} not found")

    if (
        unit.status != BloodUnitStatus.ISSUED.value
        or unit.issued_to_order_id != order.id
    ):
        raise ConflictError(
            f"Blood unit {unit.unit_barcode} is not issued to order {order.id}"
        )

    now = _utcnow()
    if _as_utc_naive(unit.expires_at) <= now:
        unit.status = BloodUnitStatus.EXPIRED.value
        unit.version += 1
        raise ConflictError(f"Blood unit {unit.unit_barcode} has expired")

    event = db.session.execute(
        select(BloodTransfusionBarcodeEvent).where(
            BloodTransfusionBarcodeEvent.id
            == _positive_id(
                barcode_verification_event_id,
                "barcode_verification_event_id",
            ),
            BloodTransfusionBarcodeEvent.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if event is None:
        raise NotFoundError(
            f"Barcode verification event {barcode_verification_event_id} not found"
        )

    if (
        event.verification_result != BarcodeVerificationResult.BARCODE_VERIFIED
        or event.blood_product_order_id != order.id
        or event.blood_unit_id != unit.id
        or event.patient_id != order.patient_id
    ):
        raise ConflictError(
            "Valid barcode_verified event matching order, patient, and unit is required",
            code=BloodBarcodeFailureCode.STALE_VERIFICATION.value,
        )

    if event.source_order_version != order.version:
        raise ConflictError(
            "Barcode verification event is stale for the current blood order version",
            code=BloodBarcodeFailureCode.STALE_VERIFICATION.value,
        )

    existing = db.session.execute(
        select(BloodTransfusion.id).where(
            BloodTransfusion.clinic_id == clinic_id,
            BloodTransfusion.blood_unit_id == unit.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"Transfusion already recorded for blood unit {unit.unit_barcode}"
        )

    transfusion = BloodTransfusion(
        clinic_id=clinic_id,
        patient_id=order.patient_id,
        admission_id=order.admission_id,
        blood_product_order_id=order.id,
        blood_unit_id=unit.id,
        barcode_verification_event_id=event.id,
        administered_by_staff_id=actor.id,
        started_at=now,
        status=TransfusionStatus.IN_PROGRESS.value,
        pre_vitals=dict(pre_vitals) if pre_vitals is not None else None,
        reaction_reported=False,
        reaction_type=TransfusionReactionType.NONE.value,
        reaction_severity=TransfusionReactionSeverity.NONE.value,
        lookback_triggered=False,
        notes=_optional_text(notes, "notes", 5000),
        version=1,
    )
    unit.status = BloodUnitStatus.TRANSFUSED.value
    unit.version += 1
    db.session.add(transfusion)
    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="BloodTransfusion",
        entity_id=transfusion.id,
        description="Started barcode-verified blood transfusion",
        new_value={
            "blood_product_order_id": order.id,
            "blood_unit_id": unit.id,
            "barcode_verification_event_id": event.id,
            "status": transfusion.status,
        },
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )
    return transfusion


@transactional
def complete_blood_transfusion(
    transfusion_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    post_vitals: dict | None = None,
    notes: str | None = None,
) -> BloodTransfusion:
    clinic_id = _positive_id(clinic_id, "clinic_id")
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.DOCTOR),
        role_message="Completing blood transfusion requires nurse or doctor role",
    )
    transfusion = db.session.execute(
        select(BloodTransfusion)
        .where(
            BloodTransfusion.id
            == _positive_id(transfusion_id, "transfusion_id"),
            BloodTransfusion.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if transfusion is None:
        raise NotFoundError(f"Blood transfusion {transfusion_id} not found")

    if transfusion.status != TransfusionStatus.IN_PROGRESS.value:
        raise ConflictError(
            "Only an in-progress blood transfusion can be completed"
        )

    transfusion.status = TransfusionStatus.COMPLETED.value
    transfusion.ended_at = _utcnow()
    if post_vitals is not None:
        transfusion.post_vitals = dict(post_vitals)
    if notes is not None:
        transfusion.notes = _optional_text(notes, "notes", 5000)
    transfusion.version += 1
    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="BloodTransfusion",
        entity_id=transfusion.id,
        description="Completed blood transfusion",
        old_value={"status": TransfusionStatus.IN_PROGRESS.value},
        new_value={
            "status": transfusion.status,
            "version": transfusion.version,
        },
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )
    return transfusion


@transactional
def record_transfusion_reaction(
    transfusion_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    reaction_type: TransfusionReactionType | str,
    reaction_severity: TransfusionReactionSeverity | str,
    reaction_notes: str,
    post_vitals: dict | None = None,
) -> tuple[BloodTransfusion, list[BloodUnit]]:
    clinic_id = _positive_id(clinic_id, "clinic_id")
    actor = _get_staff(
        actor_staff_id,
        clinic_id,
        allowed_roles=(Role.NURSE, Role.DOCTOR),
        role_message="Recording transfusion reaction requires nurse or doctor role",
    )
    transfusion = db.session.execute(
        select(BloodTransfusion)
        .where(
            BloodTransfusion.id
            == _positive_id(transfusion_id, "transfusion_id"),
            BloodTransfusion.clinic_id == clinic_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if transfusion is None:
        raise NotFoundError(f"Blood transfusion {transfusion_id} not found")

    r_type = TransfusionReactionType(reaction_type).value
    r_severity = TransfusionReactionSeverity(reaction_severity).value
    if (
        r_type == TransfusionReactionType.NONE.value
        or r_severity == TransfusionReactionSeverity.NONE.value
    ):
        raise ValidationError(
            "reaction_type and reaction_severity cannot be 'none' when reporting a reaction"
        )

    now = _utcnow()
    old_status = transfusion.status
    if transfusion.status == TransfusionStatus.IN_PROGRESS.value:
        transfusion.status = TransfusionStatus.STOPPED_REACTION.value
        transfusion.ended_at = now
    transfusion.reaction_reported = True
    transfusion.reaction_type = r_type
    transfusion.reaction_severity = r_severity
    transfusion.reaction_notes = _required_text(
        reaction_notes,
        "reaction_notes",
        5000,
    )
    transfusion.reaction_reported_at = now
    transfusion.reaction_reported_by_staff_id = actor.id
    if post_vitals is not None:
        transfusion.post_vitals = dict(post_vitals)

    quarantined_siblings: list[BloodUnit] = []
    if r_type in _LOOKBACK_REACTION_TYPES or r_severity in _LOOKBACK_SEVERITIES:
        transfusion.lookback_triggered = True
        unit = db.session.execute(
            select(BloodUnit).where(
                BloodUnit.id == transfusion.blood_unit_id,
                BloodUnit.clinic_id == clinic_id,
            )
        ).scalar_one()
        if unit.donation_id is not None:
            siblings = list(
                db.session.execute(
                    select(BloodUnit)
                    .where(
                        BloodUnit.clinic_id == clinic_id,
                        BloodUnit.donation_id == unit.donation_id,
                        BloodUnit.id != unit.id,
                        BloodUnit.status.in_([
                            BloodUnitStatus.AVAILABLE.value,
                            BloodUnitStatus.RESERVED.value,
                        ]),
                    )
                    .with_for_update()
                ).scalars().all()
            )
            for sibling in siblings:
                sibling.status = BloodUnitStatus.QUARANTINED.value
                sibling.reserved_for_order_id = None
                sibling.reserved_by_staff_id = None
                sibling.reserved_at = None
                sibling.notes = (
                    f"Hemovigilance lookback quarantine from transfusion #{transfusion.id} "
                    f"({r_type}/{r_severity})"
                )
                sibling.version += 1
                quarantined_siblings.append(sibling)

    transfusion.version += 1
    db.session.flush()

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="BloodTransfusion",
        entity_id=transfusion.id,
        description="Recorded transfusion reaction and evaluated hemovigilance lookback",
        old_value={"status": old_status},
        new_value={
            "status": transfusion.status,
            "reaction_type": transfusion.reaction_type,
            "reaction_severity": transfusion.reaction_severity,
            "lookback_triggered": transfusion.lookback_triggered,
            "quarantined_sibling_unit_ids": [u.id for u in quarantined_siblings],
            "version": transfusion.version,
        },
        user_id=actor.user_id,
        clinic_id=actor.clinic_id,
    )
    return transfusion, quarantined_siblings


def list_blood_transfusions(
    *,
    clinic_id: int,
    patient_id: int | None = None,
    blood_product_order_id: int | None = None,
) -> list[BloodTransfusion]:
    stmt = select(BloodTransfusion).where(
        BloodTransfusion.clinic_id == _positive_id(clinic_id, "clinic_id")
    )
    if patient_id is not None:
        stmt = stmt.where(
            BloodTransfusion.patient_id
            == _positive_id(patient_id, "patient_id")
        )
    if blood_product_order_id is not None:
        stmt = stmt.where(
            BloodTransfusion.blood_product_order_id
            == _positive_id(blood_product_order_id, "blood_product_order_id")
        )
    return list(
        db.session.execute(
            stmt.order_by(
                BloodTransfusion.started_at.desc(),
                BloodTransfusion.id.desc(),
            )
        ).scalars().all()
    )
