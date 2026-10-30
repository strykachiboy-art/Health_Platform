from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)

import pytest

import app.core.clinical_safety.services.clinical_safety_context_service as context_service
from app.core.enums.consultation_enums import ConsultationStatus
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.patient.models.patient_model import (
    PatientAllergy,
    PatientVitals,
)
from app.modules.prescription.models.prescription_model import (
    Prescription,
    PrescriptionItem,
)


def _activate(clinic, patient):
    patient.clinic_id = clinic.id
    patient.is_active = True


def _staff(make_authenticated_staff, clinic):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    return staff


def test_future_structured_allergy_is_excluded_by_evaluation_time(
    db,
    clinic,
    patient,
):
    _activate(clinic, patient)

    cutoff = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    db.session.add_all(
        [
            PatientAllergy(
                clinic_id=clinic.id,
                patient_id=patient.id,
                allergen_name="Penicillin",
                allergen_type="drug",
                clinical_status="active",
                verification_status="confirmed",
                recorded_at=cutoff - timedelta(minutes=1),
            ),
            PatientAllergy(
                clinic_id=clinic.id,
                patient_id=patient.id,
                allergen_name="Future Latex",
                allergen_type="latex",
                clinical_status="active",
                verification_status="confirmed",
                recorded_at=cutoff + timedelta(minutes=1),
            ),
        ]
    )
    db.session.flush()

    context = (
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            evaluation_at=cutoff,
        ).to_dict()
    )

    assert [
        item["allergen"]
        for item in context["allergies"]
    ] == ["Penicillin"]


def test_future_prescription_is_excluded_by_issue_time(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(clinic, patient)

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    cutoff = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    current_drug = make_drug(
        clinic,
        is_active=True,
    )
    future_drug = make_drug(
        clinic,
        is_active=True,
    )

    current = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=PrescriptionStatus.ACTIVE,
        issued_at=cutoff - timedelta(minutes=1),
    )

    future = Prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        status=PrescriptionStatus.ACTIVE,
        issued_at=cutoff + timedelta(minutes=1),
    )

    db.session.add_all([current, future])
    db.session.flush()

    db.session.add_all(
        [
            PrescriptionItem(
                prescription_id=current.id,
                drug_id=current_drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
            PrescriptionItem(
                prescription_id=future.id,
                drug_id=future_drug.id,
                is_prn=False,
                order_priority="routine",
                order_type="outpatient",
            ),
        ]
    )
    db.session.flush()

    context = (
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            evaluation_at=cutoff,
        ).to_dict()
    )

    assert context["medication_ids"] == [
        current_drug.id
    ]


def test_future_vitals_weight_is_excluded(
    db,
    clinic,
    patient,
):
    _activate(clinic, patient)

    cutoff = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    db.session.add_all(
        [
            PatientVitals(
                patient_id=patient.id,
                weight_kg=70,
                recorded_at=cutoff - timedelta(minutes=1),
            ),
            PatientVitals(
                patient_id=patient.id,
                weight_kg=99,
                recorded_at=cutoff + timedelta(minutes=1),
            ),
        ]
    )
    db.session.flush()

    context = (
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            evaluation_at=cutoff,
        ).to_dict()
    )

    assert context["weight_kg"] == 70.0
    assert context["weight"] == 70.0


def test_future_and_unfinished_diagnoses_are_excluded(
    db,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(clinic, patient)

    staff = _staff(
        make_authenticated_staff,
        clinic,
    )

    cutoff = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )

    db.session.add_all(
        [
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code="I10",
                started_at=cutoff - timedelta(days=1),
                ended_at=cutoff - timedelta(minutes=1),
            ),
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code="E11.9",
                started_at=cutoff - timedelta(days=1),
                ended_at=cutoff + timedelta(minutes=1),
            ),
            Consultation(
                clinic_id=clinic.id,
                patient_id=patient.id,
                staff_id=staff.id,
                status=ConsultationStatus.COMPLETED,
                icd10_code="J45.909",
                started_at=cutoff - timedelta(days=1),
                ended_at=None,
            ),
        ]
    )
    db.session.flush()

    context = (
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            evaluation_at=cutoff,
        ).to_dict()
    )

    assert context["diagnosis_codes"] == ["I10"]


def test_naive_evaluation_time_fails_closed(
    clinic,
    patient,
):
    _activate(clinic, patient)

    with pytest.raises(
        ValidationError,
        match="timezone",
    ):
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            evaluation_at=datetime(
                2026,
                10,
                30,
                8,
                0,
            ),
        )


def test_invalid_proposed_medication_fails_closed(
    clinic,
    patient,
):
    _activate(clinic, patient)

    with pytest.raises(
        ValidationError,
        match="drug_id",
    ):
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
            proposed_medication={
                "drug_id": -1,
            },
        )


def test_clinical_source_failure_is_not_silently_replaced_with_empty_context(
    monkeypatch,
    clinic,
    patient,
):
    _activate(clinic, patient)

    def fail_labs(**_kwargs):
        raise RuntimeError(
            "simulated clinical source failure"
        )

    monkeypatch.setattr(
        context_service,
        "_latest_completed_labs",
        fail_labs,
    )

    with pytest.raises(
        RuntimeError,
        match="simulated clinical source failure",
    ):
        context_service.build_clinical_safety_context(
            clinic_id=clinic.id,
            patient_id=patient.id,
        )
