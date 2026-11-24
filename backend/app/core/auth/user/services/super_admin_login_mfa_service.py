"""Staged, backend-authoritative Super Admin login MFA challenge gate.

Disabled by default. This service creates no JWT or privileged session until a
verified second factor is durably consumed. Production activation requires MFA
schema migration, enrollment, and approved emergency recovery operations.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets

from flask import current_app, g, has_request_context
from flask_jwt_extended import get_jwt
from redis.exceptions import RedisError

from app import extensions
from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.super_admin_mfa_provider import (
    DatabaseSuperAdminRecoveryFactorProvider,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.extensions import db

_CHALLENGE_PREFIX = "auth:sa-mfa:login-challenge:"
_POLICY_VALUES = frozenset({"disabled", "if_enrolled", "required"})


def login_mfa_policy() -> str:
    value = current_app.config.get("AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY", "disabled")
    if not isinstance(value, str) or value.strip().lower() not in _POLICY_VALUES:
        raise ServiceUnavailableError("Privileged login MFA policy is misconfigured")
    return value.strip().lower()


def _binding(user: User, *, required: bool = False) -> str | None:
    if not isinstance(user, User) or not user.is_active or user.role is not Role.SUPER_ADMIN:
        raise ValidationError("Invalid privileged account")
    try:
        binding = DatabaseSuperAdminRecoveryFactorProvider().get_recovery_binding(user)
    except Exception as exc:
        raise ServiceUnavailableError("Privileged MFA provider is unavailable") from exc
    if binding is None and required:
        raise ServiceUnavailableError("Privileged login requires enrolled MFA")
    if binding is not None and (not isinstance(binding, str) or not binding):
        raise ServiceUnavailableError("Privileged MFA enrollment is invalid")
    return binding


def _challenge_ttl() -> int:
    value = current_app.config.get("AUTH_SUPER_ADMIN_LOGIN_MFA_CHALLENGE_TTL_SECONDS", 300)
    if type(value) is not int or not 60 <= value <= 600:
        raise ServiceUnavailableError("Privileged login MFA TTL is misconfigured")
    return value


def _challenge_key(token: str) -> str:
    return _CHALLENGE_PREFIX + hashlib.sha256(token.encode("ascii")).hexdigest()


def maybe_start_login_challenge(user: User) -> dict | None:
    """Return a non-JWT challenge if a Super Admin needs the second factor."""
    if user.role is not Role.SUPER_ADMIN:
        return None
    policy = login_mfa_policy()
    if policy == "disabled":
        return None
    binding = _binding(user, required=(policy == "required"))
    if binding is None:
        # Permitted only in if_enrolled staging policy.
        return None
    client = extensions.redis_client
    if client is None:
        raise ServiceUnavailableError("Privileged login challenge storage unavailable")
    token = secrets.token_urlsafe(48)
    payload = json.dumps({
        "user_id": user.id,
        "token_version": user.token_version,
        "binding": binding,
    }, separators=(",", ":"))
    try:
        saved = client.set(_challenge_key(token), payload, nx=True, ex=_challenge_ttl())
    except RedisError as exc:
        raise ServiceUnavailableError("Privileged login challenge storage unavailable") from exc
    if saved is not True:
        raise ServiceUnavailableError("Privileged login challenge could not be issued")
    return {
        "mfa_required": True,
        "challenge_token": token,
        "challenge_expires_in": _challenge_ttl(),
        "method": "authenticator_or_recovery_code",
    }


def _request_grant(user: User, binding: str) -> bool:
    if not has_request_context():
        return False
    evidence = getattr(g, "_csp_sa_login_mfa_verified", None)
    if evidence is not None and evidence == (user.id, user.token_version, binding):
        return True
    try:
        claims = get_jwt()
    except RuntimeError:
        return False
    return (
        claims.get("sub") == str(user.id)
        and claims.get("token_version") == user.token_version
        and claims.get("role") == Role.SUPER_ADMIN.value
        and claims.get("sa_mfa_verified") is True
        and isinstance(claims.get("sa_mfa_binding"), str)
        and hmac.compare_digest(claims["sa_mfa_binding"], binding)
    )


def require_token_issuance_authority(user: User) -> str | None:
    """Shared issuer gate used by login, Google, refresh and clinic switching."""
    if user.role is not Role.SUPER_ADMIN:
        return None
    policy = login_mfa_policy()
    if policy == "disabled":
        return None
    binding = _binding(user, required=(policy == "required"))
    if binding is None:
        return None
    if not _request_grant(user, binding):
        raise ValidationError("Additional privileged authentication required")
    return binding


def token_has_current_mfa_authority(user: User, claims: dict) -> bool:
    """Invoked by the existing global JWT revocation callback; fail closed."""
    # Reject an old Super Admin access token after role demotion, even if the
    # current login MFA policy is disabled. Refresh tokens historically lack a
    # role claim, so check their current database role separately.
    role = claims.get("role")
    if role == Role.SUPER_ADMIN.value and user.role is not Role.SUPER_ADMIN:
        return False
    if user.role is not Role.SUPER_ADMIN:
        return True
    policy = login_mfa_policy()
    if policy == "disabled":
        return True
    binding = _binding(user, required=(policy == "required"))
    if binding is None:
        return claims.get("sa_mfa_verified") is not True
    return (
        role == Role.SUPER_ADMIN.value
        and claims.get("sa_mfa_verified") is True
        and isinstance(claims.get("sa_mfa_binding"), str)
        and hmac.compare_digest(claims["sa_mfa_binding"], binding)
    )


def complete_login_challenge(*, token: str, factor: str) -> dict:
    """Consume Redis challenge once, then commit factor use before issuing JWTs."""
    if (
        not isinstance(token, str) or len(token) < 40 or len(token) > 256
        or not token.isascii() or not token.isprintable()
        or not isinstance(factor, str) or len(factor) not in (6, 32)
    ):
        raise ValidationError("Invalid or expired MFA challenge")
    if login_mfa_policy() == "disabled":
        raise ValidationError("Invalid or expired MFA challenge")
    client = extensions.redis_client
    if client is None:
        raise ServiceUnavailableError("Privileged login challenge storage unavailable")
    try:
        # Redis GETDEL is atomic across workers: even invalid factor attempts
        # consume the challenge, avoiding a reusable brute-force credential.
        raw = client.getdel(_challenge_key(token))
    except RedisError as exc:
        raise ServiceUnavailableError("Privileged login challenge storage unavailable") from exc
    if not isinstance(raw, str):
        raise ValidationError("Invalid or expired MFA challenge")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Invalid challenge payload")
        user_id = payload["user_id"]
        token_version = payload["token_version"]
        expected_binding = payload["binding"]
        if (
            type(user_id) is not int or user_id <= 0
            or type(token_version) is not int or token_version < 0
            or not isinstance(expected_binding, str) or not expected_binding
        ):
            raise ValueError("Invalid challenge payload")
    except (ValueError, TypeError, KeyError) as exc:
        raise ValidationError("Invalid or expired MFA challenge") from exc
    user = db.session.get(User, user_id)
    if (user is None or user.role is not Role.SUPER_ADMIN or not user.is_active
            or user.token_version != token_version):
        raise ValidationError("Invalid or expired MFA challenge")
    binding = _binding(user, required=True)
    if not hmac.compare_digest(binding, expected_binding):
        raise ValidationError("Invalid or expired MFA challenge")
    try:
        verified = DatabaseSuperAdminRecoveryFactorProvider().verify_recovery_factor(user, factor)
        from app.core.audit.services.audit_service import create_audit_log
        from app.core.enums.audit_enums import AuditAction
        if not verified:
            db.session.rollback()
            # No factor or challenge is ever included in the audit evidence.
            create_audit_log(
                action=AuditAction.UPDATE,
                entity_type="User",
                entity_id=user.id,
                description="Privileged MFA verification rejected",
                user_id=user.id,
            )
            db.session.commit()
            raise ValidationError("Invalid or expired MFA challenge")
        # Keep OTP/recovery consumption durable before any privileged token
        # is minted. If token issuance fails, user must sign in again.
        create_audit_log(
            action=AuditAction.LOGIN,
            entity_type="User",
            entity_id=user.id,
            description="Privileged login completed after MFA",
            user_id=user.id,
        )
        user.last_login_at = db.func.now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    g._csp_sa_login_mfa_verified = (user.id, user.token_version, binding)
    from app.core.auth.user.services.user_service import issue_auth_tokens
    return issue_auth_tokens(user)
