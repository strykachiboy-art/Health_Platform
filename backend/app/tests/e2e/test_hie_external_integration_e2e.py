from __future__ import annotations

from datetime import datetime

import pytest

from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEOperation,
    HIESubmissionStatus,
    HIEPurposeOfUse,
)
from app.core.enums.role_enums import Role
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)
from app.modules.hie.providers import registry


class DeterministicHIEProvider:
    def __init__(self):
        self.calls = []
        self.fail_next = False

    def _record(self, operation, payload):
        self.calls.append(
            {
                "operation": operation,
                "payload": payload,
            }
        )

        if self.fail_next:
            self.fail_next = False
            raise RuntimeError(
                "deterministic HIE provider failure"
            )

    def submit_patient(self, payload):
        self._record(
            "submit_patient",
            payload,
        )
        return {
            "status_code": 201,
            "external_reference": "E2E-PATIENT-001",
        }

    def submit_clinical_data(self, payload):
        self._record(
            "submit_clinical_data",
            payload,
        )
        return {
            "status_code": 200,
            "external_reference": "E2E-CLINICAL-001",
        }

    def submit_clinical_document(self, payload):
        self._record(
            "submit_clinical_document",
            payload,
        )
        return {
            "status_code": 201,
            "external_reference": "E2E-DOCUMENT-001",
        }

    def query_patient(self, patient_identifier):
        self._record(
            "query_patient",
            patient_identifier,
        )
        return {
            "status_code": 200,
            "external_reference": "E2E-QUERY-001",
            "patient": {
                "identifier": patient_identifier,
                "name": "Remote Test Patient",
            },
        }

    def query_clinical_data(
        self,
        patient_identifier,
        filters=None,
    ):
        self._record(
            "query_clinical_data",
            {
                "patient_identifier": patient_identifier,
                "filters": filters,
            },
        )
        return {
            "status_code": 200,
            "external_reference": "E2E-CLINICAL-QUERY-001",
            "records": [
                {
                    "resource_type": "Observation",
                    "code": "E2E-OBS-001",
                    "status": "final",
                }
            ],
        }


def _headers(login: dict) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


def _submissions_for(
    db,
    integration_id: int,
):
    return (
        db.session.execute(
            db.select(HIESubmission)
            .where(
                HIESubmission.integration_id
                == integration_id,
            )
            .order_by(
                HIESubmission.id.asc(),
            )
        )
        .scalars()
        .all()
    )


