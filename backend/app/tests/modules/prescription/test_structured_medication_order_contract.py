from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.prescription.schemas.prescription_schema import (
    PrescriptionItemSchema,
)


def test_legacy_medication_item_remains_valid():
    item = PrescriptionItemSchema.model_validate(
        {
            "drug_id": 10,
            "dosage": "500 mg",
            "frequency": "twice daily",
            "duration": "5 days",
            "quantity": 10,
            "instructions": "after food",
        }
    )

    assert item.drug_id == 10
    assert item.dosage == "500 mg"
    assert item.order_priority == "routine"
    assert item.order_type == "outpatient"
    assert item.is_prn is False
    assert item.scheduled_times == []


def test_full_structured_medication_item_is_valid():
    item = PrescriptionItemSchema.model_validate(
        {
            "drug_id": 10,
            "dosage": "500 mg",
            "frequency": "every 8 hours",
            "duration": "7 days",
            "quantity": 21,
            "dose_value": 500,
            "dose_unit": "mg",
            "route_code": "oral",
            "route_display": "Oral",
            "frequency_code": "q8h",
            "frequency_interval": 8,
            "frequency_interval_unit": "hour",
            "scheduled_times": [
                "06:00",
                "14:00",
                "22:00",
            ],
            "start_at": "2026-10-30T08:00:00+01:00",
            "end_at": "2026-11-06T08:00:00+01:00",
            "max_single_dose": 500,
            "max_daily_dose": 1500,
            "administration_instructions": "Give after food",
            "order_priority": "urgent",
            "order_type": "inpatient",
        }
    )

    assert item.dose_value == 500
    assert item.dose_unit == "mg"
    assert item.route_code == "oral"
    assert item.frequency_interval == 8
    assert item.frequency_interval_unit == "hour"
    assert item.order_priority == "urgent"
    assert item.order_type == "inpatient"

    assert item.start_at == datetime(
        2026,
        10,
        30,
        7,
        0,
        tzinfo=timezone.utc,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "drug_id": 1,
            "dose_value": 500,
        },
        {
            "drug_id": 1,
            "dose_unit": "mg",
        },
    ],
)
def test_structured_dose_requires_value_and_unit(
    payload,
):
    with pytest.raises(
        ValidationError,
        match="dose_value and dose_unit",
    ):
        PrescriptionItemSchema.model_validate(
            payload
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "drug_id": 1,
            "frequency_interval": 8,
        },
        {
            "drug_id": 1,
            "frequency_interval_unit": "hour",
        },
    ],
)
def test_frequency_interval_requires_value_and_unit(
    payload,
):
    with pytest.raises(
        ValidationError,
        match=(
            "frequency_interval and "
            "frequency_interval_unit"
        ),
    ):
        PrescriptionItemSchema.model_validate(
            payload
        )


def test_prn_requires_reason():
    with pytest.raises(
        ValidationError,
        match="PRN medication requires prn_reason",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "is_prn": True,
            }
        )


def test_prn_cannot_have_fixed_schedule():
    with pytest.raises(
        ValidationError,
        match="cannot define fixed scheduled_times",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "is_prn": True,
                "prn_reason": "breakthrough pain",
                "scheduled_times": [
                    "08:00",
                ],
            }
        )


def test_prn_reason_requires_prn_flag():
    with pytest.raises(
        ValidationError,
        match="prn_reason requires is_prn=true",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "prn_reason": "pain",
            }
        )


def test_invalid_scheduled_clock_time_is_rejected():
    with pytest.raises(
        ValidationError,
        match="invalid clock time",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "scheduled_times": [
                    "25:00",
                ],
            }
        )


def test_duplicate_scheduled_time_is_rejected():
    with pytest.raises(
        ValidationError,
        match="cannot contain duplicates",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "scheduled_times": [
                    "08:00",
                    "08:00",
                ],
            }
        )


def test_end_at_cannot_precede_start_at():
    with pytest.raises(
        ValidationError,
        match="end_at cannot be earlier",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "start_at": "2026-10-31T10:00:00+01:00",
                "end_at": "2026-10-30T10:00:00+01:00",
            }
        )


def test_structured_timestamps_require_timezone():
    with pytest.raises(
        ValidationError,
        match="must include timezone",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "start_at": "2026-10-30T10:00:00",
            }
        )


def test_max_daily_dose_cannot_be_below_single_dose():
    with pytest.raises(
        ValidationError,
        match=(
            "max_daily_dose cannot be less "
            "than max_single_dose"
        ),
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "dose_value": 500,
                "dose_unit": "mg",
                "max_single_dose": 500,
                "max_daily_dose": 250,
            }
        )


def test_max_dose_requires_structured_dose_unit():
    with pytest.raises(
        ValidationError,
        match="Maximum dose limits require",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "max_daily_dose": 1000,
            }
        )


def test_route_display_requires_route_code():
    with pytest.raises(
        ValidationError,
        match="route_display requires route_code",
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "route_display": "Oral",
            }
        )


def test_invalid_priority_is_rejected():
    with pytest.raises(
        ValidationError,
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "order_priority": "whenever",
            }
        )


def test_invalid_order_type_is_rejected():
    with pytest.raises(
        ValidationError,
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "order_type": "unknown",
            }
        )


def test_unknown_structured_field_is_rejected():
    with pytest.raises(
        ValidationError,
    ):
        PrescriptionItemSchema.model_validate(
            {
                "drug_id": 1,
                "magic_dose": 10,
            }
        )
