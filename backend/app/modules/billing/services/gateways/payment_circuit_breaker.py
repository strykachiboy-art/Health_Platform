from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from flask import current_app

from app import extensions
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitPermit,
    RedisCircuitBreakerStore,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayTransientError,
    GatewayUnknownOutcomeError,
)

T = TypeVar("T")

PAYMENT_PROVIDER_TIMEOUT_SECONDS = 30.0
SUPPORTED_PAYMENT_CIRCUIT_PROVIDERS = frozenset(
    {"paystack", "flutterwave"}
)
SUPPORTED_PAYMENT_CIRCUIT_OPERATIONS = frozenset(
    {"initialize", "verify"}
)


def _normalize_component(
    value: str,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")

    normalized = value.strip().lower()

    if normalized not in allowed:
        raise ValueError(
            f"Unsupported payment circuit {field_name}"
        )

    return normalized


def classify_payment_circuit_failure(
    error: Exception,
) -> str | None:
    if isinstance(error, GatewayUnknownOutcomeError):
        return "unknown_outcome"

    if isinstance(error, GatewayTransientError):
        return "transient"

    if isinstance(
        error,
        (GatewayRejectedError, ValueError),
    ):
        return None

    return None


def get_payment_circuit(
    *,
    provider: str,
    operation: str,
) -> CircuitBreaker:
    provider = _normalize_component(
        provider,
        field_name="provider",
        allowed=SUPPORTED_PAYMENT_CIRCUIT_PROVIDERS,
    )
    operation = _normalize_component(
        operation,
        field_name="operation",
        allowed=SUPPORTED_PAYMENT_CIRCUIT_OPERATIONS,
    )

    settings = CircuitBreakerSettings.from_mapping(
        current_app.config
    )

    if (
        settings.enabled
        and settings.probe_lease_seconds
        <= PAYMENT_PROVIDER_TIMEOUT_SECONDS
    ):
        raise ValidationError(
            "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS "
            "must exceed payment provider timeout"
        )

    circuit_name = f"{provider}:{operation}"

    store = RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=circuit_name,
    )

    return CircuitBreaker(
        name=circuit_name,
        store=store,
        settings=settings,
        failure_classifier=classify_payment_circuit_failure,
        provider=provider,
        operation=operation,
    )


def acquire_payment_circuit(
    *,
    provider: str,
    operation: str,
) -> tuple[CircuitBreaker, CircuitPermit]:
    breaker = get_payment_circuit(
        provider=provider,
        operation=operation,
    )
    permit = breaker.acquire()

    if not permit.allowed:
        raise CircuitBreakerOpenError(
            circuit_name=breaker.name,
            retry_after_seconds=permit.retry_after_seconds,
        )

    return breaker, permit


def call_payment_with_permit(
    breaker: CircuitBreaker,
    permit: CircuitPermit,
    function: Callable[..., T],
    /,
    *args: Any,
    **kwargs: Any,
) -> T:
    try:
        result = function(*args, **kwargs)
    except Exception as exc:
        failure_class = classify_payment_circuit_failure(exc)

        if failure_class is None:
            breaker.record_success(permit)
        else:
            breaker.record_failure(
                permit,
                failure_class=failure_class,
            )
        raise

    breaker.record_success(permit)
    return result


def call_payment_with_circuit(
    *,
    provider: str,
    operation: str,
    function: Callable[..., T],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
) -> T:
    breaker, permit = acquire_payment_circuit(
        provider=provider,
        operation=operation,
    )

    return call_payment_with_permit(
        breaker,
        permit,
        function,
        *args,
        **(kwargs or {}),
    )
