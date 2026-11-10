from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.core.enums.medication_reconciliation_enums import (
    MedicationReconciliationEvent,
)
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import (
    AdmissionStatus,
    BedStatus,
    ReservationStatus,
    WardType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.extensions import db
from app.modules.medication_reconciliation.services import (
    medication_reconciliation_service as med_recon_service,
)
from app.modules.ward.services import ward_service


@pytest.fixture()
def ward_routes():
    import app.modules.ward.routes.ward_route as routes

    return routes


def _headers(
    auth_headers_for,
    user,
    role=None,
):
    return auth_headers_for(
        user,
        role=role,
    )


def _future_expiry(
    minutes: int = 30,
) -> datetime:
    return (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
        + timedelta(minutes=minutes)
    )


def _create_ward(
    clinic,
    *,
    name="General Ward",
    ward_type=WardType.GENERAL,
    capacity=5,
):
    return ward_service.create_ward(
        clinic_id=clinic.id,
        name=name,
        ward_type=ward_type,
        capacity=capacity,
    )


def _create_bed(
    clinic,
    *,
    ward_name="General Ward",
    capacity=5,
    bed_number="B-001",
):
    ward = _create_ward(
        clinic,
        name=ward_name,
        capacity=capacity,
    )

    bed = ward_service.add_bed(
        ward_id=ward.id,
        bed_number=bed_number,
        clinic_id=clinic.id,
    )

    return ward, bed


def _create_reservation(
    clinic,
    make_patient,
    make_staff,
    *,
    expires_at=None,
    reason=None,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(
        clinic,
    )

    reservation = ward_service.reserve_bed(
        patient_id=patient.id,
        bed_id=bed.id,
        reserved_by_id=staff.id,
        clinic_id=clinic.id,
        reason=reason,
        expires_at=expires_at,
        actor_user_id=staff.user_id,
    )

    return (
        patient,
        staff,
        bed,
        reservation,
    )


def _create_admission(
    clinic,
    make_patient,
    make_staff,
    *,
    reason=None,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(
        clinic,
    )

    admission = ward_service.admit_patient(
        patient_id=patient.id,
        bed_id=bed.id,
        admitted_by_id=staff.id,
        clinic_id=clinic.id,
        reason=reason,
        actor_user_id=staff.user_id,
    )

    return (
        patient,
        staff,
        bed,
        admission,
    )

def _mark_admission_discharged(
    admission,
    bed,
):
    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
    )
    bed.status = BedStatus.AVAILABLE
    db.session.flush()


def _clinical_discharge_payload(
    *,
    reason="Clinical discharge complete",
):
    return {
        "reason": reason,
        "primary_diagnosis": "Resolved acute illness",
        "secondary_diagnoses": ["Hypertension"],
        "condition_at_discharge": "Stable and ambulatory",
        "instructions": "Return for worsening symptoms",
        "follow_up_plan": "Clinic follow-up in seven days",
        "destination": "Home",
    }


def _finalize_empty_discharge_reconciliation(
    clinic,
    admission,
    actor,
):
    record = med_recon_service.create_medication_reconciliation(
        clinic_id=clinic.id,
        admission_id=admission.id,
        event_type=MedicationReconciliationEvent.DISCHARGE,
        actor_staff_id=actor.id,
        no_medications_reported=True,
        notes="Discharge medication review",
        items=[],
    )

    return med_recon_service.finalize_medication_reconciliation(
        record.id,
        clinic_id=clinic.id,
        actor_staff_id=actor.id,
        expected_version=1,
        expected_updated_at=med_recon_service._utc_draft_timestamp(record.updated_at),
    )



# Authentication


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/wards"),
        ("GET", "/api/v1/wards"),
        ("PATCH", "/api/v1/wards/1"),
        ("GET", "/api/v1/wards/1"),
        ("GET", "/api/v1/wards/1/occupancy"),
        ("POST", "/api/v1/wards/1/beds"),
        ("GET", "/api/v1/wards/1/beds"),
        ("GET", "/api/v1/wards/beds/1"),
        ("PATCH", "/api/v1/wards/beds/1/maintenance"),
        ("POST", "/api/v1/wards/reservations"),
        ("GET", "/api/v1/wards/reservations"),
        ("GET", "/api/v1/wards/reservations/1"),
        ("GET", "/api/v1/wards/patients/1/reservation"),
        ("GET", "/api/v1/wards/beds/1/reservation"),
        ("POST", "/api/v1/wards/reservations/1/cancel"),
        ("POST", "/api/v1/wards/reservations/1/admit"),
        ("POST", "/api/v1/wards/admissions"),
        ("GET", "/api/v1/wards/admissions/1"),
        ("GET", "/api/v1/wards/patients/1/admissions"),
        ("GET", "/api/v1/wards/patients/1/current-admission"),
        ("POST", "/api/v1/wards/admissions/1/transfer"),
        ("POST", "/api/v1/wards/admissions/1/discharge"),
    ],
)
def test_all_ward_routes_require_authentication(
    client,
    method,
    path,
):
    response = client.open(
        path,
        method=method,
    )

    assert response.status_code in (401, 422)


