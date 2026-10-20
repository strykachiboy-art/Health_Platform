from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling.redis_capacity_observation import (
    RedisCapacityObservationAdapter,
    RedisCapacityObservationConfig,
)
from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityAction,
    RedisCapacityPolicyConfig,
)
from app.core.autoscaling.redis_capacity_preview import (
    RedisCapacityPreviewConfig,
    RedisCapacityPreviewStatus,
)
from app.core.autoscaling.redis_runtime_evaluation import (
    RedisRuntimeCapacityEvaluator,
    RedisRuntimeEvaluationConfig,
    RedisRuntimeEvaluationStatus,
)


NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
CONTAINER = "clinic-system-phase9-redis-1"


def policy():
    # Synthetic thresholds for tests, not production recommendations.
    return RedisCapacityPolicyConfig(
        warning_usage_ratio=0.70,
        critical_usage_ratio=0.85,
    )


def redis_metrics(*, sampled_at=None, **overrides):
    if sampled_at is None:
        sampled_at = NOW - timedelta(seconds=2)

    values = {
        "timestamp": sampled_at.isoformat(),
        "healthy": True,
        "used_memory_bytes": 2_377_648,
        "used_memory_peak_bytes": 2_422_264,
        "memory_capacity_metrics_healthy": True,
        "memory_capacity_metrics_reason": "ok",
        "max_memory_bytes": 0,
        "max_memory_policy": "noeviction",
        "memory_limit_configured": False,
        "memory_limit_usage_ratio": None,
        "memory_limit_remaining_bytes": None,
    }
    values.update(overrides)
    return values


def runtime_snapshot(*, sampled_at=None, **overrides):
    if sampled_at is None:
        sampled_at = NOW - timedelta(seconds=1)

    values = {
        "timestamp": sampled_at.isoformat(),
        "healthy": True,
        "container_name": CONTAINER,
        "docker_daemon_memory_total_bytes": 4_062_822_400,
        "linux_memory_total_bytes": 4_062_822_400,
        "linux_memory_available_bytes": 1_759_162_368,
        "redis_container_memory_current_bytes": 68_886_528,
        "redis_container_memory_limit_bytes": 0,
        "aof_enabled": True,
        "data_volume_mounted": True,
    }
    values.update(overrides)
    return values


def preview_config(**overrides):
    values = {
        "proposed_redis_maxmemory_bytes": 256_000_000,
        "proposed_container_memory_limit_bytes": 384_000_000,
        "redis_overhead_headroom_bytes": 128_000_000,
        "minimum_linux_available_reserve_bytes": 1_000_000_000,
        "minimum_docker_daemon_reserve_bytes": 1_000_000_000,
    }
    values.update(overrides)
    return RedisCapacityPreviewConfig(**values)


def make_evaluator(
    *,
    redis_source=None,
    runtime_source=None,
    metrics_collector=None,
    runtime_collector=None,
    clock=lambda: NOW,
    observation_clock=lambda: NOW,
    max_runtime_age=20,
    expected_container=CONTAINER,
    sizing=None,
):
    if redis_source is None:
        redis_source = redis_metrics()

    if metrics_collector is None:
        metrics_collector = lambda **kwargs: redis_source

    observation_adapter = RedisCapacityObservationAdapter(
        redis_client=object(),
        config=RedisCapacityObservationConfig(
            max_metrics_age_seconds=20,
        ),
        policy=policy(),
        metrics_collector=metrics_collector,
        clock=observation_clock,
    )

    if runtime_collector is None:
        if runtime_source is None:
            runtime_source = runtime_snapshot()
        runtime_collector = lambda: runtime_source

    return RedisRuntimeCapacityEvaluator(
        observation_adapter=observation_adapter,
        runtime_snapshot_collector=runtime_collector,
        config=RedisRuntimeEvaluationConfig(
            expected_container_name=expected_container,
            max_runtime_metrics_age_seconds=max_runtime_age,
        ),
        preview_config=preview_config() if sizing is None else sizing,
        clock=clock,
    )


