from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.prescription_enums import PrescriptionStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.modules.prescription.models.prescription_model import (
    PrescriptionItem,
)
import app.modules.prescription.services.prescription_service as service


@pytest.fixture()
def structured_service(monkeypatch):
    monkeypatch.setattr(
        service,
        "create_audit_log",
        Mock(),
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


def _base_setup(
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

    return staff, drug


def test_service_accepts_valid_structured_item(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    staff, drug = _base_setup(
        clinic,
        patient,
        make_authenticated_staff,
        make_drug,
    )

    start_at = datetime.now(
        timezone.utc
    )

    prescription, _ = (
        structured_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "dose_value": 500.0,
                    "dose_unit": "mg",
                    "route_code": "oral",
                    "frequency_interval": 8,
                    "frequency_interval_unit": "hour",
                    "scheduled_times": [
                        "06:00",
                        "14:00",
                        "22:00",
                    ],
                    "start_at": start_at,
                    "end_at": (
                        start_at
                        + timedelta(
                            days=7
                        )
                    ),
                    "max_single_dose": 500.0,
                    "max_daily_dose": 1500.0,
                    "order_priority": "urgent",
                    "order_type": "inpatient",
                }
            ],
        )
    )

    assert (
        prescription.items[0].dose_unit
        == "mg"
    )


@pytest.mark.parametrize(
    "updates,match",
    [
        (
            {
                "dose_value": 500.0,
            },
            "dose_value and dose_unit",
        ),
        (
            {
                "frequency_interval": 8,
            },
            "frequency_interval and frequency_interval_unit",
        ),
        (
            {
                "frequency_interval": 8,
                "frequency_interval_unit": "month",
            },
            "frequency_interval_unit",
        ),
        (
            {
                "route_display": "Oral",
            },
            "requires route_code",
        ),
        (
            {
                "is_prn": True,
            },
            "requires prn_reason",
        ),
        (
            {
                "is_prn": False,
                "prn_reason": "pain",
            },
            "requires is_prn=true",
        ),
        (
            {
                "is_prn": True,
                "prn_reason": "pain",
                "scheduled_times": [
                    "08:00",
                ],
            },
            "cannot define fixed scheduled_times",
        ),
        (
            {
                "dose_value": 500.0,
                "dose_unit": "mg",
                "max_single_dose": 500.0,
                "max_daily_dose": 250.0,
            },
            "cannot be less than",
        ),
        (
            {
                "order_priority": "whenever",
            },
            "order_priority",
        ),
        (
            {
                "order_type": "mystery",
            },
            "order_type",
        ),
    ],
)
def test_service_rejects_invalid_structured_item(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    updates,
    match,
):
    staff, drug = _base_setup(
        clinic,
        patient,
        make_authenticated_staff,
        make_drug,
    )

    item = {
        "drug_id": drug.id,
    }

    item.update(
        updates
    )

    with pytest.raises(
        ValidationError,
        match=match,
    ):
        structured_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                item
            ],
        )


def test_service_rejects_naive_structured_datetime(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    staff, drug = _base_setup(
        clinic,
        patient,
        make_authenticated_staff,
        make_drug,
    )

    with pytest.raises(
        ValidationError,
        match="must include timezone",
    ):
        structured_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "start_at": datetime.now(),
                }
            ],
        )


def test_service_rejects_end_before_start(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
):
    staff, drug = _base_setup(
        clinic,
        patient,
        make_authenticated_staff,
        make_drug,
    )

    start_at = datetime.now(
        timezone.utc
    )

    with pytest.raises(
        ValidationError,
        match="cannot be earlier",
    ):
        structured_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "start_at": start_at,
                    "end_at": (
                        start_at
                        - timedelta(
                            minutes=1
                        )
                    ),
                }
            ],
        )


@pytest.mark.parametrize(
    "scheduled_times",
    [
        [
            "25:00",
        ],
        [
            "08:00",
            "08:00",
        ],
    ],
)
def test_service_rejects_invalid_schedule(
    structured_service,
    clinic,
    patient,
    make_authenticated_staff,
    make_drug,
    scheduled_times,
):
    staff, drug = _base_setup(
        clinic,
        patient,
        make_authenticated_staff,
        make_drug,
    )

    with pytest.raises(
        ValidationError,
    ):
        structured_service.create_prescription(
            clinic_id=clinic.id,
            patient_id=patient.id,
            prescribed_by_id=staff.id,
            items=[
                {
                    "drug_id": drug.id,
                    "scheduled_times": (
                        scheduled_times
                    ),
                }
            ],
        )


def _direct_item_context(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    make_drug,
):
    clinic.status = ClinicStatus.ACTIVE

    staff = _doctor(
        make_authenticated_staff,
        clinic,
    )

    prescription = make_prescription(
        clinic,
        patient,
        staff,
        status=PrescriptionStatus.ACTIVE,
    )

    drug = make_drug(
        clinic,
        is_active=True,
    )

    db.session.flush()

    return prescription, drug


def test_db_rejects_negative_dose_value(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    make_drug,
):
    prescription, drug = _direct_item_context(
        db,
        clinic,
        patient,
        make_authenticated_staff,
        make_prescription,
        make_drug,
    )

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=-1,
        dose_unit="mg",
        is_prn=False,
        order_priority="routine",
        order_type="inpatient",
    )

    db.session.add(
        item
    )

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_db_rejects_unpaired_structured_dose(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    make_drug,
):
    prescription, drug = _direct_item_context(
        db,
        clinic,
        patient,
        make_authenticated_staff,
        make_prescription,
        make_drug,
    )

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=500,
        dose_unit=None,
        is_prn=False,
        order_priority="routine",
        order_type="inpatient",
    )

    db.session.add(
        item
    )

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_db_rejects_invalid_max_dose_order(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    make_drug,
):
    prescription, drug = _direct_item_context(
        db,
        clinic,
        patient,
        make_authenticated_staff,
        make_prescription,
        make_drug,
    )

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        dose_value=500,
        dose_unit="mg",
        max_single_dose=500,
        max_daily_dose=250,
        is_prn=False,
        order_priority="routine",
        order_type="inpatient",
    )

    db.session.add(
        item
    )

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()


def test_db_rejects_invalid_order_priority(
    db,
    clinic,
    patient,
    make_authenticated_staff,
    make_prescription,
    make_drug,
):
    prescription, drug = _direct_item_context(
        db,
        clinic,
        patient,
        make_authenticated_staff,
        make_prescription,
        make_drug,
    )

    item = PrescriptionItem(
        prescription_id=prescription.id,
        drug_id=drug.id,
        is_prn=False,
        order_priority="whenever",
        order_type="inpatient",
    )

    db.session.add(
        item
    )

    with pytest.raises(
        IntegrityError,
    ):
        db.session.flush()

    db.session.rollback()
