"""SaaS GAP-A: organization ownership foundation; no billing or auth privileges."""
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
    """Minimal membership foundation; roles and token enforcement are NOT conferred."""
    __tablename__ = 'tenant_memberships'
    __table_args__ = (
        db.UniqueConstraint('organization_id', 'user_id', name='uq_tenant_memberships_org_user'),
    )

    id = db.Column(db.BigInteger, primary_key=True, autoincrement=True)
    organization_id = db.Column(UUID(as_uuid=True), db.ForeignKey('hospital_organizations.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False, server_default=db.text('false'))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    organization = db.relationship('HospitalOrganization', back_populates='memberships')
