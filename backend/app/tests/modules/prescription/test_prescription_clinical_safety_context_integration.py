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


def test_medication_safety_unions_active_context_and_proposed_ids(
    monkeypatch,
):
    captured = {}

    def fake_interactions(
        *,
        drug_ids,
        clinic_id,
    ):
        captured[
            "interaction_ids"
        ] = list(drug_ids)
        captured[
            "clinic_id"
        ] = clinic_id
        return []

    clinical = SimpleNamespace(
        outcome="safe",
        blocked=False,
        requires_acknowledgement=False,
        requires_justification=False,
        to_dict=lambda: {
            "outcome": "safe",
        },
    )

    def fake_rules(
        *,
        clinic_id,
        context,
        department_code=None,
        evaluation_at=None,
    ):
        captured[
            "rule_context"
        ] = dict(context)
        return clinical

    monkeypatch.setattr(
        medication_safety,
        "check_interactions",
        fake_interactions,
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_clinical_rules",
        fake_rules,
    )

    result = (
        medication_safety
        .evaluate_medication_safety(
            clinic_id=7,
            medication_ids=[
                20,
                30,
            ],
            context={
                "patient_id": 9,
                "medication_ids": [
                    10,
                    20,
                ],
            },
        )
    )

    assert (
        captured["interaction_ids"]
        == [
            10,
            20,
            30,
        ]
    )

    assert (
        captured["rule_context"][
            "medication_ids"
        ]
        == [
            10,
            20,
            30,
        ]
    )

    assert (
        captured["rule_context"][
            "proposed_medication_ids"
        ]
        == [
            20,
            30,
        ]
    )

    assert (
        result.medication_ids
        == (
            10,
            20,
            30,
        )
    )


def test_invalid_context_medication_ids_fail_closed(
    monkeypatch,
):
    monkeypatch.setattr(
        medication_safety,
        "check_interactions",
        lambda **_kwargs: [],
    )

    with pytest.raises(
        ValidationError,
        match="Context medication_ids",
    ):
        medication_safety.evaluate_medication_safety(
            clinic_id=1,
            medication_ids=[
                20,
            ],
            context={
                "medication_ids": "10,20",
            },
        )


def test_create_prescription_wires_builder_context_and_shared_timestamp(
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

    drug = make_drug(
        clinic,
        is_active=True,
    )

    captured = {}

    def fake_builder(
        *,
        clinic_id,
        patient_id,
        evaluation_at,
        proposed_medication=None,
    ):
        captured[
            "builder"
        ] = {
            "clinic_id": clinic_id,
            "patient_id": patient_id,
            "evaluation_at": evaluation_at,
            "proposed_medication": proposed_medication,
        }

        return _Context(
            {
                "clinic_id": clinic_id,
                "patient_id": patient_id,
                "medication_ids": [
                    999,
                ],
                "allergies": [
                    {
                        "allergen": "Penicillin",
                    }
                ],
            }
        )

    def fake_evaluate(
        *,
        clinic_id,
        medication_ids,
        context,
        department_code=None,
        evaluation_at=None,
    ):
        captured[
            "evaluation"
        ] = {
            "clinic_id": clinic_id,
            "medication_ids": list(
                medication_ids
            ),
            "context": dict(context),
            "evaluation_at": evaluation_at,
        }

        return SimpleNamespace(
            known_interactions=(),
            blocked=False,
        )

    monkeypatch.setattr(
        context_service,
        "build_clinical_safety_context",
        fake_builder,
    )

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        fake_evaluate,
    )

    prescription, warnings = (
        prescription_service
        .create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dosage": "500 mg",
                    "frequency": "twice daily",
                }
            ],
        )
    )

    assert prescription.id is not None
    assert warnings == []

    assert (
        captured["builder"][
            "clinic_id"
        ]
        == clinic.id
    )

    assert (
        captured["builder"][
            "patient_id"
        ]
        == patient.id
    )

    assert (
        captured["builder"][
            "proposed_medication"
        ]
        is None
    )

    assert (
        captured["evaluation"][
            "medication_ids"
        ]
        == [
            drug.id
        ]
    )

    assert (
        captured["evaluation"][
            "context"
        ][
            "medication_ids"
        ]
        == [
            999
        ]
    )

    assert (
        captured["evaluation"][
            "context"
        ][
            "allergies"
        ][0]["allergen"]
        == "Penicillin"
    )

    assert (
        captured["builder"][
            "evaluation_at"
        ]
        == captured["evaluation"][
            "evaluation_at"
        ]
    )


def test_context_builder_failure_prevents_prescription_persistence(
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

    before = (
        db.session.query(
            Prescription
        ).count()
    )

    def fail_builder(**_kwargs):
        raise RuntimeError(
            "context unavailable"
        )

    monkeypatch.setattr(
        context_service,
        "build_clinical_safety_context",
        fail_builder,
    )

    with pytest.raises(
        RuntimeError,
        match="context unavailable",
    ):
        prescription_service.create_prescription(
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

    db.session.rollback()

    after = (
        db.session.query(
            Prescription
        ).count()
    )

    assert after == before


def test_blocked_safety_evaluation_prevents_prescription_persistence(
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

    before = (
        db.session.query(
            Prescription
        ).count()
    )

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

    monkeypatch.setattr(
        medication_safety,
        "evaluate_medication_safety",
        lambda **_kwargs: SimpleNamespace(
            known_interactions=(),
            blocked=True,
        ),
    )

    with pytest.raises(
        ValidationError,
        match="blocked by clinical safety rules",
    ):
        prescription_service.create_prescription(
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

    db.session.rollback()

    after = (
        db.session.query(
            Prescription
        ).count()
    )

    assert after == before
