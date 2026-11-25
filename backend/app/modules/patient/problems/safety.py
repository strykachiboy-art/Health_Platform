"""Version-aware clinical-context enrichment, never a clinical action trigger."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from sqlalchemy import select
from app.extensions import db
from app.core.exceptions import ConflictError
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinical_records.models.clinical_record_amendment_model import ClinicalRecordAmendment
from .contracts import ProblemContent, eligible_for_clinical_context
from .model import PatientProblem

ICD = {'http://hl7.org/fhir/sid/icd-10', 'http://hl7.org/fhir/sid/icd-10-cm'}


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ConflictError('Problem version timestamp is invalid')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _digest(content):
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str).encode('utf-8')).hexdigest()


def confirmed_problem_context(*, clinic_id, patient_id, evaluation_at):
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or clinic.organization_id is None:
        raise ConflictError('Problem context organization unavailable')
    at = _utc(evaluation_at)
    rows = list(db.session.execute(select(PatientProblem).where(
        PatientProblem.organization_id == clinic.organization_id,
        PatientProblem.clinic_id == clinic_id,
        PatientProblem.patient_id == patient_id,
        PatientProblem.recorded_at <= at,
    ).order_by(PatientProblem.id)).scalars())
    if not rows:
        return [], []
    changes = defaultdict(list)
    for amendment in db.session.execute(select(ClinicalRecordAmendment).where(
        ClinicalRecordAmendment.clinic_id == clinic_id,
        ClinicalRecordAmendment.patient_id == patient_id,
        ClinicalRecordAmendment.source_record_type == 'patient_problem',
        ClinicalRecordAmendment.source_record_id.in_([r.id for r in rows]),
    ).order_by(ClinicalRecordAmendment.source_record_id, ClinicalRecordAmendment.new_version)).scalars():
        changes[amendment.source_record_id].append(amendment)
    conditions, codes = [], []
    for row in rows:
        initial = row.original_snapshot
        if not isinstance(initial, dict) or initial.get('version') != 1 or initial.get('id') != row.id or initial.get('patient_id') != patient_id or initial.get('clinic_id') != clinic_id or initial.get('organization_id') != str(clinic.organization_id):
            raise ConflictError('Problem original snapshot ownership mismatch')
        chosen = initial
        previous_id = None
        latest = initial
        for version, change in enumerate(changes[row.id], start=2):
            new = change.new_content
            if (change.new_version != version or change.previous_version != version - 1
                or change.previous_amendment_id != previous_id or _digest(new) != change.new_content_sha256
                or new.get('version') != version or new.get('id') != row.id
                or new.get('patient_id') != patient_id or new.get('clinic_id') != clinic_id
                or new.get('organization_id') != str(clinic.organization_id)):
                raise ConflictError('Problem amendment history invalid')
            if _utc(change.amended_at) <= at:
                chosen = new
            previous_id, latest = change.id, new
        if latest.get('version') != row.version or latest.get('content') != row.content:
            raise ConflictError('Problem current version does not match immutable history')
        item = ProblemContent.model_validate(chosen['content'])
        if not eligible_for_clinical_context(item):
            continue
        if item.onset_date and item.onset_date > at.date():
            continue
        conditions.append({'problem_id': row.id, 'version': chosen['version'], 'name': item.name,
                           'code': item.code, 'coding_system': item.coding_system,
                           'clinical_status': item.clinical_status, 'verification_status': item.verification_status})
        if item.code and item.coding_system in ICD:
            codes.append(item.code)
    return conditions, list(dict.fromkeys(codes))
