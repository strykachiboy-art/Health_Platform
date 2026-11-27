"""Synthetic-only reporting lifecycle over existing RIS, CT and amendment records."""
from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import ClinicalRecordType, ClinicalRecordStatus
from app.core.enums.notification_enums import NotificationChannel, NotificationType
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.core.auth.user.models.user_model import User
from app.core.notifications.services.notification_service import create_notification_in_transaction
from app.modules.clinical_records.services import clinical_record_service as records
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
from app.modules.patient.models.patient_model import Patient
from app.modules.lab.services import lab_release_service as lab
from app.modules.radiology.models.ris_model import (RadiologyExam, RadiologyReport, RadiologyReportVersion, RadiologyStagedReport, RadiologyStagedReportRelease, RadiologyStagedCTStudy, RadiologyStudyAssociation, RadiologyWorklistPublication)
from app.modules.radiology.services import radc_ct_service as ct, radc_worklist_service as local, ris_service as ris, radc_delivery_service as delivery
from app.modules.printing.services.print_policy import authorize, build_document, PrintDenied
from app.modules.printing.services.print_renderer import render_pdf
from app.modules.printing.services.branding_payload import render_authorized

TYPE=ClinicalRecordType.STAGED_RADIOLOGY_REPORT
LIMIT=50


def _enabled():
    local._enabled()
    if not current_app.testing or current_app.config.get('RAD_C_REPORT_STAGED_ENABLED') is not True:
        raise NotFoundError('Staged radiology reporting held')


def _text(value):
    if not isinstance(value,str) or not value.startswith('SYNTHETIC ') or len(value)>12000 or value!=value.strip() or not value.isascii() or any(ord(c)<32 and c not in '\n\t' for c in value):
        raise ValidationError('Bounded synthetic report text required')
    return value


def _version(value,expected):
    if type(value) is not int or value!=expected:
        raise ConflictError('Stale staged report version')


def _ct(args):
    _enabled()
    archive,exam,actor,pub=ct._context(**args)
    row=db.session.get(RadiologyStagedCTStudy,pub.id)
    assoc=db.session.get(RadiologyStudyAssociation,row.association_id) if row else None
    if (row is None or row.state!='staged_validated' or row.archive_key!=archive.archive_key
            or not isinstance(row.manifest,list) or not 1<=len(row.manifest)==row.instance_count<=16
            or local._digest(row.manifest)!=row.manifest_sha256 or assoc is None
            or assoc.exam_id!=exam.id or assoc.source!='radc_staged_ct' or assoc.match_status!='unverified'
            or assoc.study_instance_uid!=pub.payload['study_instance_uid']):
        raise NotFoundError('Staged CT evidence unavailable')
    try:
        if ct._snapshot(archive,pub.payload)[0]!=row.manifest:
            raise ct.InvalidStudy('CT content changed')
    except ct.InvalidStudy as exc:
        raise NotFoundError('Staged CT evidence unavailable') from exc
    order=ris._order(exam.imaging_order_id,args['clinic_id'])
    return exam,order,actor,pub,row


def _draft(exam):
    report=ris._report_for_exam(exam.id,lock=True)
    if report is None or report.status!='private_draft': raise NotFoundError('RIS working note unavailable')
    return report


def _revision(report):
    v=db.session.execute(select(RadiologyReportVersion).where(RadiologyReportVersion.report_id==report.id,RadiologyReportVersion.revision==report.version)).scalar_one_or_none()
    if v is None or ris._digest(v.content)!=v.content_sha256: raise ConflictError('Draft integrity unavailable')
    _text(v.content)
    return v


def _snapshot(exam,order,report,v,pub,row):
    return dict(exam_id=exam.id,order_id=order.id,clinic_id=order.clinic_id,patient_id=order.patient_id,report_id=report.id,
        draft_revision=v.revision,publication_id=pub.id,manifest_sha256=row.manifest_sha256,study_instance_uid=pub.payload['study_instance_uid'],
        report_text=v.content,modality='ct',synthetic_only=True)


def _payload(snap,version,text):
    return dict(snap,report_text=text,record_version=version)


