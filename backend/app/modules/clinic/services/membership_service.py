"""GAP-B Gate 3: explicit auditable grant/revoke service.

The platform's SUPER_ADMIN can bootstrap organization administration, but
never gains clinical data access from that role. Normal organization admins
can manage grants only in their own active organization. HTTP member-management
UI is intentionally deferred to SaaS-E; services remain server-authoritative.
"""
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.token_service import revoke_user_tokens
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.clinic_enums import ClinicStatus
from app.core.utils.decorators import transactional
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied, _parse_organization_id, require_tenant_membership,
)

TENANT_ROLES = frozenset({'member', 'organization_admin', 'billing_admin'})


def _user(user_id):
    if type(user_id) is not int or user_id <= 0:
        raise TenantAccessDenied('Invalid member identity')
    user = db.session.get(User, user_id)
    if user is None or not user.is_active:
        raise TenantAccessDenied('Member is not active')
    return user


def _authorized_manager(actor_id, organization_id):
    actor = _user(actor_id)
    # Exceptional platform provisioning never confers clinical record access.
    if actor.role is Role.SUPER_ADMIN:
        return actor
    member = require_tenant_membership(
        user_id=actor_id, organization_id=organization_id)
    if member.role != 'organization_admin':
        raise TenantAccessDenied('Organization administration grant required')
    return actor


def _org(organization_id):
    oid = _parse_organization_id(organization_id)
    org = db.session.get(HospitalOrganization, oid)
    if org is None:
        raise TenantAccessDenied('Organization unavailable')
    return org


def _audit(action, kind, subject_id, actor_id, description, clinic_id=None, value=None):
    create_audit_log(
        action=action, entity_type=kind, entity_id=subject_id,
        description=description, user_id=actor_id, clinic_id=clinic_id,
        new_value=value,
    )


@transactional
def grant_tenant_membership(*, actor_id, user_id, organization_id, role='member'):
    if role not in TENANT_ROLES:
        raise TenantAccessDenied('Invalid organization role')
    org = _org(organization_id)
    _authorized_manager(actor_id, org.id)
    target = _user(user_id)
    membership = db.session.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == target.id,
            TenantMembership.organization_id == org.id,
        )
    ).scalar_one_or_none()
    if membership is not None and membership.is_active and membership.status == 'active' and membership.role == role:
        return membership
    if membership is None:
        membership = TenantMembership(
            organization_id=org.id, user_id=user_id,
            role=role, status='active', is_active=True)
        db.session.add(membership)
    else:
        membership.role = role
        membership.status = 'active'
        membership.is_active = True
    db.session.flush()
    revoke_user_tokens(user_id)
    _audit(AuditAction.CREATE, 'TenantMembership', membership.id, actor_id,
           'Explicit organization membership granted', value={'organization_id': str(org.id), 'role': role})
    return membership


@transactional
def revoke_tenant_membership(*, actor_id, user_id, organization_id):
    org = _org(organization_id)
    _authorized_manager(actor_id, org.id)
    _user(user_id)
    member = db.session.execute(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.organization_id == org.id,
        )
    ).scalar_one_or_none()
    if member is None or (member.status == 'revoked' and not member.is_active):
        raise TenantAccessDenied('No active membership to revoke')
    member.status = 'revoked'
    member.is_active = False
    for clinic_grant in db.session.execute(
        select(ClinicMembership).where(
            ClinicMembership.user_id == user_id,
            ClinicMembership.organization_id == org.id,
            ClinicMembership.is_active.is_(True),
        )
    ).scalars():
        clinic_grant.is_active = False
        clinic_grant.revoked_at = datetime.now(timezone.utc)
    revoke_user_tokens(user_id)
    _audit(AuditAction.STATUS_CHANGE, 'TenantMembership', member.id, actor_id,
           'Explicit organization membership revoked', value={'organization_id': str(org.id)})
    return member


@transactional
def grant_clinic_membership(*, actor_id, user_id, clinic_id, role):
    if type(clinic_id) is not int or clinic_id <= 0:
        raise TenantAccessDenied('Invalid clinic')
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None or clinic.status is not ClinicStatus.ACTIVE:
        raise TenantAccessDenied('Active clinic required')
    actor = _authorized_manager(actor_id, clinic.organization_id)
    if actor.id == user_id:
        raise TenantAccessDenied('Self-grant of clinical access is prohibited')
    target = _user(user_id)
    member = require_tenant_membership(
        user_id=user_id, organization_id=clinic.organization_id)
    if target.role is Role.SUPER_ADMIN or role != target.role.value:
        raise TenantAccessDenied('Clinic grant must match server-side clinical role')
    grant = db.session.execute(
        select(ClinicMembership).where(
            ClinicMembership.user_id == target.id,
            ClinicMembership.clinic_id == clinic.id,
        )
    ).scalar_one_or_none()
    if grant is not None and grant.organization_id != clinic.organization_id:
        raise TenantAccessDenied('Existing grant has different organization')
    if grant is not None and grant.is_active and grant.role == role:
        return grant
    if grant is None:
        grant = ClinicMembership(
            organization_id=member.organization_id,
            user_id=user_id, clinic_id=clinic.id, role=role, is_active=True)
        db.session.add(grant)
    else:
        grant.role = role
        grant.is_active = True
        grant.revoked_at = None
    db.session.flush()
    revoke_user_tokens(user_id)
    _audit(AuditAction.CREATE, 'ClinicMembership', grant.id, actor_id,
           'Explicit clinic membership granted',
           value={'organization_id': str(clinic.organization_id), 'clinic_id': clinic.id, 'role': role})
    return grant


@transactional
def revoke_clinic_membership(*, actor_id, user_id, clinic_id):
    if type(clinic_id) is not int or clinic_id <= 0:
        raise TenantAccessDenied('Invalid clinic')
    clinic = db.session.get(Clinic, clinic_id)
    if clinic is None:
        raise TenantAccessDenied('Clinic unavailable')
    _authorized_manager(actor_id, clinic.organization_id)
    _user(user_id)
    grant = db.session.execute(
        select(ClinicMembership).where(
            ClinicMembership.user_id == user_id,
            ClinicMembership.clinic_id == clinic.id,
            ClinicMembership.organization_id == clinic.organization_id,
        )
    ).scalar_one_or_none()
    if grant is None or not grant.is_active:
        raise TenantAccessDenied('No active clinic grant to revoke')
    grant.is_active = False
    grant.revoked_at = datetime.now(timezone.utc)
    revoke_user_tokens(user_id)
    _audit(AuditAction.STATUS_CHANGE, 'ClinicMembership', grant.id, actor_id,
           'Explicit clinic membership revoked',
           value={'organization_id': str(clinic.organization_id), 'clinic_id': clinic.id})
    return grant
