from __future__ import annotations

from collections.abc import Mapping
import secrets
from urllib.parse import urlencode

import requests
from flask import current_app
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_auth_requests
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
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)


GOOGLE_AUTHORIZATION_URL = (
    "https://accounts.google.com/o/oauth2/v2/auth"
)

GOOGLE_TOKEN_URL = (
    "https://oauth2.googleapis.com/token"
)

GOOGLE_USERINFO_URL = (
    "https://openidconnect.googleapis.com/v1/userinfo"
)


class _BoundedGoogleAuthRequest(google_auth_requests.Request):
    """Apply a bounded network timeout to Google ID-token certificate requests."""

    def __call__(
        self,
        url,
        method="GET",
        body=None,
        headers=None,
        timeout=10,
        **kwargs,
    ):
        return super().__call__(
            url,
            method=method,
            body=body,
            headers=headers,
            timeout=timeout,
            **kwargs,
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

    try:
        response = requests.post(
            GOOGLE_TOKEN_URL,
            data=payload,
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ValidationError(
            "Unable to communicate with Google"
        ) from exc

    if _is_redirect_response(response):
        raise ValidationError(
            "Google authorization code exchange returned an unexpected redirect"
        )

    if not response.ok:
        raise ValidationError(
            "Google authorization code exchange failed"
        )

    try:
        token_data = response.json()
    except ValueError as exc:
        raise ValidationError(
            "Invalid response received from Google"
        ) from exc

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

    try:
        response = requests.get(
            GOOGLE_USERINFO_URL,
            headers=headers,
            timeout=10,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise ValidationError(
            "Unable to retrieve Google user information"
        ) from exc

    if _is_redirect_response(response):
        raise ValidationError(
            "Google user information returned an unexpected redirect"
        )

    if not response.ok:
        raise ValidationError(
            "Unable to retrieve Google user information"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise ValidationError(
            "Invalid user information received from Google"
        ) from exc

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
) -> GoogleUserInfoSchema:
    """Verify a Google-issued ID token for this backend audience."""

    if (
        not isinstance(token, str)
        or not token
        or token != token.strip()
        or len(token) > 8192
    ):
        raise ValidationError("Google ID token is required or invalid")

    client_id = current_app.config.get("GOOGLE_CLIENT_ID")
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