from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import tuple_

from app.core.audit.models.audit_model import AuditLog
from app.core.enums.asset_enums import AssetCategory
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emergency_access_enums import EmergencyAccessStatus
from app.core.enums.feedback_enums import (
    FeedbackCategory,
    FeedbackType,
)
from app.core.enums.reports_enums import (
    ReportFormat,
    ReportType,
)
from app.core.enums.role_enums import Role
from app.modules.asset_control.models.asset_model import Asset
from app.modules.billing.models.billing_model import Invoice
from app.core.emergency_access.models.emergency_access_model import (
    EmergencyAccessGrant,
)
from app.modules.feedback.models.feedback_model import Feedback
from app.modules.patient.models.patient_model import Patient
from app.modules.reports.models.reports_model import GeneratedReport


def _headers(login: dict) -> dict[str, str]:
    return {
        "Authorization": (
            f"Bearer {login['access_token']}"
        ),
    }


def _audit_rows(
    db,
    entity_type: str,
    entity_id: int,
):
    return (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.entity_type == entity_type,
                AuditLog.entity_id == entity_id,
            )
            .order_by(
                AuditLog.id.asc(),
            )
        )
        .scalars()
        .all()
    )


def _assert_audit_owner(
    rows,
    *,
    clinic_id: int,
    user_id: int,
    action: AuditAction,
):
    assert rows
    assert any(
        row.clinic_id == clinic_id
        and row.user_id == user_id
        and row.action is action
        for row in rows
    )
    assert all(
        row.clinic_id == clinic_id
        for row in rows
    )


