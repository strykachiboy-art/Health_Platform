from __future__ import annotations

from sqlalchemy.orm import joinedload
from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError, ValidationError
from app.core.auth.user.models.user_model import User
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.session_authority import (
    TenantContextDenied, context_from_claims, bound_context_for_issuance,
    validate_platform_clinic, platform_context_from_claims,
)

CLINIC_CONTEXT_CLAIM = 'clinic_context_id'
TENANT_CONTEXT_CLAIM = 'tenant_id'


def _get_user(user_id):
    user = db.session.get(User, user_id, options=(joinedload(User.staff),))
    if user is None:
        raise NotFoundError(f'User {user_id} not found')
    if not user.is_active:
        raise ValidationError('User account is inactive')
    return user


def _get_active_clinic(clinic_id):
    if type(clinic_id) is not int or clinic_id <= 0:
        raise ValidationError('Clinic ID must be a positive integer')
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None:
        raise NotFoundError(f'Clinic {clinic_id} not found')
    if clinic.status is not ClinicStatus.ACTIVE:
        raise ValidationError('Selected clinic is not active')
    return clinic


def _resolve_assigned_clinic_id(clinic_id):
    # Legacy helper retained for compatibility; no longer grants context by
    # itself. Actual request authority comes only from bound JWT claims.
    return None if clinic_id is None else _get_active_clinic(clinic_id).id


def resolve_effective_clinic_id(user_id, jwt_payload):
    _get_user(user_id)
    _, clinic_id = context_from_claims(user_id, jwt_payload)
    return clinic_id


def get_current_clinic_context(user_id, jwt_payload):
    user = _get_user(user_id)
    _, clinic_id = context_from_claims(user_id, jwt_payload)
    platform_id = platform_context_from_claims(user_id, jwt_payload)
    if platform_id is not None:
        clinic = _get_active_clinic(platform_id)
        return {'clinic_id': clinic.id, 'clinic_name': clinic.name, 'source': 'selected'}
    if clinic_id is None:
        return {'clinic_id': None, 'clinic_name': None, 'source': 'system'}
    clinic = _get_active_clinic(clinic_id)
    return {'clinic_id': clinic.id, 'clinic_name': clinic.name,
            'source': 'assigned' if clinic_id == user.clinic_id else 'selected'}


def select_clinic_context(user_id, clinic_id):
    user = _get_user(user_id)
    clinic = _get_active_clinic(clinic_id)
    if user.role is Role.SUPER_ADMIN:
        validate_platform_clinic(user, clinic_id)
    else:
        bound_context_for_issuance(user, clinic_id=clinic_id)
    # The immutable audit row is scoped to the actor's assigned clinic.
    # Keep the legacy clinic_id audit argument when the actor has no
    # different assigned clinic; the target remains in new_value either way.
    audit_clinic_id = clinic.id if user.clinic_id in (None, clinic.id) else None
    create_audit_log(
        action=AuditAction.UPDATE, entity_type='User', entity_id=user.id,
        user_id=user.id, clinic_id=audit_clinic_id,
        description='Authorized platform clinic-context switch' if user.role is Role.SUPER_ADMIN else 'Authorized clinical clinic-context switch',
        new_value={'tenant_id': str(clinic.organization_id), 'clinic_context_id': clinic.id},
    )
    return clinic


def clear_clinic_context(user_id, clinic_id=None):
    user = _get_user(user_id)
    if user.role is not Role.SUPER_ADMIN:
        raise ValidationError('Only a super administrator can clear a clinic context')
    if clinic_id is not None and (type(clinic_id) is not int or clinic_id <= 0):
        raise ValidationError('Invalid clinic context')
    audit_clinic_id = clinic_id if user.clinic_id in (None, clinic_id) else None
    create_audit_log(
        action=AuditAction.UPDATE, entity_type='User', entity_id=user.id,
        user_id=user.id, clinic_id=audit_clinic_id,
        description='Authorized clinical clinic-context cleared',
        old_value={'clinic_context_id': clinic_id},
        new_value={'clinic_context_id': None},
    )


def select_tenant_context(user_id, organization_id):
    user = _get_user(user_id)
    tenant_id, _ = bound_context_for_issuance(user, tenant_id=organization_id,
                                               auto_assign_clinic=False)
    create_audit_log(
        action=AuditAction.UPDATE, entity_type='User', entity_id=user.id,
        user_id=user.id, description='Authorized organization-context switch',
        new_value={'tenant_id': tenant_id, 'clinic_context_id': None},
    )
    return tenant_id


def clear_tenant_context(user_id, previous_tenant_id=None):
    user = _get_user(user_id)
    create_audit_log(
        action=AuditAction.UPDATE, entity_type='User', entity_id=user.id,
        user_id=user.id, description='Authorized organization-context cleared',
        old_value={'tenant_id': previous_tenant_id},
        new_value={'tenant_id': None, 'clinic_context_id': None},
    )
