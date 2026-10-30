from __future__ import annotations

import pytest

import app.modules.nursing.routes.nursing_route as nursing_routes
import app.modules.nursing.services.nursing_service as nursing_service

from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.nursing.models.nursing_model import (
    NursingCarePlan,
    NursingCareTask,
    NursingHandoff,
    NursingNote,
)
from app.modules.ward.models.ward_model import Admission

from app.tests.modules.nursing.test_nursing_idempotency_contract import (
    ENTITY_TYPES,
    FINAL_STATUSES,
    KINDS,
    TIMESTAMP_FIELDS,
    _invoke,
    _prepare_target,
)
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
)


MODELS = {
    "care_plan": NursingCarePlan,
    "care_task": NursingCareTask,
    "note": NursingNote,
    "handoff": NursingHandoff,
}

ROUTE_SERVICES = {
    "care_plan": "complete_care_plan",
    "care_task": "complete_care_task",
    "note": "sign_nursing_note",
    "handoff": "acknowledge_handoff",
}

ROUTE_URLS = {
    "care_plan": (
        "/api/v1/nursing/care-plans/101/complete"
    ),
    "care_task": (
        "/api/v1/nursing/tasks/201/complete"
    ),
    "note": (
        "/api/v1/nursing/notes/301/sign"
    ),
    "handoff": (
        "/api/v1/nursing/handoffs/401/acknowledge"
    ),
}


@pytest.mark.parametrize("kind", KINDS)
def test_terminal_transition_is_tenant_scoped(
    kind,
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    target, _actor = _prepare_target(
        kind,
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    other_clinic = make_clinic(
        name=(
            "Gate 8 Slice 5 Other "
            f"{kind}"
        ),
    )

    other_nurse = make_staff(
        clinic=other_clinic,
        role=Role.NURSE,
    )

    with pytest.raises(
        NotFoundError,
    ):
        _invoke(
            kind,
            target=target,
            clinic=other_clinic,
            actor=other_nurse,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_terminal_transition_rejects_suspended_nurse(
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

    actor.status = StaffStatus.SUSPENDED
    db.session.commit()

    with pytest.raises(
        ValidationError,
        match="Nursing actor must be active",
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_terminal_transition_rejects_discharged_admission(
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

    admission = db.session.get(
        Admission,
        target.admission_id,
    )

    assert admission is not None

    admission.status = (
        AdmissionStatus.DISCHARGED
    )
    admission.discharged_at = _db_now()
    db.session.commit()

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
        )


@pytest.mark.parametrize("kind", KINDS)
def test_terminal_transition_audit_has_exact_actor_clinic_entity(
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

    audits = []

    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
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

    assert len(audits) == 1

    audit = audits[0]

    assert (
        audit["action"]
        == AuditAction.STATUS_CHANGE
    )
    assert (
        audit["entity_type"]
        == ENTITY_TYPES[kind]
    )
    assert (
        audit["entity_id"]
        == target.id
    )
    assert (
        audit["clinic_id"]
        == clinic.id
    )
    assert (
        audit["user_id"]
        == actor.user_id
    )
    assert (
        audit["new_value"]["status"]
        == FINAL_STATUSES[kind].value
    )


@pytest.mark.parametrize("kind", KINDS)
def test_audit_failure_rolls_back_terminal_transition(
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

    original_status = target.status
    original_version = target.version
    original_timestamp = getattr(
        target,
        TIMESTAMP_FIELDS[kind],
    )

    def fail_audit(**_kwargs):
        raise RuntimeError(
            "gate8-slice5-audit-failure"
        )

    monkeypatch.setattr(
        nursing_service,
        "create_audit_log",
        fail_audit,
    )

    with pytest.raises(
        RuntimeError,
        match="gate8-slice5-audit-failure",
    ):
        _invoke(
            kind,
            target=target,
            clinic=clinic,
            actor=actor,
        )

    db.session.expire_all()

    current = db.session.get(
        MODELS[kind],
        target.id,
    )

    assert current is not None
    assert current.status == original_status
    assert current.version == original_version
    assert (
        getattr(
            current,
            TIMESTAMP_FIELDS[kind],
        )
        == original_timestamp
    )


@pytest.mark.parametrize("kind", KINDS)
def test_doctor_cannot_execute_terminal_nursing_route(
    kind,
    client,
    clinic,
    make_staff,
    auth_headers_for,
    monkeypatch,
):
    doctor = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    headers = {
        **auth_headers_for(
            doctor.user,
            role=Role.DOCTOR,
        ),
        "Idempotency-Key": (
            f"gate8-slice5-doctor-{kind}"
        ),
    }

    monkeypatch.setattr(
        nursing_routes,
        ROUTE_SERVICES[kind],
        lambda *args, **kwargs: pytest.fail(
            "RBAC must reject doctor before "
            "the Nursing service executes"
        ),
    )

    response = client.post(
        ROUTE_URLS[kind],
        headers=headers,
    )

    assert response.status_code == 403
