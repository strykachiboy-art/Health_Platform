"""Staged corrected-result workflow. Original lab items and B01 snapshot are immutable."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from sqlalchemy import select
from app.extensions import db
from app.core.enums.role_enums import Role
from app.core.enums.lab_enums import LabResultFlag, LabOrderStatus
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import ClinicalRecordType, ClinicalRecordStatus
from app.core.enums.notification_enums import NotificationChannel, NotificationType
from app.core.exceptions import ConflictError, ValidationError, NotFoundError
from app.core.utils.decorators import transactional
from app.core.audit.services.audit_service import create_audit_log
from app.core.notifications.services.notification_service import create_notification_in_transaction
from app.modules.clinical_records.services import clinical_record_service as records
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.lab.models.lab_model import LabOrderItem, LabReportPublication
from app.modules.lab.services import lab_release_service as base
from app.modules.lab.services.lab_service import _get_lab_order, _validate_actor_for_order, _resolve_result_flag
from app.modules.staff.models.staff_model import Staff

TYPE = ClinicalRecordType.LAB_REPORT
MAX_VERSIONS = 100


def _conflict():
    return ConflictError('Laboratory correction requires clinical review', code='LAB_CORRECTION_REVIEW_REQUIRED')


def _tech(order_id, clinic_id, user_id):
    base._enabled()
    ensure_clinic_active(clinic_id)
    order = _get_lab_order(order_id, clinic_id, for_update=True)
    staff = db.session.execute(select(Staff).where(Staff.user_id == user_id, Staff.clinic_id == clinic_id)).scalar_one_or_none()
    if staff is None:
        raise NotFoundError('Laboratory report unavailable')
    actor = _validate_actor_for_order(staff.id, order, allowed_roles=(Role.LAB_TECHNICIAN,))
    if actor.user_id != user_id or actor.user.clinic_id != clinic_id:
        raise NotFoundError('Laboratory report unavailable')
    return order, actor


def publications(order):
    return list(db.session.execute(select(LabReportPublication).where(LabReportPublication.order_id == order.id).order_by(LabReportPublication.version)).scalars())


def active_publication(order):
    return db.session.execute(select(LabReportPublication).where(LabReportPublication.order_id == order.id, LabReportPublication.withdrawn_at.is_(None))).scalar_one_or_none()


def _chain(order):
    # Scope and validate the actual shared amendment chain, including all hashes.
    if order.status is not LabOrderStatus.COMPLETED or order.patient_released_at is None or order.patient_release_withdrawn_at is None:
        raise _conflict()
    original = base._original_released(order)
    original = {k: v for k, v in original.items() if k not in ('released_at', 'release_status')}
    history = records.get_clinical_record_amendments(clinic_id=order.clinic_id, source_record_type=TYPE, source_record_id=order.id)
    if len(history) >= MAX_VERSIONS:
        raise _conflict()
    prior = None
    previous = original
    for version, amendment in enumerate(history, 2):
        content = amendment.new_content
        if amendment.patient_id != order.patient_id or amendment.previous_version != version - 1 or amendment.new_version != version or amendment.previous_amendment_id != (prior.id if prior else None):
            raise _conflict()
        if not isinstance(content, dict) or set(content) != {'report', 'correction_verified_at'} or records._canonical_content(content)[1] != amendment.new_content_sha256:
            raise _conflict()
        report = content['report']
        if not isinstance(report, dict) or set(report) != set(original) or any(report[k] != original[k] for k in original if k != 'results') or not isinstance(report['results'], list) or len(report['results']) != len(original['results']):
            raise _conflict()
        try:
            time = datetime.fromisoformat(content['correction_verified_at'])
            if time.tzinfo is None or time < datetime.fromisoformat(base._time(order.patient_release_withdrawn_at)):
                raise _conflict()
        except (TypeError, ValueError):
            raise _conflict() from None
        for old, new in zip(original['results'], report['results']):
            if not isinstance(new, dict) or set(new) != set(old) or any(new[k] != old[k] for k in old if k not in ('result_value','flag')) or new['flag'] not in ('normal','abnormal') or not isinstance(new['result_value'],str) or not new['result_value'].strip() or len(new['result_value']) > 150:
                raise _conflict()
        if report == previous:
            raise _conflict()
        previous = report
        prior = amendment
    return original, history


def _state(order):
    original, history = _chain(order)
    pubs = publications(order)
    if len(pubs) not in (len(history), len(history)-1):
        raise _conflict()
    previous_withdrawal = order.patient_release_withdrawn_at
    for pub, amendment in zip(pubs, history):
        if previous_withdrawal is None or pub.version != amendment.new_version or pub.amendment_id != amendment.id or pub.recipient_user_id != order.patient_release_user_id or pub.content_sha256 != amendment.new_content_sha256:
            raise _conflict()
        if datetime.fromisoformat(amendment.new_content['correction_verified_at']) < datetime.fromisoformat(base._time(previous_withdrawal)) or datetime.fromisoformat(base._time(pub.released_at)) < datetime.fromisoformat(amendment.new_content['correction_verified_at']):
            raise _conflict()
        previous_withdrawal = pub.withdrawn_at
    if len(history) > len(pubs) and (previous_withdrawal is None or datetime.fromisoformat(history[-1].new_content['correction_verified_at']) < datetime.fromisoformat(base._time(previous_withdrawal))):
        raise _conflict()
    return original, history, pubs


def _same_recipient(order, *, lock=False):
    user = base._recipient(order, lock=lock)
    if user.id != order.patient_release_user_id:
        raise _conflict()
    return user


@transactional
def verify_correction(*, order_id, clinic_id, tech_user_id, expected_version, changes, reason):
    order, actor = _tech(order_id, clinic_id, tech_user_id)
    _same_recipient(order, lock=True)
    original, history, pubs = _state(order)
    if len(history) != len(pubs) or (pubs and pubs[-1].withdrawn_at is None):
        raise _conflict()
    withdrawal_reason = pubs[-1].withdrawal_reason if pubs else order.patient_release_withdrawal_reason
    if withdrawal_reason != 'clinical_correction':
        raise _conflict()
    version = len(history) + 1
    if type(expected_version) is not int or expected_version != version or version >= MAX_VERSIONS:
        raise ConflictError('Laboratory report version changed', code='LAB_CORRECTION_STALE_VERSION')
    if not isinstance(reason,str) or not 5 <= len(reason.strip()) <= 1000 or not isinstance(changes,list) or not 1 <= len(changes) <= 100:
        raise ValidationError('Invalid laboratory correction')
    # Map only immutable original item identities; no changes to the order or lab items.
    items = list(db.session.execute(select(LabOrderItem).where(LabOrderItem.order_id==order.id).order_by(LabOrderItem.id).with_for_update()).scalars())
    if len(items) != len(original['results']):
        raise _conflict()
    indexes = {}
    for i,(item,row) in enumerate(zip(items, original['results'])):
        meta = {k: row[k] for k in ('test_name','loinc_code','unit','reference_range')}
        if item.result_display_metadata != meta or item.result_value != row['result_value'] or item.flag is None or item.flag.value != row['flag'] or base._time(item.resulted_at) != row['resulted_at'] or item.result_notes or item.result_file_url:
            raise _conflict()
        indexes[item.id] = i
    report = copy.deepcopy(history[-1].new_content['report'] if history else original)
    before = copy.deepcopy(report)
    seen = set()
    for change in changes:
        if not isinstance(change,dict) or set(change) != {'item_id','result_value','flag'} or type(change['item_id']) is not int or change['item_id'] not in indexes or change['item_id'] in seen:
            raise ValidationError('Invalid laboratory correction item')
        seen.add(change['item_id']); i = indexes[change['item_id']]; item = items[i]; value = change['result_value']
        if not isinstance(value,str) or not 1 <= len(value.strip()) <= 150 or change['flag'] not in ('normal','abnormal'):
            raise ValidationError('Invalid corrected value or classification')
        # Re-evaluate a newly verified correction under the current catalogue only
        # when its display semantics agree with the original report. Critical and
        # ambiguous classifications remain held; no historical metadata guessing.
        test = item.test
        if test is None or test.clinic_id not in (None, order.clinic_id) or {'test_name':test.name,'loinc_code':test.loinc_code,'unit':test.unit,'reference_range':test.reference_range} != item.result_display_metadata:
            raise _conflict()
        supplied = LabResultFlag(change['flag'])
        resolved, _ = _resolve_result_flag(test=test,result_value=value.strip(),supplied_flag=supplied)
        if resolved != supplied:
            raise _conflict()
        report['results'][i]['result_value'] = value.strip()
        report['results'][i]['flag'] = supplied.value
    if report == before:
        raise ValidationError('Correction must change a result')
    now = datetime.now(timezone.utc)
    withdrawn_at = pubs[-1].withdrawn_at if pubs else order.patient_release_withdrawn_at
    if now < datetime.fromisoformat(base._time(withdrawn_at)):
        raise _conflict()
    amendment = records.append_clinical_record_amendment(clinic_id=clinic_id, patient_id=order.patient_id, source_record_type=TYPE, source_record_id=order.id, current_status=ClinicalRecordStatus.AMENDED if history else ClinicalRecordStatus.FINAL, previous_version=version, amended_by_staff_id=actor.id, reason=reason.strip(), new_content={'report':report,'correction_verified_at':base._time(now)})
    return {'order_id':order.id,'record_version':amendment.new_version,'release_status':'correction_pending'}


def _pending(order):
    _, history, pubs = _state(order)
    if len(history) != len(pubs)+1 or (pubs and pubs[-1].withdrawn_at is None):
        raise _conflict()
    if (pubs[-1].withdrawal_reason if pubs else order.patient_release_withdrawal_reason) != 'clinical_correction':
        raise _conflict()
    return history[-1]


def _preview(order, amendment, user_id):
    report = {**amendment.new_content['report'], 'record_version':amendment.new_version, 'correction_verified_at':amendment.new_content['correction_verified_at']}
    return {'report':report,'preview_sha256':base._digest({'report':report,'amendment_id':amendment.id,'content_sha256':amendment.new_content_sha256},user_id)}


@transactional
def preview_replacement(*, order_id, clinic_id, doctor_user_id):
    order, _ = base._doctor_order(order_id, clinic_id, doctor_user_id)
    recipient = _same_recipient(order)
    amendment = _pending(order)
    result = _preview(order, amendment, recipient.id)
    create_audit_log(action=AuditAction.VIEW,entity_type='LabOrder',entity_id=order.id,clinic_id=clinic_id,patient_id=order.patient_id,user_id=doctor_user_id,description='Doctor reviewed corrected laboratory report',new_value={'record_version':amendment.new_version})
    return result


@transactional
def publish_replacement(*, order_id, clinic_id, doctor_user_id, preview_sha256):
    order, doctor = base._doctor_order(order_id, clinic_id, doctor_user_id, lock=True)
    recipient = _same_recipient(order, lock=True)
    amendment = _pending(order)
    if not isinstance(preview_sha256,str) or preview_sha256 != _preview(order,amendment,recipient.id)['preview_sha256']:
        raise ConflictError('Corrected report changed; review again',code='LAB_RELEASE_PREVIEW_CHANGED')
    now = datetime.now(timezone.utc)
    if now < datetime.fromisoformat(amendment.new_content['correction_verified_at']):
        raise _conflict()
    pub = LabReportPublication(order_id=order.id,version=amendment.new_version,amendment_id=amendment.id,recipient_user_id=recipient.id,content_sha256=amendment.new_content_sha256,released_at=now,released_by_id=doctor.id)
    db.session.add(pub); db.session.flush()
    create_audit_log(action=AuditAction.STATUS_CHANGE,entity_type='LabOrder',entity_id=order.id,clinic_id=clinic_id,patient_id=order.patient_id,user_id=doctor_user_id,description='Corrected laboratory report released',new_value={'record_version':pub.version,'publication_id':pub.id})
    create_notification_in_transaction(clinic_id=clinic_id,user_id=recipient.id,title='An updated record is available',message='Sign in to view your updated clinical record.',notification_type=NotificationType.LAB,channel=NotificationChannel.IN_APP,reference_type='released_lab_result',reference_id=order.id,actor_user_id=doctor_user_id)
    return {'order_id':order.id,'record_version':pub.version,'release_status':'released','released_at':base._time(now)}


def withdraw_current(order, doctor, reason_code):
    pub = active_publication(order)
    if pub is None:
        return None
    _, history, pubs = _state(order)
    if not pubs or pub.id != pubs[-1].id or len(history) != len(pubs):
        raise _conflict()
    now = datetime.now(timezone.utc)
    if now < datetime.fromisoformat(base._time(pub.released_at)):
        raise _conflict()
    pub.withdrawn_at = now; pub.withdrawn_by_id = doctor.id; pub.withdrawal_reason = reason_code
    create_audit_log(action=AuditAction.STATUS_CHANGE,entity_type='LabOrder',entity_id=order.id,clinic_id=order.clinic_id,patient_id=order.patient_id,user_id=doctor.user_id,description='Corrected laboratory report withdrawn',old_value={'release_status':'released','record_version':pub.version},new_value={'release_status':'withdrawn','record_version':pub.version,'reason_code':reason_code})
    return {'order_id':order.id,'record_version':pub.version,'release_status':'withdrawn'}


def status_fields(order):
    if order.patient_release_withdrawn_at is None:
        return {'record_version':1 if order.patient_released_at is not None else 0,'correction_pending':False}
    _, history, pubs = _state(order)
    if len(history) > len(pubs):
        return {'record_version':history[-1].new_version,'correction_pending':True}
    if pubs:
        pub = pubs[-1]
        return {'record_version':pub.version,'correction_pending':False,'release_status':'released' if pub.withdrawn_at is None else 'withdrawn','released_at':base._time(pub.released_at),'withdrawn_at':base._time(pub.withdrawn_at) if pub.withdrawn_at is not None else None}
    return {'record_version':1,'correction_pending':False}


def current_report(order):
    if order.patient_release_withdrawn_at is None:
        return base._original_released(order)
    try:
        _, history, pubs = _state(order)
        if not pubs or len(history)!=len(pubs) or pubs[-1].withdrawn_at is not None:
            raise _conflict()
        pub=pubs[-1]; amendment=history[-1]
        return {**amendment.new_content['report'],'record_version':pub.version,'correction_verified_at':amendment.new_content['correction_verified_at'],'release_status':'released','released_at':base._time(pub.released_at)}
    except (ConflictError, ValidationError):
        raise NotFoundError('Laboratory report unavailable') from None
