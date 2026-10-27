from __future__ import annotations

import pytest

from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
)
from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitBreakerStoreError,
    CircuitPermit,
    CircuitState,
    CircuitTransition,
)


class FakeStore:
    def __init__(self, permit=None):
        self.circuit_name = "openai:chat"
        self.redis_client = object()
        self.permit = permit or CircuitPermit(
            allowed=True,
            state=CircuitState.CLOSED,
            generation=0,
        )
        self.success_calls = []
        self.failure_calls = []
        self.raise_on_acquire = False
        self.raise_on_success = False
        self.raise_on_failure = False

    def acquire(self, settings):
        if self.raise_on_acquire:
            raise CircuitBreakerStoreError("down")
        return self.permit

    def record_success(self, *, permit, settings):
        self.success_calls.append((permit, settings))
        if self.raise_on_success:
            raise CircuitBreakerStoreError("down")
        return CircuitTransition(
            action="closed",
            previous_state=permit.state,
            new_state=CircuitState.CLOSED,
            generation=permit.generation,
            failure_count=0,
            opened_until_ms=None,
            changed=(
                permit.state
                is CircuitState.HALF_OPEN
            ),
        )

    def record_failure(self, *, permit, settings):
        self.failure_calls.append(
            (permit, settings)
        )
        if self.raise_on_failure:
            raise CircuitBreakerStoreError("down")
        return CircuitTransition(
            action="opened",
            previous_state=permit.state,
            new_state=CircuitState.OPEN,
            generation=permit.generation + 1,
            failure_count=settings.failure_threshold,
            opened_until_ms=123456789,
            changed=True,
        )


def make_breaker(
    store,
    *,
    classifier=None,
    enabled=True,
    events=None,
):
    if events is None:
        events = []

    def recorder(**payload):
        events.append(payload)
        return payload

    return CircuitBreaker(
        name="openai:chat",
        store=store,
        settings=CircuitBreakerSettings(
            enabled=enabled,
            failure_threshold=5,
            open_seconds=30,
            probe_lease_seconds=10,
            state_ttl_seconds=3600,
        ),
        failure_classifier=classifier,
        provider="openai",
        operation="chat",
        event_recorder=recorder,
    )


def test_successful_call_records_success_once():
    store = FakeStore()
    breaker = make_breaker(store)

    result = breaker.call(lambda: "ok")

    assert result == "ok"
    assert len(store.success_calls) == 1
    assert store.failure_calls == []


def test_qualifying_failure_is_recorded_and_original_error_is_preserved():
    store = FakeStore()
    breaker = make_breaker(
        store,
        classifier=lambda exc: "retryable",
    )

    error = ConnectionError("provider unavailable")

    def fail():
        raise error

    with pytest.raises(ConnectionError) as caught:
        breaker.call(fail)

    assert caught.value is error
    assert len(store.failure_calls) == 1
    assert store.success_calls == []


def test_nonqualifying_failure_resets_failure_streak_and_is_re_raised():
    store = FakeStore()
    breaker = make_breaker(
        store,
        classifier=lambda exc: None,
    )

    error = ValueError("provider rejected request")

    def fail():
        raise error

    with pytest.raises(ValueError) as caught:
        breaker.call(fail)

    assert caught.value is error
    assert len(store.success_calls) == 1
    assert store.failure_calls == []


def test_open_circuit_fails_fast_without_invoking_provider():
    store = FakeStore(
        permit=CircuitPermit(
            allowed=False,
            state=CircuitState.OPEN,
            generation=3,
            retry_after_seconds=12.5,
            emit_rejected_event=True,
        )
    )
    breaker = make_breaker(store)
    called = {"value": False}

    def provider():
        called["value"] = True
        return "unexpected"

    with pytest.raises(
        CircuitBreakerOpenError
    ) as caught:
        breaker.call(provider)

    assert called["value"] is False
    assert caught.value.status_code == 503
    assert caught.value.code == (
        "dependency_circuit_open"
    )
    assert (
        caught.value.retry_after_seconds
        == 12.5
    )


def test_store_failure_on_admission_fails_open_and_provider_still_runs():
    store = FakeStore()
    store.raise_on_acquire = True
    breaker = make_breaker(store)

    assert breaker.call(lambda: "ok") == "ok"

    assert store.success_calls == []
    assert store.failure_calls == []


def test_store_failure_does_not_mask_original_provider_exception():
    store = FakeStore()
    breaker = make_breaker(
        store,
        classifier=lambda exc: "retryable",
    )
    store.raise_on_failure = True

    error = TimeoutError("provider timeout")

    def fail():
        raise error

    with pytest.raises(TimeoutError) as caught:
        breaker.call(fail)

    assert caught.value is error


def test_disabled_breaker_never_touches_store():
    store = FakeStore()
    store.raise_on_acquire = True
    breaker = make_breaker(
        store,
        enabled=False,
    )

    assert breaker.call(lambda: 7) == 7

    assert store.success_calls == []
    assert store.failure_calls == []


def test_half_open_success_emits_closed_transition():
    events = []
    store = FakeStore(
        permit=CircuitPermit(
            allowed=True,
            state=CircuitState.HALF_OPEN,
            generation=9,
            probe_token="probe-token",
        )
    )
    breaker = make_breaker(
        store,
        events=events,
    )

    assert breaker.call(lambda: "recovered") == (
        "recovered"
    )

    assert any(
        event["event_type"] == "circuit.closed"
        for event in events
    )


def test_failure_open_transition_emits_safe_operational_event():
    events = []
    store = FakeStore()
    breaker = make_breaker(
        store,
        classifier=lambda exc: "retryable",
        events=events,
    )

    with pytest.raises(ConnectionError):
        breaker.call(
            lambda: (_ for _ in ()).throw(
                ConnectionError("secret details")
            )
        )

    opened = [
        event
        for event in events
        if event["event_type"]
        == "circuit.opened"
    ]

    assert len(opened) == 1
    assert opened[0]["circuit_name"] == (
        "openai:chat"
    )
    assert opened[0]["provider"] == "openai"
    assert opened[0]["operation"] == "chat"
    assert opened[0]["failure_class"] == (
        "retryable"
    )
    assert "secret details" not in str(
        opened[0]
    )


def test_store_name_must_match_breaker_name():
    store = FakeStore()
    store.circuit_name = "paystack:verify"

    with pytest.raises(ValueError):
        make_breaker(store)
