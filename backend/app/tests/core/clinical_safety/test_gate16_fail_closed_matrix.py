from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.clinical_safety.services.rule_engine_service import (
    evaluate_clinical_rules,
)
from app.core.clinical_safety.services.rule_evaluator import (
    OUTCOME_BLOCKED,
    OUTCOME_SAFE,
    evaluate_rule,
)
from app.core.enums.clinical_safety_enums import (
    ClinicalRuleAction,
    ClinicalRuleSeverity,
    ClinicalRuleType,
)


def make_rule(
    rule_type,
    configuration,
    *,
    rule_id=1,
    hard=False,
    conditions=None,
):
    return SimpleNamespace(
        id=rule_id,
        rule_code=f"GATE16_{rule_type.value.upper()}",
        version=1,
        severity=(
            ClinicalRuleSeverity.CRITICAL
            if hard
            else ClinicalRuleSeverity.HIGH
        ),
        action=(
            ClinicalRuleAction.BLOCK
            if hard
            else ClinicalRuleAction.ALERT
        ),
        rule_type=rule_type,
        priority=10,
        is_hard_rule=hard,
        conditions={} if conditions is None else conditions,
        configuration=configuration,
    )


MATRIX = (
    (
        ClinicalRuleType.ALLERGY_CONFLICT,
        {"allergen": "penicillin"},
        {"allergies": [{"allergen": "penicillin"}]},
    ),
    (
        ClinicalRuleType.DRUG_INTERACTION,
        {"drug_a_id": 10, "drug_b_id": 20},
        {"medication_ids": [10, 20]},
    ),
    (
        ClinicalRuleType.MAX_DOSE,
        {"drug_id": 10, "threshold": 100},
        {
            "proposed_medication": {
                "drug_id": 10,
                "dose": 150,
            }
        },
    ),
    (
        ClinicalRuleType.MIN_DOSE,
        {"drug_id": 10, "threshold": 100},
        {
            "proposed_medication": {
                "drug_id": 10,
                "dose": 50,
            }
        },
    ),
    (
        ClinicalRuleType.AGE_RESTRICTION,
        {"min_age": 18},
        {"age": 12},
    ),
    (
        ClinicalRuleType.WEIGHT_RESTRICTION,
        {"min_weight": 50},
        {"weight": 30},
    ),
    (
        ClinicalRuleType.PREGNANCY_RESTRICTION,
        {"restricted_statuses": ["pregnant"]},
        {"pregnancy_status": "pregnant"},
    ),
    (
        ClinicalRuleType.DUPLICATE_THERAPY,
        {"drug_ids": [10, 20]},
        {"medication_ids": [10, 20]},
    ),
    (
        ClinicalRuleType.THERAPEUTIC_DUPLICATION,
        {"drug_ids": [10, 20]},
        {"medication_ids": [10, 20]},
    ),
    (
        ClinicalRuleType.LAB_CONFLICT,
        {
            "measure": "potassium",
            "operator": "gt",
            "threshold": 5.5,
        },
        {
            "labs": [
                {
                    "measure": "potassium",
                    "value": 6.2,
                }
            ]
        },
    ),
    (
        ClinicalRuleType.RENAL_FUNCTION,
        {
            "measure": "egfr",
            "operator": "lt",
            "threshold": 30,
        },
        {"renal_function": {"egfr": 20}},
    ),
    (
        ClinicalRuleType.HEPATIC_FUNCTION,
        {
            "measure": "alt",
            "operator": "gt",
            "threshold": 100,
        },
        {"hepatic_function": {"alt": 220}},
    ),
    (
        ClinicalRuleType.DIAGNOSIS_CONFLICT,
        {"diagnosis_codes": ["N18.4"]},
        {"diagnosis_codes": ["N18.4"]},
    ),
    (
        ClinicalRuleType.FREQUENCY_LIMIT,
        {
            "max_occurrences": 2,
            "interval_hours": 24,
        },
        {
            "proposed_medication": {
                "frequency_occurrences": 3,
            }
        },
    ),
    (
        ClinicalRuleType.DURATION_LIMIT,
        {"maximum_days": 7},
        {
            "proposed_medication": {
                "duration_days": 10,
            }
        },
    ),
    (
        ClinicalRuleType.PATIENT_SPECIFIC_RESTRICTION,
        {
            "field": "patient_active",
            "operator": "eq",
            "value": False,
        },
        {"patient_active": False},
    ),
)


@pytest.mark.parametrize(
    ("rule_type", "configuration", "context"),
    MATRIX,
)
def test_gate16_matrix_rule_evaluates(
    rule_type,
    configuration,
    context,
):
    result = evaluate_rule(
        make_rule(rule_type, configuration),
        context,
    )

    assert result.matched is True
    assert result.evidence["evaluated"] is True
    assert result.evidence["missing_context"] == []