def test_fresh_matching_runtime_produces_accepted_preview():
    result = make_evaluator().evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED
    assert result.ok is True
    assert result.reason == "runtime_capacity_preview_accepted_no_changes_performed"
    assert result.runtime_container_name == CONTAINER
    assert result.runtime_age_seconds == 1
    assert result.preview.status is RedisCapacityPreviewStatus.ACCEPTED
    assert result.observation.decision.action is RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_critical_redis_usage_can_be_previewed_but_never_applied():
    source = redis_metrics(
        used_memory_bytes=900,
        used_memory_peak_bytes=950,
        max_memory_bytes=1000,
        max_memory_policy="noeviction",
        memory_limit_configured=True,
        memory_limit_usage_ratio=0.90,
        memory_limit_remaining_bytes=100,
    )
    result = make_evaluator(redis_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED
    assert result.observation.decision.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_refuses_mismatched_container_identity():
    source = runtime_snapshot(container_name="another-redis-container")
    result = make_evaluator(runtime_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_container_identity_mismatch"
    assert result.actuation_allowed is False


def test_refuses_unhealthy_runtime_snapshot():
    result = make_evaluator(
        runtime_source=runtime_snapshot(healthy=False)
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_metrics_unhealthy"


def test_refuses_non_mapping_runtime_snapshot():
    result = make_evaluator(runtime_collector=lambda: ["not", "a", "mapping"]).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "invalid_runtime_snapshot"


def test_runtime_collection_exception_is_redacted():
    def collector():
        raise TimeoutError("private runtime connection detail")

    result = make_evaluator(runtime_collector=collector).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_snapshot_collection_failed"
    assert result.error_type == "TimeoutError"
    assert "private runtime connection detail" not in repr(result)


def test_redis_observation_must_be_healthy_before_runtime_collection():
    calls = []

    def runtime_collector():
        calls.append("runtime")
        return runtime_snapshot()

    result = make_evaluator(
        redis_source=redis_metrics(
            memory_capacity_metrics_healthy=False,
            memory_capacity_metrics_reason="redis_capacity_config_unavailable",
        ),
        runtime_collector=runtime_collector,
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_capacity_observation_unhealthy"
    assert calls == []


def test_redis_collection_exception_fails_closed():
    def collector(**kwargs):
        raise TimeoutError("private Redis connection detail")

    result = make_evaluator(metrics_collector=collector).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_capacity_observation_unhealthy"
    assert result.observation is not None
    assert result.observation.decision.action is RedisCapacityAction.FAIL_CLOSED
    assert "private Redis connection detail" not in repr(result)


def test_runtime_timestamp_exactly_at_age_limit_is_accepted():
    source = runtime_snapshot(sampled_at=NOW - timedelta(seconds=10))
    result = make_evaluator(runtime_source=source, max_runtime_age=10).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED
    assert result.runtime_age_seconds == 10


def test_stale_runtime_snapshot_fails_closed():
    source = runtime_snapshot(sampled_at=NOW - timedelta(seconds=21))
    result = make_evaluator(runtime_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "stale_runtime_snapshot"


def test_future_runtime_snapshot_fails_closed():
    source = runtime_snapshot(sampled_at=NOW + timedelta(seconds=1))
    result = make_evaluator(runtime_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "future_runtime_snapshot"


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "",
        "not-a-timestamp",
        "2026-10-20T10:00:00",
        123,
    ],
)
def test_invalid_runtime_timestamp_fails_closed(timestamp):
    source = runtime_snapshot(timestamp=timestamp)
    result = make_evaluator(runtime_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "invalid_or_missing_runtime_timestamp"


@pytest.mark.parametrize(
    "field,value",
    [
        ("docker_daemon_memory_total_bytes", True),
        ("docker_daemon_memory_total_bytes", 0),
        ("linux_memory_total_bytes", -1),
        ("linux_memory_available_bytes", True),
        ("redis_container_memory_current_bytes", -1),
        ("redis_container_memory_limit_bytes", False),
    ],
)
def test_invalid_runtime_memory_values_fail_closed(field, value):
    result = make_evaluator(
        runtime_source=runtime_snapshot(**{field: value})
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "invalid_runtime_memory_metrics"


def test_linux_available_memory_above_total_fails_closed():
    result = make_evaluator(
        runtime_source=runtime_snapshot(
            linux_memory_available_bytes=4_062_822_401
        )
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "linux_available_memory_exceeds_total"


@pytest.mark.parametrize(
    "field,value",
    [
        ("aof_enabled", None),
        ("aof_enabled", 1),
        ("data_volume_mounted", None),
        ("data_volume_mounted", "true"),
    ],
)
def test_malformed_persistence_evidence_fails_closed(field, value):
    result = make_evaluator(
        runtime_source=runtime_snapshot(**{field: value})
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "invalid_runtime_persistence_metrics"


def test_aof_disabled_preview_is_refused():
    result = make_evaluator(
        runtime_source=runtime_snapshot(aof_enabled=False)
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_aof_not_enabled"
    assert result.preview.status is RedisCapacityPreviewStatus.REFUSED


def test_missing_data_volume_refuses_preview():
    result = make_evaluator(
        runtime_source=runtime_snapshot(data_volume_mounted=False)
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_data_volume_missing"


def test_insufficient_linux_reserve_refuses_preview():
    result = make_evaluator(
        sizing=preview_config(
            minimum_linux_available_reserve_bytes=1_600_000_000
        )
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "linux_available_memory_budget_exceeded"


def test_runtime_clock_exception_fails_closed_without_leaking_message():
    def broken_clock():
        raise RuntimeError("private clock diagnostic")

    result = make_evaluator(clock=broken_clock).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_clock_failed"
    assert result.error_type == "RuntimeError"
    assert "private clock diagnostic" not in repr(result)


def test_naive_runtime_clock_fails_closed():
    result = make_evaluator(
        clock=lambda: datetime(2026, 10, 20, 10, 0)
    ).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "invalid_runtime_clock"


def test_extra_runtime_fields_are_not_exposed_in_result():
    source = runtime_snapshot(private_token="do-not-expose-this")
    result = make_evaluator(runtime_source=source).evaluate()

    assert result.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED
    assert "do-not-expose-this" not in repr(result)


@pytest.mark.parametrize(
    "expected_container,age",
    [
        ("", 20),
        ("   ", 20),
        (CONTAINER, 0),
        (CONTAINER, -1),
        (CONTAINER, float("nan")),
        (CONTAINER, float("inf")),
        (CONTAINER, True),
        (CONTAINER, "20"),
    ],
)
def test_invalid_runtime_evaluation_config_is_rejected(expected_container, age):
    with pytest.raises(ValueError):
        RedisRuntimeEvaluationConfig(
            expected_container_name=expected_container,
            max_runtime_metrics_age_seconds=age,
        )


def test_evaluator_requires_valid_injected_dependencies():
    observation_adapter = make_evaluator()._observation_adapter

    with pytest.raises(TypeError):
        RedisRuntimeCapacityEvaluator(
            observation_adapter=None,
            runtime_snapshot_collector=lambda: runtime_snapshot(),
            config=RedisRuntimeEvaluationConfig(CONTAINER, 20),
            preview_config=preview_config(),
        )

    with pytest.raises(TypeError):
        RedisRuntimeCapacityEvaluator(
            observation_adapter=observation_adapter,
            runtime_snapshot_collector=None,
            config=RedisRuntimeEvaluationConfig(CONTAINER, 20),
            preview_config=preview_config(),
        )

    with pytest.raises(TypeError):
        RedisRuntimeCapacityEvaluator(
            observation_adapter=observation_adapter,
            runtime_snapshot_collector=lambda: runtime_snapshot(),
            config=None,
            preview_config=preview_config(),
        )

    with pytest.raises(TypeError):
        RedisRuntimeCapacityEvaluator(
            observation_adapter=observation_adapter,
            runtime_snapshot_collector=lambda: runtime_snapshot(),
            config=RedisRuntimeEvaluationConfig(CONTAINER, 20),
            preview_config=None,
        )


def test_result_never_exposes_actuation_permission():
    result = make_evaluator().evaluate()

    assert result.actuation_allowed is False
    assert not hasattr(result, "scale_command")
    assert not hasattr(result, "apply")