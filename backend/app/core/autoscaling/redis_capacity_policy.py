"""Decision-only Redis memory-capacity policy.

This module recommends operational review. It has no infrastructure actuator,
does not change Redis configuration, and does not scale Compose services.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RedisCapacityAction(str, Enum):
    """Safe outcomes of evaluating a Redis capacity snapshot."""

    HOLD = "hold"
    REVIEW_UNBOUNDED_MEMORY = "review_unbounded_memory"
    REVIEW_CAPACITY_WARNING = "review_capacity_warning"
    REVIEW_CAPACITY_CRITICAL = "review_capacity_critical"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True, slots=True)
class RedisCapacityPolicyConfig:
    """Explicit usage ratios chosen for a calibrated deployment.

    Ratios are fractions, not percentages: 0.80 means 80% of maxmemory.
    There are deliberately no production-tuned defaults.
    """

    warning_usage_ratio: float
    critical_usage_ratio: float

    def __post_init__(self) -> None:
        warning = _configured_ratio(
            "warning_usage_ratio", self.warning_usage_ratio
        )
        critical = _configured_ratio(
            "critical_usage_ratio", self.critical_usage_ratio
        )

        if warning >= critical:
            raise ValueError(
                "warning_usage_ratio must be less than critical_usage_ratio"
            )


@dataclass(frozen=True, slots=True)
class RedisCapacityDecision:
    """Structured recommendation without an infrastructure command."""

    action: RedisCapacityAction
    reason: str
    observed_usage_ratio: float | None
    warning_usage_ratio: float
    critical_usage_ratio: float
    max_memory_bytes: int | None = None
    remaining_memory_bytes: int | None = None
    memory_policy: str | None = None

    @property
    def requires_operator_review(self) -> bool:
        return self.action in {
            RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY,
            RedisCapacityAction.REVIEW_CAPACITY_WARNING,
            RedisCapacityAction.REVIEW_CAPACITY_CRITICAL,
        }

    @property
    def actuation_allowed(self) -> bool:
        """Live scaling/configuration changes are never allowed by this policy."""
        return False


def evaluate_redis_capacity(
    metrics: Mapping[str, Any],
    *,
    config: RedisCapacityPolicyConfig,
) -> RedisCapacityDecision:
    """Evaluate a Redis metrics snapshot and fail closed on uncertain evidence.

    The caller is responsible for supplying a fresh snapshot from the Redis
    metrics collector. This function does not connect to Redis or perform I/O.
    """
    if not isinstance(config, RedisCapacityPolicyConfig):
        raise TypeError("config must be a RedisCapacityPolicyConfig")

    if not isinstance(metrics, Mapping):
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "invalid_metrics_snapshot",
            config,
        )

    if metrics.get("healthy") is not True:
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "redis_health_unavailable",
            config,
        )

    if (
        metrics.get("memory_capacity_metrics_healthy") is not True
        or metrics.get("memory_capacity_metrics_reason") != "ok"
    ):
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "capacity_metrics_unavailable",
            config,
        )

    max_memory = metrics.get("max_memory_bytes")
    memory_policy = metrics.get("max_memory_policy")
    limit_configured = metrics.get("memory_limit_configured")
    usage_ratio = metrics.get("memory_limit_usage_ratio")
    remaining_bytes = metrics.get("memory_limit_remaining_bytes")

    valid_base = (
        type(max_memory) is int
        and max_memory >= 0
        and isinstance(memory_policy, str)
        and bool(memory_policy.strip())
        and type(limit_configured) is bool
    )
    if not valid_base:
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "invalid_capacity_metrics",
            config,
        )

    if max_memory == 0:
        # In this collector's contract, zero maxmemory means no configured
        # Redis memory cap. Do not invent a usage ratio or an upsize amount.
        if (
            limit_configured is not False
            or usage_ratio is not None
            or remaining_bytes is not None
        ):
            return _decision(
                RedisCapacityAction.FAIL_CLOSED,
                "invalid_unbounded_memory_metrics",
                config,
                memory_policy=memory_policy,
            )

        return _decision(
            RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY,
            "memory_limit_unconfigured",
            config,
            max_memory_bytes=0,
            memory_policy=memory_policy,
        )

    if limit_configured is not True:
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "memory_limit_state_mismatch",
            config,
            max_memory_bytes=max_memory,
            memory_policy=memory_policy,
        )

    normalized_usage = _observed_ratio(usage_ratio)
    valid_remaining = (
        type(remaining_bytes) is int
        and 0 <= remaining_bytes <= max_memory
    )
    if normalized_usage is None or not valid_remaining:
        return _decision(
            RedisCapacityAction.FAIL_CLOSED,
            "invalid_capacity_metrics",
            config,
            max_memory_bytes=max_memory,
            memory_policy=memory_policy,
        )

    # Critical takes precedence, including observations above 100%.
    if normalized_usage >= config.critical_usage_ratio:
        action = RedisCapacityAction.REVIEW_CAPACITY_CRITICAL
        reason = "critical_threshold_reached"
    elif normalized_usage >= config.warning_usage_ratio:
        action = RedisCapacityAction.REVIEW_CAPACITY_WARNING
        reason = "warning_threshold_reached"
    else:
        action = RedisCapacityAction.HOLD
        reason = "usage_below_warning_threshold"

    return _decision(
        action,
        reason,
        config,
        observed_usage_ratio=normalized_usage,
        max_memory_bytes=max_memory,
        remaining_memory_bytes=remaining_bytes,
        memory_policy=memory_policy,
    )


def _configured_ratio(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite and in the range (0, 1]")

    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(
            f"{name} must be finite and in the range (0, 1]"
        ) from None

    if not math.isfinite(normalized) or not 0 < normalized <= 1:
        raise ValueError(f"{name} must be finite and in the range (0, 1]")

    return normalized


def _observed_ratio(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return None

    if not math.isfinite(normalized) or normalized < 0:
        return None

    # A measured ratio may exceed 1 when usage is above maxmemory.
    return normalized


def _decision(
    action: RedisCapacityAction,
    reason: str,
    config: RedisCapacityPolicyConfig,
    *,
    observed_usage_ratio: float | None = None,
    max_memory_bytes: int | None = None,
    remaining_memory_bytes: int | None = None,
    memory_policy: str | None = None,
) -> RedisCapacityDecision:
    return RedisCapacityDecision(
        action=action,
        reason=reason,
        observed_usage_ratio=observed_usage_ratio,
        warning_usage_ratio=float(config.warning_usage_ratio),
        critical_usage_ratio=float(config.critical_usage_ratio),
        max_memory_bytes=max_memory_bytes,
        remaining_memory_bytes=remaining_memory_bytes,
        memory_policy=memory_policy,
    )