from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
import redis

from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)
from app.core.resilience.circuit_breaker_store import (
    CircuitState,
    RedisCircuitBreakerStore,
)


@pytest.fixture
def redis_client():
    client = redis.StrictRedis.from_url(
        os.environ.get(
            "TEST_REDIS_URL",
            "redis://localhost:6379/15",
        ),
        decode_responses=True,
    )
    client.ping()
    return client


@pytest.fixture
def make_store(redis_client):
    stores = []

    def factory():
        store = RedisCircuitBreakerStore(
            redis_client=redis_client,
            circuit_name=(
                f"test:breaker:{uuid4().hex}"
            ),
        )
        stores.append(store)
        return store

    yield factory

    for store in stores:
        redis_client.delete(store.key)


def settings(
    *,
    threshold=5,
    open_seconds=0.15,
    probe_lease_seconds=0.5,
):
    return CircuitBreakerSettings(
        failure_threshold=threshold,
        open_seconds=open_seconds,
        probe_lease_seconds=probe_lease_seconds,
        state_ttl_seconds=60,
    )


def test_threshold_opens_circuit(make_store):
    store = make_store()
    policy = settings(
        threshold=3,
        open_seconds=2,
    )

    for expected_failures in (1, 2):
        permit = store.acquire(policy)
        transition = store.record_failure(
            permit=permit,
            settings=policy,
        )

        assert transition.new_state is (
            CircuitState.CLOSED
        )
        assert (
            transition.failure_count
            == expected_failures
        )

    permit = store.acquire(policy)
    transition = store.record_failure(
        permit=permit,
        settings=policy,
    )

    assert transition.previous_state is (
        CircuitState.CLOSED
    )
    assert transition.new_state is (
        CircuitState.OPEN
    )
    assert transition.changed is True
    assert transition.generation == 1

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.OPEN
    assert snapshot.failure_count == 3
    assert snapshot.generation == 1


def test_open_circuit_rejects_and_emits_rejection_only_once(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=2,
    )

    permit = store.acquire(policy)
    store.record_failure(
        permit=permit,
        settings=policy,
    )

    first = store.acquire(policy)
    second = store.acquire(policy)

    assert first.allowed is False
    assert second.allowed is False
    assert first.state is CircuitState.OPEN
    assert first.emit_rejected_event is True
    assert second.emit_rejected_event is False

    snapshot = store.snapshot()
    assert snapshot.rejected_count == 2


def test_only_one_half_open_probe_is_admitted_across_threads(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.15,
        probe_lease_seconds=10.0,
    )

    initial = store.acquire(policy)
    store.record_failure(
        permit=initial,
        settings=policy,
    )

    time.sleep(0.20)

    worker_count = 16
    start_barrier = Barrier(worker_count)

    def acquire_together(_):
        start_barrier.wait(timeout=5)
        return store.acquire(policy)

    with ThreadPoolExecutor(
        max_workers=worker_count,
    ) as executor:
        permits = list(
            executor.map(
                acquire_together,
                range(worker_count),
            )
        )

    allowed = [
        permit
        for permit in permits
        if permit.allowed
    ]

    assert len(allowed) == 1
    assert (
        allowed[0].state
        is CircuitState.HALF_OPEN
    )
    assert allowed[0].probe_token


def test_expired_half_open_probe_lease_allows_recovery_probe(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.10,
        probe_lease_seconds=0.10,
    )

    initial = store.acquire(policy)
    store.record_failure(
        permit=initial,
        settings=policy,
    )

    time.sleep(0.15)

    first_probe = store.acquire(policy)

    assert first_probe.allowed is True
    assert first_probe.state is CircuitState.HALF_OPEN
    assert first_probe.probe_token

    denied_while_lease_active = store.acquire(policy)

    assert denied_while_lease_active.allowed is False
    assert (
        denied_while_lease_active.state
        is CircuitState.HALF_OPEN
    )

    time.sleep(0.15)

    recovery_probe = store.acquire(policy)

    assert recovery_probe.allowed is True
    assert recovery_probe.state is CircuitState.HALF_OPEN
    assert recovery_probe.probe_token
    assert (
        recovery_probe.probe_token
        != first_probe.probe_token
    )


