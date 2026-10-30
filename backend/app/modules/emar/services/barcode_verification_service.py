from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import (
    MedicationOrderType,
    PrescriptionStatus,
)
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
import app.modules.emar.services.emar_service as emar_service
from app.modules.patient.models.patient_model import Patient
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission


BARCODE_VERIFICATION_POLICY_VERSION = 1
BARCODE_EARLY_WINDOW_MINUTES = 60
BARCODE_LATE_WINDOW_MINUTES = 60

_PENDING_STATUSES = (
    MedicationAdministrationStatus.SCHEDULED,
    MedicationAdministrationStatus.DUE,
)


def _normalize_required_text(
    value: str,
    *,
    field_name: str,
    max_length: int,
) -> str:
    if not isinstance(value, str):
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


def _normalize_barcode(
    value: str | None,
) -> str:
    if value is None:
        return ""

    if not isinstance(value, str):
        return ""

    return value.strip()


def _barcode_is_malformed(
    value: str,
) -> bool:
    return (
        not value
        or len(value) > 128
    )


def _normalize_scan_timestamp(
    value: datetime,
) -> datetime:
    if not isinstance(
        value,
        datetime,
    ):
        raise ValidationError(
            "scan_timestamp must be a datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            "scan_timestamp must include "
            "timezone information"
        )

    return value.astimezone(
        timezone.utc
    )


def _as_utc(
    value: datetime | None,
) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _db_time(
    value: datetime | None,
) -> datetime | None:
    value = _as_utc(
        value
    )

    if value is None:
        return None

    return value.replace(
        tzinfo=None
    )


def _window_for(
    scheduled_at: datetime,
) -> tuple[datetime, datetime]:
    scheduled_at = _as_utc(
        scheduled_at
    )

    if scheduled_at is None:
        raise ValidationError(
            "Scheduled barcode verification "
            "requires scheduled_at"
        )

    return (
        scheduled_at
        - timedelta(
            minutes=BARCODE_EARLY_WINDOW_MINUTES
        ),
        scheduled_at
        + timedelta(
            minutes=BARCODE_LATE_WINDOW_MINUTES
        ),
    )


def _same_scan_request(
    event: MedicationAdministrationEvent,
    *,
    actor_staff_id: int,
    patient_barcode: str,
    medication_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
) -> bool:
    return (
        event.actor_staff_id
        == actor_staff_id
        and event.patient_barcode
        == patient_barcode
        and event.medication_barcode
        == medication_barcode
        and _as_utc(
            event.scan_timestamp
        )
        == scan_timestamp
        and event.scan_device
        == scan_device
    )


