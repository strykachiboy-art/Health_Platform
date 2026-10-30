from __future__ import annotations

from datetime import (
    date,
    datetime,
    timezone,
)

from app.core.clinical_safety.services.clinical_safety_context_service import (
    build_clinical_safety_context,
)
from app.core.clinical_safety.services.rule_evaluator import (
    evaluate_rule,
)
from app.core.enums.clinical_safety_enums import (
    ClinicalRuleAction,
    ClinicalRuleSeverity,
    ClinicalRuleType,
)
from app.modules.patient.models.patient_model import (
    PatientAllergy,
    PatientVitals,
)


def _activate(
    clinic,
    patient,
):
    patient.clinic_id = clinic.id
    patient.is_active = True


def _rule(
    *,
    rule_id: int,
    code: str,
    rule_type: ClinicalRuleType,
    configuration: dict,
    conditions: dict | None = None,
):
    return {
        "id": rule_id,
        "rule_code": code,
        "version": 1,
        "severity": (
            ClinicalRuleSeverity.HIGH
        ),
        "action": (
            ClinicalRuleAction.BLOCK
        ),
        "rule_type": rule_type,
        "priority": 10,
        "is_hard_rule": True,
        "conditions": (
            conditions
            if conditions is not None
            else {}
        ),
        "configuration": configuration,
    }


def test_context_preserves_canonical_and_rule_compatible_age(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.date_of_birth = date(
        2010,
        11,
        1,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=datetime(
            2026,
            10,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    ).to_dict()

    assert (
        context["age_years"]
        == 15
    )

    assert (
        context["age"]
        == 15
    )

    result = evaluate_rule(
        _rule(
            rule_id=1,
            code="age-under-18",
            rule_type=(
                ClinicalRuleType.AGE_RESTRICTION
            ),
            configuration={
                "min_age": 18,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_context_preserves_canonical_and_rule_compatible_weight(
    db,
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    db.session.add(
        PatientVitals(
            patient_id=patient.id,
            weight_kg=112.5,
            recorded_at=datetime(
                2026,
                10,
                30,
                7,
                0,
                tzinfo=timezone.utc,
            ),
        )
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert (
        context["weight_kg"]
        == 112.5
    )

    assert (
        context["weight"]
        == 112.5
    )

    result = evaluate_rule(
        _rule(
            rule_id=2,
            code="weight-over-100",
            rule_type=(
                ClinicalRuleType.WEIGHT_RESTRICTION
            ),
            configuration={
                "max_weight": 100,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_structured_allergy_shape_matches_existing_allergy_rule(
    db,
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    db.session.add(
        PatientAllergy(
            clinic_id=clinic.id,
            patient_id=patient.id,
            allergen_name="Penicillin",
            allergen_code="RXNORM:7980",
            allergen_type="drug",
            severity="severe",
            clinical_status="active",
            verification_status="confirmed",
        )
    )

    db.session.flush()

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    result = evaluate_rule(
        _rule(
            rule_id=3,
            code="allergy-penicillin",
            rule_type=(
                ClinicalRuleType.ALLERGY_CONFLICT
            ),
            configuration={
                "allergen": "penicillin",
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_legacy_allergy_shape_matches_existing_allergy_rule(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.allergies = (
        "Penicillin, Latex"
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
    ).to_dict()

    assert (
        context["legacy_allergies_used"]
        is True
    )

    result = evaluate_rule(
        _rule(
            rule_id=4,
            code="legacy-allergy-latex",
            rule_type=(
                ClinicalRuleType.ALLERGY_CONFLICT
            ),
            configuration={
                "allergen": "latex",
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_structured_dose_value_is_normalized_for_existing_dose_rule(
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
            "dose_value": 750,
            "dose_unit": "mg",
        },
    ).to_dict()

    proposed = (
        context["proposed_medication"]
    )

    assert (
        proposed["dose_value"]
        == 750
    )

    assert (
        proposed["dose"]
        == 750.0
    )

    result = evaluate_rule(
        _rule(
            rule_id=5,
            code="max-dose-77",
            rule_type=(
                ClinicalRuleType.MAX_DOSE
            ),
            configuration={
                "drug_id": 77,
                "threshold": 500,
                "unit": "mg",
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_scheduled_times_normalize_to_frequency_occurrences(
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
            "drug_id": 88,
            "scheduled_times": [
                "08:00",
                "20:00",
            ],
        },
    ).to_dict()

    proposed = (
        context["proposed_medication"]
    )

    assert (
        proposed[
            "frequency_occurrences"
        ]
        == 2
    )

    result = evaluate_rule(
        _rule(
            rule_id=6,
            code="frequency-limit",
            rule_type=(
                ClinicalRuleType.FREQUENCY_LIMIT
            ),
            configuration={
                "max_occurrences": 1,
                "interval_hours": 24,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_generic_condition_path_can_use_canonical_nested_patient_age(
    clinic,
    patient,
):
    _activate(
        clinic,
        patient,
    )

    patient.date_of_birth = date(
        2000,
        1,
        1,
    )

    context = build_clinical_safety_context(
        clinic_id=clinic.id,
        patient_id=patient.id,
        evaluation_at=datetime(
            2026,
            10,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        ),
    ).to_dict()

    result = evaluate_rule(
        _rule(
            rule_id=7,
            code="patient-specific-age",
            rule_type=(
                ClinicalRuleType.PATIENT_SPECIFIC_RESTRICTION
            ),
            configuration={
                "field": "patient.age_years",
                "operator": "gte",
                "value": 18,
            },
        ),
        context,
    )

    assert result.matched is True
    assert result.blocked is True


def test_safe_empty_shapes_exist_for_future_clinical_enrichment(
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
    ).to_dict()

    assert (
        context["pregnancy_status"]
        is None
    )

    assert (
        context["diagnosis_codes"]
        == []
    )

    assert (
        context["contraindication_codes"]
        == []
    )

    assert (
        context["labs"]
        == []
    )

    assert (
        context["renal_function"]
        == {}
    )

    assert (
        context["hepatic_function"]
        == {}
    )
