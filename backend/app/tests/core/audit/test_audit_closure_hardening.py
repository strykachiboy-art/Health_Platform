from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from app.core.audit.integrity import (
    verify_audit_log_integrity,
)
from app.core.audit.queries.audit_queries import (
    list_audit_logs,
)
from app.core.audit.queries.patient_timeline import (
    get_patient_timeline,
)
from app.core.audit.services.audit_integrity_service import (
    verify_audit_integrity,
)
from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.logging_safety import PHISafeLogFilter


def _root() -> Path:
    return Path(
        __file__
    ).resolve().parents[5]


def test_explicit_patient_id_is_first_class_evidence(
    db_session,
    clinic,
    user,
    make_patient,
):
    patient = make_patient(
        clinic
    )

    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="ClinicalRecord",
        entity_id=101,
        description="Clinical record changed",
        patient_id=patient.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert log.patient_id == patient.id
    assert verify_audit_log_integrity(
        log
    )


def test_old_style_patient_entity_call_auto_resolves_patient(
    db_session,
    clinic,
    user,
    make_patient,
):
    patient = make_patient(
        clinic
    )

    log = create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Patient",
        entity_id=patient.id,
        description="Patient record changed",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert log.patient_id == patient.id


def test_patient_id_is_inferred_from_nested_payload(
    db_session,
    clinic,
    user,
    make_patient,
):
    patient = make_patient(
        clinic
    )

    log = create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="EmergencyAccessGrant",
        entity_id=999999,
        description="Emergency access changed",
        new_value={
            "context": {
                "patient_id": patient.id,
            }
        },
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    assert log.patient_id == patient.id


def test_cross_tenant_patient_scope_is_rejected(
    clinic,
    user,
    make_clinic,
    make_patient,
):
    other_clinic = make_clinic()
    patient = make_patient(
        other_clinic
    )

    with pytest.raises(
        ValidationError,
        match="does not belong",
    ):
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="ClinicalRecord",
            entity_id=102,
            description="Invalid scope",
            patient_id=patient.id,
            user_id=user.id,
            clinic_id=clinic.id,
        )


def test_patient_filter_returns_cross_domain_events(
    db_session,
    clinic,
    user,
    make_patient,
):
    patient = make_patient(
        clinic
    )

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Prescription",
        entity_id=201,
        description="Medication order created",
        patient_id=patient.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="MedicationAdministration",
        entity_id=202,
        description="Medication administered",
        patient_id=patient.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    page = list_audit_logs(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
        page=1,
        per_page=20,
    )

    assert {
        row.entity_type
        for row in page.items
    }.issuperset(
        {
            "Prescription",
            "MedicationAdministration",
        }
    )


def test_patient_timeline_is_cross_domain(
    db_session,
    clinic,
    user,
    make_patient,
):
    patient = make_patient(
        clinic
    )

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="NursingCarePlan",
        entity_id=301,
        description="Care plan created",
        patient_id=patient.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="DischargeSummary",
        entity_id=302,
        description="Discharge finalized",
        patient_id=patient.id,
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    rows = get_patient_timeline(
        actor_role=Role.ADMIN,
        actor_clinic_id=clinic.id,
        patient_id=patient.id,
    )

    assert {
        row.entity_type
        for row in rows
    }.issuperset(
        {
            "NursingCarePlan",
            "DischargeSummary",
        }
    )


def test_integrity_verifier_reports_valid_range(
    db_session,
    clinic,
    user,
):
    first = create_audit_log(
        action=AuditAction.CREATE,
        entity_type="AuditProbe",
        entity_id=401,
        description="Probe one",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    second = create_audit_log(
        action=AuditAction.CREATE,
        entity_type="AuditProbe",
        entity_id=402,
        description="Probe two",
        user_id=user.id,
        clinic_id=clinic.id,
    )

    db_session.flush()

    result = verify_audit_integrity(
        clinic_id=clinic.id,
        start_id=first.id,
        end_id=second.id,
        limit=100,
    )

    assert result["checked"] >= 2
    assert result["invalid"] == 0
    assert result["valid"] == result["checked"]
    assert result["invalid_ids"] == []


def test_phi_safe_logging_filter_redacts_structured_clinical_data():
    stream = io.StringIO()

    handler = logging.StreamHandler(
        stream
    )
    handler.addFilter(
        PHISafeLogFilter()
    )

    logger = logging.getLogger(
        "gate13.phi-safety"
    )
    logger.handlers = [
        handler
    ]
    logger.propagate = False
    logger.setLevel(
        logging.INFO
    )

    sentinel_name = (
        "PATIENT_NAME_SECRET_84721"
    )
    sentinel_dx = (
        "DIAGNOSIS_SECRET_84721"
    )
    sentinel_token = (
        "TOKEN_SECRET_84721"
    )

    logger.info(
        "clinical event %s",
        {
            "patient_name": sentinel_name,
            "diagnosis": sentinel_dx,
            "authorization": sentinel_token,
            "status": "recorded",
        },
    )

    output = stream.getvalue()

    assert sentinel_name not in output
    assert sentinel_dx not in output
    assert sentinel_token not in output
    assert "[REDACTED]" in output
    assert "recorded" in output


def test_unexpected_error_handler_does_not_log_raw_exception_text():
    text = (
        _root()
        / "backend/app/core/error_handlers.py"
    ).read_text(
        encoding="utf-8-sig"
    )

    assert "logger.exception(" not in text
    assert "exc_info=error" not in text
    assert '"error_type": type(error).__name__' in text


def test_patient_id_is_exposed_by_audit_response_schema():
    text = (
        _root()
        / (
            "backend/app/core/audit/schema/"
            "audit_response.py"
        )
    ).read_text(
        encoding="utf-8-sig"
    )

    assert "patient_id: StrictInt | None" in text


def test_patient_migration_does_not_fabricate_historical_attribution():
    text = (
        _root()
        / (
            "backend/migrations/versions/"
            "b0c92d3e5f18_audit_patient_scope_hardening.py"
        )
    ).read_text(
        encoding="utf-8"
    )

    assert "patient_id" in text
    assert "Historical rows remain patient_id=NULL" in text
    assert "SET patient_id" not in text
    assert "audit_logs_no_update" in text
    assert "audit_logs_no_delete" in text
