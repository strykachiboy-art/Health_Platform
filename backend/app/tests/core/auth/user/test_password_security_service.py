from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import extensions
from app.core.auth.user.services import (
    password_security_service as service,
)
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    RateLimitError,
    ServiceUnavailableError,
    ValidationError,
)


def _disable_change_throttle(
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_assert_change_password_attempt_allowed",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_record_change_password_failure",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_clear_change_password_failures",
        lambda user_id: None,
    )


def test_change_password_success_updates_hash_and_revokes_tokens(
    db_session,
    user,
    monkeypatch,
):
    _disable_change_throttle(
        monkeypatch
    )

    audits = []

    monkeypatch.setattr(
        service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )

    previous_version = (
        user.token_version
    )

    service.change_password(
        user_id=user.id,
        current_password="supersecret",
        new_password="NewStrongPass123!",
        confirm_password="NewStrongPass123!",
        ip_address="127.0.0.1",
    )

    db_session.refresh(
        user
    )

    assert not user.check_password(
        "supersecret"
    )
    assert user.check_password(
        "NewStrongPass123!"
    )
    assert (
        user.token_version
        == previous_version + 1
    )

    assert len(
        audits
    ) == 1

    audit = audits[0]

    assert (
        audit["action"]
        is AuditAction.UPDATE
    )
    assert (
        audit["entity_type"]
        == "User"
    )
    assert (
        audit["entity_id"]
        == user.id
    )
    assert (
        audit["user_id"]
        == user.id
    )
    assert (
        audit["ip_address"]
        == "127.0.0.1"
    )

    serialized = repr(
        audit
    )

    assert (
        "NewStrongPass123!"
        not in serialized
    )
    assert (
        "supersecret"
        not in serialized
    )


def test_change_password_rejects_wrong_current_password(
    user,
    monkeypatch,
):
    monkeypatch.setattr(
        service,
        "_assert_change_password_attempt_allowed",
        lambda user_id: None,
    )

    record_failure = Mock()

    monkeypatch.setattr(
        service,
        "_record_change_password_failure",
        record_failure,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "Current password is incorrect"
        ),
    ):
        service.change_password(
            user_id=user.id,
            current_password="WrongPassword123!",
            new_password="NewStrongPass123!",
            confirm_password="NewStrongPass123!",
        )

    record_failure.assert_called_once_with(
        user.id
    )

    assert user.check_password(
        "supersecret"
    )


def test_change_password_rejects_password_reuse(
    user,
    monkeypatch,
):
    _disable_change_throttle(
        monkeypatch
    )

    with pytest.raises(
        ValidationError,
        match=(
            "New password must be different"
        ),
    ):
        service.change_password(
            user_id=user.id,
            current_password="supersecret",
            new_password="supersecret",
            confirm_password="supersecret",
        )


def test_change_password_rejects_oauth_only_account(
    db_session,
    user,
    monkeypatch,
):
    _disable_change_throttle(
        monkeypatch
    )

    user.password_hash = None
    db_session.commit()

    with pytest.raises(
        ValidationError,
        match="OAuth-only",
    ):
        service.change_password(
            user_id=user.id,
            current_password="anything",
            new_password="NewStrongPass123!",
            confirm_password="NewStrongPass123!",
        )


def test_change_password_failure_window_blocks_after_threshold(
    app,
    user,
    monkeypatch,
):
    client = Mock()
    client.get.return_value = "5"

    monkeypatch.setattr(
        extensions,
        "redis_client",
        client,
    )

    app.config[
        "AUTH_PASSWORD_CHANGE_MAX_FAILURES"
    ] = 5

    with pytest.raises(
        RateLimitError,
        match="Too many password change attempts",
    ):
        service._assert_change_password_attempt_allowed(
            user.id
        )


