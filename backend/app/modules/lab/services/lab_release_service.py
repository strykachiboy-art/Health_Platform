"""Staged patient laboratory release using the existing laboratory authority."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select

from app.core.audit.services.audit_service import create_audit_log
from app.core.auth.user.models.user_model import User
from app.core.enums.audit_enums import AuditAction
from app.core.enums.lab_enums import LabOrderStatus, LabResultFlag
from app.core.enums.notification_enums import NotificationChannel, NotificationType
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError, ValidationError
from app.core.notifications.services.notification_service import create_notification_in_transaction
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.lab.models.lab_model import LabOrder, LabOrderItem
from app.modules.lab.services.lab_service import (
    _get_lab_order, _validate_actor_for_order, validate_lab_order_integrity,
)
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff


def _enabled():
    if not current_app.testing or current_app.config.get('PATIENT_LAB_RELEASE_ENABLED') is not True:
        raise ServiceUnavailableError('Patient laboratory release is not enabled', code='PATIENT_LAB_RELEASE_DISABLED')


def _unavailable():
    return NotFoundError('Laboratory report unavailable')


def _time(value):
    if not isinstance(value, datetime):
        raise ConflictError('Laboratory report timestamps require review')
    # Existing laboratory writes use UTC; SQLite may strip the offset.
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat()


def _digest(report, recipient_id):
    raw = json.dumps({'report': report, 'recipient_user_id': recipient_id}, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _doctor_order(order_id, clinic_id, doctor_user_id, *, lock=False):
    _enabled()
    ensure_clinic_active(clinic_id)
    order = _get_lab_order(order_id, clinic_id, for_update=lock)
    staff = db.session.execute(select(Staff).where(Staff.user_id == doctor_user_id, Staff.clinic_id == clinic_id)).scalar_one_or_none()
    if staff is None:
        raise _unavailable()
    actor = _validate_actor_for_order(staff.id, order, allowed_roles=(Role.DOCTOR,))
    if actor.user_id != doctor_user_id or actor.user.clinic_id != clinic_id:
        raise _unavailable()
    assigned = order.ordered_by_id == actor.id
    if not assigned and order.consultation_id is not None:
        consultation = db.session.execute(select(Consultation).where(
            Consultation.id == order.consultation_id,
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == order.patient_id,
            Consultation.staff_id == actor.id,
        )).scalar_one_or_none()
        assigned = consultation is not None
    if not assigned:
        raise _unavailable()
    return order, actor


def _recipient(order, *, lock=False):
    statement = select(Patient).where(Patient.id == order.patient_id, Patient.clinic_id == order.clinic_id)
    if lock:
        statement = statement.with_for_update()
    patient = db.session.execute(statement).scalar_one_or_none()
    if patient is None or not patient.is_active or patient.user_id is None:
        raise ConflictError('Patient clinical enrollment required', code='PATIENT_ENROLLMENT_REQUIRED')
    stmt = select(User).where(User.id == patient.user_id)
    if lock:
        stmt = stmt.with_for_update()
    user = db.session.execute(stmt).scalar_one_or_none()
    if user is None or not user.is_active or user.role is not Role.PATIENT or user.clinic_id != order.clinic_id:
        raise ConflictError('Patient clinical enrollment required', code='PATIENT_ENROLLMENT_REQUIRED')
    return user


def _report(order, *, lock=False):
    validate_lab_order_integrity(order)
    if order.status is not LabOrderStatus.COMPLETED:
        raise ConflictError('Only a verified completed order may be released')
    if order.patient_released_at is not None:
        raise ConflictError('Laboratory report already has a release decision')
    statement = select(LabOrderItem).where(LabOrderItem.order_id == order.id).order_by(LabOrderItem.id)
    if lock:
        statement = statement.with_for_update()
    items = db.session.execute(statement).scalars().all()
    if not items:
        raise ConflictError('No laboratory results to release')
    rows = []
    for item in items:
        # The first staged slice deliberately holds critical, unclassified,
        # annotated, attachment-bearing and historical unsnapshotted results.
        if item.flag not in (LabResultFlag.NORMAL, LabResultFlag.ABNORMAL) or item.result_notes or item.result_file_url:
            raise ConflictError('Laboratory result requires additional clinical release review', code='LAB_RELEASE_REVIEW_REQUIRED')
        meta = item.result_display_metadata
        if (not isinstance(meta, dict) or set(meta) != {'test_name','loinc_code','unit','reference_range'}
            or not isinstance(meta['test_name'], str) or not meta['test_name'].strip()
            or any(meta[k] is not None and not isinstance(meta[k], str) for k in ('loinc_code','unit','reference_range'))
            or not isinstance(item.result_value, str) or not item.result_value.strip()
            or item.test is None or item.test.clinic_id not in (None, order.clinic_id)):
            raise ConflictError('Original laboratory report metadata requires review', code='LAB_RELEASE_METADATA_REQUIRED')
        times = [datetime.fromisoformat(_time(v)) for v in (order.processed_at, item.resulted_at, order.verified_at, order.completed_at)]
        if times != sorted(times):
            raise ConflictError('Laboratory result timestamps require review')
        rows.append({'test_name': meta['test_name'], 'loinc_code': meta['loinc_code'], 'unit': meta['unit'], 'reference_range': meta['reference_range'], 'result_value': item.result_value, 'flag': item.flag.value, 'resulted_at': _time(item.resulted_at)})
    return {'order_id': order.id, 'clinic_id': order.clinic_id, 'patient_id': order.patient_id, 'verified_at': _time(order.verified_at), 'completed_at': _time(order.completed_at), 'results': rows}


@transactional
def preview_release(*, order_id, clinic_id, doctor_user_id):
    order, _ = _doctor_order(order_id, clinic_id, doctor_user_id)
    user = _recipient(order)
    report = _report(order)
    create_audit_log(action=AuditAction.VIEW, entity_type='LabOrder', entity_id=order.id, clinic_id=clinic_id, patient_id=order.patient_id, user_id=doctor_user_id, description='Doctor reviewed laboratory report release preview')
    return {'report': report, 'preview_sha256': _digest(report, user.id)}


def release_status(*, order_id, clinic_id, doctor_user_id):
    _enabled()
    user = db.session.get(User, doctor_user_id)
    if user is not None and user.is_active and user.role is Role.LAB_TECHNICIAN and user.clinic_id == clinic_id:
        ensure_clinic_active(clinic_id)
        order = _get_lab_order(order_id, clinic_id)
        staff = db.session.execute(select(Staff).where(Staff.user_id == user.id, Staff.clinic_id == clinic_id)).scalar_one_or_none()
        if staff is None:
            raise _unavailable()
        _validate_actor_for_order(staff.id, order, allowed_roles=(Role.LAB_TECHNICIAN,))
    else:
        order, _ = _doctor_order(order_id, clinic_id, doctor_user_id)
    return {'order_id': order.id, 'release_status': 'withdrawn' if order.patient_release_withdrawn_at is not None else 'released' if order.patient_released_at is not None else 'unreleased', 'released_at': _time(order.patient_released_at) if order.patient_released_at is not None else None, 'withdrawn_at': _time(order.patient_release_withdrawn_at) if order.patient_release_withdrawn_at is not None else None}


@transactional
def release_report(*, order_id, clinic_id, doctor_user_id, preview_sha256):
    order, doctor = _doctor_order(order_id, clinic_id, doctor_user_id, lock=True)
    recipient = _recipient(order, lock=True)
    report = _report(order, lock=True)
    if not isinstance(preview_sha256, str) or preview_sha256 != _digest(report, recipient.id):
        raise ConflictError('Report changed; review the current preview before releasing', code='LAB_RELEASE_PREVIEW_CHANGED')
    now = datetime.now(timezone.utc)
    if now < datetime.fromisoformat(report['completed_at']):
        raise ConflictError('Laboratory completion timestamp requires review')
    order.patient_released_at = now
    order.patient_released_by_id = doctor.id
    order.patient_release_user_id = recipient.id
    order.patient_release_snapshot = report
    create_audit_log(action=AuditAction.STATUS_CHANGE, entity_type='LabOrder', entity_id=order.id, clinic_id=clinic_id, patient_id=order.patient_id, user_id=doctor_user_id, description='Laboratory report released to patient', old_value={'patient_release': 'unreleased'}, new_value={'patient_release': 'released', 'released_at': _time(now)})
    # Reuse existing notification creation within the SAME transaction.
    # No external delivery or test values in the message. The reference is an
    # opaque record identifier; every destination must recheck patient ownership.
    create_notification_in_transaction(clinic_id=clinic_id, user_id=recipient.id, title='A new record is available', message='Sign in to view your new clinical record.', notification_type=NotificationType.LAB, channel=NotificationChannel.IN_APP, reference_type='released_lab_result', reference_id=order.id, actor_user_id=doctor_user_id)
    return {'order_id': order.id, 'release_status': 'released', 'released_at': _time(now)}


@transactional
def withdraw_report(*, order_id, clinic_id, doctor_user_id, reason_code):
    order, doctor = _doctor_order(order_id, clinic_id, doctor_user_id, lock=True)
    if reason_code not in ('clinical_correction','identity_review','other_safety_review'):
        raise ValidationError('Invalid withdrawal reason')
    if order.patient_released_at is None or order.patient_release_withdrawn_at is not None:
        raise ConflictError('No current released laboratory report')
    now = datetime.now(timezone.utc)
    if now < datetime.fromisoformat(_time(order.patient_released_at)):
        raise ConflictError('Laboratory release timestamp requires review')
    order.patient_release_withdrawn_at = now
    order.patient_release_withdrawn_by_id = doctor.id
    create_audit_log(action=AuditAction.STATUS_CHANGE, entity_type='LabOrder', entity_id=order.id, clinic_id=clinic_id, patient_id=order.patient_id, user_id=doctor_user_id, description='Patient laboratory report withdrawn', old_value={'patient_release': 'released'}, new_value={'patient_release': 'withdrawn', 'reason_code': reason_code})
    return {'order_id': order.id, 'release_status': 'withdrawn'}


def _own_patient(user_id, clinic_id):
    _enabled()
    ensure_clinic_active(clinic_id)
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role is not Role.PATIENT or user.clinic_id != clinic_id:
        raise _unavailable()
    patient = user.patient
    if patient is None or not patient.is_active or patient.user_id != user.id or patient.clinic_id != clinic_id:
        raise ConflictError('Patient clinical enrollment required', code='PATIENT_ENROLLMENT_REQUIRED')
    return patient


def _visible(patient, user_id):
    return select(LabOrder).where(LabOrder.patient_id == patient.id, LabOrder.clinic_id == patient.clinic_id, LabOrder.status == LabOrderStatus.COMPLETED, LabOrder.verified_at.is_not(None), LabOrder.verified_by_id.is_not(None), LabOrder.completed_at.is_not(None), LabOrder.patient_released_at.is_not(None), LabOrder.patient_released_by_id.is_not(None), LabOrder.patient_release_user_id == user_id, LabOrder.patient_release_withdrawn_at.is_(None), LabOrder.patient_release_withdrawn_by_id.is_(None))


def _released(order):
    snapshot = order.patient_release_snapshot
    expected = {'order_id','clinic_id','patient_id','verified_at','completed_at','results'}
    if (not isinstance(snapshot, dict) or set(snapshot) != expected or snapshot['order_id'] != order.id or snapshot['clinic_id'] != order.clinic_id or snapshot['patient_id'] != order.patient_id or snapshot['verified_at'] != _time(order.verified_at) or snapshot['completed_at'] != _time(order.completed_at) or not isinstance(snapshot['results'], list) or not snapshot['results']):
        raise _unavailable()
    keys = {'test_name','loinc_code','unit','reference_range','result_value','flag','resulted_at'}
    for row in snapshot['results']:
        if (not isinstance(row, dict) or set(row) != keys or row['flag'] not in ('normal','abnormal') or any(not isinstance(row[k], str) or not row[k].strip() for k in ('test_name','result_value','resulted_at')) or any(row[k] is not None and not isinstance(row[k], str) for k in ('loinc_code','unit','reference_range'))):
            raise _unavailable()
    return {**snapshot, 'released_at': _time(order.patient_released_at), 'release_status': 'released'}


def list_released(*, user_id, clinic_id, page=1):
    patient = _own_patient(user_id, clinic_id)
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValidationError('Invalid page')
    result = db.paginate(_visible(patient, user_id).order_by(LabOrder.patient_released_at.desc(), LabOrder.id.desc()), page=page, per_page=20, error_out=False)
    items = []
    for order in result.items:
        report = _released(order)
        items.append({'order_id': order.id, 'released_at': report['released_at'], 'completed_at': report['completed_at'], 'test_count': len(report['results']), 'release_status': 'released'})
    return {'items': items, 'page': result.page, 'per_page': result.per_page, 'total': result.total, 'pages': result.pages, 'has_next': result.has_next}


@transactional
def get_released(*, user_id, clinic_id, order_id):
    patient = _own_patient(user_id, clinic_id)
    order = db.session.execute(_visible(patient, user_id).where(LabOrder.id == order_id)).scalar_one_or_none()
    if order is None:
        raise _unavailable()
    report = _released(order)
    create_audit_log(action=AuditAction.VIEW, entity_type='LabOrder', entity_id=order.id, clinic_id=clinic_id, patient_id=patient.id, user_id=user_id, description='Patient viewed released laboratory report')
    return report
