from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.core.enums.patient_enums import (
    AllergyClinicalStatus,
    AllergySeverity,
    AllergyType,
    AllergyVerificationStatus,
)
from app.modules.patient.schemas.patient_allergy_schema import (
    PatientAllergyCreateSchema,
    PatientAllergyResponseSchema,
    PatientAllergyStatusTransitionSchema,
    PatientAllergyUpdateSchema,
)


def test_allergy_enum_contract():
    assert [
        item.value
        for item in AllergyType
    ] == [
        "drug",
        "food",
        "environmental",
        "latex",
        "biologic",
        "other",
    ]

    assert [
        item.value
        for item in AllergySeverity
    ] == [
        "unknown",
        "mild",
        "moderate",
        "severe",
        "life_threatening",
    ]

    assert [
        item.value
        for item in AllergyClinicalStatus
    ] == [
        "active",
        "inactive",
        "resolved",
    ]

    assert [
        item.value
        for item in AllergyVerificationStatus
    ] == [
        "unconfirmed",
        "confirmed",
        "refuted",
    ]


def test_create_allergy_normalizes_and_defaults():
    payload = PatientAllergyCreateSchema(
        allergen_name="  Penicillin  ",
        allergen_code="  RXNORM:7980  ",
        allergen_type="drug",
        reaction="  urticaria  ",
        notes="  verified history  ",
    )

    dumped = payload.model_dump()

    assert (
        dumped["allergen_name"]
        == "Penicillin"
    )
    assert (
        dumped["allergen_code"]
        == "RXNORM:7980"
    )
    assert (
        dumped["allergen_type"]
        == "drug"
    )
    assert (
        dumped["reaction"]
        == "urticaria"
    )
    assert (
        dumped["severity"]
        == "unknown"
    )
    assert (
        dumped["notes"]
        == "verified history"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("clinic_id", 1),
        ("patient_id", 2),
        ("recorded_by_id", 3),
        (
            "recorded_at",
            "2026-10-30T08:00:00+00:00",
        ),
        ("clinical_status", "active"),
        ("verification_status", "confirmed"),
    ],
)
def test_create_allergy_rejects_server_controlled_fields(
    field,
    value,
):
    data = {
        "allergen_name": "Penicillin",
        field: value,
    }

    with pytest.raises(
        ValidationError,
    ):
        PatientAllergyCreateSchema(
            **data
        )


def test_create_allergy_rejects_blank_name():
    with pytest.raises(
        ValidationError,
    ):
        PatientAllergyCreateSchema(
            allergen_name="   ",
        )


def test_create_allergy_rejects_invalid_code():
    with pytest.raises(
        ValidationError,
    ):
        PatientAllergyCreateSchema(
            allergen_name="Penicillin",
            allergen_code="bad code with spaces",
        )


def test_create_allergy_rejects_naive_onset():
    with pytest.raises(
        ValidationError,
        match="timezone",
    ):
        PatientAllergyCreateSchema(
            allergen_name="Penicillin",
            onset_at=datetime(
                2020,
                1,
                1,
                8,
                0,
            ),
        )


def test_create_allergy_normalizes_onset_to_utc():
    payload = PatientAllergyCreateSchema(
        allergen_name="Penicillin",
        onset_at=datetime.fromisoformat(
            "2020-01-01T09:00:00+01:00"
        ),
    )

    assert (
        payload.onset_at
        == datetime(
            2020,
            1,
            1,
            8,
            0,
            tzinfo=timezone.utc,
        )
    )


def test_update_requires_at_least_one_field():
    with pytest.raises(
        ValidationError,
        match="At least one allergy field",
    ):
        PatientAllergyUpdateSchema()


@pytest.mark.parametrize(
    "field",
    [
        "allergen_name",
        "allergen_type",
    ],
)
def test_update_rejects_null_required_identity_fields(
    field,
):
    with pytest.raises(
        ValidationError,
    ):
        PatientAllergyUpdateSchema(
            **{
                field: None,
            }
        )


def test_update_allows_clearing_optional_details():
    payload = PatientAllergyUpdateSchema(
        reaction=None,
        notes=None,
        onset_at=None,
    )

    assert (
        payload.model_fields_set
        == {
            "reaction",
            "notes",
            "onset_at",
        }
    )


def test_status_transition_requires_status_change():
    with pytest.raises(
        ValidationError,
        match="At least one allergy status",
    ):
        PatientAllergyStatusTransitionSchema(
            reason="reviewed",
        )


def test_status_transition_requires_reason():
    with pytest.raises(
        ValidationError,
    ):
        PatientAllergyStatusTransitionSchema(
            clinical_status="resolved",
            reason="   ",
        )


def test_status_transition_accepts_resolve():
    payload = (
        PatientAllergyStatusTransitionSchema(
            clinical_status="resolved",
            reason="Patient tolerates medication now",
        )
    )

    assert (
        payload.clinical_status
        == "resolved"
    )


def test_status_transition_accepts_refute():
    payload = (
        PatientAllergyStatusTransitionSchema(
            verification_status="refuted",
            reason="Formal challenge negative",
        )
    )

    assert (
        payload.verification_status
        == "refuted"
    )


def test_response_contract_from_attributes():
    class AllergyRecord:
        id = 7
        clinic_id = 1
        patient_id = 2
        allergen_name = "Penicillin"
        allergen_code = "RXNORM:7980"
        allergen_type = AllergyType.DRUG
        reaction = "urticaria"
        severity = AllergySeverity.SEVERE
        clinical_status = (
            AllergyClinicalStatus.ACTIVE
        )
        verification_status = (
            AllergyVerificationStatus.CONFIRMED
        )
        onset_at = None
        recorded_at = datetime(
            2026,
            10,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        )
        recorded_by_id = 3
        notes = None
        created_at = recorded_at
        updated_at = recorded_at

    result = (
        PatientAllergyResponseSchema
        .model_validate(
            AllergyRecord()
        )
        .model_dump()
    )

    assert (
        result["allergen_type"]
        == "drug"
    )
    assert (
        result["severity"]
        == "severe"
    )
    assert (
        result["clinical_status"]
        == "active"
    )
    assert (
        result["verification_status"]
        == "confirmed"
    )