def test_request_password_reset_queues_normalized_email(
    app,
    monkeypatch,
):
    app.config.update(
        PASSWORD_RESET_PUBLIC_URL=(
            "https://app.example.com/reset-password"
        ),
        PLATFORM_EMAIL_HOST=(
            "smtp.example.com"
        ),
        PLATFORM_EMAIL_PORT=587,
        PLATFORM_EMAIL_FROM_EMAIL=(
            "security@example.com"
        ),
        PLATFORM_EMAIL_USE_TLS=True,
        PLATFORM_EMAIL_USE_SSL=False,
    )

    monkeypatch.setattr(
        service,
        "_allow_password_reset_request",
        lambda email: True,
    )

    delay = Mock()

    monkeypatch.setattr(
        service.process_password_reset_request,
        "delay",
        delay,
    )

    service.request_password_reset(
        email="  USER@Example.COM  "
    )

    delay.assert_called_once_with(
        "user@example.com"
    )


def test_request_password_reset_silently_suppresses_identity_spam(
    app,
    monkeypatch,
):
    app.config.update(
        PASSWORD_RESET_PUBLIC_URL=(
            "https://app.example.com/reset-password"
        ),
        PLATFORM_EMAIL_HOST=(
            "smtp.example.com"
        ),
        PLATFORM_EMAIL_PORT=587,
        PLATFORM_EMAIL_FROM_EMAIL=(
            "security@example.com"
        ),
        PLATFORM_EMAIL_USE_TLS=True,
        PLATFORM_EMAIL_USE_SSL=False,
    )

    monkeypatch.setattr(
        service,
        "_allow_password_reset_request",
        lambda email: False,
    )

    delay = Mock()

    monkeypatch.setattr(
        service.process_password_reset_request,
        "delay",
        delay,
    )

    service.request_password_reset(
        email="user@example.com"
    )

    delay.assert_not_called()


def test_request_password_reset_fails_closed_when_delivery_is_unconfigured(
    app,
    monkeypatch,
):
    app.config[
        "PASSWORD_RESET_PUBLIC_URL"
    ] = None
    app.config[
        "PLATFORM_EMAIL_HOST"
    ] = None

    allow = Mock(
        return_value=True
    )

    monkeypatch.setattr(
        service,
        "_allow_password_reset_request",
        allow,
    )

    with pytest.raises(
        ServiceUnavailableError,
        match=(
            "Password reset service is not configured"
        ),
    ):
        service.request_password_reset(
            email="user@example.com"
        )

    allow.assert_not_called()


def test_password_reset_request_identity_throttle_uses_cooldown_and_windows(
    app,
    monkeypatch,
):
    client = Mock()
    client.set.return_value = True
    client.eval.side_effect = [
        1,
        1,
    ]

    monkeypatch.setattr(
        extensions,
        "redis_client",
        client,
    )

    assert service._allow_password_reset_request(
        "user@example.com"
    )

    assert (
        client.eval.call_count
        == 2
    )

    cooldown_key = (
        client.set.call_args.args[0]
    )

    assert (
        "user@example.com"
        not in cooldown_key
    )


def test_password_reset_request_identity_throttle_suppresses_cooldown_repeat(
    app,
    monkeypatch,
):
    client = Mock()
    client.set.return_value = False

    monkeypatch.setattr(
        extensions,
        "redis_client",
        client,
    )

    assert not service._allow_password_reset_request(
        "user@example.com"
    )

    client.eval.assert_not_called()


def test_process_password_reset_request_does_not_reveal_missing_account(
    app,
    monkeypatch,
):
    sender = Mock()

    monkeypatch.setattr(
        service,
        "_send_password_reset_email",
        sender,
    )

    result = (
        service._process_password_reset_request(
            "missing@example.com"
        )
    )

    assert result is False
    sender.assert_not_called()


def test_process_password_reset_request_skips_inactive_account(
    db_session,
    user,
    monkeypatch,
):
    user.is_active = False
    db_session.commit()

    sender = Mock()

    monkeypatch.setattr(
        service,
        "_send_password_reset_email",
        sender,
    )

    result = (
        service._process_password_reset_request(
            user.email
        )
    )

    assert result is False
    sender.assert_not_called()


