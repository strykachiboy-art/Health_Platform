"""CE1-S09 source-anchored operative documentation and clinical signatures.

No incision/procedure authority, eMAR, Sign-Out, operative completion, PACU
arrival, nursing transfer, billing, stock movements or discharge here.
"""
from __future__ import annotations
import hmac
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.surgery.models.operative_report_model import (
    SurgicalOperativeReport, SurgicalOperativeSignature,
)
from app.modules.surgery.models.pacu_preparation_model import SurgicalPacuPreparation
from app.modules.surgery.services import pacu_preparation_service as pacu
from app.modules.surgery.services import intraoperative_evidence_service as evidence
from app.modules.surgery.services import surgical_material_trace_service as materials
from app.modules.surgery.services.surgical_case_service import _doctor, _positive
from app.modules.surgery.services.theatre_booking_service import _required_text

_FIELDS = {
    'preoperative_diagnosis':1200,'postoperative_diagnosis':1200,
    'procedure_narrative':6000,'operative_findings':4000,
    'operative_course':6000,'complication_notes':2000,
    'specimen_notes':2000,'implant_notes':2000,'postoperative_plan':3000,
}
_COMPLICATIONS = frozenset({'none_reported','documented'})


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _snapshot_source(row):
    return {key:(getattr(row,key).isoformat() if key=='drafted_at' else getattr(row,key))
        for key in ('clinic_id','patient_id','admission_id','surgical_case_id',
            'case_version','journal_id','journal_seal_sha256','pacu_preparation_id',
            'pacu_review_sha256','trace_set_sha256','surgeon_staff_id',
            'anesthetist_staff_id','drafted_by_staff_id','drafted_at')}


def _snapshot_content(row):
    return {**_snapshot_source(row),**{k:getattr(row,k) for k in _FIELDS},
            'complication_status':row.complication_status,
            'estimated_blood_loss_ml':row.estimated_blood_loss_ml}


def _snapshot_signature(sig):
    return {key:(getattr(sig,key).isoformat() if key=='signed_at' else getattr(sig,key))
        for key in ('id','clinic_id','report_id','signer_kind','signed_by_staff_id',
                    'signed_at','document_sha256','attestation_note')}


def _source_context(journal_id:int,clinic_id:int):
    journal,case,admission = pacu._journal_context(journal_id,clinic_id)
    packet = db.session.execute(select(SurgicalPacuPreparation).where(
        SurgicalPacuPreparation.clinic_id==clinic_id,
        SurgicalPacuPreparation.journal_id==journal.id,
        SurgicalPacuPreparation.surgical_case_id==case.id,
    )).scalar_one_or_none()
    if packet is None:
        raise NotFoundError('PACU preparation not found for surgical journal')
    pacu._integrity(packet)
    if packet.status != 'reviewed':
        raise ConflictError('Reviewed PACU preparation required for operative documentation')
    if (packet.patient_id!=journal.patient_id or packet.admission_id!=admission.id
            or packet.journal_seal_sha256!=journal.seal_sha256
            or packet.case_version!=case.version):
        raise ConflictError('PACU packet or operative source changed')
    traces = materials._all_traces(journal.id,clinic_id)
    trace_digest = evidence._digest({'journal_id':journal.id,
        'traces':[(x.id,x.evidence_sha256) for x in traces]})
    return journal,case,admission,packet,trace_digest


def _current(row):
    journal,case,admission,packet,trace_digest = _source_context(row.journal_id,row.clinic_id)
    if (row.patient_id!=case.patient_id or row.admission_id!=admission.id
            or row.surgical_case_id!=case.id or row.case_version!=case.version
            or row.journal_seal_sha256!=journal.seal_sha256
            or row.pacu_preparation_id!=packet.id
            or row.pacu_review_sha256!=packet.review_sha256
            or row.trace_set_sha256!=trace_digest
            or row.surgeon_staff_id!=journal.surgeon_staff_id
            or row.anesthetist_staff_id!=journal.anesthetist_staff_id):
        raise ConflictError('Operative report source evidence changed')
    return journal


