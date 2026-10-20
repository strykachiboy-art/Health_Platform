"""Fail-closed observation adapter for Celery worker autoscaling."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from app.core.autoscaling.decision_engine import ScalingObservation
from app.core.observability.celery_metrics import collect_celery_metrics


def _finite_number(value: object) -> float | None:
    """Return a finite numeric value, rejecting booleans and bad inputs."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _non_negative_integer(value: object) -> int | None:
    """Accept only a genuine, non-negative integer."""
    if type(value) is not int or value < 0:
        return None
    return value


def _positive_finite(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and number > 0


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _parse_timestamp(value: object) -> datetime | None:
    """Parse an ISO timestamp only when it includes a timezone."""
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError):
        return None

    if not _aware_datetime(parsed):
        return None

    return parsed


@dataclass(frozen=True, slots=True)
class WorkerObservationConfig:
    """Explicit capacity and freshness settings for worker observations.

    Targets must be calibrated for the deployed task mix before production use.
    """

    target_outstanding_tasks_per_worker_slot: float
    target_queue_age_ms: float
    max_metrics_age_seconds: float = 20.0
    max_queue_age_sample_age_seconds: float = 20.0
    queue_names: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for name in (
            "target_outstanding_tasks_per_worker_slot",
            "target_queue_age_ms",
            "max_metrics_age_seconds",
            "max_queue_age_sample_age_seconds",
        ):
            if not _positive_finite(getattr(self, name)):
                raise ValueError(f"{name} must be finite and greater than zero")

        if self.queue_names is not None:
            names = self.queue_names

            if not isinstance(names, (tuple, list)) or not names:
                raise ValueError(
                    "queue_names must be None or a non-empty tuple/list"
                )

            if any(
                not isinstance(name, str) or not name.strip()
                for name in names
            ):
                raise ValueError(
                    "queue_names must contain non-empty strings"
                )

            if len(set(names)) != len(names):
                raise ValueError("queue_names must not contain duplicates")

            object.__setattr__(self, "queue_names", tuple(names))


@dataclass(frozen=True, slots=True)
class WorkerObservationResult:
    """A scaling observation plus raw and derived metric evidence."""

    observation: ScalingObservation
    metrics: Mapping[str, Any]


