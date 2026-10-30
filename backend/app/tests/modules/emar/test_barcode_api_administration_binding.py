from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

import app.modules.emar.routes.emar_routes as emar_routes
import app.modules.emar.services.barcode_verification_service as barcode_service
import app.modules.emar.services.emar_service as emar_service
from app.core.enums.emar_enums import (
    BarcodeVerificationFailureCode,
    BarcodeVerificationResult,
    MedicationAdministrationStatus,
)
from app.core.exceptions import ConflictError
from app.modules.emar.schemas.emar_schema import (
    BarcodeMedicationVerificationRequestSchema,
    MedicationAdministrationAdministerSchema,
)
from app.tests.modules.emar.test_barcode_verification_service import (
    _context,
    _verify,
)


def _raw(handler):
    return inspect.unwrap(handler)


def _patch_audits(monkeypatch):
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


def _route_event(*, result=BarcodeVerificationResult.BARCODE_VERIFIED):
    failed = result == BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    return SimpleNamespace(
        id=41,
        clinic_id=7,
        patient_id=None if failed else 5,
        admission_id=None if failed else 9,
        medication_administration_id=None if failed else 1,
        prescription_id=None if failed else 11,
        prescription_item_id=None if failed else 13,
        actor_staff_id=17,
        scanned_patient_id=None if failed else 5,
        scanned_drug_id=None if failed else 3,
        patient_barcode="UNKNOWN" if failed else "PAT-5",
        medication_barcode="MED-X" if failed else "MED-3",
        scan_timestamp=datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc),
        scan_device="rover-01",
        verification_result=result,
        failure_code=(
            BarcodeVerificationFailureCode.UNKNOWN_PATIENT_BARCODE
            if failed
            else None
        ),
        failure_reason=("Patient barcode not found" if failed else None),
        patient_match=not failed,
        medication_match=not failed,
        dose_match=not failed,
        route_match=not failed,
        time_window_match=not failed,
        expected_drug_id=None if failed else 3,
        expected_dose_value=None if failed else 500,
        expected_dose_unit=None if failed else "mg",
        expected_route_code=None if failed else "oral",
        scheduled_at=None,
        verification_window_start=None,
        verification_window_end=None,
        source_order_version=None if failed else 1,
        verification_policy_version=1,
        operation_identity="scan-key",
        created_at=None,
    )


def test_barcode_verify_schema_rejects_naive_timestamp_and_extra():
    with pytest.raises(PydanticValidationError):
        BarcodeMedicationVerificationRequestSchema.model_validate(
            {
                "patient_barcode": "PAT-1",
                "medication_barcode": "MED-1",
                "scan_timestamp": "2026-10-30T11:00:00",
                "scan_device": "rover-01",
            }
        )

    with pytest.raises(PydanticValidationError):
        BarcodeMedicationVerificationRequestSchema.model_validate(
            {
                "patient_barcode": "PAT-1",
                "medication_barcode": "MED-1",
                "scan_timestamp": "2026-10-30T11:00:00+00:00",
                "scan_device": "rover-01",
                "patient_id": 999,
            }
        )


def test_administer_schema_requires_barcode_verification_event_id():
    with pytest.raises(PydanticValidationError):
        MedicationAdministrationAdministerSchema.model_validate({})

    payload = MedicationAdministrationAdministerSchema.model_validate(
        {"barcode_verification_event_id": 42}
    )
    assert payload.barcode_verification_event_id == 42