def _signatures(row):
    signatures=list(db.session.execute(select(SurgicalOperativeSignature).where(
        SurgicalOperativeSignature.report_id==row.id,
        SurgicalOperativeSignature.clinic_id==row.clinic_id,
    ).order_by(SurgicalOperativeSignature.signer_kind)).scalars())
    for sig in signatures:
        if (not isinstance(sig.evidence_sha256,str)
                or not hmac.compare_digest(sig.evidence_sha256,evidence._digest(_snapshot_signature(sig)))
                or sig.document_sha256!=row.content_sha256):
            raise ConflictError('Operative clinical signature fingerprint mismatch')
        correct=row.surgeon_staff_id if sig.signer_kind=='surgeon' else row.anesthetist_staff_id
        if sig.signer_kind not in ('surgeon','anesthetist') or sig.signed_by_staff_id!=correct:
            raise ConflictError('Operative clinical signature actor mismatch')
    kinds={sig.signer_kind for sig in signatures}
    expected={'draft':set(),'surgeon_signed':{'surgeon'},'dual_signed':{'surgeon','anesthetist'}}.get(row.status)
    if expected is None or kinds!=expected or len(signatures)!=len(kinds):
        raise ConflictError('Operative report status and signatures disagree')
    return signatures


def _integrity(row):
    if (not isinstance(row.source_sha256,str) or not isinstance(row.content_sha256,str)
            or not hmac.compare_digest(row.source_sha256,evidence._digest(_snapshot_source(row)))
            or not hmac.compare_digest(row.content_sha256,evidence._digest(_snapshot_content(row)))):
        raise ConflictError('Operative report source or content fingerprint mismatch')
    return _signatures(row)


def _get(report_id:int,clinic_id:int,*,lock:bool=False):
    q=select(SurgicalOperativeReport).where(
        SurgicalOperativeReport.id==_positive(report_id,'report_id'),
        SurgicalOperativeReport.clinic_id==_positive(clinic_id,'clinic_id'))
    if lock:
        q=q.with_for_update()
    row=db.session.execute(q).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Operative documentation report not found in clinic')
    return row


def _content_values(*,complication_status,estimated_blood_loss_ml,**kwargs):
    if set(kwargs)!=set(_FIELDS):
        raise ValidationError('Exact operative narrative fields required')
    result={key:_required_text(kwargs[key],key,maxlen) for key,maxlen in _FIELDS.items()}
    if complication_status not in _COMPLICATIONS:
        raise ValidationError('Invalid complication documentation status')
    if isinstance(estimated_blood_loss_ml,bool) or not isinstance(estimated_blood_loss_ml,int) or estimated_blood_loss_ml<0 or estimated_blood_loss_ml>100000:
        raise ValidationError('Estimated blood loss must be a documented nonnegative integer in mL')
    if complication_status=='documented' and len(result['complication_notes'].strip())<8:
        raise ValidationError('Detailed complication notes required')
    result.update(complication_status=complication_status,
                  estimated_blood_loss_ml=estimated_blood_loss_ml)
    return result


@transactional
def create_operative_report(*,clinic_id:int,journal_id:int,actor_staff_id:int,
    complication_status:str,estimated_blood_loss_ml:int,**narrative)->SurgicalOperativeReport:
    clinic_id=_positive(clinic_id,'clinic_id')
    journal,case,admission,packet,trace_digest=_source_context(_positive(journal_id,'journal_id'),clinic_id)
    surgeon=_doctor(actor_staff_id,clinic_id)
    if surgeon.id!=journal.surgeon_staff_id:
        raise ConflictError('Only assigned privileged surgeon may author operative report')
    if db.session.execute(select(SurgicalOperativeReport.id).where(
        SurgicalOperativeReport.surgical_case_id==case.id)).scalar_one_or_none() is not None:
        raise ConflictError('Operative documentation already exists for case')
    fields=_content_values(complication_status=complication_status,
        estimated_blood_loss_ml=estimated_blood_loss_ml,**narrative)
    row=SurgicalOperativeReport(
        clinic_id=clinic_id,patient_id=case.patient_id,admission_id=admission.id,
        surgical_case_id=case.id,case_version=case.version,
        journal_id=journal.id,journal_seal_sha256=journal.seal_sha256,
        pacu_preparation_id=packet.id,pacu_review_sha256=packet.review_sha256,
        trace_set_sha256=trace_digest,surgeon_staff_id=journal.surgeon_staff_id,
        anesthetist_staff_id=journal.anesthetist_staff_id,
        drafted_by_staff_id=surgeon.id,drafted_at=_now(),status='draft',version=1,
        source_sha256='0'*64,content_sha256='0'*64,**fields)
    row.source_sha256=evidence._digest(_snapshot_source(row))
    row.content_sha256=evidence._digest(_snapshot_content(row))
    db.session.add(row)
    db.session.flush()
    create_audit_log(action=AuditAction.CREATE,entity_type='SurgicalOperativeReport',
        entity_id=row.id,clinic_id=clinic_id,patient_id=row.patient_id,
        user_id=surgeon.user_id,description='Created operative narrative; no procedure completion authority',
        new_value={'version':row.version,'content_sha256':row.content_sha256})
    return row


