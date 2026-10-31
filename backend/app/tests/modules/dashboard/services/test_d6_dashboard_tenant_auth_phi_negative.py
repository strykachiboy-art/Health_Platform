from __future__ import annotations

from unittest.mock import Mock

import app.modules.dashboard.routes.dashboard_route as dashboard_routes

from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus, BedStatus
from app.modules.dashboard.services import clinical_dashboard_service
from app.modules.dashboard.services import management_dashboard_service
from app.modules.dashboard.services import operations_dashboard_service
from app.modules.dashboard.services.hospital_dashboard_widget_service import (
    build_hospital_read_model,
)
from app.modules.medication_reconciliation.models.medication_reconciliation_model import (
    MedicationReconciliation,
)
from app.modules.nursing.models.nursing_model import NursingCarePlan
from app.modules.ward.models.ward_model import Admission


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

FORBIDDEN_PHI_KEYS = {
    "patient_id",
    "patient_name",
    "first_name",
    "last_name",
    "barcode",
    "patient_barcode",
    "medication_barcode",
    "medication_name",
    "note_text",
    "diagnosis",
    "reason",
}


def _assert_no_forbidden_phi(value):
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in FORBIDDEN_PHI_KEYS
            _assert_no_forbidden_phi(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_phi(child)


def _create_active_admission(
    *,
    clinic,
    patient,
    staff,
    make_ward,
    make_bed,
    db_session,
):
    ward = make_ward(clinic)

    bed = make_bed(
        ward,
        status=BedStatus.OCCUPIED,
    )

    admission = Admission(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        status=AdmissionStatus.ADMITTED,
    )

    db_session.add(admission)
    db_session.flush()

    return admission


def test_shared_hospital_read_model_isolates_two_clinics(
    clinic,
    make_clinic,
    make_staff,
    make_patient,
    make_ward,
    make_bed,
    db_session,
):
    other_clinic = make_clinic()

    staff_a = make_staff(
        clinic,
        role=Role.DOCTOR,
    )
    staff_b = make_staff(
        other_clinic,
        role=Role.DOCTOR,
    )

    patient_a = make_patient(clinic)
    patient_b = make_patient(other_clinic)

    admission_a = _create_active_admission(
        clinic=clinic,
        patient=patient_a,
        staff=staff_a,
        make_ward=make_ward,
        make_bed=make_bed,
        db_session=db_session,
    )
    admission_b = _create_active_admission(
        clinic=other_clinic,
        patient=patient_b,
        staff=staff_b,
        make_ward=make_ward,
        make_bed=make_bed,
        db_session=db_session,
    )

    db_session.add(
        NursingCarePlan(
            clinic_id=clinic.id,
            patient_id=patient_a.id,
            admission_id=admission_a.id,
            created_by_staff_id=staff_a.id,
            title="Clinic A care plan",
            problem="A",
            goal="A",
        )
    )

    db_session.add_all(
        [
            NursingCarePlan(
                clinic_id=other_clinic.id,
                patient_id=patient_b.id,
                admission_id=admission_b.id,
                created_by_staff_id=staff_b.id,
                title="Clinic B care plan 1",
                problem="B1",
                goal="B1",
            ),
            NursingCarePlan(
                clinic_id=other_clinic.id,
                patient_id=patient_b.id,
                admission_id=admission_b.id,
                created_by_staff_id=staff_b.id,
                title="Clinic B care plan 2",
                problem="B2",
                goal="B2",
            ),
        ]
    )

    db_session.add(
        MedicationReconciliation(
            clinic_id=clinic.id,
            patient_id=patient_a.id,
            admission_id=admission_a.id,
            event_type="discharge",
            event_identity="d6-clinic-a-discharge",
            created_by_staff_id=staff_a.id,
        )
    )

    db_session.add_all(
        [
            MedicationReconciliation(
                clinic_id=other_clinic.id,
                patient_id=patient_b.id,
                admission_id=admission_b.id,
                event_type="discharge",
                event_identity="d6-clinic-b-discharge-1",
                created_by_staff_id=staff_b.id,
            ),
            MedicationReconciliation(
                clinic_id=other_clinic.id,
                patient_id=patient_b.id,
                admission_id=admission_b.id,
                event_type="discharge",
                event_identity="d6-clinic-b-discharge-2",
                created_by_staff_id=staff_b.id,
            ),
        ]
    )

    db_session.flush()

    clinic_a = build_hospital_read_model(
        clinic_id=clinic.id,
    )
    clinic_b = build_hospital_read_model(
        clinic_id=other_clinic.id,
    )

    assert clinic_a["admissions"] == {
        "active_admissions": 1,
        "total_beds": 1,
        "occupied_beds": 1,
        "available_beds": 0,
    }
    assert clinic_b["admissions"] == {
        "active_admissions": 1,
        "total_beds": 1,
        "occupied_beds": 1,
        "available_beds": 0,
    }

    assert clinic_a["nursing"]["active_care_plans"] == 1
    assert clinic_b["nursing"]["active_care_plans"] == 2

    assert clinic_a["medication_reconciliation"]["draft"] == 1
    assert clinic_a["medication_reconciliation"]["draft_discharge"] == 1

    assert clinic_b["medication_reconciliation"]["draft"] == 2
    assert clinic_b["medication_reconciliation"]["draft_discharge"] == 2

    _assert_no_forbidden_phi(clinic_a)
    _assert_no_forbidden_phi(clinic_b)


class _FakeDashboardResult:
    def model_dump(self, *, mode):
        assert mode == "json"
        return {
            "context": {
                "role": "admin",
                "scope": "clinic",
                "clinic_id": 1,
            }
        }


def test_dashboard_route_ignores_client_controlled_role_and_clinic_selectors(
    client,
    user,
    make_auth_headers,
    monkeypatch,
):
    service = Mock(
        return_value=_FakeDashboardResult(),
    )

    monkeypatch.setattr(
        dashboard_routes,
        "get_dashboard",
        service,
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=make_auth_headers(user),
        query_string={
            "clinic_id": "999999",
            "role": Role.SUPER_ADMIN.value,
        },
    )

    assert response.status_code == 200

    kwargs = service.call_args.kwargs

    assert kwargs["actor_id"] == user.id
    assert kwargs["query"].date_from is None
    assert kwargs["query"].date_to is None

    assert not hasattr(
        kwargs["query"],
        "clinic_id",
    )
    assert not hasattr(
        kwargs["query"],
        "role",
    )


def test_doctor_and_nurse_hospital_payloads_are_aggregate_only(
    monkeypatch,
):
    monkeypatch.setattr(
        clinical_dashboard_service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    for role in (
        Role.DOCTOR,
        Role.NURSE,
    ):
        hospital = (
            clinical_dashboard_service
            ._build_hospital_dashboard(
                role=role,
                clinic_id=7,
            )
        )

        assert hospital is not None

        payload = hospital.model_dump(
            exclude_none=True,
        )

        _assert_no_forbidden_phi(payload)


def test_pharmacist_hospital_payload_is_reconciliation_only(
    monkeypatch,
):
    def forbidden_full_builder(**_):
        raise AssertionError(
            "pharmacist must not trigger full hospital read model"
        )

    monkeypatch.setattr(
        clinical_dashboard_service,
        "build_hospital_read_model",
        forbidden_full_builder,
    )

    monkeypatch.setattr(
        clinical_dashboard_service,
        "build_reconciliation_aggregates",
        lambda **_: HOSPITAL_DATA[
            "medication_reconciliation"
        ],
    )

    hospital = (
        clinical_dashboard_service
        ._build_hospital_dashboard(
            role=Role.PHARMACIST,
            clinic_id=7,
        )
    )

    payload = hospital.model_dump(
        exclude_none=True,
    )

    assert set(payload) == {
        "medication_reconciliation",
    }

    _assert_no_forbidden_phi(payload)


def test_non_authorized_clinical_roles_cannot_trigger_hospital_builders(
    monkeypatch,
):
    def forbidden_builder(**_):
        raise AssertionError(
            "restricted hospital builder must not be called"
        )

    monkeypatch.setattr(
        clinical_dashboard_service,
        "build_hospital_read_model",
        forbidden_builder,
    )
    monkeypatch.setattr(
        clinical_dashboard_service,
        "build_reconciliation_aggregates",
        forbidden_builder,
    )

    for role in (
        Role.LAB_TECHNICIAN,
        Role.PARAMEDIC,
        Role.EMT,
    ):
        assert (
            clinical_dashboard_service
            ._build_hospital_dashboard(
                role=role,
                clinic_id=7,
            )
            is None
        )


def test_management_hospital_payload_is_aggregate_only(
    monkeypatch,
):
    monkeypatch.setattr(
        management_dashboard_service,
        "build_hospital_read_model",
        lambda **_: HOSPITAL_DATA,
    )

    hospital = (
        management_dashboard_service
        ._build_management_hospital_operations(
            clinic_id=7,
        )
    )

    payload = hospital.model_dump()

    assert set(payload) == {
        "admissions",
        "nursing",
        "emar",
        "medication_reconciliation",
    }

    _assert_no_forbidden_phi(payload)


def test_receptionist_hospital_payload_is_capacity_only(
    monkeypatch,
):
    calls = []

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_admission_aggregates",
        lambda **kwargs: calls.append(kwargs)
        or HOSPITAL_DATA["admissions"],
    )

    capacity = (
        operations_dashboard_service
        ._build_operations_hospital_capacity(
            role=Role.RECEPTIONIST,
            clinic_id=7,
        )
    )

    assert calls == [
        {
            "clinic_id": 7,
        }
    ]

    payload = capacity.model_dump()

    assert set(payload) == {
        "active_admissions",
        "total_beds",
        "occupied_beds",
        "available_beds",
    }

    _assert_no_forbidden_phi(payload)


def test_non_receptionist_operations_roles_cannot_trigger_ward_builder(
    monkeypatch,
):
    def forbidden_builder(**_):
        raise AssertionError(
            "ward capacity builder must not be called"
        )

    monkeypatch.setattr(
        operations_dashboard_service,
        "build_admission_aggregates",
        forbidden_builder,
    )

    for role in (
        Role.DRIVER,
        Role.AMBULANCE_DISPATCHER,
        Role.AMBULANCE_COORDINATOR,
        Role.OTHER,
    ):
        assert (
            operations_dashboard_service
            ._build_operations_hospital_capacity(
                role=role,
                clinic_id=7,
            )
            is None
        )
