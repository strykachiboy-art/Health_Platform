"""Authoritative search authorization. Results are references, never grants."""
from flask import current_app
from sqlalchemy import select

from app.core.auth.user.models.user_model import User
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import DomainError
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.session_authority import context_from_claims, TenantContextDenied
from app.modules.clinic.services.tenant_boundary import require_tenant_clinic_access, TenantAccessDenied
from app.modules.staff.models.staff_model import Staff

from .contracts import PATIENT_ROLES, available_resources, decode_cursor, encode_cursor
from .query import page_statement


class SearchDenied(DomainError):
    status_code = 403


def search(user_id, claims, filters):
    user = db.session.get(User, user_id)
    if user is None or not user.is_active or user.role.value not in PATIENT_ROLES:
        raise SearchDenied('Search access denied')
    try:
        tenant, clinic_id = context_from_claims(user_id, claims)
        require_tenant_clinic_access(user_id=user_id, organization_id=tenant, clinic_id=clinic_id)
    except (TenantContextDenied, TenantAccessDenied) as exc:
        raise SearchDenied('Bound clinic membership required') from exc
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or str(clinic.organization_id) != tenant:
        raise SearchDenied('Bound organization required')
    staff = db.session.execute(select(Staff).where(Staff.user_id == user_id,
        Staff.clinic_id == clinic_id, Staff.status == StaffStatus.ACTIVE)).scalar_one_or_none()
    if staff is None:
        raise SearchDenied('Active clinic staff required')
    jti = claims.get('jti')
    if not isinstance(jti, str) or not jti:
        raise SearchDenied('Bound session required')
    available = available_resources(user.role.value)
    kinds = tuple(available) if filters.resource_types is None else filters.resource_types
    if not set(kinds) <= available.keys():
        raise SearchDenied('Resource access denied')
    if filters.status is not None and not any(filters.status in available[k] for k in kinds):
        raise ValueError('Status not supported for selected resources')
    secret = current_app.config['JWT_SECRET_KEY']
    binding = filters.binding(secret, user_id=user.id, tenant_id=tenant, clinic_id=clinic.id,
        role=user.role.value, token_version=user.token_version, jti=jti, kinds=kinds)
    after = decode_cursor(secret, binding, filters.cursor)
    statement = page_statement(db.metadata.tables, clinic_id=clinic.id, filters=filters, kinds=kinds, after=after)
    rows = list(db.session.execute(statement).mappings()) if statement is not None else []
    items = [{'resource_type': r['resource_type'], 'record_id': r['record_id'],
              'patient_id': r['patient_id'], 'patient_name': f"{r['first_name']} {r['last_name']}",
              'patient_number': r['patient_number'], 'status': r['status'],
              'admission_id': r['admission_id'], 'kind': 'patient' if r['resource_type'] == 'patient' else 'operational'}
             for r in rows[:filters.limit]]
    next_cursor = None
    if len(rows) > filters.limit:
        last = rows[filters.limit - 1]
        next_cursor = encode_cursor(secret, binding, last['rank'], last['record_id'])
    return {'organization_id': tenant, 'clinic_id': clinic.id, 'items': items,
            'available_resources': available, 'next_cursor': next_cursor}
