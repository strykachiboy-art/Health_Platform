"""Orchestrate fresh Redis observations and a read-only capacity preview.

Runtime metrics must come from an explicitly injected read-only collector.
This module does not execute Docker commands, change Redis settings, or scale
infrastructure. A preview result is never permission to apply a change.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from app.core.autoscaling.redis_capacity_observation import (
    RedisCapacityObservationAdapter,
    RedisCapacityObservationResult,
    RedisCapacityObservationConfig,
)
from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityAction,
    RedisCapacityPolicyConfig,
)
from app.core.autoscaling.redis_capacity_preview import (
    RedisCapacityPreviewConfig,
    RedisCapacityPreviewResult,
    RedisCapacityPreviewStatus,
    preview_redis_capacity,
)


class RedisRuntimeEvaluationStatus(str, Enum):
    PREVIEW_ACCEPTED = "preview_accepted"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class RedisRuntimeEvaluationConfig:
    """Explicit runtime snapshot freshness and identity contract."""

    expected_container_name: str
    max_runtime_metrics_age_seconds: float

    def __post_init__(self) -> None:
        name = self.expected_container_name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("expected_container_name must be a non-empty string")

        age = self.max_runtime_metrics_age_seconds
        if isinstance(age, bool) or not isinstance(age, (int, float)):
            raise ValueError(
                "max_runtime_metrics_age_seconds must be finite and > 0"
            )

        try:
            normalized_age = float(age)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                "max_runtime_metrics_age_seconds must be finite and > 0"
            ) from None

        if not math.isfinite(normalized_age) or normalized_age <= 0:
            raise ValueError(
                "max_runtime_metrics_age_seconds must be finite and > 0"
            )

        object.__setattr__(self, "expected_container_name", name.strip())
        object.__setattr__(
            self, "max_runtime_metrics_age_seconds", normalized_age
        )


@dataclass(frozen=True, slots=True)
class RedisRuntimeEvaluationResult:
    """Runtime-backed preview evidence without raw collector payloads."""

    status: RedisRuntimeEvaluationStatus
    reason: str
    observation: RedisCapacityObservationResult | None = None
    preview: RedisCapacityPreviewResult | None = None
    runtime_container_name: str | None = None
    runtime_sampled_at: datetime | None = None
    runtime_age_seconds: float | None = None
    error_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED

    @property
    def actuation_allowed(self) -> bool:
        """Evaluation and preview never authorize infrastructure changes."""
        return False

    @property
    def requires_operator_review(self) -> bool:
        if not self.ok or self.observation is None:
            return True
        return self.observation.requires_operator_review


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _parse_timestamp(value: object) -> datetime | None:
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


_RUNTIME_INTEGER_FIELDS = {
    "docker_daemon_memory_total_bytes": True,
    "linux_memory_total_bytes": True,
    "linux_memory_available_bytes": False,
    "redis_container_memory_current_bytes": False,
    "redis_container_memory_limit_bytes": False,
}


_SAFE_DOCKER_RUNTIME_FAILURE_REASONS = frozenset(
    {
        "collection_clock_failed",
        "invalid_collection_clock",
        "docker_daemon_memory_query_failed",
        "invalid_docker_daemon_memory",
        "docker_container_inspection_failed",
        "runtime_container_identity_mismatch",
        "invalid_runtime_container_identity",
        "runtime_image_mismatch",
        "invalid_runtime_compose_labels",
        "runtime_compose_project_mismatch",
        "runtime_compose_service_mismatch",
        "invalid_runtime_container_state",
        "runtime_container_not_running",
        "runtime_container_oom_killed",
        "runtime_container_unhealthy",
        "invalid_container_restart_count",
        "invalid_configured_container_memory_limit",
        "redis_data_volume_missing_or_not_writable",
        "linux_memory_query_failed",
        "invalid_linux_memory_metrics",
        "linux_available_memory_exceeds_total",
        "cgroup_memory_usage_query_failed",
        "cgroup_memory_limit_query_failed",
        "inconsistent_cgroup_version",
        "invalid_cgroup_memory_limit",
        "invalid_cgroup_memory_usage",
        "container_usage_exceeds_effective_memory_limit",
        "redis_persistence_query_failed",
        "redis_aof_disabled",
        "redis_aof_write_unhealthy",
        "runtime_collection_failed",
    }
)

class RedisRuntimeCapacityEvaluator:
    """Join fresh Redis metrics with separately sampled runtime capacity."""

    def __init__(
        self,
        *,
        observation_adapter: RedisCapacityObservationAdapter,
        runtime_snapshot_collector: Callable[[], Mapping[str, Any]],
        config: RedisRuntimeEvaluationConfig,
        preview_config: RedisCapacityPreviewConfig,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(
            observation_adapter, RedisCapacityObservationAdapter
        ):
            raise TypeError(
                "observation_adapter must be a RedisCapacityObservationAdapter"
            )
        if not callable(runtime_snapshot_collector):
            raise TypeError("runtime_snapshot_collector must be callable")
        if not isinstance(config, RedisRuntimeEvaluationConfig):
            raise TypeError("config must be a RedisRuntimeEvaluationConfig")
        if not isinstance(preview_config, RedisCapacityPreviewConfig):
            raise TypeError("preview_config must be a RedisCapacityPreviewConfig")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._observation_adapter = observation_adapter
        self._runtime_snapshot_collector = runtime_snapshot_collector
        self.config = config
        self.preview_config = preview_config
        self._clock = clock

    def evaluate(self) -> RedisRuntimeEvaluationResult:
        """Collect fresh evidence, validate it, and evaluate without mutation."""
        try:
            observation = self._observation_adapter.collect()
        except Exception as exc:
            return self._refuse(
                "redis_observation_collection_failed",
                error_type=type(exc).__name__,
            )

        if not isinstance(observation, RedisCapacityObservationResult):
            return self._refuse("invalid_redis_observation_result")

        if (
            observation.healthy is not True
            or observation.decision.action is RedisCapacityAction.FAIL_CLOSED
        ):
            return self._refuse(
                "redis_capacity_observation_unhealthy",
                observation=observation,
            )

        try:
            raw_runtime = self._runtime_snapshot_collector()
        except Exception as exc:
            return self._refuse(
                "runtime_snapshot_collection_failed",
                observation=observation,
                error_type=type(exc).__name__,
            )

        if not isinstance(raw_runtime, Mapping):
            return self._refuse(
                "invalid_runtime_snapshot",
                observation=observation,
            )

        # Keep only known, non-secret runtime evidence. Arbitrary collector
        # payload fields are not copied into the result or preview.
        if raw_runtime.get("healthy") is not True:
            known_reason = raw_runtime.get("reason")
            if (
                isinstance(known_reason, str)
                and known_reason in _SAFE_DOCKER_RUNTIME_FAILURE_REASONS
            ):
                return self._refuse(
                    known_reason,
                    observation=observation,
                )
            return self._refuse(
                "runtime_metrics_unhealthy",
                observation=observation,
            )

        container_name = raw_runtime.get("container_name")
        if not isinstance(container_name, str) or not container_name.strip():
            return self._refuse(
                "invalid_runtime_container_identity",
                observation=observation,
            )

        if container_name.strip() != self.config.expected_container_name:
            return self._refuse(
                "runtime_container_identity_mismatch",
                observation=observation,
                container_name=container_name.strip(),
            )

        if raw_runtime.get("healthy") is not True:
            return self._refuse(
                "runtime_metrics_unhealthy",
                observation=observation,
                container_name=container_name.strip(),
            )

        sampled_at = _parse_timestamp(raw_runtime.get("timestamp"))
        if sampled_at is None:
            return self._refuse(
                "invalid_or_missing_runtime_timestamp",
                observation=observation,
                container_name=container_name.strip(),
            )

        try:
            reference_time = self._clock()
        except Exception as exc:
            return self._refuse(
                "runtime_clock_failed",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                error_type=type(exc).__name__,
            )

        if not _aware_datetime(reference_time):
            return self._refuse(
                "invalid_runtime_clock",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
            )

        try:
            age_seconds = (
                reference_time.astimezone(timezone.utc)
                - sampled_at.astimezone(timezone.utc)
            ).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return self._refuse(
                "invalid_runtime_metrics_age",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
            )

        if not math.isfinite(age_seconds):
            return self._refuse(
                "invalid_runtime_metrics_age",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
            )

        if age_seconds < 0:
            return self._refuse(
                "future_runtime_snapshot",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
            )

        if age_seconds > self.config.max_runtime_metrics_age_seconds:
            return self._refuse(
                "stale_runtime_snapshot",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
            )

        runtime_values: dict[str, int] = {}
        for field, must_be_positive in _RUNTIME_INTEGER_FIELDS.items():
            value = raw_runtime.get(field)
            if (
                type(value) is not int
                or value < 0
                or (must_be_positive and value == 0)
            ):
                return self._refuse(
                    "invalid_runtime_memory_metrics",
                    observation=observation,
                    container_name=container_name.strip(),
                    sampled_at=sampled_at,
                    age_seconds=age_seconds,
                )
            runtime_values[field] = value

        if runtime_values["linux_memory_available_bytes"] > runtime_values[
            "linux_memory_total_bytes"
        ]:
            return self._refuse(
                "linux_available_memory_exceeds_total",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
            )

        for field in ("aof_enabled", "data_volume_mounted"):
            if type(raw_runtime.get(field)) is not bool:
                return self._refuse(
                    "invalid_runtime_persistence_metrics",
                    observation=observation,
                    container_name=container_name.strip(),
                    sampled_at=sampled_at,
                    age_seconds=age_seconds,
                )

        metrics = observation.metrics
        if not isinstance(metrics, Mapping):
            return self._refuse(
                "invalid_redis_observation_metrics",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
            )

        preview_snapshot: dict[str, Any] = {
            **runtime_values,
            "redis_used_memory_bytes": metrics.get("used_memory_bytes"),
            "redis_used_memory_peak_bytes": metrics.get(
                "used_memory_peak_bytes"
            ),
            "redis_maxmemory_bytes": metrics.get("max_memory_bytes"),
            "aof_enabled": raw_runtime["aof_enabled"],
            "data_volume_mounted": raw_runtime["data_volume_mounted"],
        }

        try:
            preview = preview_redis_capacity(
                preview_snapshot,
                config=self.preview_config,
            )
        except Exception as exc:
            return self._refuse(
                "capacity_preview_failed",
                observation=observation,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
                error_type=type(exc).__name__,
            )

        if preview.status is not RedisCapacityPreviewStatus.ACCEPTED:
            return self._refuse(
                preview.reason,
                observation=observation,
                preview=preview,
                container_name=container_name.strip(),
                sampled_at=sampled_at,
                age_seconds=age_seconds,
            )

        return RedisRuntimeEvaluationResult(
            status=RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED,
            reason="runtime_capacity_preview_accepted_no_changes_performed",
            observation=observation,
            preview=preview,
            runtime_container_name=container_name.strip(),
            runtime_sampled_at=sampled_at,
            runtime_age_seconds=age_seconds,
        )

    def _refuse(
        self,
        reason: str,
        *,
        observation: RedisCapacityObservationResult | None = None,
        preview: RedisCapacityPreviewResult | None = None,
        container_name: str | None = None,
        sampled_at: datetime | None = None,
        age_seconds: float | None = None,
        error_type: str | None = None,
    ) -> RedisRuntimeEvaluationResult:
        return RedisRuntimeEvaluationResult(
            status=RedisRuntimeEvaluationStatus.REFUSED,
            reason=reason,
            observation=observation,
            preview=preview,
            runtime_container_name=container_name,
            runtime_sampled_at=sampled_at,
            runtime_age_seconds=age_seconds,
            error_type=error_type,
        )