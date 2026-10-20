from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.autoscaling.api_controller import (
    APIBackendAutoscalingController,
    ComposeBackendReplicaReader,
)
from app.core.autoscaling.api_observation import (
    APIObservationAdapter,
    APIObservationConfig,
)
from app.core.autoscaling.compose_adapter import (
    ScalePreviewResult,
    ScalePreviewStatus,
)
from app.core.autoscaling.coordinator import (
    AutoscalingCoordinator,
    AutoscalingCycleStatus,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def output_for(count=2, *, bad_index=None):
    rows = []
    for index in range(1, count + 1):
        status = "Up 5 hours (healthy)"
        if bad_index == index:
            status = "Up 5 hours (unhealthy)"
        rows.append(
            f"{index:012x}|clinic-system-phase9-backend-{index}|running|{status}"
        )
    return "\n".join(rows)


class StaticRunner:
    def __init__(self, stdout="", returncode=0, error=None):
        self.stdout = stdout
        self.returncode = returncode
        self.error = error
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            stdout=self.stdout,
            stderr="",
            returncode=self.returncode,
        )


def make_replica_reader(tmp_path, *, stdout=None, returncode=0, error=None):
    runner = StaticRunner(
        stdout=output_for() if stdout is None else stdout,
        returncode=returncode,
        error=error,
    )
    reader = ComposeBackendReplicaReader(
        project_root=Path(tmp_path),
        runner=runner,
    )
    return reader, runner


def test_reader_counts_only_confirmed_healthy_backend_replicas(tmp_path):
    reader, runner = make_replica_reader(tmp_path)

    snapshot = reader.read()

    assert snapshot.current_replicas == 2
    assert snapshot.healthy is True
    assert snapshot.reason == "ok"
    assert snapshot.backend_names == (
        "clinic-system-phase9-backend-1",
        "clinic-system-phase9-backend-2",
    )
    assert snapshot.backend_instance_ids == (
        "000000000001",
        "000000000002",
    )

    command, kwargs = runner.calls[0]
    assert command[:2] == ["docker", "ps"]
    assert "label=com.docker.compose.project=clinic-system-phase9" in command
    assert "label=com.docker.compose.service=backend" in command
    assert kwargs["cwd"] == str(Path(tmp_path).resolve())
    assert kwargs["check"] is False


def test_reader_refuses_unhealthy_backend(tmp_path):
    reader, _ = make_replica_reader(
        tmp_path,
        stdout=output_for(bad_index=2),
    )

    snapshot = reader.read()

    assert snapshot.current_replicas == 2
    assert snapshot.healthy is False
    assert snapshot.reason == "backend_health_not_confirmed"


def test_reader_fails_closed_when_no_backend_is_running(tmp_path):
    reader, _ = make_replica_reader(tmp_path, stdout="")

    snapshot = reader.read()

    assert snapshot.current_replicas == 0
    assert snapshot.healthy is False
    assert snapshot.reason == "no_running_backends"


@pytest.mark.parametrize(
    "stdout",
    [
        "unexpected-output",
        "another-project-backend-1|running|Up 5 hours (healthy)",
        (
            "000000000001|clinic-system-phase9-backend-1|running|Up 5 hours (healthy)\n"
            "000000000003|clinic-system-phase9-backend-3|running|Up 5 hours (healthy)"
        ),
    ],
)
def test_reader_refuses_malformed_or_ambiguous_container_state(
    tmp_path,
    stdout,
):
    reader, _ = make_replica_reader(tmp_path, stdout=stdout)

    snapshot = reader.read()

    assert snapshot.healthy is False
    assert snapshot.reason in {
        "backend_replica_snapshot_invalid",
        "backend_replica_identity_invalid",
    }


def test_reader_refuses_docker_errors_and_timeouts(tmp_path):
    failed_reader, _ = make_replica_reader(
        tmp_path,
        stdout="",
        returncode=1,
    )
    failed = failed_reader.read()
    assert failed.healthy is False
    assert failed.reason == "backend_replica_discovery_failed"

    timeout_reader, _ = make_replica_reader(
        tmp_path,
        error=__import__("subprocess").TimeoutExpired(["docker", "ps"], 5),
    )
    timed_out = timeout_reader.read()
    assert timed_out.healthy is False
    assert timed_out.reason == "backend_replica_discovery_timeout"


