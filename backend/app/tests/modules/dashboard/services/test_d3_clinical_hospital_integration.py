from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
import pytest

from app.core.enums.role_enums import Role
from app.modules.dashboard.schemas.clinical_dashboard_schema import (
    ClinicalHospitalDashboardSchema,
)
from app.modules.dashboard.services import clinical_dashboard_service as service


HOSPITAL_DATA = {
    "admissions": {
        "active_admissions": 4,
        "total_beds": 12,
        "occupied_beds": 7,
        "available_beds": 5,
    },
    "nursing": {
        "active_care_plans": 3,
        "pending_tasks": 6,
        "in_progress_tasks": 2,
        "urgent_open_tasks": 1,
        "overdue_open_tasks": 2,
        "unsigned_assessments": 1,
        "ready_handoffs": 2,
    },
    "emar": {
        "scheduled": 8,
        "due": 3,
        "administered": 10,
        "held": 1,
        "refused": 1,
        "missed": 2,
    },
    "medication_reconciliation": {
        "draft": 5,
        "final": 8,
        "amended": 1,
        "draft_discharge": 2,
        "finalized_discharge": 4,
    },
}

RECONCILIATION_DATA = HOSPITAL_DATA["medication_reconciliation"]


def test_doctor_and_nurse_receive_authorized_hospital_sections(monkeypatch):
    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    for role in (Role.DOCTOR, Role.NURSE):
        hospital = service._build_hospital_dashboard(
            role=role,
            clinic_id=7,
        )

        assert hospital is not None
        assert hospital.admissions is not None
        assert hospital.nursing is not None
        assert hospital.emar is not None
        assert hospital.medication_reconciliation is not None


def test_pharmacist_gets_reconciliation_without_nursing_or_emar(monkeypatch):
    full_builder_calls = []

    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **kwargs: full_builder_calls.append(kwargs),
    )
    monkeypatch.setattr(
        service,
        "build_reconciliation_aggregates",
        lambda **_: RECONCILIATION_DATA,
    )

    hospital = service._build_hospital_dashboard(
        role=Role.PHARMACIST,
        clinic_id=7,
    )

    assert full_builder_calls == []
    assert hospital is not None
    assert hospital.admissions is None
    assert hospital.nursing is None
    assert hospital.emar is None
    assert hospital.medication_reconciliation is not None


@pytest.mark.parametrize(
    "role",
    [
        Role.LAB_TECHNICIAN,
        Role.PARAMEDIC,
        Role.EMT,
    ],
)
def test_other_clinical_roles_do_not_receive_restricted_hospital_sections(
    monkeypatch,
    role,
):
    def forbidden_builder(**_):
        raise AssertionError("hospital builder must not be called")

    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        forbidden_builder,
    )
    monkeypatch.setattr(
        service,
        "build_reconciliation_aggregates",
        forbidden_builder,
    )

    assert (
        service._build_hospital_dashboard(
            role=role,
            clinic_id=7,
        )
        is None
    )


def test_role_specific_workflow_metrics_and_alerts(monkeypatch):
    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    nurse_hospital = service._build_hospital_dashboard(
        role=Role.NURSE,
        clinic_id=7,
    )

    metrics, alerts = service._hospital_workflow_presentation(
        role=Role.NURSE,
        hospital=nurse_hospital,
    )

    metric_keys = {metric.key for metric in metrics}
    alert_keys = {alert.key for alert in alerts}

    assert metric_keys == {
        "pending_nursing_tasks",
        "emar_due",
    }
    assert alert_keys == {
        "urgent_nursing_tasks",
        "overdue_nursing_tasks",
        "missed_medication_administrations",
    }


def test_clinical_hospital_schema_is_strict():
    with pytest.raises(PydanticValidationError):
        ClinicalHospitalDashboardSchema.model_validate(
            {
                "unexpected": 1,
            }
        )


def test_get_clinical_dashboard_integrates_hospital_section(
    clinic,
    make_staff,
    monkeypatch,
):
    doctor = make_staff(
        clinic,
        role=Role.DOCTOR,
    )

    calls = []

    monkeypatch.setattr(
        service,
        "build_hospital_read_model",
        lambda **kwargs: calls.append(kwargs) or HOSPITAL_DATA,
    )

    dashboard = service.get_clinical_dashboard(
        actor=doctor.user,
    )

    assert calls == [{"clinic_id": clinic.id}]
    assert dashboard.hospital is not None
    assert dashboard.hospital.admissions.active_admissions == 4
    assert dashboard.hospital.nursing.pending_tasks == 6
    assert dashboard.hospital.emar.due == 3
    assert (
        dashboard.hospital.medication_reconciliation.draft_discharge
        == 2
    )
    assert {
        metric.key
        for metric in dashboard.metrics
    }.issuperset(
        {
            "active_admissions",
            "draft_discharge_reconciliations",
        }
    )
