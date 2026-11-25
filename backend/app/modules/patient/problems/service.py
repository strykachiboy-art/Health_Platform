"""Clinic/organization-scoped longitudinal problem operations.

Requires reconciled SaaS session authority; never falls back to legacy clinic IDs.
"""
from datetime import datetime, timezone
from sqlalchemy import select
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinical_record_enums import ClinicalRecordType
from app.core.exceptions import DomainError, NotFoundError, ConflictError, ValidationError
from app.core.audit.services.audit_service import create_audit_log
from app.core.utils.decorators import transactional
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.consultation.models.consultation_model import Consultation
from app.modules.clinical_records.services.clinical_record_service import append_clinical_record_amendment, get_clinical_record_amendments
from .contracts import ProblemContent, ProblemCreate, ProblemChange, TERMINAL, CURRENT, validate_create, validate_change
from .model import PatientProblem


class ProblemAccessDenied(DomainError):
    status_code = 403


def _scope(user_id, claims, patient_id, *, write=False, medication_review=False):
    from app.modules.clinic.services.session_authority import context_from_claims, TenantContextDenied
    from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access, TenantAccessDenied
    user = db.session.get(User, user_id)
    allowed = {Role.DOCTOR} if write else {Role.DOCTOR, Role.NURSE}
    # Pharmacy receives only the bounded medication-reconciliation projection,
    # never the general problem list or mutation authority.
    if medication_review and not write:
        allowed.add(Role.PHARMACIST)
    if user is None or not user.is_active or user.role not in allowed:
        raise ProblemAccessDenied('Problem-list access denied')
    try:
        tenant, clinic_id = context_from_claims(user_id, claims)
        require_tenant_clinic_access(user_id=user_id, organization_id=tenant, clinic_id=clinic_id)
    except (TenantAccessDenied, TenantContextDenied) as exc:
        raise ProblemAccessDenied('Bound clinic membership required') from exc
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or str(clinic.organization_id) != tenant:
        raise ProblemAccessDenied('Bound organization required')
    staff = db.session.execute(select(Staff).where(Staff.user_id == user_id, Staff.clinic_id == clinic_id, Staff.status == StaffStatus.ACTIVE)).scalar_one_or_none()
    if staff is None:
        raise ProblemAccessDenied('Active clinical staff required')
    stmt = select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)
    if write:
        stmt = stmt.with_for_update()
    patient = db.session.execute(stmt).scalar_one_or_none()
    if patient is None:
        raise NotFoundError('Patient not found')
    if write and not patient.is_active:
        raise ConflictError('Inactive patient cannot receive problem changes')
    return clinic, staff, patient


def _query(clinic, patient):
    return select(PatientProblem).where(PatientProblem.organization_id == clinic.organization_id, PatientProblem.clinic_id == clinic.id, PatientProblem.patient_id == patient.id)


def _get(clinic, patient, problem_id, *, lock=False):
    stmt = _query(clinic, patient).where(PatientProblem.id == problem_id)
    if lock:
        stmt = stmt.with_for_update()
    item = db.session.execute(stmt).scalar_one_or_none()
    if item is None:
        raise NotFoundError('Problem not found')
    return item


def _check_consultation(clinic, patient, consultation_id):
    if consultation_id is None:
        return
    if db.session.execute(select(Consultation.id).where(Consultation.id == consultation_id, Consultation.clinic_id == clinic.id, Consultation.patient_id == patient.id)).scalar_one_or_none() is None:
        raise NotFoundError('Consultation not found for this patient')


