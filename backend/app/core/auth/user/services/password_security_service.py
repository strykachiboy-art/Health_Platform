from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import secrets
from urllib.parse import quote, urlparse

from flask import current_app
from redis.exceptions import ConnectionError, TimeoutError

from app import extensions
from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.recovery_factor_service import (
    resolve_super_admin_recovery_factor_state,
    verify_super_admin_recovery_factor,
)
from app.core.auth.user.services.token_service import (
    revoke_user_tokens,
)
from app.core.auth.user.services.user_service import (
    get_user_by_email,
)
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import (
    RateLimitError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.notifications.providers.email_provider import (
    EmailNotificationProvider,
)
from app.core.utils.decorators import transactional
from app.extensions import celery, db


PASSWORD_RESET_TOKEN_PREFIX = (
    "auth:password-reset:token:"
)
PASSWORD_RESET_CURRENT_PREFIX = (
    "auth:password-reset:current:"
)
PASSWORD_RESET_CLAIM_PREFIX = (
    "auth:password-reset:claim:"
)
PASSWORD_RESET_ISSUE_LOCK_PREFIX = (
    "auth:password-reset:issue-lock:"
)
PASSWORD_RESET_REQUEST_COOLDOWN_PREFIX = (
    "auth:password-reset:request:cooldown:"
)
PASSWORD_RESET_REQUEST_HOUR_PREFIX = (
    "auth:password-reset:request:hour:"
)
PASSWORD_RESET_REQUEST_DAY_PREFIX = (
    "auth:password-reset:request:day:"
)
PASSWORD_CHANGE_FAILURE_PREFIX = (
    "auth:password-change:fail:"
)
PASSWORD_RESET_MFA_FAILURE_PREFIX = (
    "auth:password-reset:mfa-fail:"
)

PASSWORD_RESET_GENERIC_ERROR = (
    "Invalid or expired password reset token"
)

_RATE_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
if current > tonumber(ARGV[2]) then
    return 0
end
return 1
"""

_CONSUME_RESET_TOKEN_SCRIPT = """
if redis.call('GET', KEYS[2]) == ARGV[1] then
    redis.call('DEL', KEYS[2])
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[3])
redis.call('DEL', KEYS[4])
return 1
"""

_DISCARD_RESET_TOKEN_SCRIPT = """
if redis.call('GET', KEYS[2]) == ARGV[1] then
    redis.call('DEL', KEYS[2])
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[3])
return 1
"""

_INCREMENT_FAILURE_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return current
"""


@dataclass(slots=True)
class _SecurityEmail:
    title: str
    message: str


@dataclass(frozen=True, slots=True)
class _IssuedPasswordResetToken:
    token: str
    recovery_factor_required: bool


def _redis_client():
    client = extensions.redis_client

    if client is None:
        raise ServiceUnavailableError(
            "Password security service is temporarily unavailable"
        )

    return client


def _positive_config_int(
    name: str,
    default: int,
) -> int:
    value = current_app.config.get(
        name,
        default,
    )

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ServiceUnavailableError(
            "Password security service is misconfigured"
        )

    return value


def _normalize_email(
    email: str,
) -> str:
    if not isinstance(email, str):
        raise ValidationError(
            "A valid email is required"
        )

    email = email.lower().strip()

    if (
        not email
        or "@" not in email
        or len(email) > 120
    ):
        raise ValidationError(
            "A valid email is required"
        )

    return email


def _validate_new_password(
    new_password: str,
    confirm_password: str,
) -> str:
    if not isinstance(new_password, str):
        raise ValidationError(
            "New password is required"
        )

    if not isinstance(confirm_password, str):
        raise ValidationError(
            "Password confirmation is required"
        )

    if new_password != confirm_password:
        raise ValidationError(
            "Password confirmation does not match"
        )

    if len(new_password) < 8:
        raise ValidationError(
            "Password must be at least 8 characters"
        )

    if len(new_password) > 128:
        raise ValidationError(
            "Password cannot exceed 128 characters"
        )

    if not new_password.strip():
        raise ValidationError(
            "Password cannot be blank"
        )

    return new_password