def _chain(exam,order,report,staged):
    if staged is None or staged.report_id!=report.id or staged.draft_revision!=report.version:
        raise NotFoundError('Staged report unavailable')
    pub=db.session.get(RadiologyWorklistPublication,staged.publication_id)
    row=db.session.get(RadiologyStagedCTStudy,staged.publication_id)
    if (pub is None or row is None or row.state!='staged_validated' or pub.exam_id!=exam.id
            or not isinstance(pub.payload,dict) or local._digest(pub.payload)!=pub.payload_sha256
            or not isinstance(row.manifest,list) or not 1<=len(row.manifest)==row.instance_count<=16 or local._digest(row.manifest)!=row.manifest_sha256):
        raise ConflictError('Staged source integrity unavailable')
    assoc=db.session.get(RadiologyStudyAssociation,row.association_id)
    if assoc is None or assoc.exam_id!=exam.id or assoc.match_status!='unverified' or assoc.source!='radc_staged_ct' or assoc.study_instance_uid!=pub.payload['study_instance_uid'] or pub.payload.get('patient_id')!=order.patient_id or pub.payload.get('clinic_id')!=order.clinic_id:
        raise ConflictError('Staged identity unavailable')
    v=_revision(report);snap=dict(_snapshot(exam,order,report,v,pub,row),finalized_by_staff_id=staged.finalized_by_staff_id,finalized_at=lab._time(staged.finalized_at))
    if staged.snapshot!=snap or local._digest(snap)!=staged.snapshot_sha256:
        raise ConflictError('Staged report integrity unavailable')
    history=records.get_clinical_record_amendments(clinic_id=order.clinic_id,source_record_type=TYPE,source_record_id=report.id)
    if len(history)>=LIMIT: raise ConflictError('Staged amendment limit')
    previous=None;current=_payload(snap,1,v.content)
    for i,a in enumerate(history,2):
        content=a.new_content
        if (a.patient_id!=order.patient_id or a.previous_version!=i-1 or a.new_version!=i or a.previous_amendment_id!=(previous.id if previous else None)
                or not isinstance(content,dict) or set(content)!={'report_text'} or records._canonical_content(content)[1]!=a.new_content_sha256
                or _text(content['report_text'])==current['report_text']): raise ConflictError('Staged amendment integrity unavailable')
        current=_payload(snap,i,content['report_text']);previous=a
    return current


def _staff_state(args):
    exam,order,actor,pub,row=_ct(args);report=_draft(exam);staged=db.session.get(RadiologyStagedReport,report.id)
    current=_chain(exam,order,report,staged)
    if staged.publication_id!=pub.id: raise ConflictError('Staged study changed')
    return exam,order,actor,report,staged,current


def _active(report):
    return db.session.execute(select(RadiologyStagedReportRelease).where(RadiologyStagedReportRelease.report_id==report.id,RadiologyStagedReportRelease.withdrawn_at.is_(None))).scalar_one_or_none()


def _receipt(current,active=None):
    # Deliberately exclude image identifiers and original draft contents from other versions.
    return dict(exam_id=current['exam_id'],report_id=current['report_id'],record_version=current['record_version'],report_text=current['report_text'],modality='ct',synthetic_only=True,
        staged_finalized=True,finalized_at=current['finalized_at'],staged_patient_released=active is not None,diagnostic_report_signed=False,archive_verified=False,released_to_chart=False)


@transactional
def preview(**args):
    exam,order,actor,pub,row=_ct(args);report=_draft(exam)
    if db.session.get(RadiologyStagedReport,report.id): raise ConflictError('Staged report already finalized')
    snap=_snapshot(exam,order,report,_revision(report),pub,row)
    return dict(draft_revision=report.version,report_text=snap['report_text'],preview_sha256=local._digest(dict(snapshot=snap,reviewer=actor.id)),synthetic_only=True,diagnostic_report_signed=False)


@transactional
def finalize(*,expected_version,preview_sha256,**args):
    exam,order,actor,pub,row=_ct(args);report=_draft(exam);_version(expected_version,report.version)
    if db.session.get(RadiologyStagedReport,report.id): raise ConflictError('Staged report already finalized')
    v=_revision(report);snap=_snapshot(exam,order,report,v,pub,row)
    if preview_sha256!=local._digest(dict(snapshot=snap,reviewer=actor.id)): raise ConflictError('Staged review changed')
    records.validate_finalization_transition(ClinicalRecordStatus.DRAFT)
    stamp=delivery.now();snap=dict(snap,finalized_by_staff_id=actor.id,finalized_at=lab._time(stamp))
    db.session.add(RadiologyStagedReport(report_id=report.id,publication_id=pub.id,draft_revision=v.revision,snapshot=snap,snapshot_sha256=local._digest(snap),finalized_by_staff_id=actor.id,finalized_at=stamp))
    db.session.flush();ris._audit(actor,exam.id,AuditAction.CREATE,'Synthetic report finalized for staged exercise',{'report_id':report.id,'snapshot_sha256':local._digest(snap)})
    return _receipt(_payload(snap,1,v.content))


