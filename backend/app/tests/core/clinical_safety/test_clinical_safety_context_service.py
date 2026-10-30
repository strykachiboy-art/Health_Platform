from __future__ import annotations

from datetime import (
    date,
    datetime,
    timedelta,
    timezone,
)

import pytest

from app.core.enums.clinic_enums import (
    ClinicStatus,
)
from app.core.enums.prescription_enums import (
    PrescriptionStatus,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ValidationError,
)
from app.core.clinical_safety.services.clinical_safety_context_service import (
    build_clinical_safety_context,
)
from app.modules.patient.models.patient_model import (
    PatientAllergy,
    PatientVitals,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)


def _doctor(
    make_authenticated_staff,
    clinic,
):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )

    return staff


def _activate(
    clinic,
    patient,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True


def _prescription(
    db,
    clinic,
    patient,
    staff,
    *,
    status=PrescriptionStatus.ACTIVE,
    expires_at=None,
):
    record = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=status,
        expires_at=expires_at,
    )

    db.session.add(
        record
    )
    db.session.flush()

    return record


def test_builder_is_tenant_scoped(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    with pytest.raises(
        ValidationError,
        match="does not belong to clinic",
    ):
        build_clinical_safety_context(
            clinic_id=clinic.id + 999,
            patient_id=patient.id,
        )


def test_builder_uses_active_non_refuted_structured_allergies(
    db,
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    active = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergen_name="Penicillin",
        allergen_code="RXNORM:7980",
        allergen_type="drug",
        reaction="Anaphylaxis",
        severity="life_threatening",
        clinical_status="active",
        verification_status="confirmed",
    )

    resolved = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergen_name="Latex",
        allergen_type="latex",
        clinical_status="resolved",
        verification_status="confirmed",
    )

    refuted = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergen_name="Egg",
        allergen_type="food",
        clinical_status="inactive",
        verification_status="refuted",
    )

    db.session.add_all(
        [
            active,
            resolved,
            refuted,
        ]
    )
    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert len(
        context["allergies"]
    ) == 1

    allergy = context["allergies"][0]

    assert (
        allergy["allergen"]
        == "Penicillin"
    )
    assert (
        allergy["allergen_code"]
        == "RXNORM:7980"
    )
    assert (
        allergy["severity"]
        == "life_threatening"
    )
    assert (
        context["legacy_allergies_used"]
        is False
    )
    assert (
        context["allergy_codes"]
        == [
            "RXNORM:7980"
        ]
    )


def test_structured_allergies_are_authoritative_over_legacy_text(
    db,
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.allergies = (
        "LegacyPenicillin, LegacyLatex"
    )

    db.session.add(
        PatientAllergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            allergen_name="Structured Peanut",
            allergen_type="food",
            clinical_status="active",
            verification_status="confirmed",
        )
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert [
        item["allergen"]
        for item in context["allergies"]
    ] == [
        "Structured Peanut"
    ]

    assert (
        context["legacy_allergies_used"]
        is False
    )


def test_legacy_allergy_fallback_normalizes_and_deduplicates(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.allergies = (
        " Penicillin, Latex; penicillin\nPeanut "
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert [
        item["allergen"]
        for item in context["allergies"]
    ] == [
        "Penicillin",
        "Latex",
        "Peanut",
    ]

    assert (
        context["legacy_allergies_used"]
        is True
    )


def test_builder_includes_only_active_unexpired_medications(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(
        clinic,
        patient,
    )

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    active_drug = make_drug(
        clinic,
        is_active=True,
    )

    expired_drug = make_drug(
        clinic,
        is_active=True,
    )

    cancelled_drug = make_drug(
        clinic,
        is_active=True,
    )

    now = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    active_rx = _prescription(
        db,
        clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=now
        + timedelta(
            days=1
        ),
    )

    expired_rx = _prescription(
        db,
        clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
        expires_at=now
        - timedelta(
            seconds=1
        ),
    )

    cancelled_rx = _prescription(
        db,
        clinic,
        patient,
        staff,
        status=PrescriptionStatus.CANCELLED,
        expires_at=None,
    )

    db.session.add_all(
        [
            PrescriptionItem(
                prescription_id=active_rx.id,
                drug_id=active_drug.id,
                dose_value=500,
                dose_unit="mg",
                route_code="oral",
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
            PrescriptionItem(
                prescription_id=expired_rx.id,
                drug_id=expired_drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
            PrescriptionItem(
                prescription_id=cancelled_rx.id,
                drug_id=cancelled_drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
        ]
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=now,
    ).to_dict()

    assert (
        context["medication_ids"]
        == [
            active_drug.id
        ]
    )

    assert len(
        context["medications"]
    ) == 1

    medication = (
        context["medications"][0]
    )

    assert (
        medication["drug_id"]
        == active_drug.id
    )

    assert (
        medication["dose_value"]
        == 500.0
    )

    assert (
        medication["dose_unit"]
        == "mg"
    )


def test_builder_deduplicates_active_medication_ids(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(
        clinic,
        patient,
    )

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    first = _prescription(
        db,
        clinic,
        patient,
        staff,
    )

    second = _prescription(
        db,
        clinic,
        patient,
        staff,
    )

    db.session.add_all(
        [
            PrescriptionItem(
                prescription_id=first.id,
                drug_id=drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
            PrescriptionItem(
                prescription_id=second.id,
                drug_id=drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
        ]
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert (
        context["medication_ids"]
        == [
            drug.id
        ]
    )

    assert len(
        context["medications"]
    ) == 2


def test_builder_uses_latest_vitals_weight(
    db,
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    old = PatientVitals(
        patient_id=patient.id,
        weight_kg=70,
        recorded_at=datetime(
            2026,
            10,
            29,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    )

    latest = PatientVitals(
        patient_id=patient.id,
        weight_kg=72.5,
        recorded_at=datetime(
            2026,
            10,
            30,
            7,
            0,
            tzinfo=timezone.utc,
        ),
    )

    db.session.add_all(
        [
            old,
            latest,
        ]
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert (
        context["weight_kg"]
        == 72.5
    )


def test_builder_computes_age_and_gender(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.date_of_birth = date(
        2000,
        11,
        1,
    )

    evaluation_at = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=evaluation_at,
    ).to_dict()

    assert (
        context["age_years"]
        == 25
    )

    assert (
        context["patient"]["age_years"]
        == 25
    )

    assert (
        context["gender"]
        == (
            patient.gender.value
            if patient.gender is not None
            else None
        )
    )


def test_builder_carries_proposed_medication_without_merging_id_yet(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        proposed_medication={
            "drug_id": 77,
            "dose": 5,
            "dose_unit": "mg",
        },
    ).to_dict()

    assert (
        context["proposed_medication"]
        == {
            "drug_id": 77,
            "dose": 5,
            "dose_unit": "mg",
        }
    )

    assert (
        77
        not in context["medication_ids"]
    )
