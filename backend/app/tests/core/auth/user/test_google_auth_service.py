from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from redis.exceptions import (
    ConnectionError,
    TimeoutError,
)

from app.core.auth.user.models.user_auth_identity_model import (
    UserAuthIdentity,
)
from app.core.auth.user.models.user_model import User
from app.core.auth.user.schema.user_schema import (
    GoogleUserInfoSchema,
)
from app.core.auth.user.services import google_auth_service
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)


# ============================================================================
# OAUTH STATE
# ============================================================================


def test_create_google_oauth_state_raises_when_redis_unavailable():
    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        None,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Redis is not available "
                "for Google authentication"
            ),
        ):
            google_auth_service.create_google_oauth_state()


def test_create_google_oauth_state_stores_state():
    redis_mock = MagicMock()

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        state = (
            google_auth_service.create_google_oauth_state()
        )

    assert state
    assert isinstance(
        state,
        str,
    )
    assert len(state) > 20

    redis_mock.setex.assert_called_once_with(
        (
            f"{google_auth_service.GOOGLE_OAUTH_STATE_PREFIX}"
            f"{state}"
        ),
        google_auth_service.GOOGLE_OAUTH_STATE_TTL,
        "1",
    )


def test_create_google_oauth_state_handles_redis_connection_error():
    redis_mock = MagicMock()

    redis_mock.setex.side_effect = ConnectionError(
        "Redis connection failed"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to initialize "
                "Google authentication"
            ),
        ):
            google_auth_service.create_google_oauth_state()


def test_create_google_oauth_state_handles_redis_timeout_error():
    redis_mock = MagicMock()

    redis_mock.setex.side_effect = TimeoutError(
        "Redis timeout"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to initialize "
                "Google authentication"
            ),
        ):
            google_auth_service.create_google_oauth_state()


def test_create_google_oauth_state_does_not_swallow_unexpected_errors():
    redis_mock = MagicMock()

    redis_mock.setex.side_effect = RuntimeError(
        "Unexpected Redis error"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            RuntimeError,
            match="Unexpected Redis error",
        ):
            google_auth_service.create_google_oauth_state()


@pytest.mark.parametrize(
    "state",
    [
        "",
        None,
    ],
)
def test_validate_google_oauth_state_requires_state(
    state,
):
    with pytest.raises(
        ValidationError,
        match="Google OAuth state is required",
    ):
        google_auth_service.validate_google_oauth_state(
            state
        )


def test_validate_google_oauth_state_raises_when_redis_unavailable():
    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        None,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Redis is not available "
                "for Google authentication"
            ),
        ):
            google_auth_service.validate_google_oauth_state(
                "state"
            )


def test_validate_google_oauth_state_rejects_invalid_state():
    redis_mock = MagicMock()

    redis_mock.eval.return_value = 0

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Invalid or expired "
                "Google OAuth state"
            ),
        ):
            google_auth_service.validate_google_oauth_state(
                "bad-state"
            )

    key = (
        f"{google_auth_service.GOOGLE_OAUTH_STATE_PREFIX}"
        "bad-state"
    )

    redis_mock.eval.assert_called_once_with(
        google_auth_service.GOOGLE_OAUTH_STATE_CONSUME_SCRIPT,
        1,
        key,
    )

def test_validate_google_oauth_state_accepts_and_consumes_valid_state():
    redis_mock = MagicMock()

    redis_mock.eval.return_value = 1

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        result = (
            google_auth_service.validate_google_oauth_state(
                "valid-state"
            )
        )

    assert result is None

    key = (
        f"{google_auth_service.GOOGLE_OAUTH_STATE_PREFIX}"
        "valid-state"
    )

    redis_mock.eval.assert_called_once_with(
        google_auth_service.GOOGLE_OAUTH_STATE_CONSUME_SCRIPT,
        1,
        key,
    )

def test_validate_google_oauth_state_handles_redis_connection_error():
    redis_mock = MagicMock()

    redis_mock.eval.side_effect = ConnectionError(
        "Redis connection failed"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to validate "
                "Google OAuth state"
            ),
        ):
            google_auth_service.validate_google_oauth_state(
                "state"
            )


