from __future__ import annotations

from pathlib import Path

import pytest

from app.core.audit.integrity import (
    calculate_audit_evidence_sha256,
    verify_audit_log_integrity,
)
from app.core.audit.serialization.audit_serializer import (
    serialize_audit_log,
)
from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError


def _root() -> Path:
    return Path(
        __file__
    ).resolve().parents[5]


def test_audit_evidence_fields_are_persisted(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="ClinicalRecord",
        entity_id=41,
        description="Clinical record finalized",
        reason="Clinician signed documentation",
        operation_id="gate13-op-001",
        old_value={
            "status": "draft",
        },
        new_value={
            "status": "final",
        },
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert log.reason == (
        "Clinician signed documentation"
    )
    assert log.operation_id == "gate13-op-001"
    assert isinstance(
        log.evidence_sha256,
        str,
    )
    assert len(log.evidence_sha256) == 64
    assert verify_audit_log_integrity(
        log
    )


def test_reason_is_derived_from_clinical_payload(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="MedicationAdministration",
        entity_id=42,
        description="Medication held",
        new_value={
            "status": "held",
            "reason": "Blood pressure too low",
        },
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert (
        log.reason
        == "Blood pressure too low"
    )


def test_reason_falls_back_to_audit_description(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Prescription",
        entity_id=43,
        description="Medication order created",
        new_value={
            "status": "active",
        },
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert (
        log.reason
        == "Medication order created"
    )


def test_idempotency_key_becomes_operation_identity(
    app,
    db_session,
    clinic,
    user,
):
    with app.test_request_context(
        "/audit-test",
        headers={
            "Idempotency-Key": (
                "gate13-idempotency-001"
            ),
        },
    ):
        log = create_audit_log(
            action=AuditAction.CREATE,
            entity_type="Prescription",
            entity_id=44,
            description="Medication order created",
            user_id=user.id,
            clinic_id=clinic.id,
        )

    db_session.flush()

    assert (
        log.operation_id
        == "gate13-idempotency-001"
    )


def test_explicit_operation_identity_precedes_request_header(
    app,
    db_session,
    clinic,
    user,
):
    with app.test_request_context(
        "/audit-test",
        headers={
            "Idempotency-Key": "header-operation",
        },
    ):
        log = create_audit_log(
            action=AuditAction.CREATE,
            entity_type="Prescription",
            entity_id=45,
            description="Medication order created",
            operation_id="explicit-operation",
            user_id=user.id,
            clinic_id=clinic.id,
        )

    db_session.flush()

    assert (
        log.operation_id
        == "explicit-operation"
    )


def test_x_operation_id_is_supported(
    app,
    db_session,
    clinic,
    user,
):
    with app.test_request_context(
        "/audit-test",
        headers={
            "X-Operation-Id": "offline-op-123",
            "Idempotency-Key": "fallback-idem",
        },
    ):
        log = create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="MedicationReconciliation",
            entity_id=46,
            description="Medication reconciliation amended",
            user_id=user.id,
            clinic_id=clinic.id,
        )

    db_session.flush()

    assert log.operation_id == "offline-op-123"


def test_secret_redaction_is_sealed_not_raw_secret(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        description="Credential metadata changed",
        old_value={
            "password": "old-secret",
        },
        new_value={
            "password": "new-secret",
        },
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert log.old_value["password"] == "[REDACTED]"
    assert log.new_value["password"] == "[REDACTED]"
    assert verify_audit_log_integrity(log)


def test_integrity_verifier_detects_in_memory_tampering(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="PatientAllergy",
        entity_id=47,
        description="Allergy status changed",
        old_value={"status": "active"},
        new_value={"status": "resolved"},
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    original_digest = log.evidence_sha256

    log.description = "tampered description"

    assert (
        calculate_audit_evidence_sha256(
            log
        )
        != original_digest
    )
    assert not verify_audit_log_integrity(
        log
    )

    db_session.rollback()


def test_orm_update_is_rejected(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="PatientAllergy",
        entity_id=48,
        description="Allergy changed",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    log.description = "tampered"

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        db_session.flush()

    db_session.rollback()


def test_orm_delete_is_rejected(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="PatientAllergy",
        entity_id=49,
        description="Allergy changed",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    db_session.delete(
        log
    )

    with pytest.raises(
        ConflictError,
        match="immutable",
    ):
        db_session.flush()

    db_session.rollback()


def test_serializer_exposes_audit_evidence_contract(
    db_session,
    clinic,
    user,
):
    log = create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="DischargeSummary",
        entity_id=50,
        description="Discharge summary finalized",
        reason="Clinical discharge completed",
        operation_id="gate13-op-serializer",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    payload = serialize_audit_log(
        log
    )

    assert payload["reason"] == (
        "Clinical discharge completed"
    )
    assert payload["operation_id"] == (
        "gate13-op-serializer"
    )
    assert payload["evidence_sha256"] == (
        log.evidence_sha256
    )


def test_critical_clinical_writers_use_central_audit_service():
    root = _root()

    critical_writers = {
        "medication_orders": (
            "backend/app/modules/prescription/"
            "services/prescription_service.py"
        ),
        "cds_outcomes": (
            "backend/app/core/clinical_safety/"
            "services/alert_service.py"
        ),
        "allergy_changes": (
            "backend/app/modules/patient/"
            "services/patient_service.py"
        ),
        "medication_reconciliation": (
            "backend/app/modules/medication_reconciliation/"
            "services/medication_reconciliation_service.py"
        ),
        "emar_actions": (
            "backend/app/modules/emar/"
            "services/emar_service.py"
        ),
        "nursing_care": (
            "backend/app/modules/nursing/"
            "services/nursing_service.py"
        ),
        "consultation_finalization": (
            "backend/app/modules/consultation/"
            "services/consultation_service.py"
        ),
        "shared_amendments": (
            "backend/app/modules/clinical_records/"
            "services/clinical_record_service.py"
        ),
        "discharge_finalization": (
            "backend/app/modules/ward/"
            "services/discharge_service.py"
        ),
        "break_glass": (
            "backend/app/core/emergency_access/"
            "services/emergency_access_service.py"
        ),
    }

    for label, rel in critical_writers.items():
        path = root / rel

        assert path.is_file(), (
            f"{label} writer missing: {rel}"
        )

        text = path.read_text(
            encoding="utf-8-sig"
        )

        assert "create_audit_log" in text, (
            f"{label} bypasses central audit service"
        )


def test_migration_defines_database_immutability_guards():
    migration = (
        _root()
        / (
            "backend/migrations/versions/"
            "a9b81c2d4f07_immutable_audit_evidence.py"
        )
    ).read_text(
        encoding="utf-8"
    )

    assert "audit_logs_no_update" in migration
    assert "audit_logs_no_delete" in migration
    assert (
        "prevent_audit_logs_mutation"
        in migration
    )
    assert "evidence_sha256" in migration
    assert "operation_id" in migration
    assert "reason" in migration
