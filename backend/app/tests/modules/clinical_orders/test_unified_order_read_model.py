from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.enums.clinical_order_enums import (
    ClinicalOrderLifecycleStatus,
    ClinicalOrderType,
)
from app.core.enums.lab_enums import LabOrderStatus
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.clinical_orders.models.blood_product_order_model import (
    BloodProductOrder,
)
from app.modules.clinical_orders.models.diet_order_model import DietOrder
from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
from app.modules.clinical_orders.models.nursing_order_model import NursingOrder
from app.modules.clinical_orders.models.procedure_order_model import (
    ProcedureOrder,
)
from app.modules.clinical_orders.services.unified_order_service import (
    list_unified_clinical_orders,
)
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)
from app.modules.pharmacy.models.pharmacy_model import Drug
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)
from app.tests.modules.nursing.test_nursing_service_invariants import (
    _db_now,
    _setup_admission,
)


def _context(
    *,
    clinic,
    make_patient,
    make_staff,
):
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
    }


def _seed_all_domains(
    ctx,
    clinic,
):
    base = _db_now()

    drug = Drug(
        clinic_id=clinic.id,
        name="Ceftriaxone",
        generic_name="ceftriaxone",
    )
    db.session.add(drug)
    db.session.flush()

    prescription = Prescription(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        prescribed_by_id=ctx["doctor"].id,
        status=PrescriptionStatus.ACTIVE,
        issued_at=base,
    )
    db.session.add(prescription)
    db.session.flush()

    medication = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dosage="1 g",
        frequency="daily",
        instructions="Administer slowly",
        order_priority="urgent",
        order_type="inpatient",
        ordered_at=base + timedelta(minutes=1),
    )
    db.session.add(medication)

    lab_test = LabTest(
        clinic_id=clinic.id,
        name="Complete Blood Count",
        code=f"CBC-G10-{ctx['patient'].id}",
    )
    db.session.add(lab_test)
    db.session.flush()

    lab_order = LabOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        ordered_by_id=ctx["doctor"].id,
        status=LabOrderStatus.ORDERED,
        created_at=base + timedelta(minutes=2),
    )
    db.session.add(lab_order)
    db.session.flush()

    db.session.add(
        LabOrderItem(
            order_id=lab_order.id,
            test_id=lab_test.id,
        )
    )

    imaging = ImagingOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        ordered_by_staff_id=ctx["doctor"].id,
        study_name="Portable chest radiograph",
        modality="xray",
        body_site="chest",
        indication="Evaluate respiratory symptoms",
        priority="routine",
        status="active",
        ordered_at=base + timedelta(minutes=3),
    )
    db.session.add(imaging)

    procedure = ProcedureOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        ordered_by_staff_id=ctx["doctor"].id,
        procedure_name="Lumbar puncture",
        procedure_type="diagnostic",
        indication="Diagnostic evaluation",
        consent_required=False,
        priority="routine",
        status="active",
        ordered_at=base + timedelta(minutes=4),
    )
    db.session.add(procedure)

    blood = BloodProductOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        ordered_by_staff_id=ctx["doctor"].id,
        product_type="packed_red_blood_cells",
        units_requested=1,
        units_issued=0,
        indication="Acute anemia",
        patient_blood_type_at_order="O+",
        compatibility_status="pending",
        priority="stat",
        status="active",
        ordered_at=base + timedelta(minutes=5),
    )
    db.session.add(blood)

    diet = DietOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        ordered_by_staff_id=ctx["doctor"].id,
        diet_type="regular",
        feeding_route="oral",
        priority="routine",
        status="active",
        ordered_at=base + timedelta(minutes=6),
    )
    db.session.add(diet)

    nursing = NursingOrder(
        clinic_id=clinic.id,
        patient_id=ctx["patient"].id,
        admission_id=ctx["admission"].id,
        ordered_by_staff_id=ctx["doctor"].id,
        category="monitoring",
        order_text="Record neurological observations",
        frequency="q4h",
        priority="urgent",
        status="active",
        ordered_at=base + timedelta(minutes=7),
    )
    db.session.add(nursing)

    db.session.commit()

    return {
        "medication": medication,
        "prescription": prescription,
        "lab": lab_order,
        "imaging": imaging,
        "procedure": procedure,
        "blood": blood,
        "diet": diet,
        "nursing": nursing,
    }


