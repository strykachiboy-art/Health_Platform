from __future__ import annotations

from app.core.enums.hie_enums import (
    HIEFailureClass,
    HIEIntegrationStatus,
    HIESubmissionStatus,
    HIEPurposeOfUse,
)
from app.core.enums.role_enums import Role
from app.modules.hie.models.hie_model import (
    HIEIntegration,
    HIESubmission,
)
from app.modules.hie.providers import registry
from app.modules.hie.providers.exceptions import (
    HIEProviderHTTPError,
    HIERetryableError,
)


class FailureMatrixHIEProvider:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    def _record(self, operation, payload):
        self.calls.append(
            {
                "operation": operation,
                "payload": payload,
            }
        )

    def _response_or_failure(self):
        if self.mode == "timeout":
            raise TimeoutError(
                "deterministic timeout"
            )

        if self.mode == "connection":
            raise ConnectionError(
                "deterministic connection failure"
            )

        if self.mode == "http_503":
            raise HIEProviderHTTPError(
                503,
                "deterministic HTTP 503",
            )

        if self.mode == "http_422":
            raise HIEProviderHTTPError(
                422,
                "deterministic HTTP 422",
            )

        if self.mode == "malformed":
            return []

        if self.mode == "empty":
            return {}

        if self.mode == "partial":
            return {
                "status_code": 200,
            }

        if self.mode == "retry_then_success":
            if len(self.calls) == 1:
                raise HIERetryableError(
                    "deterministic intermittent failure"
                )

        return {
            "status_code": 200,
            "external_reference": (
                f"E2E-HIE-RECOVERY-{len(self.calls):03d}"
            ),
            "patient": {
                "identifier": "REMOTE-MRN-SLICE3",
                "name": "Slice 3 Test Patient",
            },
        }

    def query_patient(
        self,
        patient_identifier,
    ):
        self._record(
            "query_patient",
            patient_identifier,
        )
        return self._response_or_failure()


def _headers(login):
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


def _submissions_for(
    db,
    integration_id,
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


def _create_active_integration(
    client,
    admin_headers,
    provider_name,
):
    create_response = client.post(
        "/api/v1/hie/integrations",
        json={
            "provider": provider_name,
            "endpoint_url": (
                "https://fhir.partnerhealth.org/fhir/r4"
            ),
            "organization_id": "SLICE3-ORG",
            "facility_id": "SLICE3-FACILITY",
        },
        headers=admin_headers,
    )

    assert create_response.status_code == 201, (
        create_response.get_json()
    )

    integration_id = (
        create_response.get_json()["data"]["id"]
    )

    activate_response = client.patch(
        f"/api/v1/hie/integrations/{integration_id}",
        json={
            "status": HIEIntegrationStatus.ACTIVE.value,
        },
        headers=admin_headers,
    )

    assert activate_response.status_code == 200, (
        activate_response.get_json()
    )

    return integration_id


def _make_staff_logins(
    make_staff,
    e2e_login,
    clinic,
):
    admin_staff = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "slice3-hie-admin@test.com",
        },
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "slice3-hie-doctor@test.com",
        },
    )

    admin_login = e2e_login(
        "slice3-hie-admin@test.com",
    )

    doctor_login = e2e_login(
        "slice3-hie-doctor@test.com",
    )

    assert admin_login["user_id"] == (
        admin_staff.user.id
    )
    assert doctor_login["user_id"] == (
        doctor_staff.user.id
    )

    return (
        _headers(admin_login),
        _headers(doctor_login),
    )


