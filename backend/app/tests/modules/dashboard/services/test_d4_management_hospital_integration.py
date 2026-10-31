from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
import pytest

from app.core.enums.role_enums import Role
from app.modules.dashboard.schemas.management_dashboard_schema import (
    ManagementHospitalOperationsSchema,
)
from app.modules.dashboard.services import management_dashboard_service as service


HOSPITAL_DATA = {
    "admissions": {
        "active_admissions": 7,
        "total_beds": 20,
        "occupied_beds": 16,
        "available_beds": 4,
    },
    "nursing": {
        "active_care_plans": 9,
        "pending_tasks": 11,
        "in_progress_tasks": 5,
        "urgent_open_tasks": 2,
        "overdue_open_tasks": 3,
        "unsigned_assessments": 4,
        "ready_handoffs": 2,
    },
    "emar": {
        "scheduled": 14,
        "due": 6,
        "administered": 24,
        "held": 2,
        "refused": 1,
        "missed": 3,
    },
    "medication_reconciliation": {
        "draft": 8,
        "final": 12,
        "amended": 2,
        "draft_discharge": 3,
        "finalized_discharge": 5,
    },
}


def test_management_hospital_schema_is_strict():
    with pytest.raises(PydanticValidationError):
        ManagementHospitalOperationsSchema.model_validate(
            {
                "admissions": HOSPITAL_DATA["admissions"],
                "nursing": HOSPITAL_DATA["nursing"],
                "emar": HOSPITAL_DATA["emar"],
                "medication_reconciliation": (
                    HOSPITAL_DATA["medication_reconciliation"]
                ),
                "patient_id": 99,
            }
        )


def test_management_hospital_builder_reuses_shared_read_model(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **kwargs: calls.append(kwargs) or HOSPITAL_DATA,
    )

    hospital = service._build_management_hospital_operations(
        clinic_id=7,
    )

    assert calls == [{"clinic_id": 7}]
    assert hospital.admissions.active_admissions == 7
    assert hospital.admissions.available_beds == 4
    assert hospital.nursing.urgent_open_tasks == 2
    assert hospital.emar.missed == 3
    assert hospital.medication_reconciliation.draft_discharge == 3


def test_management_hospital_section_contains_aggregates_only(monkeypatch):
    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    hospital = service._build_management_hospital_operations(
        clinic_id=7,
    )

    payload = hospital.model_dump()

    assert set(payload) == {
        "admissions",
        "nursing",
        "emar",
        "medication_reconciliation",
    }

    forbidden = {
        "patient_id",
        "patient_name",
        "barcode",
        "medication_name",
        "note_text",
        "reason",
    }

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                assert key not in forbidden
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)


def test_management_hospital_presentation_adds_operational_metrics_and_alerts(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    hospital = service._build_management_hospital_operations(
        clinic_id=7,
    )

    metrics, alerts = service._management_hospital_presentation(
        hospital=hospital,
    )

    metric_keys = {metric.key for metric in metrics}
    alert_keys = {alert.key for alert in alerts}

    assert metric_keys == {
        "hospital_available_beds",
        "urgent_nursing_tasks",
        "emar_due",
        "draft_discharge_reconciliations",
    }

    assert alert_keys == {
        "urgent_nursing_tasks",
        "overdue_nursing_tasks",
        "missed_medication_administrations",
        "draft_discharge_reconciliations",
    }


def test_get_management_dashboard_integrates_hospital_operations(
    clinic,
    make_user,
    monkeypatch,
):
    actor = make_user(
        clinic,
        role=Role.ADMIN,
    )

    calls = []

    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **kwargs: calls.append(kwargs) or HOSPITAL_DATA,
    )

    dashboard = service.get_management_dashboard(
        actor=actor,
    )

    assert calls == [{"clinic_id": clinic.id}]
    assert dashboard.hospital.admissions.total_beds == 20
    assert dashboard.hospital.nursing.pending_tasks == 11
    assert dashboard.hospital.emar.due == 6
    assert (
        dashboard.hospital.medication_reconciliation.draft_discharge
        == 3
    )

    assert {
        metric.key
        for metric in dashboard.metrics
    }.issuperset(
        {
            "hospital_available_beds",
            "urgent_nursing_tasks",
            "emar_due",
            "draft_discharge_reconciliations",
        }
    )