def test_barcode_verify_route_is_registered_under_api_v1(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/api/v1/emar/barcode/verify" in rules


def test_barcode_verify_route_passes_authenticated_scope_and_idempotency(
    monkeypatch,
    app,
):
    captured = {}
    event = _route_event()

    monkeypatch.setattr(emar_routes, "_current_clinic_id", lambda: 7)
    monkeypatch.setattr(
        emar_routes,
        "_current_user",
        lambda: SimpleNamespace(id=23, staff=SimpleNamespace(id=17)),
    )

    def fake_verify(**kwargs):
        captured.update(kwargs)
        return event

    monkeypatch.setattr(
        emar_routes,
        "verify_barcode_medication_administration",
        fake_verify,
    )

    with app.test_request_context(
        "/api/v1/emar/barcode/verify",
        method="POST",
        json={
            "patient_barcode": "PAT-5",
            "medication_barcode": "MED-3",
            "scan_timestamp": "2026-10-30T11:00:00+00:00",
            "scan_device": "rover-01",
        },
        headers={"Idempotency-Key": " scan-key "},
    ):
        response, status = _raw(
            emar_routes.verify_barcode_medication_administration_route
        )()

    assert status == 200
    assert captured["clinic_id"] == 7
    assert captured["actor_staff_id"] == 17
    assert captured["operation_identity"] == "scan-key"
    assert response.get_json()["verification"]["verification_result"] == "barcode_verified"


def test_failed_unresolved_barcode_route_response_is_durable_outcome(
    monkeypatch,
    app,
):
    event = _route_event(
        result=BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    )

    monkeypatch.setattr(emar_routes, "_current_clinic_id", lambda: 7)
    monkeypatch.setattr(
        emar_routes,
        "_current_user",
        lambda: SimpleNamespace(id=23, staff=SimpleNamespace(id=17)),
    )
    monkeypatch.setattr(
        emar_routes,
        "verify_barcode_medication_administration",
        lambda **_kwargs: event,
    )

    with app.test_request_context(
        "/api/v1/emar/barcode/verify",
        method="POST",
        json={
            "patient_barcode": "UNKNOWN",
            "medication_barcode": "MED-X",
            "scan_timestamp": "2026-10-30T11:00:00+00:00",
            "scan_device": "rover-01",
        },
        headers={"Idempotency-Key": "failed-scan"},
    ):
        response, status = _raw(
            emar_routes.verify_barcode_medication_administration_route
        )()

    body = response.get_json()
    assert status == 200
    assert body["verification"]["verification_result"] == "not_barcode_verified"
    assert body["verification"]["failure_code"] == "unknown_patient_barcode"
    assert body["verification"]["medication_administration_id"] is None


def test_administer_route_passes_barcode_binding_to_safe_wrapper(
    monkeypatch,
    app,
):
    captured = {}
    record = SimpleNamespace(
        id=1,
        clinic_id=7,
        patient_id=5,
        admission_id=9,
        prescription_id=11,
        prescription_item_id=13,
        scheduled_at=None,
        dose_value=None,
        dose_unit=None,
        route_code=None,
        status=MedicationAdministrationStatus.ADMINISTERED,
        administered_at=None,
        administered_by_staff_id=17,
        held_at=None,
        held_by_staff_id=None,
        hold_reason=None,
        refused_at=None,
        refused_recorded_by_staff_id=None,
        refusal_reason=None,
        missed_at=None,
        missed_reason=None,
        cancelled_at=None,
        cancel_reason=None,
        source_order_version=1,
        idempotency_key=None,
        patient_barcode="PAT-5",
        medication_barcode="MED-3",
        scan_timestamp=None,
        scan_device="rover-01",
        verification_result=BarcodeVerificationResult.BARCODE_VERIFIED,
        barcode_verified_by_staff_id=17,
        barcode_verification_event_id=41,
        notes=None,
        created_at=None,
        updated_at=None,
    )

    monkeypatch.setattr(emar_routes, "_current_clinic_id", lambda: 7)
    monkeypatch.setattr(
        emar_routes,
        "_current_user",
        lambda: SimpleNamespace(id=23, staff=SimpleNamespace(id=17)),
    )

    def fake_administer(**kwargs):
        captured.update(kwargs)
        return record

    monkeypatch.setattr(
        emar_routes,
        "administer_barcode_verified_medication",
        fake_administer,
    )

    with app.test_request_context(
        "/api/v1/emar/1/administer",
        method="POST",
        json={"barcode_verification_event_id": 41},
        headers={"Idempotency-Key": "admin-key"},
    ):
        response, status = _raw(emar_routes.administer_medication_route)(
            administration_id=1
        )

    assert status == 200
    assert captured["barcode_verification_event_id"] == 41
    assert captured["idempotency_key"] == "admin-key"
    assert captured["idempotency_user_id"] == 23
    assert response.get_json()["administration"]["barcode_verification_event_id"] == 41


def test_verified_event_can_authorize_exact_administration(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
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
        operation_identity="bind-success",
    )

    result = emar_service.administer_barcode_verified_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        barcode_verification_event_id=event.id,
        administered_at=scheduled_at,
        idempotency_key="bind-admin",
        idempotency_user_id=actor.user_id,
    )

    assert result.status == MedicationAdministrationStatus.ADMINISTERED
    assert result.barcode_verification_event_id == event.id


def test_failed_verification_event_cannot_authorize_administration(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, _drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    failed = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode="WRONG-MEDICATION",
        scan_timestamp=scheduled_at,
        operation_identity="bind-failed",
    )

    with pytest.raises(ConflictError, match="successful barcode verification"):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=failed.id,
            administered_at=scheduled_at,
            idempotency_key="bind-failed-admin",
            idempotency_user_id=actor.user_id,
        )


