"""P23D.E S03.B: per-unit verified lab evidence (never clinical clearance).

Synthetic in-memory SQLite / isolated test Redis. No clinical action or migration.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from flask import current_app
from sqlalchemy import select

from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag, SampleType
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.models.blood_product_lab_followup_model import (
    BloodProductLabFollowup,
)
from app.modules.clinical_orders.services import blood_product_order_service as svc
from app.modules.lab.models.lab_model import LabOrder, LabOrderItem, LabTest
from app.tests.modules.clinical_orders.test_emergency_blood_unit_traceability import (
    _ctx, _release, _requested, _verify,
)


CODE = "P23DE-TEST-POST-XMATCH"


def _issued(monkeypatch, clinic, make_patient, make_staff):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["LAB-FU-001", "LAB-FU-002"])
    return ctx, order


def _lab(ctx, clinic, order, *, code=CODE, flag=LabResultFlag.NORMAL,
         sample_type=SampleType.BLOOD, verified=True, patient_id=None,
         result_before_issue=False):
    unit = order.issued_units[0]
    issue_at = unit.issued_at
    if issue_at.tzinfo is not None:
        issue_at = issue_at.replace(tzinfo=None)
    now = issue_at + timedelta(seconds=3)
    test = LabTest(
        name="Synthetic verified blood-bank compatibility evidence",
        code=code, clinic_id=clinic.id, sample_type=sample_type,
    )
    db.session.add(test)
    db.session.flush()
    lab = LabOrder(
        clinic_id=clinic.id,
        patient_id=(patient_id or ctx["patient"].id),
        ordered_by_id=ctx["doctor"].id,
        status=(LabOrderStatus.COMPLETED if verified else LabOrderStatus.IN_PROGRESS),
        created_at=issue_at - timedelta(days=1),
        sample_collected_at=issue_at - timedelta(minutes=20),
        collected_by_id=ctx["lab_tech"].id,
        processed_at=issue_at - timedelta(minutes=1),
        processed_by_id=ctx["lab_tech"].id,
        verified_at=(now if verified else None),
        verified_by_id=(ctx["lab_tech"].id if verified else None),
        completed_at=(now if verified else None),
    )
    db.session.add(lab)
    db.session.flush()
    item = LabOrderItem(
        order_id=lab.id, test_id=test.id,
        result_value="Synthetic lab evidence; NOT a compatibility clearance",
        flag=flag,
        resulted_at=(issue_at - timedelta(seconds=30) if result_before_issue else now),
    )
    db.session.add(item)
    db.session.commit()
    return item


def _record(ctx, clinic, order, item, *, unit_idx=0, **overrides):
    data = {
        "issued_unit_id": order.issued_units[unit_idx].id,
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["lab_tech"].id,
        "lab_order_item_id": item.id,
        "outcome": "evidence_recorded",
        "evidence_note": "Blood-bank team linked completed lab result; clearance remains independent",
    }
    data.update(overrides)
    return svc.record_emergency_blood_lab_followup(order.id, **data)


def test_release_creates_one_pending_obligation_per_issued_unit(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    assert order.status == "completed"
    assert len(order.issued_units) == 2
    assert len(order.lab_followups) == 2
    assert {x.issued_unit_id for x in order.lab_followups} == {
        x.id for x in order.issued_units
    }
    assert all(x.status == "pending" for x in order.lab_followups)
    assert all(x.lab_order_item_id is None for x in order.lab_followups)
    assert db.session.scalar(select(BloodProductLabFollowup.id)) is not None


def test_verified_approved_lab_evidence_is_linked_not_certified(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    result = _record(ctx, clinic, order, item)
    record = next(x for x in result.lab_followups if x.issued_unit_id == result.issued_units[0].id)
    assert record.status == "evidence_recorded"
    assert record.lab_order_item_id == item.id
    assert record.recorded_by_staff_id == ctx["lab_tech"].id
    assert record.recorded_at is not None
    assert len([x for x in result.lab_followups if x.status == "pending"]) == 1
    assert result.emergency_review_outcome is None
    assert ctx["audits"][-1]["new_value"]["compatibility_clearance_certified"] is False


def test_replay_is_denied_and_other_unit_remains_pending(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    _record(ctx, clinic, order, item)
    before = len(ctx["audits"])
    with pytest.raises(ConflictError, match="already been recorded"):
        _record(ctx, clinic, order, item)
    assert len(ctx["audits"]) == before
    assert order.lab_followups[1].status == "pending"


def test_verified_lab_item_cannot_be_reused_across_issued_units(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    _record(ctx, clinic, order, item, unit_idx=0)
    audit_count = len(ctx["audits"])

    with pytest.raises(ConflictError, match="already linked"):
        _record(ctx, clinic, order, item, unit_idx=1)

    assert len(ctx["audits"]) == audit_count
    assert order.lab_followups[0].status == "evidence_recorded"
    assert order.lab_followups[1].status == "pending"
    assert order.lab_followups[1].lab_order_item_id is None


def test_allowlist_defaults_to_fail_closed(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config.pop("EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES", None)
    item = _lab(ctx, clinic, order)
    with pytest.raises(ConflictError, match="not configured"):
        _record(ctx, clinic, order, item)
    assert all(x.status == "pending" for x in order.lab_followups)


def test_wrong_lab_code_and_nonblood_sample_rejected(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = ("OTHER-APPROVED",)
    item = _lab(ctx, clinic, order)
    with pytest.raises(ConflictError, match="not an approved"):
        _record(ctx, clinic, order, item)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item.test.sample_type = SampleType.URINE
    db.session.commit()
    with pytest.raises(ConflictError, match="not an approved"):
        _record(ctx, clinic, order, item)


def test_unverified_lab_order_fails_closed(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order, verified=False)
    with pytest.raises(ConflictError, match="fully verified"):
        _record(ctx, clinic, order, item)
    assert order.lab_followups[0].status == "pending"


def test_pre_issue_lab_result_cannot_be_marked_post_release(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order, result_before_issue=True)
    with pytest.raises(ConflictError, match="predates"):
        _record(ctx, clinic, order, item)


def test_critical_result_requires_discrepancy_and_escalation_note(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order, flag=LabResultFlag.CRITICAL)
    with pytest.raises(ConflictError, match="requires escalation"):
        _record(ctx, clinic, order, item)
    with pytest.raises(ValidationError, match="escalation_note"):
        _record(ctx, clinic, order, item, outcome="discrepancy_escalated")
    ok = _record(
        ctx, clinic, order, item,
        outcome="discrepancy_escalated",
        escalation_note="Clinical team and blood bank contacted for urgent review",
    )
    assert ok.lab_followups[0].status == "discrepancy_escalated"
    assert ok.lab_followups[0].escalation_note


def test_wrong_patient_and_wrong_unit_rejected(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    other = make_patient(clinic=clinic)
    item = _lab(ctx, clinic, order, patient_id=other.id)
    with pytest.raises(NotFoundError, match="patient and clinic"):
        _record(ctx, clinic, order, item)
    with pytest.raises(NotFoundError, match="Issued blood unit"):
        _record(ctx, clinic, order, item, issued_unit_id=999999)


def test_lab_role_and_tenant_isolation(
    monkeypatch, clinic, make_clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    with pytest.raises(ValidationError, match="lab technician role"):
        _record(ctx, clinic, order, item, actor_staff_id=ctx["doctor"].id)
    foreign = make_clinic(name="S03B Foreign Clinic")
    foreign_lab = make_staff(clinic=foreign, role=Role.LAB_TECHNICIAN)
    with pytest.raises(NotFoundError):
        _record(ctx, clinic, order, item, clinic_id=foreign.id,
                actor_staff_id=foreign_lab.id)


def test_followup_can_finish_after_feature_disabled_without_new_issue(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = False
    result = _record(ctx, clinic, order, item)
    assert result.lab_followups[0].status == "evidence_recorded"


def test_post_release_review_does_not_mark_lab_followup_complete(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    svc.review_emergency_blood_release(
        order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
        outcome="reviewed", note="Independent physician review; lab follow-up remains pending",
    )
    assert all(x.status == "pending" for x in order.lab_followups)


def test_route_role_split_and_followup_readback(
    monkeypatch, client, clinic, make_patient, make_staff, auth_headers_for,
):
    ctx, order = _issued(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_FOLLOWUP_TEST_CODES"] = (CODE,)
    item = _lab(ctx, clinic, order)
    target = (f"/api/v1/clinical-orders/blood-products/{order.id}"
              f"/units/{order.issued_units[0].id}/lab-followup")
    doctor_headers = auth_headers_for(ctx["doctor"].user, role=Role.DOCTOR)
    lab_headers = auth_headers_for(ctx["lab_tech"].user, role=Role.LAB_TECHNICIAN)
    body = {
        "lab_order_item_id": item.id,
        "outcome": "evidence_recorded",
        "evidence_note": "Verified lab evidence linked, no compatibility clearance asserted",
    }
    assert client.post(target, json=body, headers=doctor_headers).status_code == 403
    ok = client.post(target, json=body, headers=lab_headers)
    assert ok.status_code == 200, ok.get_json()
    records = ok.get_json()["order"]["lab_followups"]
    assert len(records) == 2
    assert records[0]["status"] == "evidence_recorded"
    result = client.get(f"/api/v1/clinical-orders/blood-products/{order.id}", headers=lab_headers)
    assert result.status_code == 200
    assert result.get_json()["lab_followups"][0]["status"] == "evidence_recorded"
