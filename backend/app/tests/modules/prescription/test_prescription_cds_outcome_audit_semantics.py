from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.core.clinical_safety.services.clinical_safety_context_service as context_service
import app.core.clinical_safety.services.medication_safety_service as medication_safety
import app.modules.prescription.services.prescription_service as prescription_service
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.prescription.models.prescription_model import Prescription


class _Context:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _RuleResult:
    def __init__(
        self,
        *,
        rule_id=1,
        rule_code="test-rule",
        outcome="warning",
        matched=True,
        severity="moderate",
        action="alert",
        reason="test reason",
    ):
        self.rule_id = rule_id
        self.rule_code = rule_code
        self.rule_version = 1
        self.outcome = outcome
        self.matched = matched
        self.severity = severity
        self.action = action
        self.reason = reason


class _ClinicalEvaluation:
    def __init__(self, outcome="safe", *, results=()):
        self.outcome = outcome
        self.results = tuple(results)

    @property
    def blocked(self):
        return self.outcome == "blocked"

    @property
    def requires_acknowledgement(self):
        return self.outcome == "acknowledgement_required"

    @property
    def requires_justification(self):
        return self.outcome == "justification_required"


class _MedicationEvaluation:
    def __init__(
        self,
        outcome="safe",
        *,
        known_interactions=(),
        results=(),
    ):
        self.outcome = outcome
        self.known_interactions = tuple(known_interactions)
        self.clinical_safety = _ClinicalEvaluation(
            outcome=outcome,
            results=results,
        )

    @property
    def blocked(self):
        return self.outcome == "blocked"

    @property
    def requires_acknowledgement(self):
        return self.outcome == "acknowledgement_required"

    @property
    def requires_justification(self):
        return self.outcome == "justification_required"


def _activate(clinic, patient):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True


def _doctor(make_authenticated_staff, clinic):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    return staff


def _patch_context(monkeypatch):
    monkeypatch.setattr(
        context_service,
        "build_clinical_safety_context",
        lambda **kwargs: _Context(
            {
                "clinic_id": kwargs["clinic_id"],
                "patient_id": kwargs["patient_id"],
                "medication_ids": [],
            }
        ),
    )


def _create(*, clinic, patient, staff, drug):
    return prescription_service.create_prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        items=[
            {
                "drug_id": drug.id,
                "dosage": "5 mg",
            }
        ],
    )


@pytest.mark.parametrize(
    (
        "prescription_outcome",
        "item_outcome",
        "message",
    ),
    [
        (
            "acknowledgement_required",
            "safe",
            "requires clinical safety acknowledgement",
        ),
        (
            "justification_required",
            "safe",
            "requires clinical safety justification",
        ),
        (
            "safe",
            "acknowledgement_required",
            "Prescription item 1 requires clinical safety acknowledgement",
        ),
        (
            "safe",
            "justification_required",
            "Prescription item 1 requires clinical safety justification",
        ),
    ],
)
def test_required_actions_fail_closed_before_persistence(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    prescription_outcome,
    item_outcome,
    message,
):
    _activate(clinic, patient)

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    _patch_context(monkeypatch)

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: _MedicationEvaluation(
            prescription_outcome
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            _ClinicalEvaluation(
                item_outcome
            ),
        ),
    )

    before = db.session.query(
        Prescription
    ).count()

    with pytest.raises(
        ValidationError,
        match=message,
    ):
        _create(
            clinic=clinic,
            patient=patient,
            staff=staff,
            drug=drug,
        )

    db.session.rollback()

    after = db.session.query(
        Prescription
    ).count()

    assert after == before


def test_item_block_remains_higher_priority_than_acknowledgement(
    monkeypatch,
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(clinic, patient)

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    _patch_context(monkeypatch)

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: _MedicationEvaluation(
            "acknowledgement_required"
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            _ClinicalEvaluation(
                "blocked"
            ),
        ),
    )

    before = db.session.query(
        Prescription
    ).count()

    with pytest.raises(
        ValidationError,
        match="Prescription item 1 blocked",
    ):
        _create(
            clinic=clinic,
            patient=patient,
            staff=staff,
            drug=drug,
        )

    db.session.rollback()

    assert (
        db.session.query(
            Prescription
        ).count()
        == before
    )


@pytest.mark.parametrize(
    "outcome",
    [
        "warning",
        "information",
    ],
)
def test_nonblocking_clinical_outcomes_allow_persistence(
    monkeypatch,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    outcome,
):
    _activate(clinic, patient)

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    _patch_context(monkeypatch)

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: _MedicationEvaluation(
            outcome
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            _ClinicalEvaluation(
                "safe"
            ),
        ),
    )

    prescription, warnings = _create(
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
    )

    assert prescription.id is not None
    assert warnings == []


def test_legacy_interaction_warnings_remain_unchanged(
    monkeypatch,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(clinic, patient)

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    _patch_context(monkeypatch)

    interaction = {
        "drug_a_id": 1,
        "drug_b_id": 2,
        "severity": "moderate",
        "description": "legacy warning",
    }

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: _MedicationEvaluation(
            "warning",
            known_interactions=(
                interaction,
            ),
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            _ClinicalEvaluation(
                "information"
            ),
        ),
    )

    prescription, warnings = _create(
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
    )

    assert prescription.id is not None
    assert warnings == [interaction]


def test_success_audit_contains_compact_clinical_safety_summary(
    monkeypatch,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    _activate(clinic, patient)

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    _patch_context(monkeypatch)

    prescription_rule = _RuleResult(
        rule_id=11,
        rule_code="renal-warning",
        outcome="warning",
        reason="renal warning",
    )

    item_rule = _RuleResult(
        rule_id=12,
        rule_code="dose-info",
        outcome="information",
        severity="info",
        action="inform",
        reason="dose information",
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: _MedicationEvaluation(
            "warning",
            results=(
                prescription_rule,
            ),
        ),
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            _ClinicalEvaluation(
                "information",
                results=(
                    item_rule,
                ),
            ),
        ),
    )

    audit_calls = []

    monkeypatch.setattr(
        prescription_service,
        "create_audit_log",
        lambda **kwargs: audit_calls.append(
            kwargs
        ),
    )

    prescription, warnings = _create(
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
    )

    assert prescription.id is not None
    assert warnings == []
    assert len(audit_calls) == 1

    payload = audit_calls[0][
        "new_value"
    ][
        "clinical_safety"
    ]

    assert (
        payload["overall_outcome"]
        == "warning"
    )

    assert (
        payload["prescription_level"][
            "outcome"
        ]
        == "warning"
    )

    assert (
        payload["prescription_level"][
            "matched_rules"
        ][0][
            "rule_code"
        ]
        == "renal-warning"
    )

    assert (
        payload["items"][0][
            "drug_id"
        ]
        == drug.id
    )

    assert (
        payload["items"][0][
            "outcome"
        ]
        == "information"
    )

    assert (
        payload["items"][0][
            "matched_rules"
        ][0][
            "rule_code"
        ]
        == "dose-info"
    )

    assert "evaluated_at" in payload
    assert "context" not in payload
    assert "patient_id" not in payload