def test_hie_external_integration_e2e(
    client,
    db,
    clinic,
    make_clinic,
    make_staff,
    e2e_login,
):
    admin_staff = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "e2e-g15-hie-admin@test.com",
        },
    )

    query_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-g15-hie-doctor@test.com",
        },
    )

    admin = admin_staff.user
    query_user = query_staff.user

    admin_login = e2e_login(
        "e2e-g15-hie-admin@test.com",
    )

    query_login = e2e_login(
        "e2e-g15-hie-doctor@test.com",
    )

    assert admin_login["user_id"] == admin.id
    assert admin_login["role"] == Role.ADMIN.value

    assert query_login["user_id"] == query_user.id
    assert query_login["role"] == Role.DOCTOR.value

    admin_headers = _headers(
        admin_login
    )
    query_headers = _headers(
        query_login
    )

    provider_name = "phase8-g15-hie"
    provider = DeterministicHIEProvider()

    registry.unregister_provider(
        provider_name
    )

    registry.register_provider(
        provider_name,
        lambda endpoint: provider,
    )

    try:
        # ============================================================
        # CREATE HIE INTEGRATION THROUGH REAL HTTP ROUTE
        # ============================================================

        create_response = client.post(
            "/api/v1/hie/integrations",
            json={
                "provider": provider_name,
                "endpoint_url": (
                    "https://fhir.partnerhealth.org/fhir/r4"
                ),
                "organization_id": "E2E-ORG-15",
                "facility_id": "E2E-FACILITY-15",
            },
            headers=admin_headers,
        )

        assert create_response.status_code == 201, (
            create_response.get_json()
        )

        create_body = create_response.get_json()

        assert create_body["success"] is True
        assert create_body["data"]["clinic_id"] == clinic.id
        assert create_body["data"]["provider"] == provider_name
        assert create_body["data"]["status"] == (
            HIEIntegrationStatus.PENDING.value
        )

        integration_id = create_body["data"]["id"]

        integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        assert integration is not None
        assert integration.clinic_id == clinic.id
        assert integration.status is (
            HIEIntegrationStatus.PENDING
        )

        # ============================================================
        # ACTIVATE INTEGRATION THROUGH REAL HTTP ROUTE
        # ============================================================

        activate_response = client.patch(
            f"/api/v1/hie/integrations/{integration_id}",
            json={
                "status": (
                    HIEIntegrationStatus.ACTIVE.value
                ),
            },
            headers=admin_headers,
        )

        assert activate_response.status_code == 200, (
            activate_response.get_json()
        )

        activate_body = (
            activate_response.get_json()
        )

        assert activate_body["success"] is True
        assert activate_body["data"]["status"] == (
            HIEIntegrationStatus.ACTIVE.value
        )

        integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        assert integration.status is (
            HIEIntegrationStatus.ACTIVE
        )

        # ============================================================
        # PATIENT QUERY:
        # HTTP -> SERVICE -> PROVIDER -> SUBMISSION
        # ============================================================

        patient_query_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": "REMOTE-MRN-15001",
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=query_headers,
        )

        assert patient_query_response.status_code == 200, (
            patient_query_response.get_json()
        )

        patient_query_body = (
            patient_query_response.get_json()
        )

        assert patient_query_body["success"] is True
        assert patient_query_body["data"]["status_code"] == 200
        assert patient_query_body["data"][
            "external_reference"
        ] == "E2E-QUERY-001"

        assert len(provider.calls) == 1
        assert provider.calls[0]["operation"] == (
            "query_patient"
        )
        assert provider.calls[0]["payload"] == (
            "REMOTE-MRN-15001"
        )

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 1

        patient_submission = submissions[0]

        assert patient_submission.clinic_id == clinic.id
        assert patient_submission.integration_id == (
            integration_id
        )
        assert patient_submission.patient_id is None
        assert patient_submission.operation is (
            HIEOperation.PATIENT_QUERY
        )
        assert patient_submission.status is (
            HIESubmissionStatus.SUCCESS
        )
        assert patient_submission.status_code == 200
        assert patient_submission.external_reference == (
            "E2E-QUERY-001"
        )
        assert patient_submission.request_data == {
            "patient_identifier": "REMOTE-MRN-15001",
            "purpose_of_use": "treatment",
            "requesting_user_id": query_user.id,
        }
        assert patient_submission.response_data[
            "external_reference"
        ] == "E2E-QUERY-001"
        assert patient_submission.submitted_at is not None
        assert patient_submission.retry_count == 0

        integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        assert integration.last_sync_at is not None
        assert isinstance(
            integration.last_sync_at,
            datetime,
        )

        # ============================================================
        # CLINICAL DATA QUERY:
        # REAL ROUTE + FILTERS + SUBMISSION PERSISTENCE
        # ============================================================

        filters = {
            "resource_type": "Observation",
            "status": "final",
        }

        clinical_response = client.post(
            "/api/v1/hie/queries/clinical-data",
            json={
                "patient_identifier": "REMOTE-MRN-15001",
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "filters": filters,
                "integration_id": integration_id,
            },
            headers=query_headers,
        )

        assert clinical_response.status_code == 200, (
            clinical_response.get_json()
        )

        clinical_body = clinical_response.get_json()

        assert clinical_body["success"] is True
        assert clinical_body["data"]["status_code"] == 200
        assert clinical_body["data"][
            "external_reference"
        ] == "E2E-CLINICAL-QUERY-001"

        assert len(provider.calls) == 2
        assert provider.calls[1]["operation"] == (
            "query_clinical_data"
        )
        assert provider.calls[1]["payload"] == {
            "patient_identifier": (
                "REMOTE-MRN-15001"
            ),
            "filters": filters,
        }

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 2

        clinical_submission = submissions[1]

        assert clinical_submission.clinic_id == clinic.id
        assert clinical_submission.operation is (
            HIEOperation.CLINICAL_DATA_QUERY
        )
        assert clinical_submission.status is (
            HIESubmissionStatus.SUCCESS
        )
        assert clinical_submission.status_code == 200
        assert clinical_submission.request_data == {
            "patient_identifier": (
                "REMOTE-MRN-15001"
            ),
            "filters": filters,
            "purpose_of_use": "treatment",
            "requesting_user_id": query_user.id,
        }
        assert clinical_submission.response_data[
            "external_reference"
        ] == "E2E-CLINICAL-QUERY-001"

        # ============================================================
        # SUBMISSION LIST:
        # VERIFY PERSISTED EXTERNAL ACTIVITY THROUGH HTTP
        # ============================================================

        submissions_response = client.get(
            "/api/v1/hie/submissions"
            f"?integration_id={integration_id}",
            headers=query_headers,
        )

        assert submissions_response.status_code == 200

        submissions_body = (
            submissions_response.get_json()
        )

        assert submissions_body["success"] is True
        assert submissions_body["data"]["total"] == 2
        assert len(
            submissions_body["data"]["items"]
        ) == 2

        returned_operations = {
            item["operation"]
            for item in submissions_body[
                "data"
            ]["items"]
        }

        assert returned_operations == {
            HIEOperation.PATIENT_QUERY.value,
            HIEOperation.CLINICAL_DATA_QUERY.value,
        }

        # ============================================================
        # FAILURE:
        # PROVIDER ERROR -> FAILED SUBMISSION
        # ============================================================

        provider.fail_next = True

        failed_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": "REMOTE-MRN-FAIL-15001",
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=query_headers,
        )

        assert failed_response.status_code == 500

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 3

        failed_submission = submissions[2]

        assert failed_submission.clinic_id == clinic.id
        assert failed_submission.operation is (
            HIEOperation.PATIENT_QUERY
        )
        assert failed_submission.status is (
            HIESubmissionStatus.FAILED
        )
        assert failed_submission.error_message == (
            "HIE provider operation failed"
        )
        assert failed_submission.retry_count == 1
        assert failed_submission.submitted_at is not None
        assert failed_submission.request_data[
            "patient_identifier"
        ] == "REMOTE-MRN-FAIL-15001"

        # ============================================================
        # CROSS-CLINIC ISOLATION
        # ============================================================

        foreign_clinic = make_clinic(
            name="Gate 15 Foreign HIE Clinic",
        )

        foreign_staff = make_staff(
            clinic=foreign_clinic,
            role=Role.DOCTOR,
            user_overrides={
                "email": (
                    "e2e-g15-hie-foreign@test.com"
                ),
            },
        )

        foreign_login = e2e_login(
            "e2e-g15-hie-foreign@test.com",
        )

        assert foreign_login["user_id"] == (
            foreign_staff.user.id
        )

        foreign_headers = _headers(
            foreign_login
        )

        foreign_get_response = client.get(
            f"/api/v1/hie/integrations/{integration_id}",
            headers=foreign_headers,
        )

        assert foreign_get_response.status_code == 422

        before_foreign_query = len(
            _submissions_for(
                db,
                integration_id,
            )
        )

        foreign_query_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": (
                    "REMOTE-MRN-FOREIGN-15001"
                ),
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=foreign_headers,
        )

        assert foreign_query_response.status_code == 422

        after_foreign_query = len(
            _submissions_for(
                db,
                integration_id,
            )
        )

        assert after_foreign_query == before_foreign_query

        assert all(
            submission.clinic_id == clinic.id
            for submission in _submissions_for(
                db,
                integration_id,
            )
        )

    finally:
        registry.unregister_provider(
            provider_name
        )
