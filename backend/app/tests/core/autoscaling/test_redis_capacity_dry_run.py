from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling.redis_capacity_dry_run import (
    RedisCapacityDryRun,
    RedisCapacityDryRunConfig,
)
from app.core.autoscaling.redis_capacity_observation import (
    RedisCapacityObservationConfig,
)
from app.core.autoscaling.redis_capacity_policy import (
    RedisCapacityAction,
    RedisCapacityPolicyConfig,
)
from app.core.autoscaling.redis_capacity_preview import (
    RedisCapacityPreviewStatus,
    RedisCapacityPreviewConfig,
)
from app.core.autoscaling.redis_docker_runtime_collector import (
    DockerRedisRuntimeCollectorConfig,
)
from app.core.autoscaling.redis_runtime_evaluation import (
    RedisRuntimeEvaluationConfig,
    RedisRuntimeEvaluationStatus,
)


NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
CONTAINER = "clinic-system-phase9-redis-1"
PROJECT = "clinic-system-phase9"
IMAGE = "redis:8"

MEMINFO = (
    "MemTotal:        3967600 kB\n"
    "MemFree:          141028 kB\n"
    "MemAvailable:    1717932 kB\n"
    "SwapTotal:             0 kB\n"
    "SwapFree:              0 kB\n"
)
PERSISTENCE = (
    "# Persistence\n"
    "loading:0\n"
    "aof_enabled:1\n"
    "aof_last_write_status:ok\n"
)


class FakeDockerRunner:
    def __init__(self, **overrides):
        self.calls = []
        self.overrides = dict(overrides)

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, dict(kwargs)))

        key = self._classify(command)
        if key is None:
            return subprocess.CompletedProcess(command, 97, "", "private error")

        defaults = {
            "daemon_info": "4062822400\n",
            "inspect_identity": f"/{CONTAINER}|{IMAGE}\n",
            "inspect_labels": f"{PROJECT}|redis\n",
            "inspect_state": "running|true|false|0|healthy\n",
            "inspect_memory_limit": "0\n",
            "inspect_data_mount": "volume|true\n",
            "meminfo": MEMINFO,
            "cgroup_current": "68886528\n",
            "cgroup_limit": "max\n",
            "persistence": PERSISTENCE,
        }
        code, output = self.overrides.get(key, (0, defaults[key]))
        return subprocess.CompletedProcess(
            command, code, output, "private diagnostic"
        )

    @staticmethod
    def _classify(command):
        if len(command) >= 2 and command[1] == "info":
            return "daemon_info"

        if len(command) >= 2 and command[1] == "inspect":
            template = command[command.index("--format") + 1]
            if template == "{{.Name}}|{{.Config.Image}}":
                return "inspect_identity"
            if "com.docker.compose.project" in template:
                return "inspect_labels"
            if "RestartCount" in template:
                return "inspect_state"
            if template == "{{.HostConfig.Memory}}":
                return "inspect_memory_limit"
            if "/data" in template:
                return "inspect_data_mount"
            return None

        if len(command) >= 4 and command[1] == "exec":
            if command[3] == "cat":
                return {
                    "/proc/meminfo": "meminfo",
                    "/sys/fs/cgroup/memory.current": "cgroup_current",
                    "/sys/fs/cgroup/memory.max": "cgroup_limit",
                }.get(command[4])
            if command[3:6] == ["redis-cli", "--raw", "INFO"]:
                if command[6:] == ["persistence"]:
                    return "persistence"
            return None

        return None


def metrics(*, sampled_at=None, **overrides):
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


def config(**overrides):
    values = {
        "docker": DockerRedisRuntimeCollectorConfig(
            container_name=CONTAINER,
            compose_project=PROJECT,
            compose_service="redis",
            expected_image=IMAGE,
            command_timeout_seconds=5,
        ),
        "observation": RedisCapacityObservationConfig(
            max_metrics_age_seconds=20,
        ),
        "policy": RedisCapacityPolicyConfig(
            warning_usage_ratio=0.70,
            critical_usage_ratio=0.85,
        ),
        "runtime": RedisRuntimeEvaluationConfig(
            expected_container_name=CONTAINER,
            max_runtime_metrics_age_seconds=20,
        ),
        # Synthetic values for tests; not recommendations for deployment.
        "preview": RedisCapacityPreviewConfig(
            proposed_redis_maxmemory_bytes=256_000_000,
            proposed_container_memory_limit_bytes=384_000_000,
            redis_overhead_headroom_bytes=128_000_000,
            minimum_linux_available_reserve_bytes=1_000_000_000,
            minimum_docker_daemon_reserve_bytes=1_000_000_000,
        ),
    }
    values.update(overrides)
    return RedisCapacityDryRunConfig(**values)