def test_validate_google_oauth_state_handles_redis_timeout_error():
    redis_mock = MagicMock()

    redis_mock.eval.side_effect = TimeoutError(
        "Redis timeout"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to validate "
                "Google OAuth state"
            ),
        ):
            google_auth_service.validate_google_oauth_state(
                "state"
            )


def test_validate_google_oauth_state_does_not_swallow_unexpected_errors():
    redis_mock = MagicMock()

    redis_mock.eval.side_effect = RuntimeError(
        "Unexpected Redis error"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        redis_mock,
    ):
        with pytest.raises(
            RuntimeError,
            match="Unexpected Redis error",
        ):
            google_auth_service.validate_google_oauth_state(
                "state"
            )


# ============================================================================
# AUTHORIZATION URL
# ============================================================================


def test_get_google_authorization_url_requires_client_id(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = None
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Google OAuth client ID "
            "is not configured"
        ),
    ):
        google_auth_service.get_google_authorization_url()


def test_get_google_authorization_url_requires_redirect_uri(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_REDIRECT_URI"] = None

    with pytest.raises(
        ValidationError,
        match=(
            "Google OAuth redirect URI "
            "is not configured"
        ),
    ):
        google_auth_service.get_google_authorization_url()


def test_get_google_authorization_url_requires_redis(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    with patch.object(
        google_auth_service.extensions,
        "redis_client",
        None,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Redis is not available "
                "for Google authentication"
            ),
        ):
            google_auth_service.get_google_authorization_url()


def test_get_google_authorization_url_returns_url_and_state(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/api/v1/auth/google/callback"
    )

    with patch.object(
        google_auth_service,
        "create_google_oauth_state",
        return_value="test-state",
    ) as state_mock:
        authorization_url, state = (
            google_auth_service.get_google_authorization_url()
        )

    state_mock.assert_called_once()

    assert state == "test-state"

    assert authorization_url.startswith(
        google_auth_service.GOOGLE_AUTHORIZATION_URL
    )

    assert "client_id=client-id" in authorization_url
    assert "response_type=code" in authorization_url
    assert "state=test-state" in authorization_url
    assert "scope=openid+email+profile" in authorization_url
    assert "access_type=offline" in authorization_url
    assert "prompt=select_account" in authorization_url
    assert (
        "redirect_uri="
        "http%3A%2F%2Flocalhost%2Fapi%2Fv1%2Fauth%2Fgoogle%2Fcallback"
        in authorization_url
    )


# ============================================================================
# GOOGLE AUTHORIZATION CODE EXCHANGE
# ============================================================================


def test_exchange_google_code_requires_code(
    app,
):
    with pytest.raises(
        ValidationError,
        match=(
            "Google authorization "
            "code is required"
        ),
    ):
        google_auth_service.exchange_google_code(
            ""
        )


def test_exchange_google_code_requires_client_id(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = None
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Google OAuth client ID "
            "is not configured"
        ),
    ):
        google_auth_service.exchange_google_code(
            "code"
        )


def test_exchange_google_code_requires_client_secret(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = None
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Google OAuth client secret "
            "is not configured"
        ),
    ):
        google_auth_service.exchange_google_code(
            "code"
        )


