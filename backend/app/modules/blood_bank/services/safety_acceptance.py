"""Durable clinical safety work, in the same transaction as the clinical event.
No automatic clearance or outbound delivery. No donor details in clinical alerts.
"""
from sqlalchemy import select,func
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.notification_enums import NotificationType,NotificationPriority
from app.core.exceptions import ConflictError, ValidationError,NotFoundError
from app.core.utils.decorators import transactional
from app.modules.blood_bank import policy as p
from app.modules.blood_bank.models.blood_bank_model import BloodUnit,BloodDonation,BloodDonor,BloodTransfusion
from app.modules.blood_bank.models_extra import BloodRecallEvidence
from app.modules.blood_bank.models_acceptance import BloodInvestigationEvent,BloodSafetyInbox,BloodLabArtifact,BloodLabVerification
from app.modules.blood_bank.services import closed_loop as loop
from app.modules.clinical_orders.models.blood_product_order_model import BloodProductOrder
from app.modules.nursing.models.admission_care_assignment_model import AdmissionCareAssignment
from app.modules.staff.models.staff_model import Staff
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access,TenantAccessDenied


def notify(actor,policy,key,kind,message,*,recall=None,order=None):
    # Alerts go only to active configured safety staff and active care assignments.
    clinic_id=actor.clinic_id
    configured=policy.get('recall_staff_ids',()) if kind!='stock_shortage' else policy.get('stock_alert_staff_ids',())
    ids=set(x for x in configured if type(x) is int and x>0)
    if order is not None:
        ids.update(db.session.execute(select(AdmissionCareAssignment.staff_id).where(
            AdmissionCareAssignment.clinic_id==clinic_id,AdmissionCareAssignment.admission_id==order.admission_id,
            AdmissionCareAssignment.patient_id==order.patient_id,AdmissionCareAssignment.ended_at.is_(None))).scalars())
        ids.add(order.ordered_by_staff_id)
    for sid in sorted(ids):
        staff=db.session.execute(select(Staff).where(Staff.clinic_id==clinic_id,Staff.id==sid,Staff.status==StaffStatus.ACTIVE)).scalar_one_or_none()
        if staff is None or staff.user is None or not staff.user.is_active or staff.user.clinic_id!=clinic_id or staff.user.role not in (Role.DOCTOR,Role.NURSE,Role.LAB_TECHNICIAN,Role.ADMIN):continue
        # Require genuine tenant/clinic grant before creating any recipient record.
        try:
            clinic=db.session.get(Clinic,clinic_id)
            require_tenant_clinic_access(user_id=staff.user_id,organization_id=clinic.organization_id,clinic_id=clinic_id)
        except TenantAccessDenied:
            continue
        if kind=='stock_shortage' and staff.user.role not in (Role.ADMIN,Role.LAB_TECHNICIAN):continue
        row=db.session.execute(select(BloodSafetyInbox).where(BloodSafetyInbox.clinic_id==clinic_id,BloodSafetyInbox.event_key==key,BloodSafetyInbox.recipient_staff_id==sid).with_for_update()).scalar_one_or_none()
        if row is None:
            from sqlalchemy.exc import IntegrityError
            try:
                with db.session.begin_nested():
                    row=BloodSafetyInbox(clinic_id=clinic_id,event_key=key,kind=kind,recipient_staff_id=sid,recall_id=recall.id if recall else None,order_id=order.id if order else None,message=message)
                    db.session.add(row);db.session.flush()
            except IntegrityError:
                row=db.session.execute(select(BloodSafetyInbox).where(BloodSafetyInbox.clinic_id==clinic_id,BloodSafetyInbox.event_key==key,BloodSafetyInbox.recipient_staff_id==sid).with_for_update()).scalar_one()
        if row.notification_id is not None:continue
        from app.core.notifications.services.notification_service import create_notification_in_transaction
        db.session.flush()
        # A notification-projection error must not roll back the physical safety hold.
        try:
            with db.session.begin_nested():
                n=create_notification_in_transaction(clinic_id=clinic_id,user_id=staff.user_id,title='Blood bank safety review required',message='Open your authorized blood bank safety inbox for details.',notification_type=NotificationType.GENERAL,priority=NotificationPriority.URGENT,reference_type='BloodSafetyInbox',reference_id=row.id,actor_user_id=actor.user_id)
                row.notification_id=n.id
                db.session.flush()
        except Exception:
            # Inbox remains durable and visible; no delivery success is claimed.
            from flask import current_app
            current_app.logger.warning('blood_bank.notification_projection_pending')
    db.session.flush()


def open_event(actor,*,donation_id=None,recall_id=None,artifact_id=None,key,notes):
    row=BloodInvestigationEvent(clinic_id=actor.clinic_id,donation_id=donation_id,recall_id=recall_id,artifact_id=artifact_id,operation_identity=key,outcome='opened',notes=notes,actor_staff_id=actor.id)
    db.session.add(row);loop.audit(actor,row,'Opened blood safety investigation',{'outcome':'opened'})
    return row


