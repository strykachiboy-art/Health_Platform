from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest

import app.modules.emar.services.barcode_verification_service as barcode_service
import app.modules.emar.services.emar_service as emar_service
from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.emar.models.emar_model import (
    MedicationAdministrationEvent,
)
from app.modules.patient.models.patient_model import Patient
from app.tests.modules.emar.test_emar_service_invariants import (
    _base_context,
    _schedule,
)


def _context(
    *,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    scheduled_at=None,
):
    (
        actor,
        drug,
        admission,
        prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    patient.barcode = (
        f"PAT-{patient.id}"
    )

    drug.barcode = (
        f"MED-{drug.id}"
    )

    db.session.flush()

    scheduled_at = (
        scheduled_at
        or datetime.now(
            timezone.utc
        )
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
        scheduled_at=scheduled_at,
    )

    return (
        actor,
        drug,
        admission,
        prescription,
        item,
        record,
        scheduled_at,
    )


def _verify(
    *,
    clinic,
    actor,
    patient_barcode,
    medication_barcode,
    scan_timestamp,
    operation_identity,
):
    return (
        barcode_service
        .verify_barcode_medication_administration(
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            patient_barcode=patient_barcode,
            medication_barcode=(
                medication_barcode
            ),
            scan_timestamp=(
                scan_timestamp
            ),
            scan_device="rover-test-01",
            operation_identity=(
                operation_identity
            ),
        )
    )


def test_verified_scan_resolves_authoritative_five_right_context(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    audits = []

    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        item,
        record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-verified",
    )

    assert (
        event.verification_result
        == BarcodeVerificationResult
        .BARCODE_VERIFIED
    )
    assert event.failure_code is None
    assert event.patient_match is True
    assert event.medication_match is True
    assert event.dose_match is True
    assert event.route_match is True
    assert event.time_window_match is True
    assert (
        event.medication_administration_id
        == record.id
    )
    assert (
        event.expected_drug_id
        == drug.id
    )
    assert (
        event.expected_dose_value
        == item.dose_value
    )
    assert (
        event.expected_route_code
        == item.route_code
    )

    assert (
        record.verification_result
        == BarcodeVerificationResult
        .BARCODE_VERIFIED
    )
    assert (
        record.patient_barcode
        == patient.barcode
    )
    assert (
        record.medication_barcode
        == drug.barcode
    )
    assert (
        record.barcode_verified_by_staff_id
        == actor.id
    )

    assert len(audits) == 1
    assert (
        audits[0]["entity_type"]
        == "MedicationAdministrationEvent"
    )


@pytest.mark.parametrize(
    (
        "patient_barcode",
        "expected_code",
    ),
    [
        (
            "",
            BarcodeVerificationFailureCode
            .MALFORMED_PATIENT_BARCODE,
        ),
        (
            "UNKNOWN-PATIENT",
            BarcodeVerificationFailureCode
            .UNKNOWN_PATIENT_BARCODE,
        ),
    ],
)
def test_unresolved_patient_scan_is_durably_recorded(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    patient_barcode,
    expected_code,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient_barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity=(
            f"gate7-patient-{expected_code.value}"
        ),
    )

    assert (
        event.verification_result
        == BarcodeVerificationResult
        .NOT_BARCODE_VERIFIED
    )
    assert (
        event.failure_code
        == expected_code
    )
    assert event.patient_id is None
    assert (
        event.medication_administration_id
        is None
    )


def test_cross_clinic_patient_barcode_fails_without_linking_other_patient(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_clinic = Clinic(
        name="Gate 7 Other Clinic",
    )
    db.session.add(
        other_clinic
    )
    db.session.flush()

    other_patient = Patient(
        clinic_id=other_clinic.id,
        first_name="Other",
        last_name="Patient",
        patient_number=(
            "GATE7-OTHER-PATIENT"
        ),
        barcode="CROSS-CLINIC-BARCODE",
        is_active=True,
    )
    db.session.add(
        other_patient
    )
    db.session.flush()

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=(
            "CROSS-CLINIC-BARCODE"
        ),
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity=(
            "gate7-cross-clinic"
        ),
    )

    assert (
        event.failure_code
        == BarcodeVerificationFailureCode
        .CROSS_CLINIC_PATIENT_BARCODE
    )
    assert event.scanned_patient_id is None
    assert event.patient_id is None


def test_unknown_medication_barcode_is_durably_recorded(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        _drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode="UNKNOWN-MED",
        scan_timestamp=scheduled_at,
        operation_identity="gate7-unknown-med",
    )

    assert (
        event.failure_code
        == BarcodeVerificationFailureCode
        .UNKNOWN_MEDICATION_BARCODE
    )
    assert event.patient_match is True
    assert (
        event.medication_administration_id
        is None
    )


def test_wrong_medication_is_rejected(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        _drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    wrong_drug = make_drug(
        clinic,
        is_active=True,
    )
    wrong_drug.barcode = (
        f"MED-WRONG-{wrong_drug.id}"
    )
    db.session.flush()

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=(
            wrong_drug.barcode
        ),
        scan_timestamp=scheduled_at,
        operation_identity="gate7-wrong-med",
    )

    assert (
        event.failure_code
        == BarcodeVerificationFailureCode
        .WRONG_MEDICATION
    )
    assert (
        event.verification_result
        == BarcodeVerificationResult
        .NOT_BARCODE_VERIFIED
    )


def test_outside_time_window_records_failed_event_and_snapshot(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    scheduled_at = (
        datetime.now(
            timezone.utc
        )
        + timedelta(
            hours=3
        )
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        record,
        _scheduled,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
        scheduled_at=scheduled_at,
    )

    scan_at = datetime.now(
        timezone.utc
    )

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scan_at,
        operation_identity="gate7-time-window",
    )

    assert (
        event.failure_code
        == BarcodeVerificationFailureCode
        .OUTSIDE_TIME_WINDOW
    )
    assert event.time_window_match is False
    assert (
        event.medication_administration_id
        == record.id
    )
    assert (
        record.verification_result
        == BarcodeVerificationResult
        .NOT_BARCODE_VERIFIED
    )


@pytest.mark.parametrize(
    (
        "field",
        "new_value",
        "expected_code",
    ),
    [
        (
            "dose_value",
            750,
            BarcodeVerificationFailureCode
            .DOSE_MISMATCH,
        ),
        (
            "route_code",
            "iv",
            BarcodeVerificationFailureCode
            .ROUTE_MISMATCH,
        ),
    ],
)
def test_order_snapshot_drift_fails_closed(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    field,
    new_value,
    expected_code,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    setattr(
        item,
        field,
        new_value,
    )
    db.session.flush()

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity=(
            f"gate7-drift-{field}"
        ),
    )

    assert (
        event.failure_code
        == expected_code
    )


@pytest.mark.parametrize(
    (
        "mutation",
        "expected_code",
    ),
    [
        (
            "cancel_order",
            BarcodeVerificationFailureCode
            .ORDER_CANCELLED,
        ),
        (
            "discharge",
            BarcodeVerificationFailureCode
            .INACTIVE_ADMISSION,
        ),
        (
            "stale",
            BarcodeVerificationFailureCode
            .STALE_VERIFICATION,
        ),
    ],
)
def test_invalidated_context_fails_closed(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    mutation,
    expected_code,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        drug,
        admission,
        prescription,
        _item,
        record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    if mutation == "cancel_order":
        prescription.status = (
            PrescriptionStatus.CANCELLED
        )

    elif mutation == "discharge":
        admission.status = (
            AdmissionStatus.DISCHARGED
        )
        admission.discharged_at = (
            scheduled_at
        )

    elif mutation == "stale":
        # Capture the scalar before mutating the eMAR row.
        # Reading actor.id after status=HELD can trigger autoflush
        # while HELD evidence is still incomplete.
        actor_id = actor.id

        record.status = (
            MedicationAdministrationStatus
            .HELD
        )
        record.held_at = (
            scheduled_at
        )
        record.held_by_staff_id = (
            actor_id
        )
        record.hold_reason = (
            "Clinical hold"
        )

    db.session.flush()

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity=(
            f"gate7-invalid-{mutation}"
        ),
    )

    assert (
        event.failure_code
        == expected_code
    )
    assert (
        event.verification_result
        == BarcodeVerificationResult
        .NOT_BARCODE_VERIFIED
    )


def test_same_operation_identity_replays_same_verification_event(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    audit_count = {
        "n": 0,
    }

    def audit(**_kwargs):
        audit_count["n"] += 1

    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        audit,
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    first = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-replay",
    )

    second = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-replay",
    )

    assert second.id == first.id
    assert audit_count["n"] == 1

    count = (
        db.session.query(
            MedicationAdministrationEvent
        )
        .filter(
            MedicationAdministrationEvent
            .operation_identity
            == "gate7-replay"
        )
        .count()
    )

    assert count == 1


def test_operation_identity_payload_mismatch_fails_closed(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=(
            make_authenticated_staff
        ),
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-mismatch",
    )

    with pytest.raises(
        ConflictError,
    ) as exc_info:
        barcode_service.verify_barcode_medication_administration(
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            patient_barcode=patient.barcode,
            medication_barcode=drug.barcode,
            scan_timestamp=scheduled_at,
            scan_device="different-device",
            operation_identity="gate7-mismatch",
        )

    assert (
        getattr(
            exc_info.value,
            "code",
            None,
        )
        == "BARCODE_OPERATION_PAYLOAD_MISMATCH"
    )
