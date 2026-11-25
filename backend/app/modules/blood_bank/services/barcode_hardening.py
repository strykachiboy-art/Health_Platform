"""Live, single-use bedside verification and append-only hemovigilance."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.emar_enums import BarcodeVerificationResult as Result
from app.core.enums.blood_bank_enums import BloodBarcodeFailureCode as Failure, TransfusionReactionType, TransfusionReactionSeverity
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.modules.blood_bank.models.blood_bank_model import BloodUnit, BloodTransfusion, BloodTransfusionBarcodeEvent
from app.modules.blood_bank.models_extra import BloodReactionEvidence
from app.modules.blood_bank.services import closed_loop as loop
from app.modules.blood_bank import policy as p
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.patient.models.patient_model import Patient

ROLES = (Role.NURSE, Role.DOCTOR)


def _barcode(value):
    if not isinstance(value,str) or len(value)>255:
        raise ValidationError('Barcode must be a bounded string')
    return value.strip()


def _match(event, actor, patient, unit, scan_time, device, order_id):
    return (event.actor_staff_id == actor.id and event.patient_barcode == patient and event.unit_barcode == unit
            and p.db_utc(event.scan_timestamp) == scan_time and event.scan_device == device
            and event.requested_order_id == order_id)


def verify(*, clinic_id, actor_staff_id, patient_barcode, unit_barcode, scan_timestamp, scan_device,
           operation_identity, blood_product_order_id=None):
    actor, policy = loop.authority(clinic_id,actor_staff_id,'bedside_staff_ids',ROLES)
    patient_code, unit_code = _barcode(patient_barcode), _barcode(unit_barcode)
    ts=loop.validate(p.utc,scan_timestamp)
    device=loop.validate(p.text,scan_device,'scan_device',255)
    op=loop.validate(p.text,operation_identity,'operation_identity',255)
    if blood_product_order_id is not None:
        loop.validate(p.positive_int,blood_product_order_id,'blood_product_order_id')
    existing_q=select(BloodTransfusionBarcodeEvent).where(BloodTransfusionBarcodeEvent.clinic_id==clinic_id,
             BloodTransfusionBarcodeEvent.operation_identity==op)
    old=db.session.execute(existing_q).scalar_one_or_none()
    if old is not None:
        if not _match(old,actor,patient_code,unit_code,ts,device,blood_product_order_id):
            raise ConflictError('Barcode operation identity already bound to another request')
        # Returning history is not a fresh authorization; start independently revalidates.
        return old
    patient=unit=order=issue=None
    failure=None; reason=None
    try:
        loop.validate(p.fresh_scan,policy,ts,loop.now())
    except ValidationError:
        failure,reason=Failure.STALE_VERIFICATION,'Scan timestamp is outside the approved live window'
    if failure is None and (not patient_code or len(patient_code)>128):
        failure,reason=Failure.MALFORMED_PATIENT_BARCODE,'Invalid patient barcode'
    if failure is None and (not unit_code or len(unit_code)>128):
        failure,reason=Failure.MALFORMED_UNIT_BARCODE,'Invalid component barcode'
    if failure is None:
        patient=db.session.execute(select(Patient).where(Patient.clinic_id==clinic_id,Patient.barcode==patient_code)).scalar_one_or_none()
        if patient is None:
            failure,reason=Failure.UNKNOWN_PATIENT_BARCODE,'Patient barcode not found in effective clinic'
    if failure is None:
        unit=db.session.execute(select(BloodUnit).where(BloodUnit.clinic_id==clinic_id,BloodUnit.unit_barcode==unit_code)).scalar_one_or_none()
        if unit is None:
            failure,reason=Failure.UNKNOWN_UNIT_BARCODE,'Component barcode not found in effective clinic'
    if failure is None:
        oid=blood_product_order_id or unit.issued_to_order_id
        if oid is None:
            failure,reason=Failure.UNIT_NOT_ISSUED_TO_ORDER,'Component is not issued'
        else:
            try:
                order=loop.scoped(BloodProductOrder,clinic_id,oid,lock=True)
                unit=loop.scoped(BloodUnit,clinic_id,unit.id,lock=True)
            except NotFoundError:
                failure,reason=Failure.ORDER_NOT_VERIFIED,'Order or component not found in effective clinic'
    if failure is None and patient.id != order.patient_id:
        failure,reason=Failure.WRONG_PATIENT,'Patient wristband does not match the order'
    if failure is None:
        try:
            _,issue=loop.assert_issued_context(order,unit,policy,lock_admission=True)
        except (ConflictError,ValidationError,NotFoundError):
            failure,reason=Failure.ORDER_NOT_VERIFIED,'Current issue, clearance, compatibility or admission evidence is invalid'
    if failure is None:
        try:
            loop.require_bedside_assignment(actor,order)
        except ConflictError:
            failure,reason=Failure.UNAUTHORIZED_ACTOR,'Current admission care assignment is required'
            patient=unit=order=issue=None
    ok=failure is None
    event=BloodTransfusionBarcodeEvent(clinic_id=clinic_id,
        patient_id=order.patient_id if order else None,admission_id=order.admission_id if order else None,
        blood_product_order_id=order.id if order else None,blood_unit_id=unit.id if unit else None,
        actor_staff_id=actor.id,scanned_patient_id=patient.id if patient else None,scanned_unit_id=unit.id if unit else None,
        patient_barcode=patient_code,unit_barcode=unit_code,scan_timestamp=ts,scan_device=device,
        verification_result=Result.BARCODE_VERIFIED if ok else Result.NOT_BARCODE_VERIFIED,
        failure_code=failure,failure_reason=reason,patient_match=bool(order and patient and order.patient_id==patient.id),
        unit_match=bool(order and unit and unit.issued_to_order_id==order.id),blood_type_match=ok,expiry_match=ok,order_status_match=ok,
        expected_patient_blood_type=order.patient_blood_type_verified if order else None,scanned_unit_blood_type=unit.blood_type if unit else None,
        source_order_version=order.version if order else None,source_unit_version=unit.version if unit else None,
        policy_reference=loop.validate(p.policy_version,policy),verification_policy_version=2,operation_identity=op,
        requested_order_id=blood_product_order_id,issued_unit_evidence_id=issue.id if issue else None,created_at=loop.now())
    # Per-clinic operation identity is authoritative even under concurrent requests.
    try:
        with db.session.begin_nested():
            db.session.add(event); db.session.flush()
    except IntegrityError as exc:
        old=db.session.execute(existing_q).scalar_one_or_none()
        if old is None or not _match(old,actor,patient_code,unit_code,ts,device,blood_product_order_id):
            raise ConflictError('Barcode operation conflict') from exc
        return old
    loop.audit(actor,event,'Recorded live bedside barcode verification',{'result':event.verification_result.value,'failure':failure.value if failure else None})
    return event


def start(*,clinic_id,actor_staff_id,blood_product_order_id,blood_unit_id,barcode_verification_event_id,pre_vitals=None,notes=None):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'bedside_staff_ids',ROLES)
    order=loop.scoped(BloodProductOrder,clinic_id,blood_product_order_id,lock=True)
    unit=loop.scoped(BloodUnit,clinic_id,blood_unit_id,lock=True)
    event=loop.scoped(BloodTransfusionBarcodeEvent,clinic_id,barcode_verification_event_id,lock=True)
    admission,issue=loop.assert_issued_context(order,unit,policy,lock_admission=True)
    loop.require_bedside_assignment(actor,order)
    if (event.verification_result!=Result.BARCODE_VERIFIED or event.actor_staff_id!=actor.id or
        event.patient_id!=order.patient_id or event.scanned_patient_id!=order.patient_id or event.admission_id!=admission.id or
        event.blood_product_order_id!=order.id or event.blood_unit_id!=unit.id or event.scanned_unit_id!=unit.id or
        event.source_order_version!=order.version or event.source_unit_version!=unit.version or
        event.issued_unit_evidence_id!=issue.id or event.policy_reference!=loop.validate(p.policy_version,policy) or
        event.verification_policy_version!=2):
        raise ConflictError('Barcode verification is stale or belongs to another clinical context')
    now=loop.now()
    loop.validate(p.fresh_scan,policy,event.scan_timestamp,now)
    loop.validate(p.fresh_scan,policy,event.created_at,now)
    if db.session.execute(select(BloodTransfusion.id).where(BloodTransfusion.clinic_id==clinic_id,
        BloodTransfusion.blood_unit_id==unit.id)).scalar_one_or_none() is not None:
        raise ConflictError('Component verification already consumed')
    if not isinstance(pre_vitals,dict) or not pre_vitals:
        raise ValidationError('Documented pre-transfusion observations required')
    row=BloodTransfusion(clinic_id=clinic_id,patient_id=order.patient_id,admission_id=admission.id,blood_product_order_id=order.id,
        blood_unit_id=unit.id,barcode_verification_event_id=event.id,administered_by_staff_id=actor.id,
        started_at=now,status='in_progress',pre_vitals=pre_vitals,notes=notes)
    unit.status='administration_started'; unit.version+=1
    db.session.add(row); loop.audit(actor,row,'Started verified transfusion; not completed',{'blood_unit_id':unit.id,'barcode_event_id':event.id})
    return row


def finish(transfusion_id,*,clinic_id,actor_staff_id,post_vitals=None,notes=None,stop=False):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'bedside_staff_ids',ROLES)
    row=loop.scoped(BloodTransfusion,clinic_id,transfusion_id,lock=True)
    if row.administered_by_staff_id != actor.id:
        order=loop.scoped(BloodProductOrder,clinic_id,row.blood_product_order_id)
        loop.require_bedside_assignment(actor,order)
    unit=loop.scoped(BloodUnit,clinic_id,row.blood_unit_id,lock=True)
    if row.status!='in_progress' or unit.status!='administration_started':
        raise ConflictError('Transfusion is not in progress')
    if not isinstance(post_vitals,dict) or not post_vitals:
        raise ValidationError('Documented post-transfusion observations required')
    if stop:
        notes=loop.validate(p.text,notes,'stop reason',5000)
    elif unit.recall_blocked:
        raise ConflictError('Recall requires stopping and documenting the transfusion, not ordinary completion')
    row.status='stopped_other' if stop else 'completed'; row.ended_at=loop.now(); row.post_vitals=post_vitals
    if notes is not None: row.notes=loop.validate(p.text,notes,'notes',5000)
    row.version+=1; unit.status='administration_stopped' if stop else 'transfused'; unit.version+=1
    loop.audit(actor,row,'Recorded transfusion end',{'status':row.status,'blood_unit_id':unit.id})
    return row


def reaction(transfusion_id,*,clinic_id,actor_staff_id,reaction_type,reaction_severity,reaction_notes,post_vitals=None):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'bedside_staff_ids',ROLES)
    row=loop.scoped(BloodTransfusion,clinic_id,transfusion_id,lock=True)
    if row.administered_by_staff_id != actor.id:
        order=loop.scoped(BloodProductOrder,clinic_id,row.blood_product_order_id)
        loop.require_bedside_assignment(actor,order)
    # Lock siblings in deterministic order to avoid concurrent lookback lock inversion.
    target=loop.scoped(BloodUnit,clinic_id,row.blood_unit_id)
    q=select(BloodUnit).where(BloodUnit.clinic_id==clinic_id)
    if target.donation_id is not None: q=q.where(BloodUnit.donation_id==target.donation_id)
    else: q=q.where(BloodUnit.external_supplier_id==target.external_supplier_id,BloodUnit.din==target.din)
    units=db.session.execute(q.order_by(BloodUnit.id).with_for_update()).scalars().all()
    unit=next((u for u in units if u.id==target.id),None)
    if unit is None: raise ConflictError('Transfusion component context is invalid')
    try:
        typ=TransfusionReactionType(reaction_type).value; severity=TransfusionReactionSeverity(reaction_severity).value
    except ValueError as exc: raise ValidationError('Invalid reaction classification') from exc
    if typ=='none' or severity=='none': raise ValidationError('Reaction requires a real classification and severity')
    note=loop.validate(p.text,reaction_notes,'reaction notes',5000)
    evidence=BloodReactionEvidence(clinic_id=clinic_id,transfusion_id=row.id,actor_staff_id=actor.id,reaction_type=typ,
        severity=severity,notes=note,vitals=post_vitals)
    db.session.add(evidence); db.session.flush()
    if row.status=='in_progress':
        row.status='stopped_reaction'; row.ended_at=loop.now(); unit.status='administration_stopped'; unit.version+=1
    if not row.reaction_reported:
        row.reaction_reported=True; row.reaction_type=typ; row.reaction_severity=severity; row.reaction_notes=note
        row.reaction_reported_at=loop.now(); row.reaction_reported_by_staff_id=actor.id
    row.version+=1
    # Conservative hold for every suspected reaction; clinical investigation decides disposition.
    row.lookback_triggered=True
    blocked=[]
    for u in units:
        if loop.recall_locked(u,actor,'Suspected transfusion reaction; investigation required',evidence): blocked.append(u)
    loop.audit(actor,row,'Appended transfusion reaction evidence and lookback hold',{'reaction_evidence_id':evidence.id,'blocked_unit_ids':[x.id for x in blocked]})
    return row,blocked