def _password_reset_identity_digest(
    email: str,
) -> str:
    secret = current_app.config.get(
        "SECRET_KEY"
    )

    if not isinstance(secret, str) or not secret:
        raise ServiceUnavailableError(
            "Password security service is misconfigured"
        )

    return hmac.new(
        secret.encode("utf-8"),
        email.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _token_digest(
    token: str,
) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _reset_token_key(
    digest: str,
) -> str:
    return (
        f"{PASSWORD_RESET_TOKEN_PREFIX}"
        f"{digest}"
    )


def _reset_current_key(
    user_id: int,
) -> str:
    return (
        f"{PASSWORD_RESET_CURRENT_PREFIX}"
        f"{user_id}"
    )


def _reset_claim_key(
    digest: str,
) -> str:
    return (
        f"{PASSWORD_RESET_CLAIM_PREFIX}"
        f"{digest}"
    )


def _reset_issue_lock_key(
    user_id: int,
) -> str:
    return (
        f"{PASSWORD_RESET_ISSUE_LOCK_PREFIX}"
        f"{user_id}"
    )


def _change_failure_key(
    user_id: int,
) -> str:
    return (
        f"{PASSWORD_CHANGE_FAILURE_PREFIX}"
        f"{user_id}"
    )


def _reset_mfa_failure_key(
    digest: str,
) -> str:
    return (
        f"{PASSWORD_RESET_MFA_FAILURE_PREFIX}"
        f"{digest}"
    )


def _allow_password_reset_request(
    email: str,
) -> bool:
    client = _redis_client()

    digest = _password_reset_identity_digest(
        email
    )

    cooldown_seconds = _positive_config_int(
        "AUTH_PASSWORD_RESET_REQUEST_COOLDOWN_SECONDS",
        60,
    )
    max_per_hour = _positive_config_int(
        "AUTH_PASSWORD_RESET_REQUEST_MAX_PER_HOUR",
        3,
    )
    max_per_day = _positive_config_int(
        "AUTH_PASSWORD_RESET_REQUEST_MAX_PER_DAY",
        8,
    )

    cooldown_key = (
        f"{PASSWORD_RESET_REQUEST_COOLDOWN_PREFIX}"
        f"{digest}"
    )
    hour_key = (
        f"{PASSWORD_RESET_REQUEST_HOUR_PREFIX}"
        f"{digest}"
    )
    day_key = (
        f"{PASSWORD_RESET_REQUEST_DAY_PREFIX}"
        f"{digest}"
    )

    try:
        acquired = client.set(
            cooldown_key,
            "1",
            nx=True,
            ex=cooldown_seconds,
        )

        if not acquired:
            return False

        hour_allowed = client.eval(
            _RATE_WINDOW_SCRIPT,
            1,
            hour_key,
            3600,
            max_per_hour,
        )

        if int(hour_allowed or 0) != 1:
            return False

        day_allowed = client.eval(
            _RATE_WINDOW_SCRIPT,
            1,
            day_key,
            86400,
            max_per_day,
        )

        return int(day_allowed or 0) == 1

    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password security service is temporarily unavailable"
        ) from exc


