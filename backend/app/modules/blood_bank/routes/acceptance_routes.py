"""Additional bounded clinic-scoped laboratory and safety endpoints."""
from datetime import datetime
from flask import jsonify,request
from pydantic import Field
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError
from app.core.utils.decorators import role_required
from app.modules.blood_bank.routes.blood_bank_routes import blood_bank_bp,ctx,Request,_json_body,LAB,STOCK
from app.modules.blood_bank.models_acceptance import BloodLabArtifact,BloodLabVerification,BloodInvestigationEvent,BloodSafetyInbox
from app.modules.blood_bank.services import lab_evidence as lab,safety_acceptance as safety,closed_loop as loop

class ArtifactRequest(Request):
    reference:str=Field(min_length=1,max_length=255)
    kind:str=Field(min_length=1,max_length=32)
    marker:str=Field(min_length=1,max_length=80)
    specimen_identifier:str=Field(min_length=1,max_length=128)
    test_code:str=Field(min_length=1,max_length=80)
    result:str=Field(min_length=1,max_length=80)
    source_reference:str=Field(min_length=1,max_length=255)
    observed_at:datetime
    donation_id:int|None=Field(default=None,gt=0,strict=True)
    blood_unit_id:int|None=Field(default=None,gt=0,strict=True)
    order_id:int|None=Field(default=None,gt=0,strict=True)
    lab_order_item_id:int|None=Field(default=None,gt=0,strict=True)

class VerificationRequest(Request):
    verification_reference:str=Field(min_length=1,max_length=255)

class InvestigationRequest(Request):
    operation_identity:str=Field(min_length=1,max_length=128)
    outcome:str=Field(min_length=1,max_length=24)
    notes:str=Field(min_length=1,max_length=5000)
    donation_id:int|None=Field(default=None,gt=0,strict=True)
    recall_id:int|None=Field(default=None,gt=0,strict=True)
    artifact_id:int|None=Field(default=None,gt=0,strict=True)


def fields(row,names):
    return {n:(getattr(row,n).isoformat() if isinstance(getattr(row,n),datetime) else getattr(row,n)) for n in names.split()}


def artifact(row):
    out=fields(row,'id reference kind marker specimen_identifier donation_id blood_unit_id order_id result test_code source_reference lab_order_item_id policy_version observed_at recorded_at recorded_by_staff_id')
    proof=db.session.execute(select(BloodLabVerification).where(BloodLabVerification.clinic_id==row.clinic_id,BloodLabVerification.artifact_id==row.id)).scalar_one_or_none()
    out['verification']=fields(proof,'id artifact_id content_sha256 verification_reference verified_by_staff_id verified_at') if proof else None
    return out


def page(query,model):
    if set(request.args)-{'after_id','limit'} or any(len(request.args.getlist(k))!=1 for k in request.args):raise ValidationError('Unknown or repeated filter')
    try: after=int(request.args.get('after_id','0'));limit=int(request.args.get('limit','50'))
    except ValueError as exc:raise ValidationError('Invalid page') from exc
    if after<0 or not 1<=limit<=100:raise ValidationError('Invalid page')
    rows=db.session.execute(query.where(model.id>after).order_by(model.id).limit(limit+1)).scalars().all()
    return rows[:limit],rows[limit-1].id if len(rows)>limit else None

@blood_bank_bp.post('/lab-artifacts')
@role_required(*LAB)
def record_lab_artifact():
    row=lab.record_artifact(**ctx(LAB),**ArtifactRequest.model_validate(_json_body()).model_dump())
    return jsonify(artifact(row)),201

@blood_bank_bp.post('/lab-artifacts/<int:artifact_id>/verify')
@role_required(*LAB)
def verify_lab_artifact(artifact_id):
    c=ctx(LAB);lab.verify_artifact(artifact_id,**c,**VerificationRequest.model_validate(_json_body()).model_dump())
    return jsonify(artifact(loop.scoped(BloodLabArtifact,c['clinic_id'],artifact_id)))

@blood_bank_bp.get('/lab-artifacts')
@role_required(*LAB)
def lab_artifacts():
    c=ctx(LAB);policy=loop.policy_for(c['clinic_id']);actor=c['actor_staff_id']
    if actor not in policy.get('lab_record_staff_ids',()) and actor not in policy.get('lab_verify_staff_ids',()):raise ValidationError('Laboratory evidence read authority required')
    rows,cursor=page(select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==c['clinic_id']),BloodLabArtifact)
    return jsonify({'items':[artifact(x) for x in rows],'next_after_id':cursor})

@blood_bank_bp.get('/lab-artifacts/<int:artifact_id>')
@role_required(*LAB)
def lab_artifact(artifact_id):
    c=ctx(LAB);policy=loop.policy_for(c['clinic_id']);actor=c['actor_staff_id']
    if actor not in policy.get('lab_record_staff_ids',()) and actor not in policy.get('lab_verify_staff_ids',()):raise ValidationError('Laboratory evidence read authority required')
    return jsonify(artifact(loop.scoped(BloodLabArtifact,c['clinic_id'],artifact_id)))

@blood_bank_bp.post('/investigations')
@role_required(Role.LAB_TECHNICIAN,Role.DOCTOR)
def investigation_record():
    row=safety.record_investigation(**ctx((Role.LAB_TECHNICIAN,Role.DOCTOR)),**InvestigationRequest.model_validate(_json_body()).model_dump())
    return jsonify(fields(row,'id donation_id recall_id artifact_id outcome actor_staff_id created_at')),201

