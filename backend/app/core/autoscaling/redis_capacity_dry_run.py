"""Reusable, read-only Redis capacity dry-run entry point."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.autoscaling.redis_capacity_observation import (
    RedisCapacityObservationConfig,
    RedisCapacityObservationAdapter,
)
from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityPolicyConfig,
)
from app.core.autoscaling.redis_capacity_preview import (
    RedisCapacityPreviewConfig,
)
from app.core.autoscaling.redis_docker_runtime_collector import (
    DockerRedisRuntimeCollectorConfig,
    DockerRedisRuntimeSnapshotCollector,
)
from app.core.autoscaling.redis_runtime_evaluation import (
    RedisRuntimeCapacityEvaluator,
    RedisRuntimeEvaluationConfig,
    RedisRuntimeEvaluationResult,
)
from app.core.observability.redis_metrics import collect_redis_metrics


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class RedisCapacityDryRunConfig:
    """All inputs required for an explicitly sized capacity dry run."""

    docker: DockerRedisRuntimeCollectorConfig
    observation: RedisCapacityObservationConfig
    policy: RedisCapacityPolicyConfig
    runtime: RedisRuntimeEvaluationConfig
    preview: RedisCapacityPreviewConfig

    def __post_init__(self) -> None:
        expected_types = (
            ("docker", self.docker, DockerRedisRuntimeCollectorConfig),
            ("observation", self.observation, RedisCapacityObservationConfig),
            ("policy", self.policy, RedisCapacityPolicyConfig),
            ("runtime", self.runtime, RedisRuntimeEvaluationConfig),
            ("preview", self.preview, RedisCapacityPreviewConfig),
        )

        for name, value, expected_type in expected_types:
            if not isinstance(value, expected_type):
                raise TypeError(f"{name} must be a {expected_type.__name__}")

        if self.docker.container_name != self.runtime.expected_container_name:
            raise ValueError(
                "Docker and runtime evaluation container names must match"
            )


class RedisCapacityDryRun:
    """Join real or injected Redis/Docker collectors without an actuator.

    A successful result means the explicitly configured proposal passed the
    observation, identity, freshness, persistence, and resource-budget checks.
    It never means a Redis setting or container limit was applied.
    """

    def __init__(
        self,
        *,
        redis_client,
        config: RedisCapacityDryRunConfig,
        metrics_collector: Callable[..., dict[str, Any]] = (
            collect_redis_metrics
        ),
        docker_runner: Callable[..., Any] = subprocess.run,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if redis_client is None:
            raise ValueError("redis_client must be provided")
        if not isinstance(config, RedisCapacityDryRunConfig):
            raise TypeError("config must be a RedisCapacityDryRunConfig")
        if not callable(metrics_collector):
            raise TypeError("metrics_collector must be callable")
        if not callable(docker_runner):
            raise TypeError("docker_runner must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        effective_clock = _utc_now if clock is None else clock

        observation_adapter = RedisCapacityObservationAdapter(
            redis_client=redis_client,
            config=config.observation,
            policy=config.policy,
            metrics_collector=metrics_collector,
            clock=effective_clock,
        )

        runtime_collector = DockerRedisRuntimeSnapshotCollector(
            config=config.docker,
            runner=docker_runner,
            clock=effective_clock,
        )

        self.config = config
        self._evaluator = RedisRuntimeCapacityEvaluator(
            observation_adapter=observation_adapter,
            runtime_snapshot_collector=runtime_collector,
            config=config.runtime,
            preview_config=config.preview,
            clock=effective_clock,
        )

    def evaluate(self) -> RedisRuntimeEvaluationResult:
        """Run one bounded observation/preview cycle; never apply changes."""
        return self._evaluator.evaluate()