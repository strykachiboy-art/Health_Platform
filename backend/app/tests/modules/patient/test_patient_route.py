import pytest

from app.core.enums.role_enums import Role


def _headers(
    make_authenticated_staff,
    clinic,
    role,
):
    _, headers = make_authenticated_staff(
        clinic,
        role,
    )
    return headers


def _page_data(response):
    data = response.get_json()["data"]

    assert isinstance(data, dict)
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "per_page" in data

    return data


def _assert_default_page(
    response,
    *,
    expected_total=None,
):
    data = _page_data(response)

    assert data["page"] == 1
    assert data["per_page"] == 50
    assert isinstance(data["items"], list)
    assert data["total"] >= 0

    if expected_total is not None:
        assert data["total"] == expected_total

    return data


class TestPatientRoutes:

    def test_create_patient_requires_receptionist_or_admin(
        self,
        client,
        clinic,
        make_authenticated_staff,
        assert_forbidden,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "A",
                "last_name": "B",
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_create_patient_happy_path_uses_own_clinic(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
            },
            headers=headers,
        )

        assert response.status_code == 201

        body = response.get_json()["data"]

        assert body["clinic_id"] == clinic.id
        assert body["first_name"] == "Jane"
        assert body["last_name"] == "Doe"
        assert body["patient_number"] is not None

    def test_create_patient_rejects_invalid_email_at_schema_layer(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "A",
                "last_name": "B",
                "email": "not-an-email",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_create_patient_rejects_missing_required_name(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "Jane",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_create_patient_rejects_invalid_payload(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "unknown_field": "not allowed",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_create_patient_requires_authentication(
        self,
        client,
    ):
        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
            },
        )

        assert response.status_code == 401

    def test_create_patient_rejects_client_controlled_fields(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            "/api/v1/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "clinic_id": clinic.id,
                "patient_number": "ATTACK",
                "is_active": False,
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_patients_uses_authenticated_users_clinic(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        make_patient(
            clinic,
            first_name="Alice",
        )

        make_patient(
            other_clinic,
            first_name="Bob",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.PHARMACIST,
        )

        response = client.get(
            "/api/v1/patients",
            headers=headers,
        )

        assert response.status_code == 200

        data = _assert_default_page(
            response,
            expected_total=1,
        )

        names = {
            patient["first_name"]
            for patient in data["items"]
        }

        assert names == {"Alice"}

    def test_list_patients_rejects_client_clinic_id(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        make_patient(
            clinic,
            first_name="Alice",
        )

        make_patient(
            other_clinic,
            first_name="Bob",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients?clinic_id={other_clinic.id}",
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_patients_search(
        self,
        client,
        clinic,
        make_patient,
        make_authenticated_staff,
    ):
        make_patient(
            clinic,
            first_name="Alice",
        )

        make_patient(
            clinic,
            first_name="Bob",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.PHARMACIST,
        )

        response = client.get(
            "/api/v1/patients?search=alice",
            headers=headers,
        )

        assert response.status_code == 200

        data = _assert_default_page(
            response,
            expected_total=1,
        )

        names = {
            patient["first_name"]
            for patient in data["items"]
        }

        assert names == {"Alice"}

    def test_list_patients_active_only(
        self,
        client,
        clinic,
        make_patient,
        make_authenticated_staff,
    ):
        active = make_patient(
            clinic,
            first_name="Active",
        )

        inactive = make_patient(
            clinic,
            first_name="Inactive",
        )

        inactive.is_active = False

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?active_only=true",
            headers=headers,
        )

        assert response.status_code == 200

        data = _assert_default_page(
            response,
            expected_total=1,
        )

        ids = {
            patient["id"]
            for patient in data["items"]
        }

        assert active.id in ids
        assert inactive.id not in ids

    def test_list_patients_pagination(
        self,
        client,
        clinic,
        make_patient,
        make_authenticated_staff,
    ):
        for index in range(5):
            make_patient(
                clinic,
                first_name=f"Patient{index}",
                last_name="Pagination",
            )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?page=2&per_page=2",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 2
        assert data["per_page"] == 2
        assert data["total"] == 5
        assert len(data["items"]) == 2

    def test_list_patients_beyond_last_page_is_empty(
        self,
        client,
        clinic,
        make_patient,
        make_authenticated_staff,
    ):
        make_patient(
            clinic,
            first_name="Only",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?page=99&per_page=50",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 99
        assert data["per_page"] == 50
        assert data["total"] == 1
        assert data["items"] == []

    def test_list_patients_rejects_invalid_page(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?page=0",
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_patients_rejects_invalid_per_page(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?per_page=501",
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_patients_rejects_unknown_query_parameter(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients?unknown=value",
            headers=headers,
        )

        assert response.status_code == 422

    def test_get_patient_not_found(
        self,
        client,
        clinic,
        make_authenticated_staff,
        assert_domain_error,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients/999999",
            headers=headers,
        )

        assert_domain_error(
            response,
            404,
        )

    def test_get_patient_requires_authentication(
        self,
        client,
        patient,
    ):
        response = client.get(
            f"/api/v1/patients/{patient.id}",
        )

        assert response.status_code == 401

    def test_get_patient_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{other_patient.id}",
            headers=headers,
        )

        assert response.status_code == 404

    def test_get_patient_rejects_invalid_patient_id(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            "/api/v1/patients/0",
            headers=headers,
        )

        assert response.status_code == 422

    def test_update_patient(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        response = client.patch(
            f"/api/v1/patients/{patient.id}",
            json={
                "first_name": "New",
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"]["first_name"] == "New"

    def test_update_patient_rejects_invalid_payload(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        response = client.patch(
            f"/api/v1/patients/{patient.id}",
            json={
                "unknown_field": "bad",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_update_patient_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        response = client.patch(
            f"/api/v1/patients/{other_patient.id}",
            json={
                "first_name": "Hacked",
            },
            headers=headers,
        )

        assert response.status_code == 404

    def test_update_patient_rejects_invalid_patient_id(
        self,
        client,
        clinic,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        response = client.patch(
            "/api/v1/patients/0",
            json={
                "first_name": "Hacked",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_set_patient_status(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.patch(
            f"/api/v1/patients/{patient.id}/status",
            json={
                "is_active": False,
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"]["is_active"] is False

    def test_set_patient_status_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.patch(
            f"/api/v1/patients/{other_patient.id}/status",
            json={
                "is_active": False,
            },
            headers=headers,
        )

        assert response.status_code == 404


class TestFamilyMemberRoutes:

    def test_add_and_list_family_member(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        add_response = client.post(
            f"/api/v1/patients/{patient.id}/family",
            json={
                "full_name": "John Doe",
                "relation": "spouse",
            },
            headers=headers,
        )

        assert add_response.status_code == 201

        list_response = client.get(
            f"/api/v1/patients/{patient.id}/family",
            headers=headers,
        )

        assert list_response.status_code == 200

        data = _assert_default_page(
            list_response,
            expected_total=1,
        )

        assert len(data["items"]) == 1
        assert data["items"][0]["full_name"] == "John Doe"

    def test_list_family_members_pagination(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        for index in range(5):
            response = client.post(
                f"/api/v1/patients/{patient.id}/family",
                json={
                    "full_name": f"Member {index}",
                    "relation": "child",
                },
                headers=headers,
            )

            assert response.status_code == 201

        response = client.get(
            f"/api/v1/patients/{patient.id}/family?page=2&per_page=2",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 2
        assert data["per_page"] == 2
        assert data["total"] == 5
        assert len(data["items"]) == 2

    def test_list_family_members_rejects_invalid_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/family?page=0",
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_family_members_rejects_unknown_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/family?unknown=value",
            headers=headers,
        )

        assert response.status_code == 422

    def test_add_family_member_rejects_invalid_payload(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/family",
            json={
                "full_name": "John Doe",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_family_routes_reject_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.get(
            f"/api/v1/patients/{other_patient.id}/family",
            headers=headers,
        )

        assert response.status_code == 404

    def test_update_and_remove_family_member(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        add_response = client.post(
            f"/api/v1/patients/{patient.id}/family",
            json={
                "full_name": "Old",
                "relation": "child",
            },
            headers=headers,
        )

        assert add_response.status_code == 201

        member_id = add_response.get_json()["data"]["id"]

        update_response = client.patch(
            f"/api/v1/patients/{patient.id}/family/{member_id}",
            json={
                "full_name": "New",
            },
            headers=headers,
        )

        assert update_response.status_code == 200
        assert (
            update_response.get_json()["data"]["full_name"]
            == "New"
        )

        delete_response = client.delete(
            f"/api/v1/patients/{patient.id}/family/{member_id}",
            headers=headers,
        )

        assert delete_response.status_code == 200

    def test_update_family_member_rejects_invalid_payload(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        add_response = client.post(
            f"/api/v1/patients/{patient.id}/family",
            json={
                "full_name": "Old",
                "relation": "child",
            },
            headers=headers,
        )

        assert add_response.status_code == 201

        member_id = add_response.get_json()["data"]["id"]

        response = client.patch(
            f"/api/v1/patients/{patient.id}/family/{member_id}",
            json={
                "unknown_field": "bad",
            },
            headers=headers,
        )

        assert response.status_code == 422


class TestInsuranceRoutes:

    def test_add_and_list_insurance(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        add_response = client.post(
            f"/api/v1/patients/{patient.id}/insurance",
            json={
                "provider_name": "Acme",
                "policy_number": "P1",
            },
            headers=headers,
        )

        assert add_response.status_code == 201

        view_headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ACCOUNTANT,
        )

        list_response = client.get(
            f"/api/v1/patients/{patient.id}/insurance",
            headers=view_headers,
        )

        assert list_response.status_code == 200

        data = _assert_default_page(
            list_response,
            expected_total=1,
        )

        assert len(data["items"]) == 1
        assert data["items"][0]["provider_name"] == "Acme"

    def test_list_insurance_pagination(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        for index in range(5):
            response = client.post(
                f"/api/v1/patients/{patient.id}/insurance",
                json={
                    "provider_name": f"Provider {index}",
                    "policy_number": f"POL-{index}",
                },
                headers=headers,
            )

            assert response.status_code == 201

        response = client.get(
            f"/api/v1/patients/{patient.id}/insurance?page=2&per_page=2",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 2
        assert data["per_page"] == 2
        assert data["total"] == 5
        assert len(data["items"]) == 2

    def test_list_insurance_rejects_invalid_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ACCOUNTANT,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/insurance?per_page=501",
            headers=headers,
        )

        assert response.status_code == 422

    def test_list_insurance_rejects_unknown_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ACCOUNTANT,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/insurance?unknown=value",
            headers=headers,
        )

        assert response.status_code == 422

    def test_add_insurance_rejects_invalid_payload(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/insurance",
            json={
                "provider_name": "Acme",
                "policy_number": "",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_update_insurance(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ADMIN,
        )

        add_response = client.post(
            f"/api/v1/patients/{patient.id}/insurance",
            json={
                "provider_name": "Acme",
                "policy_number": "P1",
            },
            headers=headers,
        )

        assert add_response.status_code == 201

        insurance_id = add_response.get_json()["data"]["id"]

        update_response = client.patch(
            f"/api/v1/patients/{patient.id}/insurance/{insurance_id}",
            json={
                "is_active": False,
            },
            headers=headers,
        )

        assert update_response.status_code == 200
        assert (
            update_response.get_json()["data"]["is_active"]
            is False
        )

    def test_insurance_routes_reject_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.ACCOUNTANT,
        )

        response = client.get(
            f"/api/v1/patients/{other_patient.id}/insurance",
            headers=headers,
        )

        assert response.status_code == 404


class TestVitalsRoutes:

    def test_record_and_get_latest_vitals(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        record_response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 75,
            },
            headers=headers,
        )

        assert record_response.status_code == 201

        latest_response = client.get(
            f"/api/v1/patients/{patient.id}/vitals/latest",
            headers=headers,
        )

        assert latest_response.status_code == 200
        assert (
            latest_response.get_json()["data"]["heart_rate"]
            == 75
        )

    def test_record_vitals_maps_heart_rate_to_bpm(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 72,
            },
            headers=headers,
        )

        assert response.status_code == 201

        data = response.get_json()["data"]

        assert data["heart_rate"] == 72

    def test_record_vitals_rejects_empty_payload(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

    def test_record_vitals_rejects_unknown_field(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "made_up_vital": 123,
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_record_vitals_rejects_client_recorded_at(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 75,
                "recorded_at": "2020-01-01T00:00:00",
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_get_latest_vitals_null_when_none_recorded(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals/latest",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"] is None

    def test_get_vitals_history(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        first_response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 60,
            },
            headers=headers,
        )

        second_response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 90,
            },
            headers=headers,
        )

        assert first_response.status_code == 201
        assert second_response.status_code == 201

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals",
            headers=headers,
        )

        assert response.status_code == 200

        data = _assert_default_page(
            response,
            expected_total=2,
        )

        assert len(data["items"]) == 2

    def test_get_vitals_history_pagination(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        for heart_rate in range(70, 75):
            response = client.post(
                f"/api/v1/patients/{patient.id}/vitals",
                json={
                    "heart_rate": heart_rate,
                },
                headers=headers,
            )

            assert response.status_code == 201

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals?page=2&per_page=2",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 2
        assert data["per_page"] == 2
        assert data["total"] == 5
        assert len(data["items"]) == 2

    def test_get_vitals_history_beyond_last_page_is_empty(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 75,
            },
            headers=headers,
        )

        assert response.status_code == 201

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals?page=99&per_page=50",
            headers=headers,
        )

        assert response.status_code == 200

        data = _page_data(response)

        assert data["page"] == 99
        assert data["total"] == 1
        assert data["items"] == []

    def test_get_vitals_history_rejects_invalid_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals?page=0",
            headers=headers,
        )

        assert response.status_code == 422

    def test_get_vitals_history_rejects_unknown_query(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{patient.id}/vitals?unknown=value",
            headers=headers,
        )

        assert response.status_code == 422

    def test_record_vitals_requires_clinical_role(
        self,
        client,
        clinic,
        patient,
        make_authenticated_staff,
        assert_forbidden,
    ):
        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.RECEPTIONIST,
        )

        response = client.post(
            f"/api/v1/patients/{patient.id}/vitals",
            json={
                "heart_rate": 75,
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_record_vitals_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.NURSE,
        )

        response = client.post(
            f"/api/v1/patients/{other_patient.id}/vitals",
            json={
                "heart_rate": 75,
            },
            headers=headers,
        )

        assert response.status_code == 404

    def test_get_vitals_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{other_patient.id}/vitals",
            headers=headers,
        )

        assert response.status_code == 404

    def test_latest_vitals_rejects_other_clinic_patient(
        self,
        client,
        clinic,
        make_clinic,
        make_patient,
        make_authenticated_staff,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        other_patient = make_patient(
            other_clinic,
            first_name="Other",
        )

        headers = _headers(
            make_authenticated_staff,
            clinic,
            Role.DOCTOR,
        )

        response = client.get(
            f"/api/v1/patients/{other_patient.id}/vitals/latest",
            headers=headers,
        )

        assert response.status_code == 404
