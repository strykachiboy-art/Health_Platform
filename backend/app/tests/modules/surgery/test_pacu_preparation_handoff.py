"""CE1-S08 ONLY: PACU preparation, preliminary observations, nursing content review.

No older test module is selected. Import their FIXTURES, not their tests.
PACU packet review never mutates admission, NursingHandoff, care assignment,
theatre booking, SurgicalCase, ProcedureOrder, operative completion or Sign-Out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.surgery.models.pacu_preparation_model import (
    SurgicalPacuPreparation, SurgicalPacuObservation,
)
from app.modules.surgery.services import pacu_preparation_service as svc
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import (
    ready_s06, _journal, _event, _all_counts, _seal,
)


def _utc():
    return datetime.now(timezone.utc) - timedelta(minutes=3)


@pytest.fixture
def workspace(ready_s06, monkeypatch, make_staff):
    ctx = ready_s06
    monkeypatch.setattr(svc, 'create_audit_log', lambda **kw: ctx['events'].append(kw))
    ctx['receiving'] = make_staff(ctx['clinic'], role=Role.NURSE)
    ctx['journal'] = _journal(ctx)
    _event(ctx, ctx['journal'])
    _all_counts(ctx, ctx['journal'])
    _seal(ctx, ctx['journal'])
    return ctx


def _packet(ctx, **kw):
    params = dict(clinic_id=ctx['clinic'].id, journal_id=ctx['journal'].id,
        actor_staff_id=ctx['nurse'].id,receiving_nurse_staff_id=ctx['receiving'].id,
        situation='Safety briefing before PACU disposition, not transfer authority',
        background='Documented preoperative risk, booking and consent reviewed',
        assessment='Preliminary clinical assessment; operation completion unverified',
        recommendation='Review with surgeon before any transfer decision',
        safety_concerns='Airway and bleeding require nursing escalation if present',
        pending_actions='Confirm signed operative record and independent transfer authorization',
        airway_plan='Direct airway reassessment and escalation plan',
        analgesia_plan='Document pain response without medication administration',
        escalation_plan='Contact responsible surgeon and anesthetist for deterioration')
    params.update(kw)
    return svc.create_pacu_preparation(**params)


def _observe(ctx, packet, **kw):
    params=dict(preparation_id=packet.id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['nurse'].id,expected_version=packet.version,
        observed_at=_utc(),heart_rate_bpm=78,systolic_bp=118,
        diastolic_bp=76,spo2_percent=98,respiratory_rate=16,
        temperature_tenths_c=370,pain_score=2,consciousness='alert',
        airway_patent=True,bleeding_concern=False,escalation_communicated=False,
        note='Direct preliminary observation, not operative completion')
    params.update(kw)
    return svc.add_pacu_observation(**params)


def _prepare(ctx, packet, **kw):
    params=dict(preparation_id=packet.id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['nurse'].id,expected_version=packet.version)
    params.update(kw)
    return svc.prepare_pacu_handoff(**params)


def _review(ctx, packet, **kw):
    params=dict(preparation_id=packet.id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['receiving'].id,expected_version=packet.version)
    params.update(kw)
    return svc.review_pacu_handoff(**params)


def test_packet_draft_is_patient_scoped_and_non_executable(workspace):
    ctx=workspace
    p=_packet(ctx)
    assert p.status == 'draft' and p.version == 1 and p.id is not None
    assert p.surgical_case_id == ctx['case'].id and p.patient_id == ctx['patient'].id
    assert p.admission_id == ctx['admission'].id
    assert p.journal_seal_sha256 == ctx['journal'].seal_sha256
    assert len(p.source_sha256) == 64 and len(p.content_sha256) == 64
    assert ctx['case'].status == 'planned' and ctx['order'].status == 'active'
    assert ctx['admission'].status == AdmissionStatus.ADMITTED
    assert db.session.query(NursingHandoff).count() == 0
    assert any(x['entity_type'] == 'SurgicalPacuPreparation' for x in ctx['events'])


def test_unsealed_s06_journal_cannot_create_packet(ready_s06, make_staff):
    ctx=ready_s06
    ctx['receiving']=make_staff(ctx['clinic'],role=Role.NURSE)
    ctx['journal']=_journal(ctx)
    with pytest.raises(ConflictError, match='Sealed'):
        _packet(ctx)


def test_missing_journal_denies_packet(workspace):
    with pytest.raises(NotFoundError,match='journal'):
        _packet(workspace,journal_id=999999)


def test_foreign_clinic_cannot_create_packet(workspace, make_clinic):
    other=make_clinic(name='S08 foreign')
    with pytest.raises(NotFoundError,match='journal'):
        _packet(workspace,clinic_id=other.id)


def test_only_outgoing_count_nurse_can_create(workspace):
    with pytest.raises(ConflictError,match='counting nurse'):
        _packet(workspace,actor_staff_id=workspace['receiving'].id)


def test_receiving_nurse_must_be_active_and_in_clinic(workspace, make_staff, make_clinic):
    other=make_clinic(name='S08 foreign nurse')
    foreign=make_staff(other,role=Role.NURSE)
    with pytest.raises(NotFoundError,match='clinic'):
        _packet(workspace,receiving_nurse_staff_id=foreign.id)
    inactive=make_staff(workspace['clinic'],role=Role.NURSE,status=StaffStatus.SUSPENDED)
    with pytest.raises(ValidationError,match='Active clinic nurse'):
        _packet(workspace,receiving_nurse_staff_id=inactive.id)


def test_non_nurse_receiving_rejected(workspace):
    with pytest.raises(ValidationError,match='Active clinic nurse'):
        _packet(workspace,receiving_nurse_staff_id=workspace['doctor'].id)


def test_same_outgoing_receiving_denied(workspace):
    with pytest.raises(ValidationError,match='Distinct'):
        _packet(workspace,receiving_nurse_staff_id=workspace['nurse'].id)


def test_duplicate_packet_denied(workspace):
    _packet(workspace)
    with pytest.raises(ConflictError,match='already exists'):
        _packet(workspace)
    assert db.session.query(SurgicalPacuPreparation).count() == 1


def test_blank_or_oversize_sbar_denied(workspace):
    with pytest.raises(ValidationError,match='blank'):
        _packet(workspace,situation='  ')
    with pytest.raises(ValidationError):
        _packet(workspace,analgesia_plan='x'*1201)


def test_observation_is_timestamped_versioned_and_audited(workspace):
    p=_packet(workspace)
    row=_observe(workspace,p)
    assert row.sequence == 1 and row.preparation_id == p.id and p.version == 2
    assert len(row.evidence_sha256)==64
    assert len(svc.list_pacu_observations(preparation_id=p.id,clinic_id=workspace['clinic'].id))==1
    assert any(x['entity_type']=='SurgicalPacuObservation' for x in workspace['events'])
    assert workspace['case'].status == 'planned'


def test_named_receiving_nurse_may_observe_without_transfer(workspace):
    p=_packet(workspace)
    row=_observe(workspace,p,actor_staff_id=workspace['receiving'].id)
    assert row.observed_by_staff_id==workspace['receiving'].id
    assert db.session.query(AdmissionCareAssignment).count() == 0


def test_unassigned_nurse_cannot_observe(workspace, make_staff):
    p=_packet(workspace)
    stranger=make_staff(workspace['clinic'],role=Role.NURSE)
    with pytest.raises(ConflictError,match='named nurses'):
        _observe(workspace,p,actor_staff_id=stranger.id)
    assert db.session.query(SurgicalPacuObservation).count()==0


def test_invalid_vital_ranges_denied(workspace):
    p=_packet(workspace)
    for bad in ({'spo2_percent':101},{'systolic_bp':0},{'diastolic_bp':120},
                {'heart_rate_bpm':True},{'respiratory_rate':0},{'pain_score':11},
                {'temperature_tenths_c':290}):
        with pytest.raises(ValidationError):
            _observe(workspace,p,**bad)
    assert db.session.query(SurgicalPacuObservation).count()==0


def test_invalid_consciousness_booleans_time_note_denied(workspace):
    p=_packet(workspace)
    for bad in ({'consciousness':'sedated'},{'airway_patent':1},
                {'bleeding_concern':'false'},{'escalation_communicated':None},
                {'observed_at':datetime.now(timezone.utc)+timedelta(days=1)},
                {'observed_at':datetime.now(timezone.utc).replace(tzinfo=None)},
                {'note':'  '}):
        with pytest.raises(ValidationError):
            _observe(workspace,p,**bad)
    assert db.session.query(SurgicalPacuObservation).count()==0


def test_multiple_observations_preserve_order(workspace):
    p=_packet(workspace)
    a=_observe(workspace,p)
    b=_observe(workspace,p,pain_score=3)
    assert (a.sequence,b.sequence)==(1,2) and p.version==3
    assert a.evidence_sha256 != b.evidence_sha256


def test_stale_packet_version_rejected(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    with pytest.raises(ConflictError,match='version'):
        _observe(workspace,p,expected_version=1)
    with pytest.raises(ConflictError,match='version'):
        _prepare(workspace,p,expected_version=1)


def test_no_observations_prevents_preparation(workspace):
    p=_packet(workspace)
    with pytest.raises(ConflictError,match='observation'):
        _prepare(workspace,p)


def test_unsafe_airway_requires_documented_escalation(workspace):
    p=_packet(workspace)
    _observe(workspace,p,airway_patent=False)
    with pytest.raises(ConflictError,match='escalation'):
        _prepare(workspace,p)


def test_bleeding_concern_requires_documented_escalation(workspace):
    p=_packet(workspace)
    _observe(workspace,p,bleeding_concern=True)
    with pytest.raises(ConflictError,match='escalation'):
        _prepare(workspace,p)


def test_explicit_escalation_allows_content_preparation_not_transfer(workspace):
    p=_packet(workspace)
    _observe(workspace,p,airway_patent=False,bleeding_concern=True,escalation_communicated=True)
    _prepare(workspace,p)
    assert p.status=='prepared' and p.prepared_by_staff_id==workspace['nurse'].id
    assert len(p.prepared_sha256)==64
    assert workspace['admission'].status==AdmissionStatus.ADMITTED
    assert workspace['case'].status=='planned'


def test_only_outgoing_nurse_may_prepare(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    with pytest.raises(ConflictError,match='outgoing'):
        _prepare(workspace,p,actor_staff_id=workspace['receiving'].id)
    assert p.status=='draft'


def test_review_requires_prepared_packet(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    with pytest.raises(ConflictError,match='Prepared'):
        _review(workspace,p)


def test_review_only_designated_receiving_nurse(workspace,make_staff):
    p=_packet(workspace)
    _observe(workspace,p)
    _prepare(workspace,p)
    stranger=make_staff(workspace['clinic'],role=Role.NURSE)
    with pytest.raises(ConflictError,match='designated'):
        _review(workspace,p,actor_staff_id=stranger.id)


def test_review_does_not_transfer_primary_nursing_or_complete_operation(workspace):
    ctx=workspace
    p=_packet(ctx)
    _observe(ctx,p)
    _prepare(ctx,p)
    _review(ctx,p)
    assert p.status=='reviewed' and len(p.review_sha256)==64
    assert p.reviewed_by_staff_id==ctx['receiving'].id
    assert ctx['journal'].status=='sealed'
    assert ctx['case'].status=='planned' and ctx['order'].status=='active'
    assert ctx['admission'].status==AdmissionStatus.ADMITTED
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0
    assert any(x['entity_type']=='SurgicalPacuPreparation' and 'reviewed' in str(x).lower()
               for x in ctx['events'])


def test_no_mutation_after_prepared_or_reviewed(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    _prepare(workspace,p)
    with pytest.raises(ConflictError,match='immutable'):
        _observe(workspace,p)
    _review(workspace,p)
    with pytest.raises(ConflictError,match='immutable'):
        _observe(workspace,p)
    with pytest.raises(ConflictError,match='Prepared'):
        _review(workspace,p)


def test_observation_tamper_detection(workspace):
    p=_packet(workspace)
    o=_observe(workspace,p)
    o.pain_score=8
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.list_pacu_observations(preparation_id=p.id,clinic_id=workspace['clinic'].id)


def test_content_tamper_detection(workspace):
    p=_packet(workspace)
    p.recommendation='Modified without attestation'
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_pacu_preparation(preparation_id=p.id,clinic_id=workspace['clinic'].id)


def test_review_tamper_detection(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    _prepare(workspace,p)
    _review(workspace,p)
    p.reviewed_by_staff_id=workspace['doctor'].id
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_pacu_preparation(preparation_id=p.id,clinic_id=workspace['clinic'].id)


def test_source_booking_changed_denies_new_observation(workspace):
    p=_packet(workspace)
    workspace['journal'].seal_sha256='f'*64
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        _observe(workspace,p)


def test_cross_clinic_cannot_read_review_or_append(workspace,make_clinic):
    p=_packet(workspace)
    other=make_clinic(name='S08 another tenant')
    with pytest.raises(NotFoundError,match='clinic'):
        svc.get_pacu_preparation(preparation_id=p.id,clinic_id=other.id)
    with pytest.raises(NotFoundError,match='clinic'):
        svc.list_pacu_observations(preparation_id=p.id,clinic_id=other.id)
    with pytest.raises(NotFoundError,match='clinic'):
        _observe(workspace,p,clinic_id=other.id)


def test_discharged_admission_blocks_preparation(workspace):
    workspace['admission'].status=AdmissionStatus.DISCHARGED
    db.session.flush()
    with pytest.raises(ConflictError,match='Active inpatient'):
        _packet(workspace)


def test_revoked_receiving_nurse_blocks_review(workspace):
    p=_packet(workspace)
    _observe(workspace,p)
    _prepare(workspace,p)
    workspace['receiving'].status=StaffStatus.SUSPENDED
    db.session.flush()
    with pytest.raises(ValidationError,match='Active clinic nurse'):
        _review(workspace,p)


def test_database_unique_case_guard(workspace):
    p=_packet(workspace)
    duplicate=SurgicalPacuPreparation(**{
        c.name:getattr(p,c.name) for c in SurgicalPacuPreparation.__table__.columns if c.name != 'id'
    })
    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_no_s08_public_routes_created(workspace):
    from app.core import web_routes
    assert not hasattr(web_routes,'create_pacu_preparation')
    assert not hasattr(web_routes,'review_pacu_handoff')
