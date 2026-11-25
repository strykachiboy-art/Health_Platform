"""Clinic-scoped API. Every mutation delegates to an authoritative service."""
from flask import Blueprint, jsonify, request
from pydantic import BaseModel, ConfigDict, Field
from typing import Any
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.utils.decorators import role_required, transactional
from app.modules.clinical_orders.routes.clinical_order_route import _clinic_id, _current_staff_id, _json_body, _serialize
from app.modules.blood_bank.services import blood_bank_service as bank, blood_barcode_transfusion_service as bedside, closed_loop as loop
from app.modules.blood_bank.models.blood_bank_model import BloodUnit, BloodDonation, BloodTransfusion
from app.modules.blood_bank.models_extra import BloodSupplier, BloodReactionEvidence, BloodRecallEvidence
from app.modules.blood_bank.schemas import blood_bank_schema as S
from app.modules.blood_bank import policy as p

blood_bank_bp=Blueprint('blood_bank',__name__,url_prefix='/blood-bank')
LAB=(Role.LAB_TECHNICIAN,)
INTAKE=(Role.NURSE,Role.LAB_TECHNICIAN,Role.ADMIN)
STOCK=(Role.ADMIN,Role.LAB_TECHNICIAN,Role.DOCTOR,Role.NURSE)
BEDSIDE=(Role.NURSE,Role.DOCTOR)


class Request(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)


class SupplierRequest(Request):
    supplier_code:str=Field(min_length=1,max_length=80)
    name:str=Field(min_length=1,max_length=200)
    approval_reference:str=Field(min_length=1,max_length=255)


class ReleaseRequest(Request):
    blood_group_reference:str=Field(min_length=1,max_length=255)
    expected_version:int=Field(gt=0,strict=True)
    screening_results:dict[str,Any]
    release_reference:str=Field(min_length=1,max_length=255)
    label_check_reference:str=Field(min_length=1,max_length=255)
    storage_check_reference:str=Field(min_length=1,max_length=255)


class ReservationItem(Request):
    blood_unit_id:int=Field(gt=0,strict=True)
    expected_version:int=Field(gt=0,strict=True)
    evidence_reference:str=Field(min_length=1,max_length=255)
    special_requirements_reference:str=Field(min_length=1,max_length=255)


class OrderVersion(Request):
    expected_order_version:int=Field(gt=0,strict=True)


class ReservationRequest(OrderVersion):
    units:list[ReservationItem]=Field(min_length=1,max_length=100)


class RecallRequest(Request):
    reason:str=Field(min_length=1,max_length=5000)
    donation_id:int|None=Field(default=None,gt=0,strict=True)
    supplier_id:int|None=Field(default=None,gt=0,strict=True)
    din:str|None=Field(default=None,max_length=64)


class ReturnRequest(Request):
    expected_version:int=Field(gt=0,strict=True)
    inspection_reference:str=Field(min_length=1,max_length=255)
    reason:str=Field(min_length=1,max_length=5000)


class StopRequest(Request):
    notes:str=Field(min_length=1,max_length=5000)
    post_vitals:dict[str,Any]=Field(min_length=1)


def ctx(roles,authority=None):
    cid, sid=_clinic_id(),_current_staff_id()
    pol=loop.policy_for(cid)
    actor=bank._get_staff(sid,cid,allowed_roles=roles,role_message='Blood bank access denied')
    if authority: loop.validate(p.require_actor,pol,authority,actor.id)
    return {'clinic_id':cid,'actor_staff_id':sid}


def call(fn,schema,roles,response_schema,*args,created=False,authority=None):
    data=schema.model_validate(_json_body()).model_dump()
    obj=fn(*args,**ctx(roles,authority),**data)
    return jsonify(_serialize(response_schema,obj)),201 if created else 200


def list_payload(schema,rows):
    return jsonify({'items':[_serialize(schema,r) for r in rows],'total':len(rows)})


def supplier_dict(s):
    return {'id':s.id,'clinic_id':s.clinic_id,'supplier_code':s.supplier_code,'name':s.name,
            'approval_reference':s.approval_reference,'active':s.active}


