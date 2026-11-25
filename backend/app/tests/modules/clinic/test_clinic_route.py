import pytest

from app.core.enums.clinic_enums import ClinicStatus, ClinicType
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.clinic.routes import clinic_route
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.extensions import db
from app.modules.clinic.models.organization_model import ClinicMembership, HospitalOrganization


def _grant(user, clinic):
    # Positive fixture only. No production fallback, no broad implicit grants.
    db.session.add(ClinicMembership(user_id=user.id, clinic_id=clinic.id,
        organization_id=clinic.organization_id, role=user.role.value, is_active=True))
    db.session.flush()


def _platform_headers(user, clinic):
    return {"Authorization": "Bearer " + issue_auth_tokens(user, clinic_context_id=clinic.id)["access_token"]}



# ============================================================================
# HELPERS
# ============================================================================


def admin_headers(auth_headers_for, user):
    return auth_headers_for(user, role=Role.ADMIN)


def super_admin_headers(auth_headers_for, user):
    # Use a genuine SUPER_ADMIN database identity and matching signed JWT.
    user.role = Role.SUPER_ADMIN
    return auth_headers_for(user)


def user_headers(auth_headers_for, user):
    return auth_headers_for(user)


# ============================================================================
# SERIALIZATION / RESPONSE SHAPE
# ============================================================================


class TestClinicSerialization:
    def test_get_clinic_returns_expected_shape(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{clinic.id}",
            headers=headers,
        )

        assert response.status_code == 200

        body = response.get_json()

        assert "data" in body

        data = body["data"]

        expected_keys = {
            "id",
            "name",
            "clinic_type",
            "status",
            "parent_clinic_id",
            "is_headquarters",
            "address",
            "city",
            "country",
            "phone",
            "email",
            "timezone",
            "opening_time",
            "closing_time",
            "ai_credits",
            "ai_requests_this_month",
            "created_at",
            "updated_at",
        }

        assert set(data.keys()) == expected_keys

        assert data["id"] == clinic.id
        assert data["name"] == clinic.name
        assert data["clinic_type"] == clinic.clinic_type.value
        assert data["status"] == clinic.status.value

    def test_clinic_response_never_exposes_api_token(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        db_session,
    ):
        clinic.api_token = "secret-token"
        db_session.flush()

        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{clinic.id}",
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert "api_token" not in data
        assert "token" not in data


# ============================================================================
# AUTHENTICATION
# ============================================================================


class TestClinicRouteAuthentication:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/clinics"),
            ("GET", "/api/v1/clinics/1"),
            ("GET", "/api/v1/clinics/1/branches"),
            ("POST", "/api/v1/clinics"),
            ("POST", "/api/v1/clinics/1/branches"),
            ("PATCH", "/api/v1/clinics/1"),
            ("PATCH", "/api/v1/clinics/1/branch-configuration"),
            ("PATCH", "/api/v1/clinics/1/status"),
            ("PATCH", "/api/v1/clinics/1/ai-credits"),
            ("POST", "/api/v1/clinics/1/api-token/regenerate"),
        ],
    )
    def test_missing_authentication_is_rejected(
        self,
        client,
        method,
        path,
    ):
        response = client.open(
            path,
            method=method,
            json={},
        )

        assert response.status_code in (401, 422)


# ============================================================================
# ROLE AUTHORIZATION
# ============================================================================


