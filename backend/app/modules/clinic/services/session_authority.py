"""GAP-B Gate 4 server-authoritative tenant/clinic JWT binding.

Never trust tenant_id or clinic_context_id as authorization by themselves.
There is no ambient clinic when the JWT has no explicitly validated context.
An unscoped patient registration/login remains possible, but it confers no
organization or clinical permission.
"""
from uuid import UUID
from sqlalchemy import select
from app.core.exceptions import DomainError
from app.core.enums.clinic_enums import ClinicStatus
from app.core.auth.user.models.user_model import User
from app.extensions import db
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import TenantMembership
from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied, _parse_organization_id,
    require_tenant_membership, require_tenant_clinic_access,
)


class TenantContextDenied(DomainError):
    status_code = 403


def validate_bound_claims(payload, user, *, session=None):
    """Boolean checked after Redis JTI and token_version on EVERY JWT use."""
    if not isinstance(payload, dict) or user is None or not user.is_active:
        return False
    role = payload.get('role')
    if role is not None and role != user.role.value:
        return False
    tenant_present = 'tenant_id' in payload
    clinic_present = 'clinic_context_id' in payload
    if not tenant_present:
        # Unscoped patient/onboarding or platform session is allowed, but no
        # clinic is assigned by implication. A clinic-only claim is rejected.
        return not clinic_present
    raw_tenant = payload.get('tenant_id')
    if not isinstance(raw_tenant, str) or raw_tenant != raw_tenant.lower():
        return False
    session = session if session is not None else db.session
    try:
        organization_id = _parse_organization_id(raw_tenant)
        require_tenant_membership(
            user_id=user.id, organization_id=organization_id, session=session)
        if clinic_present:
            clinic_id = payload['clinic_context_id']
            require_tenant_clinic_access(
                user_id=user.id, organization_id=organization_id,
                clinic_id=clinic_id, session=session)
    except (TenantAccessDenied, ValueError, TypeError):
        return False
    return True


def bound_context_for_issuance(user, *, tenant_id=None, clinic_id=None,
                               auto_assign_clinic=True):
    """Resolve the server-approved context before signing access/refresh JWTs.

    On initial login, auto-assign only the user's existing clinic if explicit
    active memberships already authorize it. An invalid explicit switch
    always fails closed (never becomes an unscoped session).
    """
    if not isinstance(user, User) or not user.is_active:
        raise TenantContextDenied('Active authenticated user required')
    if clinic_id is not None:
        if type(clinic_id) is not int or clinic_id <= 0:
            raise TenantContextDenied('Invalid clinic context')
        clinic = db.session.get(Clinic, clinic_id)
        if clinic is None or clinic.status is not ClinicStatus.ACTIVE:
            raise TenantContextDenied('Clinic unavailable')
        oid = clinic.organization_id
        if tenant_id is not None:
            try:
                if _parse_organization_id(tenant_id) != oid:
                    raise TenantContextDenied('Clinic belongs to another tenant')
            except TenantAccessDenied as exc:
                raise TenantContextDenied('Invalid tenant context') from exc
        try:
            require_tenant_clinic_access(user_id=user.id,
                organization_id=oid, clinic_id=clinic.id)
        except TenantAccessDenied as exc:
            raise TenantContextDenied('Clinic grant required') from exc
        return str(oid), clinic.id
    if tenant_id is not None:
        try:
            oid = _parse_organization_id(tenant_id)
            require_tenant_membership(user_id=user.id, organization_id=oid)
        except TenantAccessDenied as exc:
            raise TenantContextDenied('Organization grant required') from exc
        return str(oid), None
    if auto_assign_clinic and user.clinic_id is not None:
        # Preserve patient-first onboarding: no implicit clinical grant is
        # provisioned if an assigned clinic lacks active memberships.
        assigned = db.session.get(Clinic, user.clinic_id)
        if assigned is not None and assigned.status is ClinicStatus.ACTIVE:
            try:
                require_tenant_clinic_access(user_id=user.id,
                    organization_id=assigned.organization_id,
                    clinic_id=assigned.id)
            except TenantAccessDenied:
                pass
            else:
                return str(assigned.organization_id), assigned.id
    return None, None


def context_from_claims(user_id, claims):
    if type(user_id) is not int or user_id <= 0:
        raise TenantContextDenied('Invalid identity')
    user = db.session.get(User, user_id)
    if not validate_bound_claims(claims, user):
        raise TenantContextDenied('Tenant session is revoked or invalid')
    return claims.get('tenant_id'), claims.get('clinic_context_id')
