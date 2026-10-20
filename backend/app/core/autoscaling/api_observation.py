"""Convert validated shared API request telemetry into a scaling observation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from app.core.autoscaling.decision_engine import ScalingObservation
from app.core.observability.api_request_metrics import (
    API_REQUEST_METRICS_BUCKET_SECONDS,
    API_REQUEST_METRICS_RETENTION_SECONDS,
    collect_api_request_metrics,
)


@dataclass(frozen=True, slots=True)
class APIObservationConfig:
    """Explicit per-replica capacity and metrics freshness policy."""

    target_requests_per_second_per_replica: float
    window_seconds: int = 30
    bucket_seconds: int = API_REQUEST_METRICS_BUCKET_SECONDS
    max_sample_age_seconds: int = 20

    def __post_init__(self) -> None:
        target = self.target_requests_per_second_per_replica
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            raise ValueError(
                "target_requests_per_second_per_replica must be finite and > 0"
            )

        try:
            target_float = float(target)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(
                "target_requests_per_second_per_replica must be finite and > 0"
            ) from None

        if not math.isfinite(target_float) or target_float <= 0:
            raise ValueError(
                "target_requests_per_second_per_replica must be finite and > 0"
            )

        if (
            type(self.window_seconds) is not int
            or type(self.bucket_seconds) is not int
            or type(self.max_sample_age_seconds) is not int
            or self.window_seconds <= 0
            or self.bucket_seconds <= 0
            or self.max_sample_age_seconds <= 0
            or self.window_seconds < self.bucket_seconds
            or self.window_seconds % self.bucket_seconds != 0
            or self.max_sample_age_seconds > self.window_seconds
            or self.window_seconds + self.bucket_seconds
            > API_REQUEST_METRICS_RETENTION_SECONDS
        ):
            raise ValueError("Invalid API observation window configuration")


@dataclass(frozen=True, slots=True)
class APIObservationResult:
    """Observation plus safe aggregate telemetry used to produce it."""

    observation: ScalingObservation
    metrics: Mapping[str, Any]


class APIObservationAdapter:
    """Build fleet-level observations using a shared Redis request window.

    The load ratio is total fleet requests/second divided by the configured
    per-replica target multiplied by the observed replica count. This ratio is
    meaningful only when the target has been calibrated for the deployment.
    """

    def __init__(
        self,
        *,
        redis_client,
        config: APIObservationConfig,
        metrics_collector: Callable[..., dict[str, Any]] = (
            collect_api_request_metrics
        ),
    ) -> None:
        if not isinstance(config, APIObservationConfig):
            raise TypeError("config must be an APIObservationConfig")
        if not callable(metrics_collector):
            raise TypeError("metrics_collector must be callable")

        self._redis_client = redis_client
        self.config = config
        self._metrics_collector = metrics_collector

    def collect(
        self,
        *,
        current_replicas: int,
        evaluated_at: datetime,
        expected_instance_ids: tuple[str, ...] | list[str] | None = None,
    ) -> APIObservationResult:
        """Collect metrics and produce a fail-closed ScalingObservation."""
        if (
            not isinstance(evaluated_at, datetime)
            or evaluated_at.tzinfo is None
            or evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")

        try:
            metric_arguments = {
                "client": self._redis_client,
                "now": evaluated_at.timestamp(),
                "window_seconds": self.config.window_seconds,
                "bucket_seconds": self.config.bucket_seconds,
                "max_sample_age_seconds": (
                    self.config.max_sample_age_seconds
                ),
            }
            if expected_instance_ids is not None:
                metric_arguments["expected_instance_ids"] = tuple(
                    expected_instance_ids
                )

            metrics = self._metrics_collector(**metric_arguments)
        except Exception as exc:
            metrics = {
                "available": False,
                "healthy": False,
                "reason": "metric_collection_failed",
                "error_type": type(exc).__name__,
            }

        if not isinstance(metrics, dict):
            metrics = {
                "available": False,
                "healthy": False,
                "reason": "invalid_metric_collector_result",
            }

        rate = metrics.get("requests_per_second")
        valid_rate = (
            not isinstance(rate, bool)
            and isinstance(rate, (int, float))
            and math.isfinite(float(rate))
            and rate >= 0
        )
        valid_replicas = (
            type(current_replicas) is int
            and current_replicas >= 1
        )
        healthy = (
            metrics.get("available") is True
            and metrics.get("healthy") is True
            and valid_rate
            and valid_replicas
        )

        load_ratio = None
        if healthy:
            load_ratio = float(rate) / (
                float(self.config.target_requests_per_second_per_replica)
                * current_replicas
            )
            if not math.isfinite(load_ratio) or load_ratio < 0:
                load_ratio = None
                healthy = False

        observation = ScalingObservation(
            current_replicas=current_replicas,
            load_ratio=load_ratio,
            observed_at=evaluated_at,
            healthy=healthy,
        )

        return APIObservationResult(
            observation=observation,
            metrics=dict(metrics),
        )