def test_process_password_reset_request_skips_oauth_only_account(
    db_session,
    user,
    monkeypatch,
):
    user.password_hash = None
    db_session.commit()

    sender = Mock()

    monkeypatch.setattr(
        service,
        "_send_password_reset_email",
        sender,
    )

    result = (
        service._process_password_reset_request(
            user.email
        )
    )

    assert result is False
    sender.assert_not_called()


def test_process_password_reset_request_issues_single_use_token_out_of_band(
    user,
    monkeypatch,
):
    token = "r" * 64

    monkeypatch.setattr(
        service,
        "_acquire_reset_issue_lock",
        lambda user_id: True,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_issue_lock",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_issue_password_reset_token",
        lambda target: service._IssuedPasswordResetToken(
            token=token,
            recovery_factor_required=False,
        ),
    )

    sender = Mock()

    monkeypatch.setattr(
        service,
        "_send_password_reset_email",
        sender,
    )

    result = (
        service._process_password_reset_request(
            user.email
        )
    )

    assert result is True

    sender.assert_called_once_with(
        email=user.email,
        token=token,
        recovery_factor_required=False,
    )


def test_process_password_reset_request_discards_token_when_email_delivery_fails(
    user,
    monkeypatch,
):
    token = "r" * 64

    monkeypatch.setattr(
        service,
        "_acquire_reset_issue_lock",
        lambda user_id: True,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_issue_lock",
        lambda user_id: None,
    )
    monkeypatch.setattr(
        service,
        "_issue_password_reset_token",
        lambda target: service._IssuedPasswordResetToken(
            token=token,
            recovery_factor_required=False,
        ),
    )
    monkeypatch.setattr(
        service,
        "_send_password_reset_email",
        Mock(
            side_effect=RuntimeError(
                "SMTP failed"
            )
        ),
    )

    discard = Mock()

    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )

    with pytest.raises(
        RuntimeError,
        match="SMTP failed",
    ):
        service._process_password_reset_request(
            user.email
        )

    discard.assert_called_once_with(
        digest=service._token_digest(
            token
        ),
        user_id=user.id,
    )


def test_issue_password_reset_token_stores_only_digest_and_metadata(
    app,
    user,
    monkeypatch,
):
    client = Mock()
    client.get.return_value = None

    pipeline = Mock()
    client.pipeline.return_value = (
        pipeline
    )

    monkeypatch.setattr(
        extensions,
        "redis_client",
        client,
    )

    issued = (
        service._issue_password_reset_token(
            user
        )
    )
    token = issued.token

    assert len(
        token
    ) >= 32
    assert (
        issued.recovery_factor_required
        is False
    )

    calls = (
        pipeline.setex.call_args_list
    )

    assert len(
        calls
    ) == 2

    serialized_calls = repr(
        calls
    )

    assert token not in serialized_calls

    digest = service._token_digest(
        token
    )

    assert digest in serialized_calls


def test_password_reset_link_uses_fragment_not_query_string(
    app,
):
    app.config[
        "PASSWORD_RESET_PUBLIC_URL"
    ] = (
        "https://app.example.com/reset-password"
    )

    link = (
        service._build_password_reset_link(
            "secret-token"
        )
    )

    assert (
        link
        == (
            "https://app.example.com/reset-password"
            "#token=secret-token"
        )
    )
    assert "?token=" not in link