@pytest.mark.parametrize(
    ("rule_type", "configuration"),
    [
        (rule_type, configuration)
        for rule_type, configuration, _context in MATRIX
    ],
)
def test_hard_missing_context_fails_closed(
    rule_type,
    configuration,
):
    result = evaluate_rule(
        make_rule(
            rule_type,
            configuration,
            hard=True,
        ),
        {},
    )

    assert result.matched is True
    assert result.outcome == OUTCOME_BLOCKED
    assert result.blocked is True
    assert result.evidence["evaluated"] is False
    assert result.evidence["missing_context"]
    assert (
        result.evidence["context_policy"]
        == "hard_fail_closed"
    )


@pytest.mark.parametrize(
    ("rule_type", "configuration"),
    [
        (rule_type, configuration)
        for rule_type, configuration, _context in MATRIX
    ],
)
def test_soft_missing_context_is_explicitly_not_evaluated(
    rule_type,
    configuration,
):
    result = evaluate_rule(
        make_rule(
            rule_type,
            configuration,
            hard=False,
        ),
        {},
    )

    assert result.matched is False
    assert result.outcome == OUTCOME_SAFE
    assert result.evidence["evaluated"] is False
    assert result.evidence["missing_context"]
    assert (
        result.evidence["context_policy"]
        == "soft_not_evaluated"
    )


def test_hard_generic_condition_dependency_missing_fails_closed():
    result = evaluate_rule(
        make_rule(
            ClinicalRuleType.ALLERGY_CONFLICT,
            {"allergen": "penicillin"},
            hard=True,
            conditions={
                "field": "patient.risk_tier",
                "operator": "eq",
                "value": "high",
            },
        ),
        {"allergies": []},
    )

    assert result.outcome == OUTCOME_BLOCKED
    assert "patient.risk_tier" in result.evidence[
        "missing_context"
    ]


def test_exists_condition_can_intentionally_check_absence():
    result = evaluate_rule(
        make_rule(
            ClinicalRuleType.ALLERGY_CONFLICT,
            {"allergen": "penicillin"},
            conditions={
                "field": "patient.special_flag",
                "operator": "exists",
                "value": False,
            },
        ),
        {"allergies": []},
    )

    assert result.evidence["evaluated"] is True


def test_empty_known_collections_are_evaluated_not_missing():
    allergy = evaluate_rule(
        make_rule(
            ClinicalRuleType.ALLERGY_CONFLICT,
            {"allergen": "penicillin"},
            hard=True,
        ),
        {"allergies": []},
    )

    interaction = evaluate_rule(
        make_rule(
            ClinicalRuleType.DRUG_INTERACTION,
            {
                "drug_a_id": 10,
                "drug_b_id": 20,
            },
            hard=True,
        ),
        {"medication_ids": []},
    )

    assert allergy.outcome == OUTCOME_SAFE
    assert allergy.evidence["evaluated"] is True
    assert interaction.outcome == OUTCOME_SAFE
    assert interaction.evidence["evaluated"] is True


def test_targeted_dose_rule_for_other_drug_does_not_require_dose():
    result = evaluate_rule(
        make_rule(
            ClinicalRuleType.MAX_DOSE,
            {"drug_id": 10, "threshold": 100},
            hard=True,
        ),
        {
            "proposed_medication": {
                "drug_id": 99,
            }
        },
    )

    assert result.outcome == OUTCOME_SAFE
    assert result.evidence["evaluated"] is True


def test_hard_missing_context_blocks_engine_aggregate():
    rule = make_rule(
        ClinicalRuleType.RENAL_FUNCTION,
        {
            "measure": "egfr",
            "operator": "lt",
            "threshold": 30,
        },
        hard=True,
    )

    result = evaluate_clinical_rules(
        clinic_id=1,
        context={"renal_function": {}},
        rules=[rule],
    )

    assert result.blocked is True
    assert result.outcome == OUTCOME_BLOCKED
    assert result.matched_rule_count == 1
    assert result.results[0].evidence["evaluated"] is False


def test_soft_missing_context_is_not_counted_as_matched():
    rule = make_rule(
        ClinicalRuleType.PREGNANCY_RESTRICTION,
        {"restricted_statuses": ["pregnant"]},
        hard=False,
    )

    result = evaluate_clinical_rules(
        clinic_id=1,
        context={"pregnancy_status": None},
        rules=[rule],
    )

    assert result.blocked is False
    assert result.outcome == OUTCOME_SAFE
    assert result.matched_rule_count == 0
    assert result.results[0].evidence["evaluated"] is False
