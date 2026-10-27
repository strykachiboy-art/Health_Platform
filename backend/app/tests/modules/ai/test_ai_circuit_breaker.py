from __future__ import annotations

import pytest

from app import extensions
from app.core.enums.ai_enums import AIFeature
from app.core.exceptions import ValidationError
from app.core.resilience.circuit_breaker_store import (
    CircuitState,
    RedisCircuitBreakerStore,
)
from app.modules.ai.ai_provider_exceptions import (
    AIProviderError,
    AIProviderFailureClass,
    AIProviderHTTPError,
    AIProviderResponseError,
    AIProviderUnavailableError,
)
from app.modules.ai.services import ai_service


def _configure_breaker(app) -> None:
    app.config.update(
        CIRCUIT_BREAKER_ENABLED=True,
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=1,
        CIRCUIT_BREAKER_OPEN_SECONDS=30,
        CIRCUIT_BREAKER_PROBE_LEASE_SECONDS=60,
        CIRCUIT_BREAKER_STATE_TTL_SECONDS=3600,
        AI_PROVIDER_TIMEOUT_SECONDS=30,
        OPENAI_API_KEY="test-key",
    )


def _store() -> RedisCircuitBreakerStore:
    return RedisCircuitBreakerStore(
        redis_client=extensions.redis_client,
        circuit_name=ai_service.OPENAI_CIRCUIT_NAME,
    )


@pytest.fixture
def clean_openai_circuit(app):
    with app.app_context():
        _store().delete()

    yield

    with app.app_context():
        _store().delete()


def test_openai_circuit_counts_only_retryable_failures(app):
    with app.app_context():
        assert ai_service._classify_openai_circuit_failure(
            AIProviderHTTPError(429)
        ) == AIProviderFailureClass.RETRYABLE.value
        assert ai_service._classify_openai_circuit_failure(
            AIProviderHTTPError(503)
        ) == AIProviderFailureClass.RETRYABLE.value
        assert ai_service._classify_openai_circuit_failure(
            TimeoutError("provider timeout")
        ) == AIProviderFailureClass.RETRYABLE.value
        assert ai_service._classify_openai_circuit_failure(
            AIProviderHTTPError(401)
        ) is None
        assert ai_service._classify_openai_circuit_failure(
            AIProviderResponseError("malformed provider response")
        ) is None


def test_retryable_failure_opens_and_next_call_is_short_circuited(
    app,
    monkeypatch,
    clean_openai_circuit,
):
    calls = {"count": 0}

    def fail_once(feature, payload):
        calls["count"] += 1
        raise AIProviderError(
            "temporary provider outage",
            failure_class=AIProviderFailureClass.RETRYABLE,
        )

    with app.app_context():
        _configure_breaker(app)
        monkeypatch.setattr(
            ai_service,
            "_call_openai_unprotected",
            fail_once,
        )

        with pytest.raises(AIProviderError) as first_error:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={"drug_names": ["Aspirin", "Warfarin"]},
            )

        assert first_error.value.failure_class is AIProviderFailureClass.RETRYABLE
        assert calls["count"] == 1

        snapshot = _store().snapshot()
        assert snapshot.state is CircuitState.OPEN
        assert snapshot.failure_count == 1

        with pytest.raises(AIProviderUnavailableError) as second_error:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={"drug_names": ["Aspirin", "Warfarin"]},
            )

        assert second_error.value.failure_class is AIProviderFailureClass.RETRYABLE
        assert second_error.value.status_code == 503
        assert calls["count"] == 1


def test_non_retryable_provider_response_failure_does_not_open(
    app,
    monkeypatch,
    clean_openai_circuit,
):
    calls = {"count": 0}

    def provider_call(feature, payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise AIProviderResponseError(
                "AI provider returned malformed response"
            )
        return {"summary": "provider reachable"}

    with app.app_context():
        _configure_breaker(app)
        monkeypatch.setattr(
            ai_service,
            "_call_openai_unprotected",
            provider_call,
        )

        with pytest.raises(AIProviderResponseError):
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={"drug_names": ["Aspirin", "Warfarin"]},
            )

        snapshot = _store().snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 0

        result = ai_service._call_openai(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            payload={"drug_names": ["Aspirin", "Warfarin"]},
        )

        assert result == {"summary": "provider reachable"}
        assert calls["count"] == 2


def test_non_retryable_http_failure_does_not_open(
    app,
    monkeypatch,
    clean_openai_circuit,
):
    calls = {"count": 0}

    def provider_call(feature, payload):
        calls["count"] += 1
        if calls["count"] == 1:
            raise AIProviderHTTPError(401)
        return {"summary": "provider reachable"}

    with app.app_context():
        _configure_breaker(app)
        monkeypatch.setattr(
            ai_service,
            "_call_openai_unprotected",
            provider_call,
        )

        with pytest.raises(AIProviderHTTPError) as exc_info:
            ai_service._call_openai(
                feature=AIFeature.DRUG_INTERACTION_CHECK,
                payload={"drug_names": ["Aspirin", "Warfarin"]},
            )

        assert exc_info.value.failure_class is (
            AIProviderFailureClass.USER_INTERVENTION_REQUIRED
        )

        snapshot = _store().snapshot()
        assert snapshot.state is CircuitState.CLOSED
        assert snapshot.failure_count == 0

        assert ai_service._call_openai(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            payload={"drug_names": ["Aspirin", "Warfarin"]},
        ) == {"summary": "provider reachable"}
        assert calls["count"] == 2


def test_breaker_redis_failure_fails_open_for_provider_call(
    app,
    monkeypatch,
):
    calls = {"count": 0}

    class BrokenRedis:
        def eval(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

        def lpush(self, *args, **kwargs):
            raise ConnectionError("redis unavailable")

    def provider_call(feature, payload):
        calls["count"] += 1
        return {"summary": "provider reachable"}

    with app.app_context():
        _configure_breaker(app)
        monkeypatch.setattr(extensions, "redis_client", BrokenRedis())
        monkeypatch.setattr(
            ai_service,
            "_call_openai_unprotected",
            provider_call,
        )

        result = ai_service._call_openai(
            feature=AIFeature.DRUG_INTERACTION_CHECK,
            payload={"drug_names": ["Aspirin", "Warfarin"]},
        )

        assert result == {"summary": "provider reachable"}
        assert calls["count"] == 1


def test_probe_lease_must_exceed_ai_provider_timeout(app):
    with app.app_context():
        _configure_breaker(app)
        app.config["CIRCUIT_BREAKER_PROBE_LEASE_SECONDS"] = 30
        app.config["AI_PROVIDER_TIMEOUT_SECONDS"] = 30

        with pytest.raises(
            ValidationError,
            match=(
                "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS "
                "must exceed AI_PROVIDER_TIMEOUT_SECONDS"
            ),
        ):
            ai_service._get_openai_circuit_breaker()