@blood_bank_bp.post('/suppliers')
@role_required(Role.ADMIN,Role.LAB_TECHNICIAN)
def create_supplier():
    row=loop.register_supplier(**ctx((Role.ADMIN,Role.LAB_TECHNICIAN)),**SupplierRequest.model_validate(_json_body()).model_dump())
    return jsonify(supplier_dict(row)),201


@blood_bank_bp.get('/suppliers')
@role_required(Role.ADMIN,Role.LAB_TECHNICIAN)
def suppliers():
    c=ctx((Role.ADMIN,Role.LAB_TECHNICIAN)); rows=db.session.execute(select(BloodSupplier).where(BloodSupplier.clinic_id==c['clinic_id']).order_by(BloodSupplier.id)).scalars().all()
    return jsonify({'items':[supplier_dict(r) for r in rows],'total':len(rows)})


@blood_bank_bp.post('/donors')
@role_required(*INTAKE)
def register_donor():
    return call(bank.register_blood_donor,S.BloodDonorCreateSchema,INTAKE,S.BloodDonorResponseSchema,created=True)


@blood_bank_bp.get('/donors')
@role_required(*INTAKE)
def donors():
    c=ctx(INTAKE,'donor_intake_staff_ids')
    return list_payload(S.BloodDonorResponseSchema,bank.list_blood_donors(clinic_id=c['clinic_id'],blood_type=request.args.get('blood_type'),deferral_status=request.args.get('deferral_status')))


@blood_bank_bp.post('/donors/<int:donor_id>/deferral')
@role_required(Role.NURSE,Role.LAB_TECHNICIAN,Role.DOCTOR)
def donor_deferral(donor_id):
    return call(bank.update_blood_donor_deferral,S.BloodDonorDeferralUpdateSchema,(Role.NURSE,Role.LAB_TECHNICIAN,Role.DOCTOR),S.BloodDonorResponseSchema,donor_id)


@blood_bank_bp.post('/donations')
@role_required(Role.NURSE,Role.LAB_TECHNICIAN)
def donate():
    data=S.BloodDonationCreateSchema.model_validate(_json_body()).model_dump()
    donation,units=bank.record_blood_donation(**ctx((Role.NURSE,Role.LAB_TECHNICIAN)),**data)
    return jsonify({'donation':_serialize(S.BloodDonationResponseSchema,donation),'units':[_serialize(S.BloodUnitResponseSchema,x) for x in units]}),201


@blood_bank_bp.get('/donations')
@role_required(Role.LAB_TECHNICIAN,Role.NURSE)
def donations():
    c=ctx((Role.LAB_TECHNICIAN,Role.NURSE),'donor_intake_staff_ids')
    rows=db.session.execute(select(BloodDonation).where(BloodDonation.clinic_id==c['clinic_id']).order_by(BloodDonation.id.desc())).scalars().all()
    return list_payload(S.BloodDonationResponseSchema,rows)


@blood_bank_bp.post('/donations/<int:donation_id>/screen')
@role_required(*LAB)
def screen(donation_id):
    row,units=bank.screen_blood_donation(donation_id,**ctx(LAB),**S.BloodDonationScreenSchema.model_validate(_json_body()).model_dump())
    return jsonify({'donation':_serialize(S.BloodDonationResponseSchema,row),'units':[_serialize(S.BloodUnitResponseSchema,x) for x in units]})


@blood_bank_bp.post('/units/receive')
@role_required(Role.LAB_TECHNICIAN,Role.ADMIN)
def receive():
    return call(bank.receive_external_blood_unit,S.ExternalBloodUnitReceiveSchema,(Role.LAB_TECHNICIAN,Role.ADMIN),S.BloodUnitResponseSchema,created=True)


@blood_bank_bp.get('/units')
@role_required(Role.LAB_TECHNICIAN,Role.ADMIN)
def inventory():
    c=ctx((Role.LAB_TECHNICIAN,Role.ADMIN))
    return list_payload(S.BloodUnitResponseSchema,bank.list_blood_units(clinic_id=c['clinic_id'],status=request.args.get('status'),product_type=request.args.get('product_type'),blood_type=request.args.get('blood_type')))


