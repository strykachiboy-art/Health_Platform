from __future__ import annotations

from datetime import datetime, timezone

from flask_jwt_extended import get_jwt
from redis.exceptions import ConnectionError, TimeoutError

from app import extensions
from app.core.exceptions import ValidationError


REVOKED_TOKEN_PREFIX = "auth:revoked:"


def _revoked_token_key(
    jti: str,
) -> str:
    return f"{REVOKED_TOKEN_PREFIX}{jti}"


def _token_remaining_seconds(
    jwt_payload: dict | None = None,
) -> int:
    if jwt_payload is None:
        jwt_payload = get_jwt()

    exp = jwt_payload.get(
        "exp"
    )

    if exp is None:
        raise ValidationError(
            "JWT expiration is missing"
        )

    now = datetime.now(
        timezone.utc
    ).timestamp()

    remaining = int(
        exp - now
    )

    return max(
        remaining,
        1,
    )


def _validate_user_id(
    user_id: int,
) -> None:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
    ):
        raise ValidationError(
            "User ID must be a positive integer"
        )


def revoke_token(
    jwt_payload: dict,
) -> None:
    if extensions.redis_client is None:
        raise ValidationError(
            "Redis is not available for token revocation"
        )

    jti = jwt_payload.get(
        "jti"
    )

    if not jti:
        raise ValidationError(
            "JWT ID is missing"
        )

    try:
        extensions.redis_client.setex(
            _revoked_token_key(jti),
            _token_remaining_seconds(
                jwt_payload
            ),
            "1",
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ValidationError(
            "Unable to access token revocation storage"
        ) from exc


def revoke_current_token() -> None:
    revoke_token(
        get_jwt()
    )


def revoke_user_tokens(
    user_id: int,
) -> None:
    """
    Invalidate all currently issued tokens for a user.

    The token_version increment participates in the caller's
    existing database transaction.
    """

    _validate_user_id(
        user_id
    )

    # Import locally to avoid the circular dependency that occurs
    # during app.extensions initialization.
    from app.core.auth.user.models.user_model import User
    from app.extensions import db

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise ValidationError(
            f"User {user_id} not found"
        )

    user.token_version += 1


def is_token_revoked(
    jwt_payload: dict,
) -> bool:
    # Fail closed when Redis is unavailable.
    if extensions.redis_client is None:
        return True

    jti = jwt_payload.get(
        "jti"
    )

    if not jti:
        return True

    try:
        if extensions.redis_client.exists(
            _revoked_token_key(jti)
        ):
            return True

    except (
        ConnectionError,
        TimeoutError,
    ):
        # Authentication must fail closed if
        # revocation storage cannot be checked.
        return True

    token_user_id = jwt_payload.get(
        "sub"
    )

    token_version = jwt_payload.get(
        "token_version"
    )

    if token_user_id is None:
        return True

    if token_version is None:
        return True

    try:
        token_user_id = int(
            token_user_id
        )
        token_version = int(
            token_version
        )
    except (
        TypeError,
        ValueError,
    ):
        return True

    from app.core.auth.user.models.user_model import User
    from app.extensions import db

    user = db.session.get(
        User,
        token_user_id,
    )

    if user is None:
        return True

    if not user.is_active:
        return True

    if user.token_version != token_version:
        return True

    # Protect every jwt_required() route, including refresh, context switch,
    # and restored sessions. Role promotion and MFA factor rotation invalidate
    # older privileged credentials without trusting the Flutter client.
    try:
        from app.core.auth.user.services.super_admin_login_mfa_service import (
            token_has_current_mfa_authority,
        )
        if not token_has_current_mfa_authority(user, jwt_payload):
            return True
    except Exception:
        # Any provider/schema/policy failure must reject, not skip, MFA.
        return True

    # Tenant membership and MFA are independent mandatory checks.
    try:
        from app.modules.clinic.services.session_authority import validate_bound_claims
        if not validate_bound_claims(jwt_payload, user):
            return True
    except Exception:
        return True

    return False
