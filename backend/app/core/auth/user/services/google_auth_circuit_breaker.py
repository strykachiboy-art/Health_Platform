from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from flask import current_app
from google.auth import exceptions as google_auth_exceptions

from app import extensions
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker import CircuitBreaker
from app.core.resilience.circuit_breaker_config import CircuitBreakerSettings
from app.core.resilience.circuit_breaker_store import RedisCircuitBreakerStore
from app.core.auth.user.services.google_oauth_transport import (
    GoogleOAuthAuthRequest,
    GoogleOAuthTransportError,
)

T = TypeVar("T")
GOOGLE_AUTH_PROVIDER = "google"
GOOGLE_AUTH_PROVIDER_TIMEOUT_SECONDS = 30.0
GOOGLE_AUTH_OPERATIONS = frozenset(
    {"token_exchange", "userinfo", "id_token_transport"}
)


class GoogleAuthTransientError(ValidationError):
    """Google authentication dependency is temporarily unavailable."""


def is_transient_google_http_status(status_code: object) -> bool:
    return (
        isinstance(status_code, int)
        and not isinstance(status_code, bool)
        and (status_code in {408, 425, 429} or status_code >= 500)
    )


def classify_google_auth_circuit_failure(error: Exception) -> str | None:
    if isinstance(error, GoogleAuthTransientError):
        return "transient"
    if (
        isinstance(error, google_auth_exceptions.TransportError)
        and isinstance(error.__cause__, GoogleOAuthTransportError)
    ):
        return "transport"
    return None


def get_google_auth_circuit(*, operation: str) -> CircuitBreaker:
    if not isinstance(operation, str):
        raise ValueError("Google auth circuit operation must be a string")
    operation = operation.strip().lower()
    if operation not in GOOGLE_AUTH_OPERATIONS:
        raise ValueError("Unsupported Google auth circuit operation")

    settings = CircuitBreakerSettings.from_mapping(current_app.config)
    if (
        settings.enabled
        and settings.probe_lease_seconds <= GOOGLE_AUTH_PROVIDER_TIMEOUT_SECONDS
    ):
        raise ValidationError(
            "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS "
            "must exceed Google auth provider timeout"
        )

    name = f"{GOOGLE_AUTH_PROVIDER}:{operation}"
    store = RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=name,
    )
    return CircuitBreaker(
        name=name,
        store=store,
        settings=settings,
        failure_classifier=classify_google_auth_circuit_failure,
        provider=GOOGLE_AUTH_PROVIDER,
        operation=operation,
    )


def call_google_auth_with_circuit(
    *,
    operation: str,
    function: Callable[..., T],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> T:
    return get_google_auth_circuit(operation=operation).call(
        function,
        *args,
        **(kwargs or {}),
    )


class CircuitProtectedGoogleAuthRequest(GoogleOAuthAuthRequest):
    """Pinned google-auth transport with circuit-breaker admission."""

    def __call__(
        self,
        url,
        method="GET",
        body=None,
        headers=None,
        timeout=10,
        **kwargs,
    ):
        def _request():
            response = super(
                CircuitProtectedGoogleAuthRequest,
                self,
            ).__call__(
                url,
                method=method,
                body=body,
                headers=headers,
                timeout=timeout,
                **kwargs,
            )
            status = getattr(
                response,
                "status",
                getattr(response, "status_code", None),
            )
            if is_transient_google_http_status(status):
                cause = GoogleOAuthTransportError(
                    "Google auth certificate endpoint is temporarily unavailable"
                )
                raise google_auth_exceptions.TransportError(
                    "Google auth certificate request failed"
                ) from cause
            return response

        return call_google_auth_with_circuit(
            operation="id_token_transport",
            function=_request,
        )