@blood_bank_bp.get('/units/<int:unit_id>')
@role_required(Role.LAB_TECHNICIAN,Role.ADMIN)
def component(unit_id):
    c=ctx((Role.LAB_TECHNICIAN,Role.ADMIN))
    return jsonify(_serialize(S.BloodUnitResponseSchema,bank.get_blood_unit(unit_id,clinic_id=c['clinic_id'])))


@blood_bank_bp.post('/units/<int:unit_id>/release')
@role_required(*LAB)
def release(unit_id):
    return call(loop.release_unit,ReleaseRequest,LAB,S.BloodUnitResponseSchema,unit_id)


@blood_bank_bp.post('/units/<int:unit_id>/discard')
@role_required(*LAB)
def discard(unit_id):
    return call(bank.discard_blood_unit,S.BloodUnitDiscardSchema,LAB,S.BloodUnitResponseSchema,unit_id)


@blood_bank_bp.post('/units/<int:unit_id>/return')
@role_required(*LAB)
def return_unit(unit_id):
    return call(loop.return_issued_component,ReturnRequest,LAB,S.BloodUnitResponseSchema,unit_id)


@blood_bank_bp.post('/units/expire')
@role_required(*LAB)
def expire():
    Request.model_validate(_json_body())
    return list_payload(S.BloodUnitResponseSchema,bank.expire_stale_blood_units(**ctx(LAB)))


@blood_bank_bp.post('/stock-thresholds')
@role_required(Role.ADMIN)
def thresholds():
    return call(bank.upsert_blood_stock_threshold,S.BloodStockThresholdUpsertSchema,(Role.ADMIN,),S.BloodStockThresholdResponseSchema)


@blood_bank_bp.get('/stock-summary')
@role_required(*STOCK)
def stock_summary():
    c=ctx(STOCK)
    return jsonify(S.BloodStockSummaryResponseSchema.model_validate(bank.get_blood_stock_summary(clinic_id=c['clinic_id'],product_type=request.args.get('product_type'))).model_dump(mode='json'))


@blood_bank_bp.get('/shortages/<int:order_id>/donor-matches')
@role_required(Role.ADMIN,Role.LAB_TECHNICIAN)
def match_donors(order_id):
    c=ctx((Role.ADMIN,Role.LAB_TECHNICIAN),'donor_outreach_staff_ids')
    data=bank.find_eligible_donors_for_shortage(clinic_id=c['clinic_id'],blood_product_order_id=order_id)
    return jsonify(S.ShortageDonorMatchesResponseSchema.model_validate(data).model_dump(mode='json'))


@blood_bank_bp.post('/orders/<int:order_id>/reserve')
@role_required(*LAB)
def reserve(order_id):
    rows=loop.reserve_order_units(order_id,**ctx(LAB),**ReservationRequest.model_validate(_json_body()).model_dump())
    return list_payload(S.BloodUnitResponseSchema,rows)


@blood_bank_bp.post('/orders/<int:order_id>/release-reservations')
@role_required(*LAB)
def unreserve(order_id):
    rows=loop.release_order_reservations(order_id,**ctx(LAB),**OrderVersion.model_validate(_json_body()).model_dump())
    return list_payload(S.BloodUnitResponseSchema,rows)


@blood_bank_bp.post('/barcode/verify')
@role_required(*BEDSIDE)
def barcode():
    return call(bedside.verify_blood_transfusion_barcode,S.BloodTransfusionBarcodeVerifySchema,BEDSIDE,S.BloodTransfusionBarcodeEventResponseSchema,created=True)


@blood_bank_bp.post('/transfusions')
@role_required(*BEDSIDE)
def start():
    return call(bedside.start_blood_transfusion,S.BloodTransfusionStartSchema,BEDSIDE,S.BloodTransfusionResponseSchema,created=True)


