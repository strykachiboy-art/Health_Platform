from __future__ import annotations

from datetime import timedelta

import pytest

import app.modules.emar.services.barcode_verification_service as barcode_service
import app.modules.emar.services.emar_service as emar_service
from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.emar.models.emar_model import (
    MedicationAdministration,
    MedicationAdministrationEvent,
)
from app.tests.modules.emar.test_barcode_verification_service import _context


def _silence_audits(monkeypatch):
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )


def _setup(
    *,
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
    silence_audits=True,
):
    if silence_audits:
        _silence_audits(monkeypatch)

    return _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )


def _scan(
    *,
    clinic,
    actor,
    patient_barcode,
    medication_barcode,
    scan_timestamp,
    operation_identity,
    scan_device="rover-security-01",
):
    return barcode_service.verify_barcode_medication_administration(
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        patient_barcode=patient_barcode,
        medication_barcode=medication_barcode,
        scan_timestamp=scan_timestamp,
        scan_device=scan_device,
        operation_identity=operation_identity,
    )


def _event_count(operation_identity):
    return (
        db.session.query(MedicationAdministrationEvent)
        .filter(
            MedicationAdministrationEvent.operation_identity
            == operation_identity
        )
        .count()
    )


def test_cross_clinic_medication_barcode_is_explicit_and_never_links_foreign_drug(
    monkeypatch,
    db,
    clinic,
    patient,
    make_clinic,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        _local_drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_clinic = make_clinic(
        name="Gate 7 Cross Medication Clinic",
    )
    foreign_drug = make_drug(
        other_clinic,
        barcode="GATE7-CROSS-CLINIC-MED",
        is_active=True,
    )
    db.session.flush()

    event = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=foreign_drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-cross-med",
    )

    assert (
        event.verification_result
        == BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    )
    assert (
        event.failure_code
        == BarcodeVerificationFailureCode.CROSS_CLINIC_MEDICATION_BARCODE
    )
    assert event.scanned_drug_id is None
    assert event.expected_drug_id is None
    assert event.medication_administration_id is None


