from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError

import app.core.clinical_safety.services.clinical_safety_context_service as context_service
import app.core.clinical_safety.services.medication_safety_service as medication_safety
import app.modules.prescription.routes.prescription_routes as prescription_routes
import app.modules.prescription.services.prescription_service as prescription_service
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.prescription.models.prescription_model import Prescription
from app.modules.prescription.schemas.prescription_schema import (
    PrescriptionCreateSchema,
)


class _Context:
    def __init__(self, payload):
        self._payload = payload

    def to_dict(self):
        return dict(self._payload)


class _RuleResult:
    def __init__(
        self,
        *,
        outcome,
        matched=True,
        rule_code="test-rule",
    ):
        self.rule_id = 1
        self.rule_code = rule_code
        self.rule_version = 1
        self.outcome = outcome
        self.matched = matched
        self.severity = "high"
        self.action = "test"
        self.reason = "test reason"


class _ClinicalEvaluation:
    def __init__(
        self,
        outcome="safe",
        *,
        results=(),
    ):
        self.outcome = outcome
        self.results = tuple(results)

    @property
    def blocked(self):
        return self.outcome == "blocked"

    @property
    def requires_acknowledgement(self):
        return (
            self.outcome
            == "acknowledgement_required"
        )

    @property
    def requires_justification(self):
        return (
            self.outcome
            == "justification_required"
        )


class _MedicationEvaluation:
    def __init__(
        self,
        outcome="safe",
        *,
        results=(),
        known_interactions=(),
    ):
        self.outcome = outcome
        self.known_interactions = tuple(
            known_interactions
        )
        self.clinical_safety = (
            _ClinicalEvaluation(
                outcome=outcome,
                results=results,
            )
        )

    @property
    def blocked(self):
        return self.outcome == "blocked"

    @property
    def requires_acknowledgement(self):
        return (
            self.outcome
            == "acknowledgement_required"
        )

    @property
    def requires_justification(self):
        return (
            self.outcome
            == "justification_required"
        )


def _activate(clinic, patient):
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


def _patch_context(monkeypatch):
    monkeypatch.setattr(
        context_service,
        "build_clinical_safety_context",
        lambda **kwargs: _Context(
            {
                "clinic_id": kwargs[
                    "clinic_id"
                ],
                "patient_id": kwargs[
                    "patient_id"
                ],
                "medication_ids": [],
            }
        ),
    )


def _patch_evaluations(
    monkeypatch,
    *,
    prescription_evaluation,
    item_evaluation,
):
    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: (
            prescription_evaluation
        ),
    )
    monkeypatch.setattr(
        medication_safety,
        "evaluate_structured_medication_items",
        lambda **_kwargs: (
            item_evaluation,
        ),
    )


def _create(
    *,
    clinic,
    patient,
    staff,
    drug,
    acknowledged=False,
    justification=None,
    idempotency_key=None,
    idempotency_user_id=None,
):
    return (
        prescription_service
        .create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dosage": "5 mg",
                }
            ],
            clinical_safety_acknowledged=acknowledged,
            clinical_safety_justification=justification,
            idempotency_key=idempotency_key,
            idempotency_user_id=idempotency_user_id,
        )
    )


def test_request_schema_accepts_and_normalizes_safety_authorization():
    payload = (
        PrescriptionCreateSchema
        .model_validate(
            {
                "patient_id": 10,
                "items": [
                    {
                        "drug_id": 20,
                        "dosage": "5 mg",
                    }
                ],
                "clinical_safety_acknowledged": True,
                "clinical_safety_justification": "  renal risk accepted  ",
            }
        )
    )

    assert (
        payload.clinical_safety_acknowledged
        is True
    )
    assert (
        payload.clinical_safety_justification
        == "renal risk accepted"
    )

    with pytest.raises(
        PydanticValidationError,
    ):
        PrescriptionCreateSchema.model_validate(
            {
                "patient_id": 10,
                "items": [
                    {
                        "drug_id": 20,
                    }
                ],
                "clinical_safety_acknowledged": "yes",
            }
        )


def test_route_passes_authorization_fields_without_response_change(
    monkeypatch,
    app,
):
    captured = {}

    monkeypatch.setattr(
        prescription_routes,
        "_get_current_clinic_id",
        lambda: 7,
    )
    monkeypatch.setattr(
        prescription_routes,
        "_get_current_staff",
        lambda: SimpleNamespace(
            id=11,
            user_id=13,
        ),
    )

    prescription = SimpleNamespace(
        id=99,
        clinic_id=7,
        patient_id=5,
        consultation_id=None,
        prescribed_by_id=11,
        status=SimpleNamespace(
            value="active"
        ),
        notes=None,
        issued_at=None,
        expires_at=None,
        items=[],
    )

    def fake_create(**kwargs):
        captured.update(kwargs)
        return prescription, []

    monkeypatch.setattr(
        prescription_routes,
        "create_prescription",
        fake_create,
    )

    raw_handler = inspect.unwrap(
        prescription_routes
        .create_prescription_route
    )

    with app.test_request_context(
        "/prescriptions",
        method="POST",
        json={
            "patient_id": 5,
            "items": [
                {
                    "drug_id": 21,
                    "dosage": "5 mg",
                }
            ],
            "clinical_safety_acknowledged": True,
            "clinical_safety_justification": "documented reason",
        },
    ):
        response, status = raw_handler()

    assert status == 201
    assert (
        captured[
            "clinical_safety_acknowledged"
        ]
        is True
    )
    assert (
        captured[
            "clinical_safety_justification"
        ]
        == "documented reason"
    )

    body = response.get_json()

    assert (
        body["interaction_warnings"]
        == []
    )
    assert "clinical_safety" not in body


