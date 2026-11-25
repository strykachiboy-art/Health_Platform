"""Native donor/component lab artifacts, with independent verification.

The existing LabOrder requires patient_id, so donor screening is deliberately
not represented by a fake patient. External references are reviewed local
attestations, not a claim of external issuer authentication.
"""
import hashlib
from datetime import datetime
import json
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, ValidationError
from app.core.utils.decorators import transactional
from app.modules.blood_bank import policy as p
from app.modules.blood_bank.models.blood_bank_model import BloodUnit, BloodDonation
from app.modules.blood_bank.models_acceptance import BloodLabArtifact, BloodLabVerification
from app.modules.blood_bank.services import closed_loop as loop
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder

KINDS = {'screening', 'blood_group', 'compatibility', 'special_requirements', 'confirmatory', 'emergency_assessment'}
ORDER_KINDS = {'compatibility', 'special_requirements', 'emergency_assessment'}


def content_hash(row):
    fields = ('clinic_id','reference','kind','marker','specimen_identifier','donation_id','blood_unit_id',
              'order_id','patient_id','admission_id','result','test_code','source_reference','policy_version','recorded_by_staff_id','lab_order_item_id','lab_result_snapshot')
    payload={k:getattr(row,k) for k in fields}
    payload['observed_at']=p.db_utc(row.observed_at).isoformat()
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def patient_lab_snapshot(policy,order,item_id,code,result):
    from app.modules.lab.models.lab_model import LabOrder,LabOrderItem,LabTest
    from app.core.enums.lab_enums import LabOrderStatus,LabResultFlag,SampleType
    item=db.session.execute(select(LabOrderItem).join(LabOrder,LabOrderItem.order_id==LabOrder.id).join(LabTest,LabOrderItem.test_id==LabTest.id).where(
        LabOrderItem.id==loop.validate(p.positive_int,item_id,'lab_order_item_id'),LabOrder.clinic_id==order.clinic_id,LabOrder.patient_id==order.patient_id,
        LabTest.code==code,LabTest.sample_type==SampleType.BLOOD, (LabTest.clinic_id==order.clinic_id)|(LabTest.clinic_id.is_(None)))).scalar_one_or_none()
    if item is None:raise ConflictError('Patient laboratory evidence is not scoped to the order')
    lab=item.order
    if lab.status!=LabOrderStatus.COMPLETED or not all((lab.verified_at,lab.verified_by_id,lab.completed_at,lab.sample_collected_at,lab.processed_at,item.resulted_at)) or not isinstance(item.result_value,str) or item.flag!=LabResultFlag.NORMAL:
        raise ConflictError('Fully verified, completed non-discrepant laboratory result required')
    stamps=[p.db_utc(x) for x in (lab.sample_collected_at,lab.processed_at,item.resulted_at,lab.verified_at,lab.completed_at)]
    if stamps!=sorted(stamps) or stamps[0]<p.db_utc(order.ordered_at) or stamps[-1]>loop.now():raise ConflictError('Laboratory timestamps do not match current order')
    amendments=db.metadata.tables.get('clinical_record_amendments')
    if amendments is not None and db.session.execute(select(amendments.c.id).where(amendments.c.clinic_id==order.clinic_id,amendments.c.source_record_type=='lab_report',amendments.c.source_record_id==lab.id).limit(1)).scalar_one_or_none() is not None:
        raise ConflictError('Amended laboratory report requires separately reviewed evidence; automatic reuse prohibited')
    mapping=policy.get('patient_lab_result_mapping',{})
    if not isinstance(mapping,dict) or not isinstance(mapping.get(code),dict) or mapping[code].get(item.result_value)!=result:raise ConflictError('Laboratory result does not match approved interpretation')
    return {'id':item.id,'order_id':lab.id,'test_id':item.test_id,'code':code,'value':item.result_value,'flag':getattr(item.flag,'value',item.flag),'verified_by_id':lab.verified_by_id,'resulted_at':stamps[2].isoformat(),'verified_at':stamps[3].isoformat(),'completed_at':stamps[4].isoformat()}


def protocol(policy, kind, marker, code, result):
    config=policy.get('lab_artifact_protocol')
    rule=config.get(kind,{}).get(marker) if isinstance(config,dict) and isinstance(config.get(kind),dict) else None
    if not isinstance(rule,dict) or not isinstance(rule.get('test_codes'),(tuple,list)) or not isinstance(rule.get('allowed_results'),(tuple,list)) or code not in rule['test_codes'] or result not in rule['allowed_results']:
        raise ConflictError('Approved laboratory artifact protocol is missing or does not match result')