def _check_duplicate(clinic, patient, content, exclude=None):
    # The patient row is locked by all mutations, serializing same-patient creation and changes.
    if content.clinical_status not in CURRENT or content.verification_status in TERMINAL:
        return
    stmt = _query(clinic, patient).where(PatientProblem.clinical_status.in_(CURRENT), PatientProblem.verification_status.not_in(TERMINAL))
    if exclude is not None:
        stmt = stmt.where(PatientProblem.id != exclude)
    for row in db.session.execute(stmt).scalars():
        prior = ProblemContent.model_validate(row.content)
        same_code = content.code is not None and (content.coding_system, content.code) == (prior.coding_system, prior.code)
        same_name = content.name.casefold() == prior.name.casefold()
        if same_code or same_name:
            raise ConflictError('A matching current problem already exists')


def _iso(value):
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).isoformat()


def serialize(item):
    return {'id': item.id, 'organization_id': str(item.organization_id), 'clinic_id': item.clinic_id, 'patient_id': item.patient_id,
            'content': item.content, 'consultation_ids': list(item.consultation_ids), 'version': item.version,
            'recorded_by_staff_id': item.recorded_by_staff_id, 'recorded_at': _iso(item.recorded_at),
            'reviewed_by_staff_id': item.reviewed_by_staff_id, 'reviewed_at': _iso(item.reviewed_at),
            'confirmed_by_staff_id': item.confirmed_by_staff_id, 'confirmed_at': _iso(item.confirmed_at)}


def _snapshot(item):
    return serialize(item)


def _audit(item, staff, action, reason):
    create_audit_log(action=action, entity_type='PatientProblem', entity_id=item.id, user_id=staff.user_id,
                     clinic_id=item.clinic_id, patient_id=item.patient_id, description='Patient problem record operation', reason=reason,
                     new_value={'version': item.version, 'clinical_status': item.clinical_status, 'verification_status': item.verification_status})


@transactional
def create_problem(user_id, claims, patient_id, data: ProblemCreate):
    clinic, staff, patient = _scope(user_id, claims, patient_id, write=True)
    now = datetime.now(timezone.utc)
    content = ProblemContent.model_validate(data.model_dump(exclude={'consultation_id'}))
    try:
        validate_create(content, now.date())
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    _check_consultation(clinic, patient, data.consultation_id)
    _check_duplicate(clinic, patient, content)
    item = PatientProblem(organization_id=clinic.organization_id, clinic_id=clinic.id, patient_id=patient.id,
                          content=content.model_dump(mode='json'), original_snapshot={}, clinical_status=content.clinical_status,
                          verification_status=content.verification_status, consultation_ids=[data.consultation_id] if data.consultation_id else [],
                          version=1, recorded_by_staff_id=staff.id, reviewed_by_staff_id=staff.id, recorded_at=now, reviewed_at=now,
                          confirmed_by_staff_id=staff.id if content.verification_status == 'confirmed' else None,
                          confirmed_at=now if content.verification_status == 'confirmed' else None)
    db.session.add(item)
    db.session.flush()
    item.original_snapshot = _snapshot(item)
    _audit(item, staff, AuditAction.CREATE, 'Explicit problem creation')
    return serialize(item)


@transactional
def change_problem(user_id, claims, patient_id, problem_id, data: ProblemChange):
    clinic, staff, patient = _scope(user_id, claims, patient_id, write=True)
    item = _get(clinic, patient, problem_id, lock=True)
    if item.version != data.expected_version:
        raise ConflictError('Problem version is stale')
    old = ProblemContent.model_validate(item.content)
    if old.verification_status in TERMINAL:
        raise ConflictError('Terminal verification record is immutable')
    now = datetime.now(timezone.utc)
    if data.action == 'associate':
        _check_consultation(clinic, patient, data.consultation_id)
        if data.consultation_id in item.consultation_ids:
            raise ConflictError('Consultation is already associated')
        item.consultation_ids = [*item.consultation_ids, data.consultation_id]
    elif data.action != 'review':
        new = data.content
        try:
            validate_change(old, new, data.action, now.date())
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        _check_duplicate(clinic, patient, new, exclude=item.id)
        if new.verification_status == 'confirmed' and (old.verification_status != 'confirmed' or data.action == 'correct'):
            item.confirmed_by_staff_id, item.confirmed_at = staff.id, now
        elif new.verification_status != 'confirmed':
            item.confirmed_by_staff_id, item.confirmed_at = None, None
        item.content = new.model_dump(mode='json')
        item.clinical_status = new.clinical_status
        item.verification_status = new.verification_status
    previous = item.version
    item.version += 1
    item.reviewed_by_staff_id, item.reviewed_at = staff.id, now
    append_clinical_record_amendment(clinic_id=clinic.id, patient_id=patient.id, source_record_type=ClinicalRecordType.PATIENT_PROBLEM,
                                    source_record_id=item.id, current_status='final' if previous == 1 else 'amended', previous_version=previous,
                                    amended_by_staff_id=staff.id, reason=data.reason, new_content=_snapshot(item))
    _audit(item, staff, AuditAction.UPDATE, data.reason)
    return serialize(item)


