from __future__ import annotations

import math

import pytest

from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityAction,
    RedisCapacityPolicyConfig,
    evaluate_redis_capacity,
)


def policy():
    # Test fixture only; these are not recommended production thresholds.
    return RedisCapacityPolicyConfig(
        warning_usage_ratio=0.70,
        critical_usage_ratio=0.85,
    )


def bounded_snapshot(usage_ratio=0.50, **overrides):
    remaining = 500
    if (
        not isinstance(usage_ratio, bool)
        and isinstance(usage_ratio, (int, float))
        and math.isfinite(float(usage_ratio))
    ):
        remaining = max(0, min(1000, int(round(1000 * (1 - usage_ratio)))))

    metrics = {
        "healthy": True,
        "memory_capacity_metrics_healthy": True,
        "memory_capacity_metrics_reason": "ok",
        "max_memory_bytes": 1000,
        "max_memory_policy": "noeviction",
        "memory_limit_configured": True,
        "memory_limit_usage_ratio": usage_ratio,
        "memory_limit_remaining_bytes": remaining,
    }
    metrics.update(overrides)
    return metrics


def evaluate(metrics):
    return evaluate_redis_capacity(metrics, config=policy())


def test_usage_below_warning_threshold_holds():
    result = evaluate(bounded_snapshot(usage_ratio=0.69))

    assert result.action is RedisCapacityAction.HOLD
    assert result.reason == "usage_below_warning_threshold"
    assert result.requires_operator_review is False
    assert result.actuation_allowed is False


def test_warning_threshold_is_inclusive():
    result = evaluate(bounded_snapshot(usage_ratio=0.70))

    assert result.action is RedisCapacityAction.REVIEW_CAPACITY_WARNING
    assert result.reason == "warning_threshold_reached"
    assert result.observed_usage_ratio == 0.70
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_critical_threshold_is_inclusive():
    result = evaluate(bounded_snapshot(usage_ratio=0.85))

    assert result.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert result.reason == "critical_threshold_reached"
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


def test_critical_threshold_takes_precedence():
    result = evaluate(bounded_snapshot(usage_ratio=0.95))

    assert result.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert result.observed_usage_ratio == 0.95
    assert result.actuation_allowed is False


def test_usage_above_maxmemory_is_valid_critical_evidence():
    result = evaluate(bounded_snapshot(usage_ratio=1.05))

    assert result.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert result.observed_usage_ratio == 1.05


def test_unconfigured_memory_limit_requires_review_not_scaling():
    metrics = {
        "healthy": True,
        "memory_capacity_metrics_healthy": True,
        "memory_capacity_metrics_reason": "ok",
        "max_memory_bytes": 0,
        "max_memory_policy": "noeviction",
        "memory_limit_configured": False,
        "memory_limit_usage_ratio": None,
        "memory_limit_remaining_bytes": None,
    }

    result = evaluate(metrics)

    assert result.action is RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY
    assert result.reason == "memory_limit_unconfigured"
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"healthy": False},
        {"healthy": None},
        {"memory_capacity_metrics_healthy": False},
        {"memory_capacity_metrics_reason": "config_access_denied"},
        {"max_memory_bytes": True},
        {"max_memory_bytes": -1},
        {"max_memory_policy": ""},
        {"max_memory_policy": None},
        {"memory_limit_configured": 1},
        {"memory_limit_configured": False},
        {"memory_limit_remaining_bytes": True},
        {"memory_limit_remaining_bytes": -1},
        {"memory_limit_remaining_bytes": 1001},
        {"memory_limit_usage_ratio": True},
        {"memory_limit_usage_ratio": -0.1},
        {"memory_limit_usage_ratio": float("nan")},
        {"memory_limit_usage_ratio": float("inf")},
        {"memory_limit_usage_ratio": "0.5"},
    ],
)
def test_missing_unhealthy_or_malformed_metrics_fail_closed(overrides):
    result = evaluate(bounded_snapshot(**overrides))

    assert result.action is RedisCapacityAction.FAIL_CLOSED
    assert result.actuation_allowed is False


def test_missing_snapshot_fails_closed():
    result = evaluate_redis_capacity(None, config=policy())

    assert result.action is RedisCapacityAction.FAIL_CLOSED
    assert result.reason == "invalid_metrics_snapshot"
    assert result.actuation_allowed is False


def test_unbounded_memory_fields_must_be_consistent():
    metrics = {
        "healthy": True,
        "memory_capacity_metrics_healthy": True,
        "memory_capacity_metrics_reason": "ok",
        "max_memory_bytes": 0,
        "max_memory_policy": "noeviction",
        "memory_limit_configured": False,
        "memory_limit_usage_ratio": 0.5,
        "memory_limit_remaining_bytes": None,
    }

    result = evaluate(metrics)

    assert result.action is RedisCapacityAction.FAIL_CLOSED
    assert result.reason == "invalid_unbounded_memory_metrics"


@pytest.mark.parametrize(
    "warning,critical",
    [
        (0, 0.85),
        (-0.1, 0.85),
        (0.70, 0),
        (0.70, 1.01),
        (0.70, 0.70),
        (0.90, 0.85),
        (float("nan"), 0.85),
        (0.70, float("inf")),
        (True, 0.85),
        (0.70, True),
    ],
)
def test_invalid_policy_thresholds_are_rejected(warning, critical):
    with pytest.raises(ValueError):
        RedisCapacityPolicyConfig(
            warning_usage_ratio=warning,
            critical_usage_ratio=critical,
        )


def test_policy_configuration_is_required_and_explicit():
    with pytest.raises(TypeError):
        evaluate_redis_capacity(bounded_snapshot(), config=None)


def test_critical_result_contains_no_scale_target_or_actuator():
    result = evaluate(bounded_snapshot(usage_ratio=0.90))

    assert result.action is RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
    assert not hasattr(result, "target_replicas")
    assert not hasattr(result, "scale_command")
    assert result.actuation_allowed is False