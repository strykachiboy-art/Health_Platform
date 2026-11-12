"""CE1-S04 ONLY: checklist attestation security and phase ordering.

Run just this module; do not rerun S01/S02/S03 or clinical-order regression.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.surgery.models.surgical_checklist_model import SurgicalSafetyChecklistAttestation
from app.modules.surgery.services import surgical_checklist_service as checklist
from app.modules.surgery.services import theatre_booking_service as booking
from app.modules.surgery.services import surgical_authorization_service as authorization
from app.tests.modules.surgery.test_surgical_authorization import scenario, _ready


def _answers(phase: str):
    return {key: True for key in checklist.REQUIRED_ANSWERS[phase]}


@pytest.fixture
def ready(scenario, monkeypatch):
    ctx = scenario
    monkeypatch.setattr(checklist, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    monkeypatch.setattr(booking, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    ctx["consent"] = _ready(ctx)
    ctx["room"] = booking.create_operating_theatre(
        clinic_id=ctx["clinic"].id, actor_staff_id=ctx["admin"].id,
        code="OR-S04", display_name="S04 Test Theatre",
    )
    start = datetime(2030, 1, 11, 9, tzinfo=timezone.utc)
    ctx["booking"] = booking.schedule_theatre(
        clinic_id=ctx["clinic"].id, actor_staff_id=ctx["doctor"].id,
        surgical_case_id=ctx["case"].id, theatre_id=ctx["room"].id,
        starts_at=start, ends_at=start + timedelta(hours=2),
    )
    return ctx


def _attest(ctx, phase="sign_in", *, actor=None, **extra):
    params = dict(
        clinic_id=ctx["clinic"].id, surgical_case_id=ctx["case"].id,
        actor_staff_id=(actor or (ctx["doctor"] if phase == "time_out" else ctx["nurse"])).id,
        surgeon_staff_id=ctx["doctor"].id,
        anesthetist_staff_id=ctx["anesthetist"].id,
        phase=phase, answers=_answers(phase),
    )
    params.update(extra)
    return checklist.attest_checklist_phase(**params)


def test_sign_in_preserves_evidence_and_does_not_start_surgery(ready):
    row = _attest(ready)
    assert row.id and row.phase == "sign_in"
    assert row.procedure_order_id == ready["order"].id
    assert row.procedure_order_version == ready["order"].version
    assert row.theatre_booking_id == ready["booking"].id
    assert row.booking_version == 1
    assert row.surgical_consent_id == ready["consent"].id
    assert row.attested_by_staff_id == ready["nurse"].id
    assert len(row.evidence_sha256) == 64
    assert checklist.get_checklist_phase(
        surgical_case_id=ready["case"].id,
        clinic_id=ready["clinic"].id, phase="sign_in",
    ).id == row.id
    assert ready["case"].status == "planned"
    assert ready["order"].status == "active"
    assert any(x["entity_type"] == "SurgicalSafetyChecklistAttestation" for x in ready["events"])


def test_time_out_requires_sign_in(ready):
    with pytest.raises(ConflictError, match="Sign-In"):
        _attest(ready, "time_out")
    assert db.session.query(SurgicalSafetyChecklistAttestation).count() == 0


def test_time_out_survives_exact_stable_preconditions(ready):
    first = _attest(ready)
    second = _attest(ready, "time_out")
    assert second.id != first.id and second.phase == "time_out"
    assert second.attested_by_staff_id == ready["doctor"].id
    assert first.surgical_consent_id == second.surgical_consent_id
    assert db.session.query(SurgicalSafetyChecklistAttestation).count() == 2
    assert ready["case"].status == "planned"


def test_duplicate_phase_rejected_without_second_row(ready):
    _attest(ready)
    with pytest.raises(ConflictError, match="already attested"):
        _attest(ready)
    assert db.session.query(SurgicalSafetyChecklistAttestation).count() == 1


def test_required_answers_must_be_complete_and_true(ready):
    for invalid in ({}, {"patient_identity_confirmed": True},
                    {**_answers("sign_in"), "invented_check": True}):
        with pytest.raises(ValidationError, match="Exact required"):
            _attest(ready, answers=invalid)
    false_answers = _answers("sign_in")
    false_answers["monitoring_equipment_ready"] = False
    with pytest.raises(ConflictError, match="affirmative"):
        _attest(ready, answers=false_answers)
    assert db.session.query(SurgicalSafetyChecklistAttestation).count() == 0


def test_sign_in_requires_active_nurse(ready):
    with pytest.raises(ValidationError, match="nurse"):
        _attest(ready, actor=ready["doctor"])


def test_time_out_requires_the_actual_privileged_surgeon(ready):
    _attest(ready)
    with pytest.raises(ConflictError, match="privileged surgeon"):
        _attest(ready, "time_out", actor=ready["reviewer"])
    with pytest.raises(ValidationError, match="doctor"):
        _attest(ready, "time_out", actor=ready["nurse"])


def test_missing_verified_consent_blocks_sign_in(scenario, monkeypatch):
    monkeypatch.setattr(checklist, "create_audit_log", lambda **kw: None)
    # There is no theatre booking and no consent: must fail closed.
    with pytest.raises(ConflictError):
        _attest(scenario)


def test_withdrawal_of_consent_blocks_time_out(ready):
    _attest(ready)
    authorization.withdraw_surgical_consent(
        consent_id=ready["consent"].id, clinic_id=ready["clinic"].id,
        doctor_staff_id=ready["reviewer"].id, expected_version=2,
        reason="Patient withdrew after sign-in",
    )
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _attest(ready, "time_out")


def test_privilege_revocation_blocks_time_out(ready):
    _attest(ready)
    privilege = db.session.execute(
        db.select(authorization.SurgicalProfessionalPrivilege).where(
            authorization.SurgicalProfessionalPrivilege.staff_id == ready["doctor"].id,
            authorization.SurgicalProfessionalPrivilege.privilege_kind == "surgeon",
        )
    ).scalar_one()
    authorization.revoke_surgical_privilege(
        privilege_id=privilege.id, clinic_id=ready["clinic"].id,
        admin_staff_id=ready["admin"].id, expected_version=1,
        reason="Privilege withdrawn",
    )
    with pytest.raises(ConflictError, match="surgeon privilege"):
        _attest(ready, "time_out")


def test_rescheduling_invalidates_existing_sign_in(ready):
    _attest(ready)
    start = datetime(2030, 1, 11, 13, tzinfo=timezone.utc)
    booking.reschedule_theatre(
        booking_id=ready["booking"].id, clinic_id=ready["clinic"].id,
        actor_staff_id=ready["doctor"].id, expected_version=1,
        starts_at=start, ends_at=start + timedelta(hours=2),
    )
    with pytest.raises(ConflictError, match="booking or consent changed"):
        _attest(ready, "time_out")


def test_cancelled_booking_blocks_time_out(ready):
    _attest(ready)
    booking.cancel_theatre_booking(
        booking_id=ready["booking"].id, clinic_id=ready["clinic"].id,
        actor_staff_id=ready["doctor"].id, expected_version=1,
        reason="Clinical reschedule",
    )
    with pytest.raises(ConflictError, match="active theatre booking"):
        _attest(ready, "time_out")


def test_cross_clinic_cannot_read_or_attest(ready, make_clinic, make_staff):
    _attest(ready)
    other = make_clinic(name="Foreign Operating Clinic")
    other_nurse = make_staff(other, role=Role.NURSE)
    with pytest.raises(NotFoundError):
        checklist.get_checklist_phase(
            surgical_case_id=ready["case"].id, clinic_id=other.id, phase="sign_in",
        )
    with pytest.raises(NotFoundError):
        _attest(ready, clinic_id=other.id, actor_staff_id=other_nurse.id)


def test_evidence_mutation_detected_by_digest(ready):
    row = _attest(ready)
    row.answers = {**row.answers, "monitoring_equipment_ready": False}
    db.session.commit()
    with pytest.raises(ConflictError, match="fingerprint mismatch"):
        checklist.get_checklist_phase(
            surgical_case_id=ready["case"].id,
            clinic_id=ready["clinic"].id, phase="sign_in",
        )


def test_database_rejects_forged_duplicate_phase(ready):
    row = _attest(ready)
    props = {column.name: getattr(row, column.name)
             for column in SurgicalSafetyChecklistAttestation.__table__.columns
             if column.name != "id"}
    db.session.add(SurgicalSafetyChecklistAttestation(**props))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_sign_out_explicitly_blocked_until_true_execution_evidence(ready):
    _attest(ready)
    _attest(ready, "time_out")
    with pytest.raises(ConflictError, match="future authoritative operative completion"):
        _attest(ready, "sign_out")
    assert db.session.query(SurgicalSafetyChecklistAttestation).count() == 2


def test_no_public_surgery_routes_or_executable_transition(app, ready):
    _attest(ready)
    assert not any("/surgery" in rule.rule for rule in app.url_map.iter_rules())
    assert ready["case"].status == "planned"
    assert ready["order"].status == "active"
