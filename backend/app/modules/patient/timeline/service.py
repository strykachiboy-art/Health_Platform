"""Patient journey authorization and bounded source projection."""
from flask import current_app
from sqlalchemy import select
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import DomainError, NotFoundError
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.session_authority import context_from_claims, TenantContextDenied
from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access, TenantAccessDenied
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff
from app.modules.surgery.services.surgical_dashboard_read_service import _case_scope
from .contracts import CATEGORIES, decode_cursor, encode_cursor, utc
from .query import event_selects, page_statement


class TimelineDenied(DomainError):
    status_code = 403


def _scope(user_id, claims, patient_id):
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role not in (Role.DOCTOR, Role.NURSE):
        raise TimelineDenied('Clinical timeline access denied')
    try:
        tenant, clinic_id = context_from_claims(user_id, claims)
        require_tenant_clinic_access(user_id=user_id, organization_id=tenant, clinic_id=clinic_id)
    except (TenantContextDenied, TenantAccessDenied) as exc:
        raise TimelineDenied('Bound clinic membership required') from exc
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or str(clinic.organization_id) != tenant:
        raise TimelineDenied('Bound organization required')
    staff = db.session.execute(select(Staff).where(Staff.user_id == user_id, Staff.clinic_id == clinic_id, Staff.status == StaffStatus.ACTIVE)).scalar_one_or_none()
    if staff is None:
        raise TimelineDenied('Active clinical staff required')
    patient = db.session.execute(select(Patient).where(Patient.id == patient_id, Patient.clinic_id == clinic_id)).scalar_one_or_none()
    if patient is None:
        raise NotFoundError('Patient not found')
    return user, clinic, staff, patient


def list_events(user_id, claims, patient_id, filters):
    user, clinic, staff, patient = _scope(user_id, claims, patient_id)
    secret = current_app.config['JWT_SECRET_KEY']
    binding = filters.binding(user_id=user_id, organization_id=clinic.organization_id, clinic_id=clinic.id, patient_id=patient.id, role=user.role.value, token_version=user.token_version)
    after = decode_cursor(secret, binding, filters.cursor)
    tables = db.metadata.tables
    allowed_cases = _case_scope(clinic_id=clinic.id, role=user.role, staff_id=staff.id).correlate(None) if user.clinic_id == clinic.id else None
    selects = event_selects(tables, clinic_id=clinic.id, patient_id=patient.id, organization_id=clinic.organization_id, staff_id=staff.id, role=user.role.value, assigned_clinic=user.clinic_id,
        categories=filters.categories, dialect=db.engine.dialect.name,
        problem_enabled=current_app.config.get('PATIENT_PROBLEM_LIST_ENABLED') is True,
        lab_release_enabled=current_app.config.get('PATIENT_LAB_RELEASE_ENABLED') is True,
        surgery_case_scope=allowed_cases,
        blood_enabled=current_app.testing and current_app.config.get("BLOOD_BANK_INVENTORY_ENABLED") is True)
    q = page_statement(selects, start=filters.start, end=filters.end, after=after, limit=filters.limit, tables=tables, clinic_id=clinic.id)
    rows = list(db.session.execute(q).mappings()) if q is not None else []
    items = []
    for row in rows[:filters.limit]:
        items.append({'event_id': f"{row['event_type']}:{row['event_row_id']}", 'category': row['category'], 'event_type': row['event_type'], 'phase': row['phase'],
            'occurred_at': utc(row['occurred_at']).isoformat(), 'source': {'kind': row['source_kind'], 'id': row['source_id']}, 'record_version': row['version'],
            'related_staff': {'id': row['visible_staff_id'], 'name': f"{row['first_name']} {row['last_name']}", 'current_department': row['current_department_name']} if row['visible_staff_id'] is not None else None})
    continuation = None
    if len(rows) > filters.limit:
        last = rows[filters.limit - 1]
        continuation = encode_cursor(secret, binding, last['occurred_at'], last['event_type'], last['event_row_id'])
    available = set(CATEGORIES)
    if not current_app.testing or current_app.config.get('BLOOD_BANK_INVENTORY_ENABLED') is not True or user.clinic_id!=clinic.id:
        available.discard('blood_transfusion')
    if user.role is not Role.DOCTOR:
        available.discard('prescriptions')
    if user.clinic_id != clinic.id:
        available.difference_update({'consultations', 'surgery'})
    if current_app.config.get('PATIENT_PROBLEM_LIST_ENABLED') is not True:
        available.discard('problems')
    return {'organization_id': str(clinic.organization_id), 'clinic_id': clinic.id, 'patient_id': patient.id, 'items': items, 'next_cursor': continuation,
            'available_categories': sorted(available),
            'source_limitations': ['patient_check_in_timestamp_unavailable', 'clinical_radiology_final_report_unavailable', 'surgery_documentation_is_not_operation_completion'],
            'lab_release_events_enabled': bool(user.role is Role.DOCTOR and user.clinic_id == clinic.id and current_app.config.get('PATIENT_LAB_RELEASE_ENABLED') is True)}
