from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.autoscaling.compose_adapter import ScalePreviewStatus
from app.core.autoscaling.decision_engine import ScalingAction, ScalingDecision
from app.core.autoscaling.live_actuator import (
    ComposeLiveScalingActuator,
    DockerExecBackendDrainAdapter,
    DrainOperationResult,
    ScaleActuationStatus,
)


PROJECT = "clinic-system"
IMAGE = "clinic-system-phase9:wave1-local-20261021-r1"


def decision(action=ScalingAction.SCALE_OUT, current=3, target=4):
    return ScalingDecision(
        action, current, target, "test",
        0.95 if action is ScalingAction.SCALE_OUT else 0.1,
    )


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(["docker", "test"], returncode, stdout, "")


def inventory(count, *, bad_index=None, image=IMAGE):
    rows = []
    for i in range(1, count + 1):
        status = "Up 1 minute (healthy)"
        if i == bad_index:
            status = "Up 1 minute (unhealthy)"
        rows.append(f"{PROJECT}-backend-{i}|{image}|running|{status}")
    return "\n".join(rows)


def config_reply(image=IMAGE):
    return completed(json.dumps({"services": {"backend": {"image": image}}}))


class FakePreview:
    def __init__(self, tmp_path, status=ScalePreviewStatus.DRY_RUN_ACCEPTED):
        self.config = SimpleNamespace(
            project_root=Path(tmp_path).resolve(),
            project_name=PROJECT, min_replicas=2, max_replicas=5,
        )
        self.project_name = PROJECT
        self.status = status
        self.calls = []

    def preview(self, decision, *, scale_in_drain_confirmed=False):
        self.calls.append((decision, scale_in_drain_confirmed))
        return SimpleNamespace(
            status=self.status,
            reason="dry_run_validated_no_infrastructure_change",
            observed_running_replicas=decision.current_replicas,
            exit_code=0,
        )


class FakeDrain:
    project_name = PROJECT

    def __init__(self, *, confirmed=True, restored=True):
        self.confirmed, self.restored = confirmed, restored
        self.drain_calls, self.undrain_calls = [], []

    def drain_and_confirm(self, name):
        self.drain_calls.append(name)
        return DrainOperationResult(
            self.confirmed, self.restored, "test", 0 if self.confirmed else 1,
        )

    def undrain(self, name):
        self.undrain_calls.append(name)
        return self.restored


class ScriptedRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if not self.replies:
            raise AssertionError("Unexpected subprocess call")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def make_actuator(tmp_path, runner, *, enabled=True, drainer=None, preview_status=ScalePreviewStatus.DRY_RUN_ACCEPTED):
    preview = FakePreview(tmp_path, preview_status)
    actuator = ComposeLiveScalingActuator(
        preview, live_enabled=enabled, drain_adapter=drainer, runner=runner,
    )
    return actuator, preview


def test_live_actuation_is_disabled_by_default_without_commands(tmp_path):
    runner = ScriptedRunner([])
    preview = FakePreview(tmp_path)
    actuator = ComposeLiveScalingActuator(preview)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "live_actuation_disabled"
    assert runner.calls == [] and preview.calls == []


def test_scale_out_applies_only_after_target_is_healthy(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4)),
    ])
    actuator, preview = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.succeeded and result.may_acknowledge_decision
    assert result.observed_replicas == 4
    command = runner.calls[4][0]
    assert command[-3:] == ["scale", "--no-deps", "backend=4"]
    assert preview.calls[0][1] is False


def test_command_error_is_reconciled_against_actual_state(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(4)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.reason == "target_state_verified_after_command_error"


def test_previous_state_after_command_failure_is_known_failure(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.FAILED
    assert result.outcome_known and result.may_acknowledge_decision


def test_unhealthy_target_remains_uncertain(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4, bad_index=4)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert not result.outcome_known and not result.may_acknowledge_decision


def test_scale_in_requires_drain_adapter(tmp_path):
    runner = ScriptedRunner([])
    actuator, preview = make_actuator(tmp_path, runner)

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "scale_in_drain_unavailable"
    assert not runner.calls and not preview.calls


def test_scale_in_drains_then_previews_and_applies(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(2)),
    ])
    actuator, preview = make_actuator(tmp_path, runner, drainer=drainer)

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.observed_replicas == 2
    assert drainer.drain_calls == ["clinic-system-backend-3"]
    assert drainer.undrain_calls == []
    assert preview.calls[0][1] is True


def test_failed_scale_in_restores_drain_on_verified_old_state(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner, drainer=drainer)

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.FAILED
    assert drainer.undrain_calls == ["clinic-system-backend-3"]


def test_unconfirmed_drain_does_not_scale(tmp_path):
    drainer = FakeDrain(confirmed=False, restored=None)
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner, drainer=drainer)

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert not result.may_acknowledge_decision
    assert len(runner.calls) == 2


def test_preview_refusal_never_executes_scale_command(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, preview_status=ScalePreviewStatus.REFUSED
    )

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert not any("scale" in call[0] for call in runner.calls)


def test_mismatched_drain_project_is_rejected(tmp_path):
    preview = FakePreview(tmp_path)
    drainer = FakeDrain()
    drainer.project_name = "clinic-system-phase9"

    with pytest.raises(ValueError, match="same project"):
        ComposeLiveScalingActuator(
            preview, live_enabled=True, drain_adapter=drainer,
        )


def test_drain_adapter_rejects_another_project_without_docker():
    runner = ScriptedRunner([])
    drainer = DockerExecBackendDrainAdapter(project_name=PROJECT, runner=runner)

    result = drainer.drain_and_confirm("clinic-system-phase9-backend-3")

    assert not result.confirmed
    assert result.reason == "invalid_backend_name"
    assert runner.calls == []


def test_drain_token_is_not_passed_in_process_arguments():
    payload = {
        "ok": True, "reason": "drain_confirmed", "draining": True,
        "active_http_requests": 0, "active_socket_connections": 0,
        "ready_for_retirement": True,
    }
    runner = ScriptedRunner([completed(json.dumps(payload))])
    drainer = DockerExecBackendDrainAdapter(project_name=PROJECT, runner=runner)

    result = drainer.drain_and_confirm("clinic-system-backend-3")

    assert result.confirmed
    command = runner.calls[0][0]
    assert command[:4] == ["docker", "exec", "clinic-system-backend-3", "python"]
    assert "PHASE9_BACKEND_DRAIN_TOKEN" in command[5]
    assert "Authorization: Bearer " not in " ".join(command)