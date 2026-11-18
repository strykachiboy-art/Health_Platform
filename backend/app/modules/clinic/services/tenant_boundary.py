"""GAP-B: independent organization and explicit clinic membership guards.

There is no implicit access from an organization-admin or platform-admin role.
All authorities are server-side and checked again on each request.
"""
from uuid import UUID
from sqlalchemy import select
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import TenantMembership, ClinicMembership


class TenantAccessDenied(PermissionError):
    pass


def _parse_organization_id(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise TenantAccessDenied('Tenant context required')
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise TenantAccessDenied('Invalid tenant context') from exc
    if str(parsed) != value.lower():
        raise TenantAccessDenied('Noncanonical tenant context')
    return parsed


def _validate_user(user_id, session):
    if type(user_id) is not int or user_id <= 0:
        raise TenantAccessDenied('Invalid user context')
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise TenantAccessDenied('Active user required')
    return user


def require_tenant_membership(*, user_id, organization_id, session=None):
    """Returns the active organization membership; does NOT grant clinical access."""
    org_id = _parse_organization_id(organization_id)
    session = session if session is not None else db.session
    _validate_user(user_id, session)
    membership = session.execute(
        select(TenantMembership)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.organization_id == org_id,
            TenantMembership.is_active.is_(True),
            TenantMembership.status == 'active',
        )
    ).scalar_one_or_none()
    if membership is None:
        raise TenantAccessDenied('Active organization membership required')
    return membership


def require_tenant_clinic_access(*, user_id, organization_id, clinic_id, session=None):
    if type(clinic_id) is not int or clinic_id <= 0:
        raise TenantAccessDenied('Invalid clinic context')
    org_id = _parse_organization_id(organization_id)
    session = session if session is not None else db.session
    user = _validate_user(user_id, session)
    require_tenant_membership(user_id=user_id, organization_id=org_id, session=session)
    clinic = session.get(Clinic, clinic_id)
    if clinic is None or clinic.organization_id != org_id:
        raise TenantAccessDenied('Clinic not owned by active organization')
    from app.core.enums.clinic_enums import ClinicStatus
    if clinic.status is not ClinicStatus.ACTIVE:
        raise TenantAccessDenied('Clinic is not active')
    grant = session.execute(
        select(ClinicMembership)
        .where(
            ClinicMembership.user_id == user_id,
            ClinicMembership.organization_id == org_id,
            ClinicMembership.clinic_id == clinic_id,
            ClinicMembership.is_active.is_(True),
            ClinicMembership.role == user.role.value,
        )
    ).scalar_one_or_none()
    if grant is None:
        raise TenantAccessDenied('Explicit active clinic-role membership required')
    return clinic_id


def list_authorized_clinics_for_user(*, user_id, status=None, session=None):
    session = session if session is not None else db.session
    user = _validate_user(user_id, session)
    statement = (
        select(Clinic)
        .join(ClinicMembership,
              (ClinicMembership.clinic_id == Clinic.id) &
              (ClinicMembership.organization_id == Clinic.organization_id))
        .join(TenantMembership,
              (TenantMembership.user_id == ClinicMembership.user_id) &
              (TenantMembership.organization_id == ClinicMembership.organization_id))
        .where(
            ClinicMembership.user_id == user_id,
            ClinicMembership.role == user.role.value,
            ClinicMembership.is_active.is_(True),
            TenantMembership.is_active.is_(True),
            TenantMembership.status == 'active',
        )
        .order_by(Clinic.name.asc(), Clinic.id.asc())
    )
    if status is not None:
        statement = statement.where(Clinic.status == status)
    return list(session.execute(statement).scalars().all())
