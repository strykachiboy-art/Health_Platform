from __future__ import annotations

from datetime import timedelta
import inspect
from types import SimpleNamespace

import pytest

import app.modules.nursing.routes.nursing_route as nursing_routes
import app.modules.nursing.services.nursing_service as nursing_service

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)

from app.tests.modules.nursing.test_nursing_route_contract import (
    _handoff,
    _note,
    _now,
    _plan,
    _task,
)
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


KINDS = (
    "care_plan",
    "care_task",
    "note",
    "handoff",
)

OPERATIONS = {
    "care_plan": "nursing.care_plan.complete",
    "care_task": "nursing.care_task.complete",
    "note": "nursing.note.sign",
    "handoff": "nursing.handoff.acknowledge",
}

ENTITY_TYPES = {
    "care_plan": "NursingCarePlan",
    "care_task": "NursingCareTask",
    "note": "NursingNote",
    "handoff": "NursingHandoff",
}

ID_FIELDS = {
    "care_plan": "care_plan_id",
    "care_task": "task_id",
    "note": "note_id",
    "handoff": "handoff_id",
}

LOADERS = {
    "care_plan": "_get_care_plan",
    "care_task": "_get_care_task",
    "note": "_get_note",
    "handoff": "_get_handoff",
}

FINAL_STATUSES = {
    "care_plan": NursingCarePlanStatus.COMPLETED,
    "care_task": NursingCareTaskStatus.COMPLETED,
    "note": NursingNoteStatus.SIGNED,
    "handoff": NursingHandoffStatus.ACKNOWLEDGED,
}

TIMESTAMP_FIELDS = {
    "care_plan": "completed_at",
    "care_task": "completed_at",
    "note": "signed_at",
    "handoff": "acknowledged_at",
}

ROUTE_FUNCTIONS = {
    "care_plan": "complete_care_plan_route",
    "care_task": "complete_care_task_route",
    "note": "sign_nursing_note_route",
    "handoff": "acknowledge_handoff_route",
}

ROUTE_SERVICES = {
    "care_plan": "complete_care_plan",
    "care_task": "complete_care_task",
    "note": "sign_nursing_note",
    "handoff": "acknowledge_handoff",
}


def _silence_audit(monkeypatch):
    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **_kwargs: None,
    )


