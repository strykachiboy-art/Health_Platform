from __future__ import annotations

from app.core.audit.models.audit_model import AuditLog
from app.core.clinical_safety.services.medication_safety_service import (
    evaluate_medication_safety,
)
from app.core.emergency_access.services.consent_guard_service import (
    evaluate_consent_guard,
)
from app.core.emergency_access.services.emergency_access_service import (
    assert_emergency_access,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.emergency_access_enums import (
    ConsentGuardDecision,
    EmergencyAccessStatus,
)
from app.core.enums.role_enums import Role
from app.core.clinical_safety.models.clinical_rule_model import (
    ClinicalRule,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.prescription.models.prescription_model import (
    Prescription,
)


def test_emergency_access_clinical_safety_e2e(
    client,
    db,
    clinic,
    make_clinic,
    make_user,
    make_staff,
    make_patient,
    e2e_login,
    make_consultation,
    make_lab_test,
    make_lab_order,
    make_prescription,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="e2e-g8-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-g8-doctor@test.com",
        },
    )

    receptionist = make_user(
        clinic=clinic,
        role=Role.RECEPTIONIST,
        email="e2e-g8-receptionist@test.com",
    )

    admin_login = e2e_login(
        "e2e-g8-admin@test.com",
    )

    doctor_login = e2e_login(
        "e2e-g8-doctor@test.com",
    )

    receptionist_login = e2e_login(
        "e2e-g8-receptionist@test.com",
    )

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value
    assert receptionist_login["role"] == Role.RECEPTIONIST.value

    # =========================================================================
    # PATIENT
    # =========================================================================

    patient_response = client.post(
        "/api/v1/patients",
        json={
            "first_name": "Gate",
            "last_name": "Eight",
        },
        headers={
            "Authorization": (
                f"Bearer {receptionist_login['access_token']}"
            ),
        },
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

    consultation = make_consultation(
        clinic=clinic,
        patient=persisted_patient,
        staff=doctor_staff,
    )
    assert consultation.clinic_id == clinic.id
    assert consultation.patient_id == patient_id

    lab_test = make_lab_test(
        clinic=clinic,
        name="Emergency Access CBC",
    )
    lab_order = make_lab_order(
        clinic=clinic,
        patient=persisted_patient,
        staff=doctor_staff,
        tests=[lab_test],
    )
    assert lab_order.clinic_id == clinic.id
    assert lab_order.patient_id == patient_id

    prescription = make_prescription(
        clinic=clinic,
        patient=persisted_patient,
        staff=doctor_staff,
    )
    assert prescription.clinic_id == clinic.id
    assert prescription.patient_id == patient_id

    # =========================================================================
    # EMERGENCY ACCESS REQUEST
    # =========================================================================

    request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient_id,
            "reason": "Immediate emergency treatment required.",
            "purpose": "Emergency clinical care.",
            "scope": [
                "patient:read",
                f"consultation:{consultation.id}:read",
                f"lab_order:{lab_order.id}:read",
                f"prescription:{prescription.id}:read",
            ],
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
    )

    assert request_response.status_code == 201, (
        request_response.get_json()
    )

    request_body = request_response.get_json()

    assert request_body["success"] is True
    assert (
        request_body["data"]["status"]
        == EmergencyAccessStatus.REQUESTED.value
    )
    assert request_body["data"]["clinic_id"] == clinic.id
    assert request_body["data"]["patient_id"] == patient_id
    assert (
        request_body["data"]["requester_user_id"]
        == doctor_staff.user.id
    )
    assert request_body["data"]["scope"] == [
        "patient:read",
        f"consultation:{consultation.id}:read",
        f"lab_order:{lab_order.id}:read",
        f"prescription:{prescription.id}:read",
    ]

    emergency_access_id = request_body["data"]["id"]

    # =========================================================================
    # REVIEW + GRANT
    # =========================================================================

    grant_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/grant",
        json={
            "duration_minutes": 20,
            "review_notes": "Approved for immediate emergency treatment.",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
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
    assert grant_body["data"]["granted_at"] is not None
    assert grant_body["data"]["expires_at"] is not None

    # =========================================================================
    # REAL PATIENT ROUTE ? EXPLICIT EMERGENCY ACCESS
    # =========================================================================

    patient_emergency_response = client.get(
        f"/api/v1/patients/{patient_id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert patient_emergency_response.status_code == 200, (
        patient_emergency_response.get_json()
    )

    db.session.expire_all()

    route_view_audits = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        )
        .scalars()
        .all()
    )

    assert len(route_view_audits) == 1, (
        "The patient-detail API must invoke emergency authorization "
        "and create its own emergency-access audit event."
    )

    consultation_response = client.get(
        f"/api/v1/consultations/{consultation.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert consultation_response.status_code == 200, (
        consultation_response.get_json()
    )

    db.session.expire_all()

    consultation_route_view_audits = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        )
        .scalars()
        .all()
    )

    assert len(consultation_route_view_audits) == 2, (
        "The consultation-detail API must perform its own emergency "
        "authorization check and audit the access."
    )

    lab_order_response = client.get(
        f"/api/v1/lab/orders/{lab_order.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert lab_order_response.status_code == 200, (
        lab_order_response.get_json()
    )

    db.session.expire_all()

    lab_route_view_audits = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        )
        .scalars()
        .all()
    )

    assert len(lab_route_view_audits) == 3, (
        "The lab-order detail API must perform its own emergency "
        "authorization check and audit the access."
    )

    prescription_response = client.get(
        f"/api/v1/prescriptions/{prescription.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert prescription_response.status_code == 200, (
        prescription_response.get_json()
    )

    db.session.expire_all()

    prescription_route_view_audits = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        ).scalars().all()
    )

    assert len(prescription_route_view_audits) == 4, (
        "The prescription-detail API must perform its own emergency "
        "authorization check and audit the access."
    )

    # =========================================================================
    # BREAK-GLASS ENFORCEMENT BOUNDARY
    # =========================================================================

    assert (
        assert_emergency_access(
            actor_id=doctor_staff.user.id,
            patient_id=patient_id,
            resource_type="patient",
            resource_id=patient_id,
            action="read",
        )
        is True
    )

    assert (
        assert_emergency_access(
            actor_id=doctor_staff.user.id,
            patient_id=patient_id,
            resource_type="patient",
            resource_id=patient_id + 1,
            action="read",
        )
        is False
    )

    assert (
        assert_emergency_access(
            actor_id=doctor_staff.user.id,
            patient_id=patient_id,
            resource_type="patient",
            resource_id=patient_id,
            action="write",
        )
        is False
    )

    # =========================================================================
    # CONSENT GUARD + EMERGENCY EXCEPTION
    # =========================================================================

    consent_evaluation = evaluate_consent_guard(
        actor_id=doctor_staff.user.id,
        patient_id=patient_id,
        clinic_id=clinic.id,
        recipient_role=Role.DOCTOR.value,
        purpose="Emergency clinical care.",
        emergency_exception=True,
        consent_reference=None,
        policy_context={
            "source": "phase8_gate8_e2e",
        },
    )

    assert (
        consent_evaluation.decision
        == ConsentGuardDecision.EMERGENCY_EXCEPTION
    )
    assert (
        consent_evaluation.emergency_exception
        is True
    )
    assert (
        consent_evaluation.emergency_access_id
        == emergency_access_id
    )
    assert (
        consent_evaluation.effective_from
        is not None
    )
    assert (
        consent_evaluation.effective_until
        is not None
    )

    # =========================================================================
    # EMERGENCY ACCESS AUDIT
    # =========================================================================

    db.session.expire_all()

    emergency_audits = (
        db.session.execute(
            db.select(AuditLog)
            .where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type
                == "EmergencyAccessGrant",
                AuditLog.entity_id
                == emergency_access_id,
            )
            .order_by(
                AuditLog.id.asc(),
            )
        )
        .scalars()
        .all()
    )

    assert emergency_audits

    assert any(
        row.action is AuditAction.CREATE
        and row.user_id == doctor_staff.user.id
        for row in emergency_audits
    )

    assert any(
        row.action is AuditAction.STATUS_CHANGE
        and row.user_id == admin.id
        for row in emergency_audits
    )

    assert any(
        row.action is AuditAction.VIEW
        and row.user_id == doctor_staff.user.id
        for row in emergency_audits
    )

    assert all(
        row.clinic_id == clinic.id
        for row in emergency_audits
    )

    # =========================================================================
    # SECOND-CLINIC PATIENT MUST NOT BE BREAK-GLASS TARGET
    # =========================================================================

    second_clinic = make_clinic(
        name="Gate 8 Other Clinic",
    )

    foreign_patient = make_patient(
        second_clinic,
        first_name="Foreign",
        last_name="Patient",
    )

    foreign_request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": foreign_patient.id,
            "reason": "Cross-tenant emergency test.",
            "purpose": "Cross-tenant denial test.",
            "scope": [
                "patient:read",
            ],
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
    )

    assert foreign_request_response.status_code == 404

    # =========================================================================
    # REVOKE + ENFORCEMENT MUST IMMEDIATELY FAIL
    # =========================================================================

    revoke_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/revoke",
        json={
            "reason": "Emergency treatment completed.",
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert revoke_response.status_code == 200, (
        revoke_response.get_json()
    )

    revoke_body = revoke_response.get_json()

    assert revoke_body["success"] is True
    assert (
        revoke_body["data"]["status"]
        == EmergencyAccessStatus.REVOKED.value
    )

    assert (
        assert_emergency_access(
            actor_id=doctor_staff.user.id,
            patient_id=patient_id,
            resource_type="patient",
            resource_id=patient_id,
            action="read",
        )
        is False
    )

    # A declared emergency request must fail once its grant is revoked.
    revoked_patient_response = client.get(
        f"/api/v1/patients/{patient_id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert revoked_patient_response.status_code == 403, (
        revoked_patient_response.get_json()
    )

    revoked_consultation_response = client.get(
        f"/api/v1/consultations/{consultation.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert revoked_consultation_response.status_code == 403, (
        revoked_consultation_response.get_json()
    )

    revoked_lab_order_response = client.get(
        f"/api/v1/lab/orders/{lab_order.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert revoked_lab_order_response.status_code == 403, (
        revoked_lab_order_response.get_json()
    )

    revoked_prescription_response = client.get(
        f"/api/v1/prescriptions/{prescription.id}",
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
            "X-Emergency-Access-ID": str(emergency_access_id),
        },
    )

    assert revoked_prescription_response.status_code == 403, (
        revoked_prescription_response.get_json()
    )

    # =========================================================================
    # CLINICAL SAFETY RULE
    # =========================================================================

    drug_a_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate 8 Drug A",
            "generic_name": "Gate 8 Drug A",
            "dosage_form": "tablet",
            "strength": "100 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert drug_a_response.status_code == 201, (
        drug_a_response.get_json()
    )

    drug_a_id = drug_a_response.get_json()["data"]["id"]

    drug_b_response = client.post(
        "/api/v1/pharmacy/drugs",
        json={
            "name": "Gate 8 Drug B",
            "generic_name": "Gate 8 Drug B",
            "dosage_form": "tablet",
            "strength": "100 mg",
            "unit_price": "5.00",
            "is_controlled": False,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert drug_b_response.status_code == 201, (
        drug_b_response.get_json()
    )

    drug_b_id = drug_b_response.get_json()["data"]["id"]

    rule_response = client.post(
        "/api/v1/clinical-safety/rules",
        json={
            "rule_code": "GATE8_EMERGENCY_MED_BLOCK",
            "name": "Gate 8 Emergency Medication Block",
            "description": (
                "Break-glass access must never bypass "
                "clinical medication safety."
            ),
            "scope": "clinic",
            "rule_type": "drug_interaction",
            "severity": "critical",
            "action": "block",
            "conditions": {},
            "configuration": {
                "drug_a_id": drug_a_id,
                "drug_b_id": drug_b_id,
                "minimum_severity": "moderate",
            },
            "priority": 1,
            "enabled": True,
        },
        headers={
            "Authorization": (
                f"Bearer {admin_login['access_token']}"
            ),
        },
    )

    assert rule_response.status_code == 201, (
        rule_response.get_json()
    )

    rule_body = rule_response.get_json()

    assert rule_body["rule"]["clinic_id"] == clinic.id
    assert (
        rule_body["rule"]["rule_type"]
        == "drug_interaction"
    )
    assert rule_body["rule"]["action"] == "block"
    assert rule_body["rule"]["enabled"] is True

    # =========================================================================
    # CLINICAL SAFETY ENGINE MUST BLOCK
    # EVEN WHEN EMERGENCY ACCESS CONTEXT EXISTS
    # =========================================================================

    safety_result = evaluate_medication_safety(
        clinic_id=clinic.id,
        medication_ids=[
            drug_a_id,
            drug_b_id,
        ],
        context={
            "patient_id": patient_id,
            "emergency_access_id": emergency_access_id,
            "emergency_exception": True,
            "proposed_medication": {
                "drug_id": drug_b_id,
                "dose": 100,
            },
        },
    )

    assert safety_result.blocked is True
    assert safety_result.outcome == "blocked"

    # =========================================================================
    # REAL PRESCRIPTION WORKFLOW
    #
    # This is the Gate 8 downstream integration assertion.
    # A clinical safety BLOCK must stop prescription creation.
    # =========================================================================

    prescription_ids_before_safety_block = set(
        db.session.execute(
            db.select(Prescription.id)
            .where(
                Prescription.patient_id == patient_id,
                Prescription.clinic_id == clinic.id,
            )
        ).scalars().all()
    )

    prescription_response = client.post(
        "/api/v1/prescriptions",
        json={
            "patient_id": patient_id,
            "items": [
                {
                    "drug_id": drug_a_id,
                    "dosage": "100 mg",
                    "frequency": "once daily",
                    "duration": "5 days",
                    "quantity": 5,
                },
                {
                    "drug_id": drug_b_id,
                    "dosage": "100 mg",
                    "frequency": "once daily",
                    "duration": "5 days",
                    "quantity": 5,
                },
            ],
            "notes": (
                "Gate 8 emergency-access clinical-safety "
                "enforcement test."
            ),
        },
        headers={
            "Authorization": (
                f"Bearer {doctor_login['access_token']}"
            ),
        },
    )

    assert prescription_response.status_code == 422, (
        "Clinical safety BLOCK must prevent prescription creation. "
        f"Response: {prescription_response.get_json()}"
    )

    prescription_ids_after_safety_block = set(
        db.session.execute(
            db.select(Prescription.id)
            .where(
                Prescription.patient_id == patient_id,
                Prescription.clinic_id == clinic.id,
            )
        ).scalars().all()
    )

    assert (
        prescription_ids_after_safety_block
        == prescription_ids_before_safety_block
    ), (
        "Clinical safety BLOCK must not change existing patient prescriptions. "
        f"Before={sorted(prescription_ids_before_safety_block)}; "
        f"after={sorted(prescription_ids_after_safety_block)}"
    )

    print(
        "PHASE8_E2E_GATE8_EMERGENCY_CLINICAL_SAFETY=PASS"
    )

def test_emergency_access_pharmacy_dispense_reads_are_scoped_and_audited(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_prescription,
    e2e_login,
):
    from app.modules.pharmacy.models.pharmacy_model import DispenseRecord

    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="e2e-ea-pharmacy-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-ea-pharmacy-doctor@test.com",
        },
    )

    pharmacist_staff = make_staff(
        clinic=clinic,
        role=Role.PHARMACIST,
        user_overrides={
            "email": "e2e-ea-pharmacy-pharmacist@test.com",
        },
    )

    patient = make_patient(
        clinic,
        first_name="Emergency",
        last_name="Pharmacy Patient",
    )

    prescription = make_prescription(
        clinic=clinic,
        patient=patient,
        staff=doctor_staff,
    )

    dispense_record = DispenseRecord(
        prescription_id=prescription.id,
        dispensed_by_id=pharmacist_staff.id,
    )
    db.session.add(dispense_record)
    db.session.flush()

    admin_login = e2e_login("e2e-ea-pharmacy-admin@test.com")
    pharmacist_login = e2e_login("e2e-ea-pharmacy-pharmacist@test.com")

    assert admin_login["role"] == Role.ADMIN.value
    assert pharmacist_login["role"] == Role.PHARMACIST.value
    doctor_login = e2e_login("e2e-ea-pharmacy-doctor@test.com")
    assert doctor_login["role"] == Role.DOCTOR.value

    pharmacist_without_grant = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record.id}",
        headers={
            "Authorization": f"Bearer {pharmacist_login['access_token']}",
        },
    )
    assert pharmacist_without_grant.status_code == 200, (
        pharmacist_without_grant.get_json()
    )

    doctor_without_grant = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record.id}",
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
        },
    )
    assert doctor_without_grant.status_code == 403, (
        doctor_without_grant.get_json()
    )

    request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient.id,
            "reason": "Emergency medication history review required.",
            "purpose": "Emergency pharmacy care.",
            "scope": [
                f"dispense_record:{dispense_record.id}:read",
                f"prescription:{prescription.id}:read",
            ],
        },
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
        },
    )

    assert request_response.status_code == 201, request_response.get_json()

    request_body = request_response.get_json()
    assert request_body["data"]["patient_id"] == patient.id
    assert request_body["data"]["scope"] == [
        f"dispense_record:{dispense_record.id}:read",
        f"prescription:{prescription.id}:read",
    ]

    emergency_access_id = request_body["data"]["id"]

    grant_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/grant",
        json={
            "duration_minutes": 20,
            "review_notes": "Approved for emergency medication review.",
        },
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert grant_response.status_code == 200, grant_response.get_json()
    assert (
        grant_response.get_json()["data"]["status"]
        == EmergencyAccessStatus.ACTIVE.value
    )

    emergency_headers = {
        "Authorization": f"Bearer {doctor_login['access_token']}",
        "X-Emergency-Access-ID": str(emergency_access_id),
    }

    detail_response = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record.id}",
        headers=emergency_headers,
    )

    assert detail_response.status_code == 200, detail_response.get_json()

    db.session.expire_all()

    dispense_record_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        ).scalars().all()
    )

    assert len(dispense_record_audits) == 1, (
        "The dispense-record detail API must validate emergency access "
        "and create its own audit event."
    )

    history_response = client.get(
        f"/api/v1/pharmacy/prescriptions/{prescription.id}/dispense-records",
        headers=emergency_headers,
    )

    assert history_response.status_code == 200, history_response.get_json()
    assert len(history_response.get_json()["data"]["items"]) == 1

    db.session.expire_all()

    dispense_history_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        ).scalars().all()
    )

    assert len(dispense_history_audits) == 2, (
        "The prescription dispense-history API must validate emergency "
        "access and create its own audit event."
    )

    revoke_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/revoke",
        json={"reason": "Emergency pharmacy review completed."},
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert revoke_response.status_code == 200, revoke_response.get_json()
    assert (
        revoke_response.get_json()["data"]["status"]
        == EmergencyAccessStatus.REVOKED.value
    )

    revoked_detail_response = client.get(
        f"/api/v1/pharmacy/dispense/{dispense_record.id}",
        headers=emergency_headers,
    )
    assert revoked_detail_response.status_code == 403, (
        revoked_detail_response.get_json()
    )

    revoked_history_response = client.get(
        f"/api/v1/pharmacy/prescriptions/{prescription.id}/dispense-records",
        headers=emergency_headers,
    )
    assert revoked_history_response.status_code == 403, (
        revoked_history_response.get_json()
    )