def _existing_operation(
    *,
    clinic_id: int,
    operation_identity: str,
    actor_staff_id: int,
    patient_barcode: str,
    medication_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
) -> MedicationAdministrationEvent | None:
    existing = db.session.execute(
        select(
            MedicationAdministrationEvent
        )
        .where(
            MedicationAdministrationEvent.clinic_id
            == clinic_id,
            MedicationAdministrationEvent.operation_identity
            == operation_identity,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if existing is None:
        return None

    if not _same_scan_request(
        existing,
        actor_staff_id=actor_staff_id,
        patient_barcode=patient_barcode,
        medication_barcode=medication_barcode,
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
    ):
        raise ConflictError(
            "Barcode operation identity was already "
            "used for a different scan request",
            code=(
                "BARCODE_OPERATION_PAYLOAD_MISMATCH"
            ),
        )

    return existing


def _event_context(
    *,
    record: MedicationAdministration | None,
    item: PrescriptionItem | None,
) -> dict:
    if record is None:
        return {
            "patient_id": None,
            "admission_id": None,
            "medication_administration_id": None,
            "prescription_id": None,
            "prescription_item_id": None,
            "expected_drug_id": None,
            "expected_dose_value": None,
            "expected_dose_unit": None,
            "expected_route_code": None,
            "scheduled_at": None,
            "verification_window_start": None,
            "verification_window_end": None,
            "source_order_version": None,
        }

    window_start = None
    window_end = None

    if record.scheduled_at is not None:
        (
            window_start,
            window_end,
        ) = _window_for(
            record.scheduled_at
        )

    return {
        "patient_id": record.patient_id,
        "admission_id": record.admission_id,
        "medication_administration_id": (
            record.id
        ),
        "prescription_id": record.prescription_id,
        "prescription_item_id": (
            record.prescription_item_id
        ),
        "expected_drug_id": (
            item.drug_id
            if item is not None
            else None
        ),
        "expected_dose_value": (
            item.dose_value
            if item is not None
            else None
        ),
        "expected_dose_unit": (
            item.dose_unit
            if item is not None
            else None
        ),
        "expected_route_code": (
            item.route_code
            if item is not None
            else None
        ),
        "scheduled_at": record.scheduled_at,
        "verification_window_start": (
            window_start
        ),
        "verification_window_end": (
            window_end
        ),
        "source_order_version": (
            record.source_order_version
        ),
    }


def _audit_event(
    event: MedicationAdministrationEvent,
    *,
    actor: Staff,
) -> None:
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type=(
            "MedicationAdministrationEvent"
        ),
        entity_id=event.id,
        clinic_id=event.clinic_id,
        description=(
            "Barcode medication verification "
            f"{event.id}: "
            f"{event.verification_result.value}"
        ),
        old_value=None,
        new_value={
            "verification_result": (
                event.verification_result.value
            ),
            "failure_code": (
                event.failure_code.value
                if event.failure_code is not None
                else None
            ),
            "failure_reason": (
                event.failure_reason
            ),
            "actor_staff_id": (
                event.actor_staff_id
            ),
            "operation_identity": (
                event.operation_identity
            ),
            "scan_timestamp": (
                event.scan_timestamp.isoformat()
            ),
            "medication_administration_id": (
                event.medication_administration_id
            ),
            "patient_match": event.patient_match,
            "medication_match": (
                event.medication_match
            ),
            "dose_match": event.dose_match,
            "route_match": event.route_match,
            "time_window_match": (
                event.time_window_match
            ),
            "scan_device": event.scan_device,
            "verification_policy_version": (
                event.verification_policy_version
            ),
        },
        user_id=actor.user_id,
    )


def _create_event(
    *,
    clinic_id: int,
    actor: Staff,
    patient_barcode: str,
    medication_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    operation_identity: str,
    verification_result: (
        BarcodeVerificationResult
    ),
    failure_code: (
        BarcodeVerificationFailureCode
        | None
    ),
    failure_reason: str | None,
    patient_match: bool,
    medication_match: bool,
    dose_match: bool,
    route_match: bool,
    time_window_match: bool,
    scanned_patient: Patient | None = None,
    scanned_drug: Drug | None = None,
    record: MedicationAdministration | None = None,
    item: PrescriptionItem | None = None,
) -> MedicationAdministrationEvent:
    context = _event_context(
        record=record,
        item=item,
    )

    event = MedicationAdministrationEvent(
        clinic_id=clinic_id,
        actor_staff_id=actor.id,
        scanned_patient_id=(
            scanned_patient.id
            if scanned_patient is not None
            else None
        ),
        scanned_drug_id=(
            scanned_drug.id
            if scanned_drug is not None
            else None
        ),
        patient_barcode=patient_barcode,
        medication_barcode=(
            medication_barcode
        ),
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        verification_result=(
            verification_result
        ),
        failure_code=failure_code,
        failure_reason=failure_reason,
        patient_match=patient_match,
        medication_match=(
            medication_match
        ),
        dose_match=dose_match,
        route_match=route_match,
        time_window_match=(
            time_window_match
        ),
        verification_policy_version=(
            BARCODE_VERIFICATION_POLICY_VERSION
        ),
        operation_identity=(
            operation_identity
        ),
        **context,
    )

    savepoint = db.session.begin_nested()

    db.session.add(
        event
    )

    try:
        db.session.flush()
    except IntegrityError:
        savepoint.rollback()

        existing = db.session.execute(
            select(
                MedicationAdministrationEvent
            )
            .where(
                MedicationAdministrationEvent.clinic_id
                == clinic_id,
                MedicationAdministrationEvent.operation_identity
                == operation_identity,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing is None:
            raise ConflictError(
                "Barcode operation could not be reconciled",
                code=(
                    "BARCODE_OPERATION_OUTCOME_UNRESOLVED"
                ),
            )

        if not _same_scan_request(
            existing,
            actor_staff_id=actor.id,
            patient_barcode=patient_barcode,
            medication_barcode=medication_barcode,
            scan_timestamp=scan_timestamp,
            scan_device=scan_device,
        ):
            raise ConflictError(
                "Barcode operation identity was already "
                "used for a different scan request",
                code=(
                    "BARCODE_OPERATION_PAYLOAD_MISMATCH"
                ),
            )

        return existing

    savepoint.commit()

    _audit_event(
        event,
        actor=actor,
    )

    return event


def _update_administration_snapshot(
    record: MedicationAdministration,
    *,
    actor: Staff,
    patient_barcode: str,
    medication_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    result: BarcodeVerificationResult,
) -> None:
    record.patient_barcode = (
        patient_barcode
    )
    record.medication_barcode = (
        medication_barcode
    )
    record.scan_timestamp = (
        scan_timestamp
    )
    record.scan_device = (
        scan_device
    )
    record.verification_result = (
        result
    )
    record.barcode_verified_by_staff_id = (
        actor.id
    )


def _failed(
    *,
    clinic_id: int,
    actor: Staff,
    patient_barcode: str,
    medication_barcode: str,
    scan_timestamp: datetime,
    scan_device: str,
    operation_identity: str,
    failure_code: BarcodeVerificationFailureCode,
    failure_reason: str,
    patient_match: bool = False,
    medication_match: bool = False,
    dose_match: bool = False,
    route_match: bool = False,
    time_window_match: bool = False,
    scanned_patient: Patient | None = None,
    scanned_drug: Drug | None = None,
    record: MedicationAdministration | None = None,
    item: PrescriptionItem | None = None,
) -> MedicationAdministrationEvent:
    if record is not None:
        _update_administration_snapshot(
            record,
            actor=actor,
            patient_barcode=patient_barcode,
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=scan_timestamp,
            scan_device=scan_device,
            result=(
                BarcodeVerificationResult
                .NOT_BARCODE_VERIFIED
            ),
        )

    return _create_event(
        clinic_id=clinic_id,
        actor=actor,
        patient_barcode=patient_barcode,
        medication_barcode=(
            medication_barcode
        ),
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        operation_identity=(
            operation_identity
        ),
        verification_result=(
            BarcodeVerificationResult
            .NOT_BARCODE_VERIFIED
        ),
        failure_code=failure_code,
        failure_reason=failure_reason,
        patient_match=patient_match,
        medication_match=(
            medication_match
        ),
        dose_match=dose_match,
        route_match=route_match,
        time_window_match=(
            time_window_match
        ),
        scanned_patient=(
            scanned_patient
        ),
        scanned_drug=scanned_drug,
        record=record,
        item=item,
    )


def _find_patient(
    *,
    clinic_id: int,
    patient_barcode: str,
) -> tuple[
    Patient | None,
    BarcodeVerificationFailureCode | None,
]:
    patient = db.session.execute(
        select(
            Patient
        ).where(
            Patient.clinic_id
            == clinic_id,
            Patient.barcode
            == patient_barcode,
        )
    ).scalar_one_or_none()

    if patient is not None:
        return (
            patient,
            None,
        )

    other_clinic_match = (
        db.session.execute(
            select(
                Patient.id
            ).where(
                Patient.barcode
                == patient_barcode,
                Patient.clinic_id
                != clinic_id,
            )
            .limit(1)
        ).scalar_one_or_none()
    )

    if other_clinic_match is not None:
        return (
            None,
            BarcodeVerificationFailureCode
            .CROSS_CLINIC_PATIENT_BARCODE,
        )

    return (
        None,
        BarcodeVerificationFailureCode
        .UNKNOWN_PATIENT_BARCODE,
    )


def _find_drug(
    *,
    clinic_id: int,
    medication_barcode: str,
) -> tuple[
    Drug | None,
    BarcodeVerificationFailureCode | None,
]:
    drug = db.session.execute(
        select(
            Drug
        ).where(
            Drug.barcode
            == medication_barcode,
            Drug.is_active.is_(
                True
            ),
        )
    ).scalar_one_or_none()

    if drug is None:
        return (
            None,
            BarcodeVerificationFailureCode
            .UNKNOWN_MEDICATION_BARCODE,
        )

    if (
        drug.clinic_id is not None
        and drug.clinic_id != clinic_id
    ):
        return (
            None,
            BarcodeVerificationFailureCode
            .CROSS_CLINIC_MEDICATION_BARCODE,
        )

    return (
        drug,
        None,
    )


def _active_admission_for_patient(
    patient: Patient,
) -> Admission | None:
    rows = (
        db.session.execute(
            select(
                Admission
            ).where(
                Admission.patient_id
                == patient.id,
                Admission.status
                == AdmissionStatus.ADMITTED,
                Admission.discharged_at
                .is_(None),
            )
            .order_by(
                Admission.admitted_at.desc(),
                Admission.id.desc(),
            )
        )
        .scalars()
        .all()
    )

    if len(rows) != 1:
        return None

    return rows[0]


def _pending_rows(
    *,
    clinic_id: int,
    patient_id: int,
    admission_id: int,
    drug_id: int | None = None,
) -> list[MedicationAdministration]:
    query = (
        select(
            MedicationAdministration
        )
        .join(
            PrescriptionItem,
            PrescriptionItem.id
            == MedicationAdministration
            .prescription_item_id,
        )
        .where(
            MedicationAdministration.clinic_id
            == clinic_id,
            MedicationAdministration.patient_id
            == patient_id,
            MedicationAdministration.admission_id
            == admission_id,
            MedicationAdministration.status
            .in_(
                _PENDING_STATUSES
            ),
            MedicationAdministration.scheduled_at
            .is_not(None),
        )
    )

    if drug_id is not None:
        query = query.where(
            PrescriptionItem.drug_id
            == drug_id
        )

    return list(
        db.session.execute(
            query
        ).scalars().all()
    )



def _matching_rows_any_status(
    *,
    clinic_id: int,
    patient_id: int,
    admission_id: int,
    drug_id: int,
) -> list[MedicationAdministration]:
    return list(
        db.session.execute(
            select(
                MedicationAdministration
            )
            .join(
                PrescriptionItem,
                PrescriptionItem.id
                == MedicationAdministration
                .prescription_item_id,
            )
            .where(
                MedicationAdministration.clinic_id
                == clinic_id,
                MedicationAdministration.patient_id
                == patient_id,
                MedicationAdministration.admission_id
                == admission_id,
                PrescriptionItem.drug_id
                == drug_id,
                MedicationAdministration.scheduled_at
                .is_not(None),
            )
        ).scalars().all()
    )


def _within_window(
    record: MedicationAdministration,
    *,
    scan_timestamp: datetime,
) -> bool:
    if record.scheduled_at is None:
        return False

    (
        start,
        end,
    ) = _window_for(
        record.scheduled_at
    )

    return (
        start
        <= scan_timestamp
        <= end
    )


def _distance_seconds(
    record: MedicationAdministration,
    *,
    scan_timestamp: datetime,
) -> float:
    scheduled_at = _as_utc(
        record.scheduled_at
    )

    if scheduled_at is None:
        return float(
            "inf"
        )

    return abs(
        (
            scan_timestamp
            - scheduled_at
        ).total_seconds()
    )


def _choose_candidate(
    records: Iterable[
        MedicationAdministration
    ],
    *,
    scan_timestamp: datetime,
) -> tuple[
    MedicationAdministration | None,
    bool,
]:
    rows = list(
        records
    )

    if not rows:
        return (
            None,
            False,
        )

    ranked = sorted(
        rows,
        key=lambda record: (
            _distance_seconds(
                record,
                scan_timestamp=(
                    scan_timestamp
                ),
            ),
            record.id,
        ),
    )

    if len(ranked) > 1:
        first = _distance_seconds(
            ranked[0],
            scan_timestamp=scan_timestamp,
        )
        second = _distance_seconds(
            ranked[1],
            scan_timestamp=scan_timestamp,
        )

        if first == second:
            return (
                None,
                True,
            )

    return (
        ranked[0],
        False,
    )


def _lock_record(
    administration_id: int,
) -> MedicationAdministration:
    return db.session.execute(
        select(
            MedicationAdministration
        )
        .where(
            MedicationAdministration.id
            == administration_id
        )
        .with_for_update()
    ).scalar_one()


def _lock_item(
    item_id: int,
) -> PrescriptionItem:
    return db.session.execute(
        select(
            PrescriptionItem
        )
        .where(
            PrescriptionItem.id
            == item_id
        )
        .with_for_update()
    ).scalar_one()


def _lock_prescription(
    prescription_id: int,
) -> Prescription:
    return db.session.execute(
        select(
            Prescription
        )
        .where(
            Prescription.id
            == prescription_id
        )
        .with_for_update()
    ).scalar_one()


def _lock_admission(
    admission_id: int,
) -> Admission:
    return db.session.execute(
        select(
            Admission
        )
        .where(
            Admission.id
            == admission_id
        )
        .with_for_update()
    ).scalar_one()


def _order_failure(
    *,
    prescription: Prescription,
    item: PrescriptionItem,
    scan_timestamp: datetime,
) -> tuple[
    BarcodeVerificationFailureCode | None,
    str | None,
]:
    if (
        prescription.status
        == PrescriptionStatus.CANCELLED
    ):
        return (
            BarcodeVerificationFailureCode
            .ORDER_CANCELLED,
            "Medication order is cancelled",
        )

    if (
        prescription.status
        == PrescriptionStatus.EXPIRED
    ):
        return (
            BarcodeVerificationFailureCode
            .ORDER_EXPIRED,
            "Medication order is expired",
        )

    if (
        prescription.status
        != PrescriptionStatus.ACTIVE
    ):
        return (
            BarcodeVerificationFailureCode
            .INACTIVE_ORDER,
            "Medication order is not active",
        )

    expires_at = _as_utc(
        prescription.expires_at
    )

    if (
        expires_at is not None
        and expires_at
        <= scan_timestamp
    ):
        return (
            BarcodeVerificationFailureCode
            .ORDER_EXPIRED,
            "Medication order has expired",
        )

    if (
        item.order_type
        != MedicationOrderType.INPATIENT
    ):
        return (
            BarcodeVerificationFailureCode
            .INACTIVE_ORDER,
            "Barcode eMAR requires "
            "an inpatient order",
        )

    start_at = _as_utc(
        item.start_at
    )

    if (
        start_at is not None
        and scan_timestamp < start_at
    ):
        return (
            BarcodeVerificationFailureCode
            .INACTIVE_ORDER,
            "Medication order has not started",
        )

    end_at = _as_utc(
        item.end_at
    )

    if (
        end_at is not None
        and scan_timestamp > end_at
    ):
        return (
            BarcodeVerificationFailureCode
            .INACTIVE_ORDER,
            "Medication order has ended",
        )

    return (
        None,
        None,
    )


def _same_decimal(
    left,
    right,
) -> bool:
    if (
        left is None
        or right is None
    ):
        return False

    return (
        Decimal(
            str(left)
        )
        == Decimal(
            str(right)
        )
    )



def list_barcode_verification_events(
    *,
    administration_id: int,
    clinic_id: int,
) -> list[MedicationAdministrationEvent]:
    """
    Read-only, clinic-scoped barcode verification history.

    The administration is resolved under tenant scope before event lookup,
    preventing cross-clinic identifiers from enumerating verification data.
    """

    record = (
        emar_service
        .get_medication_administration(
            administration_id,
            clinic_id=clinic_id,
        )
    )

    return list(
        db.session.execute(
            select(
                MedicationAdministrationEvent
            )
            .where(
                MedicationAdministrationEvent.clinic_id
                == clinic_id,
                MedicationAdministrationEvent
                .medication_administration_id
                == record.id,
            )
            .order_by(
                MedicationAdministrationEvent
                .scan_timestamp.desc(),
                MedicationAdministrationEvent
                .id.desc(),
            )
        )
        .scalars()
        .all()
    )

@transactional
def verify_barcode_medication_administration(
    *,
    clinic_id: int,
    actor_staff_id: int,
    patient_barcode: str | None,
    medication_barcode: str | None,
    scan_timestamp: datetime,
    scan_device: str,
    operation_identity: str,
) -> MedicationAdministrationEvent:
    """
    Server-authoritative barcode medication verification.

    The client supplies scanned identities and scan metadata only.
    Patient, admission, order, scheduled administration, dose, route,
    and time-window truth are rebuilt from the database.
    """

    scan_timestamp = (
        _normalize_scan_timestamp(
            scan_timestamp
        )
    )

    scan_device = (
        _normalize_required_text(
            scan_device,
            field_name="scan_device",
            max_length=255,
        )
    )

    operation_identity = (
        _normalize_required_text(
            operation_identity,
            field_name=(
                "operation_identity"
            ),
            max_length=255,
        )
    )

    patient_barcode = (
        _normalize_barcode(
            patient_barcode
        )
    )

    medication_barcode = (
        _normalize_barcode(
            medication_barcode
        )
    )

    ensure_clinic_active(
        clinic_id
    )

    actor = (
        emar_service
        ._get_authorized_actor(
            actor_staff_id,
            clinic_id=clinic_id,
        )
    )

    existing = _existing_operation(
        clinic_id=clinic_id,
        operation_identity=(
            operation_identity
        ),
        actor_staff_id=actor.id,
        patient_barcode=(
            patient_barcode
        ),
        medication_barcode=(
            medication_barcode
        ),
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
    )

    if existing is not None:
        return existing

    if _barcode_is_malformed(
        patient_barcode
    ):
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .MALFORMED_PATIENT_BARCODE
            ),
            failure_reason=(
                "Patient barcode is malformed"
            ),
        )

    (
        patient,
        patient_failure,
    ) = _find_patient(
        clinic_id=clinic_id,
        patient_barcode=(
            patient_barcode
        ),
    )

    if patient is None:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                patient_failure
                or BarcodeVerificationFailureCode
                .UNKNOWN_PATIENT_BARCODE
            ),
            failure_reason=(
                "Patient barcode does not resolve "
                "inside the current clinic"
            ),
        )

    if not patient.is_active:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .WRONG_PATIENT
            ),
            failure_reason=(
                "Scanned patient is inactive"
            ),
            scanned_patient=patient,
        )

    if _barcode_is_malformed(
        medication_barcode
    ):
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .MALFORMED_MEDICATION_BARCODE
            ),
            failure_reason=(
                "Medication barcode is malformed"
            ),
            patient_match=True,
            scanned_patient=patient,
        )

    (
        drug,
        medication_failure,
    ) = _find_drug(
        clinic_id=clinic_id,
        medication_barcode=(
            medication_barcode
        ),
    )

    if drug is None:
        failure_code = (
            medication_failure
            or BarcodeVerificationFailureCode
            .UNKNOWN_MEDICATION_BARCODE
        )

        failure_reason = (
            "Medication barcode belongs to "
            "a different clinic"
            if (
                failure_code
                == BarcodeVerificationFailureCode
                .CROSS_CLINIC_MEDICATION_BARCODE
            )
            else
            "Medication barcode is not "
            "valid for the current clinic"
        )

        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                failure_code
            ),
            failure_reason=(
                failure_reason
            ),
            patient_match=True,
            scanned_patient=patient,
        )

    admission = (
        _active_admission_for_patient(
            patient
        )
    )

    if admission is None:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .INACTIVE_ADMISSION
            ),
            failure_reason=(
                "No unique active admission "
                "could be resolved"
            ),
            patient_match=True,
            medication_match=True,
            scanned_patient=patient,
            scanned_drug=drug,
        )

    same_drug = _pending_rows(
        clinic_id=clinic_id,
        patient_id=patient.id,
        admission_id=admission.id,
        drug_id=drug.id,
    )

    if not same_drug:
        historical_same_drug = (
            _matching_rows_any_status(
                clinic_id=clinic_id,
                patient_id=patient.id,
                admission_id=admission.id,
                drug_id=drug.id,
            )
        )

        stale_candidate, stale_ambiguous = (
            _choose_candidate(
                historical_same_drug,
                scan_timestamp=scan_timestamp,
            )
        )

        if (
            stale_candidate is not None
            and not stale_ambiguous
            and _within_window(
                stale_candidate,
                scan_timestamp=scan_timestamp,
            )
        ):
            stale_record = _lock_record(
                stale_candidate.id
            )
            stale_item = _lock_item(
                stale_record.prescription_item_id
            )

            return _failed(
                clinic_id=clinic_id,
                actor=actor,
                patient_barcode=(
                    patient_barcode
                ),
                medication_barcode=(
                    medication_barcode
                ),
                scan_timestamp=(
                    scan_timestamp
                ),
                scan_device=scan_device,
                operation_identity=(
                    operation_identity
                ),
                failure_code=(
                    BarcodeVerificationFailureCode
                    .STALE_VERIFICATION
                ),
                failure_reason=(
                    "Medication administration is no "
                    "longer pending"
                ),
                patient_match=(
                    stale_record.patient_id
                    == patient.id
                ),
                medication_match=(
                    stale_item.drug_id
                    == drug.id
                ),
                dose_match=(
                    _same_decimal(
                        stale_record.dose_value,
                        stale_item.dose_value,
                    )
                    and stale_record.dose_unit
                    == stale_item.dose_unit
                ),
                route_match=(
                    stale_record.route_code
                    == stale_item.route_code
                ),
                time_window_match=True,
                scanned_patient=patient,
                scanned_drug=drug,
                record=stale_record,
                item=stale_item,
            )

        cross_patient = (
            db.session.execute(
                select(
                    MedicationAdministration.id
                )
                .join(
                    PrescriptionItem,
                    PrescriptionItem.id
                    == MedicationAdministration
                    .prescription_item_id,
                )
                .where(
                    MedicationAdministration.clinic_id
                    == clinic_id,
                    MedicationAdministration.patient_id
                    != patient.id,
                    MedicationAdministration.status
                    .in_(
                        _PENDING_STATUSES
                    ),
                    PrescriptionItem.drug_id
                    == drug.id,
                    MedicationAdministration.scheduled_at
                    .is_not(None),
                )
                .limit(1)
            ).scalar_one_or_none()
        )

        code = (
            BarcodeVerificationFailureCode
            .WRONG_PATIENT
            if cross_patient is not None
            else
            BarcodeVerificationFailureCode
            .WRONG_MEDICATION
        )

        reason = (
            "Scanned medication has a pending "
            "administration for a different patient"
            if cross_patient is not None
            else
            "Scanned medication does not match "
            "a pending administration for this patient"
        )

        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=code,
            failure_reason=reason,
            patient_match=(
                code
                != BarcodeVerificationFailureCode
                .WRONG_PATIENT
            ),
            medication_match=False,
            scanned_patient=patient,
            scanned_drug=drug,
        )

    in_window = [
        record
        for record in same_drug
        if _within_window(
            record,
            scan_timestamp=scan_timestamp,
        )
    ]

    candidate_pool = (
        in_window
        if in_window
        else same_drug
    )

    (
        candidate,
        ambiguous,
    ) = _choose_candidate(
        candidate_pool,
        scan_timestamp=scan_timestamp,
    )

    if ambiguous:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .AMBIGUOUS_SCHEDULED_ADMINISTRATION
            ),
            failure_reason=(
                "Multiple equally valid scheduled "
                "administrations match this scan"
            ),
            patient_match=True,
            medication_match=True,
            scanned_patient=patient,
            scanned_drug=drug,
        )

    if candidate is None:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .NO_SCHEDULED_ADMINISTRATION
            ),
            failure_reason=(
                "No scheduled administration "
                "could be resolved"
            ),
            patient_match=True,
            medication_match=True,
            scanned_patient=patient,
            scanned_drug=drug,
        )

    record = _lock_record(
        candidate.id
    )

    if record.status not in _PENDING_STATUSES:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .STALE_VERIFICATION
            ),
            failure_reason=(
                "Medication administration is no "
                "longer pending"
            ),
            patient_match=(
                record.patient_id
                == patient.id
            ),
            medication_match=True,
            scanned_patient=patient,
            scanned_drug=drug,
            record=record,
        )

    locked_admission = _lock_admission(
        record.admission_id
    )

    item = _lock_item(
        record.prescription_item_id
    )

    prescription = _lock_prescription(
        record.prescription_id
    )

    if (
        locked_admission.status
        != AdmissionStatus.ADMITTED
        or locked_admission.discharged_at
        is not None
    ):
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=(
                BarcodeVerificationFailureCode
                .INACTIVE_ADMISSION
            ),
            failure_reason=(
                "Medication administration requires "
                "an active admission"
            ),
            patient_match=(
                record.patient_id
                == patient.id
            ),
            medication_match=(
                item.drug_id
                == drug.id
            ),
            scanned_patient=patient,
            scanned_drug=drug,
            record=record,
            item=item,
        )

    (
        order_failure,
        order_reason,
    ) = _order_failure(
        prescription=prescription,
        item=item,
        scan_timestamp=scan_timestamp,
    )

    if order_failure is not None:
        return _failed(
            clinic_id=clinic_id,
            actor=actor,
            patient_barcode=(
                patient_barcode
            ),
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device=scan_device,
            operation_identity=(
                operation_identity
            ),
            failure_code=order_failure,
            failure_reason=(
                order_reason
                or "Medication order is invalid"
            ),
            patient_match=(
                record.patient_id
                == patient.id
            ),
            medication_match=(
                item.drug_id
                == drug.id
            ),
            scanned_patient=patient,
            scanned_drug=drug,
            record=record,
            item=item,
        )

    patient_match = (
        record.patient_id
        == patient.id
    )

    medication_match = (
        item.drug_id
        == drug.id
    )

    dose_match = (
        _same_decimal(
            record.dose_value,
            item.dose_value,
        )
        and record.dose_unit
        == item.dose_unit
    )

    route_match = (
        record.route_code
        == item.route_code
    )

    time_window_match = (
        _within_window(
            record,
            scan_timestamp=scan_timestamp,
        )
    )

    failures = (
        (
            patient_match,
            BarcodeVerificationFailureCode
            .WRONG_PATIENT,
            "Scanned patient does not match "
            "the scheduled administration",
        ),
        (
            medication_match,
            BarcodeVerificationFailureCode
            .WRONG_MEDICATION,
            "Scanned medication does not match "
            "the scheduled administration",
        ),
        (
            dose_match,
            BarcodeVerificationFailureCode
            .DOSE_MISMATCH,
            "Scheduled dose no longer matches "
            "the authoritative medication order",
        ),
        (
            route_match,
            BarcodeVerificationFailureCode
            .ROUTE_MISMATCH,
            "Scheduled route no longer matches "
            "the authoritative medication order",
        ),
        (
            time_window_match,
            BarcodeVerificationFailureCode
            .OUTSIDE_TIME_WINDOW,
            "Medication scan is outside the "
            "allowed administration window",
        ),
    )

    for (
        matched,
        failure_code,
        reason,
    ) in failures:
        if not matched:
            return _failed(
                clinic_id=clinic_id,
                actor=actor,
                patient_barcode=(
                    patient_barcode
                ),
                medication_barcode=(
                    medication_barcode
                ),
                scan_timestamp=(
                    scan_timestamp
                ),
                scan_device=scan_device,
                operation_identity=(
                    operation_identity
                ),
                failure_code=(
                    failure_code
                ),
                failure_reason=reason,
                patient_match=(
                    patient_match
                ),
                medication_match=(
                    medication_match
                ),
                dose_match=dose_match,
                route_match=route_match,
                time_window_match=(
                    time_window_match
                ),
                scanned_patient=patient,
                scanned_drug=drug,
                record=record,
                item=item,
            )

    _update_administration_snapshot(
        record,
        actor=actor,
        patient_barcode=patient_barcode,
        medication_barcode=(
            medication_barcode
        ),
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        result=(
            BarcodeVerificationResult
            .BARCODE_VERIFIED
        ),
    )

    return _create_event(
        clinic_id=clinic_id,
        actor=actor,
        patient_barcode=(
            patient_barcode
        ),
        medication_barcode=(
            medication_barcode
        ),
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        operation_identity=(
            operation_identity
        ),
        verification_result=(
            BarcodeVerificationResult
            .BARCODE_VERIFIED
        ),
        failure_code=None,
        failure_reason=None,
        patient_match=True,
        medication_match=True,
        dose_match=True,
        route_match=True,
        time_window_match=True,
        scanned_patient=patient,
        scanned_drug=drug,
        record=record,
        item=item,
    )