def test_security_audit_cross_domain_e2e(
    client,
    db,
    clinic,
    make_clinic,
    make_user,
    make_staff,
    e2e_login,
    monkeypatch,
    tmp_path,
):
    monkeypatch.chdir(tmp_path)

    # ================================================================
    # PRIMARY CLINIC ACTORS
    # ================================================================

    admin_staff = make_staff(
        clinic=clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "e2e-g13-admin@test.com",
        },
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-g13-doctor@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="e2e-g13-receptionist@test.com",
    )

    admin = admin_staff.user
    doctor = doctor_staff.user

    admin_login = e2e_login(
        "e2e-g13-admin@test.com",
    )

    doctor_login = e2e_login(
        "e2e-g13-doctor@test.com",
    )

    receptionist_login = e2e_login(
        "e2e-g13-receptionist@test.com",
    )

    assert admin_login["user_id"] == admin.id
    assert admin_login["role"] == Role.ADMIN.value

    assert doctor_login["user_id"] == doctor.id
    assert doctor_login["role"] == Role.DOCTOR.value

    assert receptionist_login["user_id"] == receptionist.id
    assert (
        receptionist_login["role"]
        == Role.RECEPTIONIST.value
    )

    admin_headers = _headers(admin_login)
    doctor_headers = _headers(doctor_login)
    receptionist_headers = _headers(
        receptionist_login
    )

    # ================================================================
    # CLINICAL DOMAIN — PATIENT
    # ================================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate",
            "last_name": "Thirteen",
        },
        headers=receptionist_headers,
    )

    assert patient_response.status_code == 201, (
        patient_response.get_json()
    )

    patient_body = patient_response.get_json()

    assert patient_body["success"] is True
    assert patient_body["data"]["clinic_id"] == clinic.id

    patient_id = patient_body["data"]["id"]

    persisted_patient = db.session.get(
        Patient,
        patient_id,
    )

    assert persisted_patient is not None
    assert persisted_patient.clinic_id == clinic.id

    # ================================================================
    # FINANCIAL DOMAIN — INVOICE
    # ================================================================

    invoice_response = client.post(
        "/api/v1/billing/invoices",
        json={
            "patient_id": patient_id,
            "currency": "NGN",
            "due_date": (
                date.today()
                + timedelta(days=7)
            ).isoformat(),
            "items": [
                {
                    "description": (
                        "Gate 13 audit "
                        "consultation"
                    ),
                    "quantity": 1,
                    "unit_price": "150.00",
                }
            ],
        },
        headers=admin_headers,
    )

    assert invoice_response.status_code == 201, (
        invoice_response.get_json()
    )

    invoice_body = invoice_response.get_json()

    assert invoice_body["success"] is True
    assert invoice_body["data"]["clinic_id"] == clinic.id
    assert (
        invoice_body["data"]["patient_id"]
        == patient_id
    )

    invoice_id = invoice_body["data"]["id"]

    persisted_invoice = db.session.get(
        Invoice,
        invoice_id,
    )

    assert persisted_invoice is not None
    assert persisted_invoice.clinic_id == clinic.id
    assert persisted_invoice.patient_id == patient_id

    # ================================================================
    # OPERATIONAL DOMAIN — ASSET
    # ================================================================

    asset_response = client.post(
        "/api/v1/assets",
        json={
            "asset_tag": "G13-ASSET-001",
            "name": "Gate 13 Portable Monitor",
            "category": (
                AssetCategory.MEDICAL_DEVICE.value
            ),
            "condition": "good",
            "ownership": "clinic",
            "location": "Gate 13 Operations",
        },
        headers=admin_headers,
    )

    assert asset_response.status_code == 201, (
        asset_response.get_json()
    )

    asset_body = asset_response.get_json()

    assert asset_body["success"] is True
    assert asset_body["asset"]["clinic_id"] == clinic.id

    asset_id = asset_body["asset"]["id"]

    persisted_asset = db.session.get(
        Asset,
        asset_id,
    )

    assert persisted_asset is not None
    assert persisted_asset.clinic_id == clinic.id

    # ================================================================
    # COMMUNICATION DOMAIN — FEEDBACK
    # ================================================================

    feedback_response = client.post(
        "/api/v1/feedback",
        json={
            "feedback_type": (
                FeedbackType.BUG_REPORT.value
            ),
            "category": (
                FeedbackCategory.CLINICAL_WORKFLOW.value
            ),
            "subject": "Gate 13 audit feedback",
            "message": (
                "Cross-domain audit attribution "
                "workflow."
            ),
            "target_module": "patient",
            "target_resource_type": "patient",
            "target_resource_id": patient_id,
        },
        headers=doctor_headers,
    )

    assert feedback_response.status_code == 201, (
        feedback_response.get_json()
    )

    feedback_body = feedback_response.get_json()

    assert feedback_body["success"] is True
    assert feedback_body["data"]["clinic_id"] == clinic.id
    assert (
        feedback_body["data"]["submitted_by_user_id"]
        == doctor.id
    )

    feedback_id = feedback_body["data"]["id"]

    persisted_feedback = db.session.get(
        Feedback,
        feedback_id,
    )

    assert persisted_feedback is not None
    assert persisted_feedback.clinic_id == clinic.id
    assert (
        persisted_feedback.submitted_by_user_id
        == doctor.id
    )

    # ================================================================
    # EMERGENCY DOMAIN — REQUEST + GRANT
    # ================================================================

    emergency_request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient_id,
            "reason": (
                "Gate 13 emergency audit "
                "security workflow."
            ),
            "purpose": (
                "Emergency clinical audit "
                "verification."
            ),
            "scope": [
                "patient:read",
            ],
        },
        headers=doctor_headers,
    )

    assert emergency_request_response.status_code == 201, (
        emergency_request_response.get_json()
    )

    emergency_request_body = (
        emergency_request_response.get_json()
    )

    assert emergency_request_body["success"] is True
    assert (
        emergency_request_body["data"]["clinic_id"]
        == clinic.id
    )
    assert (
        emergency_request_body["data"]["patient_id"]
        == patient_id
    )
    assert (
        emergency_request_body["data"]["requester_user_id"]
        == doctor.id
    )
    assert (
        emergency_request_body["data"]["status"]
        == EmergencyAccessStatus.REQUESTED.value
    )

    emergency_access_id = (
        emergency_request_body["data"]["id"]
    )

    grant_response = client.post(
        (
            "/api/v1/emergency-access/requests/"
            f"{emergency_access_id}/grant"
        ),
        json={
            "duration_minutes": 20,
            "review_notes": (
                "Gate 13 audit attribution "
                "grant verification."
            ),
        },
        headers=admin_headers,
    )

    assert grant_response.status_code == 200, (
        grant_response.get_json()
    )

    grant_body = grant_response.get_json()

    assert grant_body["success"] is True
    assert (
        grant_body["data"]["status"]
        == EmergencyAccessStatus.ACTIVE.value
    )
    assert (
        grant_body["data"]["reviewed_by_user_id"]
        == admin.id
    )

    persisted_grant = db.session.get(
        EmergencyAccessGrant,
        emergency_access_id,
    )

    assert persisted_grant is not None
    assert persisted_grant.clinic_id == clinic.id
    assert persisted_grant.requester_user_id == doctor.id
    assert persisted_grant.reviewed_by_user_id == admin.id

    # ================================================================
    # INTEGRATION / STORAGE DOMAIN — REPORT
    # ================================================================

    report_response = client.post(
        "/api/v1/reports",
        json={
            "report_type": ReportType.PATIENTS.value,
            "report_format": ReportFormat.CSV.value,
        },
        headers=admin_headers,
    )

    assert report_response.status_code == 201, (
        report_response.get_json()
    )

    report_body = report_response.get_json()

    assert report_body["success"] is True
    assert (
        report_body["data"]["clinic_id"]
        == clinic.id
    )
    assert (
        report_body["data"]["generated_by_id"]
        == admin.id
    )
    assert report_body["data"]["file_url"]

    report_id = report_body["data"]["id"]

    persisted_report = db.session.get(
        GeneratedReport,
        report_id,
    )

    assert persisted_report is not None
    assert persisted_report.clinic_id == clinic.id
    assert persisted_report.generated_by_id == admin.id

    assert Path(
        persisted_report.file_url
    ).exists()

    # ================================================================
    # AUDIT OWNERSHIP + ACTOR INTEGRITY
    # ================================================================

    patient_audits = _audit_rows(
        db,
        "patient",
        patient_id,
    )

    invoice_audits = _audit_rows(
        db,
        "Invoice",
        invoice_id,
    )

    asset_audits = _audit_rows(
        db,
        "Asset",
        asset_id,
    )

    feedback_audits = _audit_rows(
        db,
        "Feedback",
        feedback_id,
    )

    emergency_audits = _audit_rows(
        db,
        "EmergencyAccessGrant",
        emergency_access_id,
    )

    report_audits = _audit_rows(
        db,
        "GeneratedReport",
        report_id,
    )

    _assert_audit_owner(
        patient_audits,
        clinic_id=clinic.id,
        user_id=receptionist.id,
        action=AuditAction.CREATE,
    )

    _assert_audit_owner(
        invoice_audits,
        clinic_id=clinic.id,
        user_id=admin.id,
        action=AuditAction.CREATE,
    )

    _assert_audit_owner(
        asset_audits,
        clinic_id=clinic.id,
        user_id=admin.id,
        action=AuditAction.CREATE,
    )

    _assert_audit_owner(
        feedback_audits,
        clinic_id=clinic.id,
        user_id=doctor.id,
        action=AuditAction.CREATE,
    )

    _assert_audit_owner(
        emergency_audits,
        clinic_id=clinic.id,
        user_id=doctor.id,
        action=AuditAction.CREATE,
    )

    _assert_audit_owner(
        emergency_audits,
        clinic_id=clinic.id,
        user_id=admin.id,
        action=AuditAction.STATUS_CHANGE,
    )

    _assert_audit_owner(
        report_audits,
        clinic_id=clinic.id,
        user_id=admin.id,
        action=AuditAction.CREATE,
    )

    # Every audit record attached to these resources must remain
    # tenant-correct and must correspond to the real resource.
    all_scoped_audits = (
        patient_audits
        + invoice_audits
        + asset_audits
        + feedback_audits
        + emergency_audits
        + report_audits
    )

    assert all_scoped_audits
    assert all(
        row.clinic_id == clinic.id
        for row in all_scoped_audits
    )

    # ================================================================
    # SECOND CLINIC
    # ================================================================

    second_clinic = make_clinic(
        name="Gate 13 Foreign Clinic",
    )

    second_admin_staff = make_staff(
        clinic=second_clinic,
        role=Role.ADMIN,
        user_overrides={
            "email": "e2e-g13-foreign-admin@test.com",
        },
    )

    second_doctor_staff = make_staff(
        clinic=second_clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-g13-foreign-doctor@test.com",
        },
    )

    second_admin_login = e2e_login(
        "e2e-g13-foreign-admin@test.com",
    )

    second_doctor_login = e2e_login(
        "e2e-g13-foreign-doctor@test.com",
    )

    second_admin_headers = _headers(
        second_admin_login
    )

    second_doctor_headers = _headers(
        second_doctor_login
    )

    second_admin_id = second_admin_staff.user.id
    second_doctor_id = second_doctor_staff.user.id

    # Capture all audit rows associated with the successful
    # cross-domain resources before denied attempts begin.
    known_pairs = [
        ("patient", patient_id),
        ("Invoice", invoice_id),
        ("Asset", asset_id),
        ("Feedback", feedback_id),
        (
            "EmergencyAccessGrant",
            emergency_access_id,
        ),
        (
            "GeneratedReport",
            report_id,
        ),
    ]

    audit_count_before_denials = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                tuple_(
                    AuditLog.entity_type,
                    AuditLog.entity_id,
                ).in_(known_pairs)
            )
        )
        .scalars()
        .all()
    )

    audit_count_before = len(
        audit_count_before_denials
    )

    # ================================================================
    # ROLE RESTRICTION — RECEPTIONIST CANNOT MANAGE ASSETS
    # ================================================================

    denied_asset_response = client.post(
        "/api/v1/assets",
        json={
            "asset_tag": "G13-DENIED-ASSET",
            "name": "Denied Gate 13 Asset",
            "category": (
                AssetCategory.MEDICAL_DEVICE.value
            ),
        },
        headers=receptionist_headers,
    )

    assert denied_asset_response.status_code == 403

    # ================================================================
    # CROSS-CLINIC BILLING WRITE MUST FAIL
    # ================================================================

    foreign_payment_response = client.post(
        "/api/v1/billing/payments",
        json={
            "invoice_id": invoice_id,
            "amount": "1.00",
            "method": "cash",
        },
        headers=second_admin_headers,
    )

    assert foreign_payment_response.status_code == 404

    # ================================================================
    # CROSS-CLINIC COMMUNICATION TARGET MUST FAIL
    # ================================================================

    foreign_feedback_response = client.post(
        "/api/v1/feedback",
        json={
            "feedback_type": (
                FeedbackType.BUG_REPORT.value
            ),
            "category": (
                FeedbackCategory.SECURITY.value
            ),
            "subject": (
                "Gate 13 foreign target attempt"
            ),
            "message": (
                "Cross-clinic audit isolation test."
            ),
            "target_module": "patient",
            "target_resource_type": "patient",
            "target_resource_id": patient_id,
        },
        headers=second_doctor_headers,
    )

    assert foreign_feedback_response.status_code == 404

    # ================================================================
    # CROSS-CLINIC EMERGENCY REQUEST MUST FAIL
    # ================================================================

    foreign_emergency_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient_id,
            "reason": (
                "Foreign clinic emergency attempt."
            ),
            "purpose": (
                "Must remain tenant isolated."
            ),
            "scope": [
                "patient:read",
            ],
        },
        headers=second_doctor_headers,
    )

    assert foreign_emergency_response.status_code == 404

    # ================================================================
    # NO AUDIT CONTAMINATION FROM DENIED ACTIONS
    # ================================================================

    db.session.expire_all()

    audit_rows_after_denials = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                tuple_(
                    AuditLog.entity_type,
                    AuditLog.entity_id,
                ).in_(known_pairs)
            )
            .order_by(
                AuditLog.id.asc(),
            )
        )
        .scalars()
        .all()
    )

    assert len(audit_rows_after_denials) == (
        audit_count_before
    )

    assert all(
        row.clinic_id == clinic.id
        for row in audit_rows_after_denials
    )

    assert all(
        row.user_id not in {
            second_admin_id,
            second_doctor_id,
        }
        for row in audit_rows_after_denials
    )

    # No foreign audit record should ever point at one of the
    # primary clinic resources through these denied requests.
    assert not any(
        row.clinic_id != clinic.id
        for row in audit_rows_after_denials
    )

    print(
        "PHASE8_E2E_GATE13_SECURITY_AUDIT_CROSS_DOMAIN=PASS"
    )