def on_recall(row,actor):
    policy=loop.policy_for(row.clinic_id)
    open_event(actor,recall_id=row.id,key=f'recall:{row.id}',notes='Component blocked; investigation and recipient review required')
    order=loop.scoped(BloodProductOrder,row.clinic_id,row.affected_order_id) if row.affected_order_id else None
    notify(actor,policy,f'recall:{row.id}','recall','Component recall requires review; do not administer affected components.',recall=row,order=order)


def observe_artifact(row,subject,actor,policy):
    # A contradictory or adverse result must never silently permit re-release.
    if row.kind not in ('screening','blood_group','compatibility','special_requirements','emergency_assessment','confirmatory'):return
    if row.kind=='screening': adverse=row.result!='non_reactive'
    elif row.kind=='blood_group':
        if isinstance(subject,BloodUnit):adverse=subject.blood_type!=row.result
        else:
            donor=loop.scoped(BloodDonor,row.clinic_id,subject.donor_id)
            adverse=subject.tti_screening_status=='cleared' and donor.blood_type!=row.result
    elif row.kind=='confirmatory': adverse=True
    else: adverse=row.result!={'compatibility':'compatible','special_requirements':'requirements_reviewed','emergency_assessment':'authorized'}[row.kind]
    if not adverse:return
    if isinstance(subject,BloodDonation):
        donor=loop.scoped(BloodDonor,row.clinic_id,subject.donor_id,lock=True)
        donor.deferral_status='review_required';donor.deferral_reason='Laboratory evidence requires independent review';donor.deferred_until=None;donor.version+=1
        open_event(actor,donation_id=subject.id,artifact_id=row.id,key=f'artifact:{row.id}',notes='Adverse or confirmatory donor laboratory evidence recorded')
        units=db.session.execute(select(BloodUnit).where(BloodUnit.clinic_id==row.clinic_id,BloodUnit.donation_id==subject.id).order_by(BloodUnit.id).with_for_update()).scalars().all()
        for unit in units:
            if unit.status=='quarantined':unit.recall_blocked=True;unit.version+=1
            else:loop.recall_locked(unit,actor,'Donor laboratory evidence requires investigation')
        notify(actor,policy,f'artifact:{row.id}','screening_review','Donor screening investigation requires authorized laboratory review.')
    elif isinstance(subject,BloodUnit):
        loop.recall_locked(subject,actor,'Component laboratory evidence requires investigation')


@transactional
def record_investigation(*,clinic_id,actor_staff_id,operation_identity,outcome,notes,donation_id=None,recall_id=None,artifact_id=None):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'investigation_staff_ids',(Role.LAB_TECHNICIAN,Role.DOCTOR))
    if (donation_id is None)==(recall_id is None) or outcome not in ('evidence_recorded','escalated','reviewed'):
        raise ValidationError('Investigation subject and documentary outcome required')
    subject=loop.scoped(BloodDonation if donation_id else BloodRecallEvidence,clinic_id,donation_id or recall_id,lock=True)
    if actor.user.role==Role.DOCTOR:
        if donation_id is not None or subject.affected_order_id is None:raise NotFoundError('Investigation not found for this actor')
        order=loop.scoped(BloodProductOrder,clinic_id,subject.affected_order_id)
        loop.require_bedside_assignment(actor,order)
    previous=db.session.execute(select(BloodInvestigationEvent).where(BloodInvestigationEvent.clinic_id==clinic_id,BloodInvestigationEvent.donation_id==donation_id,BloodInvestigationEvent.recall_id==recall_id).order_by(BloodInvestigationEvent.id)).scalars().all()
    if not previous:raise ConflictError('Investigation has not been opened')
    latest_open=max(i for i,x in enumerate(previous) if x.outcome=='opened')
    previous=previous[latest_open:]
    if any(x.outcome=='reviewed' for x in previous):raise ConflictError('Investigation already reviewed; retain historical evidence')
    if outcome=='reviewed':
        if previous[0].actor_staff_id==actor.id or not any(x.outcome=='evidence_recorded' for x in previous) or any(x.outcome=='evidence_recorded' and x.actor_staff_id==actor.id for x in previous):raise ConflictError('Independent review and verified investigation evidence required')
    if outcome=='evidence_recorded':
        art=loop.scoped(BloodLabArtifact,clinic_id,artifact_id)
        if art.kind!='confirmatory' or (donation_id and art.donation_id!=donation_id) or (recall_id and art.blood_unit_id!=subject.blood_unit_id):raise ConflictError('Confirmatory evidence does not match investigation')
        from app.modules.blood_bank.services.lab_evidence import require_artifact
        require_artifact(policy,clinic_id,art.reference,kind=art.kind,marker=art.marker,result=art.result,donation_id=art.donation_id,blood_unit_id=art.blood_unit_id)
    elif artifact_id is not None:raise ValidationError('Artifact is only accepted with evidence-recorded outcome')
    op=loop.validate(p.identity,operation_identity,'operation_identity')
    if db.session.execute(select(BloodInvestigationEvent.id).where(BloodInvestigationEvent.clinic_id==clinic_id,BloodInvestigationEvent.operation_identity==op)).scalar_one_or_none() is not None:raise ConflictError('Investigation operation already recorded')
    row=BloodInvestigationEvent(clinic_id=clinic_id,donation_id=donation_id,recall_id=recall_id,artifact_id=artifact_id,operation_identity=op,outcome=outcome,notes=loop.validate(p.text,notes,'investigation note',5000),actor_staff_id=actor.id)
    db.session.add(row);loop.audit(actor,row,'Recorded investigation evidence; no release authorized',{'outcome':outcome})
    return row