def list_problems(user_id, claims, patient_id, status, after, limit):
    clinic, _, patient = _scope(user_id, claims, patient_id)
    stmt = _query(clinic, patient).where(PatientProblem.id > after)
    if status == 'current':
        stmt = stmt.where(PatientProblem.clinical_status.in_(CURRENT), PatientProblem.verification_status.not_in(TERMINAL))
    elif status == 'historical':
        stmt = stmt.where((PatientProblem.clinical_status.not_in(CURRENT)) | PatientProblem.verification_status.in_(TERMINAL))
    elif status == 'resolved':
        stmt = stmt.where(PatientProblem.clinical_status == 'resolved', PatientProblem.verification_status.not_in(TERMINAL))
    rows = list(db.session.execute(stmt.order_by(PatientProblem.id).limit(limit + 1)).scalars())
    return {'items': [serialize(x) for x in rows[:limit]], 'next_after': rows[limit-1].id if len(rows) > limit else None}


def get_problem(user_id, claims, patient_id, problem_id):
    clinic, _, patient = _scope(user_id, claims, patient_id)
    return serialize(_get(clinic, patient, problem_id))


def get_history(user_id, claims, patient_id, problem_id):
    clinic, _, patient = _scope(user_id, claims, patient_id)
    item = _get(clinic, patient, problem_id)
    rows = get_clinical_record_amendments(clinic_id=clinic.id, source_record_type=ClinicalRecordType.PATIENT_PROBLEM, source_record_id=item.id)
    from .safety import _digest
    initial = item.original_snapshot
    identity = {'id': item.id, 'patient_id': patient.id, 'clinic_id': clinic.id, 'organization_id': str(clinic.organization_id)}
    if not isinstance(initial, dict) or initial.get('version') != 1 or any(initial.get(k) != v for k, v in identity.items()):
        raise ConflictError('Problem history original snapshot mismatch')
    previous_id, latest = None, initial
    for version, row in enumerate(rows, start=2):
        new = row.new_content
        if (row.patient_id != patient.id or row.new_version != version or row.previous_version != version - 1
                or row.previous_amendment_id != previous_id or not isinstance(new, dict)
                or _digest(new) != row.new_content_sha256 or new.get('version') != version
                or any(new.get(k) != v for k, v in identity.items())):
            raise ConflictError('Problem history amendment mismatch')
        previous_id, latest = row.id, new
    if latest.get('version') != item.version or latest.get('content') != item.content:
        raise ConflictError('Problem history current version mismatch')
    return {'original': item.original_snapshot, 'amendments': [{'version': a.new_version, 'reason': a.reason, 'actor_staff_id': a.amended_by_staff_id,
            'amended_at': _iso(a.amended_at), 'content': a.new_content, 'sha256': a.new_content_sha256} for a in rows]}


def get_fhir_condition(user_id, claims, patient_id, problem_id):
    from .interoperability import local_condition
    return local_condition(get_problem(user_id, claims, patient_id, problem_id))