def run_dry_run(
    *,
    metric_snapshot=None,
    metrics_collector=None,
    docker_runner=None,
    config_override=None,
    clock=lambda: NOW,
):
    client = object()
    source = metrics() if metric_snapshot is None else metric_snapshot
    runner = FakeDockerRunner() if docker_runner is None else docker_runner

    if metrics_collector is None:
        def metrics_collector(**kwargs):
            assert kwargs == {"client": client}
            return source

    dry_run = RedisCapacityDryRun(
        redis_client=client,
        config=config() if config_override is None else config_override,
        metrics_collector=metrics_collector,
        docker_runner=runner,
        clock=clock,
    )
    return dry_run.evaluate(), runner


def test_full_dry_run_accepts_valid_runtime_evidence_without_actuation():
    result, runner = run_dry_run()

    assert result.status is RedisRuntimeEvaluationStatus.PREVIEW_ACCEPTED
    assert result.preview.status is RedisCapacityPreviewStatus.ACCEPTED
    assert result.observation.decision.action is RedisCapacityAction.REVIEW_UNBOUNDED_MEMORY
    assert result.requires_operator_review is True
    assert result.actuation_allowed is False
    assert result.preview.actuation_allowed is False
    assert runner.calls
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert all(call[1]["timeout"] == 5.0 for call in runner.calls)
    assert all(call[0][1] in {"info", "inspect", "exec"} for call in runner.calls)


def test_unhealthy_redis_metrics_prevent_docker_collection():
    calls = []
    runner = FakeDockerRunner()

    def collector(**kwargs):
        return metrics(
            memory_capacity_metrics_healthy=False,
            memory_capacity_metrics_reason="redis_capacity_config_unavailable",
        )

    result, _ = run_dry_run(
        metrics_collector=collector,
        docker_runner=runner,
    )

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_capacity_observation_unhealthy"
    assert runner.calls == []
    assert result.actuation_allowed is False


def test_docker_identity_mismatch_refuses_dry_run():
    runner = FakeDockerRunner(
        inspect_identity=(0, "/wrong-container|redis:8")
    )
    result, _ = run_dry_run(docker_runner=runner)

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_container_identity_mismatch"
    assert result.actuation_allowed is False


def test_runtime_snapshot_unhealthy_refuses_dry_run():
    runner = FakeDockerRunner(
        inspect_state=(0, "running|true|false|0|starting")
    )
    result, _ = run_dry_run(docker_runner=runner)

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "runtime_container_unhealthy"


def test_insufficient_resource_budget_refuses_preview():
    original = config()
    restrictive = RedisCapacityDryRunConfig(
        docker=original.docker,
        observation=original.observation,
        policy=original.policy,
        runtime=original.runtime,
        preview=RedisCapacityPreviewConfig(
            proposed_redis_maxmemory_bytes=256_000_000,
            proposed_container_memory_limit_bytes=384_000_000,
            redis_overhead_headroom_bytes=128_000_000,
            minimum_linux_available_reserve_bytes=1_600_000_000,
            minimum_docker_daemon_reserve_bytes=1_000_000_000,
        ),
    )
    result, _ = run_dry_run(config_override=restrictive)

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "linux_available_memory_budget_exceeded"
    assert result.preview.status is RedisCapacityPreviewStatus.REFUSED


def test_collector_exception_is_refused_without_leaking_details():
    def broken_collector(**kwargs):
        raise TimeoutError("private Redis connection information")

    result, _ = run_dry_run(metrics_collector=broken_collector)

    assert result.status is RedisRuntimeEvaluationStatus.REFUSED
    assert result.reason == "redis_capacity_observation_unhealthy"
    assert "private Redis connection information" not in repr(result)


def test_invalid_dependency_types_are_rejected():
    with pytest.raises(ValueError):
        RedisCapacityDryRun(
            redis_client=None,
            config=config(),
        )

    with pytest.raises(TypeError):
        RedisCapacityDryRun(
            redis_client=object(),
            config=None,
        )

    with pytest.raises(TypeError):
        RedisCapacityDryRun(
            redis_client=object(),
            config=config(),
            metrics_collector=None,
        )

    with pytest.raises(TypeError):
        RedisCapacityDryRun(
            redis_client=object(),
            config=config(),
            docker_runner=None,
        )

    with pytest.raises(TypeError):
        RedisCapacityDryRun(
            redis_client=object(),
            config=config(),
            clock=42,
        )


def test_configuration_rejects_mismatched_container_names():
    base = config()

    with pytest.raises(ValueError):
        RedisCapacityDryRunConfig(
            docker=base.docker,
            observation=base.observation,
            policy=base.policy,
            runtime=RedisRuntimeEvaluationConfig(
                expected_container_name="another-redis",
                max_runtime_metrics_age_seconds=20,
            ),
            preview=base.preview,
        )


def test_result_does_not_expose_mutation_method():
    result, _ = run_dry_run()

    assert result.actuation_allowed is False
    assert not hasattr(result, "apply")
    assert not hasattr(result, "scale_command")