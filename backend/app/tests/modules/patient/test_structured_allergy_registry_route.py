from __future__ import annotations

from app.core.enums.clinic_enums import (
    ClinicStatus,
)
from app.core.enums.role_enums import Role

import pytest
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.modules.clinic.models.organization_model import TenantMembership, ClinicMembership


@pytest.fixture
def make_authenticated_staff(app, db, make_staff):
    # This module uses actual tenant grants and the real token issuer.
    def make(clinic, role):
        staff = make_staff(clinic, role=role)
        db.session.add(TenantMembership(user_id=staff.user_id, organization_id=clinic.organization_id, role="member", is_active=True, status="active"))
        db.session.flush()
        db.session.add(ClinicMembership(user_id=staff.user_id, organization_id=clinic.organization_id, clinic_id=clinic.id, role=role.value, is_active=True))
        db.session.flush()
        with app.test_request_context():
            pair = issue_auth_tokens(staff.user, clinic_context_id=clinic.id)
        return staff, {"Authorization": "Bearer " + pair["access_token"]}
    return make


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
            "expected_updated_at": created.get_json()["data"]["updated_at"],
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
            "expected_updated_at": created.get_json()["data"]["updated_at"],
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
            "expected_updated_at": resolved.get_json()["data"]["updated_at"],
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
            "expected_updated_at": refuted.get_json()["data"]["updated_at"],
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

    assert response.status_code == 404
    missing = client.get(f"/api/v1/patients/{patient.id + 999999}/allergies", headers=headers)
    assert missing.status_code == 404


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


@pytest.fixture
def revision_case(client, clinic, patient, make_authenticated_staff):
    _activate(clinic, patient)
    _, headers = make_authenticated_staff(clinic, Role.DOCTOR)
    root = f"/api/v1/patients/{patient.id}/allergies"
    created = client.post(root, headers=headers, json={"allergen_name": "Synthetic allergen"})
    assert created.status_code == 201, created.get_json()
    row = created.get_json()["data"]
    return f"{root}/{row['id']}", headers, row


@pytest.mark.parametrize("suffix,body", [("", {"reaction": "new"}), ("/status", {"clinical_status": "resolved", "reason": "review"})])
@pytest.mark.parametrize("bad_token", [None, "2026-01-01T00:00:00", 123, "invalid"])
def test_review_token_is_mandatory_and_timezone_aware(client, revision_case, suffix, body, bad_token):
    path, headers, row = revision_case
    request_body = dict(body)
    if bad_token is not None:
        request_body["expected_updated_at"] = bad_token
    denied = client.patch(path + suffix, headers=headers, json=request_body)
    assert denied.status_code == 422, denied.get_json()
    assert client.get(path, headers=headers).get_json()["data"] == row


def test_reviewed_edit_then_stale_edit_and_status_rejected(client, revision_case):
    path, headers, row = revision_case
    token = row["updated_at"]
    changed = client.patch(path, headers=headers, json={"reaction": "new", "expected_updated_at": token})
    assert changed.status_code == 200, changed.get_json()
    latest = changed.get_json()["data"]
    assert latest["updated_at"] != token
    for suffix, body in [("", {"reaction": "stale"}), ("/status", {"clinical_status": "resolved", "reason": "stale"})]:
        denied = client.patch(path + suffix, headers=headers, json={**body, "expected_updated_at": token})
        assert denied.status_code == 409, denied.get_json()
    assert client.get(path, headers=headers).get_json()["data"] == latest
    accepted = client.patch(path + "/status", headers=headers, json={"verification_status": "refuted", "reason": "review", "expected_updated_at": latest["updated_at"]})
    assert accepted.status_code == 200, accepted.get_json()
    assert accepted.get_json()["data"]["clinical_status"] == "inactive"
    assert accepted.get_json()["data"]["updated_at"] != latest["updated_at"]


def test_token_only_is_not_an_edit(client, revision_case):
    path, headers, row = revision_case
    assert client.patch(path, headers=headers, json={"expected_updated_at": row["updated_at"]}).status_code == 422


def test_status_then_stale_edit_rejected(client, revision_case):
    path, headers, row = revision_case
    token = row["updated_at"]
    changed = client.patch(path + "/status", headers=headers, json={"clinical_status": "resolved", "reason": "review", "expected_updated_at": token})
    assert changed.status_code == 200, changed.get_json()
    denied = client.patch(path, headers=headers, json={"severity": "mild", "expected_updated_at": token})
    assert denied.status_code == 409, denied.get_json()
    assert client.get(path, headers=headers).get_json()["data"] == changed.get_json()["data"]


def test_explicit_null_review_token_rejected(client, revision_case):
    path, headers, row = revision_case
    assert client.patch(path, headers=headers, json={"reaction": "new", "expected_updated_at": None}).status_code == 422
    assert client.get(path, headers=headers).get_json()["data"] == row


def test_foreign_clinic_cannot_use_valid_token(client, revision_case, make_clinic, make_authenticated_staff):
    path, headers, row = revision_case
    other = make_clinic()
    _, foreign = make_authenticated_staff(other, Role.DOCTOR)
    for suffix, body in [("", {"reaction": "foreign"}), ("/status", {"clinical_status": "resolved", "reason": "foreign"})]:
        response = client.patch(path + suffix, headers=foreign, json={**body, "expected_updated_at": row["updated_at"]})
        assert response.status_code == 404, response.get_json()
    assert client.get(path, headers=headers).get_json()["data"] == row
