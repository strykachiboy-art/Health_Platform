"""CE1-S05 ONLY: pre-anesthesia evidence, safety and negative authorization gates.

Run this file only. Earlier Surgery/ProcedureOrder suites remain deferred.
Imports earlier fixture helpers but does NOT execute their test modules.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.surgery.models.anesthesia_model import (
    AnesthesiaBaselineObservation, AnesthesiaPreoperativeAssessment,
)
from app.modules.surgery.services import anesthesia_service as svc
from app.modules.surgery.services import surgical_authorization_service as authorization
from app.modules.surgery.services import theatre_booking_service as booking
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready


@pytest.fixture
def anesthesia_ready(ready, monkeypatch):
    ctx = ready
    monkeypatch.setattr(svc, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    return ctx


def _record(ctx, **kwargs):
    args = dict(
        clinic_id=ctx["clinic"].id,
        surgical_case_id=ctx["case"].id,
        surgeon_staff_id=ctx["doctor"].id,
        anesthetist_staff_id=ctx["anesthetist"].id,
        actor_staff_id=ctx["anesthetist"].id,
        asa_class=2, technique="general", airway_assessment="routine",
        airway_plan="Documented airway approach and backup equipment",
        allergy_status="none_known", allergy_notes=None,
        fasting_confirmed=True, fasting_notes="Fasting history reviewed",
        risk_and_comorbidity_notes="Example test-only risk assessment",
        plan_summary="Proposed approach pending operative authorization",
        monitoring_plan="Documented monitoring plan; no monitoring started",
        contingency_plan="Documented fallback and escalation strategy",
    )
    args.update(kwargs)
    return svc.record_preanesthesia_assessment(**args)


def _baseline(ctx, row, **kwargs):
    args = dict(
        assessment_id=row.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["anesthetist"].id, expected_version=row.version,
        observed_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        heart_rate_bpm=78, systolic_bp=117, diastolic_bp=74,
        spo2_percent=98, respiratory_rate=16, temperature_tenths_c=370,
        oxygen_delivery="ambient_air",
        observation_notes="Example test-only preoperative baseline",
    )
    args.update(kwargs)
    return svc.add_anesthesia_baseline(**args)


def _review(ctx, row, **kwargs):
    args = dict(assessment_id=row.id, clinic_id=ctx["clinic"].id,
                actor_staff_id=ctx["anesthetist"].id,
                expected_version=row.version)
    args.update(kwargs)
    return svc.attest_preanesthesia_review(**args)


def test_preoperative_draft_links_authorities_and_never_starts_surgery(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    assert row.id is not None and row.status == "draft" and row.version == 1
    assert row.clinic_id == ctx["clinic"].id
    assert row.patient_id == ctx["patient"].id
    assert row.surgical_case_id == ctx["case"].id
    assert row.procedure_order_id == ctx["order"].id
    assert row.procedure_order_version == ctx["order"].version
    assert row.theatre_booking_id == ctx["booking"].id
    assert row.booking_version == ctx["booking"].version
    assert row.surgical_consent_id == ctx["consent"].id
    assert row.anesthetist_staff_id == ctx["anesthetist"].id
    assert row.evidence_sha256 is None
    assert ctx["case"].status == "planned" and ctx["order"].status == "active"
    assert any(e["entity_type"] == "AnesthesiaPreoperativeAssessment" for e in ctx["events"])


def test_only_assigned_anesthetist_may_document(anesthesia_ready):
    with pytest.raises(ConflictError, match="assigned anesthetist"):
        _record(anesthesia_ready, actor_staff_id=anesthesia_ready["doctor"].id)
    with pytest.raises(ValidationError, match="doctor"):
        _record(anesthesia_ready, actor_staff_id=anesthesia_ready["nurse"].id)
    assert db.session.query(AnesthesiaPreoperativeAssessment).count() == 0


def test_missing_or_revoked_anesthetist_privilege_blocks_documentation(anesthesia_ready):
    ctx = anesthesia_ready
    privilege = db.session.execute(db.select(authorization.SurgicalProfessionalPrivilege).where(
        authorization.SurgicalProfessionalPrivilege.staff_id == ctx["anesthetist"].id,
        authorization.SurgicalProfessionalPrivilege.privilege_kind == "anesthetist",
    )).scalar_one()
    authorization.revoke_surgical_privilege(
        privilege_id=privilege.id, clinic_id=ctx["clinic"].id,
        admin_staff_id=ctx["admin"].id, expected_version=1,
        reason="Credential review pending",
    )
    with pytest.raises(ConflictError, match="anesthetist privilege"):
        _record(ctx)


def test_duplicate_assessment_fail_closed(anesthesia_ready):
    _record(anesthesia_ready)
    with pytest.raises(ConflictError, match="already exists"):
        _record(anesthesia_ready)
    assert db.session.query(AnesthesiaPreoperativeAssessment).count() == 1


def test_unknown_allergy_airway_and_unconfirmed_fasting_recordable_not_attestable(anesthesia_ready):
    row = _record(anesthesia_ready, airway_assessment="unknown",
                  allergy_status="unknown", fasting_confirmed=False)
    _baseline(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="Airway assessment"):
        _review(anesthesia_ready, row)
    assert row.status == "draft"


def test_unresolved_allergy_and_fasting_independently_block_review(anesthesia_ready):
    row = _record(anesthesia_ready, allergy_status="unknown")
    _baseline(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="Allergy status"):
        _review(anesthesia_ready, row)
    row.allergy_status = "none_known"
    row.fasting_confirmed = False
    db.session.commit()
    with pytest.raises(ConflictError, match="Fasting status"):
        _review(anesthesia_ready, row)


def test_documentation_requires_valid_values_and_explicit_notes(anesthesia_ready):
    for changes in (
        {"asa_class": 0}, {"asa_class": True}, {"technique": "unsupported"},
        {"airway_assessment": "not_checked"}, {"allergy_status": "unlisted"},
        {"allergy_status": "known", "allergy_notes": None},
        {"fasting_confirmed": None}, {"airway_plan": "  "},
        {"contingency_plan": ""},
    ):
        with pytest.raises(ValidationError):
            _record(anesthesia_ready, **changes)
    assert db.session.query(AnesthesiaPreoperativeAssessment).count() == 0


def test_attestation_without_baseline_is_denied(anesthesia_ready):
    row = _record(anesthesia_ready)
    with pytest.raises(ConflictError, match="baseline required"):
        _review(anesthesia_ready, row)


def test_baseline_is_timestamped_and_sequence_versioned(anesthesia_ready):
    row = _record(anesthesia_ready)
    first = _baseline(anesthesia_ready, row)
    second = _baseline(anesthesia_ready, row, heart_rate_bpm=81)
    assert (first.sequence, second.sequence) == (1, 2)
    assert row.version == 3
    assert first.evidence_sha256 != second.evidence_sha256
    assert len(first.evidence_sha256) == 64
    assert svc.get_preanesthesia_assessment(assessment_id=row.id, clinic_id=row.clinic_id).id == row.id
    assert any(e["entity_type"] == "AnesthesiaBaselineObservation" for e in anesthesia_ready["events"])


def test_baseline_invalid_values_and_future_time_denied(anesthesia_ready):
    row = _record(anesthesia_ready)
    for changes in (
        {"heart_rate_bpm": True}, {"heart_rate_bpm": 0},
        {"systolic_bp": 70, "diastolic_bp": 100},
        {"spo2_percent": 101}, {"respiratory_rate": 0},
        {"temperature_tenths_c": 500}, {"oxygen_delivery": "unlisted"},
        {"observed_at": datetime.now(timezone.utc) + timedelta(days=1)},
        {"observed_at": datetime.now(timezone.utc).replace(tzinfo=None)},
    ):
        with pytest.raises(ValidationError):
            _baseline(anesthesia_ready, row, **changes)
    assert db.session.query(AnesthesiaBaselineObservation).count() == 0


def test_baseline_and_review_stale_versions_denied(anesthesia_ready):
    row = _record(anesthesia_ready)
    _baseline(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="version changed"):
        _baseline(anesthesia_ready, row, expected_version=1)
    with pytest.raises(ConflictError, match="version changed"):
        _review(anesthesia_ready, row, expected_version=1)
    assert row.status == "draft" and row.version == 2


def test_independent_actor_cannot_append_or_attest(anesthesia_ready):
    row = _record(anesthesia_ready)
    with pytest.raises(ConflictError, match="assigned anesthetist"):
        _baseline(anesthesia_ready, row, actor_staff_id=anesthesia_ready["doctor"].id)
    _baseline(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="Assigned anesthetist"):
        _review(anesthesia_ready, row, actor_staff_id=anesthesia_ready["doctor"].id)


def test_attested_review_has_digest_and_no_execution_transition(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    _baseline(ctx, row)
    reviewed = _review(ctx, row)
    assert reviewed.status == "attested" and reviewed.version == 3
    assert reviewed.attested_by_staff_id == ctx["anesthetist"].id
    assert reviewed.attested_at is not None
    assert len(reviewed.evidence_sha256) == 64
    assert svc.assert_current_preanesthesia_review(
        surgical_case_id=ctx["case"].id, clinic_id=ctx["clinic"].id,
    ).id == row.id
    assert ctx["case"].status == "planned" and ctx["order"].status == "active"
    assert any(e["action"].value == "status_change" and
               e["entity_type"] == "AnesthesiaPreoperativeAssessment"
               for e in ctx["events"])


def test_attested_review_is_not_reopened_or_modified(anesthesia_ready):
    row = _record(anesthesia_ready)
    _baseline(anesthesia_ready, row)
    _review(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="already attested"):
        _review(anesthesia_ready, row)
    with pytest.raises(ConflictError, match="cannot be modified"):
        _baseline(anesthesia_ready, row)


def test_baseline_fingerprint_detects_tampering(anesthesia_ready):
    row = _record(anesthesia_ready)
    observation = _baseline(anesthesia_ready, row)
    observation.heart_rate_bpm += 1
    db.session.commit()
    with pytest.raises(ConflictError, match="baseline fingerprint mismatch"):
        svc.get_preanesthesia_assessment(assessment_id=row.id, clinic_id=row.clinic_id)


def test_attestation_fingerprint_detects_tampering(anesthesia_ready):
    row = _record(anesthesia_ready)
    _baseline(anesthesia_ready, row)
    _review(anesthesia_ready, row)
    row.plan_summary = "Altered without new attestation"
    db.session.commit()
    with pytest.raises(ConflictError, match="attestation fingerprint mismatch"):
        svc.get_preanesthesia_assessment(assessment_id=row.id, clinic_id=row.clinic_id)


def test_booking_reschedule_invalidates_draft_review(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    _baseline(ctx, row)
    start = datetime(2030, 1, 11, 14, tzinfo=timezone.utc)
    booking.reschedule_theatre(
        booking_id=ctx["booking"].id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id, expected_version=1,
        starts_at=start, ends_at=start + timedelta(hours=2),
    )
    with pytest.raises(ConflictError, match="booking or order changed"):
        _review(ctx, row)


def test_consent_withdrawal_invalidates_review(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    _baseline(ctx, row)
    authorization.withdraw_surgical_consent(
        consent_id=ctx["consent"].id, clinic_id=ctx["clinic"].id,
        doctor_staff_id=ctx["reviewer"].id, expected_version=2,
        reason="Patient withdrew consent",
    )
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _review(ctx, row)


def test_credential_revocation_invalidates_attested_review(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    _baseline(ctx, row)
    _review(ctx, row)
    privilege = db.session.execute(db.select(authorization.SurgicalProfessionalPrivilege).where(
        authorization.SurgicalProfessionalPrivilege.staff_id == ctx["anesthetist"].id,
        authorization.SurgicalProfessionalPrivilege.privilege_kind == "anesthetist",
    )).scalar_one()
    authorization.revoke_surgical_privilege(
        privilege_id=privilege.id, clinic_id=ctx["clinic"].id,
        admin_staff_id=ctx["admin"].id, expected_version=1,
        reason="Professional privilege revoked",
    )
    with pytest.raises(ConflictError, match="anesthetist privilege"):
        svc.assert_current_preanesthesia_review(
            surgical_case_id=ctx["case"].id, clinic_id=ctx["clinic"].id,
        )


def test_stale_baseline_cannot_authorize_current_review(anesthesia_ready):
    row = _record(anesthesia_ready)
    _baseline(anesthesia_ready, row, observed_at=datetime.now(timezone.utc) - timedelta(hours=36))
    with pytest.raises(ConflictError, match="baseline observation is stale"):
        _review(anesthesia_ready, row)


def test_tenant_isolation_and_no_public_operative_route(anesthesia_ready, make_clinic, app):
    ctx = anesthesia_ready
    row = _record(ctx)
    other = make_clinic(name="Unrelated Anesthesia Tenant")
    with pytest.raises(NotFoundError):
        svc.get_preanesthesia_assessment(assessment_id=row.id, clinic_id=other.id)
    with pytest.raises(NotFoundError):
        _baseline(ctx, row, clinic_id=other.id)
    # S11 permits exactly two authorized GET-only documentation endpoints.
    # No Surgery mutation or operative-execution routes may be registered.
    surgery_routes = {
        (rule.rule, frozenset(rule.methods - {"HEAD", "OPTIONS"}))
        for rule in app.url_map.iter_rules()
        if "/surgery" in rule.rule
    }
    assert surgery_routes == {
        ("/api/v1/dashboard/surgery", frozenset({"GET"})),
        ("/api/v1/dashboard/surgery/cases/<int:surgical_case_id>", frozenset({"GET"})),
    }
    assert ctx["case"].status == "planned"


def test_database_rejects_forged_review_without_attestation_evidence(anesthesia_ready):
    row = _record(anesthesia_ready)
    row.status = "attested"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