# RBAC


def test_receptionist_can_view_wards(
    client,
    clinic,
    make_user,
    auth_headers_for,
):
    user = make_user(
        clinic,
        role=Role.RECEPTIONIST,
    )

    _create_ward(clinic)

    response = client.get(
        "/api/v1/wards",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "method,path,payload",
    [
        (
            "POST",
            "/api/v1/wards",
            {
                "name": "Restricted Ward",
                "capacity": 5,
            },
        ),
        (
            "PATCH",
            "/api/v1/wards/1",
            {
                "name": "Restricted Update",
            },
        ),
        (
            "POST",
            "/api/v1/wards/1/beds",
            {
                "bed_number": "B-001",
            },
        ),
        (
            "PATCH",
            "/api/v1/wards/beds/1/maintenance",
            {
                "under_maintenance": True,
            },
        ),
        (
            "POST",
            "/api/v1/wards/reservations",
            {
                "patient_id": 1,
                "bed_id": 1,
            },
        ),
        (
            "POST",
            "/api/v1/wards/admissions",
            {
                "patient_id": 1,
                "bed_id": 1,
            },
        ),
        (
            "POST",
            "/api/v1/wards/admissions/1/transfer",
            {
                "to_bed_id": 2,
            },
        ),
        (
            "POST",
            "/api/v1/wards/admissions/1/discharge",
            {},
        ),
    ],
)
def test_receptionist_cannot_perform_management_or_clinical_actions(
    client,
    clinic,
    make_user,
    auth_headers_for,
    method,
    path,
    payload,
):
    user = make_user(
        clinic,
        role=Role.RECEPTIONIST,
    )

    response = client.open(
        path,
        method=method,
        json=payload,
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 403


# Create ward


def test_create_ward_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards",
        json={
            "name": "  Surgical Ward  ",
            "ward_type": WardType.GENERAL.value,
            "capacity": 10,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Ward created successfully"
    )

    ward = body["ward"]

    assert ward["name"] == "Surgical Ward"
    assert ward["capacity"] == 10
    assert ward["ward_type"] == (
        WardType.GENERAL.value
    )


def test_create_ward_route_rejects_client_clinic_override(
    client,
    clinic,
    make_clinic,
    user,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    response = client.post(
        "/api/v1/wards",
        json={
            "name": "Ward",
            "capacity": 5,
            "clinic_id": foreign_clinic.id,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["error"] == "Validation error"
    assert body["details"]


def test_create_ward_route_rejects_invalid_payload(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards",
        json={
            "name": "",
            "capacity": -1,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["error"] == "Validation error"
    assert body["details"]


def test_create_ward_route_rejects_unknown_fields(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards",
        json={
            "name": "Ward",
            "capacity": 5,
            "unexpected": "blocked",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_create_ward_route_maps_domain_error(
    client,
    user,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    monkeypatch.setattr(
        ward_routes,
        "create_ward",
        Mock(
            side_effect=ConflictError(
                "Ward already exists"
            )
        ),
    )

    response = client.post(
        "/api/v1/wards",
        json={
            "name": "Duplicate Ward",
            "capacity": 5,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == (
        "Ward already exists"
    )


# List wards


def test_list_wards_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _create_ward(
        clinic,
        name="General Ward",
    )

    _create_ward(
        clinic,
        name="Surgical Ward",
    )

    response = client.get(
        "/api/v1/wards",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 2
    assert body["page"] == 1
    assert body["per_page"] == 50

    names = {
        item["name"]
        for item in body["items"]
    }

    assert names == {
        "General Ward",
        "Surgical Ward",
    }


def test_list_wards_route_filters_by_type(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _create_ward(
        clinic,
        name="General Ward",
        ward_type=WardType.GENERAL,
    )

    response = client.get(
        (
            "/api/v1/wards"
            f"?ward_type={WardType.GENERAL.value}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1
    assert body["items"]

    assert all(
        item["ward_type"]
        == WardType.GENERAL.value
        for item in body["items"]
    )


def test_list_wards_route_rejects_invalid_type(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards?ward_type=NOT_REAL",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["error"] == "Validation error"
    assert "details" in body
    assert any(
        "ward_type" in str(detail)
        for detail in body["details"]
    )


def test_list_wards_route_supports_pagination(
    client,
    clinic,
    user,
    auth_headers_for,
):
    for index in range(1, 6):
        _create_ward(
            clinic,
            name=f"Ward {index:02d}",
        )

    response = client.get(
        "/api/v1/wards?page=2&per_page=2",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 5
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert len(body["items"]) == 2


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "per_page=0",
        "per_page=501",
        "page=abc",
        "per_page=abc",
    ],
)
def test_list_wards_route_rejects_invalid_pagination(
    client,
    user,
    auth_headers_for,
    query,
):
    response = client.get(
        f"/api/v1/wards?{query}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Update ward


def test_update_ward_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        name="Old Ward",
        capacity=5,
    )

    response = client.patch(
        f"/api/v1/wards/{ward.id}",
        json={
            "name": "Updated Ward",
            "capacity": 10,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["message"] == (
        "Ward updated successfully"
    )

    assert body["ward"]["id"] == ward.id
    assert body["ward"]["name"] == "Updated Ward"
    assert body["ward"]["capacity"] == 10


def test_update_ward_route_rejects_unknown_fields(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(clinic)

    response = client.patch(
        f"/api/v1/wards/{ward.id}",
        json={
            "unexpected": "blocked",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_update_ward_route_rejects_empty_payload(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(clinic)

    response = client.patch(
        f"/api/v1/wards/{ward.id}",
        json={},
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_update_ward_route_rejects_duplicate_name(
    client,
    clinic,
    user,
    auth_headers_for,
):
    first = _create_ward(
        clinic,
        name="Ward One",
    )

    second = _create_ward(
        clinic,
        name="Ward Two",
    )

    response = client.patch(
        f"/api/v1/wards/{second.id}",
        json={
            "name": first.name,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 409


def test_update_ward_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.patch(
        "/api/v1/wards/999999",
        json={
            "name": "Updated Ward",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_update_ward_route_maps_domain_error(
    client,
    user,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    monkeypatch.setattr(
        ward_routes,
        "update_ward",
        Mock(
            side_effect=ConflictError(
                "Ward update conflict"
            )
        ),
    )

    response = client.patch(
        "/api/v1/wards/1",
        json={
            "name": "Updated Ward",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == (
        "Ward update conflict"
    )


# Get ward


def test_get_ward_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        name="Recovery Ward",
        capacity=8,
    )

    response = client.get(
        f"/api/v1/wards/{ward.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == ward.id
    assert body["clinic_id"] == clinic.id
    assert body["name"] == "Recovery Ward"
    assert body["capacity"] == 8
    assert body["ward_type"] == (
        WardType.GENERAL.value
    )


def test_get_ward_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/999999",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404

    assert "Ward 999999 not found" in (
        response.get_json()["error"]
    )


def test_get_ward_route_enforces_clinic_isolation(
    client,
    clinic,
    make_clinic,
    make_user,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    foreign_ward = _create_ward(
        foreign_clinic,
        name="Foreign Ward",
    )

    local_user = make_user(
        clinic,
        role=Role.ADMIN,
    )

    response = client.get(
        f"/api/v1/wards/{foreign_ward.id}",
        headers=_headers(
            auth_headers_for,
            local_user,
        ),
    )

    assert response.status_code == 404


def test_get_ward_route_maps_not_found_error(
    client,
    user,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    monkeypatch.setattr(
        ward_routes,
        "get_ward",
        Mock(
            side_effect=NotFoundError(
                "Ward 123 not found"
            )
        ),
    )

    response = client.get(
        "/api/v1/wards/123",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == (
        "Ward 123 not found"
    )


def test_get_ward_route_maps_validation_error(
    client,
    user,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    monkeypatch.setattr(
        ward_routes,
        "get_ward",
        Mock(
            side_effect=ValidationError(
                "Invalid ward"
            )
        ),
    )

    response = client.get(
        "/api/v1/wards/1",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    assert response.get_json()["error"] == (
        "Invalid ward"
    )


# Occupancy


def test_get_ward_occupancy_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward, _ = _create_bed(
        clinic,
        capacity=2,
    )

    response = client.get(
        f"/api/v1/wards/{ward.id}/occupancy",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["ward_id"] == ward.id
    assert body["clinic_id"] == clinic.id
    assert body["capacity"] == 2
    assert body["total_beds"] == 1
    assert body["available"] == 1


def test_get_ward_occupancy_route_missing_ward(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/999999/occupancy",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


# Beds


def test_add_bed_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        capacity=3,
    )

    response = client.post(
        f"/api/v1/wards/{ward.id}/beds",
        json={
            "bed_number": "  B-101  ",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Bed added successfully"
    )

    assert body["bed"]["id"] is not None
    assert body["bed"]["ward_id"] == ward.id
    assert body["bed"]["bed_number"] == "B-101"
    assert body["bed"]["status"] == (
        BedStatus.AVAILABLE.value
    )


def test_add_bed_route_rejects_invalid_payload(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(clinic)

    response = client.post(
        f"/api/v1/wards/{ward.id}/beds",
        json={
            "bed_number": "",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_add_bed_route_rejects_unknown_fields(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(clinic)

    response = client.post(
        f"/api/v1/wards/{ward.id}/beds",
        json={
            "bed_number": "B-001",
            "clinic_id": 999,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_add_bed_route_missing_ward(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards/999999/beds",
        json={
            "bed_number": "B-001",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_add_bed_route_maps_conflict_error(
    client,
    user,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    monkeypatch.setattr(
        ward_routes,
        "add_bed",
        Mock(
            side_effect=ConflictError(
                "Ward has reached capacity"
            )
        ),
    )

    response = client.post(
        "/api/v1/wards/1/beds",
        json={
            "bed_number": "B-001",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 409

    assert response.get_json()["error"] == (
        "Ward has reached capacity"
    )


def test_list_beds_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        capacity=2,
    )

    ward_service.add_bed(
        ward.id,
        "B-002",
        clinic.id,
    )

    ward_service.add_bed(
        ward.id,
        "B-001",
        clinic.id,
    )

    response = client.get(
        f"/api/v1/wards/{ward.id}/beds",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 2
    assert body["page"] == 1
    assert body["per_page"] == 50

    assert [
        item["bed_number"]
        for item in body["items"]
    ] == [
        "B-001",
        "B-002",
    ]


def test_list_beds_route_filters_status(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        capacity=2,
    )

    available = ward_service.add_bed(
        ward.id,
        "B-001",
        clinic.id,
    )

    maintenance = ward_service.add_bed(
        ward.id,
        "B-002",
        clinic.id,
    )

    ward_service.set_bed_maintenance(
        maintenance.id,
        True,
        clinic.id,
    )

    response = client.get(
        (
            f"/api/v1/wards/{ward.id}/beds"
            f"?status={BedStatus.MAINTENANCE.value}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert maintenance.id in ids
    assert available.id not in ids


def test_list_beds_route_supports_pagination(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(
        clinic,
        capacity=5,
    )

    for index in range(1, 6):
        ward_service.add_bed(
            ward.id,
            f"B-{index:03d}",
            clinic.id,
        )

    response = client.get(
        (
            f"/api/v1/wards/{ward.id}/beds"
            "?page=2&per_page=2"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 5
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert len(body["items"]) == 2


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "per_page=0",
        "per_page=501",
        "page=abc",
        "per_page=abc",
    ],
)
def test_list_beds_route_rejects_invalid_pagination(
    client,
    clinic,
    user,
    auth_headers_for,
    query,
):
    ward = _create_ward(
        clinic,
        capacity=1,
    )

    response = client.get(
        f"/api/v1/wards/{ward.id}/beds?{query}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_list_beds_route_rejects_invalid_status(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward = _create_ward(clinic)

    response = client.get(
        (
            f"/api/v1/wards/{ward.id}/beds"
            "?status=INVALID"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["error"] == "Validation error"
    assert "details" in body
    assert any(
        "status" in str(detail)
        for detail in body["details"]
    )


def test_list_beds_route_missing_ward(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/999999/beds",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_get_bed_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    ward, bed = _create_bed(
        clinic,
        bed_number="B-100",
    )

    response = client.get(
        f"/api/v1/wards/beds/{bed.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == bed.id
    assert body["ward_id"] == ward.id
    assert body["bed_number"] == "B-100"
    assert body["status"] == (
        BedStatus.AVAILABLE.value
    )


def test_get_bed_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/beds/999999",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_get_bed_route_rejects_foreign_bed(
    client,
    clinic,
    make_clinic,
    make_user,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    _, foreign_bed = _create_bed(
        foreign_clinic,
        ward_name="Foreign Ward",
    )

    local_user = make_user(
        clinic,
        role=Role.ADMIN,
    )

    response = client.get(
        f"/api/v1/wards/beds/{foreign_bed.id}",
        headers=_headers(
            auth_headers_for,
            local_user,
        ),
    )

    assert response.status_code == 404


def test_set_bed_maintenance_route_success(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _, bed = _create_bed(clinic)

    response = client.patch(
        f"/api/v1/wards/beds/{bed.id}/maintenance",
        json={
            "under_maintenance": True,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["message"] == (
        "Bed maintenance status updated"
    )
    assert body["bed"]["id"] == bed.id
    assert body["bed"]["status"] == (
        BedStatus.MAINTENANCE.value
    )


def test_set_bed_maintenance_route_restores_bed(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _, bed = _create_bed(clinic)

    ward_service.set_bed_maintenance(
        bed.id,
        True,
        clinic.id,
    )

    response = client.patch(
        f"/api/v1/wards/beds/{bed.id}/maintenance",
        json={
            "under_maintenance": False,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    assert (
        response.get_json()["bed"]["status"]
        == BedStatus.AVAILABLE.value
    )


def test_set_bed_maintenance_route_rejects_invalid_payload(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _, bed = _create_bed(clinic)

    response = client.patch(
        f"/api/v1/wards/beds/{bed.id}/maintenance",
        json={
            "under_maintenance": "invalid",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Reservations


def test_reserve_bed_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(clinic)

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": patient.id,
            "bed_id": bed.id,
            "reason": "Expected admission",
            "expires_at": _future_expiry().isoformat(),
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Bed reserved successfully"
    )

    reservation = body["reservation"]

    assert reservation["patient_id"] == patient.id
    assert reservation["bed_id"] == bed.id
    assert reservation["reserved_by_id"] == staff.id
    assert reservation["status"] == (
        ReservationStatus.PENDING.value
    )


def test_reserve_bed_route_rejects_invalid_payload(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": 0,
            "bed_id": 0,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_reserve_bed_route_rejects_unknown_fields(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(clinic)

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": patient.id,
            "bed_id": bed.id,
            "reserved_by_id": 999999,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_reserve_bed_route_maps_service_conflict(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
    monkeypatch,
    ward_routes,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(clinic)

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    monkeypatch.setattr(
        ward_routes,
        "reserve_bed",
        Mock(
            side_effect=ConflictError(
                "Bed is already reserved"
            )
        ),
    )

    response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": patient.id,
            "bed_id": bed.id,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 409

    assert response.get_json()["error"] == (
        "Bed is already reserved"
    )


def test_list_reservations_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        "/api/v1/wards/reservations",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert reservation.id in ids
    assert patient.id
    assert bed.id


def test_list_reservations_route_filters_status(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        (
            "/api/v1/wards/reservations"
            f"?status={ReservationStatus.PENDING.value}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1

    assert any(
        item["id"] == reservation.id
        for item in body["items"]
    )


def test_list_reservations_route_filters_patient(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    actor = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient_a = make_patient(
        clinic=clinic,
    )

    patient_b = make_patient(
        clinic=clinic,
    )

    _, bed_a = _create_bed(
        clinic,
        ward_name="Reservation A",
        bed_number="B-001",
    )

    _, bed_b = _create_bed(
        clinic,
        ward_name="Reservation B",
        bed_number="B-002",
    )

    first = ward_service.reserve_bed(
        patient_id=patient_a.id,
        bed_id=bed_a.id,
        reserved_by_id=actor.id,
        clinic_id=clinic.id,
        actor_user_id=actor.user_id,
    )

    second = ward_service.reserve_bed(
        patient_id=patient_b.id,
        bed_id=bed_b.id,
        reserved_by_id=actor.id,
        clinic_id=clinic.id,
        actor_user_id=actor.user_id,
    )

    user = db.session.get(
        type(actor.user),
        actor.user_id,
    )

    response = client.get(
        (
            "/api/v1/wards/reservations"
            f"?patient_id={patient_a.id}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert ids == {first.id}
    assert second.id not in ids


def test_list_reservations_route_filters_bed(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    actor = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient_a = make_patient(
        clinic=clinic,
    )

    patient_b = make_patient(
        clinic=clinic,
    )

    _, bed_a = _create_bed(
        clinic,
        ward_name="Reservation A",
        bed_number="B-001",
    )

    _, bed_b = _create_bed(
        clinic,
        ward_name="Reservation B",
        bed_number="B-002",
    )

    first = ward_service.reserve_bed(
        patient_id=patient_a.id,
        bed_id=bed_a.id,
        reserved_by_id=actor.id,
        clinic_id=clinic.id,
        actor_user_id=actor.user_id,
    )

    second = ward_service.reserve_bed(
        patient_id=patient_b.id,
        bed_id=bed_b.id,
        reserved_by_id=actor.id,
        clinic_id=clinic.id,
        actor_user_id=actor.user_id,
    )

    user = db.session.get(
        type(actor.user),
        actor.user_id,
    )

    response = client.get(
        (
            "/api/v1/wards/reservations"
            f"?bed_id={bed_a.id}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 1

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert ids == {first.id}
    assert second.id not in ids


def test_list_reservations_route_supports_pagination(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    for index in range(1, 4):
        patient = make_patient(
            clinic=clinic,
        )

        _, bed = _create_bed(
            clinic,
            ward_name=f"Reservation Ward {index}",
            bed_number=f"B-{index:03d}",
        )

        ward_service.reserve_bed(
            patient_id=patient.id,
            bed_id=bed.id,
            reserved_by_id=staff.id,
            clinic_id=clinic.id,
            actor_user_id=staff.user_id,
        )

    response = client.get(
        "/api/v1/wards/reservations?page=2&per_page=2",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 3
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert len(body["items"]) == 1


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "per_page=0",
        "per_page=501",
        "page=abc",
        "per_page=abc",
    ],
)
def test_list_reservations_route_rejects_invalid_pagination(
    client,
    user,
    auth_headers_for,
    query,
):
    response = client.get(
        f"/api/v1/wards/reservations?{query}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_list_reservations_route_rejects_invalid_status(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/reservations?status=INVALID",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422

    body = response.get_json()

    assert body["error"] == "Validation error"
    assert "details" in body
    assert any(
        "status" in str(detail)
        for detail in body["details"]
    )


def test_list_reservations_route_rejects_invalid_patient_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/reservations?patient_id=0",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_list_reservations_route_rejects_invalid_bed_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/reservations?bed_id=0",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_get_reservation_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        f"/api/v1/wards/reservations/{reservation.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == reservation.id


def test_get_reservation_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/reservations/999999",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


# Active reservations


def test_get_patient_active_reservation_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        (
            f"/api/v1/wards/patients/"
            f"{patient.id}/reservation"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200
    assert (
        response.get_json()["id"]
        == reservation.id
    )


def test_get_patient_active_reservation_route_returns_404_when_missing(
    client,
    clinic,
    make_patient,
    user,
    auth_headers_for,
):
    patient = make_patient(
        clinic=clinic,
    )

    response = client.get(
        (
            f"/api/v1/wards/patients/"
            f"{patient.id}/reservation"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404
    assert response.get_json()["message"] == (
        "No active reservation found"
    )


def test_get_patient_active_reservation_route_rejects_invalid_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/patients/0/reservation",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_get_bed_active_reservation_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, bed, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        (
            f"/api/v1/wards/beds/"
            f"{bed.id}/reservation"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200
    assert (
        response.get_json()["id"]
        == reservation.id
    )


def test_get_bed_active_reservation_route_returns_404_when_missing(
    client,
    clinic,
    user,
    auth_headers_for,
):
    _, bed = _create_bed(clinic)

    response = client.get(
        (
            f"/api/v1/wards/beds/"
            f"{bed.id}/reservation"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_get_bed_active_reservation_route_rejects_invalid_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/beds/0/reservation",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Cancel reservation


def test_cancel_reservation_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/reservations/"
            f"{reservation.id}/cancel"
        ),
        json={
            "reason": "Patient no longer requires bed",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["message"] == (
        "Bed reservation cancelled successfully"
    )

    assert (
        body["reservation"]["status"]
        == ReservationStatus.CANCELLED.value
    )


def test_cancel_reservation_route_rejects_invalid_payload(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/reservations/"
            f"{reservation.id}/cancel"
        ),
        json={
            "reason": "x" * 256,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_cancel_reservation_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards/reservations/999999/cancel",
        json={},
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


# Admissions


def test_admit_patient_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(clinic)

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        "/api/v1/wards/admissions",
        json={
            "patient_id": patient.id,
            "bed_id": bed.id,
            "reason": "Observation",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Patient admitted successfully"
    )

    admission = body["admission"]

    assert admission["id"] is not None
    assert admission["patient_id"] == patient.id
    assert admission["bed_id"] == bed.id
    assert admission["admitted_by_id"] == staff.id
    assert admission["status"] == (
        AdmissionStatus.ADMITTED.value
    )


def test_admit_patient_route_rejects_invalid_payload(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards/admissions",
        json={
            "patient_id": 0,
            "bed_id": 0,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_admit_patient_route_rejects_client_staff_override(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    patient = make_patient(
        clinic=clinic,
    )

    _, bed = _create_bed(clinic)

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        "/api/v1/wards/admissions",
        json={
            "patient_id": patient.id,
            "bed_id": bed.id,
            "admitted_by_id": 999999,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_get_admission_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, admission = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        f"/api/v1/wards/admissions/{admission.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == admission.id
    assert body["patient_id"] == patient.id
    assert body["bed_id"] == bed.id
    assert body["admitted_by_id"] == staff.id
    assert body["status"] == (
        AdmissionStatus.ADMITTED.value
    )


def test_get_admission_route_missing(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/admissions/999999",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_get_admission_route_rejects_foreign_admission(
    client,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
    make_user,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    _, _, _, foreign_admission = (
        _create_admission(
            foreign_clinic,
            make_patient,
            make_staff,
        )
    )

    local_user = make_user(
        clinic,
        role=Role.ADMIN,
    )

    response = client.get(
        f"/api/v1/wards/admissions/{foreign_admission.id}",
        headers=_headers(
            auth_headers_for,
            local_user,
        ),
    )

    assert response.status_code == 404


# Patient admissions


def test_list_patient_admissions_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, first_bed, first = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
            reason="First admission",
        )
    )

    _mark_admission_discharged(
        first,
        first_bed,
    )

    _, second_bed = _create_bed(
        clinic,
        ward_name="Second Ward",
        bed_number="B-002",
    )

    second = ward_service.admit_patient(
        patient_id=patient.id,
        bed_id=second_bed.id,
        admitted_by_id=staff.id,
        clinic_id=clinic.id,
        actor_user_id=staff.user_id,
        reason="Second admission",
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        f"/api/v1/wards/patients/{patient.id}/admissions",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 2

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert first.id in ids
    assert second.id in ids


def test_list_patient_admissions_route_supports_pagination(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, first_bed, first = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    _mark_admission_discharged(
        first,
        first_bed,
    )

    for index in range(2, 4):
        _, bed = _create_bed(
            clinic,
            ward_name=f"Admission Ward {index}",
            bed_number=f"B-{index:03d}",
        )

        admission = ward_service.admit_patient(
            patient_id=patient.id,
            bed_id=bed.id,
            admitted_by_id=staff.id,
            clinic_id=clinic.id,
            actor_user_id=staff.user_id,
        )

        _mark_admission_discharged(
            admission,
            bed,
        )

    response = client.get(
        (
            f"/api/v1/wards/patients/{patient.id}/admissions"
            "?page=2&per_page=2"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["total"] == 3
    assert body["page"] == 2
    assert body["per_page"] == 2
    assert len(body["items"]) == 1


@pytest.mark.parametrize(
    "query",
    [
        "page=0",
        "page=-1",
        "per_page=0",
        "per_page=501",
        "page=abc",
        "per_page=abc",
    ],
)
def test_list_patient_admissions_route_rejects_invalid_pagination(
    client,
    clinic,
    make_patient,
    user,
    auth_headers_for,
    query,
):
    patient = make_patient(
        clinic=clinic,
    )

    response = client.get(
        (
            f"/api/v1/wards/patients/{patient.id}/admissions"
            f"?{query}"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_list_patient_admissions_route_rejects_invalid_patient_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/patients/0/admissions",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_current_patient_admission_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, _, admission = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        (
            f"/api/v1/wards/patients/"
            f"{patient.id}/current-admission"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == admission.id
    assert body["patient_id"] == patient.id
    assert body["status"] == (
        AdmissionStatus.ADMITTED.value
    )


def test_current_patient_admission_route_returns_404_when_missing(
    client,
    clinic,
    make_patient,
    user,
    auth_headers_for,
):
    patient = make_patient(
        clinic=clinic,
    )

    response = client.get(
        (
            f"/api/v1/wards/patients/"
            f"{patient.id}/current-admission"
        ),
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404

    assert response.get_json()["message"] == (
        "No active admission found"
    )


def test_current_patient_admission_route_rejects_invalid_patient_id(
    client,
    user,
    auth_headers_for,
):
    response = client.get(
        "/api/v1/wards/patients/0/current-admission",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Admission from reservation


def test_admit_from_reservation_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
            expires_at=_future_expiry(),
            reason="Reserved admission",
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/reservations/"
            f"{reservation.id}/admit"
        ),
        json={
            "reason": "Final admission",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Patient admitted from reservation"
    )

    admission = body["admission"]

    assert admission["id"] is not None
    assert admission["patient_id"] == patient.id
    assert admission["bed_id"] == bed.id
    assert admission["reservation_id"] == (
        reservation.id
    )
    assert admission["status"] == (
        AdmissionStatus.ADMITTED.value
    )


def test_admit_from_reservation_route_missing_reservation(
    client,
    clinic,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        "/api/v1/wards/reservations/999999/admit",
        json={
            "reason": "Admission",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


def test_admit_from_reservation_route_rejects_invalid_payload(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
            expires_at=_future_expiry(),
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/reservations/"
            f"{reservation.id}/admit"
        ),
        json={
            "reason": "x" * 256,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Transfer


def test_transfer_bed_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, source_bed, admission = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
        )
    )

    _, destination_bed = _create_bed(
        clinic,
        ward_name="Transfer Ward",
        bed_number="B-002",
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/transfer"
        ),
        json={
            "to_bed_id": destination_bed.id,
            "reason": "Clinical transfer",
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["message"] == (
        "Patient transferred successfully"
    )

    transfer = body["transfer"]

    assert transfer["id"] is not None
    assert transfer["admission_id"] == admission.id
    assert transfer["from_bed_id"] == source_bed.id
    assert transfer["to_bed_id"] == (
        destination_bed.id
    )
    assert transfer["reason"] == "Clinical transfer"
    assert transfer["transferred_at"] is not None

    assert patient.id == admission.patient_id


def test_transfer_bed_route_rejects_invalid_payload(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, admission = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/transfer"
        ),
        json={
            "to_bed_id": 0,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_transfer_bed_route_rejects_unknown_fields(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, admission = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/transfer"
        ),
        json={
            "to_bed_id": 2,
            "clinic_id": 999,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


def test_transfer_bed_route_missing_admission(
    client,
    user,
    auth_headers_for,
):
    response = client.post(
        "/api/v1/wards/admissions/999999/transfer",
        json={
            "to_bed_id": 1,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 404


# Discharge


def test_discharge_patient_route_success(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, admission = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
            reason="Observation",
        )
    )

    _finalize_empty_discharge_reconciliation(
        clinic,
        admission,
        staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    headers = _headers(
        auth_headers_for,
        user,
    )
    headers["Idempotency-Key"] = (
        "ward-route-discharge-success"
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/discharge"
        ),
        json=_clinical_discharge_payload(
            reason="Patient recovered",
        ),
        headers=headers,
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["message"] == (
        "Patient discharged successfully"
    )

    result = body["admission"]

    assert result["id"] == admission.id
    assert result["patient_id"] == patient.id
    assert result["bed_id"] == bed.id
    assert result["status"] == (
        AdmissionStatus.DISCHARGED.value
    )
    assert body["discharge_summary"]["admission_id"] == (
        admission.id
    )


def test_discharge_patient_route_allows_no_reason(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, admission = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    _finalize_empty_discharge_reconciliation(
        clinic,
        admission,
        staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    payload = _clinical_discharge_payload(
        reason=None,
    )
    payload.pop("reason")

    headers = _headers(
        auth_headers_for,
        user,
    )
    headers["Idempotency-Key"] = (
        "ward-route-discharge-no-reason"
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/discharge"
        ),
        json=payload,
        headers=headers,
    )

    assert response.status_code == 200

    assert (
        response.get_json()["admission"]["status"]
        == AdmissionStatus.DISCHARGED.value
    )


def test_discharge_patient_route_missing_admission(
    client,
    clinic,
    make_staff,
    auth_headers_for,
):
    staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    headers = _headers(
        auth_headers_for,
        user,
    )
    headers["Idempotency-Key"] = (
        "ward-route-missing-admission"
    )

    response = client.post(
        "/api/v1/wards/admissions/999999/discharge",
        json=_clinical_discharge_payload(),
        headers=headers,
    )

    assert response.status_code == 404


def test_discharge_patient_route_rejects_invalid_payload(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    _, staff, _, admission = _create_admission(
        clinic,
        make_patient,
        make_staff,
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.post(
        (
            f"/api/v1/wards/admissions/"
            f"{admission.id}/discharge"
        ),
        json={
            "reason": "x" * 256,
        },
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 422


# Clinic isolation


def test_list_reservations_uses_authenticated_clinic(
    client,
    clinic,
    make_clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    _, _, _, local_reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
        )
    )

    _, _, _, foreign_reservation = (
        _create_reservation(
            foreign_clinic,
            make_patient,
            make_staff,
        )
    )

    local_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
    )

    user = db.session.get(
        type(local_staff.user),
        local_staff.user_id,
    )

    response = client.get(
        "/api/v1/wards/reservations",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert local_reservation.id in ids
    assert foreign_reservation.id not in ids
    assert body["total"] == 1


def test_list_wards_uses_authenticated_clinic(
    client,
    clinic,
    make_clinic,
    make_user,
    auth_headers_for,
):
    foreign_clinic = make_clinic()

    local = _create_ward(
        clinic,
        name="Local Ward",
    )

    foreign = _create_ward(
        foreign_clinic,
        name="Foreign Ward",
    )

    local_user = make_user(
        clinic,
        role=Role.ADMIN,
    )

    response = client.get(
        "/api/v1/wards",
        headers=_headers(
            auth_headers_for,
            local_user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    ids = {
        item["id"]
        for item in body["items"]
    }

    assert local.id in ids
    assert foreign.id not in ids
    assert body["total"] == 1



def test_route_rejects_inactive_authenticated_user(
    client,
    clinic,
    make_user,
    auth_headers_for,
):
    inactive_user = make_user(
        clinic,
        role=Role.ADMIN,
        is_active=False,
    )

    response = client.get(
        "/api/v1/wards",
        headers=_headers(
            auth_headers_for,
            inactive_user,
        ),
    )

    assert response.status_code == 401


def test_reservation_route_requires_linked_staff(
    client,
    clinic,
    make_user,
    auth_headers_for,
):
    user_without_staff = make_user(
        clinic,
        role=Role.DOCTOR,
    )

    response = client.post(
        "/api/v1/wards/reservations",
        json={
            "patient_id": 1,
            "bed_id": 1,
        },
        headers=_headers(
            auth_headers_for,
            user_without_staff,
        ),
    )

    assert response.status_code == 422

    assert response.get_json()["error"] == (
        "Authenticated user is not linked to a staff record"
    )


# Response shapes


def test_reservation_response_contains_expected_fields(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, reservation = (
        _create_reservation(
            clinic,
            make_patient,
            make_staff,
            expires_at=_future_expiry(),
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        f"/api/v1/wards/reservations/{reservation.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == reservation.id
    assert body["patient_id"] == patient.id
    assert body["bed_id"] == bed.id
    assert body["reserved_by_id"] == staff.id
    assert "status" in body
    assert "reason" in body
    assert "reserved_at" in body
    assert "expires_at" in body


def test_admission_response_contains_expected_fields(
    client,
    clinic,
    make_patient,
    make_staff,
    auth_headers_for,
):
    patient, staff, bed, admission = (
        _create_admission(
            clinic,
            make_patient,
            make_staff,
        )
    )

    user = db.session.get(
        type(staff.user),
        staff.user_id,
    )

    response = client.get(
        f"/api/v1/wards/admissions/{admission.id}",
        headers=_headers(
            auth_headers_for,
            user,
        ),
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body["id"] == admission.id
    assert body["patient_id"] == patient.id
    assert body["bed_id"] == bed.id
    assert body["admitted_by_id"] == staff.id
    assert body["status"] == (
        AdmissionStatus.ADMITTED.value
    )
    assert "reason" in body
    assert "admitted_at" in body
    assert "discharged_at" in body
