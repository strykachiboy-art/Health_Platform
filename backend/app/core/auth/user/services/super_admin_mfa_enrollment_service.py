"""Explicitly staged, current-account Super Admin MFA enrollment.

No new authentication service: all tokens come from the existing auth issuer.
These operations require an authenticated Super Admin and a fresh password
check, and are disabled by default. Google-only accounts cannot enroll through
password reauthentication and must remain excluded from mandatory activation
until an approved Google reauthentication flow is available.
"""
from __future__ import annotations

from flask import current_app
from app.core.audit.services.audit_service import create_audit_log
from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.super_admin_mfa_provider import (
    DatabaseSuperAdminRecoveryFactorProvider,
    begin_pending_enrollment,
    confirm_pending_enrollment,
)
from app.core.auth.user.services.token_service import revoke_user_tokens
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.extensions import db


def _assert_owner(user: User) -> None:
    if not isinstance(user, User) or not user.is_active or user.role is not Role.SUPER_ADMIN:
        raise ValidationError("Active Super Admin session required")


def _enrollment_enabled() -> bool:
    value = current_app.config.get("AUTH_SUPER_ADMIN_MFA_ENROLLMENT_ENABLED", False)
    if type(value) is not bool:
        raise ServiceUnavailableError("MFA enrollment configuration is invalid")
    return value


def _assert_enabled() -> None:
    if not _enrollment_enabled():
        raise ServiceUnavailableError("MFA enrollment is not enabled")


def get_enrollment_status(user: User) -> dict:
    _assert_owner(user)
    if not _enrollment_enabled():
        # Do not query non-deployed MFA tables while feature is disabled.
        return {"enrollment_enabled": False, "enrolled": None}
    binding = DatabaseSuperAdminRecoveryFactorProvider().get_recovery_binding(user)
    return {"enrollment_enabled": True, "enrolled": binding is not None}


def begin_enrollment(user: User, current_password: str) -> dict:
    _assert_owner(user)
    _assert_enabled()
    if (
        not isinstance(current_password, str)
        or not 1 <= len(current_password) <= 128
        or not user.password_hash
        or not user.check_password(current_password)
    ):
        raise ValidationError("Current account reauthentication failed")
    try:
        secret = begin_pending_enrollment(user)
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="User", entity_id=user.id,
            description="Super Admin MFA enrollment started",
            user_id=user.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    # Sent once over authenticated TLS; never audit, enqueue or save in client.
    return {"setup_secret": secret, "expires_in": 600}


def confirm_enrollment(user: User, code: str) -> dict:
    _assert_owner(user)
    _assert_enabled()
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        raise ValidationError("Invalid authenticator code")
    try:
        codes = confirm_pending_enrollment(user, code)
        # Invalidate all existing sessions on enrollment/binding change.
        revoke_user_tokens(user.id)
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="User", entity_id=user.id,
            description="Super Admin MFA enrollment confirmed; sessions invalidated",
            user_id=user.id,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    # Only time recovery codes are returned. Backend persists keyed hashes.
    return {"recovery_codes": codes, "reauthentication_required": True}
