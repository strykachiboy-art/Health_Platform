"""SaaS GAP-B organization roles and explicit clinic grants.

An organization role NEVER grants clinical data access. A clinic grant must be
explicit, active, and consistent with the user's server-side clinical role.
"""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy.dialects.postgresql import UUID
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class HospitalOrganization(db.Model):
    __tablename__ = 'hospital_organizations'

    id = db.Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = db.Column(db.String(150), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    clinics = db.relationship('Clinic', back_populates='organization')
    memberships = db.relationship('TenantMembership', back_populates='organization')


class TenantMembership(db.Model):
    """Explicit tenant-wide administration rights, never a clinical grant."""
    __tablename__ = 'tenant_memberships'
    __table_args__ = (
        db.UniqueConstraint('organization_id', 'user_id', name='uq_tenant_memberships_org_user'),
        db.CheckConstraint(
            "role IN ('member', 'organization_admin', 'billing_admin')",
            name='ck_tenant_memberships_role',
        ),
        db.CheckConstraint(
            "status IN ('invited', 'active', 'suspended', 'revoked')",
            name='ck_tenant_memberships_status',
        ),
    )

    id = db.Column(db.BigInteger().with_variant(db.Integer(), 'sqlite'), primary_key=True, autoincrement=True)
    organization_id = db.Column(UUID(as_uuid=True), db.ForeignKey('hospital_organizations.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'))
    role = db.Column(db.String(32), nullable=False, default='member', server_default='member')
    status = db.Column(db.String(16), nullable=False, default='invited', server_default='invited')
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    organization = db.relationship('HospitalOrganization', back_populates='memberships')


class ClinicMembership(db.Model):
    """A user must have one explicit active grant for each operational site."""
    __tablename__ = 'clinic_memberships'
    __table_args__ = (
        db.UniqueConstraint('clinic_id', 'user_id', name='uq_clinic_memberships_clinic_user'),
        db.ForeignKeyConstraint(
            ['clinic_id', 'organization_id'],
            ['clinics.id', 'clinics.organization_id'],
            name='fk_clinic_memberships_clinic_same_organization',
        ),
        db.ForeignKeyConstraint(
            ['organization_id', 'user_id'],
            ['tenant_memberships.organization_id', 'tenant_memberships.user_id'],
            name='fk_clinic_memberships_user_same_organization',
        ),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False)
    clinic_id = db.Column(db.Integer, nullable=False)
    organization_id = db.Column(UUID(as_uuid=True), nullable=False)
    role = db.Column(db.String(32), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