def test_confirm_password_reset_success_updates_password_revokes_sessions_and_consumes_token(
    db_session,
    user,
    monkeypatch,
):
    token = "t" * 64
    digest = service._token_digest(
        token
    )
    previous_version = (
        user.token_version
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                previous_version
            ),
        },
    )

    consume = Mock()

    monkeypatch.setattr(
        service,
        "_consume_reset_token",
        consume,
    )

    release = Mock()

    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        release,
    )

    audits = []
    notice = Mock()

    monkeypatch.setattr(
        service,
        "create_audit_log",
        lambda **kwargs: audits.append(
            kwargs
        ),
    )
    monkeypatch.setattr(
        service,
        "queue_password_security_notice",
        notice,
    )

    service.confirm_password_reset(
        token=token,
        new_password="ResetStrongPass123!",
        confirm_password="ResetStrongPass123!",
        ip_address="127.0.0.1",
    )

    db_session.refresh(
        user
    )

    assert user.check_password(
        "ResetStrongPass123!"
    )
    assert not user.check_password(
        "supersecret"
    )
    assert (
        user.token_version
        == previous_version + 1
    )

    consume.assert_called_once_with(
        digest=digest,
        user_id=user.id,
    )
    release.assert_not_called()

    notice.assert_called_once_with(
        user_id=user.id,
        event="reset",
    )

    assert len(
        audits
    ) == 1
    assert (
        audits[0]["new_value"][
            "password_recovery"
        ]
        is True
    )

    serialized = repr(
        audits[0]
    )

    assert (
        "ResetStrongPass123!"
        not in serialized
    )
    assert (
        token
        not in serialized
    )


def test_confirm_password_reset_rejects_invalid_or_expired_token(
    monkeypatch,
):
    token = "t" * 64

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: None,
    )

    release = Mock()

    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        release,
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="ResetStrongPass123!",
            confirm_password="ResetStrongPass123!",
        )

    release.assert_called_once_with(
        service._token_digest(
            token
        )
    )


def test_confirm_password_reset_rejects_token_after_other_session_invalidation(
    user,
    monkeypatch,
):
    token = "t" * 64
    stale_version = (
        user.token_version - 1
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                stale_version
            ),
        },
    )

    discard = Mock()

    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="ResetStrongPass123!",
            confirm_password="ResetStrongPass123!",
        )

    discard.assert_called_once_with(
        digest=service._token_digest(
            token
        ),
        user_id=user.id,
    )


def test_confirm_password_reset_keeps_valid_token_when_new_password_reuses_current(
    user,
    monkeypatch,
):
    token = "t" * 64

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                user.token_version
            ),
        },
    )

    consume = Mock()
    release = Mock()

    monkeypatch.setattr(
        service,
        "_consume_reset_token",
        consume,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        release,
    )

    with pytest.raises(
        ValidationError,
        match=(
            "New password must be different"
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="supersecret",
            confirm_password="supersecret",
        )

    consume.assert_not_called()
    release.assert_called_once()


def test_confirm_password_reset_rejects_concurrent_claim(
    monkeypatch,
):
    token = "t" * 64

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: False,
    )

    load = Mock()

    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        load,
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="ResetStrongPass123!",
            confirm_password="ResetStrongPass123!",
        )

    load.assert_not_called()


def test_password_security_notice_uses_platform_email_without_credentials_in_payload(
    app,
    user,
    monkeypatch,
):
    app.config.update(
        PLATFORM_EMAIL_HOST="smtp.example.com",
        PLATFORM_EMAIL_PORT=587,
        PLATFORM_EMAIL_FROM_EMAIL=(
            "security@example.com"
        ),
        PLATFORM_EMAIL_USE_TLS=True,
        PLATFORM_EMAIL_USE_SSL=False,
    )

    provider = Mock()
    provider_class = Mock(
        return_value=provider
    )

    monkeypatch.setattr(
        service,
        "EmailNotificationProvider",
        provider_class,
    )

    result = (
        service._process_password_security_notice(
            user.id,
            "reset",
        )
    )

    assert result is True

    provider.send.assert_called_once()

    kwargs = (
        provider.send.call_args.kwargs
    )

    assert kwargs["email"] == user.email
    assert (
        "password was reset"
        in kwargs["notification"].title.lower()
    )


def test_confirm_password_reset_cannot_be_reused_even_if_redis_cleanup_is_delayed(
    db_session,
    user,
    monkeypatch,
):
    token = "t" * 64
    issued_version = (
        user.token_version
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                issued_version
            ),
        },
    )
    monkeypatch.setattr(
        service,
        "_consume_reset_token",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )
    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "create_audit_log",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "queue_password_security_notice",
        lambda **kwargs: None,
    )

    service.confirm_password_reset(
        token=token,
        new_password="FirstResetPass123!",
        confirm_password="FirstResetPass123!",
    )

    db_session.refresh(
        user
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="SecondResetPass123!",
            confirm_password="SecondResetPass123!",
        )

    assert user.check_password(
        "FirstResetPass123!"
    )