@transactional
def status(**args):
    _,_,_,report,_,current=_staff_state(args)
    row=_active(report)
    if row is not None and (row.version!=current['record_version'] or row.content_sha256!=lab._digest(current,row.recipient_user_id)):
        raise ConflictError('Staged release integrity unavailable')
    return _receipt(current,row)


@transactional
def amend(*,expected_version,report_text,reason,**args):
    exam,order,actor,report,_,current=_staff_state(args);_version(expected_version,current['record_version'])
    text=_text(report_text)
    if _active(report) or text==current['report_text'] or current['record_version']>=LIMIT or not isinstance(reason,str) or not 5<=len(reason.strip())<=1000: raise ConflictError('Withdraw active copy or review amendment')
    a=records.append_clinical_record_amendment(clinic_id=order.clinic_id,patient_id=order.patient_id,source_record_type=TYPE,source_record_id=report.id,current_status=ClinicalRecordStatus.FINAL if expected_version==1 else ClinicalRecordStatus.AMENDED,previous_version=expected_version,amended_by_staff_id=actor.id,reason=reason,new_content={'report_text':text})
    return _receipt(dict(current,record_version=a.new_version,report_text=text))


def _recipient(order,report):
    user=lab._recipient(order,lock=True)
    recipients=db.session.execute(select(RadiologyStagedReportRelease.recipient_user_id).where(RadiologyStagedReportRelease.report_id==report.id).distinct()).scalars().all()
    if recipients and recipients!=[user.id]:raise ConflictError('Staged recipient changed; manual review required')
    return user


def _release_context(args):
    exam,order,actor,report,_,current=_staff_state(args)
    ris._owner(order,actor)  # Explicit release requires both ordering authority and specialist grant.
    return exam,order,actor,report,current


@transactional
def release_preview(**args):
    _,order,_,report,current=_release_context(args);recipient=_recipient(order,report)
    if _active(report) or db.session.execute(select(RadiologyStagedReportRelease.id).where(RadiologyStagedReportRelease.report_id==report.id,RadiologyStagedReportRelease.version==current['record_version'])).first(): raise ConflictError('Staged version already has release decision')
    return dict(_receipt(current),preview_sha256=lab._digest(current,recipient.id))


@transactional
def publish(*,preview_sha256,**args):
    exam,order,actor,report,current=_release_context(args);recipient=_recipient(order,report)
    if _active(report) or db.session.execute(select(RadiologyStagedReportRelease.id).where(RadiologyStagedReportRelease.report_id==report.id,RadiologyStagedReportRelease.version==current['record_version'])).first() or preview_sha256!=lab._digest(current,recipient.id): raise ConflictError('Staged release changed')
    row=RadiologyStagedReportRelease(report_id=report.id,version=current['record_version'],recipient_user_id=recipient.id,content_sha256=preview_sha256,released_by_staff_id=actor.id)
    db.session.add(row);db.session.flush()
    create_notification_in_transaction(clinic_id=order.clinic_id,user_id=recipient.id,title='A test record is available',message='Sign in to view your staged test record.',notification_type=NotificationType.GENERAL,channel=NotificationChannel.IN_APP,reference_type='staged_radiology_report',reference_id=exam.id,actor_user_id=actor.user_id)
    ris._audit(actor,exam.id,AuditAction.UPDATE,'Staged synthetic patient copy released',{'report_id':report.id,'record_version':row.version})
    return _receipt(current,row)


@transactional
def withdraw(*,expected_version,**args):
    # Safety withdrawal remains possible if the archive is unavailable or the
    # specialist grant was revoked. Only the active ordering doctor can act.
    _enabled();exam,order,actor=local._context(args['exam_id'],args['clinic_id'],args['actor_staff_id'],lock=True)
    report=_draft(exam);staged=db.session.get(RadiologyStagedReport,report.id);row=_active(report)
    if staged is None or staged.publication_id!=args['publication_id'] or row is None: raise ConflictError('No current staged copy')
    _version(expected_version,row.version)
    row.withdrawn_at=delivery.now();row.withdrawn_by_staff_id=actor.id
    ris._audit(actor,exam.id,AuditAction.UPDATE,'Staged synthetic patient copy withdrawn',{'report_id':report.id,'record_version':row.version})
    return {'exam_id':exam.id,'record_version':row.version,'staged_patient_released':False,'synthetic_only':True,'diagnostic_report_signed':False,'released_to_chart':False}


