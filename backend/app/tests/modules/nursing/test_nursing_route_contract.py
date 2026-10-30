from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.enums.nursing_enums import (
    NursingCarePlanStatus,
    NursingCareTaskPriority,
    NursingCareTaskStatus,
    NursingHandoffStatus,
    NursingNoteStatus,
    NursingNoteType,
)
from app.core.enums.role_enums import Role


@pytest.fixture()
def nursing_routes():
    import app.modules.nursing.routes.nursing_route as routes
    return routes


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _nurse_headers(clinic, make_staff, auth_headers_for):
    nurse = make_staff(clinic=clinic, role=Role.NURSE)
    return nurse, auth_headers_for(nurse.user, role=Role.NURSE)


def _plan(clinic_id=1, admission_id=10, patient_id=20, staff_id=30):
    now = _now()
    return SimpleNamespace(
        id=101,
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        created_by_staff_id=staff_id,
        title="Fall prevention",
        problem="Fall risk",
        goal="Remain fall free",
        status=NursingCarePlanStatus.ACTIVE,
        started_at=now,
        target_at=None,
        completed_at=None,
        cancelled_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _task(clinic_id=1, admission_id=10, patient_id=20, staff_id=30):
    now = _now()
    return SimpleNamespace(
        id=201,
        care_plan_id=101,
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        created_by_staff_id=staff_id,
        assigned_to_staff_id=staff_id,
        completed_by_staff_id=None,
        description="Reposition patient",
        instructions=None,
        priority=NursingCareTaskPriority.HIGH,
        status=NursingCareTaskStatus.PENDING,
        due_at=None,
        started_at=None,
        completed_at=None,
        cancelled_at=None,
        cancellation_reason=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _note(clinic_id=1, admission_id=10, patient_id=20, staff_id=30):
    now = _now()
    return SimpleNamespace(
        id=301,
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        author_staff_id=staff_id,
        note_type=NursingNoteType.PROGRESS,
        status=NursingNoteStatus.DRAFT,
        note_text="Patient resting.",
        observed_at=now,
        signed_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _handoff(
    clinic_id=1,
    admission_id=10,
    patient_id=20,
    outgoing_staff_id=30,
    incoming_staff_id=31,
):
    now = _now()
    return SimpleNamespace(
        id=401,
        clinic_id=clinic_id,
        patient_id=patient_id,
        admission_id=admission_id,
        outgoing_staff_id=outgoing_staff_id,
        incoming_staff_id=incoming_staff_id,
        acknowledged_by_staff_id=None,
        status=NursingHandoffStatus.DRAFT,
        shift_start_at=now,
        shift_end_at=now + timedelta(hours=8),
        situation="Stable",
        background="Admitted",
        assessment="No distress",
        recommendation="Continue plan",
        safety_concerns=None,
        pending_actions=None,
        ready_at=None,
        acknowledged_at=None,
        cancelled_at=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/nursing/admissions/1/care-plans"),
        ("GET", "/api/v1/nursing/admissions/1/care-plans"),
        ("GET", "/api/v1/nursing/care-plans/1"),
        ("POST", "/api/v1/nursing/care-plans/1/hold"),
        ("POST", "/api/v1/nursing/care-plans/1/resume"),
        ("POST", "/api/v1/nursing/care-plans/1/complete"),
        ("POST", "/api/v1/nursing/care-plans/1/cancel"),
        ("POST", "/api/v1/nursing/care-plans/1/tasks"),
        ("GET", "/api/v1/nursing/admissions/1/tasks"),
        ("GET", "/api/v1/nursing/tasks/1"),
        ("POST", "/api/v1/nursing/tasks/1/start"),
        ("POST", "/api/v1/nursing/tasks/1/complete"),
        ("POST", "/api/v1/nursing/tasks/1/cancel"),
        ("POST", "/api/v1/nursing/admissions/1/notes"),
        ("GET", "/api/v1/nursing/admissions/1/notes"),
        ("GET", "/api/v1/nursing/notes/1"),
        ("PATCH", "/api/v1/nursing/notes/1"),
        ("POST", "/api/v1/nursing/notes/1/sign"),
        ("POST", "/api/v1/nursing/admissions/1/handoffs"),
        ("GET", "/api/v1/nursing/admissions/1/handoffs"),
        ("GET", "/api/v1/nursing/handoffs/1"),
        ("PATCH", "/api/v1/nursing/handoffs/1"),
        ("POST", "/api/v1/nursing/handoffs/1/ready"),
        ("POST", "/api/v1/nursing/handoffs/1/acknowledge"),
        ("POST", "/api/v1/nursing/handoffs/1/cancel"),
    ],
)
def test_all_nursing_routes_require_authentication(client, method, path):
    response = client.open(path, method=method)
    assert response.status_code in (401, 422)


def test_nursing_blueprint_is_registered(app):
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert (
        "/api/v1/nursing/admissions/<int:admission_id>/care-plans"
        in rules
    )
    assert (
        "/api/v1/nursing/handoffs/<int:handoff_id>/acknowledge"
        in rules
    )


def test_doctor_can_read_but_cannot_write_nursing_api(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    doctor = make_staff(clinic=clinic, role=Role.DOCTOR)
    headers = auth_headers_for(doctor.user, role=Role.DOCTOR)

    monkeypatch.setattr(
        nursing_routes,
        "list_care_plans",
        lambda **_kwargs: [],
    )

    read = client.get(
        "/api/v1/nursing/admissions/1/care-plans",
        headers=headers,
    )
    write = client.post(
        "/api/v1/nursing/admissions/1/care-plans",
        headers=headers,
        json={"title": "X", "problem": "Y", "goal": "Z"},
    )

    assert read.status_code == 200
    assert write.status_code in (401, 403)


def test_create_care_plan_maps_only_authoritative_context(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _plan(
            clinic_id=clinic.id,
            admission_id=77,
            patient_id=88,
            staff_id=nurse.id,
        )

    monkeypatch.setattr(nursing_routes, "create_care_plan", fake_create)

    response = client.post(
        "/api/v1/nursing/admissions/77/care-plans",
        headers=headers,
        json={
            "title": "Fall prevention",
            "problem": "Fall risk",
            "goal": "Remain safe",
        },
    )

    assert response.status_code == 201
    assert captured["admission_id"] == 77
    assert captured["clinic_id"] == clinic.id
    assert captured["actor_staff_id"] == nurse.id
    assert "patient_id" not in captured
    assert response.get_json()["care_plan"]["patient_id"] == 88


def test_care_plan_request_rejects_client_patient_override(
    client,
    clinic,
    make_staff,
    auth_headers_for,
):
    _nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    response = client.post(
        "/api/v1/nursing/admissions/1/care-plans",
        headers=headers,
        json={
            "title": "Plan",
            "problem": "Problem",
            "goal": "Goal",
            "patient_id": 999,
        },
    )
    assert response.status_code == 422


def test_create_task_maps_assignee_priority_and_due_at(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    captured = {}
    due = _now() + timedelta(hours=2)

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _task(clinic_id=clinic.id, staff_id=nurse.id)

    monkeypatch.setattr(nursing_routes, "create_care_task", fake_create)

    response = client.post(
        "/api/v1/nursing/care-plans/101/tasks",
        headers=headers,
        json={
            "description": "Reposition patient",
            "priority": "high",
            "assigned_to_staff_id": nurse.id,
            "due_at": due.isoformat(),
        },
    )

    assert response.status_code == 201
    assert captured["care_plan_id"] == 101
    assert captured["priority"] == NursingCareTaskPriority.HIGH
    assert captured["assigned_to_staff_id"] == nurse.id


def test_cancel_task_requires_reason_and_maps_it(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)

    missing = client.post(
        "/api/v1/nursing/tasks/201/cancel",
        headers=headers,
        json={},
    )
    assert missing.status_code == 422

    captured = {}

    def fake_cancel(*args, **kwargs):
        captured["task_id"] = args[0]
        captured.update(kwargs)
        task = _task(clinic_id=clinic.id, staff_id=nurse.id)
        task.status = NursingCareTaskStatus.CANCELLED
        task.cancellation_reason = kwargs["reason"]
        task.cancelled_at = _now()
        return task

    monkeypatch.setattr(nursing_routes, "cancel_care_task", fake_cancel)

    response = client.post(
        "/api/v1/nursing/tasks/201/cancel",
        headers=headers,
        json={"reason": "No longer indicated"},
    )

    assert response.status_code == 200
    assert captured["task_id"] == 201
    assert captured["reason"] == "No longer indicated"


def test_note_create_update_and_sign_routes_map_service_calls(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    calls = []

    def fake_create(**kwargs):
        calls.append(("create", kwargs))
        return _note(
            clinic_id=clinic.id,
            admission_id=55,
            staff_id=nurse.id,
        )

    def fake_update(note_id, **kwargs):
        calls.append(("update", note_id, kwargs))
        return _note(
            clinic_id=clinic.id,
            admission_id=55,
            staff_id=nurse.id,
        )

    def fake_sign(note_id, **kwargs):
        calls.append(("sign", note_id, kwargs))
        note = _note(
            clinic_id=clinic.id,
            admission_id=55,
            staff_id=nurse.id,
        )
        note.status = NursingNoteStatus.SIGNED
        note.signed_at = _now()
        return note

    monkeypatch.setattr(nursing_routes, "create_nursing_note", fake_create)
    monkeypatch.setattr(
        nursing_routes,
        "update_nursing_note_draft",
        fake_update,
    )
    monkeypatch.setattr(nursing_routes, "sign_nursing_note", fake_sign)

    created = client.post(
        "/api/v1/nursing/admissions/55/notes",
        headers=headers,
        json={"note_text": "Initial note", "note_type": "progress"},
    )
    updated = client.patch(
        "/api/v1/nursing/notes/301",
        headers=headers,
        json={"note_text": "Updated note"},
    )
    signed = client.post(
        "/api/v1/nursing/notes/301/sign",
        headers={
            **headers,
            "Idempotency-Key": "gate8-note-sign-contract",
        },
    )

    assert created.status_code == 201
    assert updated.status_code == 200
    assert signed.status_code == 200
    assert calls[0][1]["admission_id"] == 55
    assert calls[0][1]["actor_staff_id"] == nurse.id
    assert calls[1][1] == 301
    assert calls[2][1] == 301


def test_handoff_create_ready_acknowledge_contract(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    incoming = make_staff(clinic=clinic, role=Role.NURSE)
    start = _now()
    captured = []

    def fake_create(**kwargs):
        captured.append(("create", kwargs))
        return _handoff(
            clinic_id=clinic.id,
            admission_id=66,
            outgoing_staff_id=nurse.id,
            incoming_staff_id=incoming.id,
        )

    def fake_ready(handoff_id, **kwargs):
        captured.append(("ready", handoff_id, kwargs))
        item = _handoff(
            clinic_id=clinic.id,
            admission_id=66,
            outgoing_staff_id=nurse.id,
            incoming_staff_id=incoming.id,
        )
        item.status = NursingHandoffStatus.READY
        item.ready_at = _now()
        return item

    def fake_ack(handoff_id, **kwargs):
        captured.append(("ack", handoff_id, kwargs))
        item = _handoff(
            clinic_id=clinic.id,
            admission_id=66,
            outgoing_staff_id=nurse.id,
            incoming_staff_id=incoming.id,
        )
        item.status = NursingHandoffStatus.ACKNOWLEDGED
        item.acknowledged_by_staff_id = nurse.id
        item.acknowledged_at = _now()
        return item

    monkeypatch.setattr(nursing_routes, "create_handoff", fake_create)
    monkeypatch.setattr(nursing_routes, "mark_handoff_ready", fake_ready)
    monkeypatch.setattr(nursing_routes, "acknowledge_handoff", fake_ack)

    created = client.post(
        "/api/v1/nursing/admissions/66/handoffs",
        headers=headers,
        json={
            "shift_start_at": start.isoformat(),
            "shift_end_at": (start + timedelta(hours=8)).isoformat(),
            "incoming_staff_id": incoming.id,
            "situation": "Stable",
            "assessment": "No distress",
            "recommendation": "Continue care",
        },
    )
    ready = client.post(
        "/api/v1/nursing/handoffs/401/ready",
        headers=headers,
    )
    ack = client.post(
        "/api/v1/nursing/handoffs/401/acknowledge",
        headers={
            **headers,
            "Idempotency-Key": "gate8-handoff-ack-contract",
        },
    )

    assert created.status_code == 201
    assert ready.status_code == 200
    assert ack.status_code == 200
    assert captured[0][1]["actor_staff_id"] == nurse.id
    assert captured[1][1] == 401
    assert captured[2][1] == 401


def test_list_routes_serialize_total_and_items(
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
    nursing_routes,
):
    _nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)

    monkeypatch.setattr(
        nursing_routes,
        "list_care_plans",
        lambda **_kwargs: [_plan(clinic_id=clinic.id)],
    )
    monkeypatch.setattr(
        nursing_routes,
        "list_care_tasks",
        lambda **_kwargs: [_task(clinic_id=clinic.id)],
    )
    monkeypatch.setattr(
        nursing_routes,
        "list_nursing_notes",
        lambda **_kwargs: [_note(clinic_id=clinic.id)],
    )
    monkeypatch.setattr(
        nursing_routes,
        "list_handoffs",
        lambda **_kwargs: [_handoff(clinic_id=clinic.id)],
    )

    for path in (
        "/api/v1/nursing/admissions/10/care-plans",
        "/api/v1/nursing/admissions/10/tasks",
        "/api/v1/nursing/admissions/10/notes",
        "/api/v1/nursing/admissions/10/handoffs",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 200
        payload = response.get_json()
        assert payload["total"] == 1
        assert len(payload["items"]) == 1


def test_schema_forbids_unknown_handoff_fields(
    client,
    clinic,
    make_staff,
    auth_headers_for,
):
    _nurse, headers = _nurse_headers(clinic, make_staff, auth_headers_for)
    start = _now()

    response = client.post(
        "/api/v1/nursing/admissions/1/handoffs",
        headers=headers,
        json={
            "shift_start_at": start.isoformat(),
            "shift_end_at": (start + timedelta(hours=8)).isoformat(),
            "patient_id": 999,
        },
    )

    assert response.status_code == 422
