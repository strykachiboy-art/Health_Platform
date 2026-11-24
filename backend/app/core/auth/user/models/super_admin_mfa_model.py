"""Dedicated durable Super Admin MFA enrollment and single-use recovery code records.

Schema is code-only until a separately approved Alembic migration is applied.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class SuperAdminMfaEnrollment(db.Model):
    __tablename__ = "super_admin_mfa_enrollments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_totp_secret = db.Column(db.Text, nullable=False)
    encryption_key_version = db.Column(db.Integer, nullable=False)
    binding_id = db.Column(db.String(64), nullable=False)
    confirmed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    pending_expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_accepted_step = db.Column(db.BigInteger, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    recovery_codes = db.relationship(
        "SuperAdminMfaRecoveryCode",
        back_populates="enrollment",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    __table_args__ = (
        db.CheckConstraint("encryption_key_version >= 1", name="ck_super_admin_mfa_key_version"),
    )


class SuperAdminMfaRecoveryCode(db.Model):
    __tablename__ = "super_admin_mfa_recovery_codes"

    id = db.Column(db.Integer, primary_key=True)
    enrollment_id = db.Column(
        db.Integer,
        db.ForeignKey("super_admin_mfa_enrollments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_digest = db.Column(db.String(64), nullable=False, unique=True)
    consumed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    enrollment = db.relationship("SuperAdminMfaEnrollment", back_populates="recovery_codes")