def _assert_change_password_attempt_allowed(
    user_id: int,
) -> None:
    client = _redis_client()

    max_failures = _positive_config_int(
        "AUTH_PASSWORD_CHANGE_MAX_FAILURES",
        5,
    )

    try:
        raw_count = client.get(
            _change_failure_key(
                user_id
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password security service is temporarily unavailable"
        ) from exc

    try:
        count = int(
            raw_count or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        count = max_failures

    if count >= max_failures:
        raise RateLimitError(
            "Too many password change attempts. Try again later."
        )


def _record_change_password_failure(
    user_id: int,
) -> None:
    client = _redis_client()

    window_seconds = _positive_config_int(
        "AUTH_PASSWORD_CHANGE_FAILURE_WINDOW_SECONDS",
        900,
    )
    max_failures = _positive_config_int(
        "AUTH_PASSWORD_CHANGE_MAX_FAILURES",
        5,
    )

    try:
        client.eval(
            _RATE_WINDOW_SCRIPT,
            1,
            _change_failure_key(
                user_id
            ),
            window_seconds,
            max_failures,
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password security service is temporarily unavailable"
        ) from exc


def _clear_change_password_failures(
    user_id: int,
) -> None:
    client = extensions.redis_client

    if client is None:
        return

    try:
        client.delete(
            _change_failure_key(
                user_id
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ):
        current_app.logger.warning(
            "password.change.failure_counter_cleanup_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
            },
        )


@transactional
def change_password(
    *,
    user_id: int,
    current_password: str,
    new_password: str,
    confirm_password: str,
    ip_address: str | None = None,
) -> None:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if not isinstance(
        current_password,
        str,
    ) or not current_password:
        raise ValidationError(
            "Current password is required"
        )

    new_password = _validate_new_password(
        new_password,
        confirm_password,
    )

    _assert_change_password_attempt_allowed(
        user_id
    )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None or not user.is_active:
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user.is_oauth_only:
        raise ValidationError(
            "Password changes are unavailable for OAuth-only accounts"
        )

    if not user.check_password(
        current_password
    ):
        _record_change_password_failure(
            user.id
        )
        raise ValidationError(
            "Current password is incorrect"
        )

    if user.check_password(
        new_password
    ):
        raise ValidationError(
            "New password must be different from the current password"
        )

    previous_token_version = (
        user.token_version
    )

    user.set_password(
        new_password
    )

    revoke_user_tokens(
        user.id
    )

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="User",
        entity_id=user.id,
        description="User changed password",
        old_value={
            "token_version": (
                previous_token_version
            ),
        },
        new_value={
            "credential_changed": True,
            "token_version": (
                user.token_version
            ),
        },
        user_id=user.id,
        clinic_id=user.clinic_id,
        ip_address=ip_address,
    )

    _clear_change_password_failures(
        user.id
    )


def _password_reset_public_url() -> str:
    value = current_app.config.get(
        "PASSWORD_RESET_PUBLIC_URL"
    )

    if (
        not isinstance(value, str)
        or not value.strip()
    ):
        raise ServiceUnavailableError(
            "Password reset service is not configured"
        )

    value = value.strip()
    parsed = urlparse(
        value
    )

    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ServiceUnavailableError(
            "Password reset service is misconfigured"
        )

    return value


def _config_bool(
    name: str,
    default: bool,
) -> bool:
    value = current_app.config.get(
        name,
        default,
    )

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return True

        if normalized in {
            "0",
            "false",
            "no",
            "off",
        }:
            return False

    raise ServiceUnavailableError(
        "Password reset service is misconfigured"
    )


def _platform_email_credentials() -> dict:
    host = current_app.config.get(
        "PLATFORM_EMAIL_HOST"
    )
    from_email = current_app.config.get(
        "PLATFORM_EMAIL_FROM_EMAIL"
    )
    port = current_app.config.get(
        "PLATFORM_EMAIL_PORT",
        587,
    )
    use_tls = _config_bool(
        "PLATFORM_EMAIL_USE_TLS",
        True,
    )
    use_ssl = _config_bool(
        "PLATFORM_EMAIL_USE_SSL",
        False,
    )

    if (
        not isinstance(host, str)
        or not host.strip()
        or not isinstance(from_email, str)
        or not from_email.strip()
        or isinstance(port, bool)
        or not isinstance(port, int)
        or port <= 0
        or port > 65535
        or not isinstance(use_tls, bool)
        or not isinstance(use_ssl, bool)
        or use_tls == use_ssl
    ):
        raise ServiceUnavailableError(
            "Password reset service is not configured"
        )

    return {
        "host": host.strip(),
        "port": port,
        "username": current_app.config.get(
            "PLATFORM_EMAIL_USERNAME"
        ),
        "password": current_app.config.get(
            "PLATFORM_EMAIL_PASSWORD"
        ),
        "from_email": from_email.strip(),
        "use_tls": use_tls,
        "use_ssl": use_ssl,
    }


def _validate_password_reset_delivery_configuration() -> None:
    _password_reset_public_url()
    _platform_email_credentials()


def request_password_reset(
    *,
    email: str,
) -> None:
    normalized_email = _normalize_email(
        email
    )

    # Validate platform delivery before doing anything account-specific.
    # This keeps configuration failures independent of account existence.
    _validate_password_reset_delivery_configuration()

    if not _allow_password_reset_request(
        normalized_email
    ):
        # Deliberately return the same public response. Per-account
        # throttling must not become an account-enumeration oracle.
        return

    try:
        process_password_reset_request.delay(
            normalized_email
        )
    except Exception as exc:
        current_app.logger.exception(
            "password.reset.enqueue_failed",
            extra={
                "operational_event": True,
            },
        )
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc


def _acquire_reset_issue_lock(
    user_id: int,
) -> bool:
    client = _redis_client()

    try:
        return bool(
            client.set(
                _reset_issue_lock_key(
                    user_id
                ),
                "1",
                nx=True,
                ex=30,
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc


def _release_reset_issue_lock(
    user_id: int,
) -> None:
    client = extensions.redis_client

    if client is None:
        return

    try:
        client.delete(
            _reset_issue_lock_key(
                user_id
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ):
        current_app.logger.warning(
            "password.reset.issue_lock_cleanup_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
            },
        )


def _issue_password_reset_token(
    user: User,
) -> _IssuedPasswordResetToken:
    client = _redis_client()

    ttl_seconds = _positive_config_int(
        "AUTH_PASSWORD_RESET_TOKEN_TTL_SECONDS",
        900,
    )

    recovery_state = (
        resolve_super_admin_recovery_factor_state(
            user
        )
    )

    token = secrets.token_urlsafe(
        48
    )
    digest = _token_digest(
        token
    )

    token_payload = {
        "user_id": user.id,
        "token_version": (
            user.token_version
        ),
        "recovery_factor_required": (
            recovery_state.required
        ),
    }

    if recovery_state.required:
        token_payload[
            "recovery_factor_provider"
        ] = recovery_state.provider_name
        token_payload[
            "recovery_factor_binding"
        ] = recovery_state.binding_digest

    payload = json.dumps(
        token_payload,
        separators=(",", ":"),
    )

    current_key = _reset_current_key(
        user.id
    )
    token_key = _reset_token_key(
        digest
    )

    try:
        previous_digest = client.get(
            current_key
        )

        pipeline = client.pipeline(
            transaction=True
        )

        if previous_digest:
            pipeline.delete(
                _reset_token_key(
                    previous_digest
                )
            )
            pipeline.delete(
                _reset_claim_key(
                    previous_digest
                )
            )
            pipeline.delete(
                _reset_mfa_failure_key(
                    previous_digest
                )
            )

        pipeline.setex(
            token_key,
            ttl_seconds,
            payload,
        )
        pipeline.setex(
            current_key,
            ttl_seconds,
            digest,
        )
        pipeline.execute()

    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc

    return _IssuedPasswordResetToken(
        token=token,
        recovery_factor_required=(
            recovery_state.required
        ),
    )


def _build_password_reset_link(
    token: str,
    *,
    recovery_factor_required: bool = False,
) -> str:
    base_url = (
        _password_reset_public_url()
    )

    fragment = (
        f"token={quote(token, safe='')}"
    )

    if recovery_factor_required:
        # This flag is intentionally generic. It tells the client to
        # display its additional-verification UI without exposing the
        # privileged role or the MFA provider in URLs or logs.
        fragment += (
            "&verification=additional"
        )

    # Use the fragment rather than a query parameter so ordinary
    # web-server access logs and HTTP referrers do not receive the
    # reset credential.
    return (
        f"{base_url}"
        f"#{fragment}"
    )


def _send_password_reset_email(
    *,
    email: str,
    token: str,
    recovery_factor_required: bool = False,
) -> None:
    credentials = (
        _platform_email_credentials()
    )

    ttl_seconds = _positive_config_int(
        "AUTH_PASSWORD_RESET_TOKEN_TTL_SECONDS",
        900,
    )
    ttl_minutes = max(
        1,
        math.ceil(
            ttl_seconds / 60
        ),
    )

    reset_link = (
        _build_password_reset_link(
            token,
            recovery_factor_required=(
                recovery_factor_required
            ),
        )
    )

    verification_message = ""

    if recovery_factor_required:
        verification_message = (
            "\n\nThis account also requires its enrolled additional "
            "recovery verification. Have your authenticator or recovery "
            "credential available before continuing."
        )

    payload = _SecurityEmail(
        title=(
            "Reset your Clinic System Pro password"
        ),
        message=(
            "A password reset was requested for your account.\n\n"
            f"Use this single-use link within {ttl_minutes} minutes:\n"
            f"{reset_link}"
            f"{verification_message}\n\n"
            "If you did not request this change, you can ignore this "
            "message. Your current password remains unchanged."
        ),
    )

    provider = EmailNotificationProvider(
        credentials=credentials
    )

    provider.send(
        notification=payload,
        email=email,
    )


def _discard_reset_token(
    *,
    digest: str,
    user_id: int,
) -> None:
    client = extensions.redis_client

    if client is None:
        return

    try:
        client.eval(
            _DISCARD_RESET_TOKEN_SCRIPT,
            3,
            _reset_token_key(
                digest
            ),
            _reset_current_key(
                user_id
            ),
            _reset_mfa_failure_key(
                digest
            ),
            digest,
        )
    except (
        ConnectionError,
        TimeoutError,
    ):
        current_app.logger.warning(
            "password.reset.token_cleanup_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
            },
        )


def _process_password_reset_request(
    email: str,
) -> bool:
    normalized_email = _normalize_email(
        email
    )

    user = get_user_by_email(
        normalized_email
    )

    # Keep public responses generic. Ineligible accounts simply do
    # not receive a recovery message.
    if (
        user is None
        or not user.is_active
        or user.is_oauth_only
    ):
        return False

    if not _acquire_reset_issue_lock(
        user.id
    ):
        return False

    issued = None
    digest = None

    try:
        issued = _issue_password_reset_token(
            user
        )
        digest = _token_digest(
            issued.token
        )

        try:
            _send_password_reset_email(
                email=user.email,
                token=issued.token,
                recovery_factor_required=(
                    issued.recovery_factor_required
                ),
            )
        except Exception:
            _discard_reset_token(
                digest=digest,
                user_id=user.id,
            )
            raise

        return True

    finally:
        issued = None
        _release_reset_issue_lock(
            user.id
        )


@celery.task(
    name="process_password_reset_request",
)
def process_password_reset_request(
    email: str,
) -> bool:
    try:
        return _process_password_reset_request(
            email
        )
    except Exception:
        current_app.logger.exception(
            "password.reset.delivery_failed",
            extra={
                "operational_event": True,
            },
        )
        return False


def _acquire_reset_claim(
    digest: str,
) -> bool:
    client = _redis_client()

    ttl_seconds = _positive_config_int(
        "AUTH_PASSWORD_RESET_CLAIM_TTL_SECONDS",
        60,
    )

    try:
        return bool(
            client.set(
                _reset_claim_key(
                    digest
                ),
                "1",
                nx=True,
                ex=ttl_seconds,
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc


def _release_reset_claim(
    digest: str,
) -> None:
    client = extensions.redis_client

    if client is None:
        return

    try:
        client.delete(
            _reset_claim_key(
                digest
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ):
        current_app.logger.warning(
            "password.reset.claim_cleanup_failed",
            extra={
                "operational_event": True,
            },
        )


def _load_reset_payload(
    digest: str,
) -> dict | None:
    client = _redis_client()

    try:
        raw_payload = client.get(
            _reset_token_key(
                digest
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc

    if not raw_payload:
        return None

    try:
        payload = json.loads(
            raw_payload
        )
        user_id = int(
            payload["user_id"]
        )
        token_version = int(
            payload["token_version"]
        )
        recovery_factor_required = payload.get(
            "recovery_factor_required",
            False,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None

    if (
        user_id <= 0
        or token_version < 0
        or not isinstance(
            recovery_factor_required,
            bool,
        )
    ):
        return None

    recovery_factor_provider = None
    recovery_factor_binding = None

    if recovery_factor_required:
        recovery_factor_provider = payload.get(
            "recovery_factor_provider"
        )
        recovery_factor_binding = payload.get(
            "recovery_factor_binding"
        )

        if (
            not isinstance(
                recovery_factor_provider,
                str,
            )
            or not recovery_factor_provider
            or len(
                recovery_factor_provider
            ) > 80
            or not isinstance(
                recovery_factor_binding,
                str,
            )
            or len(
                recovery_factor_binding
            ) != 64
        ):
            return None

    try:
        current_digest = client.get(
            _reset_current_key(
                user_id
            )
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc

    if (
        not isinstance(
            current_digest,
            str,
        )
        or not hmac.compare_digest(
            current_digest,
            digest,
        )
    ):
        return None

    return {
        "user_id": user_id,
        "token_version": token_version,
        "recovery_factor_required": (
            recovery_factor_required
        ),
        "recovery_factor_provider": (
            recovery_factor_provider
        ),
        "recovery_factor_binding": (
            recovery_factor_binding
        ),
    }


def _record_reset_mfa_failure(
    digest: str,
) -> int:
    client = _redis_client()

    ttl_seconds = _positive_config_int(
        "AUTH_PASSWORD_RESET_TOKEN_TTL_SECONDS",
        900,
    )

    try:
        count = client.eval(
            _INCREMENT_FAILURE_SCRIPT,
            1,
            _reset_mfa_failure_key(
                digest
            ),
            ttl_seconds,
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc

    try:
        return int(
            count
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ServiceUnavailableError(
            "Password reset service is temporarily unavailable"
        ) from exc


def _consume_reset_token(
    *,
    digest: str,
    user_id: int,
) -> None:
    client = extensions.redis_client

    if client is None:
        current_app.logger.error(
            "password.reset.post_commit_cleanup_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
            },
        )
        return

    try:
        client.eval(
            _CONSUME_RESET_TOKEN_SCRIPT,
            4,
            _reset_token_key(
                digest
            ),
            _reset_current_key(
                user_id
            ),
            _reset_claim_key(
                digest
            ),
            _reset_mfa_failure_key(
                digest
            ),
            digest,
        )
    except (
        ConnectionError,
        TimeoutError,
    ):
        # The database commit has already invalidated the reset
        # token logically by incrementing token_version. Cleanup can
        # safely fall back to TTL expiry.
        current_app.logger.error(
            "password.reset.post_commit_cleanup_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
            },
        )





def _verify_required_reset_recovery_factor(
    *,
    user: User,
    payload: dict,
    digest: str,
    recovery_factor: str | None,
) -> None:
    token_requires_factor = payload.get(
        "recovery_factor_required",
        False,
    )

    current_state = (
        resolve_super_admin_recovery_factor_state(
            user
        )
    )

    if (
        current_state.required
        and not token_requires_factor
    ):
        # A policy/enrollment tightening after token issuance must never
        # leave an older email-only SUPER_ADMIN reset credential usable.
        _discard_reset_token(
            digest=digest,
            user_id=user.id,
        )
        raise ValidationError(
            PASSWORD_RESET_GENERIC_ERROR
        )

    if not token_requires_factor:
        return

    verification = (
        verify_super_admin_recovery_factor(
            user=user,
            factor=recovery_factor,
            expected_provider_name=payload[
                "recovery_factor_provider"
            ],
            expected_binding_digest=payload[
                "recovery_factor_binding"
            ],
        )
    )

    if not verification.binding_matches:
        # MFA enrollment/provider changes after issuance invalidate this
        # credential immediately; an older recovery link must never
        # downgrade to email-only recovery.
        _discard_reset_token(
            digest=digest,
            user_id=user.id,
        )
        raise ValidationError(
            PASSWORD_RESET_GENERIC_ERROR
        )

    if verification.verified:
        return

    failure_count = (
        _record_reset_mfa_failure(
            digest
        )
    )
    max_failures = _positive_config_int(
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_MAX_FAILURES",
        5,
    )

    if failure_count >= max_failures:
        _discard_reset_token(
            digest=digest,
            user_id=user.id,
        )

    # Use the generic token response so factor failures do not become a
    # password-reset token or privileged-role oracle.
    raise ValidationError(
        PASSWORD_RESET_GENERIC_ERROR
    )


def queue_password_security_notice(
    *,
    user_id: int,
    event: str,
) -> None:
    if event not in {
        "changed",
        "reset",
    }:
        return

    try:
        process_password_security_notice.delay(
            user_id,
            event,
        )
    except Exception as exc:
        current_app.logger.warning(
            "password.security_notice_enqueue_failed",
            exc_info=True,
            extra={
                "operational_event": True,
                "user_id": user_id,
                "event": event,
            },
        )


def _process_password_security_notice(
    user_id: int,
    event: str,
) -> bool:
    if event not in {
        "changed",
        "reset",
    }:
        return False

    user = db.session.get(
        User,
        user_id,
    )

    if user is None or not user.email:
        return False

    try:
        credentials = (
            _platform_email_credentials()
        )
    except ServiceUnavailableError:
        return False

    if event == "reset":
        title = (
            "Your Clinic System Pro password was reset"
        )
        message = (
            "Your account password was reset successfully.\n\n"
            "If you did not perform this reset, contact your "
            "administrator or support team immediately."
        )
    else:
        title = (
            "Your Clinic System Pro password was changed"
        )
        message = (
            "Your account password was changed successfully.\n\n"
            "If you did not make this change, contact your "
            "administrator or support team immediately."
        )

    provider = EmailNotificationProvider(
        credentials=credentials
    )

    provider.send(
        notification=_SecurityEmail(
            title=title,
            message=message,
        ),
        email=user.email,
    )

    return True


@celery.task(
    name="process_password_security_notice",
)
def process_password_security_notice(
    user_id: int,
    event: str,
) -> bool:
    try:
        return _process_password_security_notice(
            user_id,
            event,
        )
    except Exception:
        current_app.logger.exception(
            "password.security_notice_delivery_failed",
            extra={
                "operational_event": True,
                "user_id": user_id,
                "event": event,
            },
        )
        return False


def confirm_password_reset(
    *,
    token: str,
    new_password: str,
    confirm_password: str,
    recovery_factor: str | None = None,
    ip_address: str | None = None,
) -> None:
    if (
        not isinstance(token, str)
        or len(token) < 32
        or len(token) > 512
    ):
        raise ValidationError(
            PASSWORD_RESET_GENERIC_ERROR
        )

    new_password = _validate_new_password(
        new_password,
        confirm_password,
    )

    digest = _token_digest(
        token
    )

    if not _acquire_reset_claim(
        digest
    ):
        raise ValidationError(
            PASSWORD_RESET_GENERIC_ERROR
        )

    committed = False
    database_touched = False
    user_id = None

    try:
        payload = _load_reset_payload(
            digest
        )

        if payload is None:
            raise ValidationError(
                PASSWORD_RESET_GENERIC_ERROR
            )

        user_id = payload[
            "user_id"
        ]

        database_touched = True

        user = db.session.get(
            User,
            user_id,
        )

        if (
            user is None
            or not user.is_active
            or user.is_oauth_only
            or user.token_version
            != payload["token_version"]
        ):
            _discard_reset_token(
                digest=digest,
                user_id=user_id,
            )
            raise ValidationError(
                PASSWORD_RESET_GENERIC_ERROR
            )

        _verify_required_reset_recovery_factor(
            user=user,
            payload=payload,
            digest=digest,
            recovery_factor=recovery_factor,
        )

        if user.check_password(
            new_password
        ):
            raise ValidationError(
                "New password must be different from the current password"
            )

        previous_token_version = (
            user.token_version
        )

        user.set_password(
            new_password
        )

        revoke_user_tokens(
            user.id
        )

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="User",
            entity_id=user.id,
            description="User reset password",
            old_value={
                "token_version": (
                    previous_token_version
                ),
            },
            new_value={
                "credential_changed": True,
                "password_recovery": True,
                "token_version": (
                    user.token_version
                ),
            },
            user_id=user.id,
            clinic_id=user.clinic_id,
            ip_address=ip_address,
        )

        db.session.commit()
        committed = True

    except Exception:
        if (
            database_touched
            and not committed
        ):
            db.session.rollback()
        raise

    finally:
        if not committed:
            _release_reset_claim(
                digest
            )

    _consume_reset_token(
        digest=digest,
        user_id=user_id,
    )

    queue_password_security_notice(
        user_id=user_id,
        event="reset",
    )
