"""CE1-S01: isolated, inactive surgical planning persistence gates."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.services import procedure_order_service
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.services import surgical_case_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _setup_admission,
)


def _context(monkeypatch, clinic, make_patient, make_staff):
    events = []
    monkeypatch.setattr(
        procedure_order_service, "create_audit_log", lambda **kw: events.append(kw)
    )
    monkeypatch.setattr(
        surgical_case_service, "create_audit_log", lambda **kw: events.append(kw)
    )
    patient, nurse, doctor, ward, bed, admission = _setup_admission(
        clinic=clinic, make_patient=make_patient, make_staff=make_staff
    )
    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "ward": ward,
        "bed": bed,
        "admission": admission,
        "events": events,
    }


def _order(clinic, ctx, **overrides):
    payload = dict(
        admission_id=ctx["admission"].id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        procedure_name="Example surgical procedure",
        procedure_type="surgical",
        indication="Clinical example only",
        consent_required=True,
    )
    payload.update(overrides)
    return procedure_order_service.create_procedure_order(**payload)


def _plan(clinic, ctx, order, **overrides):
    payload = dict(
        procedure_order_id=order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
    )
    payload.update(overrides)
    return surgical_case_service.plan_surgical_case(**payload)


def test_surgical_case_planned_once_with_canonical_lineage(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    case = _plan(clinic, ctx, order)
    assert case.id is not None
    assert case.procedure_order_id == order.id
    assert case.patient_id == ctx["patient"].id
    assert case.admission_id == ctx["admission"].id
    assert case.clinic_id == clinic.id
    assert case.planned_by_staff_id == ctx["doctor"].id
    assert case.status == "planned"
    assert case.version == 1
    assert db.session.get(SurgicalCase, case.id) is not None
    assert any(event["entity_type"] == "SurgicalCase" for event in ctx["events"])


def test_non_surgical_procedure_order_cannot_create_case(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx, procedure_type="bedside")
    with pytest.raises(ValidationError, match="Only surgical"):
        _plan(clinic, ctx, order)
    assert db.session.query(SurgicalCase).count() == 0


def test_unrequired_consent_fails_closed_even_for_surgical_type(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx, consent_required=False)
    with pytest.raises(ConflictError, match="require consent"):
        _plan(clinic, ctx, order)
    assert db.session.query(SurgicalCase).count() == 0


def test_cancelled_procedure_cannot_start_surgical_planning(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    procedure_order_service.cancel_procedure_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="Patient decision",
    )
    with pytest.raises(ConflictError, match="must be active"):
        _plan(clinic, ctx, order)


def test_discharged_admission_cannot_plan_case(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    ctx["admission"].status = AdmissionStatus.DISCHARGED
    db.session.commit()
    with pytest.raises(ConflictError, match="Active admission"):
        _plan(clinic, ctx, order)


def test_nurse_cannot_plan_surgical_case(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    with pytest.raises(ValidationError, match="Active doctor"):
        _plan(clinic, ctx, order, actor_staff_id=ctx["nurse"].id)


def test_wrong_clinic_cannot_access_order_or_surgical_case(
    monkeypatch, clinic, make_clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    other = make_clinic(name="Separate Surgical Clinic")
    other_doctor = make_staff(other, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        _plan(other, ctx, order, actor_staff_id=other_doctor.id)
    case = _plan(clinic, ctx, order)
    with pytest.raises(NotFoundError):
        surgical_case_service.get_surgical_case(case_id=case.id, clinic_id=other.id)


def test_one_surgical_case_per_procedure_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    case = _plan(clinic, ctx, order)
    with pytest.raises(ConflictError, match="already exists"):
        _plan(clinic, ctx, order)
    assert db.session.query(SurgicalCase).count() == 1
    assert db.session.get(SurgicalCase, case.id).id == case.id


def test_mismatched_patient_admission_context_rejected(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    other_patient = make_patient(clinic=clinic)
    order.patient_id = other_patient.id
    db.session.commit()
    with pytest.raises(ConflictError, match="patient/admission mismatch"):
        _plan(clinic, ctx, order)


def test_model_check_constraint_disallows_intraoperative_state(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(monkeypatch, clinic, make_patient, make_staff)
    order = _order(clinic, ctx)
    case = _plan(clinic, ctx, order)
    case.status = "in_progress"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_case_requires_positive_ids(clinic):
    with pytest.raises(ValidationError, match="positive integer"):
        surgical_case_service.get_surgical_case(case_id=0, clinic_id=clinic.id)


def test_surgery_public_routes_not_registered_in_foundation(app):
    paths = [rule.rule for rule in app.url_map.iter_rules()]
    assert not any("/surgery" in path for path in paths)
