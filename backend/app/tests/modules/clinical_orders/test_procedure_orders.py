from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderType,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.services import procedure_order_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(*, monkeypatch, clinic, make_patient, make_staff):
    audits = []
    monkeypatch.setattr(
        procedure_order_service,
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
        "procedure_code": "PROC-CVC",
        "procedure_name": "Central venous catheter insertion",
        "procedure_type": "bedside",
        "body_site": "right internal jugular",
        "indication": "Vasoactive infusion access",
        "prep_instructions": "Sterile field and ultrasound",
        "priority": "urgent",
        "scheduled_at": _db_now() + timedelta(hours=1),
    }
    payload.update(overrides)
    return procedure_order_service.create_procedure_order(**payload)


def test_create_procedure_order_is_authoritative_and_audited(
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
    assert order.clinic_id == clinic.id
    assert order.patient_id == ctx["patient"].id
    assert order.admission_id == ctx["admission"].id
    assert order.ordered_by_staff_id == ctx["doctor"].id
    assert order.procedure_type == "bedside"
    assert order.priority == "urgent"
    assert order.status == "active"
    assert order.version == 1
    assert len(ctx["audits"]) == 1
    assert ctx["audits"][0]["entity_type"] == "ProcedureOrder"


def test_procedure_order_normalizes_to_cpoe_contract(
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
        procedure_type="surgical",
        consent_required=True,
    )
    summary = procedure_order_service.to_clinical_order_summary(order)

    assert summary["order_type"] == ClinicalOrderType.PROCEDURE
    assert summary["source_entity_type"] == "ProcedureOrder"
    assert summary["source_entity_id"] == order.id
    assert summary["details"]["procedure_type"] == "surgical"
    assert summary["details"]["consent_required"] is True


def test_nurse_cannot_create_procedure_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    with pytest.raises(ValidationError, match="requires doctor role"):
        _create(
            ctx,
            clinic,
            actor_staff_id=ctx["nurse"].id,
        )


def test_discharged_admission_cannot_receive_procedure_order(
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


def test_invalid_procedure_type_is_rejected(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    with pytest.raises(ValidationError, match="Invalid procedure type"):
        _create(ctx, clinic, procedure_type="teleportation")


def test_required_consent_blocks_completion_until_verified(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic, consent_required=True)

    with pytest.raises(ConflictError, match="consent"):
        procedure_order_service.complete_procedure_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            procedure_note="Procedure completed.",
        )

    verified = procedure_order_service.verify_procedure_consent(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
    )
    assert verified.consent_verified_at is not None
    assert verified.consent_verified_by_staff_id == ctx["doctor"].id

    completed = procedure_order_service.complete_procedure_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        procedure_note="Procedure completed.",
        outcome_summary="No immediate complication",
    )
    assert completed.status == ClinicalOrderLifecycleStatus.COMPLETED.value


def test_consent_verification_rejected_when_not_required(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic, consent_required=False)

    with pytest.raises(ConflictError, match="does not require consent"):
        procedure_order_service.verify_procedure_consent(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
        )


def test_cancel_procedure_order_records_reason_actor_and_audit(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    cancelled = procedure_order_service.cancel_procedure_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="Procedure no longer indicated",
    )

    assert cancelled.status == ClinicalOrderLifecycleStatus.CANCELLED.value
    assert cancelled.cancelled_by_staff_id == ctx["doctor"].id
    assert cancelled.cancellation_reason == "Procedure no longer indicated"
    assert cancelled.cancelled_at is not None
    assert cancelled.version == 2


def test_complete_procedure_records_note_outcome_and_actor(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    completed = procedure_order_service.complete_procedure_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        procedure_note="Line inserted under ultrasound guidance.",
        outcome_summary="Successful placement",
        performed_at=_db_now(),
    )

    assert completed.status == ClinicalOrderLifecycleStatus.COMPLETED.value
    assert completed.completed_by_staff_id == ctx["doctor"].id
    assert completed.procedure_note == "Line inserted under ultrasound guidance."
    assert completed.outcome_summary == "Successful placement"
    assert completed.performed_at is not None
    assert completed.completed_at is not None
    assert completed.version == 2


def test_cancelled_procedure_cannot_be_completed(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)
    procedure_order_service.cancel_procedure_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="Cancelled",
    )

    with pytest.raises(ConflictError, match="active procedure orders"):
        procedure_order_service.complete_procedure_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            procedure_note="Should not complete",
        )


def test_list_procedure_orders_is_admission_scoped(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    first = _create(ctx, clinic, procedure_name="First procedure")
    second = _create(ctx, clinic, procedure_name="Second procedure")

    rows = procedure_order_service.list_procedure_orders(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
    )
    assert {row.id for row in rows} == {first.id, second.id}


def test_procedure_order_tenant_isolation(
    monkeypatch, clinic, make_clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)
    other = make_clinic(name="Gate 10 Procedure Foreign Clinic")

    with pytest.raises(NotFoundError):
        procedure_order_service.get_procedure_order(
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
        "/api/v1/clinical-orders/procedures",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "procedure_name": "Lumbar puncture",
            "procedure_type": "diagnostic",
            "body_site": "lumbar spine",
            "indication": "Evaluate meningitis",
            "consent_required": True,
            "priority": "urgent",
        },
    )
    assert create_response.status_code == 201
    order_id = create_response.get_json()["order"]["id"]

    get_response = client.get(
        f"/api/v1/clinical-orders/procedures/{order_id}",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )
    assert get_response.status_code == 200
    assert get_response.get_json()["id"] == order_id


def test_nurse_route_cannot_create_procedure_order(
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
        "/api/v1/clinical-orders/procedures",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "procedure_name": "Unauthorized",
            "procedure_type": "bedside",
            "indication": "Should fail",
        },
    )
    assert response.status_code == 403


def test_route_consent_then_complete(
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
    order = _create(ctx, clinic, consent_required=True)

    consent_response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/verify-consent",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
    )
    assert consent_response.status_code == 200

    complete_response = client.post(
        f"/api/v1/clinical-orders/procedures/{order.id}/complete",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "procedure_note": "Procedure completed safely.",
            "outcome_summary": "Successful",
        },
    )
    assert complete_response.status_code == 200
    assert complete_response.get_json()["order"]["status"] == "completed"
