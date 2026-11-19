from __future__ import annotations

from collections.abc import Mapping
import hashlib
import secrets
from urllib.parse import urlencode

import requests as _requests
from flask import current_app
from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import id_token as google_id_token
from redis.exceptions import ConnectionError, TimeoutError
from sqlalchemy import select

from app import extensions
from app.extensions import db

from app.core.audit.services.audit_service import (
    create_audit_log,
)
from app.core.auth.user.models.user_auth_identity_model import (
    UserAuthIdentity,
)
from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.user_service import (
    issue_auth_tokens,
)
from app.core.auth.user.schema.user_schema import (
    GoogleUserInfoSchema,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.auth.user.services.google_oauth_transport import (
    requests_for_google_oauth,
)
from app.core.auth.user.services.google_auth_circuit_breaker import (
    CircuitProtectedGoogleAuthRequest as _BoundedGoogleAuthRequest,
    GoogleAuthTransientError,
    call_google_auth_with_circuit,
    is_transient_google_http_status,
)
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)

requests = requests_for_google_oauth(_requests)


GOOGLE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
)

GOOGLE_TOKEN_URL = (
    "https://oauth2.googleapis.com/token"
)

GOOGLE_USERINFO_URL = (
    "https://openidconnect.googleapis.com/v1/userinfo"
)


def _is_redirect_response(response: requests.Response) -> bool:
    status_code = getattr(response, "status_code", None)
    return isinstance(status_code, int) and 300 <= status_code < 400

GOOGLE_PROVIDER = "google"

GOOGLE_OAUTH_STATE_PREFIX = (
    "oauth:google:state:"
)

GOOGLE_OAUTH_STATE_TTL = 600