def test_inactive_staff_actor_is_rejected_without_event_or_snapshot_mutation(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record_id = record.id
    actor.status = StaffStatus.SUSPENDED
    db.session.flush()

    with pytest.raises(ValidationError):
        _scan(
            clinic=clinic,
            actor=actor,
            patient_barcode=patient.barcode,
            medication_barcode=drug.barcode,
            scan_timestamp=scheduled_at,
            operation_identity="gate7-s5-inactive-staff",
        )

    db.session.rollback()
    assert _event_count("gate7-s5-inactive-staff") == 0

    reloaded = db.session.get(MedicationAdministration, record_id)
    assert reloaded is not None
    assert reloaded.verification_result is None
    assert reloaded.scan_timestamp is None
    assert reloaded.scan_device is None


def test_inactive_user_actor_is_rejected_without_event_or_snapshot_mutation(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    record_id = record.id
    actor.user.is_active = False
    db.session.flush()

    with pytest.raises(ValidationError):
        _scan(
            clinic=clinic,
            actor=actor,
            patient_barcode=patient.barcode,
            medication_barcode=drug.barcode,
            scan_timestamp=scheduled_at,
            operation_identity="gate7-s5-inactive-user",
        )

    db.session.rollback()
    assert _event_count("gate7-s5-inactive-user") == 0

    reloaded = db.session.get(MedicationAdministration, record_id)
    assert reloaded is not None
    assert reloaded.verification_result is None


def test_wrong_role_actor_is_rejected_without_creating_barcode_event(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    pharmacist, _headers = make_authenticated_staff(
        clinic,
        Role.PHARMACIST,
    )

    with pytest.raises(ValidationError):
        _scan(
            clinic=clinic,
            actor=pharmacist,
            patient_barcode=patient.barcode,
            medication_barcode=drug.barcode,
            scan_timestamp=scheduled_at,
            operation_identity="gate7-s5-wrong-role",
        )

    assert _event_count("gate7-s5-wrong-role") == 0


def test_cross_clinic_actor_cannot_write_target_tenant_barcode_event(
    monkeypatch,
    db,
    clinic,
    patient,
    make_clinic,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        drug,
        _admission,
        _prescription,
        _item,
        _record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_clinic = make_clinic(
        name="Gate 7 Cross Actor Clinic",
    )
    foreign_actor, _headers = make_authenticated_staff(
        other_clinic,
        Role.NURSE,
    )

    with pytest.raises(ValidationError):
        _scan(
            clinic=clinic,
            actor=foreign_actor,
            patient_barcode=patient.barcode,
            medication_barcode=drug.barcode,
            scan_timestamp=scheduled_at,
            operation_identity="gate7-s5-cross-actor",
        )

    assert (
        db.session.query(MedicationAdministrationEvent)
        .filter(
            MedicationAdministrationEvent.clinic_id == clinic.id,
            MedicationAdministrationEvent.operation_identity
            == "gate7-s5-cross-actor",
        )
        .count()
        == 0
    )


def test_successful_barcode_audit_is_complete_and_replay_does_not_reaudit(
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
        emar_service,
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
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    audits = []
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    first = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-audit-success",
        scan_device="rover-audit-success",
    )
    second = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-audit-success",
        scan_device="rover-audit-success",
    )

    assert second.id == first.id
    assert len(audits) == 1

    audit = audits[0]
    assert audit["entity_type"] == "MedicationAdministrationEvent"
    assert audit["entity_id"] == first.id
    assert audit["clinic_id"] == clinic.id
    assert audit["user_id"] == actor.user_id

    new_value = audit["new_value"]
    assert new_value["verification_result"] == "barcode_verified"
    assert new_value["failure_code"] is None
    assert new_value["failure_reason"] is None
    assert new_value["actor_staff_id"] == actor.id
    assert new_value["operation_identity"] == "gate7-s5-audit-success"
    assert new_value["scan_device"] == "rover-audit-success"
    assert new_value["scan_timestamp"] == scheduled_at.isoformat()
    assert new_value["verification_policy_version"] >= 1


def test_failed_barcode_audit_keeps_failure_reason_and_scan_evidence(
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
        emar_service,
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
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    audits = []
    monkeypatch.setattr(
        barcode_service,
        "create_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    event = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode="GATE7-UNKNOWN-AUDIT-MED",
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-audit-failure",
        scan_device="rover-audit-failure",
    )

    assert (
        event.verification_result
        == BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    )
    assert len(audits) == 1

    new_value = audits[0]["new_value"]
    assert new_value["verification_result"] == "not_barcode_verified"
    assert new_value["failure_code"] == "unknown_medication_barcode"
    assert isinstance(new_value["failure_reason"], str)
    assert new_value["failure_reason"]
    assert new_value["operation_identity"] == "gate7-s5-audit-failure"
    assert new_value["scan_device"] == "rover-audit-failure"


def test_barcode_history_service_is_append_only_and_newest_first(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        _admission,
        _prescription,
        item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    first = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-history-1",
    )

    item.dose_value = 750
    db.session.commit()

    second = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at + timedelta(minutes=1),
        operation_identity="gate7-s5-history-2",
    )

    assert (
        second.failure_code
        == BarcodeVerificationFailureCode.DOSE_MISMATCH
    )

    history = barcode_service.list_barcode_verification_events(
        administration_id=record.id,
        clinic_id=clinic.id,
    )

    assert [event.id for event in history] == [second.id, first.id]
    assert (
        history[0].verification_result
        == BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    )
    assert (
        history[1].verification_result
        == BarcodeVerificationResult.BARCODE_VERIFIED
    )
    assert (
        db.session.query(MedicationAdministrationEvent)
        .filter(
            MedicationAdministrationEvent.medication_administration_id
            == record.id
        )
        .count()
        == 2
    )


def test_barcode_history_service_does_not_cross_tenant_boundary(
    monkeypatch,
    db,
    clinic,
    patient,
    make_clinic,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        _actor,
        _drug,
        _admission,
        _prescription,
        _item,
        record,
        _scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    other_clinic = make_clinic(
        name="Gate 7 History Other Clinic",
    )

    with pytest.raises(NotFoundError):
        barcode_service.list_barcode_verification_events(
            administration_id=record.id,
            clinic_id=other_clinic.id,
        )


def test_barcode_history_route_is_tenant_scoped_serialized_and_read_only(
    monkeypatch,
    client,
    db,
    clinic,
    patient,
    auth_headers_for,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        _admission,
        _prescription,
        _item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-history-route",
    )

    headers = auth_headers_for(
        actor.user,
        role=Role.NURSE,
    )
    url = f"/api/v1/emar/{record.id}/barcode-events"

    response = client.get(
        url,
        headers=headers,
    )
    assert response.status_code == 200

    payload = response.get_json()
    assert payload["history"]["total"] == 1
    assert payload["history"]["items"][0]["id"] == event.id
    assert (
        payload["history"]["items"][0]["operation_identity"]
        == "gate7-s5-history-route"
    )

    write_attempt = client.post(
        url,
        headers=headers,
        json={},
    )
    assert write_attempt.status_code == 405


def test_order_cancellation_after_successful_scan_invalidates_administration(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        _admission,
        prescription,
        _item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-cancel-after-verify",
    )

    prescription.status = PrescriptionStatus.CANCELLED
    db.session.commit()

    with pytest.raises((ConflictError, ValidationError)):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=event.id,
            administered_at=scheduled_at,
            idempotency_key="gate7-s5-cancel-admin",
            idempotency_user_id=actor.user_id,
        )

    db.session.rollback()
    reloaded = db.session.get(MedicationAdministration, record.id)
    assert reloaded is not None
    assert reloaded.status != MedicationAdministrationStatus.ADMINISTERED
    assert reloaded.barcode_verification_event_id is None


def test_discharge_after_successful_scan_invalidates_administration(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    (
        actor,
        drug,
        admission,
        _prescription,
        _item,
        record,
        scheduled_at,
    ) = _setup(
        monkeypatch=monkeypatch,
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    event = _scan(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="gate7-s5-discharge-after-verify",
    )

    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = scheduled_at
    db.session.commit()

    with pytest.raises((ConflictError, ValidationError)):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=event.id,
            administered_at=scheduled_at,
            idempotency_key="gate7-s5-discharge-admin",
            idempotency_user_id=actor.user_id,
        )

    db.session.rollback()
    reloaded = db.session.get(MedicationAdministration, record.id)
    assert reloaded is not None
    assert reloaded.status != MedicationAdministrationStatus.ADMINISTERED
    assert reloaded.barcode_verification_event_id is None
