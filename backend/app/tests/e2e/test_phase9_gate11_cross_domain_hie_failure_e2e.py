from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.hie_enums import (
    HIEFailureClass,
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
from app.modules.lab.models.lab_model import (
    LabOrder,
    LabOrderItem,
    LabTest,
)


def _auth(login: dict) -> dict[str, str]:
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


class Gate11HIETimeoutProvider:
    def __init__(self):
        self.calls = []

    def query_patient(
        self,
        patient_identifier,
    ):
        self.calls.append(
            {
                "operation": "query_patient",
                "patient_identifier": patient_identifier,
            }
        )

        raise TimeoutError(
            "Gate 11 simulated HIE timeout"
        )


def test_gate11_cross_domain_hie_failure_preserves_clinical_state(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate11-slice3-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate11-slice3-doctor@test.com",
        },
    )

    lab_staff = make_staff(
        clinic=clinic,
        role=Role.LAB_TECHNICIAN,
        user_overrides={
            "email": "gate11-slice3-lab@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="gate11-slice3-receptionist@test.com",
    )

    admin_login = e2e_login(
        "gate11-slice3-admin@test.com",
    )

    doctor_login = e2e_login(
        "gate11-slice3-doctor@test.com",
    )

    lab_login = e2e_login(
        "gate11-slice3-lab@test.com",
    )

    receptionist_login = e2e_login(
        "gate11-slice3-receptionist@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert (
        lab_login["role"]
        == Role.LAB_TECHNICIAN.value
    )
    assert (
        receptionist_login["role"]
        == Role.RECEPTIONIST.value
    )

    # ============================================================
    # PATIENT
    # ============================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate11",
            "last_name": "Slice3 HIE Patient",
        },
        headers=_auth(receptionist_login),
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_id = patient_response.get_json()["data"]["id"]

    # ============================================================
    # APPOINTMENT
    # ============================================================

    scheduled_start = (
        datetime.now(timezone.utc)
        + timedelta(days=1)
    )

    scheduled_end = (
        scheduled_start
        + timedelta(minutes=30)
    )

    appointment_response = client.post(
        "/api/v1/appointments/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "scheduled_start": scheduled_start.isoformat(),
            "scheduled_end": scheduled_end.isoformat(),
            "appointment_type": "in_person",
            "reason": (
                "Gate 11 Slice 3 HIE failure"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert appointment_response.status_code == 201, (
        appointment_response.get_json()
    )

    appointment_id = (
        appointment_response.get_json()["data"]["id"]
    )

    # ============================================================
    # CONSULTATION
    # ============================================================

    consultation_response = client.post(
        "/api/v1/consultations/",
        json={
            "patient_id": patient_id,
            "staff_id": doctor_staff.id,
            "appointment_id": appointment_id,
            "consultation_type": "general",
            "chief_complaint": (
                "Gate 11 Slice 3 HIE workflow"
            ),
            "symptoms": (
                "Cross-domain HIE failure test"
            ),
        },
        headers=_auth(doctor_login),
    )

    assert consultation_response.status_code == 201, (
        consultation_response.get_json()
    )

    consultation_id = (
        consultation_response.get_json()["data"]["id"]
    )

    # ============================================================
    # LAB TEST
    # ============================================================

    lab_test_response = client.post(
        "/api/v1/lab/tests",
        json={
            "name": "Gate11 Slice3 CBC",
            "loinc_code": "57021-8",
            "code": "G11-S3-CBC",
            "sample_type": "blood",
            "reference_range": "4.0-11.0",
            "unit": "10^9/L",
            "price": "25.00",
            "critical_low": "2.0",
            "critical_high": "20.0",
            "is_active": True,
        },
        headers=_auth(admin_login),
    )

    assert lab_test_response.status_code == 201, (
        lab_test_response.get_json()
    )

    lab_test_id = (
        lab_test_response.get_json()["data"]["id"]
    )

    persisted_lab_test = db.session.get(
        LabTest,
        lab_test_id,
    )

    assert persisted_lab_test is not None
    assert persisted_lab_test.clinic_id == clinic.id
    assert persisted_lab_test.is_active is True

    # ============================================================
    # LAB ORDER
    # ============================================================

    lab_order_response = client.post(
        "/api/v1/lab/orders",
        json={
            "patient_id": patient_id,
            "test_ids": [lab_test_id],
            "consultation_id": consultation_id,
        },
        headers=_auth(doctor_login),
    )

    assert lab_order_response.status_code == 201, (
        lab_order_response.get_json()
    )

    lab_order_body = lab_order_response.get_json()

    assert lab_order_body["success"] is True
    assert lab_order_body["data"]["clinic_id"] == clinic.id
    assert (
        lab_order_body["data"]["patient_id"]
        == patient_id
    )
    assert (
        lab_order_body["data"]["consultation_id"]
        == consultation_id
    )
    assert (
        lab_order_body["data"]["ordered_by_id"]
        == doctor_staff.id
    )

    lab_order_id = lab_order_body["data"]["id"]
    order_item_id = (
        lab_order_body["data"]["items"][0]["id"]
    )
    qr_code = lab_order_body["data"]["qr_code"]

    # ============================================================
    # LAB PROCESSING
    # ============================================================

    collect_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/collect-sample",
        json={
            "scanned_qr_code": qr_code,
        },
        headers=_auth(lab_login),
    )

    assert collect_response.status_code == 200, (
        collect_response.get_json()
    )
    assert (
        collect_response.get_json()["data"]["status"]
        == "sample_collected"
    )

    process_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/process",
        json={},
        headers=_auth(lab_login),
    )

    assert process_response.status_code == 200, (
        process_response.get_json()
    )
    assert (
        process_response.get_json()["data"]["status"]
        == "in_progress"
    )

    result_response = client.post(
        f"/api/v1/lab/order-items/{order_item_id}/result",
        json={
            "result_value": "7.2",
            "flag": "normal",
            "result_notes": (
                "Gate 11 Slice 3 completed result"
            ),
        },
        headers=_auth(lab_login),
    )

    assert result_response.status_code == 200, (
        result_response.get_json()
    )

    assert (
        result_response.get_json()["data"]["result_value"]
        == "7.2"
    )

    verify_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/verify",
        json={},
        headers=_auth(lab_login),
    )

    assert verify_response.status_code == 200, (
        verify_response.get_json()
    )

    assert (
        verify_response.get_json()["data"]["verified_by_id"]
        == lab_staff.id
    )

    complete_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/complete",
        json={},
        headers=_auth(lab_login),
    )

    assert complete_response.status_code == 200, (
        complete_response.get_json()
    )

    complete_body = complete_response.get_json()

    assert complete_body["success"] is True
    assert (
        complete_body["data"]["status"]
        == "completed"
    )
    assert (
        complete_body["data"]["completed_at"]
        is not None
    )

    persisted_lab_order = db.session.get(
        LabOrder,
        lab_order_id,
    )

    persisted_lab_item = db.session.get(
        LabOrderItem,
        order_item_id,
    )

    assert persisted_lab_order is not None
    assert persisted_lab_order.clinic_id == clinic.id
    assert persisted_lab_order.patient_id == patient_id
    assert (
        persisted_lab_order.consultation_id
        == consultation_id
    )
    assert (
        persisted_lab_order.status.value
        == "completed"
    )
    assert (
        persisted_lab_order.collected_by_id
        == lab_staff.id
    )
    assert (
        persisted_lab_order.processed_by_id
        == lab_staff.id
    )
    assert (
        persisted_lab_order.verified_by_id
        == lab_staff.id
    )

    assert persisted_lab_item is not None
    assert persisted_lab_item.order_id == lab_order_id
    assert persisted_lab_item.result_value == "7.2"
    assert persisted_lab_item.resulted_at is not None

    audit_rows = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_id.in_(
                    [
                        lab_test_id,
                        lab_order_id,
                        order_item_id,
                    ]
                )
            )
            .order_by(
                AuditLog.id.asc(),
            )
        )
        .scalars()
        .all()
    )

    assert audit_rows
    assert all(
        row.clinic_id == clinic.id
        for row in audit_rows
    )

    assert any(
        row.entity_type == "LabTest"
        and row.entity_id == lab_test_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    assert any(
        row.entity_type == "LabOrder"
        and row.entity_id == lab_order_id
        and row.action is AuditAction.CREATE
        for row in audit_rows
    )

    # ============================================================
    # HIE INTEGRATION
    # ============================================================

    provider_name = "gate11-slice3-hie-timeout"
    provider = Gate11HIETimeoutProvider()

    registry.unregister_provider(
        provider_name
    )

    registry.register_provider(
        provider_name,
        lambda endpoint: provider,
    )

    try:
        create_integration_response = client.post(
            "/api/v1/hie/integrations",
            json={
                "provider": provider_name,
                "endpoint_url": (
                    "https://fhir.partnerhealth.org/fhir/r4"
                ),
                "organization_id": "G11-S3-ORG",
                "facility_id": "G11-S3-FACILITY",
            },
            headers=_auth(admin_login),
        )

        assert (
            create_integration_response.status_code
            == 201
        ), create_integration_response.get_json()

        integration_id = (
            create_integration_response
            .get_json()["data"]["id"]
        )

        activate_response = client.patch(
            f"/api/v1/hie/integrations/{integration_id}",
            json={
                "status": (
                    HIEIntegrationStatus.ACTIVE.value
                ),
            },
            headers=_auth(admin_login),
        )

        assert activate_response.status_code == 200, (
            activate_response.get_json()
        )

        integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        assert integration is not None
        assert integration.clinic_id == clinic.id
        assert integration.status is (
            HIEIntegrationStatus.ACTIVE
        )

        # ========================================================
        # HIE FAILURE INJECTION
        # ========================================================

        remote_identifier = (
            "REMOTE-MRN-GATE11-S3"
        )

        failed_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": remote_identifier,
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=_auth(doctor_login),
        )

        assert failed_response.status_code == 500, (
            failed_response.get_json()
        )

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 1

        failed_submission = submissions[0]

        assert failed_submission.clinic_id == clinic.id
        assert failed_submission.integration_id == (
            integration_id
        )
        assert failed_submission.operation is (
            HIEOperation.PATIENT_QUERY
        )
        assert failed_submission.status is (
            HIESubmissionStatus.FAILED
        )
        assert failed_submission.failure_class is (
            HIEFailureClass.RETRYABLE
        )
        assert failed_submission.retry_count == 1
        assert failed_submission.external_reference is None
        assert failed_submission.response_data is None
        assert failed_submission.error_message == (
            "HIE provider operation failed"
        )
        assert failed_submission.submitted_at is not None
        assert failed_submission.request_data[
            "patient_identifier"
        ] == remote_identifier
        assert failed_submission.request_data[
            "purpose_of_use"
        ] == HIEPurposeOfUse.TREATMENT.value
        assert failed_submission.request_data[
            "requesting_user_id"
        ] == doctor_staff.user.id

        assert provider.calls == [
            {
                "operation": "query_patient",
                "patient_identifier": remote_identifier,
            }
        ]

        # ========================================================
        # CLINICAL STATE MUST REMAIN INTACT
        # ========================================================

        db.session.expire_all()

        final_lab_order = db.session.get(
            LabOrder,
            lab_order_id,
        )

        final_lab_item = db.session.get(
            LabOrderItem,
            order_item_id,
        )

        assert final_lab_order is not None
        assert final_lab_order.clinic_id == clinic.id
        assert final_lab_order.patient_id == patient_id
        assert (
            final_lab_order.consultation_id
            == consultation_id
        )
        assert (
            final_lab_order.status.value
            == "completed"
        )

        assert final_lab_item is not None
        assert final_lab_item.order_id == lab_order_id
        assert final_lab_item.result_value == "7.2"

        persisted_integration = db.session.get(
            HIEIntegration,
            integration_id,
        )

        assert persisted_integration is not None
        assert persisted_integration.clinic_id == clinic.id
        assert persisted_integration.status is (
            HIEIntegrationStatus.ACTIVE
        )

        # Exactly one local HIE submission exists for the failed attempt.
        final_submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(final_submissions) == 1

        assert all(
            submission.clinic_id == clinic.id
            for submission in final_submissions
        )

    finally:
        registry.unregister_provider(
            provider_name
        )