def test_confirm_super_admin_password_reset_requires_bound_recovery_factor(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.role = Role.SUPER_ADMIN
    db_session.commit()

    token = "m" * 64
    issued_version = user.token_version

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                issued_version
            ),
            "recovery_factor_required": True,
            "recovery_factor_provider": (
                "test-mfa"
            ),
            "recovery_factor_binding": (
                "a" * 64
            ),
        },
    )

    verify = Mock(
        return_value=SimpleNamespace(
            binding_matches=True,
            verified=True,
        )
    )

    monkeypatch.setattr(
        service,
        "verify_super_admin_recovery_factor",
        verify,
    )
    monkeypatch.setattr(
        service,
        "_consume_reset_token",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )
    monkeypatch.setattr(
        service,
        "create_audit_log",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "queue_password_security_notice",
        lambda **kwargs: None,
    )

    service.confirm_password_reset(
        token=token,
        new_password="MfaResetPass123!",
        confirm_password="MfaResetPass123!",
        recovery_factor="123456",
    )

    verify.assert_called_once_with(
        user=user,
        factor="123456",
        expected_provider_name="test-mfa",
        expected_binding_digest=(
            "a" * 64
        ),
    )

    db_session.refresh(
        user
    )

    assert user.check_password(
        "MfaResetPass123!"
    )
    assert (
        user.token_version
        == issued_version + 1
    )


def test_confirm_super_admin_password_reset_counts_invalid_recovery_factor(
    app,
    user,
    monkeypatch,
):
    token = "m" * 64
    digest = service._token_digest(
        token
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                user.token_version
            ),
            "recovery_factor_required": True,
            "recovery_factor_provider": (
                "test-mfa"
            ),
            "recovery_factor_binding": (
                "a" * 64
            ),
        },
    )
    monkeypatch.setattr(
        service,
        "verify_super_admin_recovery_factor",
        lambda **kwargs: SimpleNamespace(
            binding_matches=True,
            verified=False,
        ),
    )

    record = Mock(
        return_value=1
    )
    discard = Mock()
    release = Mock()

    monkeypatch.setattr(
        service,
        "_record_reset_mfa_failure",
        record,
    )
    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        release,
    )

    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_MAX_FAILURES"
    ] = 5

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="MfaResetPass123!",
            confirm_password="MfaResetPass123!",
            recovery_factor="wrong-factor",
        )

    record.assert_called_once_with(
        digest
    )
    discard.assert_not_called()
    release.assert_called_once_with(
        digest
    )
    assert user.check_password(
        "supersecret"
    )


def test_confirm_super_admin_password_reset_discards_after_factor_failure_limit(
    app,
    user,
    monkeypatch,
):
    token = "m" * 64
    digest = service._token_digest(
        token
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                user.token_version
            ),
            "recovery_factor_required": True,
            "recovery_factor_provider": (
                "test-mfa"
            ),
            "recovery_factor_binding": (
                "a" * 64
            ),
        },
    )
    monkeypatch.setattr(
        service,
        "verify_super_admin_recovery_factor",
        lambda **kwargs: SimpleNamespace(
            binding_matches=True,
            verified=False,
        ),
    )
    monkeypatch.setattr(
        service,
        "_record_reset_mfa_failure",
        lambda value: 5,
    )

    discard = Mock()

    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )

    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_MAX_FAILURES"
    ] = 5

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="MfaResetPass123!",
            confirm_password="MfaResetPass123!",
            recovery_factor="wrong-factor",
        )

    discard.assert_called_once_with(
        digest=digest,
        user_id=user.id,
    )


