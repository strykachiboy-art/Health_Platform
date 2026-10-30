from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import inspect

import pytest

import app.modules.emar.routes.emar_routes as emar_routes
import app.modules.emar.services.emar_service as emar_service
from app.core.enums.emar_enums import (
    MedicationAdministrationStatus,
)
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)
from app.tests.modules.emar.test_emar_service_invariants import (
    _base_context,
    _schedule,
)


def test_administer_requests_row_lock(
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
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    original = emar_service._load_administration
    locks = []

    def capture_load(
        administration_id,
        *,
        clinic_id,
        lock=False,
    ):
        locks.append(lock)
        return original(
            administration_id,
            clinic_id=clinic_id,
            lock=lock,
        )

    monkeypatch.setattr(
        emar_service,
        "_load_administration",
        capture_load,
    )

    result = emar_service.administer_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
    )

    assert (
        result.status
        == MedicationAdministrationStatus.ADMINISTERED
    )
    assert True in locks


def test_idempotency_key_requires_user_id():
    with pytest.raises(
        ValidationError,
        match="Idempotency user ID is required",
    ):
        emar_service.administer_medication.__wrapped__(
            administration_id=1,
            clinic_id=1,
            actor_staff_id=1,
            idempotency_key="gate6-test",
        )


def test_new_idempotency_operation_binds_completed_entity(
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
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )
    captured = {}

    def reserve(**kwargs):
        captured.update(kwargs)
        return ledger, True

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        reserve,
    )

    result = emar_service.administer_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        idempotency_key="gate6-bind",
        idempotency_user_id=actor.user_id,
    )

    assert result.id == record.id
    assert ledger.entity_type == "MedicationAdministration"
    assert ledger.entity_id == record.id
    assert captured["operation"] == "emar.administer"


def test_generated_event_time_is_not_in_client_request_fingerprint(
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
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )
    captured = {}

    def reserve(**kwargs):
        captured.update(kwargs)
        return ledger, True

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        reserve,
    )

    result = emar_service.administer_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        administered_at=None,
        idempotency_key="gate6-time",
        idempotency_user_id=actor.user_id,
    )

    assert result.administered_at is not None
    assert (
        captured["request_payload"]["administered_at"]
        is None
    )


def test_completed_replay_returns_existing_without_second_audit(
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
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    completed = emar_service.administer_medication(
        administration_id=record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
    )

    ledger = SimpleNamespace(
        entity_type="MedicationAdministration",
        entity_id=completed.id,
    )

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (ledger, False),
    )
    monkeypatch.setattr(
        emar_service,
        "_audit_status_change",
        lambda **_kwargs: pytest.fail(
            "Replay must not create a second audit transition"
        ),
    )

    replay = emar_service.administer_medication(
        administration_id=completed.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        idempotency_key="gate6-replay",
        idempotency_user_id=actor.user_id,
    )

    assert replay.id == completed.id
    assert (
        replay.status
        == MedicationAdministrationStatus.ADMINISTERED
    )


def test_unresolved_replay_fails_closed(
    monkeypatch,
):
    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (ledger, False),
    )

    with pytest.raises(
        ConflictError,
        match="completed medication administration",
    ):
        emar_service.administer_medication.__wrapped__(
            administration_id=1,
            clinic_id=1,
            actor_staff_id=1,
            idempotency_key="gate6-unresolved",
            idempotency_user_id=1,
        )


def test_replay_for_other_administration_fails_closed(
    monkeypatch,
):
    ledger = SimpleNamespace(
        entity_type="MedicationAdministration",
        entity_id=2,
    )

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (ledger, False),
    )

    with pytest.raises(
        ConflictError,
        match="different medication administration",
    ):
        emar_service.administer_medication.__wrapped__(
            administration_id=1,
            clinic_id=1,
            actor_staff_id=1,
            idempotency_key="gate6-other",
            idempotency_user_id=1,
        )


def test_idempotency_actor_user_mismatch_fails_closed(
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
        _drug,
        admission,
        _prescription,
        item,
    ) = _base_context(
        db=db,
        clinic=clinic,
        patient=patient,
        make_authenticated_staff=make_authenticated_staff,
        make_drug=make_drug,
        make_ward=make_ward,
        make_bed=make_bed,
    )

    monkeypatch.setattr(
        emar_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )

    record = _schedule(
        clinic=clinic,
        patient=patient,
        admission=admission,
        item=item,
        actor=actor,
    )

    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )

    monkeypatch.setattr(
        emar_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (ledger, True),
    )

    with pytest.raises(
        ValidationError,
        match="does not match",
    ):
        emar_service.administer_medication(
            administration_id=record.id,
            clinic_id=clinic.id,
            actor_staff_id=actor.id,
            idempotency_key="gate6-user-mismatch",
            idempotency_user_id=actor.user_id + 100000,
        )


def test_administer_route_requires_idempotency_key(
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        emar_routes,
        "_current_clinic_id",
        lambda: 7,
    )
    monkeypatch.setattr(
        emar_routes,
        "_current_user",
        lambda: SimpleNamespace(
            id=23,
            staff=SimpleNamespace(id=17),
        ),
    )
    monkeypatch.setattr(
        emar_routes,
        "administer_barcode_verified_medication",
        lambda **_kwargs: pytest.fail(
            "Service must not run without Idempotency-Key"
        ),
    )

    handler = inspect.unwrap(
        emar_routes.administer_medication_route
    )

    with app.test_request_context(
        "/api/v1/emar/1/administer",
        method="POST",
        json={
            "barcode_verification_event_id": 41,
        },
    ):
        with pytest.raises(
            ValidationError,
            match="Idempotency-Key header is required",
        ):
            handler(
                administration_id=1
            )


def test_administer_route_passes_idempotency_scope(
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        emar_routes,
        "_current_clinic_id",
        lambda: 7,
    )
    monkeypatch.setattr(
        emar_routes,
        "_current_user",
        lambda: SimpleNamespace(
            id=23,
            staff=SimpleNamespace(id=17),
        ),
    )

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
        administered_at=datetime.now(timezone.utc),
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
        notes=None,
        created_at=None,
        updated_at=None,
    )

    def fake_administer(**kwargs):
        captured.update(kwargs)
        return record

    monkeypatch.setattr(
        emar_routes,
        "administer_barcode_verified_medication",
        fake_administer,
    )

    handler = inspect.unwrap(
        emar_routes.administer_medication_route
    )

    with app.test_request_context(
        "/api/v1/emar/1/administer",
        method="POST",
        json={
            "barcode_verification_event_id": 41,
        },
        headers={
            "Idempotency-Key": " gate6-route-key ",
        },
    ):
        response, status = handler(
            administration_id=1
        )

    assert status == 200
    assert captured["idempotency_key"] == "gate6-route-key"
    assert captured["idempotency_user_id"] == 23
    assert captured["barcode_verification_event_id"] == 41
    assert captured["actor_staff_id"] == 17
    assert (
        response.get_json()["administration"]["status"]
        == "administered"
    )
