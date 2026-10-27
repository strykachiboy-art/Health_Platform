from __future__ import annotations

from unittest.mock import Mock

import pytest

from app import extensions
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker import (
    CircuitBreakerOpenError,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitState,
    RedisCircuitBreakerStore,
)
from app.modules.billing.services.gateways.base_gateway import (
    GatewayRejectedError,
    GatewayTransientError,
    GatewayUnknownOutcomeError,
)
from app.modules.billing.services.gateways.payment_circuit_breaker import (
    PAYMENT_PROVIDER_TIMEOUT_SECONDS,
    acquire_payment_circuit,
    call_payment_with_permit,
    classify_payment_circuit_failure,
    get_payment_circuit,
)


def _configure_breaker(app) -> None:
    app.config.update(
        CIRCUIT_BREAKER_ENABLED=True,
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        CIRCUIT_BREAKER_OPEN_SECONDS=30,
        CIRCUIT_BREAKER_PROBE_LEASE_SECONDS=60,
        CIRCUIT_BREAKER_STATE_TTL_SECONDS=3600,
    )


def _store(
    provider: str,
    operation: str,
) -> RedisCircuitBreakerStore:
    return RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=f"{provider}:{operation}",
    )


@pytest.fixture
def clean_payment_circuits(app):
    names = (
        ("paystack", "initialize"),
        ("paystack", "verify"),
        ("flutterwave", "initialize"),
        ("flutterwave", "verify"),
    )

    with app.app_context():
        for provider, operation in names:
            _store(provider, operation).delete()

    yield

    with app.app_context():
        for provider, operation in names:
            _store(provider, operation).delete()


def test_payment_failure_classifier_counts_only_availability_failures():
    assert (
        classify_payment_circuit_failure(
            GatewayUnknownOutcomeError("unknown")
        )
        == "unknown_outcome"
    )
    assert (
        classify_payment_circuit_failure(
            GatewayTransientError("temporary")
        )
        == "transient"
    )
    assert (
        classify_payment_circuit_failure(
            GatewayRejectedError("rejected")
        )
        is None
    )
    assert (
        classify_payment_circuit_failure(
            ValueError("invalid local input")
        )
        is None
    )


def test_initialization_and_verification_use_separate_circuits(
    app,
    clean_payment_circuits,
):
    with app.app_context():
        _configure_breaker(app)

        breaker, permit = acquire_payment_circuit(
            provider="paystack",
            operation="initialize",
        )

        with pytest.raises(GatewayUnknownOutcomeError):
            call_payment_with_permit(
                breaker,
                permit,
                Mock(
                    side_effect=GatewayUnknownOutcomeError(
                        "provider outcome unknown"
                    )
                ),
            )

        init_snapshot = _store(
            "paystack",
            "initialize",
        ).snapshot()
        verify_snapshot = _store(
            "paystack",
            "verify",
        ).snapshot()

        assert init_snapshot.state is CircuitState.OPEN
        assert verify_snapshot.state is CircuitState.CLOSED
        assert verify_snapshot.failure_count == 0


def test_open_initialize_circuit_rejects_before_provider_callable(
    app,
    clean_payment_circuits,
):
    provider_call = Mock()

    with app.app_context():
        _configure_breaker(app)

        breaker, permit = acquire_payment_circuit(
            provider="flutterwave",
            operation="initialize",
        )

        with pytest.raises(GatewayUnknownOutcomeError):
            call_payment_with_permit(
                breaker,
                permit,
                Mock(
                    side_effect=GatewayUnknownOutcomeError(
                        "temporary provider failure"
                    )
                ),
            )

        with pytest.raises(CircuitBreakerOpenError):
            acquire_payment_circuit(
                provider="flutterwave",
                operation="initialize",
            )

        provider_call.assert_not_called()


def test_rejection_resets_failure_streak_without_opening(
    app,
    clean_payment_circuits,
):
    with app.app_context():
        _configure_breaker(app)
        app.config["CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = 2

        breaker, permit = acquire_payment_circuit(
            provider="paystack",
            operation="verify",
        )

        with pytest.raises(GatewayTransientError):
            call_payment_with_permit(
                breaker,
                permit,
                Mock(
                    side_effect=GatewayTransientError(
                        "temporary verification failure"
                    )
                ),
            )

        snapshot = _store(
            "paystack",
            "verify",
        ).snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 1

        breaker, permit = acquire_payment_circuit(
            provider="paystack",
            operation="verify",
        )

        with pytest.raises(GatewayRejectedError):
            call_payment_with_permit(
                breaker,
                permit,
                Mock(
                    side_effect=GatewayRejectedError(
                        "provider answered with rejection"
                    )
                ),
            )

        snapshot = _store(
            "paystack",
            "verify",
        ).snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 0


def test_redis_failure_fails_open_and_provider_callable_runs(
    app,
    monkeypatch,
):
    class BrokenRedis:
        def eval(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

        def lpush(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

    provider_call = Mock(return_value={"status": "ok"})

    with app.app_context():
        _configure_breaker(app)
        monkeypatch.setattr(
            extensions,
            "redis_client",
            BrokenRedis(),
        )

        breaker, permit = acquire_payment_circuit(
            provider="paystack",
            operation="verify",
        )

        result = call_payment_with_permit(
            breaker,
            permit,
            provider_call,
        )

        assert result == {"status": "ok"}
        provider_call.assert_called_once()


def test_probe_lease_must_exceed_payment_provider_timeout(app):
    with app.app_context():
        _configure_breaker(app)
        app.config["CIRCUIT_BREAKER_PROBE_LEASE_SECONDS"] = (
            PAYMENT_PROVIDER_TIMEOUT_SECONDS
        )

        with pytest.raises(
            ValidationError,
            match=(
                "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS "
                "must exceed payment provider timeout"
            ),
        ):
            get_payment_circuit(
                provider="paystack",
                operation="initialize",
            )