def test_hie_failure_matrix_persists_failure_semantics(
    client,
    db,
    clinic,
    make_staff,
    e2e_login,
):
    admin_headers, doctor_headers = _make_staff_logins(
        make_staff,
        e2e_login,
        clinic,
    )

    modes = [
        (
            "timeout",
            HIEFailureClass.RETRYABLE,
        ),
        (
            "connection",
            HIEFailureClass.RETRYABLE,
        ),
        (
            "http_503",
            HIEFailureClass.RETRYABLE,
        ),
        (
            "http_422",
            HIEFailureClass.USER_ACTION_REQUIRED,
        ),
        (
            "malformed",
            HIEFailureClass.FAIL_CLOSED,
        ),
        (
            "empty",
            HIEFailureClass.FAIL_CLOSED,
        ),
        (
            "partial",
            HIEFailureClass.FAIL_CLOSED,
        ),
    ]

    for index, (
        mode,
        expected_failure_class,
    ) in enumerate(
        modes,
        start=1,
    ):
        provider_name = (
            f"phase9-slice3-hie-{mode}"
        )
        provider = FailureMatrixHIEProvider(mode)

        registry.unregister_provider(
            provider_name
        )
        registry.register_provider(
            provider_name,
            lambda endpoint, provider=provider: provider,
        )

        try:
            integration_id = _create_active_integration(
                client,
                admin_headers,
                provider_name,
            )

            response = client.post(
                "/api/v1/hie/queries/patient",
                json={
                    "patient_identifier": (
                        f"REMOTE-MRN-SLICE3-{index:03d}"
                    ),
                    "purpose_of_use": (
                        HIEPurposeOfUse.TREATMENT.value
                    ),
                    "integration_id": integration_id,
                },
                headers=doctor_headers,
            )

            assert response.status_code == 500, (
                mode,
                response.get_json(),
            )

            submissions = _submissions_for(
                db,
                integration_id,
            )

            assert len(submissions) == 1
            submission = submissions[0]

            assert submission.status is (
                HIESubmissionStatus.FAILED
            )
            assert submission.failure_class is (
                expected_failure_class
            )
            assert submission.retry_count == 1
            assert submission.external_reference is None
            assert submission.response_data is None
            assert submission.error_message == (
                "HIE provider operation failed"
            )
            assert submission.submitted_at is not None

            assert len(provider.calls) == 1
        finally:
            registry.unregister_provider(
                provider_name
            )


def test_hie_failure_recovery_creates_one_submission_per_attempt(
    client,
    db,
    clinic,
    make_staff,
    e2e_login,
):
    admin_headers, doctor_headers = _make_staff_logins(
        make_staff,
        e2e_login,
        clinic,
    )

    provider_name = "phase9-slice3-hie-recovery"
    provider = FailureMatrixHIEProvider(
        "retry_then_success"
    )

    registry.unregister_provider(
        provider_name
    )
    registry.register_provider(
        provider_name,
        lambda endpoint: provider,
    )

    try:
        integration_id = _create_active_integration(
            client,
            admin_headers,
            provider_name,
        )

        first_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": (
                    "REMOTE-MRN-SLICE3-RECOVERY"
                ),
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=doctor_headers,
        )

        assert first_response.status_code == 500

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 1
        failed_submission = submissions[0]

        assert failed_submission.status is (
            HIESubmissionStatus.FAILED
        )
        assert failed_submission.failure_class is (
            HIEFailureClass.RETRYABLE
        )
        assert failed_submission.retry_count == 1
        assert failed_submission.external_reference is None

        second_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": (
                    "REMOTE-MRN-SLICE3-RECOVERY"
                ),
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=doctor_headers,
        )

        assert second_response.status_code == 200, (
            second_response.get_json()
        )

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 2

        recovered_submission = submissions[1]

        assert recovered_submission.status is (
            HIESubmissionStatus.SUCCESS
        )
        assert recovered_submission.failure_class is None
        assert recovered_submission.retry_count == 0
        assert recovered_submission.status_code == 200
        assert recovered_submission.external_reference == (
            "E2E-HIE-RECOVERY-002"
        )
        assert recovered_submission.response_data[
            "external_reference"
        ] == "E2E-HIE-RECOVERY-002"
        assert recovered_submission.error_message is None

        assert len(provider.calls) == 2
    finally:
        registry.unregister_provider(
            provider_name
        )
