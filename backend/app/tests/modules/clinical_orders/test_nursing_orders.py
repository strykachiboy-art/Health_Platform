from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderType,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_orders.services import nursing_order_service
from app.modules.nursing.models.nursing_model import NursingCareTask
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(*, monkeypatch, clinic, make_patient, make_staff):
    audits = []

    monkeypatch.setattr(
        nursing_order_service,
        "create_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    patient, nurse, doctor, ward, bed, admission = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    second_nurse = make_staff(
        clinic=clinic,
        role=Role.NURSE,
    )

    db.session.commit()

    return {
        "patient": patient,
        "nurse": nurse,
        "second_nurse": second_nurse,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "audits": audits,
    }


def _create(ctx, clinic, **overrides):
    payload = {
        "admission_id": ctx["admission"].id,
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["doctor"].id,
        "category": "monitoring",
        "order_text": "Record neuro observations",
        "frequency": "q4h",
        "instructions": "Escalate any acute change",
        "priority": "urgent",
        "scheduled_at": _db_now() + timedelta(minutes=30),
    }
    payload.update(overrides)

    return nursing_order_service.create_nursing_order(
        **payload
    )


def _care_task_count():
    return db.session.execute(
        select(
            func.count(
                NursingCareTask.id
            )
        )
    ).scalar_one()


def test_create_nursing_order_is_doctor_owned_and_audited(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    assert order.id is not None
    assert order.patient_id == ctx["patient"].id
    assert order.admission_id == ctx["admission"].id
    assert order.ordered_by_staff_id == ctx["doctor"].id
    assert order.category == "monitoring"
    assert order.frequency == "q4h"
    assert order.status == "active"
    assert order.version == 1

    assert len(ctx["audits"]) == 1
    assert ctx["audits"][0]["entity_type"] == "NursingOrder"


def test_nursing_order_does_not_create_nursing_care_task(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    before = _care_task_count()

    _create(ctx, clinic)

    after = _care_task_count()

    assert after == before


def test_nursing_order_normalizes_to_cpoe_contract(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        category="wound_care",
        frequency="daily",
    )

    summary = (
        nursing_order_service
        .to_clinical_order_summary(
            order
        )
    )

    assert (
        summary["order_type"]
        == ClinicalOrderType.NURSING
    )
    assert (
        summary["source_entity_type"]
        == "NursingOrder"
    )
    assert (
        summary["source_entity_id"]
        == order.id
    )
    assert (
        summary["details"]["category"]
        == "wound_care"
    )
    assert (
        summary["details"]["frequency"]
        == "daily"
    )


def test_nurse_cannot_prescribe_nursing_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="doctor role",
    ):
        _create(
            ctx,
            clinic,
            actor_staff_id=ctx["nurse"].id,
        )


def test_discharged_admission_cannot_receive_nursing_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ctx["admission"].status = (
        AdmissionStatus.DISCHARGED
    )
    db.session.commit()

    with pytest.raises(
        ConflictError,
        match="active admission",
    ):
        _create(ctx, clinic)


def test_custom_frequency_requires_description(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="custom_frequency is required",
    ):
        _create(
            ctx,
            clinic,
            frequency="custom",
            custom_frequency=None,
        )


def test_assigned_nurse_can_execute_but_other_nurse_cannot(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        assigned_to_staff_id=ctx["nurse"].id,
    )

    with pytest.raises(
        ConflictError,
        match="different nurse",
    ):
        nursing_order_service.record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["second_nurse"].id,
            result_note="Should not be accepted",
        )

    execution = (
        nursing_order_service
        .record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["nurse"].id,
            result_note="Neuro observations stable",
        )
    )

    assert (
        execution.performed_by_staff_id
        == ctx["nurse"].id
    )


def test_doctor_cannot_record_nurse_execution(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    with pytest.raises(
        ValidationError,
        match="nurse role",
    ):
        nursing_order_service.record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            result_note="Wrong actor",
        )