@transactional
def acknowledge(inbox_id,*,clinic_id,actor_staff_id):
    actor=loop.donor._get_staff(actor_staff_id,clinic_id,allowed_roles=(Role.DOCTOR,Role.NURSE,Role.LAB_TECHNICIAN,Role.ADMIN),role_message='Clinical safety inbox access denied')
    loop.policy_for(clinic_id)
    row=loop.scoped(BloodSafetyInbox,clinic_id,inbox_id,lock=True)
    if row.recipient_staff_id!=actor.id:raise NotFoundError('Safety inbox record not found')
    if row.acknowledged_at is None:
        row.acknowledged_at=loop.now();loop.audit(actor,row,'Acknowledged safety notification; no clinical clearance',{})
    return row


@transactional
def evaluate_stock(*,clinic_id,actor_staff_id):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'stock_alert_staff_ids',(Role.ADMIN,Role.LAB_TECHNICIAN))
    # Serialize per clinic; at most one alert per bucket/day/recipient.
    db.session.execute(select(Clinic).where(Clinic.id==clinic_id).with_for_update()).scalar_one()
    summary=loop.donor.get_blood_stock_summary(clinic_id=clinic_id)
    for r in summary['items']:
        if r['stock_level'] in ('low','critical'):
            key=f"stock:{loop.now().date()}:{r['product_type']}:{r['blood_type']}:{r['stock_level']}"
            notify(actor,policy,key,'stock_shortage','Blood component stock threshold reached; review live inventory.')
    return summary


def report(clinic_id):
    loop.policy_for(clinic_id)
    def counts(model,field):
        return {str(k):v for k,v in db.session.execute(select(field,func.count(model.id)).where(model.clinic_id==clinic_id).group_by(field))}
    return {'as_of':loop.now().isoformat(),'stock':loop.donor.get_blood_stock_summary(clinic_id=clinic_id),
        'unit_status_counts':counts(BloodUnit,BloodUnit.status),'transfusion_status_counts':counts(BloodTransfusion,BloodTransfusion.status),
        'investigation_event_counts':counts(BloodInvestigationEvent,BloodInvestigationEvent.outcome),
        'recall_count':db.session.scalar(select(func.count(BloodRecallEvidence.id)).where(BloodRecallEvidence.clinic_id==clinic_id)),
        'unacknowledged_alerts':db.session.scalar(select(func.count(BloodSafetyInbox.id)).where(BloodSafetyInbox.clinic_id==clinic_id,BloodSafetyInbox.acknowledged_at.is_(None)))}


def require_donor_review(donor):
    donations=db.session.execute(select(BloodDonation.id).where(BloodDonation.clinic_id==donor.clinic_id,BloodDonation.donor_id==donor.id)).scalars().all()
    events=db.session.execute(select(BloodInvestigationEvent).where(BloodInvestigationEvent.clinic_id==donor.clinic_id,BloodInvestigationEvent.donation_id.in_(donations)).order_by(BloodInvestigationEvent.id)).scalars().all()
    latest={}
    for event in events:latest[event.donation_id]=event.outcome
    if (not latest and donor.deferral_status=='review_required') or any(x!='reviewed' for x in latest.values()):raise ConflictError('Independent donor investigation review is required before eligibility change')


@transactional
def reconcile_recall_alerts(*,clinic_id,actor_staff_id):
    actor,policy=loop.authority(clinic_id,actor_staff_id,'recall_staff_ids',(Role.LAB_TECHNICIAN,))
    db.session.execute(select(Clinic).where(Clinic.id==clinic_id).with_for_update()).scalar_one()
    recalls=db.session.execute(select(BloodRecallEvidence).where(BloodRecallEvidence.clinic_id==clinic_id).order_by(BloodRecallEvidence.id)).scalars().all()
    for recall in recalls:
        order=loop.scoped(BloodProductOrder,clinic_id,recall.affected_order_id) if recall.affected_order_id else None
        notify(actor,policy,f'recall:{recall.id}','recall','Component recall requires review; do not administer affected components.',recall=recall,order=order)
    return {'recalls_reviewed':len(recalls),'external_delivery_certified':False}