def test_exchange_google_code_requires_redirect_uri(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = None

    with pytest.raises(
        ValidationError,
        match=(
            "Google OAuth redirect URI "
            "is not configured"
        ),
    ):
        google_auth_service.exchange_google_code(
            "code"
        )


def test_exchange_google_code_handles_request_failure(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    with patch.object(
        google_auth_service.requests,
        "post",
        side_effect=(
            google_auth_service.requests.RequestException(
                "network error"
            )
        ),
    ):
        with pytest.raises(
            ValidationError,
            match="Unable to communicate with Google",
        ):
            google_auth_service.exchange_google_code(
                "code"
            )


def test_bounded_google_auth_request_uses_pinned_transport_and_ten_second_timeout(monkeypatch):
    response = MagicMock()
    request_mock = MagicMock(return_value=response)
    monkeypatch.setattr(
        "app.core.auth.user.services.google_oauth_transport.request_google_oauth",
        request_mock,
    )
    request = google_auth_service._BoundedGoogleAuthRequest()

    result = request("https://www.googleapis.com/oauth2/v1/certs")

    assert result is response
    request_mock.assert_called_once_with(
        method="GET",
        url="https://www.googleapis.com/oauth2/v1/certs",
        headers=None,
        body=None,
        timeout=10,
    )


def test_exchange_google_code_rejects_redirect(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = "client-secret"
    app.config["GOOGLE_REDIRECT_URI"] = "http://localhost/callback"

    response = MagicMock()
    response.status_code = 302
    response.ok = True

    with patch.object(
        google_auth_service.requests,
        "post",
        return_value=response,
    ) as post_mock:
        with pytest.raises(
            ValidationError,
            match="unexpected redirect",
        ):
            google_auth_service.exchange_google_code("code")

    assert post_mock.call_args.kwargs["allow_redirects"] is False


def test_exchange_google_code_handles_google_error(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    response = MagicMock()
    response.ok = False

    with patch.object(
        google_auth_service.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Google authorization "
                "code exchange failed"
            ),
        ):
            google_auth_service.exchange_google_code(
                "code"
            )


def test_exchange_google_code_handles_invalid_json(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    response = MagicMock()
    response.ok = True
    response.json.side_effect = ValueError()

    with patch.object(
        google_auth_service.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Invalid response "
                "received from Google"
            ),
        ):
            google_auth_service.exchange_google_code(
                "code"
            )


def test_exchange_google_code_requires_access_token(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "token_type": "Bearer",
    }

    with patch.object(
        google_auth_service.requests,
        "post",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Google access token "
                "was not returned"
            ),
        ):
            google_auth_service.exchange_google_code(
                "code"
            )


def test_exchange_google_code_success(
    app,
):
    app.config["GOOGLE_CLIENT_ID"] = "client-id"
    app.config["GOOGLE_CLIENT_SECRET"] = (
        "client-secret"
    )
    app.config["GOOGLE_REDIRECT_URI"] = (
        "http://localhost/callback"
    )

    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "access_token": "google-access-token",
        "refresh_token": "google-refresh-token",
        "token_type": "Bearer",
    }

    with patch.object(
        google_auth_service.requests,
        "post",
        return_value=response,
    ) as post_mock:
        result = (
            google_auth_service.exchange_google_code(
                "auth-code"
            )
        )

    assert result == {
        "access_token": "google-access-token",
        "refresh_token": "google-refresh-token",
        "token_type": "Bearer",
    }

    post_mock.assert_called_once()

    _, kwargs = post_mock.call_args

    assert kwargs["data"]["code"] == "auth-code"
    assert kwargs["data"]["client_id"] == "client-id"
    assert (
        kwargs["data"]["client_secret"]
        == "client-secret"
    )
    assert (
        kwargs["data"]["redirect_uri"]
        == "http://localhost/callback"
    )
    assert (
        kwargs["data"]["grant_type"]
        == "authorization_code"
    )
    assert kwargs["timeout"] == 10
    assert kwargs["allow_redirects"] is False


# ============================================================================
# GOOGLE USER INFORMATION
# ============================================================================


def test_get_google_user_info_requires_access_token():
    with pytest.raises(
        ValidationError,
        match="Google access token is required",
    ):
        google_auth_service.get_google_user_info(
            ""
        )


def test_get_google_user_info_handles_request_failure():
    with patch.object(
        google_auth_service.requests,
        "get",
        side_effect=(
            google_auth_service.requests.RequestException(
                "network error"
            )
        ),
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to retrieve "
                "Google user information"
            ),
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


def test_get_google_user_info_rejects_redirect():
    response = MagicMock()
    response.status_code = 302
    response.ok = True

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ) as get_mock:
        with pytest.raises(
            ValidationError,
            match="unexpected redirect",
        ):
            google_auth_service.get_google_user_info("token")

    assert get_mock.call_args.kwargs["allow_redirects"] is False


