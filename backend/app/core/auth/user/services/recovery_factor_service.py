from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from typing import Protocol

from flask import current_app

from app.core.auth.user.models.user_model import User
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError


_PROVIDER_EXTENSION_KEY = (
    "super_admin_password_reset_recovery_factor_provider"
)

_POLICY_DISABLED = "disabled"
_POLICY_IF_ENROLLED = "if_enrolled"
_POLICY_REQUIRED = "required"
_ALLOWED_POLICIES = {
    _POLICY_DISABLED,
    _POLICY_IF_ENROLLED,
    _POLICY_REQUIRED,
}


class SuperAdminRecoveryFactorProvider(Protocol):
    """
    Adapter contract for the future MFA subsystem.

    get_recovery_binding() returns a non-secret value that changes whenever
    the enrolled recovery factor set changes. Examples include an MFA epoch,
    enrollment version, authenticator-set UUID, or recovery-code generation
    version. None means the user is not enrolled.

    verify_recovery_factor() may accept a TOTP value, recovery code, or another
    MFA recovery credential. The provider owns validation and one-time recovery
    code consumption.
    """

    name: str

    def get_recovery_binding(
        self,
        user: User,
    ) -> str | None:
        ...

    def verify_recovery_factor(
        self,
        user: User,
        factor: str,
    ) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class RecoveryFactorState:
    required: bool
    provider_name: str | None = None
    binding_digest: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryFactorVerification:
    binding_matches: bool
    verified: bool


def register_super_admin_recovery_factor_provider(
    app,
    provider: SuperAdminRecoveryFactorProvider,
) -> None:
    """
    Register the MFA/recovery adapter on one Flask app instance.

    App-local registration avoids cross-test and multi-app global state.
    """
    name = getattr(
        provider,
        "name",
        None,
    )
    get_binding = getattr(
        provider,
        "get_recovery_binding",
        None,
    )
    verify = getattr(
        provider,
        "verify_recovery_factor",
        None,
    )

    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 80
        or not callable(get_binding)
        or not callable(verify)
    ):
        raise ValueError(
            "Invalid super-admin recovery factor provider"
        )

    app.extensions[
        _PROVIDER_EXTENSION_KEY
    ] = provider


def _policy() -> str:
    value = current_app.config.get(
        "AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY",
        _POLICY_DISABLED,
    )

    if not isinstance(value, str):
        raise ServiceUnavailableError(
            "Privileged password recovery is misconfigured"
        )

    value = value.strip().lower()

    if value not in _ALLOWED_POLICIES:
        raise ServiceUnavailableError(
            "Privileged password recovery is misconfigured"
        )

    return value


def _provider():
    return current_app.extensions.get(
        _PROVIDER_EXTENSION_KEY
    )


def _provider_name(
    provider,
) -> str:
    value = getattr(
        provider,
        "name",
        None,
    )

    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > 80
    ):
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        )

    return value.strip()


def _normalize_binding(
    binding,
) -> str | None:
    if binding is None:
        return None

    if (
        not isinstance(binding, str)
        or not binding.strip()
        or len(binding.strip()) > 256
    ):
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        )

    return binding.strip()


def _binding_digest(
    *,
    provider_name: str,
    binding: str,
) -> str:
    secret = current_app.config.get(
        "SECRET_KEY"
    )

    if (
        not isinstance(secret, str)
        or not secret
    ):
        raise ServiceUnavailableError(
            "Privileged password recovery is misconfigured"
        )

    value = (
        f"{provider_name}:{binding}"
    ).encode(
        "utf-8"
    )

    return hmac.new(
        secret.encode("utf-8"),
        value,
        hashlib.sha256,
    ).hexdigest()


def _read_provider_binding(
    provider,
    user: User,
) -> tuple[str, str | None]:
    provider_name = _provider_name(
        provider
    )

    try:
        binding = (
            provider.get_recovery_binding(
                user
            )
        )
    except Exception as exc:
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        ) from exc

    return (
        provider_name,
        _normalize_binding(
            binding
        ),
    )


def resolve_super_admin_recovery_factor_state(
    user: User,
) -> RecoveryFactorState:
    """
    Resolve the second-factor requirement when a reset credential is issued.

    Non-super-admin users are unaffected. The policy supports staged rollout:
    disabled -> if_enrolled -> required.

    Any enabled policy fails closed when its provider cannot be consulted.
    """
    if (
        not isinstance(user, User)
        or user.role is not Role.SUPER_ADMIN
    ):
        return RecoveryFactorState(
            required=False
        )

    policy = _policy()

    if policy == _POLICY_DISABLED:
        return RecoveryFactorState(
            required=False
        )

    provider = _provider()

    if provider is None:
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        )

    provider_name, binding = (
        _read_provider_binding(
            provider,
            user,
        )
    )

    if binding is None:
        if policy == _POLICY_REQUIRED:
            # Once required mode is enabled, an unenrolled privileged account
            # may not downgrade itself to email-only recovery.
            raise ServiceUnavailableError(
                "Privileged password recovery is unavailable for this account"
            )

        return RecoveryFactorState(
            required=False
        )

    return RecoveryFactorState(
        required=True,
        provider_name=provider_name,
        binding_digest=_binding_digest(
            provider_name=provider_name,
            binding=binding,
        ),
    )


def verify_super_admin_recovery_factor(
    *,
    user: User,
    factor: str | None,
    expected_provider_name: str,
    expected_binding_digest: str,
) -> RecoveryFactorVerification:
    """
    Verify the factor while binding the reset token to the enrollment state
    that existed when the token was issued.

    Provider or enrollment changes invalidate the reset token rather than
    silently downgrading it.
    """
    if (
        not isinstance(user, User)
        or user.role is not Role.SUPER_ADMIN
    ):
        return RecoveryFactorVerification(
            binding_matches=False,
            verified=False,
        )

    provider = _provider()

    if provider is None:
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        )

    provider_name, binding = (
        _read_provider_binding(
            provider,
            user,
        )
    )

    if (
        provider_name
        != expected_provider_name
        or binding is None
    ):
        return RecoveryFactorVerification(
            binding_matches=False,
            verified=False,
        )

    current_binding_digest = (
        _binding_digest(
            provider_name=provider_name,
            binding=binding,
        )
    )

    if not hmac.compare_digest(
        current_binding_digest,
        expected_binding_digest,
    ):
        return RecoveryFactorVerification(
            binding_matches=False,
            verified=False,
        )

    if (
        not isinstance(factor, str)
        or not factor
        or len(factor) > 512
    ):
        return RecoveryFactorVerification(
            binding_matches=True,
            verified=False,
        )

    try:
        verified = bool(
            provider.verify_recovery_factor(
                user,
                factor,
            )
        )
    except Exception as exc:
        raise ServiceUnavailableError(
            "Privileged password recovery is temporarily unavailable"
        ) from exc

    return RecoveryFactorVerification(
        binding_matches=True,
        verified=verified,
    )
