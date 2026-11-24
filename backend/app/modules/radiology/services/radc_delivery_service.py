"""RAD-C B02A: external MWL acknowledgment, reconciliation, withdrawal.
Internal staged service only. No HTTP route, actual modality or patient data.
Each mutation owns its transaction boundary: do not wrap in another transactional service.
"""
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy import select

from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError
from app.extensions import db
from app.modules.radiology.models.ris_model import RadiologyWorklistPublication, RadiologyWorklistDelivery
from app.modules.radiology.services import radc_worklist_service as local, ris_service as ris
from app.modules.radiology.services.radc_orthanc_transport import StagedOrthanc, ArchiveUnconfirmed, valid_id


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _archive():
    local._enabled()
    if current_app.config.get('RAD_C_EXTERNAL_MWL_STAGED_ENABLED') is not True:
        raise NotFoundError('External worklist delivery held')
    config = current_app.config.get('RAD_C_EXTERNAL_MWL_ARCHIVE')
    if not isinstance(config, dict) or set(config) != {'port','username','password','archive_key'}:
        raise NotFoundError('Staged archive not configured')
    return StagedOrthanc(**config)


def _context(exam_id, clinic_id, actor_staff_id, publication_id, archive):
    exam, order, actor = local._context(exam_id, clinic_id, actor_staff_id, lock=True)
    if type(publication_id) is not int or publication_id <= 0:
        raise NotFoundError('Worklist unavailable')
    pub = db.session.execute(select(RadiologyWorklistPublication).where(
        RadiologyWorklistPublication.id == publication_id,
        RadiologyWorklistPublication.exam_id == exam.id)).scalar_one_or_none()
    if pub is None:
        raise NotFoundError('Worklist unavailable')
    local._validate_digest(pub)
    identity = local._identity(exam, order)
    p = pub.payload
    if identity is None or any(p.get(k) != v for k,v in {
        'exam_id': exam.id, 'exam_version': pub.exam_version, 'order_version': pub.order_version,
        'step_id': pub.step_id, 'clinic_id': clinic_id, 'patient_id': order.patient_id,
        'patient_identifier': str(order.patient_id), 'patient_issuer': identity.patient_issuer,
        'accession': identity.dicom_accession, 'accession_issuer': identity.issuer_namespace,
        'study_instance_uid': identity.study_instance_uid, 'imaging_order_id': order.id,
    }.items()):
        raise ConflictError('Worklist identity requires reconciliation')
    row = db.session.get(RadiologyWorklistDelivery, pub.id, populate_existing=True)
    if row is not None and (row.archive_key != archive.archive_key or row.payload_sha256 != pub.payload_sha256):
        raise ConflictError('External delivery identity changed')
    return exam, order, actor, pub, row


def _tags(pub):
    p = pub.payload
    stamp = datetime.fromisoformat(p['scheduled_at'].replace('Z','+00:00'))
    return {'PatientID': p['patient_identifier'], 'IssuerOfPatientID': p['patient_issuer'],
        'PatientName': 'RAD^STAGED', 'AccessionNumber': p['accession'],
        'IssuerOfAccessionNumberSequence': [{'LocalNamespaceEntityID': p['accession_issuer']}],
        'StudyInstanceUID': p['study_instance_uid'], 'RequestedProcedureID': f"R{p['imaging_order_id']}",
        'ScheduledProcedureStepSequence': [{'Modality': p['modality'], 'ScheduledStationAETitle': p['station_aet'],
            'ScheduledProcedureStepStartDate': stamp.strftime('%Y%m%d'),
            'ScheduledProcedureStepStartTime': stamp.strftime('%H%M%S'),
            'ScheduledProcedureStepID': f"S{p['step_id']}"}]}


def _matches(value, expected):
    if isinstance(expected, dict):
        return isinstance(value, dict) and all(k in value and _matches(value[k], v) for k,v in expected.items())
    if isinstance(expected, list):
        return isinstance(value, list) and len(value)==len(expected) and all(_matches(a,b) for a,b in zip(value,expected))
    return type(value) is type(expected) and value == expected


def _confirm(record, external_id, tags):
    if not isinstance(record, dict) or record.get('ID') != external_id or not _matches(record.get('Tags'), tags):
        raise ArchiveUnconfirmed('Worklist readback does not match canonical intent')


def _current(exam, order, pub):
    step = local._current_step(exam, order)
    return (step is not None and pub.exam_version == exam.version and pub.order_version == order.version
            and pub.step_id == step.id and pub.payload == local._payload(exam, order, step, local._identity(exam, order)))


def _receipt(exam, order, pub, row):
    return {'exam_id': exam.id, 'publication_id': pub.id, 'state': row.state if row else 'not_dispatched',
        'current_intent': _current(exam, order, pub),
        'archive_acknowledged': bool(row and row.state == 'acknowledged'),
        'modality_receipt_confirmed': False, 'archive_verified': False, 'diagnostic_report_signed': False}


def _audit(actor, exam, pub, state):
    ris._audit(actor, exam.id, AuditAction.UPDATE, 'Staged external worklist evidence',
        {'publication_id': pub.id, 'state': state, 'payload_sha256': pub.payload_sha256})