@transactional
def revise_operative_report(*,report_id:int,clinic_id:int,actor_staff_id:int,
    expected_version:int,complication_status:str,estimated_blood_loss_ml:int,
    **narrative)->SurgicalOperativeReport:
    row=_get(report_id,clinic_id,lock=True)
    _integrity(row)
    if row.status!='draft':
        raise ConflictError('Signed operative report cannot be revised')
    if row.version!=_positive(expected_version,'expected_version'):
        raise ConflictError('Operative report version changed')
    _current(row)
    surgeon=_doctor(actor_staff_id,row.clinic_id)
    if surgeon.id!=row.surgeon_staff_id:
        raise ConflictError('Only assigned surgeon may revise operative documentation')
    fields=_content_values(complication_status=complication_status,
        estimated_blood_loss_ml=estimated_blood_loss_ml,**narrative)
    for k,v in fields.items():
        setattr(row,k,v)
    previous=row.content_sha256
    row.content_sha256=evidence._digest(_snapshot_content(row))
    row.version+=1
    db.session.flush()
    create_audit_log(action=AuditAction.UPDATE,entity_type='SurgicalOperativeReport',
        entity_id=row.id,clinic_id=row.clinic_id,patient_id=row.patient_id,
        user_id=surgeon.user_id,description='Revised draft operative narrative only',
        old_value={'content_sha256':previous},
        new_value={'content_sha256':row.content_sha256,'version':row.version})
    return row


@transactional
def sign_operative_report(*,report_id:int,clinic_id:int,actor_staff_id:int,
    expected_version:int,signer_kind:str,attestation_note:str)->SurgicalOperativeSignature:
    row=_get(report_id,clinic_id,lock=True)
    _integrity(row)
    if row.version!=_positive(expected_version,'expected_version'):
        raise ConflictError('Operative report version changed')
    _current(row)
    if signer_kind not in ('surgeon','anesthetist'):
        raise ValidationError('Unknown operative signer role')
    actor=_doctor(actor_staff_id,row.clinic_id)
    correct=row.surgeon_staff_id if signer_kind=='surgeon' else row.anesthetist_staff_id
    if actor.id!=correct:
        raise ConflictError('Only designated surgeon or anesthetist may sign assigned section')
    if ((signer_kind=='surgeon' and row.status!='draft')
            or (signer_kind=='anesthetist' and row.status!='surgeon_signed')):
        raise ConflictError('Operative clinical signatures must be ordered and unique')
    note=_required_text(attestation_note,'attestation_note',800)
    signature=SurgicalOperativeSignature(
        clinic_id=row.clinic_id,report_id=row.id,signer_kind=signer_kind,
        signed_by_staff_id=actor.id,signed_at=_now(),
        document_sha256=row.content_sha256,attestation_note=note,
        evidence_sha256='0'*64)
    db.session.add(signature)
    db.session.flush()
    signature.evidence_sha256=evidence._digest(_snapshot_signature(signature))
    row.status='surgeon_signed' if signer_kind=='surgeon' else 'dual_signed'
    row.version+=1
    db.session.flush()
    create_audit_log(action=AuditAction.STATUS_CHANGE,entity_type='SurgicalOperativeSignature',
        entity_id=signature.id,clinic_id=row.clinic_id,patient_id=row.patient_id,
        user_id=actor.user_id,description='Signed clinical document; not surgery completion',
        new_value={'report_id':row.id,'signer_kind':signer_kind,
                   'document_sha256':signature.document_sha256})
    return signature


def get_operative_report(*,report_id:int,clinic_id:int)->SurgicalOperativeReport:
    row=_get(report_id,clinic_id)
    _integrity(row)
    return row


def list_operative_signatures(*,report_id:int,clinic_id:int):
    row=_get(report_id,clinic_id)
    return _integrity(row)