@transactional
def record_artifact(*,clinic_id,actor_staff_id,reference,kind,marker,specimen_identifier,test_code,result,source_reference,observed_at,donation_id=None,blood_unit_id=None,order_id=None,lab_order_item_id=None):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'lab_record_staff_ids',(Role.LAB_TECHNICIAN,))
    if kind not in KINDS or (donation_id is None)==(blood_unit_id is None) or (kind in ORDER_KINDS)!=(order_id is not None):
        raise ValidationError('Laboratory evidence requires exact specimen and order context')
    marker=loop.validate(p.text,marker,'marker',80); code=loop.validate(p.text,test_code,'test_code',80); result=loop.validate(p.text,result,'result',80)
    protocol(policy,kind,marker,code,result)
    ts=loop.validate(p.utc,observed_at)
    if ts>loop.now():raise ValidationError('Observation cannot be future dated')
    order=loop.scoped(BloodProductOrder,clinic_id,order_id,lock=True) if order_id is not None else None
    subject=loop.scoped(BloodDonation,clinic_id,donation_id,lock=True) if donation_id is not None else loop.scoped(BloodUnit,clinic_id,blood_unit_id,lock=True)
    if ts<p.db_utc(subject.collected_at):raise ConflictError('Laboratory observation predates component collection')
    if order is not None:
        loop.assert_context(order)
        if subject.product_type!=order.product_type or ts<p.db_utc(order.ordered_at):raise ConflictError('Laboratory observation does not match current order')
    snapshot=None
    if kind=='compatibility':
        if lab_order_item_id is None: raise ValidationError('Compatibility requires completed patient laboratory result')
        snapshot=patient_lab_snapshot(policy,order,lab_order_item_id,code,result)
        if ts!=p.db_utc(datetime.fromisoformat(snapshot['resulted_at'])): raise ConflictError('Observation must match actual laboratory result timestamp')
    elif lab_order_item_id is not None: raise ValidationError('Patient laboratory linkage is limited to compatibility evidence')
    ref=loop.validate(p.text,reference,'artifact reference',255)
    if db.session.execute(select(BloodLabArtifact.id).where(BloodLabArtifact.clinic_id==clinic_id,BloodLabArtifact.reference==ref)).scalar_one_or_none() is not None:
        raise ConflictError('Laboratory artifact reference already exists')
    row=BloodLabArtifact(clinic_id=clinic_id,reference=ref,kind=kind,marker=marker,
        specimen_identifier=loop.validate(p.identity,specimen_identifier,'specimen identifier'),donation_id=donation_id,blood_unit_id=blood_unit_id,order_id=order_id,
        patient_id=order.patient_id if order else None,admission_id=order.admission_id if order else None,
        result=result,test_code=code,source_reference=loop.validate(p.text,source_reference,'laboratory source reference',255),
        policy_version=loop.validate(p.policy_version,policy),observed_at=ts,recorded_by_staff_id=actor.id,lab_order_item_id=lab_order_item_id,lab_result_snapshot=snapshot)
    db.session.add(row);loop.audit(actor,row,'Recorded laboratory specimen result; independent verification pending',{'kind':kind,'marker':marker})
    from app.modules.blood_bank.services import safety_acceptance as safety
    safety.observe_artifact(row,subject,actor,policy)
    return row


@transactional
def verify_artifact(artifact_id,*,clinic_id,actor_staff_id,verification_reference):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'lab_verify_staff_ids',(Role.LAB_TECHNICIAN,))
    row=loop.scoped(BloodLabArtifact,clinic_id,artifact_id,lock=True)
    if row.recorded_by_staff_id==actor.id:raise ConflictError('Independent laboratory verification required')
    if row.policy_version!=loop.validate(p.policy_version,policy):raise ConflictError('Laboratory artifact policy is stale')
    protocol(policy,row.kind,row.marker,row.test_code,row.result)
    if row.kind=='compatibility':
        order=loop.scoped(BloodProductOrder,clinic_id,row.order_id)
        if patient_lab_snapshot(policy,order,row.lab_order_item_id,row.test_code,row.result)!=row.lab_result_snapshot:raise ConflictError('Laboratory result changed before independent verification')
    if db.session.execute(select(BloodLabVerification.id).where(BloodLabVerification.artifact_id==row.id)).scalar_one_or_none() is not None:
        raise ConflictError('Laboratory artifact already verified')
    proof=BloodLabVerification(clinic_id=clinic_id,artifact_id=row.id,content_sha256=content_hash(row),
        verification_reference=loop.validate(p.text,verification_reference,'verification reference',255),verified_by_staff_id=actor.id)
    db.session.add(proof);loop.audit(actor,proof,'Independently verified specimen result',{'artifact_id':row.id,'content_sha256':proof.content_sha256})
    return proof


