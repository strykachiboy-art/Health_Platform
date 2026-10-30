from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderType,
)
from app.core.enums.patient_enums import BloodType
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_orders.services import blood_product_order_service
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(*, monkeypatch, clinic, make_patient, make_staff):
    audits = []
    monkeypatch.setattr(
        blood_product_order_service,
        "create_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )
    patient, nurse, doctor, ward, bed, admission = _setup_admission(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    patient.blood_type = BloodType.O_POS
    lab_tech = make_staff(
        clinic=clinic,
        role=Role.LAB_TECHNICIAN,
    )
    db.session.commit()

    return {
        "patient": patient,
        "nurse": nurse,
        "doctor": doctor,
        "lab_tech": lab_tech,
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
        "product_type": "packed_red_blood_cells",
        "units_requested": 2,
        "indication": "Symptomatic acute blood loss anemia",
        "special_requirements": "Leukoreduced",
        "priority": "stat",
        "scheduled_at": _db_now() + timedelta(minutes=30),
    }
    payload.update(overrides)
    return blood_product_order_service.create_blood_product_order(
        **payload
    )


def test_create_blood_product_order_captures_patient_type_and_audits(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    assert order.patient_id == ctx["patient"].id
    assert order.patient_blood_type_at_order == "O+"
    assert order.compatibility_status == "pending"
    assert order.units_requested == 2
    assert order.units_issued == 0
    assert order.status == "active"
    assert order.version == 1
    assert len(ctx["audits"]) == 1
    assert ctx["audits"][0]["entity_type"] == "BloodProductOrder"


def test_blood_product_order_normalizes_to_cpoe_contract(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)
    summary = blood_product_order_service.to_clinical_order_summary(
        order
    )

    assert summary["order_type"] == ClinicalOrderType.BLOOD_PRODUCT
    assert summary["source_entity_type"] == "BloodProductOrder"
    assert summary["source_entity_id"] == order.id
    assert summary["details"]["units_requested"] == 2
    assert summary["details"]["patient_blood_type_at_order"] == "O+"


def test_nurse_cannot_create_blood_product_order(
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


def test_lab_technician_cannot_create_blood_product_order(
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
            actor_staff_id=ctx["lab_tech"].id,
        )


def test_discharged_admission_cannot_receive_blood_product_order(
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


def test_invalid_product_type_is_rejected(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    with pytest.raises(ValidationError, match="Invalid blood product type"):
        _create(ctx, clinic, product_type="synthetic_blood")


def test_lab_technician_verifies_crossmatch_for_known_patient_type(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    verified = (
        blood_product_order_service
        .verify_blood_product_compatibility(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            crossmatch_reference="XM-G10-0001",
            product_blood_type=BloodType.O_POS,
            notes="Serologic crossmatch compatible",
        )
    )

    assert verified.compatibility_status == "compatible"
    assert verified.patient_blood_type_verified == "O+"
    assert verified.product_blood_type == "O+"
    assert verified.crossmatch_reference == "XM-G10-0001"
    assert (
        verified.compatibility_verified_by_staff_id
        == ctx["lab_tech"].id
    )
    assert verified.compatibility_verified_at is not None
    assert verified.version == 2


def test_standard_compatibility_verification_requires_known_patient_type(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    ctx["patient"].blood_type = BloodType.UNKNOWN
    db.session.commit()
    order = _create(ctx, clinic)

    with pytest.raises(ConflictError, match="blood type must be known"):
        blood_product_order_service.verify_blood_product_compatibility(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            crossmatch_reference="XM-G10-0002",
        )


def test_emergency_release_allows_unknown_patient_type_with_reason(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    ctx["patient"].blood_type = BloodType.UNKNOWN
    db.session.commit()
    order = _create(ctx, clinic)

    verified = (
        blood_product_order_service
        .verify_blood_product_compatibility(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            emergency_release=True,
            emergency_release_reason=(
                "Life-threatening hemorrhage; cannot await typing"
            ),
        )
    )

    assert verified.compatibility_status == "emergency_release"
    assert verified.patient_blood_type_verified == "unknown"
    assert "hemorrhage" in verified.emergency_release_reason


def test_emergency_release_requires_reason(
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
        match="emergency_release_reason is required",
    ):
        blood_product_order_service.verify_blood_product_compatibility(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            emergency_release=True,
        )


def test_doctor_cannot_self_verify_compatibility(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    with pytest.raises(ValidationError, match="lab technician role"):
        blood_product_order_service.verify_blood_product_compatibility(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["doctor"].id,
            crossmatch_reference="XM-G10-0003",
        )


def test_fulfillment_requires_compatibility_verification(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    with pytest.raises(ConflictError, match="compatibility"):
        blood_product_order_service.fulfill_blood_product_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            units_issued=2,
            fulfillment_note="Issued two units",
        )


def test_lab_technician_fulfills_verified_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    blood_product_order_service.verify_blood_product_compatibility(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["lab_tech"].id,
        crossmatch_reference="XM-G10-0004",
        product_blood_type=BloodType.O_POS,
    )

    fulfilled = blood_product_order_service.fulfill_blood_product_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["lab_tech"].id,
        units_issued=2,
        fulfillment_note="Two compatible units released",
    )

    assert fulfilled.status == ClinicalOrderLifecycleStatus.COMPLETED.value
    assert fulfilled.units_issued == 2
    assert fulfilled.fulfilled_by_staff_id == ctx["lab_tech"].id
    assert fulfilled.completed_at is not None
    assert fulfilled.version == 3


def test_fulfillment_cannot_issue_more_than_requested(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic, units_requested=1)

    blood_product_order_service.verify_blood_product_compatibility(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["lab_tech"].id,
        crossmatch_reference="XM-G10-0005",
    )

    with pytest.raises(
        ValidationError,
        match="cannot exceed units_requested",
    ):
        blood_product_order_service.fulfill_blood_product_order(
            order.id,
            clinic_id=clinic.id,
            actor_staff_id=ctx["lab_tech"].id,
            units_issued=2,
            fulfillment_note="Invalid over-issue",
        )


def test_doctor_can_cancel_active_blood_product_order(
    monkeypatch, clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)

    cancelled = blood_product_order_service.cancel_blood_product_order(
        order.id,
        clinic_id=clinic.id,
        actor_staff_id=ctx["doctor"].id,
        reason="Transfusion no longer indicated",
    )

    assert cancelled.status == ClinicalOrderLifecycleStatus.CANCELLED.value
    assert cancelled.cancelled_by_staff_id == ctx["doctor"].id
    assert cancelled.cancelled_at is not None


def test_blood_product_order_tenant_isolation(
    monkeypatch, clinic, make_clinic, make_patient, make_staff
):
    ctx = _context(
        monkeypatch=monkeypatch,
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    order = _create(ctx, clinic)
    other = make_clinic(name="Gate 10 Blood Foreign Clinic")

    with pytest.raises(NotFoundError):
        blood_product_order_service.get_blood_product_order(
            order.id,
            clinic_id=other.id,
        )


def test_route_doctor_orders_lab_verifies_and_fulfills_nurse_views(
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
        "/api/v1/clinical-orders/blood-products",
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "admission_id": ctx["admission"].id,
            "product_type": "packed_red_blood_cells",
            "units_requested": 1,
            "indication": "Acute hemorrhage",
            "priority": "stat",
        },
    )
    assert create_response.status_code == 201
    order_id = create_response.get_json()["order"]["id"]

    verify_response = client.post(
        (
            "/api/v1/clinical-orders/blood-products/"
            f"{order_id}/verify-compatibility"
        ),
        headers=auth_headers_for(
            ctx["lab_tech"].user,
            role=Role.LAB_TECHNICIAN,
        ),
        json={
            "crossmatch_reference": "XM-ROUTE-1",
            "product_blood_type": "O+",
        },
    )
    assert verify_response.status_code == 200

    fulfill_response = client.post(
        (
            "/api/v1/clinical-orders/blood-products/"
            f"{order_id}/fulfill"
        ),
        headers=auth_headers_for(
            ctx["lab_tech"].user,
            role=Role.LAB_TECHNICIAN,
        ),
        json={
            "units_issued": 1,
            "fulfillment_note": "Unit released",
        },
    )
    assert fulfill_response.status_code == 200

    get_response = client.get(
        f"/api/v1/clinical-orders/blood-products/{order_id}",
        headers=auth_headers_for(
            ctx["nurse"].user,
            role=Role.NURSE,
        ),
    )
    assert get_response.status_code == 200
    assert get_response.get_json()["status"] == "completed"


def test_doctor_route_cannot_verify_compatibility(
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
            "/api/v1/clinical-orders/blood-products/"
            f"{order.id}/verify-compatibility"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
        json={
            "crossmatch_reference": "XM-NOT-ALLOWED",
        },
    )
    assert response.status_code == 403
