"""RAD1 non-diagnostic RIS foundation over canonical ImagingOrder.

This module deliberately has no DICOM ingress, Orthanc interface, report
finalization, critical-result dispatch, or downstream clinical release.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.staff.models.staff_model import Staff
from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
from app.modules.clinical_orders.services.imaging_order_service import get_imaging_order
from app.modules.radiology.models.ris_model import (
    RadiologyExam, RadiologyScheduledStep, RadiologyReport, RadiologyReportVersion,
)


def _positive(number, name):
    if type(number) is not int or number <= 0:
        raise ValidationError(f'Invalid {name}')
    return number


def _staff(actor_staff_id, clinic_id, *, doctor=True):
    row = db.session.execute(select(Staff).where(
        Staff.id == _positive(actor_staff_id, 'actor_staff_id'),
        Staff.clinic_id == _positive(clinic_id, 'clinic_id'),
    )).scalar_one_or_none()
    if row is None:
        raise NotFoundError('Radiology staff unavailable')
    if (row.status != StaffStatus.ACTIVE or row.user is None
            or not row.user.is_active or
            (doctor and row.user.role != Role.DOCTOR)):
        raise ValidationError('Active authorized radiology staff required')
    return row


def _order(imaging_order_id, clinic_id, *, lock=False):
    # Reuse the canonical imaging service's patient/admission lineage check.
    order = get_imaging_order(_positive(imaging_order_id, 'imaging_order_id'),
                              clinic_id=_positive(clinic_id, 'clinic_id'))
    if lock:
        locked = db.session.execute(select(ImagingOrder).where(
            ImagingOrder.id == order.id, ImagingOrder.clinic_id == clinic_id,
        ).with_for_update()).scalar_one_or_none()
        if locked is None or locked.patient_id != order.patient_id or \
                locked.admission_id != order.admission_id:
            raise ConflictError('Imaging order identity changed')
        return locked
    return order


def _owner(order, actor):
    # Only the original ordering doctor can author RAD1 plans/private drafts.
    # Radiologist capability grants are a later independent approval gate.
    if actor.user.role != Role.DOCTOR or actor.id != order.ordered_by_staff_id:
        raise NotFoundError('RIS action unavailable in clinician scope')


def _exam(exam_id, clinic_id, *, lock=False):
    statement = select(RadiologyExam).join(ImagingOrder,
        RadiologyExam.imaging_order_id == ImagingOrder.id).where(
            RadiologyExam.id == _positive(exam_id, 'exam_id'),
            ImagingOrder.clinic_id == _positive(clinic_id, 'clinic_id'),
        )
    if lock:
        statement = statement.with_for_update(of=RadiologyExam)
    exam = db.session.execute(statement).scalar_one_or_none()
    if exam is None:
        raise NotFoundError('RIS examination unavailable')
    order = _order(exam.imaging_order_id, clinic_id)
    return exam, order


def _audit(actor, row_id, action, description, metadata):
    create_audit_log(
        action=action, entity_type='RadiologyExam', entity_id=row_id,
        clinic_id=actor.clinic_id, user_id=actor.user_id,
        description=description, new_value=metadata,
    )


def _identity(exam, order):
    # Only bounded documentary status; no PHI name, report text, or DICOM data.
    return {
        'exam_id': exam.id,
        'imaging_order_id': order.id,
        'clinic_id': order.clinic_id,
        'accession_number': exam.accession_number,
        'issuer_namespace': exam.issuer_namespace,
        'status': exam.status,
        'version': exam.version,
        'order_status': order.status,
        'worklist_published': False,
        'archive_verified': False,
        'diagnostic_report_signed': False,
    }


def get_exam(*, exam_id, clinic_id, actor_staff_id):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id)
    _owner(order, actor)
    return _identity(exam, order)


def get_exam_by_order(*, imaging_order_id, clinic_id, actor_staff_id):
    actor = _staff(actor_staff_id, clinic_id)
    order = _order(imaging_order_id, clinic_id)
    _owner(order, actor)
    exam = db.session.execute(select(RadiologyExam).where(
        RadiologyExam.imaging_order_id == order.id,
    )).scalar_one_or_none()
    if exam is None:
        raise NotFoundError('RIS examination not established')
    return _identity(exam, order)


@transactional
def establish_exam(*, imaging_order_id, clinic_id, actor_staff_id):
    actor = _staff(actor_staff_id, clinic_id)
    order = _order(imaging_order_id, clinic_id, lock=True)
    _owner(order, actor)
    if order.status not in ('pending', 'active', 'in_progress', 'on_hold'):
        raise ConflictError('Imaging order not eligible for RIS establishment')
    existing = db.session.execute(select(RadiologyExam.id).where(
        RadiologyExam.imaging_order_id == order.id,
    )).scalar_one_or_none()
    if existing is not None:
        raise ConflictError('RIS examination already exists; reconcile with GET')
    # Independent of order ID, patient identity and any DICOM UID.
    accession = 'RAD-' + secrets.token_hex(12).upper()
    issuer = f'CSP-CLINIC-{clinic_id}'
    row = RadiologyExam(
        imaging_order_id=order.id, issuer_namespace=issuer,
        accession_number=accession, status='planned', version=1,
        created_by_staff_id=actor.id,
    )
    db.session.add(row)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError('RIS accession allocation conflict; reconcile with GET') from exc
    _audit(actor, row.id, AuditAction.CREATE, 'Established inert RIS exam',
           {'imaging_order_id': order.id, 'status': 'planned', 'version': 1})
    return _identity(row, order)


def _utc_naive(aware):
    if not isinstance(aware, datetime) or aware.utcoffset() is None:
        raise ValidationError('Timezone-aware scheduling timestamp required')
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


@transactional
def schedule_intent(*, exam_id, clinic_id, actor_staff_id,
                    expected_version, scheduled_at, station_code):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id, lock=True)
    _owner(order, actor)
    if order.status not in ('pending', 'active', 'in_progress', 'on_hold'):
        raise ConflictError('Canonical imaging order cannot be scheduled')
    if exam.status == 'cancelled':
        raise ConflictError('Cancelled RIS exam cannot schedule')
    if exam.version != _positive(expected_version, 'expected_version'):
        raise ConflictError('Stale RIS examination version')
    when = _utc_naive(scheduled_at)
    if not isinstance(station_code, str) or not (2 <= len(station_code) <= 64):
        raise ValidationError('Invalid radiology station code')
    prior = db.session.execute(select(RadiologyScheduledStep).where(
        RadiologyScheduledStep.exam_id == exam.id,
        RadiologyScheduledStep.status == 'intent',
    ).with_for_update()).scalars().all()
    if len(prior) > 1:
        raise ConflictError('RIS scheduling requires reconciliation')
    for step in prior:
        step.status = 'superseded'
    step = RadiologyScheduledStep(
        exam_id=exam.id, sequence=exam.version,
        scheduled_at=when, modality=order.modality,
        station_code=station_code, status='intent', created_by_staff_id=actor.id,
    )
    db.session.add(step)
    exam.version += 1
    exam.status = 'scheduled'
    db.session.flush()
    _audit(actor, exam.id, AuditAction.UPDATE, 'Recorded RIS scheduling intent',
           {'step_id': step.id, 'version': exam.version, 'mwl_published': False})
    return _identity(exam, order)


@transactional
def cancel_schedule_intent(*, exam_id, clinic_id, actor_staff_id,
                           expected_version):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id, lock=True)
    _owner(order, actor)
    if exam.version != _positive(expected_version, 'expected_version'):
        raise ConflictError('Stale RIS examination version')
    steps = db.session.execute(select(RadiologyScheduledStep).where(
        RadiologyScheduledStep.exam_id == exam.id,
        RadiologyScheduledStep.status == 'intent',
    ).with_for_update()).scalars().all()
    if len(steps) != 1:
        raise ConflictError('No single current RIS scheduling intent')
    steps[0].status = 'cancelled'
    exam.version += 1
    exam.status = 'planned'
    db.session.flush()
    _audit(actor, exam.id, AuditAction.UPDATE, 'Cancelled RIS scheduling intent',
           {'version': exam.version, 'mwl_published': False})
    return _identity(exam, order)


def get_worklist_intent(*, exam_id, clinic_id, actor_staff_id):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id)
    _owner(order, actor)
    rows = db.session.execute(select(RadiologyScheduledStep).where(
        RadiologyScheduledStep.exam_id == exam.id,
    ).order_by(RadiologyScheduledStep.sequence.desc()).limit(25)).scalars().all()
    return {
        'exam_id': exam.id, 'exam_version': exam.version,
        'mwl_published': False, 'archive_verified': False,
        'steps': [{'step_id': row.id, 'sequence': row.sequence,
                   'scheduled_at': row.scheduled_at.isoformat() + 'Z',
                   'station_code': row.station_code, 'modality': row.modality,
                   'status': row.status} for row in rows],
    }


def _report_for_exam(exam_id, *, lock=False):
    statement = select(RadiologyReport).where(RadiologyReport.exam_id == exam_id)
    if lock:
        statement = statement.with_for_update()
    return db.session.execute(statement).scalar_one_or_none()


def _digest(content):
    if (not isinstance(content, str) or not content or content != content.strip()
            or len(content) > 20000):
        raise ValidationError('Invalid unreleased working note')
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


@transactional
def create_draft(*, exam_id, clinic_id, actor_staff_id, content):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id, lock=True)
    _owner(order, actor)
    if order.status not in ('pending', 'active', 'in_progress', 'on_hold'):
        raise ConflictError('Canonical imaging order is not open for private RIS notes')
    if _report_for_exam(exam.id) is not None:
        raise ConflictError('Private RIS note exists; reconcile with GET')
    fingerprint = _digest(content)
    report = RadiologyReport(exam_id=exam.id, author_staff_id=actor.id,
                             status='private_draft', version=1)
    db.session.add(report)
    db.session.flush()
    db.session.add(RadiologyReportVersion(
        report_id=report.id, revision=1, content=content,
        content_sha256=fingerprint, edited_by_staff_id=actor.id,
    ))
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise ConflictError('Private RIS note already exists; reconcile with GET') from exc
    _audit(actor, exam.id, AuditAction.CREATE, 'Created unreleased RIS working note',
           {'report_id': report.id, 'report_version': 1})
    return {'report_id': report.id, 'exam_id': exam.id, 'version': 1,
            'status': 'private_draft', 'content_sha256': fingerprint,
            'diagnostic_report_signed': False}


@transactional
def revise_draft(*, report_id, clinic_id, actor_staff_id,
                 expected_version, content):
    actor = _staff(actor_staff_id, clinic_id)
    report = db.session.execute(select(RadiologyReport).where(
        RadiologyReport.id == _positive(report_id, 'report_id'),
    ).with_for_update()).scalar_one_or_none()
    if report is None:
        raise NotFoundError('Private RIS note unavailable')
    exam, order = _exam(report.exam_id, clinic_id)
    _owner(order, actor)
    if order.status not in ('pending', 'active', 'in_progress', 'on_hold'):
        raise ConflictError('Canonical imaging order is not open for private RIS notes')
    if report.author_staff_id != actor.id:
        raise NotFoundError('Private RIS note unavailable in author scope')
    if report.status != 'private_draft' or report.version != _positive(expected_version, 'expected_version'):
        raise ConflictError('Stale or non-draft RIS note')
    fingerprint = _digest(content)
    report.version += 1
    db.session.add(RadiologyReportVersion(
        report_id=report.id, revision=report.version, content=content,
        content_sha256=fingerprint, edited_by_staff_id=actor.id,
    ))
    db.session.flush()
    _audit(actor, exam.id, AuditAction.UPDATE, 'Revised unreleased RIS working note',
           {'report_id': report.id, 'report_version': report.version})
    return {'report_id': report.id, 'exam_id': exam.id, 'version': report.version,
            'status': 'private_draft', 'content_sha256': fingerprint,
            'diagnostic_report_signed': False}


def get_draft_history(*, exam_id, clinic_id, actor_staff_id):
    actor = _staff(actor_staff_id, clinic_id)
    exam, order = _exam(exam_id, clinic_id)
    _owner(order, actor)
    report = _report_for_exam(exam.id)
    if report is None:
        raise NotFoundError('Private RIS note unavailable')
    if report.author_staff_id != actor.id:
        raise NotFoundError('Private RIS note unavailable in author scope')
    versions = db.session.execute(select(RadiologyReportVersion).where(
        RadiologyReportVersion.report_id == report.id,
    ).order_by(RadiologyReportVersion.revision.desc()).limit(50)).scalars().all()
    return {'report_id': report.id, 'exam_id': exam.id,
            'status': 'private_draft', 'version': report.version,
            'diagnostic_report_signed': False, 'released_to_chart': False,
            'versions': [{'revision': v.revision, 'content': v.content,
                          'content_sha256': v.content_sha256} for v in versions]}