def test_get_google_user_info_handles_http_failure():
    response = MagicMock()
    response.ok = False

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Unable to retrieve "
                "Google user information"
            ),
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


def test_get_google_user_info_handles_invalid_json():
    response = MagicMock()
    response.ok = True
    response.json.side_effect = ValueError()

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match=(
                "Invalid user information "
                "received from Google"
            ),
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


def test_get_google_user_info_requires_google_user_id():
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "email": "user@example.com",
        "email_verified": True,
    }

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match="Google user ID is missing",
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


def test_get_google_user_info_requires_google_email():
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "sub": "google-123",
        "email_verified": True,
    }

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match="Google email is missing",
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


@pytest.mark.parametrize(
    "email_verified",
    [
        False,
        None,
    ],
)
def test_get_google_user_info_requires_verified_email(
    email_verified,
):
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "sub": "google-123",
        "email": "user@example.com",
        "email_verified": email_verified,
    }

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        with pytest.raises(
            ValidationError,
            match="Google email must be verified",
        ):
            google_auth_service.get_google_user_info(
                "token"
            )


def test_get_google_user_info_normalizes_email():
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "sub": "google-123",
        "email": "  User@Example.COM  ",
        "email_verified": True,
        "given_name": "John",
        "family_name": "Doe",
        "picture": "https://example.com/photo.jpg",
    }

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ) as get_mock:
        result = (
            google_auth_service.get_google_user_info(
                "google-access-token"
            )
        )

    assert isinstance(
        result,
        GoogleUserInfoSchema,
    )

    assert (
        result.provider_user_id
        == "google-123"
    )
    assert result.email == (
        "user@example.com"
    )
    assert result.first_name == "John"
    assert result.last_name == "Doe"
    assert (
        result.picture
        == "https://example.com/photo.jpg"
    )
    assert result.email_verified is True

    get_mock.assert_called_once_with(
        google_auth_service.GOOGLE_USERINFO_URL,
        headers={
            "Authorization": (
                "Bearer google-access-token"
            ),
            "Accept": "application/json",
        },
        timeout=10,
        allow_redirects=False,
    )


def test_get_google_user_info_converts_provider_id_to_string():
    response = MagicMock()
    response.ok = True
    response.json.return_value = {
        "sub": 123456,
        "email": "user@example.com",
        "email_verified": True,
    }

    with patch.object(
        google_auth_service.requests,
        "get",
        return_value=response,
    ):
        result = (
            google_auth_service.get_google_user_info(
                "token"
            )
        )

    assert (
        result.provider_user_id
        == "123456"
    )


# ============================================================================
# GOOGLE IDENTITY LOOKUP
# ============================================================================


def test_get_user_by_google_identity_returns_none_when_missing(
    app,
):
    result = (
        google_auth_service._get_user_by_google_identity(
            "missing-google-id"
        )
    )

    assert result is None


def test_get_user_by_google_identity_returns_none_for_empty_id():
    result = (
        google_auth_service._get_user_by_google_identity(
            ""
        )
    )

    assert result is None


def test_get_user_by_google_identity_returns_existing_user(
    db_session,
    user,
):
    identity = UserAuthIdentity(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
        provider_user_id="google-existing",
    )

    db_session.add(identity)
    db_session.flush()

    result = (
        google_auth_service._get_user_by_google_identity(
            "google-existing"
        )
    )

    assert result is not None
    assert result.id == user.id


def test_get_user_by_google_identity_ignores_different_provider(
    db_session,
    user,
):
    identity = UserAuthIdentity(
        user_id=user.id,
        provider="facebook",
        provider_user_id="google-existing",
    )

    db_session.add(identity)
    db_session.flush()

    result = (
        google_auth_service._get_user_by_google_identity(
            "google-existing"
        )
    )

    assert result is None


# ============================================================================
# GET OR CREATE GOOGLE USER
# ============================================================================