def _prepare_target(
    kind,
    *,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    _silence_audit(monkeypatch)

    (
        _patient,
        nurse,
        _doctor,
        _ward,
        _bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    if kind == "care_plan":
        target = nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            title="Concurrency plan",
            problem="Race protection",
            goal="Single completion",
        )
        actor = nurse

    elif kind == "care_task":
        plan = nursing_service.create_care_plan(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            title="Concurrency task plan",
            problem="Race protection",
            goal="Single completion",
        )

        target = nursing_service.create_care_task(
            care_plan_id=plan.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            description="Complete exactly once",
        )
        actor = nurse

    elif kind == "note":
        target = nursing_service.create_nursing_note(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            note_text="Sign exactly once.",
        )
        actor = nurse

    elif kind == "handoff":
        incoming = make_staff(
            clinic=clinic,
            role=Role.NURSE,
        )

        start = _db_now()

        target = nursing_service.create_handoff(
            admission_id=admission.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
            incoming_staff_id=incoming.id,
            shift_start_at=start,
            shift_end_at=(
                start
                + timedelta(hours=8)
            ),
            situation="Stable",
            assessment="No acute distress",
            recommendation="Continue care",
        )

        nursing_service.mark_handoff_ready(
            target.id,
            clinic_id=clinic.id,
            actor_staff_id=nurse.id,
        )

        actor = incoming

    else:
        raise AssertionError(kind)

    return target, actor


def _invoke(
    kind,
    *,
    target,
    clinic,
    actor,
    idempotency_key=None,
    idempotency_user_id=None,
):
    service = {
        "care_plan": nursing_service.complete_care_plan,
        "care_task": nursing_service.complete_care_task,
        "note": nursing_service.sign_nursing_note,
        "handoff": nursing_service.acknowledge_handoff,
    }[kind]

    return service(
        target.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        idempotency_key=idempotency_key,
        idempotency_user_id=idempotency_user_id,
    )


@pytest.mark.parametrize("kind", KINDS)
def test_idempotency_key_requires_user_id(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="Idempotency user ID is required",
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
            idempotency_key="gate8-required-user",
        )


@pytest.mark.parametrize("kind", KINDS)
def test_reservation_uses_expected_operation_and_payload(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
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
        nursing_service,
        "reserve_idempotency_operation",
        reserve,
    )

    _invoke(
        kind,
        target=target,
        clinic=clinic,
        actor=actor,
        idempotency_key="gate8-operation",
        idempotency_user_id=actor.user_id,
    )

    assert (
        captured["clinic_id"]
        == clinic.id
    )
    assert (
        captured["user_id"]
        == actor.user_id
    )
    assert (
        captured["operation"]
        == OPERATIONS[kind]
    )
    assert captured["request_payload"] == {
        ID_FIELDS[kind]: target.id,
        "actor_staff_id": actor.id,
    }


@pytest.mark.parametrize("kind", KINDS)
def test_new_reservation_binds_terminal_entity(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (
            ledger,
            True,
        ),
    )

    result = _invoke(
        kind,
        target=target,
        clinic=clinic,
        actor=actor,
        idempotency_key="gate8-bind",
        idempotency_user_id=actor.user_id,
    )

    assert (
        result.status
        == FINAL_STATUSES[kind]
    )
    assert (
        ledger.entity_type
        == ENTITY_TYPES[kind]
    )
    assert (
        ledger.entity_id
        == target.id
    )


@pytest.mark.parametrize("kind", KINDS)
def test_completed_replay_returns_original_without_second_transition(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    completed = _invoke(
        kind,
        target=target,
        clinic=clinic,
        actor=actor,
    )

    original_version = (
        completed.version
    )
    original_timestamp = getattr(
        completed,
        TIMESTAMP_FIELDS[kind],
    )

    ledger = SimpleNamespace(
        entity_type=ENTITY_TYPES[kind],
        entity_id=completed.id,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (
            ledger,
            False,
        ),
    )

    monkeypatch.setattr(
        nursing_service,
        "_audit",
        lambda **_kwargs: pytest.fail(
            "Replay must not create "
            "a second clinical transition"
        ),
    )

    replay = _invoke(
        kind,
        target=completed,
        clinic=clinic,
        actor=actor,
        idempotency_key="gate8-replay",
        idempotency_user_id=actor.user_id,
    )

    assert replay.id == completed.id
    assert (
        replay.status
        == FINAL_STATUSES[kind]
    )
    assert (
        replay.version
        == original_version
    )
    assert (
        getattr(
            replay,
            TIMESTAMP_FIELDS[kind],
        )
        == original_timestamp
    )


@pytest.mark.parametrize("kind", KINDS)
def test_unresolved_idempotency_record_fails_closed(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ledger = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (
            ledger,
            False,
        ),
    )

    with pytest.raises(
        ConflictError,
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
            idempotency_key="gate8-unresolved",
            idempotency_user_id=actor.user_id,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_replay_wrong_entity_type_fails_closed(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ledger = SimpleNamespace(
        entity_type="WrongEntity",
        entity_id=target.id,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (
            ledger,
            False,
        ),
    )

    with pytest.raises(
        ConflictError,
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
            idempotency_key="gate8-wrong-type",
            idempotency_user_id=actor.user_id,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_replay_different_entity_id_fails_closed(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ledger = SimpleNamespace(
        entity_type=ENTITY_TYPES[kind],
        entity_id=target.id + 100000,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: (
            ledger,
            False,
        ),
    )

    with pytest.raises(
        ConflictError,
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
            idempotency_key="gate8-wrong-id",
            idempotency_user_id=actor.user_id,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_idempotency_user_must_match_actor_user(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    monkeypatch.setattr(
        nursing_service,
        "reserve_idempotency_operation",
        lambda **_kwargs: pytest.fail(
            "Invalid actor/user binding "
            "must fail before reservation"
        ),
    )

    with pytest.raises(
        ValidationError,
        match="does not match",
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
            idempotency_key="gate8-user-mismatch",
            idempotency_user_id=(
                actor.user_id
                + 100000
            ),
        )


@pytest.mark.parametrize("kind", KINDS)
def test_terminal_transition_requests_row_lock(
    kind,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    target, actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    loader_name = LOADERS[kind]
    original = getattr(
        nursing_service,
        loader_name,
    )

    locks = []

    def capture(
        entity_id,
        clinic_id,
        *,
        lock=False,
    ):
        locks.append(lock)

        return original(
            entity_id,
            clinic_id,
            lock=lock,
        )

    monkeypatch.setattr(
        nursing_service,
        loader_name,
        capture,
    )

    result = _invoke(
        kind,
        target=target,
        clinic=clinic,
        actor=actor,
    )

    assert (
        result.status
        == FINAL_STATUSES[kind]
    )
    assert True in locks


def _route_result(kind):
    now = _now()

    if kind == "care_plan":
        result = _plan(
            clinic_id=7,
            staff_id=17,
        )
        result.status = (
            NursingCarePlanStatus.COMPLETED
        )
        result.completed_at = now
        result.version = 2
        return result

    if kind == "care_task":
        result = _task(
            clinic_id=7,
            staff_id=17,
        )
        result.status = (
            NursingCareTaskStatus.COMPLETED
        )
        result.completed_at = now
        result.completed_by_staff_id = 17
        result.version = 2
        return result

    if kind == "note":
        result = _note(
            clinic_id=7,
            staff_id=17,
        )
        result.status = (
            NursingNoteStatus.SIGNED
        )
        result.signed_at = now
        result.version = 2
        return result

    if kind == "handoff":
        result = _handoff(
            clinic_id=7,
            outgoing_staff_id=16,
            incoming_staff_id=17,
        )
        result.status = (
            NursingHandoffStatus.ACKNOWLEDGED
        )
        result.ready_at = now
        result.acknowledged_at = now
        result.acknowledged_by_staff_id = 17
        result.version = 3
        return result

    raise AssertionError(kind)


@pytest.mark.parametrize("kind", KINDS)
def test_retry_sensitive_routes_require_idempotency_key(
    kind,
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        nursing_routes,
        "_current_clinic_id",
        lambda: 7,
    )

    monkeypatch.setattr(
        nursing_routes,
        "_current_user",
        lambda: SimpleNamespace(
            id=23,
            staff=SimpleNamespace(
                id=17,
            ),
        ),
    )

    monkeypatch.setattr(
        nursing_routes,
        ROUTE_SERVICES[kind],
        lambda *args, **kwargs: pytest.fail(
            "Service must not run without "
            "Idempotency-Key"
        ),
    )

    handler = inspect.unwrap(
        getattr(
            nursing_routes,
            ROUTE_FUNCTIONS[kind],
        )
    )

    with app.test_request_context(
        "/api/v1/nursing/slice4",
        method="POST",
    ):
        with pytest.raises(
            ValidationError,
            match="Idempotency-Key header is required",
        ):
            handler(
                **{
                    ID_FIELDS[kind]: 1,
                }
            )


@pytest.mark.parametrize("kind", KINDS)
def test_retry_sensitive_routes_pass_normalized_idempotency_scope(
    kind,
    monkeypatch,
    app,
):
    monkeypatch.setattr(
        nursing_routes,
        "_current_clinic_id",
        lambda: 7,
    )

    monkeypatch.setattr(
        nursing_routes,
        "_current_user",
        lambda: SimpleNamespace(
            id=23,
            staff=SimpleNamespace(
                id=17,
            ),
        ),
    )

    captured = {}

    def fake_service(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return _route_result(kind)

    monkeypatch.setattr(
        nursing_routes,
        ROUTE_SERVICES[kind],
        fake_service,
    )

    handler = inspect.unwrap(
        getattr(
            nursing_routes,
            ROUTE_FUNCTIONS[kind],
        )
    )

    with app.test_request_context(
        "/api/v1/nursing/slice4",
        method="POST",
        headers={
            "Idempotency-Key": (
                " gate8-route-key "
            ),
        },
    ):
        response, status = handler(
            **{
                ID_FIELDS[kind]: 1,
            }
        )

    assert status == 200
    assert captured["args"] == (1,)
    assert captured["clinic_id"] == 7
    assert captured["actor_staff_id"] == 17
    assert (
        captured["idempotency_key"]
        == "gate8-route-key"
    )
    assert (
        captured["idempotency_user_id"]
        == 23
    )
    assert response.get_json() is not None
