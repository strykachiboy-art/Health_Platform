from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
from types import SimpleNamespace

import pytest

import app.core.clinical_safety.services.medication_safety_service as medication_safety
import app.modules.prescription.services.prescription_service as prescription_service
from app.core.enums.clinical_safety_enums import (
    ClinicalRuleAction,
    ClinicalRuleSeverity,
    ClinicalRuleType,
)
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.prescription.models.prescription_model import Prescription


def _rule(
    *,
    rule_id,
    code,
    rule_type,
    configuration,
):
    return {
        "id": rule_id,
        "rule_code": code,
        "version": 1,
        "severity": ClinicalRuleSeverity.HIGH,
        "action": ClinicalRuleAction.BLOCK,
        "rule_type": rule_type,
        "priority": 10,
        "is_hard_rule": True,
        "conditions": {},
        "configuration": configuration,
    }


def _activate(
    clinic,
    patient,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True


def _doctor(
    make_authenticated_staff,
    clinic,
):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    return staff


def test_structured_item_normalization_drives_dose_frequency_and_duration_rules(
    monkeypatch,
):
    start = datetime(
        2026,
        10,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )
    end = start + timedelta(days=3)

    rules = [
        _rule(
            rule_id=1,
            code="max-dose",
            rule_type=ClinicalRuleType.MAX_DOSE,
            configuration={
                "drug_id": 77,
                "threshold": 500,
                "unit": "mg",
            },
        ),
        _rule(
            rule_id=2,
            code="frequency-limit",
            rule_type=ClinicalRuleType.FREQUENCY_LIMIT,
            configuration={
                "max_occurrences": 2,
                "interval_hours": 24,
            },
        ),
        _rule(
            rule_id=3,
            code="duration-limit",
            rule_type=ClinicalRuleType.DURATION_LIMIT,
            configuration={
                "maximum_days": 2,
            },
        ),
    ]

    monkeypatch.setattr(
        medication_safety,
        "resolve_clinical_rules",
        lambda **_kwargs: rules,
    )

    evaluations = (
        medication_safety
        .evaluate_structured_medication_items(
            clinic_id=1,
            medication_ids=[77],
            context={
                "patient_id": 8,
                "medication_ids": [10],
            },
            proposed_medications=[
                {
                    "drug_id": 77,
                    "dose_value": 750,
                    "dose_unit": "mg",
                    "route_code": "oral",
                    "frequency_code": "TID",
                    "scheduled_times": [
                        "08:00",
                        "14:00",
                        "20:00",
                    ],
                    "start_at": start,
                    "end_at": end,
                    "is_prn": False,
                    "max_single_dose": 1000,
                    "max_daily_dose": 2250,
                }
            ],
            evaluation_at=start,
        )
    )

    assert len(evaluations) == 1
    evaluation = evaluations[0]
    assert evaluation.blocked is True

    matched_codes = {
        result.rule_code
        for result in evaluation.results
        if result.matched
    }

    assert matched_codes == {
        "max-dose",
        "frequency-limit",
        "duration-limit",
    }


def test_min_dose_rule_uses_dose_value_alias(
    monkeypatch,
):
    monkeypatch.setattr(
        medication_safety,
        "resolve_clinical_rules",
        lambda **_kwargs: [
            _rule(
                rule_id=4,
                code="min-dose",
                rule_type=ClinicalRuleType.MIN_DOSE,
                configuration={
                    "drug_id": 88,
                    "threshold": 10,
                    "unit": "mg",
                },
            )
        ],
    )

    evaluation = (
        medication_safety
        .evaluate_structured_medication_items(
            clinic_id=1,
            medication_ids=[88],
            context={
                "medication_ids": [],
            },
            proposed_medications=[
                {
                    "drug_id": 88,
                    "dose_value": 5,
                    "dose_unit": "mg",
                }
            ],
        )[0]
    )

    assert evaluation.blocked is True

    result = next(
        item
        for item in evaluation.results
        if item.rule_code == "min-dose"
    )

    assert result.evidence["evaluation"]["dose"] == 5.0


def test_patient_wide_rules_are_not_repeated_per_item(
    monkeypatch,
):
    rules = [
        _rule(
            rule_id=5,
            code="age-rule",
            rule_type=ClinicalRuleType.AGE_RESTRICTION,
            configuration={
                "min_age": 18,
            },
        ),
        _rule(
            rule_id=6,
            code="max-dose-safe",
            rule_type=ClinicalRuleType.MAX_DOSE,
            configuration={
                "drug_id": 77,
                "threshold": 1000,
            },
        ),
    ]

    monkeypatch.setattr(
        medication_safety,
        "resolve_clinical_rules",
        lambda **_kwargs: rules,
    )

    evaluation = (
        medication_safety
        .evaluate_structured_medication_items(
            clinic_id=1,
            medication_ids=[77],
            context={
                "age": 10,
                "medication_ids": [],
            },
            proposed_medications=[
                {
                    "drug_id": 77,
                    "dose_value": 100,
                }
            ],
        )[0]
    )

    evaluated_codes = {
        result.rule_code
        for result in evaluation.results
    }

    assert evaluated_codes == {
        "max-dose-safe"
    }

    assert evaluation.blocked is False


def test_proposed_item_drug_must_belong_to_prescription_medication_ids(
    monkeypatch,
):
    monkeypatch.setattr(
        medication_safety,
        "resolve_clinical_rules",
        lambda **_kwargs: [],
    )

    with pytest.raises(
        ValidationError,
        match="included in medication_ids",
    ):
        medication_safety.evaluate_structured_medication_items(
            clinic_id=1,
            medication_ids=[77],
            context={
                "medication_ids": [],
            },
            proposed_medications=[
                {
                    "drug_id": 88,
                    "dose_value": 5,
                }
            ],
        )


def test_prescription_item_block_prevents_persistence(
    monkeypatch,
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

    before = db.session.query(
        Prescription
    ).count()

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: SimpleNamespace(
            known_interactions=(),
            blocked=False,
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            SimpleNamespace(
                blocked=True,
            ),
        ),
    )

    with pytest.raises(
        ValidationError,
        match="Prescription item 1 blocked",
    ):
        prescription_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dose_value": 750,
                    "dose_unit": "mg",
                    "route_code": "oral",
                    "frequency_code": "TID",
                    "scheduled_times": [
                        "08:00",
                        "14:00",
                        "20:00",
                    ],
                    "is_prn": False,
                    "order_priority": "routine",
                    "order_type": "outpatient",
                }
            ],
        )

    db.session.rollback()

    after = db.session.query(
        Prescription
    ).count()

    assert after == before