def test_half_open_success_closes_circuit(make_store):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.10,
    )

    initial = store.acquire(policy)
    store.record_failure(
        permit=initial,
        settings=policy,
    )

    time.sleep(0.15)

    probe = store.acquire(policy)
    assert probe.allowed is True
    assert probe.state is CircuitState.HALF_OPEN

    transition = store.record_success(
        permit=probe,
        settings=policy,
    )

    assert transition.previous_state is (
        CircuitState.HALF_OPEN
    )
    assert transition.new_state is (
        CircuitState.CLOSED
    )
    assert transition.changed is True

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.CLOSED
    assert snapshot.failure_count == 0


def test_half_open_failure_reopens_with_new_generation(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.10,
    )

    initial = store.acquire(policy)
    first_open = store.record_failure(
        permit=initial,
        settings=policy,
    )
    assert first_open.generation == 1

    time.sleep(0.15)

    probe = store.acquire(policy)
    reopened = store.record_failure(
        permit=probe,
        settings=policy,
    )

    assert reopened.previous_state is (
        CircuitState.HALF_OPEN
    )
    assert reopened.new_state is (
        CircuitState.OPEN
    )
    assert reopened.generation == 2

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.OPEN
    assert snapshot.generation == 2


def test_stale_closed_success_cannot_close_newer_open_generation(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=2,
    )

    slow_success = store.acquire(policy)
    failing_call = store.acquire(policy)

    opened = store.record_failure(
        permit=failing_call,
        settings=policy,
    )
    assert opened.generation == 1

    stale = store.record_success(
        permit=slow_success,
        settings=policy,
    )

    assert stale.stale is True

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.OPEN
    assert snapshot.generation == 1


def test_stale_half_open_success_cannot_close_reopened_circuit(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.10,
    )

    initial = store.acquire(policy)
    store.record_failure(
        permit=initial,
        settings=policy,
    )

    time.sleep(0.15)

    probe = store.acquire(policy)

    reopened = store.record_failure(
        permit=probe,
        settings=policy,
    )
    assert reopened.generation == 2

    stale = store.record_success(
        permit=probe,
        settings=policy,
    )
    assert stale.stale is True

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.OPEN
    assert snapshot.generation == 2


def test_closed_success_resets_consecutive_failure_counter(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=3,
        open_seconds=2,
    )

    first = store.acquire(policy)
    store.record_failure(
        permit=first,
        settings=policy,
    )

    success = store.acquire(policy)
    store.record_success(
        permit=success,
        settings=policy,
    )

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.CLOSED
    assert snapshot.failure_count == 0

    next_failure = store.acquire(policy)
    transition = store.record_failure(
        permit=next_failure,
        settings=policy,
    )

    assert transition.failure_count == 1
    assert transition.new_state is (
        CircuitState.CLOSED
    )




def test_recovery_preserves_generation_against_very_old_permit(
    make_store,
):
    store = make_store()
    policy = settings(
        threshold=1,
        open_seconds=0.10,
    )

    very_old = store.acquire(policy)
    failing = store.acquire(policy)

    opened = store.record_failure(
        permit=failing,
        settings=policy,
    )
    assert opened.generation == 1

    time.sleep(0.15)

    probe = store.acquire(policy)
    closed = store.record_success(
        permit=probe,
        settings=policy,
    )
    assert closed.new_state is CircuitState.CLOSED
    assert closed.generation == 1

    stale = store.record_failure(
        permit=very_old,
        settings=policy,
    )
    assert stale.stale is True

    snapshot = store.snapshot()
    assert snapshot.state is CircuitState.CLOSED
    assert snapshot.generation == 1
    assert snapshot.failure_count == 0

def test_state_key_has_bounded_ttl(make_store):
    store = make_store()
    policy = settings(
        threshold=2,
        open_seconds=2,
    )

    permit = store.acquire(policy)
    store.record_failure(
        permit=permit,
        settings=policy,
    )

    snapshot = store.snapshot()

    assert snapshot.ttl_seconds is not None
    assert 0 < snapshot.ttl_seconds <= 60
