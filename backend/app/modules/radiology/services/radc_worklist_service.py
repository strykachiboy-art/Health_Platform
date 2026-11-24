"""RAD-C B01: durable local worklist intent and explicit interpretation authority.
No Orthanc calls, acquisition, signing, patient release, or credential provisioning.
"""
from datetime import datetime, timezone
import hashlib
import json
import uuid

from flask import current_app
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.enums.audit_enums import AuditAction
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.clinical_orders.models.imaging_order_model import ImagingOrder
from app.modules.radiology.models.ris_model import (
    RadiologyDicomIdentity, RadiologyInterpretationGrant,
    RadiologyScheduledStep, RadiologyWorklistPublication,
)
from app.modules.radiology.services import ris_service as ris
from app.modules.radiology.services.rad3_mwl_contract import _AET, _MODALITIES

_ACTIVE = ('pending', 'active', 'in_progress', 'on_hold')


def _enabled():
    if not current_app.testing or current_app.config.get('RAD_C_WORKLIST_STAGED_ENABLED') is not True:
        raise NotFoundError('Clinical radiology is held')


def _context(exam_id, clinic_id, actor_staff_id, *, lock=False, owner=True):
    _enabled()
    ensure_clinic_active(clinic_id)
    actor = ris._staff(actor_staff_id, clinic_id)
    if actor.user.clinic_id != clinic_id:
        raise NotFoundError('Radiology authority unavailable')
    exam, order = ris._exam(exam_id, clinic_id, lock=lock)
    if lock:
        order = db.session.execute(select(ImagingOrder).where(
            ImagingOrder.id == order.id, ImagingOrder.clinic_id == clinic_id,
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if order is None:
            raise NotFoundError('Radiology order unavailable')
        # Repeat the canonical lineage check while holding the order lock.
        order = ris._order(order.id, clinic_id)
    if owner:
        ris._owner(order, actor)
    return exam, order, actor


def require_interpreting_authority(*, exam_id, clinic_id, actor_staff_id):
    """Future clinical operations must call this inside their own transaction.
    No role or specialty string is sufficient. A real provisioning gate is open.
    """
    exam, order, actor = _context(exam_id, clinic_id, actor_staff_id, lock=True, owner=False)
    grant = db.session.execute(select(RadiologyInterpretationGrant).where(
        RadiologyInterpretationGrant.exam_id == exam.id,
        RadiologyInterpretationGrant.staff_id == actor.id,
        RadiologyInterpretationGrant.clinic_id == clinic_id,
        RadiologyInterpretationGrant.modality == order.modality,
    ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if (grant is None or grant.revoked_at is not None
            or not grant.approved_at <= now < grant.expires_at
            or not grant.credential_reference.strip()):
        raise NotFoundError('Approved interpretation authority unavailable')
    if exam.status == 'cancelled' or order.status == 'cancelled':
        raise ConflictError('Cancelled examination')
    return actor


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')).hexdigest()


def _identity(exam, order, *, allocate=False):
    issuer = f'CSP-CLINIC-{order.clinic_id}'
    if exam.issuer_namespace != issuer or not 0 < exam.id <= 2147483647:
        raise ConflictError('RIS identity requires reconciliation')
    # Local, issuer-scoped namespace. No truncation, no external ID assumption.
    accession = f'R{exam.id:015X}'
    row = db.session.get(RadiologyDicomIdentity, exam.id)
    if row is None and allocate:
        row = RadiologyDicomIdentity(exam_id=exam.id, issuer_namespace=issuer,
            dicom_accession=accession, patient_id=order.patient_id, patient_issuer=issuer,
            study_instance_uid='2.25.' + str(uuid.uuid4().int))
        db.session.add(row)
        db.session.flush()
    if row is not None and (row.issuer_namespace != issuer or row.dicom_accession != accession
            or row.patient_id != order.patient_id or row.patient_issuer != issuer):
        raise ConflictError('Allocated identity requires reconciliation')
    return row


def _current_step(exam, order):
    if exam.status != 'scheduled' or order.status not in _ACTIVE:
        return None
    steps = db.session.execute(select(RadiologyScheduledStep).where(
        RadiologyScheduledStep.exam_id == exam.id,
        RadiologyScheduledStep.status == 'intent',
    )).scalars().all()
    if (len(steps) != 1 or steps[0].sequence != exam.version - 1
            or steps[0].modality != order.modality):
        raise ConflictError('Current RIS scheduling intent requires reconciliation')
    return steps[0]


def _payload(exam, order, step, identity):
    if not _AET.fullmatch(step.station_code) or order.modality not in _MODALITIES:
        raise ValidationError('Approved DICOM station and modality required')
    stamp = step.scheduled_at
    return {'exam_id': exam.id, 'exam_version': exam.version,
        'imaging_order_id': order.id, 'order_version': order.version,
        'clinic_id': order.clinic_id, 'patient_id': order.patient_id,
        'patient_identifier': str(order.patient_id), 'patient_issuer': identity.patient_issuer,
        'accession': identity.dicom_accession, 'accession_issuer': identity.issuer_namespace,
        'study_instance_uid': identity.study_instance_uid, 'step_id': step.id,
        'modality': _MODALITIES[order.modality], 'station_aet': step.station_code,
        'scheduled_at': stamp.isoformat() + 'Z'}


def _receipt(exam, order, row, state):
    return {'exam_id': exam.id, 'exam_version': exam.version, 'order_version': order.version,
        'local_state': state, 'publication_id': row.id if row else None,
        'published_exam_version': row.exam_version if row else None,
        'payload_sha256': row.payload_sha256 if row else None,
        'external_publication_confirmed': False, 'external_delivery': 'held',
        'archive_verified': False, 'diagnostic_report_signed': False}


def _latest(exam):
    return db.session.execute(select(RadiologyWorklistPublication).where(
        RadiologyWorklistPublication.exam_id == exam.id,
    ).order_by(RadiologyWorklistPublication.exam_version.desc()).limit(1)).scalar_one_or_none()


def _validate_digest(row):
    if row is not None and _digest(row.payload) != row.payload_sha256:
        raise ConflictError('Worklist publication integrity requires reconciliation')


def get_publication(*, exam_id, clinic_id, actor_staff_id):
    exam, order, _ = _context(exam_id, clinic_id, actor_staff_id)
    row = _latest(exam)
    _validate_digest(row)
    identity = _identity(exam, order)
    step = _current_step(exam, order)
    if row is None:
        return _receipt(exam, order, None, 'not_published')
    if step is None:
        return _receipt(exam, order, row, 'cancelled_or_inactive')
    if row.exam_version != exam.version or row.order_version != order.version:
        return _receipt(exam, order, row, 'superseded')
    if identity is None or row.step_id != step.id or row.payload != _payload(exam, order, step, identity):
        raise ConflictError('Worklist publication requires reconciliation')
    return _receipt(exam, order, row, 'local_published')


@transactional
def publish(*, exam_id, clinic_id, actor_staff_id, expected_version):
    exam, order, actor = _context(exam_id, clinic_id, actor_staff_id, lock=True)
    if type(expected_version) is not int or expected_version != exam.version:
        raise ConflictError('Stale RIS examination version')
    step = _current_step(exam, order)
    if step is None:
        raise ConflictError('Active scheduling intent required')
    identity = _identity(exam, order, allocate=True)
    payload = _payload(exam, order, step, identity)
    prior = _latest(exam)
    _validate_digest(prior)
    if prior is not None and prior.exam_version == exam.version:
        if prior.step_id != step.id or prior.payload != payload:
            raise ConflictError('Worklist publication changed; reschedule or reconcile')
        return _receipt(exam, order, prior, 'local_published')
    row = RadiologyWorklistPublication(exam_id=exam.id, exam_version=exam.version,
        order_version=order.version, step_id=step.id, payload=payload,
        payload_sha256=_digest(payload), published_by_staff_id=actor.id)
    db.session.add(row)
    db.session.flush()
    ris._audit(actor, exam.id, AuditAction.CREATE, 'Recorded local clinical worklist intent',
        {'publication_id': row.id, 'exam_version': exam.version, 'payload_sha256': row.payload_sha256,
         'external_publication_confirmed': False})
    return _receipt(exam, order, row, 'local_published')