def _patient(user_id,clinic_id):
    _enabled();ensure_clinic_active(clinic_id)
    user=db.session.get(User,user_id)
    if user is None or not user.is_active or user.role!=Role.PATIENT or user.clinic_id!=clinic_id: raise NotFoundError('Staged copy unavailable')
    patient=user.patient
    if patient is None or not patient.is_active or patient.clinic_id!=clinic_id or patient.user_id!=user_id: raise NotFoundError('Staged copy unavailable')
    return patient


def _patient_copy(user_id,clinic_id,exam_id):
    patient=_patient(user_id,clinic_id)
    exam=db.session.execute(select(RadiologyExam).join(ImagingOrder,RadiologyExam.imaging_order_id==ImagingOrder.id).where(RadiologyExam.id==exam_id,ImagingOrder.clinic_id==clinic_id,ImagingOrder.patient_id==patient.id).with_for_update(of=RadiologyExam)).scalar_one_or_none()
    if exam is None: raise NotFoundError('Staged copy unavailable')
    order=ris._order(exam.imaging_order_id,clinic_id)
    if order.status=='cancelled': raise NotFoundError('Staged copy unavailable')
    report=_draft(exam);staged=db.session.get(RadiologyStagedReport,report.id);row=_active(report)
    if row is None or row.recipient_user_id!=user_id: raise NotFoundError('Staged copy unavailable')
    current=_chain(exam,order,report,staged)
    if row.version!=current['record_version'] or row.content_sha256!=lab._digest(current,user_id): raise NotFoundError('Staged copy unavailable')
    return order,current,row


@transactional
def patient_detail(*,user_id,clinic_id,exam_id):
    _,current,row=_patient_copy(user_id,clinic_id,exam_id)
    return dict(_receipt(current,row),released_at=lab._time(row.released_at))


@transactional
def patient_list(*,user_id,clinic_id):
    patient=_patient(user_id,clinic_id)
    ids=db.session.execute(select(RadiologyExam.id).join(ImagingOrder,RadiologyExam.imaging_order_id==ImagingOrder.id).join(RadiologyReport,RadiologyReport.exam_id==RadiologyExam.id).join(RadiologyStagedReportRelease,RadiologyStagedReportRelease.report_id==RadiologyReport.id).where(ImagingOrder.clinic_id==clinic_id,ImagingOrder.patient_id==patient.id,RadiologyStagedReportRelease.recipient_user_id==user_id,RadiologyStagedReportRelease.withdrawn_at.is_(None)).order_by(RadiologyExam.id.desc()).limit(50)).scalars().all()
    items=[]
    for eid in ids:
        _,current,row=_patient_copy(user_id,clinic_id,eid)
        items.append(dict(exam_id=eid,record_version=current['record_version'],released_at=lab._time(row.released_at),synthetic_only=True))
    return {'items':items,'synthetic_only':True}


@transactional
def patient_pdf(*,user_id,clinic_id,exam_id):
    if current_app.config.get('RAD6_PRINTING_ENABLED') is not True: raise PrintDenied('Print unavailable')
    order,current,row=_patient_copy(user_id,clinic_id,exam_id)
    authorize(kind='staged_radiology_copy',actor_clinic_id=clinic_id,record_clinic_id=order.clinic_id,actor_role='PATIENT')
    doc=build_document(kind='staged_radiology_copy',clinic_id=clinic_id,record_id=exam_id,source_version=current['record_version'],source={'patient_id':order.patient_id,'released_at':lab._time(row.released_at),'report_text':current['report_text']})
    data, branding_audit = render_authorized(doc, render_pdf, owning_clinic_id=order.clinic_id)
    from app.core.audit.services.audit_service import create_audit_log
    create_audit_log(action=AuditAction.VIEW,entity_type='RadiologyExam',entity_id=exam_id,clinic_id=clinic_id,patient_id=order.patient_id,user_id=user_id,description='Downloaded synthetic staged radiology copy',new_value={'record_version':doc.source_version,'source_digest':doc.source_digest,**branding_audit})
    return data,doc