def status(**args):
    try:
        archive = _archive()
        exam, order, _, pub, row = _context(**args, archive=archive)
        return _receipt(exam, order, pub, row)
    finally:
        db.session.rollback()


def dispatch(**args):
    # Commit pending evidence BEFORE sending any external mutation. Never replay.
    try:
        archive = _archive()
        exam, order, actor, pub, row = _context(**args, archive=archive)
        if row is not None or not _current(exam, order, pub):
            raise ConflictError('Publication already attempted or stale; reconcile')
        prior = db.session.execute(select(RadiologyWorklistDelivery.publication_id).join(
            RadiologyWorklistPublication, RadiologyWorklistPublication.id == RadiologyWorklistDelivery.publication_id).where(
            RadiologyWorklistPublication.exam_id == exam.id, RadiologyWorklistDelivery.state != 'withdrawn')).first()
        if prior is not None:
            raise ConflictError('Prior external worklist must be withdrawn or reconciled')
        tags = _tags(pub)
        # No pre-existing matching identity is adopted by a create operation.
        for record in archive.list():
            if not isinstance(record,dict) or not isinstance(record.get('Tags'),dict) or not valid_id(record.get('ID')):
                raise ArchiveUnconfirmed('Malformed archive inventory')
            old = record['Tags']
            if old.get('StudyInstanceUID') == tags['StudyInstanceUID'] or old.get('AccessionNumber') == tags['AccessionNumber']:
                raise ConflictError('Existing archive identity requires reconciliation')
        db.session.add(RadiologyWorklistDelivery(publication_id=pub.id, archive_key=archive.archive_key,
            payload_sha256=pub.payload_sha256, state='dispatch_pending', actor_staff_id=actor.id))
        _audit(actor, exam, pub, 'dispatch_pending')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    # Reacquire canonical locks before dispatch; changed schedules stay held.
    try:
        exam, order, actor, pub, row = _context(**args, archive=archive)
        if row.state != 'dispatch_pending' or not _current(exam, order, pub):
            raise ConflictError('Publication changed; reconcile')
        external_id = archive.create(tags)
        _confirm(archive.get(external_id), external_id, tags)
        row.external_id = external_id
        row.acknowledged_at = now()
        row.state = 'acknowledged'
        _audit(actor, exam, pub, row.state)
        result = _receipt(exam, order, pub, row)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def reconcile(**args):
    """Read only at Orthanc; absence never authorizes a second create."""
    try:
        archive = _archive()
        exam, order, actor, pub, row = _context(**args, archive=archive)
        if row is None:
            raise ConflictError('No recorded attempt to reconcile')
        if row.state == 'dispatch_pending':
            tags = _tags(pub)
            matches = []
            for record in archive.list():
                if not isinstance(record,dict) or not isinstance(record.get('Tags'),dict) or not valid_id(record.get('ID')):
                    raise ArchiveUnconfirmed('Malformed archive inventory')
                old = record['Tags']
                if old.get('StudyInstanceUID') == tags['StudyInstanceUID'] or old.get('AccessionNumber') == tags['AccessionNumber']:
                    _confirm(record, record['ID'], tags)
                    matches.append(record['ID'])
            if len(matches) != 1:
                raise ArchiveUnconfirmed('Exactly one matching worklist required')
            external_id = matches[0]
            _confirm(archive.get(external_id), external_id, tags)
            row.external_id = external_id
            row.acknowledged_at = now()
            row.state = 'acknowledged'
            _audit(actor, exam, pub, row.state)
        elif row.state == 'withdraw_pending':
            if archive.get(row.external_id) is not None:
                raise ArchiveUnconfirmed('Withdrawal unconfirmed; no automatic retry')
            row.withdrawn_at = now()
            row.state = 'withdrawn'
            _audit(actor, exam, pub, row.state)
        elif row.state == 'acknowledged':
            _confirm(archive.get(row.external_id), row.external_id, _tags(pub))
        result = _receipt(exam, order, pub, row)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def withdraw(**args):
    try:
        archive = _archive()
        exam, order, actor, pub, row = _context(**args, archive=archive)
        if row is None or row.state != 'acknowledged':
            raise ConflictError('Withdrawal unavailable; reconcile')
        _confirm(archive.get(row.external_id), row.external_id, _tags(pub))
        row.state = 'withdraw_pending'
        _audit(actor, exam, pub, row.state)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    try:
        exam, order, actor, pub, row = _context(**args, archive=archive)
        if row.state != 'withdraw_pending':
            raise ConflictError('Withdrawal state changed; reconcile')
        _confirm(archive.get(row.external_id), row.external_id, _tags(pub))
        archive.delete(row.external_id)
        if archive.get(row.external_id) is not None:
            raise ArchiveUnconfirmed('Withdrawal readback unconfirmed')
        row.state = 'withdrawn'
        row.withdrawn_at = now()
        _audit(actor, exam, pub, row.state)
        result = _receipt(exam, order, pub, row)
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise
