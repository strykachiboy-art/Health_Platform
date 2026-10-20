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


NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def policy():
    # Test-only thresholds; not production recommendations.
    return RedisCapacityPolicyConfig(
        warning_usage_ratio=0.70,
        critical_usage_ratio=0.85,
    )


def metrics(*, sampled_at=None, usage_ratio=0.50, **overrides):
    if sampled_at is None:
        sampled_at = NOW - timedelta(seconds=3)

    data = {
        "timestamp": sampled_at.isoformat(),
        "healthy": True,
        "memory_capacity_metrics_healthy": True,
        "memory_capacity_metrics_reason": "ok",
        "used_memory_bytes": 500,
        "max_memory_bytes": 1000,
        "max_memory_policy": "noeviction",
        "memory_limit_configured": True,
        "memory_limit_usage_ratio": usage_ratio,
        "memory_limit_remaining_bytes": 500,
    }
    data.update(overrides)
    return data


def adapter_for(
    result,
    *,
    clock=lambda: NOW,
    max_age=20,
    collector=None,
    redis_client=None,
):
    if redis_client is None:
        redis_client = object()

    if collector is None:
        collector = lambda **kwargs: result

    return RedisCapacityObservationAdapter(
        redis_client=redis_client,
        config=RedisCapacityObservationConfig(
            max_metrics_age_seconds=max_age,
        ),
        policy=policy(),
        metrics_collector=collector,
        clock=clock,
    )


def test_fresh_metrics_produce_policy_decision():
    source = metrics(sampled_at=NOW - timedelta(seconds=3))
    result = adapter_for(source).collect()

    assert result.healthy is True
    assert result.reason == "usage_below_warning_threshold"
    assert result.decision.action is RedisCapacityAction.HOLD
    assert result.sampled_at == NOW - timedelta(seconds=3)
    assert result.metrics_age_seconds == 3
    assert result.metrics["max_memory_bytes"] == 1000
    assert result.actuation_allowed is False


def test_fresh_critical_metrics_require_review():
    result = adapter_for(metrics(usage_ratio=0.90)).collect()

    assert result.healthy is True
    assert result.decision.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_sample_exactly_at_maximum_age_is_accepted():
    sample_time = NOW - timedelta(seconds=10)
    result = adapter_for(
        metrics(sampled_at=sample_time),
        max_age=10,
    ).collect()

    assert result.healthy is True
    assert result.metrics_age_seconds == 10
    assert result.decision.action is RedisCapacityAction.HOLD


def test_stale_metrics_fail_closed():
    source = metrics(sampled_at=NOW - timedelta(seconds=21))
    result = adapter_for(source, max_age=20).collect()

    assert result.healthy is False
    assert result.reason == "stale_redis_metrics"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED
    assert result.decision.observed_usage_ratio is None
    assert result.actuation_allowed is False


def test_future_timestamp_fails_closed():
    source = metrics(sampled_at=NOW + timedelta(seconds=1))
    result = adapter_for(source).collect()

    assert result.healthy is False
    assert result.reason == "future_redis_metrics_timestamp"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED
    assert result.actuation_allowed is False


@pytest.mark.parametrize(
    "timestamp",
    [
        None,
        "",
        "not-a-timestamp",
        "2026-10-20T10:00:00",
        "2026-10-20",
        123,
    ],
)
def test_missing_or_invalid_timestamp_fails_closed(timestamp):
    source = metrics()
    source["timestamp"] = timestamp

    result = adapter_for(source).collect()

    assert result.healthy is False
    assert result.reason == "invalid_or_missing_metric_timestamp"
    assert result.sampled_at is None
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