@blood_bank_bp.get('/investigations')
@role_required(Role.LAB_TECHNICIAN)
def investigations():
    c=ctx(LAB,'investigation_staff_ids');rows,cursor=page(select(BloodInvestigationEvent).where(BloodInvestigationEvent.clinic_id==c['clinic_id']),BloodInvestigationEvent)
    return jsonify({'items':[fields(x,'id donation_id recall_id artifact_id outcome notes actor_staff_id created_at') for x in rows],'next_after_id':cursor})

@blood_bank_bp.get('/safety-inbox')
@role_required(*STOCK)
def safety_inbox():
    c=ctx(STOCK);rows,cursor=page(select(BloodSafetyInbox).where(BloodSafetyInbox.clinic_id==c['clinic_id'],BloodSafetyInbox.recipient_staff_id==c['actor_staff_id']),BloodSafetyInbox)
    return jsonify({'items':[fields(x,'id kind message recall_id order_id created_at acknowledged_at') for x in rows],'next_after_id':cursor})

@blood_bank_bp.post('/safety-inbox/<int:inbox_id>/acknowledge')
@role_required(*STOCK)
def acknowledge_inbox(inbox_id):
    Request.model_validate(_json_body());row=safety.acknowledge(inbox_id,**ctx(STOCK));return jsonify(fields(row,'id acknowledged_at'))

@blood_bank_bp.post('/stock-alerts/evaluate')
@role_required(Role.LAB_TECHNICIAN,Role.ADMIN)
def stock_alerts_evaluate():
    Request.model_validate(_json_body());return jsonify(safety.evaluate_stock(**ctx((Role.LAB_TECHNICIAN,Role.ADMIN))))

@blood_bank_bp.get('/reports/summary')
@role_required(Role.LAB_TECHNICIAN,Role.ADMIN)
def report_summary():
    c=ctx((Role.LAB_TECHNICIAN,Role.ADMIN),'report_staff_ids');return jsonify(safety.report(c['clinic_id']))

@blood_bank_bp.get('/donations/<int:donation_id>/screening-progress')
@role_required(*LAB)
def screening_progress(donation_id):
    from app.modules.blood_bank.models.blood_bank_model import BloodDonation
    c=ctx(LAB,'screening_staff_ids');d=loop.scoped(BloodDonation,c['clinic_id'],donation_id);policy=loop.policy_for(c['clinic_id'])
    rows=db.session.execute(select(BloodLabArtifact).where(BloodLabArtifact.clinic_id==c['clinic_id'],BloodLabArtifact.donation_id==d.id,BloodLabArtifact.kind=='screening').order_by(BloodLabArtifact.id.desc())).scalars().all()
    latest={}
    for x in rows:latest.setdefault(x.marker,x)
    items=[artifact(latest[m]) for m in policy.get('required_tti_markers',()) if m in latest]
    missing=[m for m in policy.get('required_tti_markers',()) if m not in latest or not any(x['marker']==m and x['verification'] for x in items)]
    return jsonify({'donation_id':d.id,'status':d.tti_screening_status,'items':items,'missing_verified_markers':missing,'release_authorized':False})

@blood_bank_bp.get('/patients/<int:patient_id>/transfusions')
@role_required(Role.DOCTOR,Role.NURSE)
def patient_transfusions(patient_id):
    from app.modules.blood_bank.models.blood_bank_model import BloodTransfusion
    from app.modules.blood_bank.schemas.blood_bank_schema import BloodTransfusionResponseSchema
    from app.modules.patient.models.patient_model import Patient
    from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment as A
    from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder as O
    from app.modules.clinical_orders.routes.clinical_order_route import _serialize
    c=ctx((Role.DOCTOR,Role.NURSE),'bedside_staff_ids');cid,sid=c['clinic_id'],c['actor_staff_id']
    loop.scoped(Patient,cid,patient_id)
    q=select(BloodTransfusion).join(O,BloodTransfusion.blood_product_order_id==O.id).where(BloodTransfusion.clinic_id==cid,BloodTransfusion.patient_id==patient_id,O.clinic_id==cid,O.patient_id==patient_id,O.admission_id==BloodTransfusion.admission_id,select(A.id).where(A.clinic_id==cid,A.patient_id==patient_id,A.admission_id==O.admission_id,A.staff_id==sid,A.ended_at.is_(None)).exists())
    rows,cursor=page(q,BloodTransfusion)
    return jsonify({'items':[_serialize(BloodTransfusionResponseSchema,x) for x in rows],'next_after_id':cursor})

@blood_bank_bp.get('/offline-policy')
@role_required(*STOCK)
def offline_policy():
    from flask import current_app
    from app.modules.blood_bank.offline_policy import mutation_contracts
    ctx(STOCK)
    return jsonify({'policy_version':'bb01-r02-deny-by-default','items':mutation_contracts(current_app),'automatic_replay_authorizations':0})

@blood_bank_bp.post('/transfusions/<int:transfusion_id>/patient-release')
@role_required(Role.DOCTOR)
def patient_release(transfusion_id):
    from app.modules.blood_bank.services import read_history
    body=VerificationRequest.model_validate(_json_body())
    row=read_history.release_patient_history(transfusion_id,**ctx((Role.DOCTOR,)),reference=body.verification_reference)
    return jsonify(fields(row,'id transfusion_id patient_id transfusion_version created_at')),201

@blood_bank_bp.get('/my/transfusions')
@role_required(Role.PATIENT)
def my_transfusions():
    from app.modules.patient.routes.patient_portal_route import _patient_context,_response
    from app.modules.blood_bank.services import read_history
    user,patient,clinic_id=_patient_context()
    return _response(read_history.own_history(clinic_id=clinic_id,patient_id=patient.id,user_id=user.id))

@blood_bank_bp.post('/safety-alerts/reconcile')
@role_required(*LAB)
def reconcile_alerts():
    Request.model_validate(_json_body());return jsonify(safety.reconcile_recall_alerts(**ctx(LAB)))
