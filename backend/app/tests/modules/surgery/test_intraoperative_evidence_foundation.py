"""CE1-S06 only: source-linked, non-executable event and count evidence.

Run THIS test module only; all prior Surgery/ProcedureOrder suites are deferred.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.surgery.models.intraoperative_evidence_model import (
    SurgicalEvidenceJournal, SurgicalEvidenceEvent, SurgicalInstrumentCount,
)
from app.modules.surgery.services import intraoperative_evidence_service as svc
from app.modules.surgery.services import surgical_checklist_service as checklist
from app.modules.surgery.services import surgical_authorization_service as auth
from app.modules.surgery.services import theatre_booking_service as booking
from app.modules.surgery.services import anesthesia_service as anesthesia
from app.tests.modules.surgery.test_anesthesia_foundation import (
    anesthesia_ready, _record, _baseline, _review,
)
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_surgical_authorization import scenario


def _now():
    return datetime.now(timezone.utc) - timedelta(minutes=2)


def _check(ctx, phase):
    return checklist.attest_checklist_phase(
        clinic_id=ctx["clinic"].id, surgical_case_id=ctx["case"].id,
        actor_staff_id=(ctx["doctor"] if phase == "time_out" else ctx["nurse"]).id,
        surgeon_staff_id=ctx["doctor"].id,
        anesthetist_staff_id=ctx["anesthetist"].id,
        phase=phase,
        answers={k: True for k in checklist.REQUIRED_ANSWERS[phase]},
    )


@pytest.fixture
def ready_s06(anesthesia_ready, monkeypatch):
    ctx = anesthesia_ready
    monkeypatch.setattr(svc, "create_audit_log", lambda **kw: ctx["events"].append(kw))
    ctx["sign_in"] = _check(ctx, "sign_in")
    ctx["time_out"] = _check(ctx, "time_out")
    assessment = _record(ctx)
    _baseline(ctx, assessment)
    ctx["review"] = _review(ctx, assessment)
    return ctx


def _journal(ctx, **kw):
    params = dict(
        clinic_id=ctx["clinic"].id, surgical_case_id=ctx["case"].id,
        actor_staff_id=ctx["doctor"].id,
        surgeon_staff_id=ctx["doctor"].id,
        anesthetist_staff_id=ctx["anesthetist"].id,
    )
    params.update(kw)
    return svc.create_surgical_evidence_journal(**params)


def _event(ctx, journal, **kw):
    params = dict(
        journal_id=journal.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["nurse"].id,
        expected_version=journal.version, category="safety_observation",
        observed_at=_now(), note="Documented test safety observation only",
    )
    params.update(kw)
    return svc.append_surgical_evidence_event(**params)


def _count(ctx, journal, kind="instruments", phase="baseline", number=5, **kw):
    params = dict(
        journal_id=journal.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["nurse"].id,
        expected_version=journal.version,
        item_kind=kind, phase=phase, observed_count=number,
        observed_at=_now(), note="Explicit test count evidence",
    )
    params.update(kw)
    return svc.record_surgical_instrument_count(**params)


def _all_counts(ctx, journal, mismatched=None):
    for kind, number in (("instruments", 5), ("sponges", 12), ("sharps", 3)):
        _count(ctx, journal, kind, "baseline", number)
    for kind, number in (("instruments", 5), ("sponges", 12), ("sharps", 3)):
        _count(ctx, journal, kind, "final", number + (1 if mismatched == kind else 0))


def _seal(ctx, journal, **kw):
    params = dict(
        journal_id=journal.id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id,
        expected_version=journal.version,
        summary="Non-executable surgical documentation packet reconciled",
    )
    params.update(kw)
    return svc.seal_surgical_evidence_journal(**params)


def test_created_journal_anchors_all_prior_evidence_without_execution(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    assert journal.id and journal.status == "draft" and journal.version == 1
    assert journal.surgical_case_id == ctx["case"].id
    assert journal.patient_id == ctx["patient"].id
    assert journal.procedure_order_id == ctx["order"].id
    assert journal.theatre_booking_id == ctx["booking"].id
    assert journal.sign_in_attestation_id == ctx["sign_in"].id
    assert journal.time_out_attestation_id == ctx["time_out"].id
    assert journal.anesthesia_assessment_id == ctx["review"].id
    assert journal.sign_in_sha256 == ctx["sign_in"].evidence_sha256
    assert journal.anesthesia_sha256 == ctx["review"].evidence_sha256
    assert journal.counting_nurse_staff_id == ctx["nurse"].id
    assert len(journal.source_sha256) == 64
    assert svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id).id == journal.id
    assert ctx["case"].status == "planned" and ctx["order"].status == "active"
    assert any(e["entity_type"] == "SurgicalEvidenceJournal" for e in ctx["events"])


def test_no_review_denies_journal_even_when_checklist_exists(anesthesia_ready):
    ctx = anesthesia_ready
    _check(ctx, "sign_in")
    _check(ctx, "time_out")
    with pytest.raises(ConflictError, match="preoperative review"):
        _journal(ctx)
    assert db.session.query(SurgicalEvidenceJournal).count() == 0


def test_missing_sign_in_denies_journal(anesthesia_ready):
    ctx = anesthesia_ready
    row = _record(ctx)
    _baseline(ctx, row)
    _review(ctx, row)
    with pytest.raises(NotFoundError, match="checklist phase"):
        _journal(ctx)


def test_missing_time_out_denies_journal(anesthesia_ready):
    ctx = anesthesia_ready
    _check(ctx, "sign_in")
    row = _record(ctx)
    _baseline(ctx, row)
    _review(ctx, row)
    with pytest.raises(NotFoundError, match="checklist phase"):
        _journal(ctx)


def test_only_actual_privileged_surgeon_creates_journal(ready_s06):
    ctx = ready_s06
    with pytest.raises(ConflictError, match="assigned privileged surgeon"):
        _journal(ctx, actor_staff_id=ctx["reviewer"].id)
    with pytest.raises(ValidationError, match="doctor"):
        _journal(ctx, actor_staff_id=ctx["nurse"].id)


def test_duplicate_case_journal_denied(ready_s06):
    ctx = ready_s06
    _journal(ctx)
    with pytest.raises(ConflictError, match="already exists"):
        _journal(ctx)
    assert db.session.query(SurgicalEvidenceJournal).count() == 1


def test_event_appends_are_ordered_versioned_and_audited(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    a = _event(ctx, journal)
    b = _event(ctx, journal, category="team_joined", related_staff_id=ctx["doctor"].id)
    assert (a.sequence, b.sequence) == (1, 2)
    assert journal.version == 3
    assert a.evidence_sha256 != b.evidence_sha256
    assert len(a.evidence_sha256) == 64
    assert db.session.query(SurgicalEvidenceEvent).count() == 2
    assert any(x["entity_type"] == "SurgicalEvidenceEvent" for x in ctx["events"])


def test_unassigned_same_clinic_doctor_cannot_write_events(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    with pytest.raises(ConflictError, match="assigned surgical team"):
        _event(ctx, journal, actor_staff_id=ctx["reviewer"].id)
    assert db.session.query(SurgicalEvidenceEvent).count() == 0


def test_related_staff_cannot_be_foreign_or_inactive(ready_s06, make_clinic, make_staff):
    ctx = ready_s06
    journal = _journal(ctx)
    other = make_clinic(name="S06 unrelated clinic")
    outsider = make_staff(other, role=Role.NURSE)
    with pytest.raises(NotFoundError, match="team staff"):
        _event(ctx, journal, category="team_joined", related_staff_id=outsider.id)
    with pytest.raises(ValidationError, match="requires related_staff_id"):
        _event(ctx, journal, category="team_joined")


def test_invalid_event_types_times_and_notes_denied(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    for kw in (
        {"category": "incision"}, {"category": "anesthesia_induction"},
        {"category": "drug_administration"}, {"category": "operation_completed"},
        {"note": " "}, {"observed_at": datetime.now(timezone.utc) + timedelta(days=1)},
        {"observed_at": datetime.now(timezone.utc).replace(tzinfo=None)},
    ):
        with pytest.raises(ValidationError):
            _event(ctx, journal, **kw)
    assert db.session.query(SurgicalEvidenceEvent).count() == 0


def test_stale_version_denies_event_and_count(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    with pytest.raises(ConflictError, match="version changed"):
        _event(ctx, journal, expected_version=1)
    with pytest.raises(ConflictError, match="version changed"):
        _count(ctx, journal, expected_version=1)
    assert journal.version == 2


def test_event_evidence_tampering_is_detected(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    event = _event(ctx, journal)
    event.note = "Silently altered"
    db.session.commit()
    with pytest.raises(ConflictError, match="event evidence"):
        svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id)


def test_source_evidence_tampering_is_detected(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    journal.procedure_order_version += 1
    db.session.commit()
    with pytest.raises(ConflictError, match="source fingerprint"):
        svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id)


def test_only_sign_in_nurse_can_record_counts(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    with pytest.raises(ConflictError, match="Sign-In nurse"):
        _count(ctx, journal, actor_staff_id=ctx["doctor"].id)
    with pytest.raises(ConflictError, match="Sign-In nurse"):
        _count(ctx, journal, actor_staff_id=ctx["anesthetist"].id)


def test_final_count_requires_matching_baseline(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    with pytest.raises(ConflictError, match="Baseline count"):
        _count(ctx, journal, phase="final")


def test_duplicate_count_kind_and_phase_denied(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _count(ctx, journal)
    with pytest.raises(ConflictError, match="already recorded"):
        _count(ctx, journal)
    assert db.session.query(SurgicalInstrumentCount).count() == 1


def test_invalid_count_kind_phase_negative_and_time_denied(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    for kw in (
        {"item_kind": "implant"}, {"phase": "unverified"},
        {"observed_count": -1}, {"observed_count": True},
        {"observed_at": datetime.now(timezone.utc) + timedelta(days=1)},
    ):
        with pytest.raises(ValidationError):
            _count(ctx, journal, **kw)
    assert db.session.query(SurgicalInstrumentCount).count() == 0


def test_missing_counts_prevent_seal(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _count(ctx, journal)
    with pytest.raises(ConflictError, match="All baseline and final"):
        _seal(ctx, journal)
    assert journal.status == "draft"


def test_no_events_prevent_seal_even_with_complete_counts(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _all_counts(ctx, journal)
    with pytest.raises(ConflictError, match="events required"):
        _seal(ctx, journal)
    assert journal.status == "draft"


def test_count_discrepancy_persists_and_blocks_seal(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _all_counts(ctx, journal, mismatched="sponges")
    with pytest.raises(ConflictError, match="Unreconciled"):
        _seal(ctx, journal)
    assert journal.status == "draft" and db.session.query(SurgicalInstrumentCount).count() == 6


def test_only_privileged_surgeon_may_seal(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _all_counts(ctx, journal)
    with pytest.raises(ConflictError, match="privileged surgeon"):
        _seal(ctx, journal, actor_staff_id=ctx["reviewer"].id)
    assert journal.status == "draft"


def test_seal_success_is_not_operation_completed_and_signout_stays_closed(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal, category="team_joined", related_staff_id=ctx["doctor"].id)
    _all_counts(ctx, journal)
    prior_version = journal.version
    result = _seal(ctx, journal)
    assert result.status == "sealed" and result.version == prior_version + 1
    assert result.sealed_by_staff_id == ctx["doctor"].id
    assert len(result.seal_sha256) == 64
    assert svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id).id == journal.id
    assert ctx["case"].status == "planned" and ctx["order"].status == "active"
    with pytest.raises(ConflictError, match="Sign-Out requires future"):
        _check(ctx, "sign_out")


def test_sealed_documentation_cannot_be_mutated_or_resealed(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _all_counts(ctx, journal)
    _seal(ctx, journal)
    for action in (
        lambda: _event(ctx, journal),
        lambda: _count(ctx, journal),
        lambda: _seal(ctx, journal),
    ):
        with pytest.raises(ConflictError, match="Sealed surgical documentation"):
            action()


def test_count_evidence_tampering_is_detected(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    count = _count(ctx, journal)
    count.observed_count += 1
    db.session.commit()
    with pytest.raises(ConflictError, match="instrument count evidence"):
        svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id)


def test_seal_fingerprint_detects_tampering(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _all_counts(ctx, journal)
    _seal(ctx, journal)
    journal.seal_summary = "Falsified seal summary"
    db.session.commit()
    with pytest.raises(ConflictError, match="Sealed surgical journal fingerprint"):
        svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=ctx["clinic"].id)


def test_consent_withdrawal_blocks_append_after_journal_creation(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    auth.withdraw_surgical_consent(
        consent_id=ctx["consent"].id, clinic_id=ctx["clinic"].id,
        doctor_staff_id=ctx["reviewer"].id, expected_version=2,
        reason="Test withdrawal after documentation opened",
    )
    with pytest.raises(ConflictError, match="verified surgical consent"):
        _event(ctx, journal)
    assert journal.version == 1


def test_booking_reschedule_blocks_later_count_entry(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    start = datetime(2030, 1, 12, 9, tzinfo=timezone.utc)
    booking.reschedule_theatre(
        booking_id=ctx["booking"].id, clinic_id=ctx["clinic"].id,
        actor_staff_id=ctx["doctor"].id, expected_version=1,
        starts_at=start, ends_at=start + timedelta(hours=2),
    )
    with pytest.raises(ConflictError, match="booking or order changed"):
        _count(ctx, journal)


def test_privilege_revocation_blocks_further_documentation(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    privilege = db.session.execute(db.select(auth.SurgicalProfessionalPrivilege).where(
        auth.SurgicalProfessionalPrivilege.staff_id == ctx["anesthetist"].id,
        auth.SurgicalProfessionalPrivilege.privilege_kind == "anesthetist",
    )).scalar_one()
    auth.revoke_surgical_privilege(
        privilege_id=privilege.id, clinic_id=ctx["clinic"].id,
        admin_staff_id=ctx["admin"].id, expected_version=1,
        reason="Test credential suspension",
    )
    with pytest.raises(ConflictError, match="anesthetist privilege"):
        _event(ctx, journal)


def test_cross_clinic_journal_access_denied(ready_s06, make_clinic):
    ctx = ready_s06
    journal = _journal(ctx)
    other = make_clinic(name="Unrelated S06 clinic")
    with pytest.raises(NotFoundError):
        svc.get_surgical_evidence_journal(journal_id=journal.id, clinic_id=other.id)
    with pytest.raises(NotFoundError):
        _event(ctx, journal, clinic_id=other.id)


def test_model_constraint_rejects_forged_sealed_without_evidence(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    journal.status = "sealed"
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_no_surgery_public_route_and_no_operative_transition(app, ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
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
    assert ctx["order"].status == "active"


def test_database_sealed_row_requires_nonnull_fingerprint(ready_s06):
    ctx = ready_s06
    journal = _journal(ctx)
    _event(ctx, journal)
    _all_counts(ctx, journal)
    _seal(ctx, journal)
    journal.seal_sha256 = None
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