def test_emergency_access_patient_scoped_lists_are_scoped_and_audited(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    make_patient,
    make_lab_test,
    make_lab_order,
    make_prescription,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="e2e-ea-list-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-ea-list-doctor@test.com",
        },
    )

    patient = make_patient(
        clinic,
        first_name="Emergency",
        last_name="List Patient",
    )

    lab_test = make_lab_test(
        clinic=clinic,
        name="Emergency List CBC",
    )
    lab_order = make_lab_order(
        clinic=clinic,
        patient=patient,
        staff=doctor_staff,
        tests=[lab_test],
    )
    prescription = make_prescription(
        clinic=clinic,
        patient=patient,
        staff=doctor_staff,
    )

    assert lab_order.patient_id == patient.id
    assert prescription.patient_id == patient.id

    admin_login = e2e_login("e2e-ea-list-admin@test.com")
    doctor_login = e2e_login("e2e-ea-list-doctor@test.com")

    assert admin_login["role"] == Role.ADMIN.value
    assert doctor_login["role"] == Role.DOCTOR.value

    request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient.id,
            "reason": "Emergency review of patient clinical history.",
            "purpose": "Emergency clinical care.",
            "scope": [
                f"lab_order_list:{patient.id}:read",
                f"prescription_list:{patient.id}:read",
            ],
        },
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
        },
    )

    assert request_response.status_code == 201, request_response.get_json()
    emergency_access_id = request_response.get_json()["data"]["id"]

    grant_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/grant",
        json={
            "duration_minutes": 20,
            "review_notes": "Approved for emergency patient-history review.",
        },
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert grant_response.status_code == 200, grant_response.get_json()
    assert (
        grant_response.get_json()["data"]["status"]
        == EmergencyAccessStatus.ACTIVE.value
    )

    emergency_headers = {
        "Authorization": f"Bearer {doctor_login['access_token']}",
        "X-Emergency-Access-ID": str(emergency_access_id),
    }

    lab_list_response = client.get(
        f"/api/v1/lab/orders?patient_id={patient.id}",
        headers=emergency_headers,
    )
    assert lab_list_response.status_code == 200, lab_list_response.get_json()

    db.session.expire_all()

    lab_list_route_view_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        ).scalars().all()
    )

    assert len(lab_list_route_view_audits) == 1, (
        "The patient-scoped lab-order list API must validate emergency "
        "access and create its own audit event."
    )

    prescription_list_response = client.get(
        f"/api/v1/prescriptions/patients/{patient.id}",
        headers=emergency_headers,
    )
    assert prescription_list_response.status_code == 200, (
        prescription_list_response.get_json()
    )

    db.session.expire_all()

    prescription_list_route_view_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        ).scalars().all()
    )

    assert len(prescription_list_route_view_audits) == 2, (
        "The patient-prescription list API must validate emergency access "
        "and create its own audit event."
    )

    revoke_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/revoke",
        json={"reason": "Emergency history review completed."},
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert revoke_response.status_code == 200, revoke_response.get_json()
    assert (
        revoke_response.get_json()["data"]["status"]
        == EmergencyAccessStatus.REVOKED.value
    )

    revoked_lab_list_response = client.get(
        f"/api/v1/lab/orders?patient_id={patient.id}",
        headers=emergency_headers,
    )
    assert revoked_lab_list_response.status_code == 403, (
        revoked_lab_list_response.get_json()
    )

    revoked_prescription_list_response = client.get(
        f"/api/v1/prescriptions/patients/{patient.id}",
        headers=emergency_headers,
    )
    assert revoked_prescription_list_response.status_code == 403, (
        revoked_prescription_list_response.get_json()
    )


