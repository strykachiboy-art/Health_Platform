"""GAP-A: fail-closed organization membership and clinic ownership checks.

Context is derived from server-owned clinic records, never from a client-supplied
organization claim. Full JWT/worker/realtime isolation remains a GAP-B gate.
"""
from uuid import UUID
from sqlalchemy import select
from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.models.organization_model import TenantMembership


class TenantAccessDenied(PermissionError):
    pass


def _parse_organization_id(value):
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise TenantAccessDenied('Tenant context required')
    try:
        return UUID(value)
    except (ValueError, AttributeError) as exc:
        raise TenantAccessDenied('Invalid tenant context') from exc


def require_tenant_clinic_access(*, user_id, organization_id, clinic_id, session=None):
    """Validate active user, active tenant membership and actual clinic owner."""
    if type(user_id) is not int or user_id <= 0 or type(clinic_id) is not int or clinic_id <= 0:
        raise TenantAccessDenied('Invalid user or clinic context')
    org_id = _parse_organization_id(organization_id)
    session = session if session is not None else db.session
    membership = TenantMembership.__table__
    users = User.__table__
    clinics = Clinic.__table__
    # Core tables avoid accidental ORM mapper initialization during a guard.
    permitted = session.execute(
        select(membership.c.id)
        .select_from(
            membership.join(users, users.c.id == membership.c.user_id)
            .join(clinics, clinics.c.organization_id == membership.c.organization_id)
        )
        .where(
            users.c.is_active.is_(True),
            membership.c.user_id == user_id,
            membership.c.organization_id == org_id,
            membership.c.is_active.is_(True),
            clinics.c.id == clinic_id,
            clinics.c.organization_id == org_id,
        )
    ).first()
    if permitted is None:
        raise TenantAccessDenied('Tenant membership and clinic ownership required')
    return clinic_id


def list_authorized_clinics_for_user(*, user_id, status=None, session=None):
    """Only active memberships may yield clinic rows; never query all tenants."""
    if type(user_id) is not int or user_id <= 0:
        raise TenantAccessDenied('Invalid user context')
    session = session if session is not None else db.session
    statement = (
        select(Clinic)
        .join(TenantMembership, TenantMembership.organization_id == Clinic.organization_id)
        .join(User, User.id == TenantMembership.user_id)
        .where(
            TenantMembership.user_id == user_id,
            TenantMembership.is_active.is_(True),
            User.is_active.is_(True),
        )
        .order_by(Clinic.name.asc(), Clinic.id.asc())
    )
    if status is not None:
        statement = statement.where(Clinic.status == status)
    return list(session.execute(statement).scalars().all())