def test_get_or_create_google_user_returns_existing_google_user(
    db_session,
    user,
):
    identity = UserAuthIdentity(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
        provider_user_id="google-existing",
    )

    db_session.add(identity)
    db_session.flush()

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-existing",
        email=user.email,
        email_verified=True,
    )

    result_user, created = (
        google_auth_service._get_or_create_google_user(
            google_user
        )
    )

    assert result_user.id == user.id
    assert created is False


def test_get_or_create_google_user_does_not_duplicate_existing_identity(
    db_session,
    user,
):
    identity = UserAuthIdentity(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
        provider_user_id="google-existing",
    )

    db_session.add(identity)
    db_session.flush()

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-existing",
        email="different@example.com",
        email_verified=True,
    )

    result_user, created = (
        google_auth_service._get_or_create_google_user(
            google_user
        )
    )

    assert result_user.id == user.id
    assert created is False

    identities = UserAuthIdentity.query.filter_by(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
    ).all()

    assert len(identities) == 1


def test_get_or_create_google_user_rejects_existing_email_without_explicit_link(
    db_session,
    user,
):
    google_user = GoogleUserInfoSchema(
        provider_user_id="google-new",
        email=user.email.upper(),
        email_verified=True,
    )

    with patch.object(
        google_auth_service,
        "create_audit_log",
    ) as audit_mock:
        with pytest.raises(
            ConflictError,
            match="An account with this email already exists",
        ):
            google_auth_service._get_or_create_google_user(
                google_user
            )

    identities = UserAuthIdentity.query.filter_by(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
    ).all()

    assert identities == []
    audit_mock.assert_not_called()

def test_get_or_create_google_user_rejects_different_google_identity(
    db_session,
    user,
):
    identity = UserAuthIdentity(
        user_id=user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
        provider_user_id="google-old",
    )

    db_session.add(identity)
    db_session.flush()

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-new",
        email=user.email,
        email_verified=True,
    )

    with pytest.raises(
        ConflictError,
        match="different Google identity",
    ):
        google_auth_service._get_or_create_google_user(
            google_user
        )


def test_get_or_create_google_user_creates_new_patient(
    db_session,
):
    google_user = GoogleUserInfoSchema(
        provider_user_id="google-new",
        email="NewUser@Example.COM",
        first_name="New",
        last_name="User",
        email_verified=True,
    )

    with patch.object(
        google_auth_service,
        "create_audit_log",
    ) as audit_mock:
        result_user, created = (
            google_auth_service._get_or_create_google_user(
                google_user
            )
        )

    assert created is True
    assert isinstance(
        result_user,
        User,
    )

    assert result_user.id is not None
    assert result_user.email == (
        "newuser@example.com"
    )
    assert result_user.role is Role.PATIENT
    assert result_user.password_hash is None
    assert result_user.clinic_id is None

    identity = UserAuthIdentity.query.filter_by(
        user_id=result_user.id,
        provider=google_auth_service.GOOGLE_PROVIDER,
        provider_user_id="google-new",
    ).first()

    assert identity is not None

    audit_mock.assert_called_once()

    audit = audit_mock.call_args.kwargs

    assert audit["action"] == AuditAction.CREATE
    assert audit["entity_type"] == "User"
    assert audit["entity_id"] == result_user.id

    assert (
        audit["description"]
        == (
            "Google user registered: "
            "newuser@example.com"
        )
    )


# ============================================================================
# GOOGLE AUTHENTICATION
# ============================================================================


def test_authenticate_google_user_rejects_unverified_email():
    google_user = GoogleUserInfoSchema(
        provider_user_id="google-123",
        email="user@example.com",
        email_verified=False,
    )

    with pytest.raises(
        ValidationError,
        match="Google email must be verified",
    ):
        google_auth_service.authenticate_google_user(
            google_user
        )


def test_authenticate_google_user_rejects_inactive_user(
    user,
):
    user.is_active = False

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-123",
        email=user.email,
        email_verified=True,
    )

    with patch.object(
        google_auth_service,
        "_get_or_create_google_user",
        return_value=(user, False),
    ):
        with pytest.raises(
            ValidationError,
            match="This account has been deactivated",
        ):
            google_auth_service.authenticate_google_user(
                google_user
            )


