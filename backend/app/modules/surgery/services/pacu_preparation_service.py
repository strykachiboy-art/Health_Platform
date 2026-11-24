"""CE1-S08: non-executable PACU preparation, observations and content review.

No PACU arrival/transfer, operative completion, WHO Sign-Out, ward bed mutation,
NursingHandoff ACKNOWLEDGED or AdmissionCareAssignment changes occur here.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.ward_enums import AdmissionStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.staff.models.staff_model import Staff
from app.modules.ward.models.ward_model import Admission, Bed, Ward
from app.modules.surgery.models.surgical_case_model import SurgicalCase
from app.modules.surgery.models.pacu_preparation_model import (
    SurgicalPacuPreparation, SurgicalPacuObservation,
)
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.modules.surgery.services.surgical_case_service import _positive
from app.modules.surgery.services.theatre_booking_service import _required_text

_CONSCIOUSNESS = frozenset({'alert','voice','pain','unresponsive'})
_RANGES = {
    'heart_rate_bpm': (20, 250), 'systolic_bp': (40, 300),
    'diastolic_bp': (20, 200), 'spo2_percent': (0, 100),
    'respiratory_rate': (1, 70), 'temperature_tenths_c': (300, 450),
    'pain_score': (0, 10),
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _nurse(staff_id: int, clinic_id: int) -> Staff:
    row = db.session.execute(select(Staff).where(
        Staff.id == _positive(staff_id, 'staff_id'), Staff.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError('PACU nurse not found in clinic')
    if (row.status != StaffStatus.ACTIVE or row.user is None
            or not row.user.is_active or row.user.role != Role.NURSE):
        raise ValidationError('Active clinic nurse required for PACU preparation')
    return row


def _admission(case: SurgicalCase, clinic_id: int) -> Admission:
    row = db.session.execute(select(Admission).join(Bed, Bed.id == Admission.bed_id)
        .join(Ward, Ward.id == Bed.ward_id).where(
            Admission.id == case.admission_id, Admission.patient_id == case.patient_id,
            Ward.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Surgical admission not found in clinic')
    if row.status != AdmissionStatus.ADMITTED:
        raise ConflictError('Active inpatient admission required for preparation')
    return row


def _journal_context(journal_id: int, clinic_id: int):
    row = evidence._journal(journal_id, clinic_id)
    evidence._integrity(row)
    if row.status != 'sealed':
        raise ConflictError('Sealed surgical evidence journal required')
    # Sealing only reconciles documentation; it is not proof of surgery.
    case = db.session.execute(select(SurgicalCase).where(
        SurgicalCase.id == row.surgical_case_id,
        SurgicalCase.clinic_id == clinic_id,
        SurgicalCase.patient_id == row.patient_id,
    )).scalar_one_or_none()
    if case is None:
        raise NotFoundError('Surgical case not found in clinic')
    admission = _admission(case, clinic_id)
    # Validate inpatient status before the prior Surgery context helper,
    # so PACU reports its own explicit fail-closed admission contract.
    evidence._require_current(row)
    return row, case, admission


def _source_snapshot(p):
    return {
        'clinic_id':p.clinic_id,'patient_id':p.patient_id,'admission_id':p.admission_id,
        'surgical_case_id':p.surgical_case_id,'journal_id':p.journal_id,
        'journal_seal_sha256':p.journal_seal_sha256,'case_version':p.case_version,
        'booking_version':p.booking_version,'outgoing_nurse_staff_id':p.outgoing_nurse_staff_id,
        'receiving_nurse_staff_id':p.receiving_nurse_staff_id,
        'created_at':p.created_at.isoformat(),'created_by_staff_id':p.created_by_staff_id,
    }


def _content_snapshot(p):
    return {**_source_snapshot(p), **{key:getattr(p,key) for key in (
        'situation','background','assessment','recommendation','safety_concerns',
        'pending_actions','airway_plan','analgesia_plan','escalation_plan',
    )}}


def _obs_snapshot(o):
    return {key:(getattr(o,key).isoformat() if key == 'observed_at' else getattr(o,key))
            for key in (
        'id','clinic_id','preparation_id','sequence','observed_by_staff_id','observed_at',
        'heart_rate_bpm','systolic_bp','diastolic_bp','spo2_percent',
        'respiratory_rate','temperature_tenths_c','pain_score','consciousness',
        'airway_patent','bleeding_concern','escalation_communicated','note',
    )}


def _prepared_snapshot(p, observations):
    return {
        'id':p.id,'source_sha256':p.source_sha256,'content_sha256':p.content_sha256,
        'prepared_at':p.prepared_at.isoformat(),'prepared_by_staff_id':p.prepared_by_staff_id,
        'prepared_version':p.prepared_version,
        'observations':[(o.id,o.evidence_sha256) for o in observations],
    }


def _review_snapshot(p):
    return {'id':p.id,'prepared_sha256':p.prepared_sha256,
            'reviewed_at':p.reviewed_at.isoformat(),
            'reviewed_by_staff_id':p.reviewed_by_staff_id,
            'reviewed_version':p.reviewed_version}


def _observations(p):
    rows = list(db.session.execute(select(SurgicalPacuObservation).where(
        SurgicalPacuObservation.preparation_id == p.id,
        SurgicalPacuObservation.clinic_id == p.clinic_id,
    ).order_by(SurgicalPacuObservation.sequence)).scalars())
    for seq, row in enumerate(rows, 1):
        if row.sequence != seq or not isinstance(row.evidence_sha256,str) or not hmac.compare_digest(
            row.evidence_sha256, evidence._digest(_obs_snapshot(row))
        ):
            raise ConflictError('PACU observation sequence or fingerprint mismatch')
    return rows


def _integrity(p):
    if (not isinstance(p.source_sha256,str) or not isinstance(p.content_sha256,str)
            or not hmac.compare_digest(p.source_sha256, evidence._digest(_source_snapshot(p)))
            or not hmac.compare_digest(p.content_sha256, evidence._digest(_content_snapshot(p)))):
        raise ConflictError('PACU preparation source or content fingerprint mismatch')
    obs = _observations(p)
    if p.status in ('prepared','reviewed'):
        if (p.prepared_at is None or p.prepared_by_staff_id is None or p.prepared_version is None
                or not isinstance(p.prepared_sha256,str)
                or not hmac.compare_digest(p.prepared_sha256, evidence._digest(_prepared_snapshot(p, obs)))):
            raise ConflictError('PACU prepared evidence fingerprint mismatch')
    if p.status == 'reviewed':
        if (p.reviewed_at is None or p.reviewed_by_staff_id is None or p.reviewed_version is None
                or not isinstance(p.review_sha256,str)
                or not hmac.compare_digest(p.review_sha256,evidence._digest(_review_snapshot(p)))):
            raise ConflictError('PACU nursing review fingerprint mismatch')
    return obs


def _packet(preparation_id: int, clinic_id: int, *, lock: bool=False):
    q = select(SurgicalPacuPreparation).where(
        SurgicalPacuPreparation.id == _positive(preparation_id,'preparation_id'),
        SurgicalPacuPreparation.clinic_id == _positive(clinic_id,'clinic_id'),
    )
    if lock:
        q = q.with_for_update()
    p = db.session.execute(q).scalar_one_or_none()
    if p is None:
        raise NotFoundError('PACU preparation not found in clinic')
    return p


def _editable(p, expected_version: int):
    observations = _integrity(p)
    if p.status != 'draft':
        raise ConflictError('Prepared or reviewed PACU evidence is immutable')
    if p.version != _positive(expected_version,'expected_version'):
        raise ConflictError('PACU preparation version changed')
    journal, case, admission = _journal_context(p.journal_id, p.clinic_id)
    if (journal.id != p.journal_id or journal.seal_sha256 != p.journal_seal_sha256
            or journal.surgical_case_id != p.surgical_case_id or journal.patient_id != p.patient_id
            or journal.counting_nurse_staff_id != p.outgoing_nurse_staff_id
            or journal.booking_version != p.booking_version
            or case.version != p.case_version or admission.id != p.admission_id):
        raise ConflictError('PACU preparation surgery or admission source changed')
    _nurse(p.outgoing_nurse_staff_id, p.clinic_id)
    _nurse(p.receiving_nurse_staff_id, p.clinic_id)
    return observations


def _number(value, label):
    low, high = _RANGES[label]
    if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
        raise ValidationError(f'{label} outside allowed documentation range')
    return value


@transactional
def create_pacu_preparation(*, clinic_id:int, journal_id:int, actor_staff_id:int,
    receiving_nurse_staff_id:int, situation:str, background:str, assessment:str,
    recommendation:str, safety_concerns:str, pending_actions:str, airway_plan:str,
    analgesia_plan:str, escalation_plan:str) -> SurgicalPacuPreparation:
    clinic_id = _positive(clinic_id,'clinic_id')
    journal, case, admission = _journal_context(_positive(journal_id,'journal_id'),clinic_id)
    outgoing = _nurse(actor_staff_id,clinic_id)
    if outgoing.id != journal.counting_nurse_staff_id:
        raise ConflictError('Only counting nurse may prepare PACU nursing evidence')
    receiving = _nurse(receiving_nurse_staff_id,clinic_id)
    if receiving.id == outgoing.id:
        raise ValidationError('Distinct receiving nurse required')
    if db.session.execute(select(SurgicalPacuPreparation.id).where(
        SurgicalPacuPreparation.surgical_case_id == case.id,
    )).scalar_one_or_none() is not None:
        raise ConflictError('PACU preparation already exists for case')
    fields = {}
    for key,value in dict(
        situation=situation,background=background,assessment=assessment,
        recommendation=recommendation,safety_concerns=safety_concerns,
        pending_actions=pending_actions,airway_plan=airway_plan,
        analgesia_plan=analgesia_plan,escalation_plan=escalation_plan,
    ).items():
        fields[key] = _required_text(value,key,1200 if key.endswith('_plan') else 2000)
    p = SurgicalPacuPreparation(
        clinic_id=clinic_id, patient_id=case.patient_id, admission_id=admission.id,
        surgical_case_id=case.id, journal_id=journal.id,
        journal_seal_sha256=journal.seal_sha256,case_version=case.version,
        booking_version=journal.booking_version,outgoing_nurse_staff_id=outgoing.id,
        receiving_nurse_staff_id=receiving.id,created_at=_now(),
        created_by_staff_id=outgoing.id,status='draft',version=1,
        source_sha256='0'*64,content_sha256='0'*64,**fields,
    )
    p.source_sha256 = evidence._digest(_source_snapshot(p))
    p.content_sha256 = evidence._digest(_content_snapshot(p))
    db.session.add(p)
    db.session.flush()
    create_audit_log(action=AuditAction.CREATE,entity_type='SurgicalPacuPreparation',
        entity_id=p.id,clinic_id=clinic_id,patient_id=p.patient_id,user_id=outgoing.user_id,
        description='Created non-executable PACU nursing preparation, no care transfer',
        new_value={'journal_id':journal.id,'version':1,'content_sha256':p.content_sha256})
    return p


@transactional
def add_pacu_observation(*, preparation_id:int, clinic_id:int, actor_staff_id:int,
    expected_version:int, observed_at:datetime, heart_rate_bpm:int, systolic_bp:int,
    diastolic_bp:int, spo2_percent:int, respiratory_rate:int, temperature_tenths_c:int,
    pain_score:int, consciousness:str, airway_patent:bool, bleeding_concern:bool,
    escalation_communicated:bool, note:str) -> SurgicalPacuObservation:
    p = _packet(preparation_id,clinic_id,lock=True)
    obs = _editable(p,expected_version)
    actor = _nurse(actor_staff_id,p.clinic_id)
    if actor.id not in (p.outgoing_nurse_staff_id,p.receiving_nurse_staff_id):
        raise ConflictError('Only named nurses may document PACU preparation observations')
    values = {key:_number(value,key) for key,value in {
        'heart_rate_bpm':heart_rate_bpm,'systolic_bp':systolic_bp,
        'diastolic_bp':diastolic_bp,'spo2_percent':spo2_percent,
        'respiratory_rate':respiratory_rate,'temperature_tenths_c':temperature_tenths_c,
        'pain_score':pain_score,
    }.items()}
    if values['diastolic_bp'] >= values['systolic_bp']:
        raise ValidationError('diastolic_bp must be below systolic_bp')
    if consciousness not in _CONSCIOUSNESS:
        raise ValidationError('Invalid consciousness level')
    for name,value in [('airway_patent',airway_patent),('bleeding_concern',bleeding_concern),
                       ('escalation_communicated',escalation_communicated)]:
        if not isinstance(value,bool):
            raise ValidationError(f'{name} must be boolean')
    when = evidence._time(observed_at)
    entry = SurgicalPacuObservation(
        clinic_id=p.clinic_id,preparation_id=p.id,sequence=len(obs)+1,
        observed_by_staff_id=actor.id,observed_at=when,
        consciousness=consciousness,airway_patent=airway_patent,
        bleeding_concern=bleeding_concern,escalation_communicated=escalation_communicated,
        note=_required_text(note,'note',1200), evidence_sha256='0'*64,**values)
    db.session.add(entry)
    db.session.flush()
    entry.evidence_sha256 = evidence._digest(_obs_snapshot(entry))
    p.version += 1
    db.session.flush()
    create_audit_log(action=AuditAction.CREATE,entity_type='SurgicalPacuObservation',
        entity_id=entry.id,clinic_id=p.clinic_id,patient_id=p.patient_id,user_id=actor.user_id,
        description='Recorded preliminary recovery observation; not a PACU arrival or treatment',
        new_value={'preparation_id':p.id,'sequence':entry.sequence,'evidence_sha256':entry.evidence_sha256})
    return entry


@transactional
def prepare_pacu_handoff(*, preparation_id:int, clinic_id:int,
    actor_staff_id:int, expected_version:int) -> SurgicalPacuPreparation:
    p = _packet(preparation_id,clinic_id,lock=True)
    obs = _editable(p,expected_version)
    actor = _nurse(actor_staff_id,p.clinic_id)
    if actor.id != p.outgoing_nurse_staff_id:
        raise ConflictError('Only outgoing counting nurse may prepare PACU handoff')
    if not obs:
        raise ConflictError('At least one recovery observation is required')
    if any((not x.airway_patent or x.bleeding_concern) and not x.escalation_communicated
           for x in obs):
        raise ConflictError('Airway or bleeding concern requires documented escalation')
    p.status='prepared'
    p.version += 1
    p.prepared_version=p.version
    p.prepared_at=_now()
    p.prepared_by_staff_id=actor.id
    p.prepared_sha256=evidence._digest(_prepared_snapshot(p,obs))
    db.session.flush()
    create_audit_log(action=AuditAction.STATUS_CHANGE,entity_type='SurgicalPacuPreparation',
        entity_id=p.id,clinic_id=p.clinic_id,patient_id=p.patient_id,user_id=actor.user_id,
        description='PACU handoff packet prepared only; no patient transfer',
        old_value={'status':'draft','version':expected_version},
        new_value={'status':'prepared','version':p.version,'prepared_sha256':p.prepared_sha256})
    return p


@transactional
def review_pacu_handoff(*, preparation_id:int, clinic_id:int,
    actor_staff_id:int, expected_version:int) -> SurgicalPacuPreparation:
    p = _packet(preparation_id,clinic_id,lock=True)
    _integrity(p)
    if p.status != 'prepared':
        raise ConflictError('Prepared PACU packet required before review')
    if p.version != _positive(expected_version,'expected_version'):
        raise ConflictError('PACU preparation version changed')
    # The independent source and admission must remain valid at review time.
    journal, case, admission = _journal_context(p.journal_id,p.clinic_id)
    if (journal.seal_sha256 != p.journal_seal_sha256 or case.version != p.case_version
            or admission.id != p.admission_id or journal.booking_version != p.booking_version):
        raise ConflictError('PACU preparation source changed before review')
    actor = _nurse(actor_staff_id,p.clinic_id)
    _nurse(p.outgoing_nurse_staff_id,p.clinic_id)
    if actor.id != p.receiving_nurse_staff_id:
        raise ConflictError('Only designated receiving nurse can review packet')
    p.status='reviewed'
    p.version += 1
    p.reviewed_version=p.version
    p.reviewed_at=_now()
    p.reviewed_by_staff_id=actor.id
    p.review_sha256=evidence._digest(_review_snapshot(p))
    db.session.flush()
    create_audit_log(action=AuditAction.STATUS_CHANGE,entity_type='SurgicalPacuPreparation',
        entity_id=p.id,clinic_id=p.clinic_id,patient_id=p.patient_id,user_id=actor.user_id,
        description='PACU preparation content reviewed only; no NursingHandoff acknowledgement',
        old_value={'status':'prepared','version':expected_version},
        new_value={'status':'reviewed','version':p.version,'review_sha256':p.review_sha256})
    return p


def get_pacu_preparation(*, preparation_id:int, clinic_id:int) -> SurgicalPacuPreparation:
    p=_packet(preparation_id,clinic_id)
    _integrity(p)
    return p


def list_pacu_observations(*, preparation_id:int, clinic_id:int) -> list[SurgicalPacuObservation]:
    p=_packet(preparation_id,clinic_id)
    return _integrity(p)