def test_acknowledgement_required_can_be_satisfied(
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
    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            _MedicationEvaluation(
                "acknowledgement_required"
            )
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
        ),
    )

    prescription, warnings = _create(
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
        acknowledged=True,
    )

    assert prescription.id is not None
    assert warnings == []


def test_justification_required_can_be_satisfied_and_normalized(
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
    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            _MedicationEvaluation(
                "justification_required"
            )
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
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
        justification="  benefit outweighs risk  ",
    )

    assert prescription.id is not None
    assert warnings == []

    authorization = (
        audit_calls[0][
            "new_value"
        ][
            "clinical_safety"
        ][
            "authorization"
        ]
    )

    assert (
        authorization[
            "justification"
        ]
        == "benefit outweighs risk"
    )


def test_blank_justification_still_fails_closed(
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
    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            _MedicationEvaluation(
                "justification_required"
            )
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
        ),
    )

    before = db.session.query(
        Prescription
    ).count()

    with pytest.raises(
        ValidationError,
        match="requires clinical safety justification",
    ):
        _create(
            clinic=clinic,
            patient=patient,
            staff=staff,
            drug=drug,
            justification="   ",
        )

    db.session.rollback()

    assert (
        db.session.query(
            Prescription
        ).count()
        == before
    )


def test_blocked_outcome_cannot_be_overridden(
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
    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            _MedicationEvaluation("blocked")
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
        ),
    )

    before = db.session.query(
        Prescription
    ).count()

    with pytest.raises(
        ValidationError,
        match="blocked by clinical safety rules",
    ):
        _create(
            clinic=clinic,
            patient=patient,
            staff=staff,
            drug=drug,
            acknowledged=True,
            justification="attempted override",
        )

    db.session.rollback()

    assert (
        db.session.query(
            Prescription
        ).count()
        == before
    )


def test_lower_rank_acknowledgement_not_lost_under_justification(
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

    prescription_eval = (
        _MedicationEvaluation(
            "justification_required",
            results=(
                _RuleResult(
                    outcome="justification_required",
                    rule_code="needs-why",
                ),
                _RuleResult(
                    outcome="acknowledgement_required",
                    rule_code="needs-ack",
                ),
            ),
        )
    )

    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            prescription_eval
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
        ),
    )

    before = db.session.query(
        Prescription
    ).count()

    with pytest.raises(
        ValidationError,
        match="requires clinical safety acknowledgement",
    ):
        _create(
            clinic=clinic,
            patient=patient,
            staff=staff,
            drug=drug,
            acknowledged=False,
            justification="valid justification",
        )

    db.session.rollback()

    assert (
        db.session.query(
            Prescription
        ).count()
        == before
    )


def test_authorization_is_part_of_idempotency_fingerprint(
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
    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            _MedicationEvaluation("safe")
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
        ),
    )

    captured = {}
    record = SimpleNamespace(
        entity_type=None,
        entity_id=None,
    )

    def fake_reserve(**kwargs):
        captured.update(kwargs)
        return record, True

    monkeypatch.setattr(
        prescription_service,
        "reserve_idempotency_operation",
        fake_reserve,
    )

    prescription, warnings = _create(
        clinic=clinic,
        patient=patient,
        staff=staff,
        drug=drug,
        acknowledged=True,
        justification="documented reason",
        idempotency_key="gate4-slice4",
        idempotency_user_id=staff.user_id,
    )

    assert prescription.id is not None
    assert warnings == []

    request_payload = (
        captured["request_payload"]
    )

    assert (
        request_payload[
            "clinical_safety_acknowledged"
        ]
        is True
    )
    assert (
        request_payload[
            "clinical_safety_justification"
        ]
        == "documented reason"
    )

    assert (
        record.entity_type
        == "Prescription"
    )
    assert (
        record.entity_id
        == prescription.id
    )


def test_audit_records_both_required_authorizations(
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

    prescription_eval = (
        _MedicationEvaluation(
            "justification_required",
            results=(
                _RuleResult(
                    outcome="justification_required",
                    rule_code="needs-why",
                ),
                _RuleResult(
                    outcome="acknowledgement_required",
                    rule_code="needs-ack",
                ),
            ),
        )
    )

    _patch_evaluations(
        monkeypatch,
        prescription_evaluation=(
            prescription_eval
        ),
        item_evaluation=(
            _ClinicalEvaluation("safe")
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
        acknowledged=True,
        justification="both obligations addressed",
    )

    assert prescription.id is not None
    assert warnings == []

    authorization = (
        audit_calls[0][
            "new_value"
        ][
            "clinical_safety"
        ][
            "authorization"
        ]
    )

    assert authorization == {
        "acknowledgement_required": True,
        "acknowledged": True,
        "justification_required": True,
        "justification": "both obligations addressed",
    }