def test_patient_read_scope_does_not_authorize_patient_clinical_lists(
    client,
    db,
    clinic,
    make_user,
    make_staff,
    make_patient,
    e2e_login,
):
    admin = make_user(
        clinic=clinic,
        role=Role.ADMIN,
        email="e2e-ea-patient-only-admin@test.com",
    )

    doctor_staff = make_staff(
        clinic=clinic,
        role=Role.DOCTOR,
        user_overrides={
            "email": "e2e-ea-patient-only-doctor@test.com",
        },
    )

    patient = make_patient(
        clinic,
        first_name="Emergency",
        last_name="Patient Scope Only",
    )

    admin_login = e2e_login("e2e-ea-patient-only-admin@test.com")
    doctor_login = e2e_login("e2e-ea-patient-only-doctor@test.com")

    request_response = client.post(
        "/api/v1/emergency-access/requests",
        json={
            "patient_id": patient.id,
            "reason": "Emergency patient-record review.",
            "purpose": "Verify that patient read scope is not a clinical-list grant.",
            "scope": ["patient:read"],
        },
        headers={
            "Authorization": f"Bearer {doctor_login['access_token']}",
        },
    )

    assert request_response.status_code == 201, request_response.get_json()
    emergency_access_id = request_response.get_json()["data"]["id"]

    grant_response = client.post(
        f"/api/v1/emergency-access/requests/{emergency_access_id}/grant",
        json={
            "duration_minutes": 20,
            "review_notes": "Approved for patient-record review only.",
        },
        headers={
            "Authorization": f"Bearer {admin_login['access_token']}",
        },
    )

    assert grant_response.status_code == 200, grant_response.get_json()

    emergency_headers = {
        "Authorization": f"Bearer {doctor_login['access_token']}",
        "X-Emergency-Access-ID": str(emergency_access_id),
    }

    lab_response = client.get(
        f"/api/v1/lab/orders?patient_id={patient.id}",
        headers=emergency_headers,
    )
    prescription_response = client.get(
        f"/api/v1/prescriptions/patients/{patient.id}",
        headers=emergency_headers,
    )

    assert lab_response.status_code == 403, lab_response.get_json()
    assert prescription_response.status_code == 403, (
        prescription_response.get_json()
    )

    db.session.expire_all()

    view_audits = (
        db.session.execute(
            db.select(AuditLog).where(
                AuditLog.clinic_id == clinic.id,
                AuditLog.entity_type == "EmergencyAccessGrant",
                AuditLog.entity_id == emergency_access_id,
                AuditLog.action == AuditAction.VIEW,
                AuditLog.user_id == doctor_staff.user.id,
            )
        )
        .scalars()
        .all()
    )

    assert view_audits == [], (
        "Denied clinical-list requests must not create successful "
        "emergency-access view audit events."
    )