def test_unified_read_model_returns_all_seven_order_domains(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    items = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
    )

    assert {
        item.order_type
        for item in items
    } == {
        ClinicalOrderType.MEDICATION,
        ClinicalOrderType.LABORATORY,
        ClinicalOrderType.IMAGING,
        ClinicalOrderType.PROCEDURE,
        ClinicalOrderType.BLOOD_PRODUCT,
        ClinicalOrderType.DIET,
        ClinicalOrderType.NURSING,
    }

    assert len(items) == 7


def test_unified_read_model_is_newest_first(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    items = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
    )

    assert [
        item.order_type
        for item in items
    ] == [
        ClinicalOrderType.NURSING,
        ClinicalOrderType.DIET,
        ClinicalOrderType.BLOOD_PRODUCT,
        ClinicalOrderType.PROCEDURE,
        ClinicalOrderType.IMAGING,
        ClinicalOrderType.LABORATORY,
        ClinicalOrderType.MEDICATION,
    ]


def test_legacy_medication_remains_prescription_item_source(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    seeded = _seed_all_domains(
        ctx,
        clinic,
    )

    items = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        order_type="medication",
    )

    assert len(items) == 1
    item = items[0]

    assert (
        item.source_entity_type
        == "PrescriptionItem"
    )
    assert (
        item.source_entity_id
        == seeded["medication"].id
    )
    assert item.admission_id is None
    assert (
        item.details["prescription_id"]
        == seeded["prescription"].id
    )
    assert (
        item.details["legacy_admission_linkage"]
        == "not_persisted_on_prescription"
    )


def test_legacy_lab_remains_lab_order_source(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    seeded = _seed_all_domains(
        ctx,
        clinic,
    )

    items = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        order_type="laboratory",
    )

    assert len(items) == 1
    item = items[0]

    assert item.source_entity_type == "LabOrder"
    assert (
        item.source_entity_id
        == seeded["lab"].id
    )
    assert item.admission_id is None
    assert (
        item.details["legacy_admission_linkage"]
        == "not_persisted_on_lab_order"
    )


@pytest.mark.parametrize(
    (
        "source_status",
        "normalized_status",
    ),
    [
        (
            PrescriptionStatus.ACTIVE,
            ClinicalOrderLifecycleStatus.ACTIVE,
        ),
        (
            PrescriptionStatus.COMPLETED,
            ClinicalOrderLifecycleStatus.COMPLETED,
        ),
        (
            PrescriptionStatus.CANCELLED,
            ClinicalOrderLifecycleStatus.CANCELLED,
        ),
        (
            PrescriptionStatus.EXPIRED,
            ClinicalOrderLifecycleStatus.COMPLETED,
        ),
    ],
)
def test_medication_status_normalization(
    source_status,
    normalized_status,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    seeded = _seed_all_domains(
        ctx,
        clinic,
    )

    prescription = seeded["prescription"]
    prescription.status = source_status

    if source_status == PrescriptionStatus.EXPIRED:
        prescription.expires_at = _db_now()

    db.session.commit()

    item = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        order_type="medication",
    )[0]

    assert item.status == normalized_status

    if (
        normalized_status
        == ClinicalOrderLifecycleStatus.COMPLETED
    ):
        assert item.completed_at is not None

    if (
        normalized_status
        == ClinicalOrderLifecycleStatus.CANCELLED
    ):
        assert item.cancelled_at is not None


