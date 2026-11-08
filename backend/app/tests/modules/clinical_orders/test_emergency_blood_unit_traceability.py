"""P23D.E S02: unit-level issuance, concurrency key, independent post review.

Uses the isolated SQLite test database and dedicated test Redis for JWT routes.
Does not authorize real clinical transfusion or release.
"""
from __future__ import annotations

import pytest
from flask import current_app
from sqlalchemy import select

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.models.blood_product_issued_unit_model import (
    BloodProductIssuedUnit,
)
from app.modules.clinical_orders.services import blood_product_order_service as svc
from app.tests.modules.clinical_orders.test_emergency_blood_request_security import (
    _setup, _requested, _verify,
)
from app.tests.modules.clinical_orders.test_blood_product_orders import _create


def _ctx(monkeypatch, clinic, make_patient, make_staff):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    return ctx


def _release(ctx, clinic, order, identifiers=None, count=2):
    return svc.fulfill_blood_product_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["lab_tech"].id,
        units_issued=count,
        fulfillment_note="Emergency release to clinical team; no administration recorded",
        issued_units=(
            [{"unit_identifier": x, "product_blood_type": "O-"} for x in identifiers]
            if identifiers is not None else None
        ),
    )


def test_emergency_issues_have_unique_normalized_unit_patient_admission_trace(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    result = _release(ctx, clinic, order, [" ems-001 ", "ems-002"])
    assert result.status == "completed"
    assert result.units_issued == 2
    assert result.emergency_review_outcome is None
    units = list(db.session.scalars(
        select(BloodProductIssuedUnit).where(BloodProductIssuedUnit.order_id == order.id)
        .order_by(BloodProductIssuedUnit.unit_identifier)
    ))
    assert [u.unit_identifier for u in units] == ["EMS-001", "EMS-002"]
    assert all(u.clinic_id == clinic.id for u in units)
    assert all(u.patient_id == ctx["patient"].id for u in units)
    assert all(u.admission_id == ctx["admission"].id for u in units)
    assert all(u.issued_by_staff_id == ctx["lab_tech"].id for u in units)
    assert all(u.product_blood_type == "O-" for u in units)
    assert all(u.issued_at is not None for u in units)
    assert set(ctx["audits"][-1]["new_value"]["unit_identifiers"]) == {
        "EMS-001", "EMS-002",
    }


@pytest.mark.parametrize("ids,match", [
    (None, "Every emergency issued unit"),
    (["ONE"], "Every emergency issued unit"),
    (["A", "a"], "distinct"),
    (["A", "   "], "unit_identifier"),
])
def test_emergency_unit_identifiers_required_and_distinct(
    monkeypatch, clinic, make_patient, make_staff, ids, match,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    with pytest.raises(ValidationError, match=match):
        _release(ctx, clinic, order, ids)
    assert order.status == "active"
    assert order.units_issued == 0
    assert db.session.scalar(select(BloodProductIssuedUnit.id)) is None


def test_issued_unit_mismatched_label_blood_group_is_rejected(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    with pytest.raises(ValidationError, match="blood type must match"):
        svc.fulfill_blood_product_order(
            order.id, clinic_id=clinic.id, actor_staff_id=ctx["lab_tech"].id,
            units_issued=2, fulfillment_note="Mismatched unit label",
            issued_units=[
                {"unit_identifier": "T-1", "product_blood_type": "O-"},
                {"unit_identifier": "T-2", "product_blood_type": "A+"},
            ],
        )
    assert order.status == "active"
    assert db.session.query(BloodProductIssuedUnit).count() == 0


def test_issued_unit_identifier_cannot_be_reissued_across_orders(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    first = _requested(ctx, clinic)
    _verify(ctx, clinic, first)
    _release(ctx, clinic, first, ["SAME-1", "FIRST-2"])

    second = _requested(ctx, clinic)
    _verify(ctx, clinic, second)
    with pytest.raises(ConflictError, match="already been issued"):
        _release(ctx, clinic, second, ["same-1", "SECOND-2"])
    assert second.status == "active"
    assert second.units_issued == 0
    assert db.session.scalar(
        select(BloodProductIssuedUnit.id).where(BloodProductIssuedUnit.unit_identifier == "SECOND-2")
    ) is None


def test_completed_emergency_cannot_issue_same_units_twice(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["ONCE-1", "ONCE-2"])
    with pytest.raises(ConflictError, match="Only active"):
        _release(ctx, clinic, order, ["ONCE-1", "ONCE-2"])
    assert db.session.query(BloodProductIssuedUnit).count() == 2


def test_standard_order_rejects_emergency_unit_payload_without_changing_normal_flow(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _create(ctx, clinic)
    svc.verify_blood_product_compatibility(
        order.id, clinic_id=clinic.id, actor_staff_id=ctx["lab_tech"].id,
        crossmatch_reference="STANDARD-XM-22",
    )
    with pytest.raises(ValidationError, match="Identified units require emergency"):
        _release(ctx, clinic, order, ["BAD", "BAD2"])
    assert order.status == "active"
    ok = _release(ctx, clinic, order)
    assert ok.status == "completed"
    assert db.session.query(BloodProductIssuedUnit).count() == 0


def test_independent_doctor_review_after_release_and_audit(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["REVIEW-1", "REVIEW-2"])
    review = svc.review_emergency_blood_release(
        order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
        outcome="escalated", note="Follow-up serologic results still required; escalated to blood bank",
    )
    assert review.emergency_review_outcome == "escalated"
    assert review.emergency_reviewed_by_staff_id == reviewer.id
    assert review.emergency_reviewed_at is not None
    assert review.emergency_review_note
    assert review.units_issued == 2
    assert "followup_testing_is_not_certified" in ctx["audits"][-1]["new_value"]


def test_requesting_doctor_cannot_self_review_and_duplicate_review_denied(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["SEP-1", "SEP-2"])
    with pytest.raises(ConflictError, match="Independent physician"):
        svc.review_emergency_blood_release(
            order.id, clinic_id=clinic.id, actor_staff_id=ctx["doctor"].id,
            outcome="reviewed", note="Self-review forbidden",
        )
    svc.review_emergency_blood_release(
        order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
        outcome="reviewed", note="Post-issue documentation reviewed; laboratory follow-up remains separate",
    )
    with pytest.raises(ConflictError, match="already been reviewed"):
        svc.review_emergency_blood_release(
            order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
            outcome="escalated", note="Duplicate review",
        )


def test_review_requires_completed_issued_units_and_doctor_role(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    with pytest.raises(ConflictError, match="must be completed"):
        svc.review_emergency_blood_release(
            order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
            outcome="reviewed", note="Too soon",
        )
    _release(ctx, clinic, order, ["DOC-1", "DOC-2"])
    with pytest.raises(ValidationError, match="doctor role"):
        svc.review_emergency_blood_release(
            order.id, clinic_id=clinic.id, actor_staff_id=ctx["lab_tech"].id,
            outcome="reviewed", note="Lab staff cannot doctor-review",
        )


def test_post_release_review_can_be_recorded_after_feature_disabled(
    monkeypatch, clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["AFTER-1", "AFTER-2"])
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = False
    result = svc.review_emergency_blood_release(
        order.id, clinic_id=clinic.id, actor_staff_id=reviewer.id,
        outcome="reviewed", note="Retrospective review, no new issue allowed",
    )
    assert result.emergency_review_outcome == "reviewed"


def test_cross_tenant_review_fails_closed(
    monkeypatch, clinic, make_clinic, make_patient, make_staff,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    _release(ctx, clinic, order, ["TENANT-1", "TENANT-2"])
    foreign = make_clinic(name="Foreign clinic for unit trace")
    reviewer = make_staff(clinic=foreign, role=Role.DOCTOR)
    with pytest.raises(NotFoundError):
        svc.review_emergency_blood_release(
            order.id, clinic_id=foreign.id, actor_staff_id=reviewer.id,
            outcome="reviewed", note="Unauthorized review",
        )


def test_route_role_split_and_readback_includes_identified_issued_units(
    monkeypatch, client, clinic, make_patient, make_staff, auth_headers_for,
):
    ctx = _ctx(monkeypatch, clinic, make_patient, make_staff)
    reviewer = make_staff(clinic=clinic, role=Role.DOCTOR)
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    root = f"/api/v1/clinical-orders/blood-products/{order.id}"
    lab_headers = auth_headers_for(ctx["lab_tech"].user, role=Role.LAB_TECHNICIAN)
    reviewer_headers = auth_headers_for(reviewer.user, role=Role.DOCTOR)
    requester_headers = auth_headers_for(ctx["doctor"].user, role=Role.DOCTOR)

    fulfillment = client.post(
        root + "/fulfill", headers=lab_headers,
        json={
            "units_issued": 2,
            "fulfillment_note": "Two identified units released",
            "issued_units": [
                {"unit_identifier": "route-unit-1", "product_blood_type": "O-"},
                {"unit_identifier": "route-unit-2", "product_blood_type": "O-"},
            ],
        },
    )
    assert fulfillment.status_code == 200, fulfillment.get_json()
    order_body = fulfillment.get_json()["order"]
    assert [u["unit_identifier"] for u in order_body["issued_units"]] == [
        "ROUTE-UNIT-1", "ROUTE-UNIT-2",
    ]
    readback = client.get(root, headers=lab_headers)
    assert readback.status_code == 200
    assert len(readback.get_json()["issued_units"]) == 2

    review_url = root + "/emergency-review"
    payload = {"outcome": "reviewed", "note": "Trace reviewed; serology follow-up separately tracked"}
    assert client.post(review_url, headers=lab_headers, json=payload).status_code == 403
    assert client.post(review_url, headers=requester_headers, json=payload).status_code == 409
    approved = client.post(review_url, headers=reviewer_headers, json=payload)
    assert approved.status_code == 200, approved.get_json()
    assert approved.get_json()["order"]["emergency_reviewed_by_staff_id"] == reviewer.id
