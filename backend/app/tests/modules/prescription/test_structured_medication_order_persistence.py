from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.modules.prescription.models.prescription_model import (
    PrescriptionItem,
)
from app.modules.prescription.routes.prescription_routes import (
    _serialize_prescription_item,
)
import app.modules.prescription.services.prescription_service as service


EXPECTED_STRUCTURED_COLUMNS = {
    "dose_value",
    "dose_unit",
    "route_code",
    "route_display",
    "frequency_code",
    "frequency_interval",
    "frequency_interval_unit",
    "scheduled_times",
    "start_at",
    "end_at",
    "is_prn",
    "prn_reason",
    "max_single_dose",
    "max_daily_dose",
    "administration_instructions",
    "order_priority",
    "order_type",
    "ordered_at",
}


@pytest.fixture()
def structured_service(monkeypatch):
    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
    )
    monkeypatch.setattr(
        "app.core.clinical_safety.services.medication_safety_service."
        "evaluate_medication_safety",
        Mock(
            return_value=SimpleNamespace(
                blocked=False,
                known_interactions=(),
            )
        ),
    )
    return service


def _doctor(
    make_authenticated_staff,
    clinic,
):
    staff, _ = make_authenticated_staff(
        clinic,
        Role.DOCTOR,
    )
    return staff


def test_structured_medication_model_columns_and_constraints():
    columns = {
        column.name
        for column in PrescriptionItem.__table__.columns
    }

    assert EXPECTED_STRUCTURED_COLUMNS <= columns

    constraint_names = {
        constraint.name
        for constraint in PrescriptionItem.__table__.constraints
        if constraint.name is not None
    }

    assert {
        "ck_prescription_item_dose_value_positive",
        "ck_prescription_item_dose_pair",
        "ck_prescription_item_frequency_interval_positive",
        "ck_prescription_item_frequency_pair",
        "ck_prescription_item_frequency_interval_unit",
        "ck_prescription_item_route_display_requires_code",
        "ck_prescription_item_max_single_dose_positive",
        "ck_prescription_item_max_daily_dose_positive",
        "ck_prescription_item_max_daily_ge_single",
        "ck_prescription_item_max_dose_requires_unit",
        "ck_prescription_item_prn_reason",
        "ck_prescription_item_time_order",
        "ck_prescription_item_order_priority",
        "ck_prescription_item_order_type",
    } <= constraint_names


def test_create_prescription_persists_structured_medication(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, warnings = structured_service.create_prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        items=[
            {
                "drug_id": drug.id,
                "dosage": "500 mg",
                "frequency": "every 8 hours",
                "duration": "7 days",
                "quantity": 21,
                "instructions": "After food",
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
                "start_at": None,
                "end_at": None,
                "is_prn": False,
                "prn_reason": None,
                "max_single_dose": 500,
                "max_daily_dose": 1500,
                "administration_instructions": "Give after food",
                "order_priority": "urgent",
                "order_type": "inpatient",
            }
        ],
    )

    assert warnings == []
    assert len(prescription.items) == 1

    item = prescription.items[0]
    assert float(item.dose_value) == 500
    assert item.dose_unit == "mg"
    assert item.route_code == "oral"
    assert item.route_display == "Oral"
    assert item.frequency_code == "q8h"
    assert item.frequency_interval == 8
    assert item.frequency_interval_unit == "hour"
    assert item.scheduled_times == ["06:00", "14:00", "22:00"]
    assert item.is_prn is False
    assert item.prn_reason is None
    assert float(item.max_single_dose) == 500
    assert float(item.max_daily_dose) == 1500
    assert item.administration_instructions == "Give after food"
    assert item.order_priority == "urgent"
    assert item.order_type == "inpatient"
    assert item.ordered_at is not None


def test_legacy_prescription_gets_safe_structured_defaults(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )
    drug = make_drug(
        clinic,
        is_active=True,
    )

    prescription, _ = structured_service.create_prescription(
        clinic_id=clinic.id,
        patient_id=patient.id,
        prescribed_by_id=staff.id,
        items=[
            {
                "drug_id": drug.id,
                "dosage": "1 tablet",
                "frequency": "daily",
                "quantity": 7,
            }
        ],
    )

    item = prescription.items[0]
    assert item.dose_value is None
    assert item.dose_unit is None
    assert item.route_code is None
    assert item.frequency_interval is None
    assert item.scheduled_times == []
    assert item.is_prn is False
    assert item.order_priority == "routine"
    assert item.order_type == "outpatient"
    assert item.ordered_at is not None


def test_structured_item_serializer_exposes_contract():
    item = SimpleNamespace(
        id=7,
        prescription_id=3,
        drug_id=11,
        dosage="500 mg",
        frequency="q8h",
        duration="7 days",
        quantity=21,
        instructions="After food",
        dose_value=Decimal("500.0000"),
        dose_unit="mg",
        route_code="oral",
        route_display="Oral",
        frequency_code="q8h",
        frequency_interval=8,
        frequency_interval_unit="hour",
        scheduled_times=["06:00", "14:00", "22:00"],
        start_at=None,
        end_at=None,
        is_prn=False,
        prn_reason=None,
        max_single_dose=Decimal("500.0000"),
        max_daily_dose=Decimal("1500.0000"),
        administration_instructions="Give after food",
        order_priority="urgent",
        order_type="inpatient",
        ordered_at=None,
    )

    payload = _serialize_prescription_item(item)

    assert payload["dose_value"] == 500.0
    assert payload["dose_unit"] == "mg"
    assert payload["route_code"] == "oral"
    assert payload["frequency_interval"] == 8
    assert payload["scheduled_times"] == ["06:00", "14:00", "22:00"]
    assert payload["max_daily_dose"] == 1500.0
    assert payload["order_priority"] == "urgent"
    assert payload["order_type"] == "inpatient"


def test_structured_medication_migration_is_linked_to_previous_head():
    migration_path = Path(
        "backend/migrations/versions/"
        "f3a1c7d9e204_structured_medication_orders.py"
    )

    text = migration_path.read_text(encoding="utf-8")

    assert 'revision = "f3a1c7d9e204"' in text
    assert 'down_revision = "2fcd41e8c6ed"' in text

    for column in EXPECTED_STRUCTURED_COLUMNS:
        assert f'"{column}"' in text