@blood_bank_bp.post('/transfusions/<int:transfusion_id>/complete')
@role_required(*BEDSIDE)
def complete(transfusion_id):
    return call(bedside.complete_blood_transfusion,S.BloodTransfusionCompleteSchema,BEDSIDE,S.BloodTransfusionResponseSchema,transfusion_id)


@blood_bank_bp.post('/transfusions/<int:transfusion_id>/stop')
@role_required(*BEDSIDE)
def stop(transfusion_id):
    return call(bedside.stop_blood_transfusion,StopRequest,BEDSIDE,S.BloodTransfusionResponseSchema,transfusion_id)


@blood_bank_bp.post('/transfusions/<int:transfusion_id>/reactions')
@role_required(*BEDSIDE)
def reaction(transfusion_id):
    row,units=bedside.record_transfusion_reaction(transfusion_id,**ctx(BEDSIDE),**S.BloodTransfusionReactionSchema.model_validate(_json_body()).model_dump())
    return jsonify({'transfusion':_serialize(S.BloodTransfusionResponseSchema,row),'blocked_unit_ids':[x.id for x in units]})


@blood_bank_bp.get('/transfusions/<int:transfusion_id>')
@role_required(*BEDSIDE)
def transfusion(transfusion_id):
    c=ctx(BEDSIDE,'bedside_staff_ids'); row=loop.scoped(BloodTransfusion,c['clinic_id'],transfusion_id)
    from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
    order=loop.scoped(BloodProductOrder,c['clinic_id'],row.blood_product_order_id)
    if row.patient_id!=order.patient_id or row.admission_id!=order.admission_id:
        from app.core.exceptions import NotFoundError
        raise NotFoundError('Transfusion not found for this actor')
    if row.administered_by_staff_id!=c['actor_staff_id']:
        actor=bank._get_staff(c['actor_staff_id'],c['clinic_id'],allowed_roles=BEDSIDE,role_message='Bedside access denied')
        loop.require_bedside_assignment(actor,order)
    return jsonify(_serialize(S.BloodTransfusionResponseSchema,row))


@blood_bank_bp.post('/recalls')
@role_required(*LAB)
def recall():
    rows=loop.recall_components(**ctx(LAB),**RecallRequest.model_validate(_json_body()).model_dump())
    return jsonify({'recall_ids':[x.id for x in rows],'affected_component_ids':[x.blood_unit_id for x in rows]})


@blood_bank_bp.get('/recalls')
@role_required(*LAB)
def recalls():
    c=ctx(LAB,'recall_staff_ids'); rows=db.session.execute(select(BloodRecallEvidence).where(BloodRecallEvidence.clinic_id==c['clinic_id']).order_by(BloodRecallEvidence.id.desc())).scalars().all()
    return jsonify({'items':[{'id':r.id,'blood_unit_id':r.blood_unit_id,'affected_order_id':r.affected_order_id,'affected_patient_id':r.affected_patient_id,'previous_status':r.previous_status,'reason':r.reason,'created_at':r.created_at.isoformat()} for r in rows]})


class ReasonRequest(Request):
    reason:str=Field(min_length=1,max_length=5000)

class PartialCloseRequest(OrderVersion):
    reason:str=Field(min_length=1,max_length=5000)

@blood_bank_bp.post('/suppliers/<int:supplier_id>/deactivate')
@role_required(Role.ADMIN,Role.LAB_TECHNICIAN)
def deactivate_supplier(supplier_id):
    row=loop.deactivate_supplier(supplier_id,**ctx((Role.ADMIN,Role.LAB_TECHNICIAN)),**ReasonRequest.model_validate(_json_body()).model_dump())
    return jsonify(supplier_dict(row))

@blood_bank_bp.post('/orders/<int:order_id>/close-partial')
@role_required(Role.DOCTOR)
def close_partial(order_id):
    from app.modules.clinical_orders.schemas.blood_product_order_schema import BloodProductOrderResponseSchema
    return call(loop.close_partial_order,PartialCloseRequest,(Role.DOCTOR,),BloodProductOrderResponseSchema,order_id)


# Register additional routes after all shared helpers are defined.
from app.modules.blood_bank.routes import acceptance_routes  # noqa: E402,F401