GOOGLE_OAUTH_STATE_CONSUME_SCRIPT = """
local value = redis.call('GET', KEYS[1])
if not value then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""
# ============================================================================
# OAUTH STATE
# ============================================================================


def _google_state_key(
    state: str,
) -> str:
    return (
        f"{GOOGLE_OAUTH_STATE_PREFIX}{state}"
    )


def create_google_oauth_state() -> str:
    """
    Generate and persist a one-time Google OAuth state value.
    """

    if extensions.redis_client is None:
        raise ValidationError(
            "Redis is not available for Google authentication"
        )

    state = secrets.token_urlsafe(32)

    try:
        extensions.redis_client.setex(
            _google_state_key(state),
            GOOGLE_OAUTH_STATE_TTL,
            "1",
        )
    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ValidationError(
            "Unable to initialize Google authentication"
        ) from exc

    return state


def validate_google_oauth_state(
    state: str,
) -> None:
    """
    Validate and consume a previously issued OAuth state.

    State values are intentionally one-time use.
    """

    if not state:
        raise ValidationError(
            "Google OAuth state is required"
        )

    if extensions.redis_client is None:
        raise ValidationError(
            "Redis is not available for Google authentication"
        )

    key = _google_state_key(state)

    try:
        consumed = extensions.redis_client.eval(
            GOOGLE_OAUTH_STATE_CONSUME_SCRIPT,
            1,
            key,
        )

        if int(consumed or 0) != 1:
            raise ValidationError(
                "Invalid or expired Google OAuth state"
            )

    except (
        ConnectionError,
        TimeoutError,
    ) as exc:
        raise ValidationError(
            "Unable to validate Google OAuth state"
        ) from exc


# ============================================================================
# GOOGLE AUTHORIZATION URL
# ============================================================================


def get_google_authorization_url() -> tuple[str, str]:
    """
    Build the Google OAuth authorization URL and return
    both the URL and generated state value.
    """

    client_id = current_app.config.get(
        "GOOGLE_CLIENT_ID"
    )

    redirect_uri = current_app.config.get(
        "GOOGLE_REDIRECT_URI"
    )

    if not client_id:
        raise ValidationError(
            "Google OAuth client ID is not configured"
        )

    if not redirect_uri:
        raise ValidationError(
            "Google OAuth redirect URI is not configured"
        )

    state = create_google_oauth_state()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    }

    authorization_url = (
        f"{GOOGLE_AUTHORIZATION_URL}"
        f"?{urlencode(params)}"
    )

    return (
        authorization_url,
        state,
    )


# ============================================================================
# GOOGLE AUTHORIZATION CODE EXCHANGE
# ============================================================================


def exchange_google_code(
    code: str,
) -> dict:
    """
    Exchange a Google authorization code for Google tokens.
    """

    if not code:
        raise ValidationError(
            "Google authorization code is required"
        )

    client_id = current_app.config.get(
        "GOOGLE_CLIENT_ID"
    )

    client_secret = current_app.config.get(
        "GOOGLE_CLIENT_SECRET"
    )

    redirect_uri = current_app.config.get(
        "GOOGLE_REDIRECT_URI"
    )

    if not client_id:
        raise ValidationError(
            "Google OAuth client ID is not configured"
        )

    if not client_secret:
        raise ValidationError(
            "Google OAuth client secret is not configured"
        )

    if not redirect_uri:
        raise ValidationError(
            "Google OAuth redirect URI is not configured"
        )

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    def _exchange():
        try:
            response = requests.post(
                GOOGLE_TOKEN_URL,
                data=payload,
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GoogleAuthTransientError(
                "Unable to communicate with Google"
            ) from exc

        if _is_redirect_response(response):
            raise ValidationError(
                "Google authorization code exchange returned an unexpected redirect"
            )

        if not response.ok:
            if is_transient_google_http_status(
                getattr(response, "status_code", None)
            ):
                raise GoogleAuthTransientError(
                    "Google authorization code exchange is temporarily unavailable"
                )
            raise ValidationError(
                "Google authorization code exchange failed"
            )

        return response

    response = call_google_auth_with_circuit(
        operation="token_exchange",
        function=_exchange,
    )

    try:
        token_data = response.json()
    except ValueError as exc:
        raise ValidationError(
            "Invalid response received from Google"
        ) from exc

    if not isinstance(token_data, dict):
        raise ValidationError("Invalid response received from Google")

    access_token = token_data.get(
        "access_token"
    )

    if not access_token:
        raise ValidationError(
            "Google access token was not returned"
        )

    return token_data


# ============================================================================
# GOOGLE USER INFORMATION
# ============================================================================


def get_google_user_info(
    google_access_token: str,
) -> GoogleUserInfoSchema:
    """
    Retrieve and validate the authenticated Google user's
    OpenID Connect profile.
    """

    if not google_access_token:
        raise ValidationError(
            "Google access token is required"
        )

    headers = {
        "Authorization": (
            f"Bearer {google_access_token}"
        ),
        "Accept": "application/json",
    }

    def _userinfo():
        try:
            response = requests.get(
                GOOGLE_USERINFO_URL,
                headers=headers,
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GoogleAuthTransientError(
                "Unable to retrieve Google user information"
            ) from exc

        if _is_redirect_response(response):
            raise ValidationError(
                "Google user information returned an unexpected redirect"
            )

        if not response.ok:
            if is_transient_google_http_status(
                getattr(response, "status_code", None)
            ):
                raise GoogleAuthTransientError(
                    "Google user information is temporarily unavailable"
                )
            raise ValidationError(
                "Unable to retrieve Google user information"
            )

        return response

    response = call_google_auth_with_circuit(
        operation="userinfo",
        function=_userinfo,
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise ValidationError(
            "Invalid user information received from Google"
        ) from exc

    if not isinstance(data, dict):
        raise ValidationError("Invalid user information received from Google")

    provider_user_id = data.get(
        "sub"
    )

    email = data.get(
        "email"
    )

    email_verified = data.get(
        "email_verified"
    )

    if not provider_user_id:
        raise ValidationError(
            "Google user ID is missing"
        )

    if not email:
        raise ValidationError(
            "Google email is missing"
        )

    if email_verified is not True:
        raise ValidationError(
            "Google email must be verified"
        )

    return GoogleUserInfoSchema(
        provider_user_id=str(
            provider_user_id
        ),
        email=(
            str(email)
            .lower()
            .strip()
        ),
        first_name=data.get(
            "given_name"
        ),
        last_name=data.get(
            "family_name"
        ),
        picture=data.get(
            "picture"
        ),
        email_verified=True,
    )


# ============================================================================
# GOOGLE IDENTITY LOOKUP
# ============================================================================


def _get_user_by_google_identity(
    provider_user_id: str,
) -> User | None:
    """
    Resolve an existing user through the Google provider identity.
    """

    if not provider_user_id:
        return None

    statement = select(
        UserAuthIdentity
    ).where(
        UserAuthIdentity.provider == GOOGLE_PROVIDER,
        UserAuthIdentity.provider_user_id
        == provider_user_id,
    )

    identity = db.session.execute(
        statement
    ).scalar_one_or_none()

    if identity is None:
        return None

    return db.session.get(
        User,
        identity.user_id,
    )


# ============================================================================
# GET OR CREATE GOOGLE USER
# ============================================================================


def _get_or_create_google_user(
    google_user: GoogleUserInfoSchema,
) -> tuple[User, bool]:
    """
    Resolve linked Google users or create a new patient; account linking is separate.
    """

    provider_user_id = (
        google_user.provider_user_id
    )

    email = (
        str(google_user.email)
        .lower()
        .strip()
    )

    # ------------------------------------------------------------------------
    # Existing Google identity
    # ------------------------------------------------------------------------

    existing_user = (
        _get_user_by_google_identity(
            provider_user_id
        )
    )

    if existing_user is not None:
        return (
            existing_user,
            False,
        )

    # ------------------------------------------------------------------------
    # Existing local account by email
    # ------------------------------------------------------------------------

    user_statement = select(
        User
    ).where(
        User.email == email
    )

    existing_user = db.session.execute(
        user_statement
    ).scalar_one_or_none()

    if existing_user is not None:
        identity_statement = select(
            UserAuthIdentity
        ).where(
            UserAuthIdentity.provider
            == GOOGLE_PROVIDER,
            UserAuthIdentity.user_id
            == existing_user.id,
        )

        existing_identity = (
            db.session.execute(
                identity_statement
            ).scalar_one_or_none()
        )

        # Existing user already has a Google identity.
        if existing_identity is not None:
            if (
                existing_identity.provider_user_id
                != provider_user_id
            ):
                raise ConflictError(
                    "This user already has a different "
                    "Google identity linked"
                )

            return (
                existing_user,
                False,
            )

        raise ConflictError(
            "An account with this email already exists. "
            "Sign in with the existing method."
        )

    # ------------------------------------------------------------------------
    # New Google-only user
    # ------------------------------------------------------------------------

    user = User(
        email=email,
        role=Role.PATIENT,
        password_hash=None,
    )

    db.session.add(
        user
    )

    db.session.flush()

    identity = UserAuthIdentity(
        user_id=user.id,
        provider=GOOGLE_PROVIDER,
        provider_user_id=provider_user_id,
    )

    db.session.add(
        identity
    )

    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="User",
        entity_id=user.id,
        description=(
            "Google user registered: "
            f"{email}"
        ),
    )

    return (
        user,
        True,
    )


# ============================================================================
# GOOGLE USER AUTHENTICATION
# ============================================================================


def authenticate_google_user(
    google_user: GoogleUserInfoSchema,
) -> dict:
    """
    Authenticate an already validated Google user against
    the local user system.
    """

    if not google_user.email_verified:
        raise ValidationError(
            "Google email must be verified"
        )

    user, _ = _get_or_create_google_user(
        google_user
    )

    if not user.is_active:
        raise ValidationError(
            "This account has been deactivated"
        )

    tokens = issue_auth_tokens(
        user
    )

    user.last_login_at = db.func.now()

    create_audit_log(
        action=AuditAction.LOGIN,
        entity_type="User",
        entity_id=user.id,
        description=(
            f"User '{user.email}' "
            "logged in with Google"
        ),
        user_id=user.id,
    )

    db.session.commit()

    return tokens


def verify_google_id_token(
    token: str,
    *,
    audience: str | None = None,
    expected_nonce: str | None = None,
) -> GoogleUserInfoSchema:
    """Verify a Google-issued ID token for this backend audience."""

    if (
        not isinstance(token, str)
        or not token
        or token != token.strip()
        or len(token) > 8192
    ):
        raise ValidationError("Google ID token is required or invalid")

    client_id = audience or current_app.config.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise ValidationError(
            "Google OAuth client ID is not configured"
        )

    try:
        claims = google_id_token.verify_oauth2_token(
            token,
            _BoundedGoogleAuthRequest(),
            audience=client_id,
        )
    except (
        ValueError,
        google_auth_exceptions.GoogleAuthError,
    ) as exc:
        raise ValidationError("Invalid Google ID token") from exc

    if not isinstance(claims, Mapping):
        raise ValidationError("Invalid Google ID token")

    if expected_nonce is not None:
        verified_nonce = claims.get("nonce")
        if (
            not isinstance(verified_nonce, str)
            or not secrets.compare_digest(verified_nonce, expected_nonce)
        ):
            raise ValidationError("Invalid Google desktop nonce")

    provider_user_id = claims.get("sub")
    email = claims.get("email")

    if (
        not isinstance(provider_user_id, str)
        or not provider_user_id.strip()
    ):
        raise ValidationError(
            "Google ID token is missing the user ID"
        )

    if (
        not isinstance(email, str)
        or not email.strip()
        or claims.get("email_verified") is not True
    ):
        raise ValidationError(
            "Google ID token must include a verified email"
        )

    given_name = claims.get("given_name")
    family_name = claims.get("family_name")
    picture = claims.get("picture")

    return GoogleUserInfoSchema(
        provider_user_id=provider_user_id,
        email=email.strip().lower(),
        first_name=given_name if isinstance(given_name, str) else None,
        last_name=family_name if isinstance(family_name, str) else None,
        picture=picture if isinstance(picture, str) else None,
        email_verified=True,
    )


def authenticate_google_id_token(
    id_token: str,
) -> dict:
    """Authenticate a native-app user using a verified Google ID token."""

    google_user = verify_google_id_token(id_token)
    return authenticate_google_user(google_user)


def _consume_google_desktop_token_once(id_token: str) -> None:
    """Redis-backed, bounded replay guard for already verified tokens."""
    cache = extensions.redis_client
    if cache is None:
        raise ValidationError("Google desktop replay storage unavailable")
    key = "wd01:google:desktop:used:" + hashlib.sha256(
        id_token.encode("utf-8")
    ).hexdigest()
    try:
        reserved = cache.set(key, "1", nx=True, ex=7200)
    except (ConnectionError, TimeoutError) as exc:
        raise ValidationError("Google desktop replay storage unavailable") from exc
    if reserved is not True:
        raise ValidationError("Google desktop authentication already consumed")


def authenticate_google_desktop_id_token(
    *, id_token: str, nonce: str,
) -> dict:
    """Desktop-only audience and nonce, then existing user/JWT pathway."""
    desktop_client_id = current_app.config.get("GOOGLE_DESKTOP_CLIENT_ID")
    if not desktop_client_id:
        raise ValidationError("Google desktop OAuth is not configured")
    if not isinstance(nonce, str) or len(nonce) < 43 or len(nonce) > 128:
        raise ValidationError("Google desktop nonce is invalid")
    google_user = verify_google_id_token(
        id_token, audience=desktop_client_id, expected_nonce=nonce,
    )
    _consume_google_desktop_token_once(id_token)
    return authenticate_google_user(google_user)


def authenticate_google_desktop_authorization_code(
    *, code: str, code_verifier: str, redirect_uri: str, nonce: str,
) -> dict:
    """Exchange one PKCE-bound desktop code on Flask and issue existing CSP JWTs.

    The Desktop OAuth client secret remains a runtime backend configuration;
    no OAuth code, Google token, or secret is logged, persisted, or retried.
    """
    import re

    client_id = current_app.config.get("GOOGLE_DESKTOP_CLIENT_ID")
    client_secret = current_app.config.get("GOOGLE_DESKTOP_CLIENT_SECRET")
    if not isinstance(client_id, str) or not client_id:
        raise ValidationError("Google desktop OAuth is not configured")
    if not isinstance(client_secret, str) or not client_secret:
        raise ValidationError("Google desktop code exchange is not configured")
    if not isinstance(code, str) or not code or len(code) > 4096:
        raise ValidationError("Invalid Google authorization code")
    if not isinstance(code_verifier, str) or not re.fullmatch(
        r"[A-Za-z0-9._~-]{43,128}", code_verifier,
    ):
        raise ValidationError("Invalid Google PKCE verifier")
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{2,5})", redirect_uri or "")
    if match is None or not (1024 <= int(match.group(1)) <= 65535):
        raise ValidationError("Invalid Google loopback redirect URI")
    if not isinstance(nonce, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{43,128}", nonce,
    ):
        raise ValidationError("Invalid Google desktop nonce")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    def _exchange_once():
        try:
            response = requests.post(
                GOOGLE_TOKEN_URL, data=payload, timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise GoogleAuthTransientError(
                "Google desktop token exchange unavailable"
            ) from exc
        if _is_redirect_response(response):
            raise ValidationError("Google desktop token exchange rejected")
        if not response.ok:
            if is_transient_google_http_status(response.status_code):
                raise GoogleAuthTransientError(
                    "Google desktop token exchange unavailable"
                )
            raise ValidationError("Google desktop token exchange rejected")
        return response

    response = call_google_auth_with_circuit(
        operation="token_exchange", function=_exchange_once,
    )
    try:
        result = response.json()
    except ValueError as exc:
        raise ValidationError("Invalid Google desktop token response") from exc
    if not isinstance(result, dict):
        raise ValidationError("Invalid Google desktop token response")
    id_token = result.get("id_token")
    if not isinstance(id_token, str):
        raise ValidationError("Google desktop ID token is missing")
    identity = verify_google_id_token(
        id_token, audience=client_id, expected_nonce=nonce,
    )
    _consume_google_desktop_token_once(id_token)
    return authenticate_google_user(identity)


# ============================================================================
# GOOGLE CODE AUTHENTICATION
# ============================================================================


def authenticate_google_code(
    code: str,
) -> dict:
    """
    Complete the Google OAuth authorization-code flow.
    """

    token_data = exchange_google_code(
        code
    )

    google_access_token = token_data.get(
        "access_token"
    )

    if not google_access_token:
        raise ValidationError(
            "Google access token is missing"
        )

    google_user = get_google_user_info(
        google_access_token
    )

    return authenticate_google_user(
        google_user
    )