def test_confirm_super_admin_password_reset_invalidates_token_if_mfa_enrollment_changes(
    user,
    monkeypatch,
):
    token = "m" * 64
    digest = service._token_digest(
        token
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                user.token_version
            ),
            "recovery_factor_required": True,
            "recovery_factor_provider": (
                "test-mfa"
            ),
            "recovery_factor_binding": (
                "a" * 64
            ),
        },
    )
    monkeypatch.setattr(
        service,
        "verify_super_admin_recovery_factor",
        lambda **kwargs: SimpleNamespace(
            binding_matches=False,
            verified=False,
        ),
    )

    discard = Mock()

    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="MfaResetPass123!",
            confirm_password="MfaResetPass123!",
            recovery_factor="123456",
        )

    discard.assert_called_once_with(
        digest=digest,
        user_id=user.id,
    )
    assert user.check_password(
        "supersecret"
    )


def test_issue_super_admin_password_reset_token_stores_only_mfa_binding_metadata(
    user,
    monkeypatch,
):
    client = Mock()
    client.get.return_value = None
    pipeline = Mock()
    client.pipeline.return_value = pipeline

    monkeypatch.setattr(
        extensions,
        "redis_client",
        client,
    )
    monkeypatch.setattr(
        service,
        "resolve_super_admin_recovery_factor_state",
        lambda target: SimpleNamespace(
            required=True,
            provider_name="test-mfa",
            binding_digest=("b" * 64),
        ),
    )

    issued = (
        service._issue_password_reset_token(
            user
        )
    )

    assert (
        issued.recovery_factor_required
        is True
    )

    token_payload_call = (
        pipeline.setex.call_args_list[0]
    )
    stored_payload = (
        token_payload_call.args[2]
    )

    assert issued.token not in stored_payload
    assert '"recovery_factor_required":true' in stored_payload
    assert '"recovery_factor_provider":"test-mfa"' in stored_payload
    assert (
        '"recovery_factor_binding":"'
        + ("b" * 64)
        + '"'
    ) in stored_payload


def test_password_reset_link_marks_additional_verification_only_in_fragment(
    app,
):
    app.config[
        "PASSWORD_RESET_PUBLIC_URL"
    ] = (
        "https://app.example.com/reset-password"
    )

    link = (
        service._build_password_reset_link(
            "secret-token",
            recovery_factor_required=True,
        )
    )

    assert (
        link
        == (
            "https://app.example.com/reset-password"
            "#token=secret-token"
            "&verification=additional"
        )
    )
    assert "?token=" not in link
    assert "super_admin" not in link
    assert "mfa" not in link


def test_confirm_super_admin_email_only_token_is_invalidated_if_mfa_policy_tightens(
    user,
    monkeypatch,
):
    token = "m" * 64
    digest = service._token_digest(
        token
    )

    monkeypatch.setattr(
        service,
        "_acquire_reset_claim",
        lambda value: True,
    )
    monkeypatch.setattr(
        service,
        "_load_reset_payload",
        lambda value: {
            "user_id": user.id,
            "token_version": (
                user.token_version
            ),
            "recovery_factor_required": False,
            "recovery_factor_provider": None,
            "recovery_factor_binding": None,
        },
    )
    monkeypatch.setattr(
        service,
        "resolve_super_admin_recovery_factor_state",
        lambda target: SimpleNamespace(
            required=True,
            provider_name="test-mfa",
            binding_digest=("a" * 64),
        ),
    )

    discard = Mock()

    monkeypatch.setattr(
        service,
        "_discard_reset_token",
        discard,
    )
    monkeypatch.setattr(
        service,
        "_release_reset_claim",
        lambda value: None,
    )

    with pytest.raises(
        ValidationError,
        match=(
            service.PASSWORD_RESET_GENERIC_ERROR
        ),
    ):
        service.confirm_password_reset(
            token=token,
            new_password="MfaResetPass123!",
            confirm_password="MfaResetPass123!",
        )

    discard.assert_called_once_with(
        digest=digest,
        user_id=user.id,
    )
    assert user.check_password(
        "supersecret"
    )
