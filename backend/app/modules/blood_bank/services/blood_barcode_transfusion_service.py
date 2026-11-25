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
    from app.modules.blood_bank.services import barcode_hardening as hardened
    return hardened.verify(clinic_id=clinic_id,actor_staff_id=actor_staff_id,patient_barcode=patient_barcode,unit_barcode=unit_barcode,scan_timestamp=scan_timestamp,scan_device=scan_device,operation_identity=operation_identity,blood_product_order_id=blood_product_order_id)


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
    from app.modules.blood_bank.services import barcode_hardening as hardened
    return hardened.start(clinic_id=clinic_id,actor_staff_id=actor_staff_id,blood_product_order_id=blood_product_order_id,blood_unit_id=blood_unit_id,barcode_verification_event_id=barcode_verification_event_id,pre_vitals=pre_vitals,notes=notes)


@transactional
def complete_blood_transfusion(
    transfusion_id: int,
    *,
    clinic_id: int,
    actor_staff_id: int,
    post_vitals: dict | None = None,
    notes: str | None = None,
) -> BloodTransfusion:
    from app.modules.blood_bank.services import barcode_hardening as hardened
    return hardened.finish(transfusion_id,clinic_id=clinic_id,actor_staff_id=actor_staff_id,post_vitals=post_vitals,notes=notes)


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
    from app.modules.blood_bank.services import barcode_hardening as hardened
    return hardened.reaction(transfusion_id,clinic_id=clinic_id,actor_staff_id=actor_staff_id,reaction_type=reaction_type,reaction_severity=reaction_severity,reaction_notes=reaction_notes,post_vitals=post_vitals)


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


@transactional
def stop_blood_transfusion(transfusion_id, *, clinic_id, actor_staff_id, post_vitals, notes):
    from app.modules.blood_bank.services import barcode_hardening as hardened
    return hardened.finish(transfusion_id,clinic_id=clinic_id,actor_staff_id=actor_staff_id,post_vitals=post_vitals,notes=notes,stop=True)