class TestClinicRouteAuthorization:
    def test_super_admin_can_create_clinic(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "New Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 201

    def test_admin_cannot_create_clinic(
        self,
        client,
        user,
        auth_headers_for,
        assert_forbidden,
    ):
        # Hospital ADMIN is intentionally denied new root clinic provisioning.
        response = client.post(
            "/api/v1/clinics",
            json={"name": "Denied Admin Root Clinic"},
            headers=admin_headers(auth_headers_for, user),
        )
        assert_forbidden(response)

    def test_non_admin_cannot_create_clinic(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Unauthorized Clinic",
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_create_branch(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={
                "name": "Unauthorized Branch",
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_update_clinic(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "name": "Unauthorized Update",
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_update_branch_configuration(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/branch-configuration",
            json={
                "is_headquarters": True,
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_change_status(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_modify_ai_credits(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": 10,
            },
            headers=headers,
        )

        assert_forbidden(response)

    def test_non_admin_cannot_regenerate_api_token(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
        assert_forbidden,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/api-token/regenerate",
            headers=headers,
        )

        assert_forbidden(response)


# ============================================================================
# CREATE CLINIC
# ============================================================================


class TestCreateClinicRoute:
    def test_create_clinic_success(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Created Clinic",
                "clinic_type": ClinicType.GENERAL.value,
                "timezone": "UTC",
            },
            headers=headers,
        )

        assert response.status_code == 201

        body = response.get_json()

        assert body["message"] == "Clinic created successfully"

        data = body["data"]

        assert data["name"] == "Created Clinic"
        assert data["clinic_type"] == ClinicType.GENERAL.value
        assert data["status"] == ClinicStatus.ACTIVE.value
        assert data["parent_clinic_id"] is None
        assert data["is_headquarters"] is False

    def test_create_clinic_with_full_profile(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Full Profile Clinic",
                "clinic_type": ClinicType.SPECIALIST.value,
                "address": "123 Hospital Road",
                "city": "Port Harcourt",
                "country": "Nigeria",
                "phone": "+2348000000000",
                "email": "clinic@test.com",
                "timezone": "Africa/Lagos",
                "opening_time": "08:00:00",
                "closing_time": "18:00:00",
            },
            headers=headers,
        )

        assert response.status_code == 201

        data = response.get_json()["data"]

        assert data["name"] == "Full Profile Clinic"
        assert data["clinic_type"] == ClinicType.SPECIALIST.value
        assert data["address"] == "123 Hospital Road"
        assert data["city"] == "Port Harcourt"
        assert data["country"] == "Nigeria"
        assert data["timezone"] == "Africa/Lagos"
        assert data["opening_time"] == "08:00:00"
        assert data["closing_time"] == "18:00:00"

    def test_create_clinic_validation_error(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert "details" in body
        assert body["details"]

    def test_create_clinic_invalid_timezone(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Invalid Timezone Clinic",
                "timezone": "Not/A/Timezone",
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid timezone 'Not/A/Timezone'"
        )

    def test_create_clinic_missing_parent_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Child Clinic",
                "parent_clinic_id": 99999,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Parent clinic 99999 not found"
        )

    def test_create_clinic_duplicate_name_returns_409(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": clinic.name,
            },
            headers=headers,
        )

        assert response.status_code == 409

        assert clinic.name in response.get_json()["error"]

    def test_create_clinic_rejects_forbidden_extra_field(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Extra Field Clinic",
                "clinic_id": 999,
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_create_headquarters_clinic(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Headquarters Clinic",
                "is_headquarters": True,
            },
            headers=headers,
        )

        assert response.status_code == 201

        data = response.get_json()["data"]

        assert data["is_headquarters"] is True
        assert data["parent_clinic_id"] is None


# ============================================================================
# LIST CLINICS
# ============================================================================


class TestListClinicsRoute:
    def test_admin_lists_only_explicitly_granted_clinics(
        self,
        client,
        make_clinic,
        user,
        auth_headers_for,
    ):
        second = make_clinic(
            name="Second Clinic",
        )

        _grant(user, second)
        ungranted = make_clinic(name="Ungrantable sibling")
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics",
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        ids = {item["id"] for item in data}

        assert user.clinic_id in ids
        assert second.id in ids

        assert ungranted.id not in ids
        assert ids == {user.clinic_id, second.id}

    def test_admin_can_filter_by_status(
        self,
        client,
        clinic,
        suspended_clinic,
        user,
        auth_headers_for,
    ):
        _grant(user, suspended_clinic)
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics",
            query_string={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == suspended_clinic.id
        assert data[0]["status"] == ClinicStatus.SUSPENDED.value

    def test_invalid_status_filter_returns_400(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics",
            query_string={
                "status": "not-a-real-status",
            },
            headers=headers,
        )

        assert response.status_code == 400

        assert (
            response.get_json()["error"]
            == "Invalid clinic status 'not-a-real-status'"
        )

    def test_non_admin_only_receives_own_clinic(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            "/api/v1/clinics",
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == clinic.id
        assert data[0]["id"] != other_clinic.id

    def test_non_admin_status_mismatch_returns_empty_list(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            "/api/v1/clinics",
            query_string={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"] == []

    def test_non_admin_without_clinic_assignment_is_forbidden(
        self,
        client,
        make_user,
        auth_headers_for,
    ):
        doctor = make_user(
            clinic=None,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            "/api/v1/clinics",
            headers=headers,
        )

        assert response.status_code == 403

        assert (
            response.get_json()["error"]
            == "Clinic context is required"
        )


# ============================================================================
# GET CLINIC
# ============================================================================


class TestGetClinicRoute:
    def test_admin_gets_explicitly_granted_clinic(
        self,
        client,
        make_clinic,
        user,
        auth_headers_for,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        denied = client.get(f"/api/v1/clinics/{other_clinic.id}", headers=admin_headers(auth_headers_for, user))
        assert denied.status_code == 403 and "data" not in denied.get_json()
        _grant(user, other_clinic)
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{other_clinic.id}",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"]["id"] == other_clinic.id

    def test_non_admin_gets_own_clinic(
        self,
        client,
        clinic,
        make_user,
        auth_headers_for,
    ):
        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{clinic.id}",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"]["id"] == clinic.id

    def test_non_admin_cannot_get_other_clinic(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{other_clinic.id}",
            headers=headers,
        )

        assert response.status_code == 403

        assert (
            response.get_json()["error"]
            == "You do not have access to this clinic"
        )

    def test_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics/99999",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )

    def test_zero_clinic_id_is_rejected_by_route(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics/0",
            headers=headers,
        )

        assert response.status_code == 400

        assert (
            response.get_json()["error"]
            == "Invalid clinic ID"
        )



# ============================================================================
# WD13 G05: SUPER ADMIN ROOT CLINIC REGISTRY READ PARITY
# ============================================================================


class TestSuperAdminClinicRegistryReadParity:
    def test_super_admin_without_context_lists_all_root_clinics(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        first = make_clinic(name="Registry First")
        second = make_clinic(name="Registry Second")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)

        response = client.get(
            "/api/v1/clinics", headers=auth_headers_for(sa),
        )
        assert response.status_code == 200
        ids = {item["id"] for item in response.get_json()["data"]}
        assert first.id in ids and second.id in ids

    def test_super_admin_selected_context_still_lists_global_registry(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        selected = make_clinic(name="Context Selected")
        other = make_clinic(name="Context Other")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            "/api/v1/clinics",
            headers=_platform_headers(sa, selected),
        )
        assert response.status_code == 200
        ids = {item["id"] for item in response.get_json()["data"]}
        assert selected.id in ids and other.id in ids

    def test_super_admin_can_filter_global_registry_by_status(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        active = make_clinic(name="Status Active")
        suspended = make_clinic(
            name="Status Suspended", status=ClinicStatus.SUSPENDED,
        )
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            "/api/v1/clinics",
            query_string={"status": ClinicStatus.SUSPENDED.value},
            headers=auth_headers_for(sa),
        )
        assert response.status_code == 200
        ids = {item["id"] for item in response.get_json()["data"]}
        assert suspended.id in ids
        assert active.id not in ids

    def test_super_admin_without_context_reads_any_clinic(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        target = make_clinic(name="Global Detail")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            f"/api/v1/clinics/{target.id}",
            headers=auth_headers_for(sa),
        )
        assert response.status_code == 200
        assert response.get_json()["data"]["id"] == target.id

    def test_super_admin_selected_context_reads_other_clinic(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        selected = make_clinic(name="Detail Selected")
        other = make_clinic(name="Detail Other")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            f"/api/v1/clinics/{other.id}",
            headers=_platform_headers(sa, selected),
        )
        assert response.status_code == 200
        assert response.get_json()["data"]["id"] == other.id

    def test_super_admin_unknown_clinic_still_returns_404(
        self, client, make_user, auth_headers_for,
    ):
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            "/api/v1/clinics/999999", headers=auth_headers_for(sa),
        )
        assert response.status_code == 404

    def test_unprivileged_patient_cannot_list_or_read_registry(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        clinic = make_clinic(name="Patient Denied Clinic")
        patient = make_user(clinic=clinic, role=Role.PATIENT)
        headers = auth_headers_for(patient)
        list_response = client.get("/api/v1/clinics", headers=headers)
        detail_response = client.get(
            f"/api/v1/clinics/{clinic.id}", headers=headers,
        )
        assert list_response.status_code == 403
        assert detail_response.status_code == 403

    def test_super_admin_branch_list_is_not_newly_granted(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        clinic = make_clinic(name="Branch Boundary")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.get(
            f"/api/v1/clinics/{clinic.id}/branches",
            headers=auth_headers_for(sa),
        )
        assert response.status_code == 403

    def test_super_admin_cannot_use_admin_only_clinic_update(
        self, client, make_user, make_clinic, auth_headers_for,
    ):
        clinic = make_clinic(name="Update Boundary")
        sa = make_user(clinic=None, role=Role.SUPER_ADMIN)
        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={"name": "Denied"}, headers=auth_headers_for(sa),
        )
        assert response.status_code == 403


# ============================================================================
# LIST BRANCHES
# ============================================================================


class TestListClinicBranchesRoute:
    def test_list_branches_success(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        branch = make_clinic(
            name="Branch Clinic",
            parent_clinic_id=clinic.id,
        )

        _grant(user, branch)
        ungranted = make_clinic(name="Unassigned Branch", parent_clinic_id=clinic.id)
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{clinic.id}/branches",
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert len(data) == 1
        assert data[0]["id"] == branch.id
        assert data[0]["parent_clinic_id"] == clinic.id

        assert all(row["id"] != ungranted.id for row in data)

    def test_non_admin_can_list_own_clinic_branches(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        branch = make_clinic(
            name="Branch Clinic",
            parent_clinic_id=clinic.id,
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        _grant(doctor, branch)
        ungranted = make_clinic(name="Other unassigned branch", parent_clinic_id=clinic.id)
        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{clinic.id}/branches",
            headers=headers,
        )

        assert response.status_code == 200
        assert response.get_json()["data"][0]["id"] == branch.id

        assert [row["id"] for row in response.get_json()["data"]] == [branch.id]

    def test_non_admin_cannot_list_other_clinic_branches(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        other_clinic = make_clinic(
            name="Other Clinic",
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{other_clinic.id}/branches",
            headers=headers,
        )

        assert response.status_code == 403

    def test_missing_parent_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics/99999/branches",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )


# ============================================================================
# CREATE BRANCH
# ============================================================================


class TestCreateClinicBranchRoute:
    def test_create_branch_success(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={
                "name": "New Branch",
                "clinic_type": ClinicType.GENERAL.value,
            },
            headers=headers,
        )

        assert response.status_code == 201

        body = response.get_json()

        assert (
            body["message"]
            == "Clinic branch created successfully"
        )

        data = body["data"]

        assert data["name"] == "New Branch"
        assert data["parent_clinic_id"] == clinic.id
        assert data["is_headquarters"] is False
        assert data["status"] == ClinicStatus.ACTIVE.value

    def test_create_branch_validation_error(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_create_branch_rejects_client_supplied_parent_id(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={
                "name": "Branch",
                "parent_clinic_id": clinic.id,
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_create_branch_missing_parent_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics/99999/branches",
            json={
                "name": "Missing Parent Branch",
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Parent clinic 99999 not found"
        )

    def test_create_branch_duplicate_name_returns_409(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        make_clinic(
            name="Existing Branch",
            parent_clinic_id=clinic.id,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={
                "name": "Existing Branch",
            },
            headers=headers,
        )

        assert response.status_code == 409

        assert "already exists" in response.get_json()["error"]


# ============================================================================
# UPDATE BRANCH CONFIGURATION
# ============================================================================


class TestUpdateClinicBranchConfigurationRoute:
    def test_update_branch_configuration_success(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        branch = make_clinic(
            name="Branch Clinic",
            parent_clinic_id=clinic.id,
        )

        new_parent = make_clinic(
            name="New Parent",
        )

        _grant(user, branch)
        _grant(user, new_parent)
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{branch.id}/branch-configuration",
            json={
                "parent_clinic_id": new_parent.id,
            },
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert data["parent_clinic_id"] == new_parent.id

    def test_update_branch_configuration_can_detach_branch(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        branch = make_clinic(
            name="Branch Clinic",
            parent_clinic_id=clinic.id,
        )

        _grant(user, branch)
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{branch.id}/branch-configuration",
            json={
                "parent_clinic_id": None,
            },
            headers=headers,
        )

        assert response.status_code == 200

        data = response.get_json()["data"]

        assert data["parent_clinic_id"] is None

    def test_update_branch_configuration_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            "/api/v1/clinics/99999/branch-configuration",
            json={
                "is_headquarters": True,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )

    def test_update_branch_configuration_invalid_parent_returns_404(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/branch-configuration",
            json={
                "parent_clinic_id": 99999,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Parent clinic 99999 not found"
        )

    def test_update_branch_configuration_rejects_self_parent(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/branch-configuration",
            json={
                "parent_clinic_id": clinic.id,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "A clinic cannot be its own parent"
        )

    def test_update_branch_configuration_rejects_hq_with_parent(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        branch = make_clinic(
            name="Branch Clinic",
            parent_clinic_id=clinic.id,
        )

        _grant(user, branch)
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{branch.id}/branch-configuration",
            json={
                "parent_clinic_id": clinic.id,
                "is_headquarters": True,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "A headquarters clinic cannot have a parent clinic"
        )


# ============================================================================
# UPDATE CLINIC
# ============================================================================


class TestUpdateClinicRoute:
    def test_update_clinic_success(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "name": "Updated Clinic",
                "city": "Lagos",
                "timezone": "Africa/Lagos",
                "opening_time": "08:00:00",
                "closing_time": "18:00:00",
            },
            headers=headers,
        )

        assert response.status_code == 200

        body = response.get_json()

        assert body["message"] == "Clinic updated successfully"

        data = body["data"]

        assert data["name"] == "Updated Clinic"
        assert data["city"] == "Lagos"
        assert data["timezone"] == "Africa/Lagos"
        assert data["opening_time"] == "08:00:00"
        assert data["closing_time"] == "18:00:00"

    def test_update_clinic_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            "/api/v1/clinics/99999",
            json={
                "name": "Updated",
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )

    def test_update_clinic_invalid_timezone(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "timezone": "Invalid/Timezone",
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid timezone 'Invalid/Timezone'"
        )

    def test_update_clinic_invalid_operating_hours(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "opening_time": "18:00:00",
                "closing_time": "08:00:00",
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert "details" in body
        assert body["details"]

    def test_update_clinic_duplicate_name_returns_409(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        make_clinic(
            name="Existing Clinic",
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "name": "Existing Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 409

        assert "already exists" in response.get_json()["error"]

    def test_update_clinic_validation_error(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "timezone": 12345,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert response.get_json()["error"] == "Validation error"

    def test_update_clinic_rejects_forbidden_extra_field(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "name": "Updated Clinic",
                "clinic_id": 999,
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]


# ============================================================================
# STATUS
# ============================================================================


class TestUpdateClinicStatusRoute:
    def test_update_status_success(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert response.status_code == 200

        body = response.get_json()

        assert (
            body["message"]
            == "Clinic status updated successfully"
        )

        assert (
            body["data"]["status"]
            == ClinicStatus.SUSPENDED.value
        )

    def test_update_status_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            "/api/v1/clinics/99999/status",
            json={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )

    def test_update_status_validation_error(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]


# ============================================================================
# AI CREDITS
# ============================================================================


class TestUpdateClinicAICreditsRoute:
    def test_add_ai_credits_success(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": 10,
            },
            headers=headers,
        )

        assert response.status_code == 200

        body = response.get_json()

        assert (
            body["message"]
            == "Clinic AI credits updated successfully"
        )

        assert body["data"]["ai_credits"] == 15

    def test_add_ai_credits_rejects_zero(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": 0,
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_add_ai_credits_rejects_negative_amount(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": -5,
            },
            headers=headers,
        )

        assert response.status_code == 422

    def test_add_ai_credits_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            "/api/v1/clinics/99999/ai-credits",
            json={
                "amount": 10,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )

    def test_add_ai_credits_validation_error(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]


# ============================================================================
# API TOKEN
# ============================================================================


class TestRegenerateClinicAPITokenRoute:
    def test_regenerate_api_token_success(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/api-token/regenerate",
            headers=headers,
        )

        assert response.status_code == 200

        body = response.get_json()

        assert (
            body["message"]
            == "Clinic API token regenerated successfully"
        )

        token = body["data"]["api_token"]

        assert token
        assert isinstance(token, str)
        assert len(token) > 20

    def test_regenerate_api_token_returns_new_token_each_time(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        first_response = client.post(
            f"/api/v1/clinics/{clinic.id}/api-token/regenerate",
            headers=headers,
        )

        second_response = client.post(
            f"/api/v1/clinics/{clinic.id}/api-token/regenerate",
            headers=headers,
        )

        assert first_response.status_code == 200
        assert second_response.status_code == 200

        first_token = (
            first_response
            .get_json()["data"]["api_token"]
        )

        second_token = (
            second_response
            .get_json()["data"]["api_token"]
        )

        assert first_token
        assert second_token
        assert first_token != second_token

    def test_regenerate_api_token_missing_clinic_returns_404(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics/99999/api-token/regenerate",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 99999 not found"
        )


# ============================================================================
# SERVICE EXCEPTION MAPPING
# ============================================================================


class TestClinicRouteExceptionMapping:
    def test_create_clinic_maps_not_found_to_404(
        self,
        client,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_create_clinic(**kwargs):
            raise NotFoundError("Parent clinic not found")

        monkeypatch.setattr(
            clinic_route,
            "create_clinic",
            fake_create_clinic,
        )

        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Test Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 404
        assert (
            response.get_json()["error"]
            == "Parent clinic not found"
        )

    def test_create_clinic_maps_validation_to_422(
        self,
        client,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_create_clinic(**kwargs):
            raise ValidationError("Invalid clinic")

        monkeypatch.setattr(
            clinic_route,
            "create_clinic",
            fake_create_clinic,
        )

        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Test Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 422
        assert response.get_json()["error"] == "Invalid clinic"

    def test_create_clinic_maps_conflict_to_409(
        self,
        client,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_create_clinic(**kwargs):
            raise ConflictError("Clinic already exists")

        monkeypatch.setattr(
            clinic_route,
            "create_clinic",
            fake_create_clinic,
        )

        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            json={
                "name": "Test Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 409

        assert (
            response.get_json()["error"]
            == "Clinic already exists"
        )

    def test_list_clinics_maps_not_found_to_404(
        self,
        client,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_list_clinics(**kwargs):
            raise NotFoundError("Clinic lookup failed")

        monkeypatch.setattr(
            clinic_route,
            "list_authorized_clinics_for_user",
            fake_list_clinics,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            "/api/v1/clinics",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic lookup failed"
        )

    def test_get_clinic_maps_not_found_to_404(
        self,
        client,
        user,
        clinic,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_get_clinic(*args, **kwargs):
            raise NotFoundError("Clinic 123 not found")

        monkeypatch.setattr(
            clinic_route,
            "get_clinic",
            fake_get_clinic,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{clinic.id}",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 123 not found"
        )

    def test_list_branches_maps_not_found_to_404(
        self,
        client,
        user,
        clinic,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_list_branches(**kwargs):
            raise NotFoundError("Clinic 123 not found")

        monkeypatch.setattr(
            clinic_route,
            "list_branches",
            fake_list_branches,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.get(
            f"/api/v1/clinics/{clinic.id}/branches",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 123 not found"
        )

    def test_create_branch_maps_validation_to_422(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_create_branch(**kwargs):
            raise ValidationError("Invalid branch")

        monkeypatch.setattr(
            clinic_route,
            "create_branch",
            fake_create_branch,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={
                "name": "Test Branch",
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid branch"
        )

    def test_update_branch_configuration_maps_validation_to_422(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_update_branch_configuration(**kwargs):
            raise ValidationError("Invalid configuration")

        monkeypatch.setattr(
            clinic_route,
            "update_branch_configuration",
            fake_update_branch_configuration,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/branch-configuration",
            json={
                "is_headquarters": True,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid configuration"
        )

    def test_update_clinic_maps_validation_to_422(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_update_clinic(**kwargs):
            raise ValidationError("Invalid clinic update")

        monkeypatch.setattr(
            clinic_route,
            "update_clinic",
            fake_update_clinic,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}",
            json={
                "name": "Updated Clinic",
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid clinic update"
        )

    def test_update_status_maps_not_found_to_404(
        self,
        client,
        user,
        clinic,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_change_status(**kwargs):
            raise NotFoundError("Clinic 123 not found")

        monkeypatch.setattr(
            clinic_route,
            "change_status",
            fake_change_status,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={
                "status": ClinicStatus.ACTIVE.value,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 123 not found"
        )

    def test_update_status_maps_validation_to_422(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_change_status(**kwargs):
            raise ValidationError("Invalid status transition")

        monkeypatch.setattr(
            clinic_route,
            "change_status",
            fake_change_status,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={
                "status": ClinicStatus.SUSPENDED.value,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid status transition"
        )

    def test_ai_credits_maps_not_found_to_404(
        self,
        client,
        user,
        clinic,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_add_ai_credits(**kwargs):
            raise NotFoundError("Clinic 123 not found")

        monkeypatch.setattr(
            clinic_route,
            "add_ai_credits",
            fake_add_ai_credits,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": 10,
            },
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 123 not found"
        )

    def test_ai_credits_maps_validation_to_422(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_add_ai_credits(**kwargs):
            raise ValidationError("Invalid AI credit amount")

        monkeypatch.setattr(
            clinic_route,
            "add_ai_credits",
            fake_add_ai_credits,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": 10,
            },
            headers=headers,
        )

        assert response.status_code == 422

        assert (
            response.get_json()["error"]
            == "Invalid AI credit amount"
        )

    def test_api_token_maps_not_found_to_404(
        self,
        client,
        user,
        clinic,
        auth_headers_for,
        monkeypatch,
    ):
        def fake_regenerate_api_token(**kwargs):
            raise NotFoundError("Clinic 123 not found")

        monkeypatch.setattr(
            clinic_route,
            "regenerate_api_token",
            fake_regenerate_api_token,
        )

        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/api-token/regenerate",
            headers=headers,
        )

        assert response.status_code == 404

        assert (
            response.get_json()["error"]
            == "Clinic 123 not found"
        )


# ============================================================================
# REQUEST VALIDATION / JSON HANDLING
# ============================================================================


class TestClinicRouteValidation:
    def test_create_clinic_accepts_empty_json_as_validation_error(
        self,
        client,
        user,
        auth_headers_for,
    ):
        headers = super_admin_headers(auth_headers_for, user)

        response = client.post(
            "/api/v1/clinics",
            data="",
            headers={
                **headers,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_create_branch_accepts_empty_json_as_validation_error(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.post(
            f"/api/v1/clinics/{clinic.id}/branches",
            json={},
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_status_rejects_invalid_enum_value(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/status",
            json={
                "status": "invalid-status",
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]

    def test_ai_credits_rejects_invalid_payload(
        self,
        client,
        clinic,
        user,
        auth_headers_for,
    ):
        headers = admin_headers(auth_headers_for, user)

        response = client.patch(
            f"/api/v1/clinics/{clinic.id}/ai-credits",
            json={
                "amount": "ten",
            },
            headers=headers,
        )

        assert response.status_code == 422

        body = response.get_json()

        assert body["error"] == "Validation error"
        assert body["details"]


# ============================================================================
# TENANT ISOLATION
# ============================================================================


class TestClinicTenantIsolation:
    def test_non_admin_cannot_read_other_clinic(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        other = make_clinic(
            name="Other Clinic",
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{other.id}",
            headers=headers,
        )

        assert response.status_code == 403

    def test_non_admin_cannot_read_other_clinic_branches(
        self,
        client,
        clinic,
        make_clinic,
        make_user,
        auth_headers_for,
    ):
        other = make_clinic(
            name="Other Clinic",
        )

        make_clinic(
            name="Other Branch",
            parent_clinic_id=other.id,
        )

        doctor = make_user(
            clinic,
            role=Role.DOCTOR,
        )

        headers = user_headers(
            auth_headers_for,
            doctor,
        )

        response = client.get(
            f"/api/v1/clinics/{other.id}/branches",
            headers=headers,
        )

        assert response.status_code == 403

    def test_admin_cannot_cross_tenant_read(
        self,
        client,
        clinic,
        make_clinic,
        user,
        auth_headers_for,
    ):
        owner = HospitalOrganization(name="Foreign tenant")
        db.session.add(owner); db.session.flush()
        other = make_clinic(
            organization_id=owner.id,
            name="Other Clinic",
        )

        headers = admin_headers(
            auth_headers_for,
            user,
        )

        response = client.get(
            f"/api/v1/clinics/{other.id}",
            headers=headers,
        )

        assert response.status_code == 403
        assert "data" not in response.get_json()


def test_reparent_requires_explicit_target_and_parent_grants(client, clinic, make_clinic, user, auth_headers_for):
    branch = make_clinic(name="Granted reparent target", parent_clinic_id=clinic.id)
    parent = make_clinic(name="Ungranted parent")
    _grant(user, branch)
    headers = admin_headers(auth_headers_for, user)
    response = client.patch(f"/api/v1/clinics/{branch.id}/branch-configuration",
                            headers=headers, json={"parent_clinic_id": parent.id})
    assert response.status_code == 403
    db.session.refresh(branch)
    assert branch.parent_clinic_id == clinic.id
    _grant(user, parent)
    response = client.patch(f"/api/v1/clinics/{branch.id}/branch-configuration",
                            headers=headers, json={"parent_clinic_id": parent.id})
    assert response.status_code == 200
    assert response.get_json()["data"]["parent_clinic_id"] == parent.id


def test_suspended_clinic_management_does_not_authorize_clinical_session(client, clinic, user, auth_headers_for):
    from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access, TenantAccessDenied
    headers = admin_headers(auth_headers_for, user)
    path = f"/api/v1/clinics/{clinic.id}/status"
    assert client.patch(path, headers=headers, json={"status": "suspended"}).status_code == 200
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=user.id, organization_id=clinic.organization_id, clinic_id=clinic.id)
    assert client.get(f"/api/v1/clinics/{clinic.id}", headers=headers).status_code == 401
    tenant = issue_auth_tokens(user, tenant_id=clinic.organization_id, auto_assign_clinic=False)
    response = client.patch(path, headers={"Authorization": "Bearer " + tenant["access_token"]}, json={"status": "active"})
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "active"
