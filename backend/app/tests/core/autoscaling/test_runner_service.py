from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.autoscaling.controller_lease import (
    LeaseGuardedAcknowledgement,
    LeaseGuardedActuator,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
    ScalingAction,
    ScalingObservation,
)
from app.core.autoscaling.live_actuator import (
    ScaleActuationResult,
    ScaleActuationStatus,
)
from app.core.autoscaling.runner_service import build_runtime
from app.core.observability.api_request_metrics import API_REQUEST_METRICS_BUCKET_SECONDS


class FakeRedis:
    def __init__(self):
        self.closed = False
        self.values = {}

    def set(self, key, value, *, nx=False, px=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def eval(self, script, numkeys, key, token, *args):
        if self.values.get(key) != token:
            return 0
        if "pexpire" in script:
            return 1
        self.values.pop(key, None)
        return 1

    def close(self):
        self.closed = True


@pytest.fixture
def runtime_env(tmp_path):
    for name in (".env.phase6", "compose.yaml", "compose.phase6.yaml", "compose.phase9.yaml"):
        (tmp_path / name).write_text("test placeholder\n", encoding="utf-8")
    route_file = tmp_path / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    route_file.parent.mkdir(parents=True, exist_ok=True)
    route_file.write_text("".join(
        f"    server clinic-system-backend-{i}:5000 resolve max_fails=2 fail_timeout=10s;\n"
        for i in range(1, 4)
    ), encoding="utf-8")
    return {
        "PHASE9_AUTOSCALING_ENABLED": "true",
        "PHASE9_AUTOSCALING_LIVE_ENABLED": "true",
        "PHASE9_AUTOSCALING_SCALE_IN_ENABLED": "false",
        "PHASE9_AUTOSCALING_PROJECT_NAME": "clinic-system",
        "PHASE9_AUTOSCALING_PROJECT_ROOT": str(tmp_path),
        "PHASE9_AUTOSCALING_REDIS_URL": "redis://127.0.0.1:16379/0",
        "PHASE9_AUTOSCALING_MIN_REPLICAS": "2",
        "PHASE9_AUTOSCALING_MAX_REPLICAS": "4",
        "PHASE9_AUTOSCALING_TARGET_REQUESTS_PER_SECOND_PER_REPLICA": "12.5",
        "PHASE9_AUTOSCALING_SCALE_OUT_LOAD_RATIO": "0.85",
        "PHASE9_AUTOSCALING_SCALE_IN_LOAD_RATIO": "0.2",
        "PHASE9_AUTOSCALING_SCALE_OUT_WINDOW_SECONDS": "60",
        "PHASE9_AUTOSCALING_SCALE_IN_WINDOW_SECONDS": "300",
        "PHASE9_AUTOSCALING_COOLDOWN_SECONDS": "120",
        "PHASE9_AUTOSCALING_MAX_METRICS_AGE_SECONDS": "20",
        "PHASE9_AUTOSCALING_MAX_OBSERVATION_GAP_SECONDS": "20",
        "PHASE9_AUTOSCALING_LOOP_INTERVAL_SECONDS": "10",
        "PHASE9_AUTOSCALING_METRICS_WINDOW_SECONDS": "30",
        "PHASE9_AUTOSCALING_METRICS_MAX_SAMPLE_AGE_SECONDS": "20",
    }


def test_runner_disabled_by_default_without_redis_or_docker_calls():
    calls = []
    result = build_runtime({}, redis_client_factory=lambda *a, **k: calls.append((a, k)))
    assert result is None
    assert calls == []


def test_enabled_runtime_wires_one_project_and_one_lease(runtime_env):
    fake_redis = FakeRedis()
    calls = []

    runtime = build_runtime(
        runtime_env,
        redis_client_factory=lambda *args, **kwargs: calls.append((args, kwargs)) or fake_redis,
    )
    try:
        assert runtime is not None
        assert len(calls) == 1
        assert runtime.project_name == "clinic-system"
        assert runtime.replica_reader.project_name == runtime.project_name
        assert runtime.coordinator.project_name == runtime.project_name
        assert runtime.runner.enabled is True
        assert runtime.runner.loop.enabled is True
        assert runtime.runner.loop._actuator.lease is runtime.lease
        assert runtime.runner.loop._acknowledge.lease is runtime.lease
        assert isinstance(runtime.runner.loop._actuator, LeaseGuardedActuator)
        assert isinstance(runtime.runner.loop._acknowledge, LeaseGuardedAcknowledgement)
        assert runtime.runner.loop._controller is runtime.controller
        assert runtime.runner.loop._actuator._actuator is runtime.actuator
        assert runtime.actuator.live_enabled is True
        assert runtime.actuator.scale_in_enabled is False
        assert runtime.actuator.routing_adapter is not None
        assert runtime.actuator.routing_adapter.project_name == "clinic-system"
        assert runtime.actuator.before_scale_command == runtime.lease.mark_scale_command_started
        assert runtime.runner.loop._controller._observation_adapter.config.bucket_seconds == API_REQUEST_METRICS_BUCKET_SECONDS
        assert runtime.runner.loop._controller._coordinator._decision_engine.policy.allow_scale_in is False
        assert runtime.min_replicas == 2 and runtime.max_replicas == 4
    finally:
        runtime.close()
    assert fake_redis.closed is True


def test_live_enabled_is_required_when_runner_enabled(runtime_env):
    runtime_env["PHASE9_AUTOSCALING_LIVE_ENABLED"] = "false"
    with pytest.raises(RuntimeError, match="live actuation is not explicitly enabled"):
        build_runtime(runtime_env, redis_client_factory=lambda *a, **k: FakeRedis())


def test_scale_in_requires_live_route_verification_gate(runtime_env):
    runtime_env["PHASE9_AUTOSCALING_SCALE_IN_ENABLED"] = "true"
    runtime_env.pop("PHASE9_AUTOSCALING_SCALE_IN_LIVE_ROUTE_VERIFIED", None)
    with pytest.raises(RuntimeError, match="LIVE_ROUTE_VERIFIED=true"):
        build_runtime(runtime_env, redis_client_factory=lambda *a, **k: FakeRedis())


def test_scale_in_requires_strong_drain_token(runtime_env):
    from pathlib import Path

    runtime_env["PHASE9_AUTOSCALING_SCALE_IN_ENABLED"] = "true"
    runtime_env["PHASE9_AUTOSCALING_SCALE_IN_LIVE_ROUTE_VERIFIED"] = "true"
    env_file = Path(runtime_env["PHASE9_AUTOSCALING_PROJECT_ROOT"]) / ".env.phase6"
    existing = env_file.read_text(encoding="utf-8")
    existing = "\n".join(
        line for line in existing.splitlines()
        if not line.lstrip().startswith("PHASE9_BACKEND_DRAIN_TOKEN=")
    )
    env_file.write_text(existing + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="strong PHASE9_BACKEND_DRAIN_TOKEN"):
        build_runtime(runtime_env, redis_client_factory=lambda *a, **k: FakeRedis())


def test_scale_in_enabled_wires_both_adapters(runtime_env):
    from pathlib import Path
    from app.core.autoscaling.live_actuator import DockerExecBackendDrainAdapter
    from app.core.autoscaling.nginx_routing import NginxBackendRoutingAdapter

    root = Path(runtime_env["PHASE9_AUTOSCALING_PROJECT_ROOT"])
    env_file = root / ".env.phase6"
    original = env_file.read_text(encoding="utf-8")
    original = "\n".join(
        line for line in original.splitlines()
        if not line.lstrip().startswith("PHASE9_BACKEND_DRAIN_TOKEN=")
    )
    env_file.write_text(
        original.rstrip() + "\nPHASE9_BACKEND_DRAIN_TOKEN=" + "x" * 64 + "\n",
        encoding="utf-8",
    )

    route_file = root / "runtime" / "phase9" / "nginx-upstream" / "backend.conf"
    route_file.parent.mkdir(parents=True, exist_ok=True)
    route_file.write_text("".join(
        f"    server clinic-system-backend-{i}:5000 resolve max_fails=2 fail_timeout=10s;\n"
        for i in range(1, 5)
    ), encoding="utf-8")

    runtime_env["PHASE9_AUTOSCALING_SCALE_IN_ENABLED"] = "true"
    runtime_env["PHASE9_AUTOSCALING_SCALE_IN_LIVE_ROUTE_VERIFIED"] = "true"
    runtime = build_runtime(
        runtime_env, redis_client_factory=lambda *a, **k: FakeRedis()
    )
    assert runtime is not None
    try:
        assert runtime.actuator.scale_in_enabled is True
        assert isinstance(runtime.actuator.drain_adapter, DockerExecBackendDrainAdapter)
        assert isinstance(runtime.actuator.routing_adapter, NginxBackendRoutingAdapter)
        assert runtime.runner.loop._controller._coordinator._decision_engine.policy.allow_scale_in is True
    finally:
        runtime.close()


def test_missing_calibration_setting_fails_before_redis_creation(runtime_env):
    runtime_env.pop("PHASE9_AUTOSCALING_TARGET_REQUESTS_PER_SECOND_PER_REPLICA")
    calls = []
    with pytest.raises(ValueError, match="TARGET_REQUESTS_PER_SECOND_PER_REPLICA"):
        build_runtime(runtime_env, redis_client_factory=lambda *a, **k: calls.append(True))
    assert calls == []


def test_scale_in_policy_disabled_never_recommends_scale_in():
    engine = AutoscalingDecisionEngine(
        AutoscalingPolicy(
            min_replicas=2,
            max_replicas=4,
            scale_out_load_ratio=0.85,
            scale_in_load_ratio=0.2,
            scale_out_window_seconds=30,
            scale_in_window_seconds=30,
            cooldown_seconds=0,
            max_metrics_age_seconds=30,
            max_observation_gap_seconds=30,
            allow_scale_in=False,
        )
    )
    from datetime import datetime, timedelta, timezone

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    result = None
    for offset in (0, 10, 20, 30, 40):
        at = start + timedelta(seconds=offset)
        result = engine.evaluate(
            ScalingObservation(3, 0.0, at, healthy=True),
            evaluated_at=at,
        )
    assert result is not None
    assert result.action is ScalingAction.HOLD
    assert result.reason == "scale_in_disabled"
    assert engine.pending_target_replicas is None


def test_refused_actuation_is_a_known_non_mutation_result():
    result = ScaleActuationResult(
        ScaleActuationStatus.REFUSED,
        "pre_action_state_not_safe",
        3,
        4,
        observed_replicas=4,
    )
    assert result.outcome_known is True
    assert result.succeeded is False
    assert result.may_acknowledge_decision is True


def test_phase9_host_redis_binding_is_loopback_only():
    root = Path(__file__).resolve().parents[5]
    source = (root / "compose.phase9.yaml").read_text(encoding="utf-8")
    assert '"127.0.0.1:16379:6379"' in source
    assert '"0.0.0.0:16379:6379"' not in source
    assert '"16379:6379"' not in source