def test_one_time_order_closes_on_nurse_execution(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        frequency="once",
    )

    execution = (
        nursing_order_service
        .record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["nurse"].id,
            result_note="Single intervention completed",
            performed_at=_db_now(),
        )
    )

    db.session.refresh(order)

    assert execution.id is not None
    assert (
        order.status
        == ClinicalOrderLifecycleStatus.COMPLETED.value
    )
    assert (
        order.completed_by_staff_id
        == ctx["nurse"].id
    )
    assert order.completed_at is not None
    assert order.version == 2


def test_recurring_order_keeps_execution_history_and_stays_open(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        frequency="q4h",
    )

    first = (
        nursing_order_service
        .record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["nurse"].id,
            result_note="First observation set stable",
        )
    )
    second = (
        nursing_order_service
        .record_nursing_order_execution(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["second_nurse"].id,
            result_note="Second observation set stable",
        )
    )

    db.session.refresh(order)

    assert (
        order.status
        == ClinicalOrderLifecycleStatus.IN_PROGRESS.value
    )
    assert order.completed_at is None

    executions = (
        nursing_order_service
        .list_nursing_order_executions(
            order.id,
            clinic_id=clinic.id,
        )
    )

    assert [
        row.id
        for row in executions
    ] == [
        first.id,
        second.id,
    ]


def test_doctor_can_complete_recurring_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        frequency="continuous",
    )

    nursing_order_service.record_nursing_order_execution(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["nurse"].id,
        result_note="Order in progress",
    )

    completed = nursing_order_service.complete_nursing_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        completion_note="Monitoring no longer required",
    )

    assert (
        completed.status
        == ClinicalOrderLifecycleStatus.COMPLETED.value
    )
    assert (
        completed.completed_by_staff_id
        == ctx["doctor"].id
    )
    assert (
        completed.completion_note
        == "Monitoring no longer required"
    )


def test_doctor_cannot_complete_unperformed_one_time_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(
        ctx,
        clinic,
        frequency="once",
    )

    with pytest.raises(
        ConflictError,
        match="through nurse execution",
    ):
        nursing_order_service.complete_nursing_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            completion_note="Should not bypass execution",
        )


def test_doctor_can_cancel_active_nursing_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    cancelled = (
        nursing_order_service
        .cancel_nursing_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            reason="Clinical plan changed",
        )
    )

    assert (
        cancelled.status
        == ClinicalOrderLifecycleStatus.CANCELLED.value
    )
    assert (
        cancelled.cancelled_by_staff_id
        == ctx["doctor"].id
    )
    assert cancelled.cancelled_at is not None


def test_nursing_order_tenant_isolation(
    monkeypatch,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    other = make_clinic(
        name="Gate 10 Nursing Order Foreign Clinic"
    )

    with pytest.raises(NotFoundError):
        nursing_order_service.get_nursing_order(
            order.id,
            clinic_id=other.id,
        )


def test_route_doctor_creates_nurse_executes_and_views_history(
    monkeypatch,
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    create_response = client.post(
        "/api/v1/clinical-orders/nursing-orders",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "category": "positioning",
            "order_text": "Reposition every 2 hours",
            "frequency": "q2h",
            "instructions": "Offload pressure points",
            "priority": "routine",
        },
    )

    assert create_response.status_code == 201

    order_id = (
        create_response
        .get_json()["order"]["id"]
    )

    execute_response = client.post(
        (
            "/api/v1/clinical-orders/nursing-orders/"
            f"{order_id}/execute"
        ),
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "result_note": "Patient repositioned",
        },
    )

    assert execute_response.status_code == 200

    history_response = client.get(
        (
            "/api/v1/clinical-orders/nursing-orders/"
            f"{order_id}/executions"
        ),
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )

    assert history_response.status_code == 200
    assert history_response.get_json()["total"] == 1


def test_nurse_route_cannot_prescribe_nursing_order(
    monkeypatch,
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    response = client.post(
        "/api/v1/clinical-orders/nursing-orders",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "category": "monitoring",
            "order_text": "Unauthorized nursing order",
            "frequency": "q4h",
        },
    )

    assert response.status_code == 403


def test_doctor_route_cannot_record_nurse_execution(
    monkeypatch,
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    response = client.post(
        (
            "/api/v1/clinical-orders/nursing-orders/"
            f"{order.id}/execute"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "result_note": "Wrong role",
        },
    )

    assert response.status_code == 403
