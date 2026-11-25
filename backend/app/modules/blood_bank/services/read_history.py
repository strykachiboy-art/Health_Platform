"""Patient-scoped documentary history. No donor identifiers or screening data."""
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError,NotFoundError
from app.core.utils.decorators import transactional
from app.modules.blood_bank.services import closed_loop as loop
from app.modules.blood_bank.models.blood_bank_model import BloodTransfusion
from app.modules.blood_bank.models_acceptance import BloodPatientRelease
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.patient.models.patient_model import Patient
from app.modules.blood_bank import policy as p


def history_row(t):
    return {'id':t.id,'admission_id':t.admission_id,'order_id':t.blood_product_order_id,
            'started_at':p.db_utc(t.started_at).isoformat(),'ended_at':p.db_utc(t.ended_at).isoformat() if t.ended_at else None,
            'status':t.status,'reaction_reported':t.reaction_reported}


def clinical_history(*,clinic_id,actor_staff_id,patient_id):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'bedside_staff_ids',(Role.DOCTOR,Role.NURSE))
    loop.scoped(Patient,clinic_id,patient_id)
    assigned=select(AdmissionCareAssignment.id).where(AdmissionCareAssignment.clinic_id==clinic_id,
        AdmissionCareAssignment.patient_id==patient_id,AdmissionCareAssignment.admission_id==BloodTransfusion.admission_id,
        AdmissionCareAssignment.staff_id==actor.id,AdmissionCareAssignment.ended_at.is_(None)).exists()
    rows=db.session.scalars(select(BloodTransfusion).where(BloodTransfusion.clinic_id==clinic_id,BloodTransfusion.patient_id==patient_id,
        assigned|(BloodTransfusion.administered_by_staff_id==actor.id)).order_by(BloodTransfusion.started_at.desc(),BloodTransfusion.id.desc()).limit(100)).all()
    return {'items':[history_row(t) for t in rows],'limit':100}


@transactional
def release_patient_history(transfusion_id,*,clinic_id,actor_staff_id,reference):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'patient_release_staff_ids',(Role.DOCTOR,))
    t=loop.scoped(BloodTransfusion,clinic_id,transfusion_id,lock=True)
    order=loop.scoped(BloodProductOrder,clinic_id,t.blood_product_order_id)
    if order.patient_id!=t.patient_id or order.admission_id!=t.admission_id:raise ConflictError('Transfusion record lineage mismatch')
    loop.require_bedside_assignment(actor,order)
    patient=loop.scoped(Patient,clinic_id,t.patient_id)
    if not patient.is_active or not patient.user_id or t.ended_at is None or t.status not in ('completed','stopped_other','stopped_reaction'):
        raise ConflictError('Only reviewed finalized transfusion history can be released to an enrolled patient')
    if db.session.scalar(select(BloodPatientRelease.id).where(BloodPatientRelease.transfusion_id==t.id,BloodPatientRelease.transfusion_version==t.version)) is not None:raise ConflictError('History release already recorded')
    row=BloodPatientRelease(clinic_id=clinic_id,transfusion_id=t.id,patient_id=t.patient_id,recipient_user_id=patient.user_id,
        actor_staff_id=actor.id,reference=loop.validate(p.text,reference,'patient release reference',255),transfusion_version=t.version,snapshot=history_row(t))
    db.session.add(row);loop.audit(actor,row,'Released limited transfusion history to enrolled patient',{'transfusion_id':t.id,'version':t.version})
    return row


def own_history(*,clinic_id,patient_id,user_id):
    loop.policy_for(clinic_id)
    patient=loop.scoped(Patient,clinic_id,patient_id)
    if not patient.is_active or patient.user_id!=user_id:return {"items":[],"limit":100}
    rows=db.session.scalars(select(BloodPatientRelease).join(BloodTransfusion,BloodTransfusion.id==BloodPatientRelease.transfusion_id).where(
        BloodPatientRelease.clinic_id==clinic_id,BloodPatientRelease.patient_id==patient_id,BloodPatientRelease.recipient_user_id==user_id,
        BloodTransfusion.clinic_id==clinic_id,BloodTransfusion.patient_id==patient_id,BloodTransfusion.version==BloodPatientRelease.transfusion_version).order_by(BloodPatientRelease.id.desc()).limit(100)).all()
    return {'items':[r.snapshot for r in rows],'limit':100}
