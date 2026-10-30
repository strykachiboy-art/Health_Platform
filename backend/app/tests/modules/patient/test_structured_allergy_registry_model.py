from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.modules.patient.models.patient_model import (
    PatientAllergy,
)


EXPECTED_COLUMNS = {
    "id",
    "clinic_id",
    "patient_id",
    "allergen_name",
    "allergen_code",
    "allergen_type",
    "reaction",
    "severity",
    "clinical_status",
    "verification_status",
    "onset_at",
    "recorded_at",
    "recorded_by_id",
    "notes",
    "created_at",
    "updated_at",
}


EXPECTED_CONSTRAINTS = {
    "ck_patient_allergies_name_nonempty",
    "ck_patient_allergies_type",
    "ck_patient_allergies_severity",
    "ck_patient_allergies_clinical_status",
    "ck_patient_allergies_verification_status",
}


def test_patient_allergy_model_contract():
    columns = {
        column.name
        for column in PatientAllergy.__table__.columns
    }

    assert EXPECTED_COLUMNS == columns

    constraint_names = {
        constraint.name
        for constraint in PatientAllergy.__table__.constraints
        if constraint.name is not None
    }

    assert EXPECTED_CONSTRAINTS <= constraint_names


def test_patient_exposes_allergy_registry_relationship(
    patient,
):
    assert hasattr(
        patient,
        "allergy_records",
    )


def test_patient_allergy_defaults_persist(
    db,
    clinic,
    patient,
):
    record = PatientAllergy(
        clinic_id=clinic.id,
        patient_id=patient.id,
        allergen_name="Penicillin",
    )

    db.session.add(record)
    db.session.flush()

    assert record.id is not None
    assert record.allergen_type == "other"
    assert record.severity == "unknown"
    assert record.clinical_status == "active"
    assert record.verification_status == "unconfirmed"
    assert record.recorded_at is not None
    assert record.created_at is not None
    assert record.updated_at is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("allergen_name", "   "),
        ("allergen_type", "mystery"),
        ("severity", "catastrophic"),
        ("clinical_status", "deleted"),
        ("verification_status", "magic"),
    ],
)
def test_patient_allergy_db_constraints_reject_invalid_values(
    db,
    clinic,
    patient,
    field,
    value,
):
    kwargs = {
        "clinic_id": clinic.id,
        "patient_id": patient.id,
        "allergen_name": "Penicillin",
    }

    kwargs[field] = value

    record = PatientAllergy(**kwargs)
    db.session.add(record)

    with pytest.raises(IntegrityError):
        db.session.flush()

    db.session.rollback()


def test_legacy_patient_allergies_text_remains_available(
    patient,
):
    patient.allergies = "Legacy free-text allergy history"

    assert (
        patient.allergies
        == "Legacy free-text allergy history"
    )


def test_structured_allergy_migration_contract():
    path = Path(
        "backend/migrations/versions/"
        "b7c2e1a4d905_structured_patient_allergies.py"
    )

    text = path.read_text(encoding="utf-8")

    assert 'revision = "b7c2e1a4d905"' in text
    assert 'down_revision = "f3a1c7d9e204"' in text
    assert '"patient_allergies"' in text

    for column in EXPECTED_COLUMNS:
        assert f'"{column}"' in text

    for constraint in EXPECTED_CONSTRAINTS:
        assert f'"{constraint}"' in text