def test_prescription_invokes_item_cds_once_for_all_items(
    monkeypatch,
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

    first = make_drug(
        clinic,
        is_active=True,
    )
    second = make_drug(
        clinic,
        is_active=True,
    )

    captured = {
        "combined_calls": 0,
        "item_calls": 0,
    }

    def combined(**_kwargs):
        captured[
            "combined_calls"
        ] += 1
        return SimpleNamespace(
            known_interactions=(),
            blocked=False,
        )

    def per_item(**kwargs):
        captured[
            "item_calls"
        ] += 1
        captured[
            "proposed_medications"
        ] = kwargs[
            "proposed_medications"
        ]
        return (
            SimpleNamespace(
                blocked=False,
            ),
            SimpleNamespace(
                blocked=False,
            ),
        )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        combined,
    )
    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        per_item,
    )

    prescription, warnings = (
        prescription_service
        .create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": first.id,
                    "dose_value": 10,
                    "dose_unit": "mg",
                    "is_prn": False,
                    "order_priority": "routine",
                    "order_type": "outpatient",
                },
                {
                    "drug_id": second.id,
                    "dose_value": 20,
                    "dose_unit": "mg",
                    "is_prn": False,
                    "order_priority": "routine",
                    "order_type": "outpatient",
                },
            ],
        )
    )

    assert prescription.id is not None
    assert warnings == []
    assert captured["combined_calls"] == 1
    assert captured["item_calls"] == 1
    assert len(
        captured[
            "proposed_medications"
        ]
    ) == 2