class WorkerObservationAdapter:
    """Build a fail-closed ScalingObservation from Celery worker metrics.

    Backlog demand is queued + active + reserved tasks divided by configured
    outstanding-task capacity across the observed worker concurrency slots.

    A fresh queue-age sample may raise that ratio, but only while outstanding
    work exists. Old queue-age history is deliberately ignored.

    This adapter only produces an observation. It does not scale infrastructure.
    """

    def __init__(
        self,
        *,
        redis_client,
        config: WorkerObservationConfig,
        metrics_collector: Callable[..., dict[str, Any]] = (
            collect_celery_metrics
        ),
        celery_app=None,
    ) -> None:
        if not isinstance(config, WorkerObservationConfig):
            raise TypeError("config must be a WorkerObservationConfig")

        if not callable(metrics_collector):
            raise TypeError("metrics_collector must be callable")

        self._redis_client = redis_client
        self._celery_app = celery_app
        self.config = config
        self._metrics_collector = metrics_collector

    def collect(
        self,
        *,
        current_replicas: int,
        evaluated_at: datetime,
        expected_worker_names: tuple[str, ...] | list[str] | None = None,
    ) -> WorkerObservationResult:
        """Collect timestamped worker evidence and fail closed on gaps."""
        if not _aware_datetime(evaluated_at):
            raise ValueError("evaluated_at must be timezone-aware")

        if type(current_replicas) is not int or current_replicas < 1:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics={},
                healthy=False,
                reason="invalid_current_replicas",
            )

        normalized_expected: tuple[str, ...] | None = None

        if expected_worker_names is not None:
            if (
                not isinstance(expected_worker_names, (tuple, list))
                or not expected_worker_names
                or any(
                    not isinstance(name, str) or not name.strip()
                    for name in expected_worker_names
                )
                or len(set(expected_worker_names))
                != len(expected_worker_names)
                or len(expected_worker_names) != current_replicas
            ):
                return self._finish(
                    current_replicas=current_replicas,
                    evaluated_at=evaluated_at,
                    metrics={},
                    healthy=False,
                    reason="invalid_expected_worker_names",
                )

            normalized_expected = tuple(expected_worker_names)

        metric_arguments: dict[str, Any] = {
            "client": self._redis_client,
        }

        if self._celery_app is not None:
            metric_arguments["celery_app"] = self._celery_app

        if self.config.queue_names is not None:
            metric_arguments["queue_names"] = self.config.queue_names

        try:
            raw_metrics = self._metrics_collector(**metric_arguments)
        except Exception as exc:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics={
                    "healthy": False,
                    "reason": "metric_collection_failed",
                    "error_type": type(exc).__name__,
                },
                healthy=False,
                reason="metric_collection_failed",
            )

        if not isinstance(raw_metrics, dict):
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics={},
                healthy=False,
                reason="invalid_metric_collector_result",
            )

        metrics = dict(raw_metrics)

        metric_timestamp = _parse_timestamp(metrics.get("timestamp"))
        if metric_timestamp is None:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_celery_metrics_timestamp",
            )

        metrics_age = (
            evaluated_at.timestamp() - metric_timestamp.timestamp()
        )

        if not math.isfinite(metrics_age) or metrics_age < 0:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="future_celery_telemetry",
                metrics_age_seconds=metrics_age,
            )

        if metrics_age > self.config.max_metrics_age_seconds:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="stale_celery_telemetry",
                metrics_age_seconds=metrics_age,
            )

        if (
            metrics.get("healthy") is not True
            or metrics.get("broker_healthy") is not True
            or metrics.get("workers_healthy") is not True
        ):
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="celery_metrics_unhealthy",
                metrics_age_seconds=metrics_age,
            )

        if metrics.get("queue_depths_healthy") is not True:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="queue_depths_unhealthy",
                metrics_age_seconds=metrics_age,
            )

        queue_errors = metrics.get("queue_depth_errors")
        if not isinstance(queue_errors, dict) or queue_errors:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="queue_depth_errors_present",
                metrics_age_seconds=metrics_age,
            )

        worker_count = metrics.get("worker_count")
        worker_names = metrics.get("worker_names")

        valid_worker_names = (
            isinstance(worker_names, (list, tuple))
            and all(
                isinstance(name, str) and bool(name.strip())
                for name in worker_names
            )
            and len(set(worker_names)) == len(worker_names)
        )

        if (
            type(worker_count) is not int
            or worker_count < 1
            or not valid_worker_names
            or len(worker_names) != worker_count
            or worker_count != current_replicas
            or (
                normalized_expected is not None
                and set(worker_names) != set(normalized_expected)
            )
        ):
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="worker_coverage_mismatch",
                metrics_age_seconds=metrics_age,
                reported_worker_count=worker_count,
                reported_worker_names=worker_names,
            )

        concurrency = _finite_number(metrics.get("total_concurrency"))
        if concurrency is None or concurrency <= 0:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_worker_concurrency",
                metrics_age_seconds=metrics_age,
            )

        active_tasks = _non_negative_integer(metrics.get("active_tasks"))
        reserved_tasks = _non_negative_integer(metrics.get("reserved_tasks"))

        if active_tasks is None or reserved_tasks is None:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_task_counts",
                metrics_age_seconds=metrics_age,
            )

        queue_depths = metrics.get("queue_depths")
        if not isinstance(queue_depths, dict) or not queue_depths:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_queue_depths",
                metrics_age_seconds=metrics_age,
            )

        if self.config.queue_names is None:
            selected_queues = tuple(queue_depths.keys())
        else:
            selected_queues = self.config.queue_names

        if (
            not selected_queues
            or any(name not in queue_depths for name in selected_queues)
        ):
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="incomplete_queue_depth_coverage",
                metrics_age_seconds=metrics_age,
            )

        validated_depths: list[int] = []
        for queue_name in selected_queues:
            depth = _non_negative_integer(queue_depths[queue_name])
            if depth is None:
                return self._finish(
                    current_replicas=current_replicas,
                    evaluated_at=evaluated_at,
                    metrics=metrics,
                    healthy=False,
                    reason="invalid_queue_depths",
                    metrics_age_seconds=metrics_age,
                    invalid_queue=queue_name,
                )
            validated_depths.append(depth)

        queued_tasks = sum(validated_depths)
        outstanding_tasks = queued_tasks + active_tasks + reserved_tasks

        try:
            capacity = (
                concurrency
                * self.config.target_outstanding_tasks_per_worker_slot
            )
            backlog_ratio = float(outstanding_tasks) / capacity
        except (OverflowError, ZeroDivisionError):
            capacity = float("inf")
            backlog_ratio = float("inf")

        if (
            not math.isfinite(capacity)
            or capacity <= 0
            or not math.isfinite(backlog_ratio)
            or backlog_ratio < 0
        ):
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_worker_load_ratio",
                metrics_age_seconds=metrics_age,
            )

        queue_age_used = False
        queue_age_ratio: float | None = None

        # Queue-age is supplemental evidence, never a substitute for current
        # backlog. A stale sample is ignored instead of used as current pressure.
        if outstanding_tasks > 0:
            aggregate = metrics.get("aggregate_task_metrics")

            if isinstance(aggregate, dict):
                sample_count = _non_negative_integer(
                    aggregate.get("queue_age_samples")
                )
                queue_age_ms = _finite_number(
                    aggregate.get("last_queue_age_ms")
                )
                queue_age_observed_at = _finite_number(
                    aggregate.get("last_queue_age_observed_at")
                )

                if (
                    sample_count is not None
                    and sample_count > 0
                    and queue_age_ms is not None
                    and queue_age_ms >= 0
                    and queue_age_observed_at is not None
                    and queue_age_observed_at > 0
                ):
                    queue_age_sample_age = (
                        evaluated_at.timestamp() - queue_age_observed_at
                    )

                    if (
                        math.isfinite(queue_age_sample_age)
                        and 0 <= queue_age_sample_age
                        <= self.config.max_queue_age_sample_age_seconds
                    ):
                        candidate_ratio = (
                            queue_age_ms / self.config.target_queue_age_ms
                        )

                        if (
                            math.isfinite(candidate_ratio)
                            and candidate_ratio >= 0
                        ):
                            queue_age_ratio = candidate_ratio
                            queue_age_used = True

        load_ratio = backlog_ratio
        if queue_age_used and queue_age_ratio is not None:
            load_ratio = max(backlog_ratio, queue_age_ratio)

        if not math.isfinite(load_ratio) or load_ratio < 0:
            return self._finish(
                current_replicas=current_replicas,
                evaluated_at=evaluated_at,
                metrics=metrics,
                healthy=False,
                reason="invalid_worker_load_ratio",
                metrics_age_seconds=metrics_age,
            )

        return self._finish(
            current_replicas=current_replicas,
            evaluated_at=evaluated_at,
            metrics=metrics,
            healthy=True,
            reason="ok",
            load_ratio=load_ratio,
            metrics_age_seconds=metrics_age,
            worker_count=worker_count,
            total_concurrency=concurrency,
            queued_tasks=queued_tasks,
            active_tasks=active_tasks,
            reserved_tasks=reserved_tasks,
            outstanding_tasks=outstanding_tasks,
            capacity_tasks=capacity,
            backlog_load_ratio=backlog_ratio,
            queue_age_used=queue_age_used,
            queue_age_load_ratio=queue_age_ratio,
        )

    @staticmethod
    def _finish(
        *,
        current_replicas: int,
        evaluated_at: datetime,
        metrics: Mapping[str, Any],
        healthy: bool,
        reason: str,
        load_ratio: float | None = None,
        **details: Any,
    ) -> WorkerObservationResult:
        """Create a consistent observation and retain diagnostic evidence."""
        safe_load_ratio = load_ratio if healthy else None

        observation = ScalingObservation(
            current_replicas=current_replicas,
            load_ratio=safe_load_ratio,
            observed_at=evaluated_at,
            healthy=healthy,
        )

        output_metrics = dict(metrics)
        output_metrics["worker_observation"] = {
            "healthy": healthy,
            "reason": reason,
            "load_ratio": safe_load_ratio,
            **details,
        }

        return WorkerObservationResult(
            observation=observation,
            metrics=output_metrics,
        )