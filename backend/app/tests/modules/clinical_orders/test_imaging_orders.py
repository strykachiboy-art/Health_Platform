
from __future__ import annotations

from datetime import timedelta

import pytest

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
from app.modules.clinical_orders.services import imaging_order_service

from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(
    *,
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    audits = []

    monkeypatch.setattr(
        imaging_order_service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    (
        patient,
        nurse,
        doctor,
        ward,
        bed,
        admission,
    ) = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "audits": audits,
    }


def _create(
    ctx,
    clinic,
    **overrides,
):
    payload = {
        "admission_id": (
            ctx["admission"].id
        ),
        "clinic_id": clinic.id,
        "actor_staff_id": (
            ctx["doctor"].id
        ),
        "study_code": "IMG-CHEST-2V",
        "study_name": "Chest radiograph 2 views",
        "modality": "xray",
        "body_site": "chest",
        "indication": "Evaluate dyspnea",
        "priority": "urgent",
        "scheduled_at": (
            _db_now()
            + timedelta(hours=1)
        ),
    }

    payload.update(
        overrides
    )

    return (
        imaging_order_service
        .create_imaging_order(
            **payload
        )
    )


def test_create_imaging_order_is_authoritative_and_audited(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
    )

    assert order.id is not None
    assert order.clinic_id == clinic.id
    assert order.patient_id == ctx["patient"].id
    assert order.admission_id == ctx["admission"].id
    assert order.ordered_by_staff_id == ctx["doctor"].id
    assert order.study_name == "Chest radiograph 2 views"
    assert order.modality == "xray"
    assert order.priority == "urgent"
    assert order.status == "active"
    assert order.version == 1

    assert len(ctx["audits"]) == 1
    assert (
        ctx["audits"][0]["entity_type"]
        == "ImagingOrder"
    )
    assert (
        ctx["audits"][0]["user_id"]
        == ctx["doctor"].user_id
    )


def test_imaging_order_normalizes_to_cpoe_contract(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
        contrast_required=True,
        contrast_type="iodinated",
        modality="ct",
    )

    summary = (
        imaging_order_service
        .to_clinical_order_summary(
            order
        )
    )

    assert (
        summary["order_type"]
        == ClinicalOrderType.IMAGING
    )
    assert (
        summary["source_entity_type"]
        == "ImagingOrder"
    )
    assert (
        summary["source_entity_id"]
        == order.id
    )
    assert (
        summary["details"]["modality"]
        == "ct"
    )
    assert (
        summary["details"]["contrast_required"]
        is True
    )


def test_nurse_cannot_create_imaging_order(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="requires doctor role",
    ):
        _create(
            ctx,
            clinic,
            actor_staff_id=ctx["nurse"].id,
        )


def test_discharged_admission_cannot_receive_imaging_order(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
        _create(
            ctx,
            clinic,
        )


def test_contrast_type_requires_contrast_flag(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="contrast_type requires",
    ):
        _create(
            ctx,
            clinic,
            contrast_required=False,
            contrast_type="gadolinium",
        )


def test_invalid_modality_is_rejected(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid imaging modality",
    ):
        _create(
            ctx,
            clinic,
            modality="telepathy",
        )


def test_cancel_imaging_order_records_reason_actor_and_audit(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
    )

    cancelled = (
        imaging_order_service
        .cancel_imaging_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            reason="Clinical indication resolved",
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
    assert (
        cancelled.cancellation_reason
        == "Clinical indication resolved"
    )
    assert cancelled.cancelled_at is not None
    assert cancelled.version == 2
    assert len(ctx["audits"]) == 2


def test_complete_imaging_order_requires_report_and_records_result(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
        modality="ct",
    )

    completed = (
        imaging_order_service
        .complete_imaging_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            report_text="No acute pulmonary embolus.",
            result_summary="No PE",
            performed_at=_db_now(),
        )
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
        completed.report_text
        == "No acute pulmonary embolus."
    )
    assert completed.result_summary == "No PE"
    assert completed.performed_at is not None
    assert completed.completed_at is not None
    assert completed.version == 2


def test_cancelled_order_cannot_be_completed(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
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
    )

    imaging_order_service.cancel_imaging_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="No longer indicated",
    )

    with pytest.raises(
        ConflictError,
        match="active imaging orders",
    ):
        imaging_order_service.complete_imaging_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            report_text="Should not complete",
        )


def test_list_imaging_orders_is_admission_scoped(
    monkeypatch,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    first = _create(
        ctx,
        clinic,
        study_name="First study",
    )
    second = _create(
        ctx,
        clinic,
        study_name="Second study",
    )

    rows = (
        imaging_order_service
        .list_imaging_orders(
            admission_id=ctx["admission"].id,
            clinic_id=clinic.id,
        )
    )

    assert {
        row.id
        for row in rows
    } == {
        first.id,
        second.id,
    }


def test_imaging_order_tenant_isolation(
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

    order = _create(
        ctx,
        clinic,
    )

    other = make_clinic(
        name="Gate 10 Imaging Foreign Clinic",
    )

    with pytest.raises(
        NotFoundError,
    ):
        imaging_order_service.get_imaging_order(
            order.id,
            clinic_id=other.id,
        )


def test_doctor_route_can_create_and_nurse_can_view(
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
        "/api/v1/clinical-orders/imaging",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "study_name": "Portable chest x-ray",
            "modality": "xray",
            "body_site": "chest",
            "indication": "New oxygen requirement",
            "priority": "stat",
        },
    )

    assert create_response.status_code == 201

    order_id = (
        create_response
        .get_json()["order"]["id"]
    )

    get_response = client.get(
        (
            "/api/v1/clinical-orders/imaging/"
            f"{order_id}"
        ),
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )

    assert get_response.status_code == 200
    assert (
        get_response.get_json()["id"]
        == order_id
    )


def test_nurse_route_cannot_create_imaging_order(
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
        "/api/v1/clinical-orders/imaging",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "study_name": "Unauthorized study",
            "modality": "xray",
            "body_site": "chest",
            "indication": "Should be rejected",
        },
    )

    assert response.status_code == 403


def test_route_complete_and_cancel_are_terminal(
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

    order = _create(
        ctx,
        clinic,
    )

    response = client.post(
        (
            "/api/v1/clinical-orders/imaging/"
            f"{order.id}/complete"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "report_text": "Normal chest radiograph.",
            "result_summary": "Normal",
        },
    )

    assert response.status_code == 200
    assert (
        response.get_json()["order"]["status"]
        == "completed"
    )

    second_cancel = client.post(
        (
            "/api/v1/clinical-orders/imaging/"
            f"{order.id}/cancel"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "reason": "Cannot cancel terminal order",
        },
    )

    assert second_cancel.status_code == 409
