"""P23D.E S01: scoped doctor intent / lab emergency verification contract.

Offline SQLite tests. Emergency feature stays disabled by default outside
explicitly enabled tests; no migration, blood issue, or clinical actions.
"""
from __future__ import annotations

import pytest
from flask import current_app

from app.core.enums.patient_enums import BloodType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, ValidationError
from app.extensions import db
from app.modules.clinical_orders.services import blood_product_order_service as svc
from app.tests.modules.clinical_orders.test_blood_product_orders import (
    _context,
    _create,
)


def _setup(monkeypatch, clinic, make_patient, make_staff):
    return _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )


def _requested(ctx, clinic, **overrides):
    fields = {
        "emergency_release_requested": True,
        "emergency_request_reason": "Life-threatening hemorrhage: physician attests emergency need",
        "emergency_risk_acknowledged": True,
    }
    fields.update(overrides)
    return _create(ctx, clinic, **fields)


def _verify(ctx, clinic, order, **overrides):
    fields = {
        "clinic_id": clinic.id,
        "actor_staff_id": ctx["lab_tech"].id,
        "emergency_release": True,
        "emergency_release_reason": "Cannot delay red-cell issue for full crossmatch",
        "product_blood_type": BloodType.O_NEG,
    }
    fields.update(overrides)
    return svc.verify_blood_product_compatibility(order.id, **fields)


def test_emergency_intent_disabled_by_default(monkeypatch, clinic, make_patient, make_staff):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    assert current_app.config.get("EMERGENCY_BLOOD_RELEASE_ENABLED") is not True
    with pytest.raises(ConflictError, match="disabled"):
        _requested(ctx, clinic)
    assert ctx["audits"] == []


def test_emergency_verification_disabled_even_for_signed_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = False
    with pytest.raises(ConflictError, match="disabled"):
        _verify(ctx, clinic, order)
    assert order.compatibility_status == "pending"


def test_no_emergency_verification_without_signed_physician_intent(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _create(ctx, clinic)
    with pytest.raises(ConflictError, match="signed physician emergency request"):
        _verify(ctx, clinic, order)
    assert order.compatibility_status == "pending"
    assert len(ctx["audits"]) == 1


@pytest.mark.parametrize(
    "change,match",
    [
        ({"emergency_risk_acknowledged": False}, "risk acknowledgment"),
        ({"emergency_request_reason": None}, "emergency_request_reason"),
        ({"priority": "routine"}, "stat priority"),
        ({"product_type": "fresh_frozen_plasma"}, "red-cell orders only"),
    ],
)
def test_doctor_request_requires_explicit_evidence(
    monkeypatch, clinic, make_patient, make_staff, change, match
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    with pytest.raises(ValidationError, match=match):
        _requested(ctx, clinic, **change)
    assert ctx["audits"] == []


def test_standard_order_cannot_smuggle_emergency_attestation(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    with pytest.raises(ValidationError, match="not allowed"):
        _create(ctx, clinic, emergency_risk_acknowledged=True)


def test_doctor_signs_intent_lab_records_red_cell_emergency_provenance(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    ctx["patient"].blood_type = BloodType.UNKNOWN
    db.session.commit()
    order = _requested(ctx, clinic)
    assert order.emergency_release_requested is True
    assert order.emergency_requested_by_staff_id == ctx["doctor"].id
    assert order.emergency_requested_at is not None
    assert order.emergency_request_reason
    assert ctx["audits"][0]["new_value"]["emergency_risk_acknowledged"] is True
    verified = _verify(ctx, clinic, order)
    assert verified.compatibility_status == "emergency_release"
    assert verified.product_blood_type == BloodType.O_NEG.value
    assert verified.patient_blood_type_verified == BloodType.UNKNOWN.value
    assert "emergency" in ctx["audits"][1]["description"].lower()
    assert ctx["audits"][1]["new_value"]["emergency_requested_by_staff_id"] == ctx["doctor"].id
    assert "emergency_release_reason" in ctx["audits"][1]["new_value"]


def test_emergency_release_requires_known_product_type(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    with pytest.raises(ValidationError, match="Product blood type"):
        _verify(ctx, clinic, order, product_blood_type=None)
    assert order.compatibility_status == "pending"
    with pytest.raises(ValidationError, match="Product blood type"):
        _verify(ctx, clinic, order, product_blood_type=BloodType.UNKNOWN)
    assert order.compatibility_status == "pending"


def test_emergency_release_requires_current_admission(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    ctx["admission"].status = AdmissionStatus.DISCHARGED
    db.session.commit()
    with pytest.raises(ConflictError, match="active admission"):
        _verify(ctx, clinic, order)
    assert order.compatibility_status == "pending"


def test_emergency_release_cannot_be_reverified(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    before = len(ctx["audits"])
    with pytest.raises(ConflictError, match="already been verified"):
        _verify(ctx, clinic, order)
    assert len(ctx["audits"]) == before



def test_emergency_fulfillment_fails_if_feature_disabled_after_verification(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    _verify(ctx, clinic, order)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = False
    with pytest.raises(ConflictError, match="disabled"):
        svc.fulfill_blood_product_order(
            order.id, clinic_id=clinic.id, actor_staff_id=ctx["lab_tech"].id,
            units_issued=1, fulfillment_note="Test-only attempt",
        )
    assert order.units_issued == 0
    assert order.status == "active"

def test_lab_cannot_fake_doctor_emergency_request(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    with pytest.raises(ValidationError, match="doctor role"):
        _requested(ctx, clinic, actor_staff_id=ctx["lab_tech"].id)


def test_doctor_cannot_record_lab_emergency_verification(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    order = _requested(ctx, clinic)
    with pytest.raises(ValidationError, match="lab technician role"):
        _verify(ctx, clinic, order, actor_staff_id=ctx["doctor"].id)
    assert order.compatibility_status == "pending"


def test_route_emergency_create_and_verify_role_split(
    monkeypatch, client, clinic, make_patient, make_staff, auth_headers_for
):
    ctx = _setup(monkeypatch, clinic, make_patient, make_staff)
    current_app.config["EMERGENCY_BLOOD_RELEASE_ENABLED"] = True
    url = "/api/v1/clinical-orders/blood-products"
    body = {
        "admission_id": ctx["admission"].id,
        "product_type": "packed_red_blood_cells",
        "units_requested": 1,
        "indication": "Life-threatening hemorrhage",
        "priority": "stat",
        "emergency_release_requested": True,
        "emergency_request_reason": "Cannot safely delay transfusion",
        "emergency_risk_acknowledged": True,
    }
    lab_headers = auth_headers_for(ctx["lab_tech"].user, role=Role.LAB_TECHNICIAN)
    doc_headers = auth_headers_for(ctx["doctor"].user, role=Role.DOCTOR)
    assert client.post(url, json=body, headers=lab_headers).status_code == 403
    created = client.post(url, json=body, headers=doc_headers)
    assert created.status_code == 201
    order = created.get_json()["order"]
    assert order["emergency_requested_by_staff_id"] == ctx["doctor"].id
    action = f"{url}/{order['id']}/verify-compatibility"
    verify_body = {
        "emergency_release": True,
        "emergency_release_reason": "Issue required before testing completes",
        "product_blood_type": "O-",
    }
    assert client.post(action, json=verify_body, headers=doc_headers).status_code == 403
    result = client.post(action, json=verify_body, headers=lab_headers)
    assert result.status_code == 200
    assert result.get_json()["order"]["compatibility_status"] == "emergency_release"
