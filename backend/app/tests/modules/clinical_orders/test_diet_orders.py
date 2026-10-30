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
from app.modules.clinical_orders.services import diet_order_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(*, monkeypatch, clinic, make_patient, make_staff):
    audits = []
    monkeypatch.setattr(
        diet_order_service,
        "create_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    patient, nurse, doctor, ward, bed, admission = _setup_admission(
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


def _create(ctx, clinic, **overrides):
    payload = {
        "admission_id": ctx["admission"].id,
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["doctor"].id,
        "diet_type": "renal",
        "feeding_route": "oral",
        "texture": "soft",
        "fluid_restriction_ml_per_day": 1500,
        "sodium_restriction_mg_per_day": 2000,
        "calorie_target_kcal_per_day": 1800,
        "instructions": "Renal diet; monitor oral intake",
        "priority": "routine",
        "start_at": _db_now() + timedelta(minutes=15),
    }
    payload.update(overrides)
    return diet_order_service.create_diet_order(**payload)


def test_create_diet_order_is_authoritative_and_audited(
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
    assert order.diet_type == "renal"
    assert order.feeding_route == "oral"
    assert order.texture == "soft"
    assert order.status == "active"
    assert order.version == 1
    assert len(ctx["audits"]) == 1
    assert ctx["audits"][0]["entity_type"] == "DietOrder"


def test_diet_order_normalizes_to_cpoe_contract(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)
    summary = diet_order_service.to_clinical_order_summary(order)

    assert summary["order_type"] == ClinicalOrderType.DIET
    assert summary["source_entity_type"] == "DietOrder"
    assert summary["source_entity_id"] == order.id
    assert summary["details"]["diet_type"] == "renal"
    assert summary["details"]["fluid_restriction_ml_per_day"] == 1500


def test_nurse_cannot_create_diet_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(ValidationError, match="doctor role"):
        _create(
            ctx,
            clinic,
            actor_staff_id=ctx["nurse"].id,
        )


def test_discharged_admission_cannot_receive_diet_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    ctx["admission"].status = AdmissionStatus.DISCHARGED
    db.session.commit()

    with pytest.raises(ConflictError, match="active admission"):
        _create(ctx, clinic)


def test_second_active_diet_order_is_rejected(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    _create(ctx, clinic)

    with pytest.raises(
        ConflictError,
        match="already has an active diet order",
    ):
        _create(
            ctx,
            clinic,
            diet_type="diabetic",
            feeding_route="oral",
        )


def test_cancelled_diet_can_be_replaced(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    first = _create(ctx, clinic)

    diet_order_service.cancel_diet_order(
        first.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="Superseded by new diet",
    )

    replacement = _create(
        ctx,
        clinic,
        diet_type="diabetic",
        feeding_route="oral",
        texture="regular",
    )

    assert replacement.id != first.id
    assert replacement.status == "active"
    assert replacement.diet_type == "diabetic"


def test_npo_requires_no_route_or_texture(
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
        match="NPO diet cannot have a feeding_route",
    ):
        _create(
            ctx,
            clinic,
            diet_type="npo",
            feeding_route="oral",
            texture=None,
        )

    order = _create(
        ctx,
        clinic,
        diet_type="npo",
        feeding_route=None,
        texture=None,
        fluid_restriction_ml_per_day=None,
        sodium_restriction_mg_per_day=None,
        calorie_target_kcal_per_day=None,
    )

    assert order.diet_type == "npo"
    assert order.feeding_route is None
    assert order.texture is None


def test_custom_diet_requires_description(
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
        match="custom_description is required",
    ):
        _create(
            ctx,
            clinic,
            diet_type="custom",
            custom_description=None,
        )


def test_positive_restriction_values_are_enforced(
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
        match="fluid_restriction_ml_per_day must be a positive integer",
    ):
        _create(
            ctx,
            clinic,
            fluid_restriction_ml_per_day=0,
        )


def test_complete_diet_order_records_actor_note_and_terminal_state(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    completed = diet_order_service.complete_diet_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        completion_note="Diet course completed",
    )

    assert completed.status == ClinicalOrderLifecycleStatus.COMPLETED.value
    assert completed.completed_by_staff_id == ctx["doctor"].id
    assert completed.completion_note == "Diet course completed"
    assert completed.completed_at is not None
    assert completed.version == 2


def test_cancelled_diet_cannot_be_completed(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)

    diet_order_service.cancel_diet_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="No longer appropriate",
    )

    with pytest.raises(ConflictError, match="active diet orders"):
        diet_order_service.complete_diet_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            completion_note="Should not complete",
        )


def test_diet_order_tenant_isolation(
    monkeypatch, clinic, make_clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    order = _create(ctx, clinic)
    other = make_clinic(name="Gate 10 Diet Foreign Clinic")

    with pytest.raises(NotFoundError):
        diet_order_service.get_diet_order(
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
        "/api/v1/clinical-orders/diets",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "diet_type": "cardiac",
            "feeding_route": "oral",
            "texture": "regular",
            "sodium_restriction_mg_per_day": 2000,
        },
    )

    assert create_response.status_code == 201
    order_id = create_response.get_json()["order"]["id"]

    get_response = client.get(
        f"/api/v1/clinical-orders/diets/{order_id}",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )

    assert get_response.status_code == 200
    assert get_response.get_json()["id"] == order_id


def test_nurse_route_cannot_create_diet_order(
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
        "/api/v1/clinical-orders/diets",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "diet_type": "regular",
            "feeding_route": "oral",
        },
    )

    assert response.status_code == 403


def test_route_cancel_allows_new_diet_afterward(
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

    cancel_response = client.post(
        f"/api/v1/clinical-orders/diets/{order.id}/cancel",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "reason": "Changing nutrition plan",
        },
    )

    assert cancel_response.status_code == 200

    create_response = client.post(
        "/api/v1/clinical-orders/diets",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "diet_type": "diabetic",
            "feeding_route": "oral",
        },
    )

    assert create_response.status_code == 201
