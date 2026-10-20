import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling.compose_adapter import (
    COMPOSE_FILES,
    ENV_FILE_NAME,
    EXPECTED_PROJECT_NAME,
    ScalePreviewStatus,
)
from app.core.autoscaling.coordinator import (
    AutoscalingCoordinator,
    AutoscalingCycleStatus,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
    ScalingAction,
)
from app.core.autoscaling.worker_compose_adapter import (
    WorkerComposeScalingAdapter,
    WorkerComposeScalingAdapterConfig,
)
from app.core.autoscaling.worker_observation import (
    WorkerObservationAdapter,
    WorkerObservationConfig,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
TEST_WORKER_IMAGE = "clinic-system-phase9:test-worker-integration"
TEST_CONFIG_HASH = "a" * 64


class ScriptedComposeRunner:
    """Return deterministic fixtures; never invoke Docker."""

    def __init__(self):
        self.calls = []
        self.config_payload = json.dumps(
            {
                "services": {
                    "worker": {
                        "image": TEST_WORKER_IMAGE,
                        "command": [
                            "celery",
                            "-A",
                            "app.celery_app",
                            "worker",
                        ],
                        "ports": [],
                    }
                }
            }
        )
        self.scale_output = "\n".join(
            (
                f"Container {EXPECTED_PROJECT_NAME}-worker-1 Running",
                f"Container {EXPECTED_PROJECT_NAME}-worker-2 Running",
                f"Container {EXPECTED_PROJECT_NAME}-worker-3 Creating",
                f"Container {EXPECTED_PROJECT_NAME}-worker-3 Created",
            )
        )

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)

        if command[:2] == ["docker", "ps"]:
            output = "\n".join(
                (
                    f"{EXPECTED_PROJECT_NAME}-worker-1|running|Up 1 hour|"
                    f"{TEST_WORKER_IMAGE}|{TEST_CONFIG_HASH}",
                    f"{EXPECTED_PROJECT_NAME}-worker-2|running|Up 1 hour|"
                    f"{TEST_WORKER_IMAGE}|{TEST_CONFIG_HASH}",
                )
            )
        elif "config" in command and "--hash" in command:
            output = f"worker {TEST_CONFIG_HASH}\n"
        elif "config" in command:
            output = self.config_payload
        elif "--scale" in command:
            if "--dry-run" not in command:
                raise AssertionError("A non-dry-run command was attempted.")
            output = self.scale_output
        else:
            raise AssertionError(f"Unexpected command: {command!r}")

        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=output,
            stderr="",
        )


def test_worker_telemetry_to_coordinator_to_compose_preview(tmp_path):
    for filename in (ENV_FILE_NAME, *COMPOSE_FILES):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test-only placeholder", encoding="utf-8")

    metric_clock = {"timestamp": START.isoformat()}
    redis_client = object()
    celery_names = ["celery@worker-1", "celery@worker-2"]

    def collect_metrics(**kwargs):
        assert kwargs["client"] is redis_client
        return {
            "timestamp": metric_clock["timestamp"],
            "healthy": True,
            "broker_healthy": True,
            "workers_healthy": True,
            "queue_depths_healthy": True,
            "queue_depth_errors": {},
            "worker_count": 2,
            "worker_names": celery_names,
            "total_concurrency": 4,
            "active_tasks": 1,
            "reserved_tasks": 1,
            "queue_depths": {"default": 6},
            "aggregate_task_metrics": {},
        }

    observation_adapter = WorkerObservationAdapter(
        redis_client=redis_client,
        config=WorkerObservationConfig(
            target_outstanding_tasks_per_worker_slot=2,
            target_queue_age_ms=2000,
            max_metrics_age_seconds=20,
            max_queue_age_sample_age_seconds=20,
            queue_names=("default",),
        ),
        metrics_collector=collect_metrics,
    )

    engine = AutoscalingDecisionEngine(
        AutoscalingPolicy(
            min_replicas=2,
            max_replicas=4,
            scale_out_load_ratio=0.8,
            scale_in_load_ratio=0.2,
            scale_out_window_seconds=10,
            scale_in_window_seconds=10,
            cooldown_seconds=30,
            max_metrics_age_seconds=60,
            max_observation_gap_seconds=30,
        )
    )

    runner = ScriptedComposeRunner()
    worker_adapter = WorkerComposeScalingAdapter(
        WorkerComposeScalingAdapterConfig(
            project_root=tmp_path,
            min_replicas=2,
            max_replicas=4,
        ),
        runner=runner,
    )
    coordinator = AutoscalingCoordinator(engine, worker_adapter)

    # Eight outstanding tasks / (four concurrency slots * target two) = 1.0.
    first_observation = observation_adapter.collect(
        current_replicas=2,
        evaluated_at=START,
        expected_worker_names=celery_names,
    )
    assert first_observation.observation.healthy is True
    assert first_observation.observation.load_ratio == pytest.approx(1.0)

    first_cycle = coordinator.run_cycle(
        first_observation.observation,
        evaluated_at=START,
    )
    assert first_cycle.status is AutoscalingCycleStatus.HOLD
    assert first_cycle.reason == "scale_out_window_not_met"
    assert runner.calls == []

    # A second fresh sample establishes sustained demand across the window.
    second_at = START + timedelta(seconds=10)
    metric_clock["timestamp"] = second_at.isoformat()

    second_observation = observation_adapter.collect(
        current_replicas=2,
        evaluated_at=second_at,
        expected_worker_names=celery_names,
    )
    assert second_observation.observation.healthy is True
    assert second_observation.observation.load_ratio == pytest.approx(1.0)

    result = coordinator.run_cycle(
        second_observation.observation,
        evaluated_at=second_at,
    )

    assert result.status is AutoscalingCycleStatus.PREVIEW_ACCEPTED
    assert result.ok is True
    assert result.decision is not None
    assert result.decision.action is ScalingAction.SCALE_OUT
    assert result.decision.current_replicas == 2
    assert result.decision.target_replicas == 3
    assert result.preview is not None
    assert result.preview.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.preview.observed_running_replicas == 2

    # The preview is a recommendation only; no scale result was acknowledged.
    assert engine.pending_target_replicas == 3

    scale_commands = [
        command for command in runner.calls if "--scale" in command
    ]
    assert len(scale_commands) == 1
    assert "--dry-run" in scale_commands[0]
    assert scale_commands[0][-5:] == [
        "--no-deps",
        "--no-recreate",
        "--scale",
        "worker=3",
        "worker",
    ]