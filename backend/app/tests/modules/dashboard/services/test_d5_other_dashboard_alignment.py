from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
import pytest

from app.core.enums.role_enums import Role
from app.modules.dashboard.schemas.finance_dashboard_schema import (
    FinanceDashboardSchema,
)
from app.modules.dashboard.schemas.operations_dashboard_schema import (
    OperationsDashboardSchema,
    OperationsHospitalCapacitySchema,
)
from app.modules.dashboard.schemas.patient_dashboard_schema import (
    PatientDashboardOverviewSchema,
    PatientDashboardSchema,
)
from app.modules.dashboard.schemas.super_admin_dashboard_schema import (
    SuperAdminDashboardSchema,
)
from app.modules.dashboard.services import operations_dashboard_service as service


CAPACITY = {
    "active_admissions": 7,
    "total_beds": 20,
    "occupied_beds": 16,
    "available_beds": 4,
}


def test_operations_hospital_capacity_schema_is_strict():
    with pytest.raises(PydanticValidationError):
        OperationsHospitalCapacitySchema.model_validate(
            {
                **CAPACITY,
                "patient_id": 99,
            }
        )


def test_receptionist_gets_ward_capacity_from_shared_builder(monkeypatch):
    calls = []

    monkeypatch.setattr(
        service,
        "build_admission_aggregates",
        lambda **kwargs: calls.append(kwargs) or CAPACITY,
    )

    capacity = service._build_operations_hospital_capacity(
        role=Role.RECEPTIONIST,
        clinic_id=7,
    )

    assert calls == [{"clinic_id": 7}]
    assert capacity is not None
    assert capacity.active_admissions == 7
    assert capacity.total_beds == 20
    assert capacity.occupied_beds == 16
    assert capacity.available_beds == 4


@pytest.mark.parametrize(
    "role",
    [
        Role.DRIVER,
        Role.AMBULANCE_DISPATCHER,
        Role.AMBULANCE_COORDINATOR,
        Role.OTHER,
    ],
)
def test_non_receptionist_operations_roles_do_not_receive_ward_capacity(
    monkeypatch,
    role,
):
    def forbidden_builder(**_):
        raise AssertionError("ward capacity builder must not be called")

    monkeypatch.setattr(
        service,
        "build_admission_aggregates",
        forbidden_builder,
    )

    assert (
        service._build_operations_hospital_capacity(
            role=role,
            clinic_id=7,
        )
        is None
    )


def test_receptionist_capacity_presentation_is_aggregate_only():
    capacity = OperationsHospitalCapacitySchema(
        **CAPACITY,
    )

    metrics, alerts = service._operations_hospital_presentation(
        capacity=capacity,
    )

    metric_keys = {metric.key for metric in metrics}
    assert metric_keys == {
        "hospital_active_admissions",
        "hospital_available_beds",
    }

    assert alerts == []

    payload = capacity.model_dump()
    assert set(payload) == {
        "active_admissions",
        "total_beds",
        "occupied_beds",
        "available_beds",
    }


def test_operations_capacity_alerts_when_no_beds_are_available():
    capacity = OperationsHospitalCapacitySchema(
        active_admissions=12,
        total_beds=12,
        occupied_beds=12,
        available_beds=0,
    )

    _metrics, alerts = service._operations_hospital_presentation(
        capacity=capacity,
    )

    assert len(alerts) == 1
    assert alerts[0].key == "hospital_bed_capacity"
    assert alerts[0].severity == "critical"


def test_get_operations_dashboard_integrates_capacity_for_receptionist(
    clinic,
    make_user,
    monkeypatch,
):
    actor = make_user(
        clinic,
        role=Role.RECEPTIONIST,
    )

    calls = []

    monkeypatch.setattr(
        service,
        "build_admission_aggregates",
        lambda **kwargs: calls.append(kwargs) or CAPACITY,
    )

    dashboard = service.get_operations_dashboard(
        actor=actor,
    )

    assert calls == [{"clinic_id": clinic.id}]
    assert dashboard.hospital_capacity is not None
    assert dashboard.hospital_capacity.available_beds == 4

    metric_keys = {
        metric.key
        for metric in dashboard.metrics
    }

    assert {
        "hospital_active_admissions",
        "hospital_available_beds",
    }.issubset(metric_keys)


def test_other_dashboards_do_not_gain_hospital_clinical_sections():
    forbidden = {
        "hospital",
        "hospital_capacity",
        "nursing",
        "emar",
        "medication_reconciliation",
    }

    assert forbidden.isdisjoint(
        SuperAdminDashboardSchema.model_fields
    )
    assert forbidden.isdisjoint(
        FinanceDashboardSchema.model_fields
    )
    assert forbidden.isdisjoint(
        PatientDashboardSchema.model_fields
    )


def test_patient_dashboard_keeps_personal_active_admission_summary():
    assert "active_admissions" in (
        PatientDashboardOverviewSchema.model_fields
    )


def test_operations_schema_adds_only_optional_capacity_boundary():
    fields = OperationsDashboardSchema.model_fields

    assert "hospital_capacity" in fields
    assert fields["hospital_capacity"].default is None
