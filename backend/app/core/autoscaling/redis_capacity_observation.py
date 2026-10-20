"""Freshness-aware Redis capacity observation adapter.

This module collects metrics and passes fresh, validated snapshots to the
decision-only Redis capacity policy. It has no infrastructure actuator.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityAction,
    RedisCapacityDecision,
    RedisCapacityPolicyConfig,
    evaluate_redis_capacity,
)
from app.core.observability.redis_metrics import collect_redis_metrics


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO timestamp only when it specifies a timezone."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError):
        return None

    return parsed if _aware_datetime(parsed) else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RedisCapacityObservationConfig:
    """Explicit maximum permitted age for a Redis metrics snapshot."""

    max_metrics_age_seconds: float

    def __post_init__(self) -> None:
        value = self.max_metrics_age_seconds

        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("max_metrics_age_seconds must be finite and > 0")

        try:
            normalized = float(value)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(
                "max_metrics_age_seconds must be finite and > 0"
            ) from None

        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError("max_metrics_age_seconds must be finite and > 0")

        object.__setattr__(self, "max_metrics_age_seconds", normalized)


@dataclass(frozen=True, slots=True)
class RedisCapacityObservationResult:
    """Capacity decision and its freshness/collection evidence."""

    decision: RedisCapacityDecision
    metrics: Mapping[str, Any]
    sampled_at: datetime | None
    metrics_age_seconds: float | None
    healthy: bool
    reason: str

    @property
    def requires_operator_review(self) -> bool:
        return self.decision.requires_operator_review

    @property
    def actuation_allowed(self) -> bool:
        """Observation results never grant permission to change infrastructure."""
        return False


class RedisCapacityObservationAdapter:
    """Produce a fail-closed decision from a fresh Redis metrics snapshot.

    The collector's timestamp is checked against a clock read after collection.
    Stale, future-dated, malformed, or unavailable observations cannot produce
    an actionable capacity assessment. This adapter never scales Redis.
    """

    def __init__(
        self,
        *,
        redis_client,
        config: RedisCapacityObservationConfig,
        policy: RedisCapacityPolicyConfig,
        metrics_collector: Callable[..., dict[str, Any]] = (
            collect_redis_metrics
        ),
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if redis_client is None:
            raise ValueError("redis_client must be provided")

        if not isinstance(config, RedisCapacityObservationConfig):
            raise TypeError(
                "config must be a RedisCapacityObservationConfig"
            )

        if not isinstance(policy, RedisCapacityPolicyConfig):
            raise TypeError("policy must be a RedisCapacityPolicyConfig")

        if not callable(metrics_collector):
            raise TypeError("metrics_collector must be callable")

        if not callable(clock):
            raise TypeError("clock must be callable")

        self._redis_client = redis_client
        self.config = config
        self.policy = policy
        self._metrics_collector = metrics_collector
        self._clock = clock

    def collect(self) -> RedisCapacityObservationResult:
        """Collect once, reject uncertain evidence, then evaluate the policy."""
        try:
            raw_metrics = self._metrics_collector(client=self._redis_client)
        except Exception as exc:
            return self._fail_closed(
                "metric_collection_failed",
                {
                    "healthy": False,
                    "reason": "metric_collection_failed",
                    "error_type": type(exc).__name__,
                },
            )

        if not isinstance(raw_metrics, Mapping):
            return self._fail_closed(
                "invalid_metric_collector_result",
                {
                    "healthy": False,
                    "reason": "invalid_metric_collector_result",
                },
            )

        metrics = dict(raw_metrics)
        sampled_at = _parse_timestamp(metrics.get("timestamp"))

        if sampled_at is None:
            return self._fail_closed(
                "invalid_or_missing_metric_timestamp",
                metrics,
            )

        try:
            reference_time = self._clock()
        except Exception as exc:
            return self._fail_closed(
                "observation_clock_failed",
                metrics,
                sampled_at=sampled_at,
                extra_metrics={"observation_clock_error_type": type(exc).__name__},
            )

        if not _aware_datetime(reference_time):
            return self._fail_closed(
                "invalid_observation_clock",
                metrics,
                sampled_at=sampled_at,
            )

        try:
            age_seconds = (
                reference_time.astimezone(timezone.utc)
                - sampled_at.astimezone(timezone.utc)
            ).total_seconds()
        except (OverflowError, TypeError, ValueError):
            return self._fail_closed(
                "invalid_metrics_age",
                metrics,
                sampled_at=sampled_at,
            )

        if not math.isfinite(age_seconds):
            return self._fail_closed(
                "invalid_metrics_age",
                metrics,
                sampled_at=sampled_at,
            )

        if age_seconds < 0:
            return self._fail_closed(
                "future_redis_metrics_timestamp",
                metrics,
                sampled_at=sampled_at,
                metrics_age_seconds=age_seconds,
            )

        if age_seconds > self.config.max_metrics_age_seconds:
            return self._fail_closed(
                "stale_redis_metrics",
                metrics,
                sampled_at=sampled_at,
                metrics_age_seconds=age_seconds,
            )

        decision = evaluate_redis_capacity(
            metrics,
            config=self.policy,
        )
        healthy = decision.action is not RedisCapacityAction.FAIL_CLOSED

        return RedisCapacityObservationResult(
            decision=decision,
            metrics=metrics,
            sampled_at=sampled_at,
            metrics_age_seconds=age_seconds,
            healthy=healthy,
            reason=decision.reason,
        )

    def _fail_closed(
        self,
        reason: str,
        metrics: Mapping[str, Any],
        *,
        sampled_at: datetime | None = None,
        metrics_age_seconds: float | None = None,
        extra_metrics: Mapping[str, Any] | None = None,
    ) -> RedisCapacityObservationResult:
        """Create a consistent, non-actuating failure result."""
        safe_metrics = dict(metrics)
        if extra_metrics:
            safe_metrics.update(extra_metrics)

        decision = RedisCapacityDecision(
            action=RedisCapacityAction.FAIL_CLOSED,
            reason=reason,
            observed_usage_ratio=None,
            warning_usage_ratio=float(self.policy.warning_usage_ratio),
            critical_usage_ratio=float(self.policy.critical_usage_ratio),
        )

        return RedisCapacityObservationResult(
            decision=decision,
            metrics=safe_metrics,
            sampled_at=sampled_at,
            metrics_age_seconds=metrics_age_seconds,
            healthy=False,
            reason=reason,
        )