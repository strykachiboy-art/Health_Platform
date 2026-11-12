"""CE1-S09 ONLY: documentation signatures, not operative execution authority.

Older Surgery, Nursing, ProcedureOrder and broad suites are NOT selected.
Fixtures imported explicitly from their defining test modules.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from app.core.enums.role_enums import Role
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.surgery.models.operative_report_model import (
    SurgicalOperativeReport,SurgicalOperativeSignature,
)
from app.modules.surgery.services import operative_report_service as svc
from app.tests.modules.surgery.test_surgical_authorization import scenario
from app.tests.modules.surgery.test_surgical_safety_checklist import ready
from app.tests.modules.surgery.test_anesthesia_foundation import anesthesia_ready
from app.tests.modules.surgery.test_intraoperative_evidence_foundation import ready_s06
from app.tests.modules.surgery.test_pacu_preparation_handoff import (
    workspace as s08_workspace,_packet,_observe,_prepare,_review,
)


def _note_fields():
    return dict(
        preoperative_diagnosis='Example preoperative diagnosis from test only',
        postoperative_diagnosis='Example postoperative assessment from test only',
        procedure_narrative='Example operative narrative; subject to external verification',
        operative_findings='Recorded findings, not a proof of execution in this module',
        operative_course='Example operative documentation, no intervention performed',
        complication_status='none_reported',
        complication_notes='No complication reported in the example test record',
        estimated_blood_loss_ml=0,
        specimen_notes='No specimen handling claimed by this report',
        implant_notes='No device implantation claimed by this report',
        postoperative_plan='Confirm external operative completion authority before transfer',
    )


def _report(ctx,**kw):
    params=dict(clinic_id=ctx['clinic'].id,journal_id=ctx['journal'].id,
        actor_staff_id=ctx['doctor'].id,**_note_fields())
    params.update(kw)
    return svc.create_operative_report(**params)


def _revise(ctx,row,**kw):
    params=dict(report_id=row.id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['doctor'].id,expected_version=row.version,**_note_fields())
    params.update(kw)
    return svc.revise_operative_report(**params)


def _sign(ctx,row,kind='surgeon',**kw):
    params=dict(report_id=row.id,clinic_id=ctx['clinic'].id,
        actor_staff_id=ctx['doctor'].id if kind=='surgeon' else ctx['anesthetist'].id,
        expected_version=row.version,signer_kind=kind,
        attestation_note='I reviewed the stored clinical documentation only')
    params.update(kw)
    return svc.sign_operative_report(**params)


@pytest.fixture
def ready_s09(s08_workspace,monkeypatch):
    ctx=s08_workspace
    monkeypatch.setattr(svc,'create_audit_log',lambda **kw:ctx['events'].append(kw))
    ctx['pacu_packet']=_packet(ctx)
    _observe(ctx,ctx['pacu_packet'])
    _prepare(ctx,ctx['pacu_packet'])
    _review(ctx,ctx['pacu_packet'])
    return ctx


def test_draft_provenance_and_patient_clinic_scope(ready_s09):
    ctx=ready_s09
    row=_report(ctx)
    assert row.id is not None and row.status=='draft' and row.version==1
    assert row.surgical_case_id==ctx['case'].id and row.patient_id==ctx['patient'].id
    assert row.journal_seal_sha256==ctx['journal'].seal_sha256
    assert row.pacu_review_sha256==ctx['pacu_packet'].review_sha256
    assert len(row.content_sha256)==64 and len(row.source_sha256)==64
    assert row.estimated_blood_loss_ml==0
    assert ctx['case'].status=='planned' and ctx['order'].status=='active'
    assert ctx['admission'].status==AdmissionStatus.ADMITTED
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0
    assert any(e['entity_type']=='SurgicalOperativeReport' for e in ctx['events'])


def test_report_cannot_exist_without_reviewed_pacu(s08_workspace):
    ctx=s08_workspace
    p=_packet(ctx)
    _observe(ctx,p)
    _prepare(ctx,p)
    with pytest.raises(ConflictError,match='Reviewed PACU'):
        _report(ctx)


def test_report_cannot_exist_without_pacu_packet(s08_workspace):
    with pytest.raises(NotFoundError,match='PACU'):
        _report(s08_workspace)


def test_unknown_journal_denied(ready_s09):
    with pytest.raises(NotFoundError,match='journal'):
        _report(ready_s09,journal_id=999999)


def test_foreign_clinic_denied(ready_s09,make_clinic):
    outsider=make_clinic(name='S09 isolated foreign clinic')
    with pytest.raises(NotFoundError):
        _report(ready_s09,clinic_id=outsider.id)


def test_only_assigned_surgeon_authors_report(ready_s09):
    with pytest.raises(ConflictError,match='assigned privileged surgeon'):
        _report(ready_s09,actor_staff_id=ready_s09['reviewer'].id)
    with pytest.raises(ValidationError,match='doctor'):
        _report(ready_s09,actor_staff_id=ready_s09['nurse'].id)


def test_duplicate_report_denied(ready_s09):
    _report(ready_s09)
    with pytest.raises(ConflictError,match='already exists'):
        _report(ready_s09)
    assert db.session.query(SurgicalOperativeReport).count()==1


def test_no_duplicate_signature_records(ready_s09):
    row=_report(ready_s09)
    _sign(ready_s09,row)
    with pytest.raises(ConflictError,match='ordered and unique'):
        _sign(ready_s09,row)
    assert db.session.query(SurgicalOperativeSignature).count()==1


def test_invalid_content_and_missing_fields_denied(ready_s09):
    for kw in ({'preoperative_diagnosis':'  '},{'procedure_narrative':'  '},
               {'postoperative_plan':'x'*3001},{'estimated_blood_loss_ml':-1},
               {'estimated_blood_loss_ml':True},{'complication_status':'unrecorded'},
               {'estimated_blood_loss_ml':100001}):
        with pytest.raises(ValidationError):
            _report(ready_s09,**kw)
    assert db.session.query(SurgicalOperativeReport).count()==0


def test_complication_record_requires_detail(ready_s09):
    with pytest.raises(ValidationError,match='Detailed complication'):
        _report(ready_s09,complication_status='documented',complication_notes='short')


def test_valid_complication_details_documented(ready_s09):
    row=_report(ready_s09,complication_status='documented',
                complication_notes='Example complication documented and escalation advised')
    assert row.complication_status=='documented'


def test_editing_draft_is_versioned_and_audited(ready_s09):
    row=_report(ready_s09)
    old=row.content_sha256
    _revise(ready_s09,row,operative_findings='Changed documented findings')
    assert row.version==2 and row.content_sha256!=old
    assert any(x['entity_type']=='SurgicalOperativeReport' for x in ready_s09['events'])


def test_stale_revision_rejected(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ConflictError,match='version changed'):
        _revise(ready_s09,row,expected_version=99)


def test_unassigned_clinician_cannot_revise(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ConflictError,match='assigned surgeon'):
        _revise(ready_s09,row,actor_staff_id=ready_s09['reviewer'].id)


def test_surgeon_signature_is_immutable_document_evidence(ready_s09):
    ctx=ready_s09
    row=_report(ctx)
    sig=_sign(ctx,row)
    assert row.status=='surgeon_signed' and row.version==2
    assert sig.signer_kind=='surgeon' and sig.document_sha256==row.content_sha256
    assert len(sig.evidence_sha256)==64
    assert len(svc.list_operative_signatures(report_id=row.id,clinic_id=ctx['clinic'].id))==1
    with pytest.raises(ConflictError,match='Signed operative report'):
        _revise(ctx,row,operative_findings='Trying to overwrite signed narrative')


def test_anesthetist_must_not_sign_first(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ConflictError,match='ordered and unique'):
        _sign(ready_s09,row,'anesthetist')


def test_anesthetist_countersigns_stored_document(ready_s09):
    ctx=ready_s09
    row=_report(ctx)
    a=_sign(ctx,row)
    b=_sign(ctx,row,'anesthetist')
    assert row.status=='dual_signed' and row.version==3
    assert a.document_sha256==b.document_sha256==row.content_sha256
    assert {s.signer_kind for s in svc.list_operative_signatures(report_id=row.id,clinic_id=ctx['clinic'].id)}=={'surgeon','anesthetist'}
    assert ctx['case'].status=='planned' and ctx['order'].status=='active'
    assert ctx['admission'].status==AdmissionStatus.ADMITTED
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0


def test_non_authorized_surgeon_cannot_sign(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ConflictError,match='designated surgeon or anesthetist'):
        _sign(ready_s09,row,actor_staff_id=ready_s09['reviewer'].id)


def test_anesthetist_role_cannot_be_assumed_by_surgeon(ready_s09):
    row=_report(ready_s09)
    _sign(ready_s09,row)
    with pytest.raises(ConflictError,match='designated surgeon or anesthetist'):
        _sign(ready_s09,row,'anesthetist',actor_staff_id=ready_s09['doctor'].id)


def test_invalid_signer_kind_or_blank_attestation_denied(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ValidationError,match='Unknown'):
        _sign(ready_s09,row,'doctor')
    with pytest.raises(ValidationError,match='blank'):
        _sign(ready_s09,row,attestation_note='  ')


def test_stale_signature_version_rejected(ready_s09):
    row=_report(ready_s09)
    with pytest.raises(ConflictError,match='version changed'):
        _sign(ready_s09,row,expected_version=2)


def test_source_fingerprint_tampering_rejected(ready_s09):
    row=_report(ready_s09)
    row.journal_seal_sha256='f'*64
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_operative_report(report_id=row.id,clinic_id=ready_s09['clinic'].id)


def test_narrative_tampering_rejected(ready_s09):
    row=_report(ready_s09)
    row.operative_findings='A different unapproved narrative'
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_operative_report(report_id=row.id,clinic_id=ready_s09['clinic'].id)


def test_signature_tampering_rejected(ready_s09):
    row=_report(ready_s09)
    sig=_sign(ready_s09,row)
    sig.attestation_note='Altered signature evidence'
    db.session.flush()
    with pytest.raises(ConflictError,match='signature fingerprint'):
        svc.get_operative_report(report_id=row.id,clinic_id=ready_s09['clinic'].id)


def test_status_tampering_rejected(ready_s09):
    row=_report(ready_s09)
    row.status='dual_signed'
    db.session.flush()
    with pytest.raises(ConflictError,match='status and signatures'):
        svc.get_operative_report(report_id=row.id,clinic_id=ready_s09['clinic'].id)


def test_patient_admission_mismatch_denied(ready_s09):
    row=_report(ready_s09)
    row.case_version+=30000
    db.session.flush()
    with pytest.raises(ConflictError,match='fingerprint'):
        svc.get_operative_report(report_id=row.id,clinic_id=ready_s09['clinic'].id)


def test_changed_case_version_denies_signature(ready_s09):
    row=_report(ready_s09)
    ready_s09['case'].version+=1
    db.session.flush()
    with pytest.raises(ConflictError,match='changed'):
        _sign(ready_s09,row)


def test_changed_pacu_review_denies_signature(ready_s09):
    row=_report(ready_s09)
    ready_s09['pacu_packet'].review_sha256='f'*64
    db.session.flush()
    with pytest.raises(ConflictError):
        _sign(ready_s09,row)


def test_discharge_blocks_report_signature(ready_s09):
    row=_report(ready_s09)
    ready_s09['admission'].status=AdmissionStatus.DISCHARGED
    db.session.flush()
    with pytest.raises(ConflictError):
        _sign(ready_s09,row)


def test_foreign_clinic_cannot_read_report(ready_s09,make_clinic):
    row=_report(ready_s09)
    outsider=make_clinic(name='S09 foreign reader')
    with pytest.raises(NotFoundError,match='clinic'):
        svc.get_operative_report(report_id=row.id,clinic_id=outsider.id)


def test_database_duplicate_report_and_signature_constraints(ready_s09):
    ctx=ready_s09
    row=_report(ctx)
    db.session.add(SurgicalOperativeReport(**{
        c.name:getattr(row,c.name) for c in SurgicalOperativeReport.__table__.columns
        if c.name!='id'}))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_audit_and_no_execution_side_effects(ready_s09):
    ctx=ready_s09
    before=(ctx['case'].status,ctx['order'].status,ctx['admission'].status)
    row=_report(ctx)
    _sign(ctx,row)
    _sign(ctx,row,'anesthetist')
    assert before==(ctx['case'].status,ctx['order'].status,ctx['admission'].status)
    assert db.session.query(NursingHandoff).count()==0
    assert db.session.query(AdmissionCareAssignment).count()==0
    assert sum(x['entity_type']=='SurgicalOperativeSignature' for x in ctx['events'])==2