class PreviewSpy:
    def __init__(self):
        self.calls = []

    def preview(self, decision, *, scale_in_drain_confirmed=False):
        self.calls.append((decision, scale_in_drain_confirmed))
        return ScalePreviewResult(
            status=ScalePreviewStatus.DRY_RUN_ACCEPTED,
            reason="dry_run_validated_no_infrastructure_change",
            current_replicas=decision.current_replicas,
            target_replicas=decision.target_replicas,
            observed_running_replicas=decision.current_replicas,
            exit_code=0,
            command=("docker", "compose", "scale", "--dry-run"),
        )


def make_controller(tmp_path, *, bad_backend=False):
    reader, runner = make_replica_reader(
        tmp_path,
        stdout=(
            output_for(bad_index=2)
            if bad_backend
            else output_for()
        ),
    )

    def metrics_collector(**kwargs):
        return {
            "available": True,
            "healthy": True,
            "reason": "ok",
            "request_count": 60,
            "requests_per_second": 2.0,
            "errors_5xx": 0,
            "error_5xx_rate": 0.0,
        }

    observation_adapter = APIObservationAdapter(
        redis_client=object(),
        config=APIObservationConfig(
            target_requests_per_second_per_replica=1.0,
        ),
        metrics_collector=metrics_collector,
    )
    engine = AutoscalingDecisionEngine(
        AutoscalingPolicy(
            min_replicas=2,
            max_replicas=5,
            scale_out_load_ratio=0.8,
            scale_in_load_ratio=0.2,
            scale_out_window_seconds=10,
            scale_in_window_seconds=10,
            cooldown_seconds=30,
            max_metrics_age_seconds=60,
            max_observation_gap_seconds=30,
        )
    )
    preview = PreviewSpy()
    coordinator = AutoscalingCoordinator(engine, preview)
    controller = APIBackendAutoscalingController(
        replica_reader=reader,
        observation_adapter=observation_adapter,
        coordinator=coordinator,
    )
    return controller, engine, preview, runner


def test_controller_wires_shared_demand_and_running_count_to_preview(tmp_path):
    controller, engine, preview, runner = make_controller(tmp_path)

    first = controller.run_cycle(evaluated_at=START)
    assert first.status is AutoscalingCycleStatus.HOLD
    assert first.reason == "scale_out_window_not_met"
    assert first.replicas.current_replicas == 2
    assert first.metrics["backend_replica_healthy"] is True
    assert preview.calls == []

    second_at = START + timedelta(seconds=10)
    second = controller.run_cycle(evaluated_at=second_at)

    assert second.status is AutoscalingCycleStatus.PREVIEW_ACCEPTED
    assert second.replicas.current_replicas == 2
    assert second.cycle.decision is not None
    assert second.cycle.decision.current_replicas == 2
    assert second.cycle.decision.target_replicas == 3
    assert engine.pending_target_replicas == 3
    assert len(preview.calls) == 1
    assert len(runner.calls) == 2


def test_controller_does_not_preview_when_any_backend_is_unhealthy(tmp_path):
    controller, _, preview, _ = make_controller(
        tmp_path,
        bad_backend=True,
    )

    result = controller.run_cycle(evaluated_at=START)

    assert result.replicas.healthy is False
    assert result.reason == "backend_health_not_confirmed"
    assert result.status is AutoscalingCycleStatus.HOLD
    assert result.cycle.decision is not None
    assert result.cycle.decision.reason == "metrics_unhealthy"
    assert preview.calls == []


def test_reader_rejects_another_project_name(tmp_path):
    with pytest.raises(ValueError, match="expected deployment"):
        ComposeBackendReplicaReader(
            project_root=Path(tmp_path),
            project_name="unrelated-project",
        )
