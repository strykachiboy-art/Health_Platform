from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.auth.user.services import (
    recovery_factor_service as service,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError


class FakeRecoveryFactorProvider:
    name = "test-mfa"

    def __init__(
        self,
        *,
        binding: str | None = "generation-1",
        accepted_factor: str = "123456",
    ):
        self.binding = binding
        self.accepted_factor = (
            accepted_factor
        )
        self.get_calls = 0
        self.verify_calls = 0

    def get_recovery_binding(
        self,
        user,
    ):
        self.get_calls += 1
        return self.binding

    def verify_recovery_factor(
        self,
        user,
        factor,
    ):
        self.verify_calls += 1
        return (
            factor
            == self.accepted_factor
        )


def _super_admin(
    user,
):
    user.role = Role.SUPER_ADMIN
    return user


def test_non_super_admin_never_requires_privileged_recovery_factor(
    app,
    user,
):
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    assert state.required is False
    assert state.provider_name is None
    assert state.binding_digest is None


def test_super_admin_disabled_policy_preserves_current_email_recovery(
    app,
    user,
):
    _super_admin(
        user
    )
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "disabled"

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    assert state.required is False


def test_super_admin_enabled_policy_fails_closed_without_provider(
    app,
    user,
):
    _super_admin(
        user
    )
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "if_enrolled"

    with pytest.raises(
        ServiceUnavailableError,
        match=(
            "Privileged password recovery "
            "is temporarily unavailable"
        ),
    ):
        service.resolve_super_admin_recovery_factor_state(
            user
        )


def test_if_enrolled_policy_allows_unenrolled_super_admin_during_rollout(
    app,
    user,
):
    _super_admin(
        user
    )
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "if_enrolled"

    provider = FakeRecoveryFactorProvider(
        binding=None
    )

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    assert state.required is False
    assert provider.get_calls == 1


def test_required_policy_blocks_unenrolled_super_admin(
    app,
    user,
):
    _super_admin(
        user
    )
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    provider = FakeRecoveryFactorProvider(
        binding=None
    )

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    with pytest.raises(
        ServiceUnavailableError,
        match=(
            "Privileged password recovery "
            "is unavailable for this account"
        ),
    ):
        service.resolve_super_admin_recovery_factor_state(
            user
        )


def test_required_policy_binds_reset_to_current_mfa_enrollment(
    app,
    user,
):
    _super_admin(
        user
    )
    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    provider = FakeRecoveryFactorProvider()

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    assert state.required is True
    assert (
        state.provider_name
        == "test-mfa"
    )
    assert isinstance(
        state.binding_digest,
        str,
    )
    assert len(
        state.binding_digest
    ) == 64
    assert (
        "generation-1"
        not in state.binding_digest
    )


def test_recovery_factor_verification_accepts_current_enrollment_and_factor(
    app,
    user,
):
    _super_admin(
        user
    )

    provider = FakeRecoveryFactorProvider()

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    result = (
        service.verify_super_admin_recovery_factor(
            user=user,
            factor="123456",
            expected_provider_name=(
                state.provider_name
            ),
            expected_binding_digest=(
                state.binding_digest
            ),
        )
    )

    assert result.binding_matches is True
    assert result.verified is True
    assert provider.verify_calls == 1


def test_recovery_factor_verification_rejects_enrollment_change_before_factor_check(
    app,
    user,
):
    _super_admin(
        user
    )

    provider = FakeRecoveryFactorProvider(
        binding="generation-1"
    )

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    state = (
        service.resolve_super_admin_recovery_factor_state(
            user
        )
    )

    provider.binding = "generation-2"

    result = (
        service.verify_super_admin_recovery_factor(
            user=user,
            factor="123456",
            expected_provider_name=(
                state.provider_name
            ),
            expected_binding_digest=(
                state.binding_digest
            ),
        )
    )

    assert result.binding_matches is False
    assert result.verified is False
    assert provider.verify_calls == 0


def test_recovery_factor_provider_failure_is_fail_closed(
    app,
    user,
):
    _super_admin(
        user
    )

    provider = FakeRecoveryFactorProvider()
    provider.get_recovery_binding = Mock(
        side_effect=RuntimeError(
            "provider unavailable"
        )
    )

    service.register_super_admin_recovery_factor_provider(
        app,
        provider,
    )

    app.config[
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"
    ] = "required"

    with pytest.raises(
        ServiceUnavailableError,
        match=(
            "Privileged password recovery "
            "is temporarily unavailable"
        ),
    ):
        service.resolve_super_admin_recovery_factor_state(
            user
        )
