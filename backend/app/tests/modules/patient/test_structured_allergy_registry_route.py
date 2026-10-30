from __future__ import annotations

from app.core.enums.clinic_enums import (
    ClinicStatus,
)
from app.core.enums.role_enums import Role


def _headers(
    make_authenticated_staff,
    clinic,
    role,
):
    staff, headers = (
        make_authenticated_staff(
            clinic,
            role,
        )
    )

    return staff, headers


def _activate(
    clinic,
    patient,
):
    clinic.status = ClinicStatus.ACTIVE
    patient.clinic_id = clinic.id
    patient.is_active = True


def test_create_allergy_route_happy_path_server_controls_context(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    staff, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    response = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "  Penicillin  ",
            "allergen_code": "  RXNORM:7980  ",
            "allergen_type": "drug",
            "reaction": "  urticaria  ",
            "severity": "severe",
        },
        headers=headers,
    )

    assert response.status_code == 201

    body = response.get_json()

    assert body["success"] is True

    data = body["data"]

    assert data["clinic_id"] == clinic.id
    assert data["patient_id"] == patient.id
    assert data["allergen_name"] == "Penicillin"
    assert data["allergen_code"] == "RXNORM:7980"
    assert data["allergen_type"] == "drug"
    assert data["reaction"] == "urticaria"
    assert data["severity"] == "severe"
    assert data["clinical_status"] == "active"
    assert data["verification_status"] == "unconfirmed"
    assert data["recorded_by_id"] == staff.id
    assert data["recorded_at"] is not None


def test_create_allergy_route_rejects_server_controlled_status(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    response = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
            "clinical_status": "resolved",
        },
        headers=headers,
    )

    assert response.status_code == 422


def test_create_allergy_route_rejects_receptionist(
    client,
    clinic,
    patient,
    make_authenticated_staff,
    assert_forbidden,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.RECEPTIONIST,
    )

    response = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
        },
        headers=headers,
    )

    assert_forbidden(
        response
    )


def test_list_and_get_allergy_routes(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    created = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Peanut",
            "allergen_type": "food",
            "severity": "moderate",
        },
        headers=headers,
    )

    assert created.status_code == 201

    allergy_id = (
        created.get_json()["data"]["id"]
    )

    listed = client.get(
        (
            f"/api/v1/patients/{patient.id}/allergies"
            "?page=1&per_page=50"
        ),
        headers=headers,
    )

    assert listed.status_code == 200

    list_data = listed.get_json()["data"]

    assert list_data["page"] == 1
    assert list_data["per_page"] == 50
    assert list_data["total"] == 1
    assert len(list_data["items"]) == 1
    assert (
        list_data["items"][0]["id"]
        == allergy_id
    )

    fetched = client.get(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{allergy_id}"
        ),
        headers=headers,
    )

    assert fetched.status_code == 200
    assert (
        fetched.get_json()["data"]["id"]
        == allergy_id
    )


def test_update_allergy_route(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.NURSE,
    )

    created = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
        },
        headers=headers,
    )

    assert created.status_code == 201

    allergy_id = (
        created.get_json()["data"]["id"]
    )

    response = client.patch(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{allergy_id}"
        ),
        json={
            "reaction": "Anaphylaxis",
            "severity": "life_threatening",
        },
        headers=headers,
    )

    assert response.status_code == 200

    data = response.get_json()["data"]

    assert data["reaction"] == "Anaphylaxis"
    assert (
        data["severity"]
        == "life_threatening"
    )


def test_refute_status_route_forces_inactive(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.PHARMACIST,
    )

    created = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Shellfish",
            "allergen_type": "food",
        },
        headers=headers,
    )

    assert created.status_code == 201

    allergy_id = (
        created.get_json()["data"]["id"]
    )

    response = client.patch(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{allergy_id}/status"
        ),
        json={
            "verification_status": "refuted",
            "reason": "Negative challenge",
        },
        headers=headers,
    )

    assert response.status_code == 200

    data = response.get_json()["data"]

    assert (
        data["verification_status"]
        == "refuted"
    )

    assert (
        data["clinical_status"]
        == "inactive"
    )


def test_list_filters_active_non_refuted(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    active = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
            "allergen_type": "drug",
        },
        headers=headers,
    )

    resolved = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Latex",
            "allergen_type": "latex",
        },
        headers=headers,
    )

    refuted = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Egg",
            "allergen_type": "food",
        },
        headers=headers,
    )

    assert active.status_code == 201
    assert resolved.status_code == 201
    assert refuted.status_code == 201

    resolved_id = (
        resolved.get_json()["data"]["id"]
    )

    refuted_id = (
        refuted.get_json()["data"]["id"]
    )

    resolved_response = client.patch(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{resolved_id}/status"
        ),
        json={
            "clinical_status": "resolved",
            "reason": "Resolved",
        },
        headers=headers,
    )

    refuted_response = client.patch(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{refuted_id}/status"
        ),
        json={
            "verification_status": "refuted",
            "reason": "Negative challenge",
        },
        headers=headers,
    )

    assert resolved_response.status_code == 200
    assert refuted_response.status_code == 200

    response = client.get(
        (
            f"/api/v1/patients/{patient.id}/allergies"
            "?active_only=true&include_refuted=false"
        ),
        headers=headers,
    )

    assert response.status_code == 200

    data = response.get_json()["data"]

    assert data["total"] == 1
    assert (
        data["items"][0]["allergen_name"]
        == "Penicillin"
    )


def test_duplicate_active_allergy_returns_conflict(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    first = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
            "allergen_code": "RXNORM:7980",
            "allergen_type": "drug",
        },
        headers=headers,
    )

    assert first.status_code == 201

    duplicate = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin G",
            "allergen_code": "rxnorm:7980",
            "allergen_type": "drug",
        },
        headers=headers,
    )

    assert duplicate.status_code == 409


def test_allergy_route_rejects_cross_clinic_patient(
    client,
    db,
    clinic,
    patient,
    make_clinic,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    other_clinic = make_clinic()
    other_clinic.status = ClinicStatus.ACTIVE

    _, headers = _headers(
        make_authenticated_staff,
        other_clinic,
        Role.DOCTOR,
    )

    db.session.flush()

    response = client.get(
        f"/api/v1/patients/{patient.id}/allergies",
        headers=headers,
    )

    assert response.status_code == 422


def test_allergy_delete_route_is_absent(
    client,
    clinic,
    patient,
    make_authenticated_staff,
):
    _activate(
        clinic,
        patient,
    )

    _, headers = _headers(
        make_authenticated_staff,
        clinic,
        Role.DOCTOR,
    )

    created = client.post(
        f"/api/v1/patients/{patient.id}/allergies",
        json={
            "allergen_name": "Penicillin",
        },
        headers=headers,
    )

    assert created.status_code == 201

    allergy_id = (
        created.get_json()["data"]["id"]
    )

    response = client.delete(
        (
            f"/api/v1/patients/{patient.id}/allergies/"
            f"{allergy_id}"
        ),
        headers=headers,
    )

    assert response.status_code in {
        404,
        405,
    }