def require_artifact(policy,clinic_id,reference,*,kind,marker,result,donation_id=None,blood_unit_id=None,order=None):
    ref=loop.validate(p.text,reference,'artifact reference',255)
    row=db.session.execute(select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==clinic_id,BloodLabArtifact.reference==ref)).scalar_one_or_none()
    if row is None or (row.kind,row.marker,row.result,row.donation_id,row.blood_unit_id,row.order_id,row.patient_id,row.admission_id)!=(kind,marker,result,donation_id,blood_unit_id,order.id if order else None,order.patient_id if order else None,order.admission_id if order else None):
        raise ConflictError('Verified laboratory artifact does not match clinical subject and result')
    proof=db.session.execute(select(BloodLabVerification).where(BloodLabVerification.clinic_id==clinic_id,BloodLabVerification.artifact_id==row.id)).scalar_one_or_none()
    if proof is None or proof.verified_by_staff_id==row.recorded_by_staff_id or proof.content_sha256!=content_hash(row) or row.policy_version!=loop.validate(p.policy_version,policy):
        raise ConflictError('Independent current laboratory verification required')
    protocol(policy,kind,marker,row.test_code,row.result)
    if kind=='compatibility' and patient_lab_snapshot(policy,order,row.lab_order_item_id,row.test_code,row.result)!=row.lab_result_snapshot:raise ConflictError('Patient laboratory result no longer matches verified artifact')
    # A later contradictory result, even before independent verification, is a safety hold.
    q=select(BloodLabArtifact.id).where(BloodLabArtifact.clinic_id==clinic_id,BloodLabArtifact.kind==kind,
        BloodLabArtifact.marker==marker,BloodLabArtifact.donation_id==donation_id,BloodLabArtifact.blood_unit_id==blood_unit_id,
        BloodLabArtifact.order_id==(order.id if order else None),BloodLabArtifact.id>row.id,BloodLabArtifact.result!=row.result)
    if db.session.execute(q.limit(1)).scalar_one_or_none() is not None:
        raise ConflictError('Later contradictory laboratory evidence requires investigation')
    return row,proof


def require_panel(policy,clinic_id,panel,*,donation_id=None,blood_unit_id=None):
    for marker,e in panel.items():
        require_artifact(policy,clinic_id,e['evidence_reference'],kind='screening',marker=marker,result=e['result'],donation_id=donation_id,blood_unit_id=blood_unit_id)


def require_compatibility(policy,order,unit,evidence,special,emergency):
    kind='emergency_assessment' if emergency else 'compatibility'
    require_artifact(policy,order.clinic_id,evidence,kind=kind,marker=order.product_type,result='authorized' if emergency else 'compatible',blood_unit_id=unit.id,order=order)
    require_artifact(policy,order.clinic_id,special,kind='special_requirements',marker=order.product_type,result='requirements_reviewed',blood_unit_id=unit.id,order=order)


def screening_subject(unit):
    return {'donation_id':unit.donation_id} if unit.donation_id is not None else {'blood_unit_id':unit.id}


def require_group(policy,clinic_id,reference,group,**subject):
    return require_artifact(policy,clinic_id,reference,kind='blood_group',marker='abo_rh',result=group,**subject)


def current_group_reference(policy,unit):
    subject=screening_subject(unit)
    q=select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==unit.clinic_id,BloodLabArtifact.kind=='blood_group',BloodLabArtifact.marker=='abo_rh',BloodLabArtifact.donation_id==subject.get('donation_id'),BloodLabArtifact.blood_unit_id==subject.get('blood_unit_id'),BloodLabArtifact.order_id.is_(None)).order_by(BloodLabArtifact.id.desc()).limit(1)
    row=db.session.execute(q).scalar_one_or_none()
    if row is None:raise ConflictError('Verified component blood-group evidence required')
    require_group(policy,unit.clinic_id,row.reference,unit.blood_type,**subject)
    return row.reference


def require_release(policy,unit,panel,blood_group_reference):
    subject=screening_subject(unit)
    require_panel(policy,unit.clinic_id,panel,**subject)
    require_group(policy,unit.clinic_id,blood_group_reference,unit.blood_type,**subject)
    if unit.donation_id is not None:
        d=loop.scoped(BloodDonation,unit.clinic_id,unit.donation_id)
        if d.blood_group_reference!=blood_group_reference:raise ConflictError('Component blood group evidence must match recorded donor screening')