@pytest.mark.parametrize(
    (
        "source_status",
        "normalized_status",
    ),
    [
        (
            LabOrderStatus.ORDERED,
            ClinicalOrderLifecycleStatus.PENDING,
        ),
        (
            LabOrderStatus.SAMPLE_COLLECTED,
            ClinicalOrderLifecycleStatus.IN_PROGRESS,
        ),
        (
            LabOrderStatus.IN_PROGRESS,
            ClinicalOrderLifecycleStatus.IN_PROGRESS,
        ),
        (
            LabOrderStatus.COMPLETED,
            ClinicalOrderLifecycleStatus.COMPLETED,
        ),
        (
            LabOrderStatus.CANCELLED,
            ClinicalOrderLifecycleStatus.CANCELLED,
        ),
    ],
)
def test_lab_status_normalization(
    source_status,
    normalized_status,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    seeded = _seed_all_domains(
        ctx,
        clinic,
    )

    order = seeded["lab"]
    order.status = source_status

    if source_status == LabOrderStatus.COMPLETED:
        order.completed_at = (
            order.created_at
            + timedelta(milliseconds=1)
        )

    db.session.commit()

    item = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        order_type="laboratory",
    )[0]

    assert item.status == normalized_status

    if (
        normalized_status
        == ClinicalOrderLifecycleStatus.COMPLETED
    ):
        assert item.completed_at is not None

    if (
        normalized_status
        == ClinicalOrderLifecycleStatus.CANCELLED
    ):
        assert item.cancelled_at is not None


def test_order_type_filter_returns_only_requested_domain(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    items = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        order_type="imaging",
    )

    assert len(items) == 1
    assert (
        items[0].order_type
        == ClinicalOrderType.IMAGING
    )


def test_status_filter_uses_normalized_status(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    pending = list_unified_clinical_orders(
        patient_id=ctx["patient"].id,
        clinic_id=clinic.id,
        status="pending",
    )

    assert len(pending) == 1
    assert (
        pending[0].order_type
        == ClinicalOrderType.LABORATORY
    )


def test_invalid_filters_are_rejected(
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    with pytest.raises(
        ValidationError,
        match="Invalid clinical order type",
    ):
        list_unified_clinical_orders(
            patient_id=ctx["patient"].id,
            clinic_id=clinic.id,
            order_type="teleportation",
        )

    with pytest.raises(
        ValidationError,
        match="Invalid clinical order status",
    ):
        list_unified_clinical_orders(
            patient_id=ctx["patient"].id,
            clinic_id=clinic.id,
            status="unknown",
        )


def test_unified_read_model_is_tenant_scoped(
    clinic,
    make_clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    other = make_clinic(
        name="Gate 10 Unified Foreign Clinic"
    )

    with pytest.raises(NotFoundError):
        list_unified_clinical_orders(
            patient_id=ctx["patient"].id,
            clinic_id=other.id,
        )


@pytest.mark.parametrize(
    "role_key,role",
    [
        ("doctor", Role.DOCTOR),
        ("nurse", Role.NURSE),
    ],
)
def test_unified_route_allows_clinical_view_roles(
    role_key,
    role,
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    response = client.get(
        (
            "/api/v1/clinical-orders/patients/"
            f"{ctx['patient'].id}"
        ),
        headers=auth_headers_for(
            ctx[role_key].user,
            role=role,
        ),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 7
    assert len(body["items"]) == 7


def test_unified_route_supports_type_and_status_filters(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )
    _seed_all_domains(
        ctx,
        clinic,
    )

    response = client.get(
        (
            "/api/v1/clinical-orders/patients/"
            f"{ctx['patient'].id}"
            "?order_type=laboratory&status=pending"
        ),
        headers=auth_headers_for(
            ctx["doctor"].user,
            role=Role.DOCTOR,
        ),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["total"] == 1
    assert (
        body["items"][0]["order_type"]
        == "laboratory"
    )


def test_unified_route_requires_authentication(
    client,
    clinic,
    make_patient,
    make_staff,
):
    ctx = _context(
        clinic=clinic,
        make_patient=make_patient,
        make_staff=make_staff,
    )

    response = client.get(
        (
            "/api/v1/clinical-orders/patients/"
            f"{ctx['patient'].id}"
        )
    )

    assert response.status_code in {
        401,
        422,
    }