def test_authenticate_google_user_success(
    user,
):
    user.token_version = 7

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-123",
        email=user.email,
        email_verified=True,
    )

    with patch.object(
        google_auth_service,
        "_get_or_create_google_user",
        return_value=(user, False),
    ), patch.object(
        google_auth_service,
        "issue_auth_tokens",
        return_value={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "user_id": user.id,
            "role": user.role.value,
        },
    ) as tokens_mock, patch.object(
        google_auth_service,
        "create_audit_log",
    ) as audit_mock:

        result = (
            google_auth_service.authenticate_google_user(
                google_user
            )
        )

    assert result == {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "user_id": user.id,
        "role": user.role.value,
    }

    tokens_mock.assert_called_once_with(user)

    audit_mock.assert_called_once()

    audit = audit_mock.call_args.kwargs

    assert audit["action"] == AuditAction.LOGIN
    assert audit["entity_type"] == "User"
    assert audit["entity_id"] == user.id
    assert audit["user_id"] == user.id

    assert (
        audit["description"]
        == (
            f"User '{user.email}' "
            "logged in with Google"
        )
    )


def test_authenticate_google_user_updates_last_login(
    db_session,
    user,
):
    user.last_login_at = None
    db_session.commit()

    google_user = GoogleUserInfoSchema(
        provider_user_id="google-123",
        email=user.email,
        email_verified=True,
    )

    with patch.object(
        google_auth_service,
        "_get_or_create_google_user",
        return_value=(user, False),
    ), patch.object(
        google_auth_service,
        "issue_auth_tokens",
        return_value={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "user_id": user.id,
            "role": user.role.value,
        },
    ), patch.object(
        google_auth_service,
        "create_audit_log",
    ):
        google_auth_service.authenticate_google_user(
            google_user
        )

    db_session.refresh(
        user
    )

    assert user.last_login_at is not None


# ============================================================================
# GOOGLE CODE AUTHENTICATION
# ============================================================================


def test_authenticate_google_code_requires_google_access_token():
    with patch.object(
        google_auth_service,
        "exchange_google_code",
        return_value={},
    ):
        with pytest.raises(
            ValidationError,
            match="Google access token is missing",
        ):
            google_auth_service.authenticate_google_code(
                "authorization-code"
            )


def test_authenticate_google_code_runs_full_flow():
    google_user = MagicMock()

    with patch.object(
        google_auth_service,
        "exchange_google_code",
        return_value={
            "access_token": "google-token",
        },
    ) as exchange_mock, patch.object(
        google_auth_service,
        "get_google_user_info",
        return_value=google_user,
    ) as info_mock, patch.object(
        google_auth_service,
        "authenticate_google_user",
        return_value={
            "user_id": 1,
            "role": Role.PATIENT.value,
        },
    ) as auth_mock:

        result = (
            google_auth_service.authenticate_google_code(
                "authorization-code"
            )
        )

    assert result == {
        "user_id": 1,
        "role": Role.PATIENT.value,
    }

    exchange_mock.assert_called_once_with(
        "authorization-code"
    )

    info_mock.assert_called_once_with(
        "google-token"
    )

    auth_mock.assert_called_once_with(
        google_user
    )


def test_authenticate_google_code_preserves_full_authentication_result():
    expected_result = {
        "access_token": "access-token",
        "refresh_token": "refresh-token",
        "user_id": 10,
        "role": Role.PATIENT.value,
    }

    google_user = MagicMock()

    with patch.object(
        google_auth_service,
        "exchange_google_code",
        return_value={
            "access_token": "google-token",
        },
    ), patch.object(
        google_auth_service,
        "get_google_user_info",
        return_value=google_user,
    ), patch.object(
        google_auth_service,
        "authenticate_google_user",
        return_value=expected_result,
    ):
        result = (
            google_auth_service.authenticate_google_code(
                "authorization-code"
            )
        )

    assert result == expected_result