def test_verification_for_different_administration_cannot_be_reused(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, admission, _prescription, item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
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
        operation_identity="bind-first",
    )

    other = emar_service.schedule_medication_administration(
        clinic_id=clinic.id,
        patient_id=patient.id,
        admission_id=admission.id,
        prescription_item_id=item.id,
        scheduled_at=scheduled_at + timedelta(hours=8),
        actor_staff_id=actor.id,
    )

    with pytest.raises(ConflictError, match="does not match"):
        emar_service.administer_barcode_verified_medication(
            administration_id=other.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=event.id,
            administered_at=scheduled_at + timedelta(hours=8),
            idempotency_key="bind-other-admin",
            idempotency_user_id=actor.user_id,
        )

    assert record.status == MedicationAdministrationStatus.SCHEDULED


def test_verification_actor_must_match_administration_actor(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )
    other_actor, _other_headers = make_authenticated_staff(
        clinic,
        role=actor.user.role,
    )

    event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="bind-actor",
    )

    with pytest.raises(ConflictError, match="actor does not match"):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=other_actor.id,
            barcode_verification_event_id=event.id,
            administered_at=scheduled_at,
            idempotency_key="bind-other-actor",
            idempotency_user_id=other_actor.user_id,
        )


def test_older_verified_event_is_invalidated_by_newer_failed_scan(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    verified = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="latest-good",
    )

    failed = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at + timedelta(hours=2),
        operation_identity="latest-failed",
    )

    assert failed.verification_result == BarcodeVerificationResult.NOT_BARCODE_VERIFIED
    assert failed.medication_administration_id == record.id

    with pytest.raises(ConflictError, match="latest verification attempt|stale"):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=verified.id,
            administered_at=scheduled_at,
            idempotency_key="latest-admin",
            idempotency_user_id=actor.user_id,
        )


def test_verified_event_expires_at_administration_window_end(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
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
        operation_identity="expired-bind",
    )

    with pytest.raises(ConflictError, match="permitted administration window"):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=event.id,
            administered_at=scheduled_at + timedelta(hours=2),
            idempotency_key="expired-admin",
            idempotency_user_id=actor.user_id,
        )


def test_barcode_event_id_is_in_admin_idempotency_fingerprint(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
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
        operation_identity="fingerprint-verify",
    )

    captured = {}
    ledger = SimpleNamespace(entity_type=None, entity_id=None)

    def reserve(**kwargs):
        captured.update(kwargs)
        return ledger, True

    monkeypatch.setattr(emar_service, "reserve_idempotency_operation", reserve)

    result = emar_service.administer_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        administered_at=scheduled_at,
        idempotency_key="fingerprint-admin",
        idempotency_user_id=actor.user_id,
        barcode_verification_event_id=event.id,
    )

    assert captured["request_payload"]["barcode_verification_event_id"] == event.id
    assert result.barcode_verification_event_id == event.id


def test_completed_admin_replay_happens_before_barcode_freshness_recheck(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
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
        operation_identity="replay-verify",
    )

    first = emar_service.administer_barcode_verified_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        barcode_verification_event_id=event.id,
        administered_at=scheduled_at,
        idempotency_key="replay-admin",
        idempotency_user_id=actor.user_id,
    )

    monkeypatch.setattr(
        emar_service,
        "_validate_barcode_verification_binding",
        lambda **_kwargs: pytest.fail(
            "Completed replay must not re-run barcode freshness checks"
        ),
    )

    replay = emar_service.administer_barcode_verified_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        barcode_verification_event_id=event.id,
        administered_at=scheduled_at,
        idempotency_key="replay-admin",
        idempotency_user_id=actor.user_id,
    )

    assert replay.id == first.id
    assert replay.barcode_verification_event_id == event.id


def test_same_admin_idempotency_key_with_different_verification_event_fails(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    make_ward,
    make_bed,
):
    _patch_audits(monkeypatch)
    actor, drug, _admission, _prescription, _item, record, scheduled_at = _context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    first_event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="change-event-1",
    )
    second_event = _verify(
        clinic=clinic,
        actor=actor,
        patient_barcode=patient.barcode,
        medication_barcode=drug.barcode,
        scan_timestamp=scheduled_at,
        operation_identity="change-event-2",
    )

    emar_service.administer_barcode_verified_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        barcode_verification_event_id=second_event.id,
        administered_at=scheduled_at,
        idempotency_key="same-admin-key",
        idempotency_user_id=actor.user_id,
    )

    with pytest.raises(ConflictError):
        emar_service.administer_barcode_verified_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            barcode_verification_event_id=first_event.id,
            administered_at=scheduled_at,
            idempotency_key="same-admin-key",
            idempotency_user_id=actor.user_id,
        )
