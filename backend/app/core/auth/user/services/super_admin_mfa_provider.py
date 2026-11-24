"""Code-only Super Admin TOTP/recovery provider for the existing recovery contract.

NOT registered in Flask production. NO HTTP enrollment route. NO mandatory login
MFA or schema migration is activated by this module. The G03 security gate
will wire registration, session challenges and anti-bypass token checks.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.core.auth.user.models.user_model import User
from app.core.auth.user.models.super_admin_mfa_model import (
    SuperAdminMfaEnrollment,
    SuperAdminMfaRecoveryCode,
)
from app.core.auth.user.services.super_admin_mfa_otp import (
    generate_recovery_codes,
    generate_totp_secret,
    match_totp_step,
    recovery_code_digest,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.core.security.encryption import (
    decrypt_credentials,
    encrypt_credentials,
    get_encryption_key_version,
)
from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


def _validate_super_admin(user):
    if not isinstance(user, User) or user.role is not Role.SUPER_ADMIN or not user.is_active:
        raise ValidationError("Super Admin account required")
    if user.id is None:
        raise ValidationError("Persisted Super Admin account required")


def _pepper() -> str:
    # Deliberately no default or fallback to SECRET_KEY.
    value = current_app.config.get("SUPER_ADMIN_MFA_RECOVERY_PEPPER")
    if not isinstance(value, str) or len(value) < 32:
        raise ServiceUnavailableError("Privileged recovery is not configured")
    return value


def _decrypt_secret(enrollment):
    try:
        data = decrypt_credentials(
            enrollment.encrypted_totp_secret,
            enrollment.encryption_key_version,
        )
        secret = data["super_admin_totp_secret"]
        if not isinstance(secret, str):
            raise ValueError("Invalid stored secret")
        return secret
    except Exception as exc:
        raise ServiceUnavailableError("Privileged recovery factor is unavailable") from exc


def _row(user_id: int, *, for_update: bool = False):
    return db.session.execute(
        db.select(SuperAdminMfaEnrollment).where(
            SuperAdminMfaEnrollment.user_id == user_id
        ).with_for_update() if for_update else db.select(SuperAdminMfaEnrollment).where(
            SuperAdminMfaEnrollment.user_id == user_id
        )
    ).scalar_one_or_none()


def begin_pending_enrollment(user: User) -> str:
    """Internal service only. The future endpoint MUST require recent reauth.

    Returns one-time authenticator setup secret; caller must never log/persist
    it in Flutter. Does not affect current logins or reset policy.
    """
    _validate_super_admin(user)
    _pepper()
    existing = _row(user.id, for_update=True)
    if existing is not None and existing.confirmed_at is not None:
        raise ValidationError("MFA already enrolled; verified rotation is required")
    secret = generate_totp_secret()
    encrypted = encrypt_credentials({"super_admin_totp_secret": secret})
    key_version = get_encryption_key_version()
    if existing is None:
        existing = SuperAdminMfaEnrollment(user_id=user.id, encrypted_totp_secret=encrypted,
                                           encryption_key_version=key_version,
                                           binding_id=secrets.token_hex(24))
        db.session.add(existing)
    else:
        existing.encrypted_totp_secret = encrypted
        existing.encryption_key_version = key_version
        existing.binding_id = secrets.token_hex(24)
    existing.confirmed_at = None
    existing.last_accepted_step = None
    existing.pending_expires_at = _utcnow() + timedelta(minutes=10)
    db.session.flush()
    return secret


def confirm_pending_enrollment(user: User, code: str, *, now: int | float | None = None) -> list[str]:
    """Complete enrollment with a valid authenticator code and one-time codes."""
    _validate_super_admin(user)
    pepper = _pepper()
    existing = _row(user.id, for_update=True)
    if existing is None or existing.confirmed_at is not None:
        raise ValidationError("No pending MFA enrollment")
    if existing.pending_expires_at is None or existing.pending_expires_at.replace(
        tzinfo=timezone.utc
    ) <= _utcnow():
        raise ValidationError("MFA enrollment expired")
    accepted = match_totp_step(secret=_decrypt_secret(existing), code=code, now=now)
    if accepted is None:
        raise ValidationError("Invalid authenticator code")
    codes = generate_recovery_codes()
    for item in list(existing.recovery_codes):
        db.session.delete(item)
    db.session.flush()
    for plain in codes:
        db.session.add(SuperAdminMfaRecoveryCode(
            enrollment_id=existing.id,
            code_digest=recovery_code_digest(code=plain, pepper=pepper),
        ))
    existing.confirmed_at = _utcnow()
    existing.pending_expires_at = None
    existing.binding_id = secrets.token_hex(24)
    existing.last_accepted_step = accepted
    db.session.flush()
    return codes


class DatabaseSuperAdminRecoveryFactorProvider:
    """Adapter for the existing SuperAdminRecoveryFactorProvider protocol."""

    name = "database-totp-v1"

    def get_recovery_binding(self, user: User) -> str | None:
        _validate_super_admin(user)
        row = _row(user.id)
        if row is None or row.confirmed_at is None:
            return None
        return row.binding_id

    def verify_recovery_factor(self, user: User, factor: str) -> bool:
        _validate_super_admin(user)
        if not isinstance(factor, str) or len(factor) > 512:
            return False
        row = _row(user.id, for_update=True)
        if row is None or row.confirmed_at is None:
            return False
        if len(factor) == 6 and factor.isascii() and factor.isdigit():
            step = match_totp_step(
                secret=_decrypt_secret(row), code=factor,
                last_accepted_step=row.last_accepted_step,
            )
            if step is None:
                return False
            row.last_accepted_step = step
            db.session.flush()
            return True
        try:
            digest = recovery_code_digest(code=factor, pepper=_pepper())
        except ValueError:
            return False
        for item in row.recovery_codes:
            if item.consumed_at is None and hmac.compare_digest(item.code_digest, digest):
                item.consumed_at = _utcnow()
                db.session.flush()
                return True
        return False
