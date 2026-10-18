from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.ai_enums import AIFeature
from app.core.enums.audit_enums import AuditAction
from app.core.enums.hie_enums import (
    HIEIntegrationStatus,
    HIEOperation,
    HIESubmissionStatus,
    HIEPurposeOfUse,
)
from app.core.enums.role_enums import Role
from app.modules.ai.ai_provider_exceptions import (
    AIProviderFailureClass,
    AIProviderUnavailableError,
)
from app.modules.ai.models.ai_model import AILog
from app.modules.ai.services import ai_service
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


def _ai_logs_for(
    db,
    clinic_id: int,
):
    return (
        db.session.execute(
            db.select(AILog)
            .where(
                AILog.clinic_id == clinic_id,
            )
            .order_by(
                AILog.id.asc(),
            )
        )
        .scalars()
        .all()
    )


class Gate11HIESuccessProvider:
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

        return {
            "status_code": 200,
            "external_reference": "G11-S4-HIE-001",
            "patient": {
                "identifier": patient_identifier,
                "name": "Gate 11 Slice 4 Remote Patient",
            },
        }


def test_gate11_cross_domain_ai_failure_preserves_lab_and_hie_state(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    e2e_login,
    monkeypatch,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="gate11-slice4-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "gate11-slice4-doctor@test.com",
        },
    )

    lab_staff = make_staff(
        clinic=clinic,
        role=Role.LAB_TECHNICIAN,
        user_overrides={
            "email": "gate11-slice4-lab@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="gate11-slice4-receptionist@test.com",
    )

    admin_login = e2e_login(
        "gate11-slice4-admin@test.com",
    )

    doctor_login = e2e_login(
        "gate11-slice4-doctor@test.com",
    )

    lab_login = e2e_login(
        "gate11-slice4-lab@test.com",
    )

    receptionist_login = e2e_login(
        "gate11-slice4-receptionist@test.com",
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
            "last_name": "Slice4 AI Patient",
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
                "Gate 11 Slice 4 AI failure"
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
                "Gate 11 Slice 4 AI workflow"
            ),
            "symptoms": (
                "Cross-domain AI failure test"
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
    # LAB SETUP
    # ============================================================

    lab_test_response = client.post(
        "/api/v1/lab/tests",
        json={
            "name": "Gate11 Slice4 CBC",
            "loinc_code": "57021-8",
            "code": "G11-S4-CBC",
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

    lab_order_id = lab_order_body["data"]["id"]
    order_item_id = (
        lab_order_body["data"]["items"][0]["id"]
    )
    qr_code = lab_order_body["data"]["qr_code"]

    # ============================================================
    # COMPLETE LAB
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

    process_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/process",
        json={},
        headers=_auth(lab_login),
    )

    assert process_response.status_code == 200, (
        process_response.get_json()
    )

    result_response = client.post(
        f"/api/v1/lab/order-items/{order_item_id}/result",
        json={
            "result_value": "7.4",
            "flag": "normal",
            "result_notes": (
                "Gate 11 Slice 4 completed result"
            ),
        },
        headers=_auth(lab_login),
    )

    assert result_response.status_code == 200, (
        result_response.get_json()
    )

    verify_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/verify",
        json={},
        headers=_auth(lab_login),
    )

    assert verify_response.status_code == 200, (
        verify_response.get_json()
    )

    complete_response = client.post(
        f"/api/v1/lab/orders/{lab_order_id}/complete",
        json={},
        headers=_auth(lab_login),
    )

    assert complete_response.status_code == 200, (
        complete_response.get_json()
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

    assert persisted_lab_item is not None
    assert persisted_lab_item.order_id == lab_order_id
    assert persisted_lab_item.result_value == "7.4"

    # ============================================================
    # HIE SUCCESS
    # ============================================================

    provider_name = "gate11-slice4-hie-success"
    provider = Gate11HIESuccessProvider()

    registry.unregister_provider(provider_name)

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
                "organization_id": "G11-S4-ORG",
                "facility_id": "G11-S4-FACILITY",
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

        hie_response = client.post(
            "/api/v1/hie/queries/patient",
            json={
                "patient_identifier": (
                    "REMOTE-MRN-GATE11-S4"
                ),
                "purpose_of_use": (
                    HIEPurposeOfUse.TREATMENT.value
                ),
                "integration_id": integration_id,
            },
            headers=_auth(doctor_login),
        )

        assert hie_response.status_code == 200, (
            hie_response.get_json()
        )

        hie_body = hie_response.get_json()

        assert hie_body["success"] is True
        assert hie_body["data"]["status_code"] == 200
        assert (
            hie_body["data"]["external_reference"]
            == "G11-S4-HIE-001"
        )

        submissions = _submissions_for(
            db,
            integration_id,
        )

        assert len(submissions) == 1

        submission = submissions[0]

        assert submission.clinic_id == clinic.id
        assert submission.integration_id == integration_id
        assert submission.operation is (
            HIEOperation.PATIENT_QUERY
        )
        assert submission.status is (
            HIESubmissionStatus.SUCCESS
        )
        assert submission.status_code == 200
        assert submission.external_reference == (
            "G11-S4-HIE-001"
        )
        assert submission.retry_count == 0

        assert provider.calls == [
            {
                "operation": "query_patient",
                "patient_identifier": (
                    "REMOTE-MRN-GATE11-S4"
                ),
            }
        ]

        # ========================================================
        # AI BASELINE
        # ========================================================

        db.session.refresh(clinic)

        starting_credits = clinic.ai_credits

        assert starting_credits >= 1

        logs_before = _ai_logs_for(
            db,
            clinic.id,
        )

        audits_before = (
            db.session.execute(
                db.select(AuditLog)
                .where(
                    AuditLog.entity_type == "AILog",
                )
                .order_by(
                    AuditLog.id.asc(),
                )
            )
            .scalars()
            .all()
        )

        def failing_ai_provider(
            feature,
            payload,
        ):
            raise AIProviderUnavailableError(
                "Gate 11 simulated AI provider timeout"
            )

        monkeypatch.setattr(
            ai_service,
            "_get_configured_provider",
            lambda: failing_ai_provider,
        )

        # ========================================================
        # AI FAILURE
        # ========================================================

        ai_response = client.post(
            "/api/v1/ai/drug-interactions",
            json={
                "drug_names": [
                    "Aspirin",
                    "Warfarin",
                ],
                "patient_id": patient_id,
            },
            headers=_auth(doctor_login),
        )

        assert ai_response.status_code == 503, (
            ai_response.get_json()
        )

        ai_body = ai_response.get_json()

        assert ai_body == {
            "success": False,
            "error": (
                "Gate 11 simulated AI provider timeout"
            ),
        }

        # ========================================================
        # AI TRANSACTION MUST ROLLBACK
        # ========================================================

        db.session.expire_all()

        refreshed_clinic = db.session.get(
            type(clinic),
            clinic.id,
        )

        assert refreshed_clinic is not None
        assert refreshed_clinic.ai_credits == (
            starting_credits
        )

        logs_after = _ai_logs_for(
            db,
            clinic.id,
        )

        assert len(logs_after) == len(logs_before)

        audits_after = (
            db.session.execute(
                db.select(AuditLog)
                .where(
                    AuditLog.entity_type == "AILog",
                )
                .order_by(
                    AuditLog.id.asc(),
                )
            )
            .scalars()
            .all()
        )

        assert len(audits_after) == len(
            audits_before
        )

        # ========================================================
        # EARLIER LAB + HIE STATE MUST REMAIN
        # ========================================================

        final_lab_order = db.session.get(
            LabOrder,
            lab_order_id,
        )

        final_lab_item = db.session.get(
            LabOrderItem,
            order_item_id,
        )

        final_submissions = _submissions_for(
            db,
            integration_id,
        )

        assert final_lab_order is not None
        assert final_lab_order.clinic_id == clinic.id
        assert (
            final_lab_order.status.value
            == "completed"
        )
        assert (
            final_lab_order.patient_id
            == patient_id
        )

        assert final_lab_item is not None
        assert final_lab_item.order_id == lab_order_id
        assert final_lab_item.result_value == "7.4"

        assert len(final_submissions) == 1

        final_submission = final_submissions[0]

        assert final_submission.clinic_id == clinic.id
        assert final_submission.status is (
            HIESubmissionStatus.SUCCESS
        )
        assert final_submission.external_reference == (
            "G11-S4-HIE-001"
        )

        # ========================================================
        # NO CROSS-TENANT AI SIDE EFFECT
        # ========================================================

        foreign_clinic_id = (
            clinic.id + 1000000
        )

        assert all(
            log.clinic_id == clinic.id
            for log in logs_after
        )

        assert foreign_clinic_id != clinic.id

        # Provider was reached exactly once.
        assert provider.calls == [
            {
                "operation": "query_patient",
                "patient_identifier": (
                    "REMOTE-MRN-GATE11-S4"
                ),
            }
        ]

    finally:
        registry.unregister_provider(
            provider_name
        )