def test_zulu_timestamp_is_accepted():
    source = metrics()
    source["timestamp"] = (NOW - timedelta(seconds=4)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    result = adapter_for(source).collect()

    assert result.healthy is True
    assert result.sampled_at == NOW - timedelta(seconds=4)
    assert result.metrics_age_seconds == 4


def test_unhealthy_redis_metrics_fail_closed():
    result = adapter_for(metrics(healthy=False)).collect()

    assert result.healthy is False
    assert result.reason == "redis_health_unavailable"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


def test_unhealthy_capacity_telemetry_fails_closed():
    result = adapter_for(
        metrics(
            memory_capacity_metrics_healthy=False,
            memory_capacity_metrics_reason="redis_capacity_config_unavailable",
        )
    ).collect()

    assert result.healthy is False
    assert result.reason == "capacity_metrics_unavailable"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


def test_malformed_capacity_fields_fail_closed():
    result = adapter_for(metrics(max_memory_bytes=True)).collect()

    assert result.healthy is False
    assert result.reason == "invalid_capacity_metrics"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


def test_unbounded_redis_limit_is_valid_but_requires_review():
    source = metrics(
        max_memory_bytes=0,
        memory_limit_configured=False,
        memory_limit_usage_ratio=None,
        memory_limit_remaining_bytes=None,
    )
    result = adapter_for(source).collect()

    assert result.healthy is True
    assert result.decision.action is RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_collector_receives_the_explicit_redis_client():
    client = object()
    source = metrics()
    calls = []

    def collector(**kwargs):
        calls.append(kwargs)
        return source

    result = adapter_for(
        source,
        redis_client=client,
        collector=collector,
    ).collect()

    assert result.healthy is True
    assert len(calls) == 1
    assert calls[0] == {"client": client}


def test_collection_exception_is_redacted_and_fails_closed():
    def collector(**kwargs):
        raise TimeoutError("sensitive connection detail")

    result = adapter_for(None, collector=collector).collect()

    assert result.healthy is False
    assert result.reason == "metric_collection_failed"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED
    assert result.metrics["error_type"] == "TimeoutError"
    assert "sensitive connection detail" not in repr(result)
    assert result.actuation_allowed is False


def test_non_mapping_collector_result_fails_closed():
    result = adapter_for(["not", "a", "mapping"]).collect()

    assert result.healthy is False
    assert result.reason == "invalid_metric_collector_result"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


def test_clock_is_read_after_collection():
    sampled_at = NOW - timedelta(seconds=2)
    events = []

    def collector(**kwargs):
        events.append("collect")
        return metrics(sampled_at=sampled_at)

    def clock():
        events.append("clock")
        return NOW

    result = adapter_for(
        None,
        collector=collector,
        clock=clock,
    ).collect()

    assert events == ["collect", "clock"]
    assert result.healthy is True
    assert result.metrics_age_seconds == 2


def test_clock_exception_fails_closed():
    def broken_clock():
        raise RuntimeError("sensitive clock detail")

    result = adapter_for(metrics(), clock=broken_clock).collect()

    assert result.healthy is False
    assert result.reason == "observation_clock_failed"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED
    assert result.metrics["observation_clock_error_type"] == "RuntimeError"
    assert "sensitive clock detail" not in repr(result)


def test_naive_clock_fails_closed():
    result = adapter_for(
        metrics(),
        clock=lambda: datetime(2026, 10, 20, 10, 0),
    ).collect()

    assert result.healthy is False
    assert result.reason == "invalid_observation_clock"
    assert result.decision.action is RedisCapacityAction.FAIL_CLOSED


@pytest.mark.parametrize(
    "max_age",
    [
        0,
        -1,
        True,
        float("nan"),
        float("inf"),
        "20",
        None,
    ],
)
def test_invalid_maximum_metric_age_is_rejected(max_age):
    with pytest.raises(ValueError):
        RedisCapacityObservationConfig(max_metrics_age_seconds=max_age)


def test_adapter_rejects_missing_redis_client():
    with pytest.raises(ValueError):
        RedisCapacityObservationAdapter(
            redis_client=None,
            config=RedisCapacityObservationConfig(
                max_metrics_age_seconds=20,
            ),
            policy=policy(),
        )


def test_adapter_rejects_invalid_dependency_types():
    with pytest.raises(TypeError):
        RedisCapacityObservationAdapter(
            redis_client=object(),
            config=None,
            policy=policy(),
        )

    with pytest.raises(TypeError):
        RedisCapacityObservationAdapter(
            redis_client=object(),
            config=RedisCapacityObservationConfig(
                max_metrics_age_seconds=20,
            ),
            policy=None,
        )

    with pytest.raises(TypeError):
        RedisCapacityObservationAdapter(
            redis_client=object(),
            config=RedisCapacityObservationConfig(
                max_metrics_age_seconds=20,
            ),
            policy=policy(),
            metrics_collector=None,
        )

    with pytest.raises(TypeError):
        RedisCapacityObservationAdapter(
            redis_client=object(),
            config=RedisCapacityObservationConfig(
                max_metrics_age_seconds=20,
            ),
            policy=policy(),
            clock=None,
        )