import json
import subprocess
from pathlib import Path

TEST_CONFIG_HASH = "a" * 64
TEST_WORKER_IMAGE = "clinic-system-phase9:986ec27"

import pytest

from app.core.autoscaling.compose_adapter import (
    COMPOSE_FILES,
    ENV_FILE_NAME,
    EXPECTED_PROJECT_NAME,
    ScalePreviewStatus,
)
from app.core.autoscaling.decision_engine import (
    ScalingAction,
    ScalingDecision,
)
from app.core.autoscaling.worker_compose_adapter import (
    WorkerComposeScalingAdapter,
    WorkerComposeScalingAdapterConfig,
)


def _decision(action=ScalingAction.SCALE_OUT, current=2, target=3, ratio=0.9):
    return ScalingDecision(
        action=action,
        current_replicas=current,
        target_replicas=target,
        reason="test_decision",
        load_ratio=ratio,
    )


def _worker_config(overrides=None):
    worker = {
        "image": TEST_WORKER_IMAGE,
        "container_name": None,
        "command": [
            "celery", "-A", "celery_worker.celery",
            "worker", "--loglevel=INFO",
        ],
        "ports": [],
    }
    worker.update(overrides or {})
    return {"services": {"backend": {}, "worker": worker}}


def _worker_rows(names=None, states=None, statuses=None, images=None, hashes=None):
    if names is None:
        names = [
            f"{EXPECTED_PROJECT_NAME}-worker-1",
            f"{EXPECTED_PROJECT_NAME}-worker-2",
        ]
    if states is None:
        states = ["running"] * len(names)
    if statuses is None:
        statuses = ["Up 1 hour (healthy)"] * len(names)
    if images is None:
        images = [TEST_WORKER_IMAGE] * len(names)
    if hashes is None:
        hashes = [TEST_CONFIG_HASH] * len(names)

    if not all(
        len(values) == len(names)
        for values in (states, statuses, images, hashes)
    ):
        raise ValueError("worker fixture lengths must match")

    return "\n".join(
        f"{name}|{state}|{status}|{image}|{config_hash}"
        for name, state, status, image, config_hash
        in zip(names, states, statuses, images, hashes)
    )

class ScriptedRunner:
    def __init__(
        self,
        *,
        config_payload=None,
        ps_output=None,
        scale_output=None,
        config_code=0,
        ps_code=0,
        scale_code=0,
        exceptions=None,
    ):
        self.config_payload = (
            json.dumps(_worker_config())
            if config_payload is None
            else config_payload
        )
        self.ps_output = _worker_rows() if ps_output is None else ps_output
        self.scale_output = (
            f"Container {EXPECTED_PROJECT_NAME}-worker-1 Running\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-2 Running\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-3 Creating\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-3 Created"
            if scale_output is None
            else scale_output
        )
        self.codes = {
            "config": config_code,
            "hash": 0,
            "ps": ps_code,
            "scale": scale_code,
        }
        self.exceptions = exceptions or {}
        self.calls = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, kwargs))

        if command[:2] == ["docker", "ps"]:
            kind, stdout = "ps", self.ps_output
        elif "config" in command and "--hash" in command:
            kind, stdout = "hash", f"worker {TEST_CONFIG_HASH}\n"
        elif "config" in command:
            kind, stdout = "config", self.config_payload
        elif "--scale" in command:
            kind, stdout = "scale", self.scale_output
        else:
            raise AssertionError(f"Unexpected command: {command}")

        if kind in self.exceptions:
            raise self.exceptions[kind]

        return subprocess.CompletedProcess(
            args=command,
            returncode=self.codes[kind],
            stdout=stdout,
            stderr="",
        )


def _make_adapter(tmp_path, runner=None, **kwargs):
    for filename in (ENV_FILE_NAME, *COMPOSE_FILES):
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test-only placeholder", encoding="utf-8")

    config = WorkerComposeScalingAdapterConfig(
        project_root=tmp_path,
        min_replicas=kwargs.get("min_replicas", 1),
        max_replicas=kwargs.get("max_replicas", 4),
        command_timeout_seconds=kwargs.get("command_timeout_seconds", 20.0),
    )
    runner = runner or ScriptedRunner()
    return WorkerComposeScalingAdapter(config, runner=runner), runner


def test_scale_out_previews_worker_only(tmp_path):
    adapter, runner = _make_adapter(tmp_path)
    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.reason == "dry_run_validated_no_infrastructure_change"
    assert result.observed_running_replicas == 2

    commands = [command for command, _ in runner.calls if "--scale" in command]
    assert len(commands) == 1
    assert commands[0][-5:] == [
        "--no-deps", "--no-recreate", "--scale", "worker=3", "worker",
    ]
    assert commands[0][2] == "--dry-run"
    assert "backend=3" not in commands[0]


def test_live_scale_command_is_never_issued(tmp_path):
    adapter, runner = _make_adapter(tmp_path)
    result = adapter.preview(_decision())

    assert result.ok is True

    commands = [command for command, _ in runner.calls if "--scale" in command]
    assert len(commands) == 1
    assert "--dry-run" in commands[0]
    assert "--no-deps" in commands[0]


def test_plan_touching_backend_is_refused(tmp_path):
    runner = ScriptedRunner(
        scale_output=f"Container {EXPECTED_PROJECT_NAME}-backend-3 Started"
    )
    adapter, _ = _make_adapter(tmp_path, runner)

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "compose_scale_plan_unsafe"


def test_plan_recreating_surviving_worker_is_refused(tmp_path):
    runner = ScriptedRunner(
        scale_output=(
            f"Container {EXPECTED_PROJECT_NAME}-worker-1 Recreated\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-3 Started"
        )
    )
    adapter, _ = _make_adapter(tmp_path, runner)

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "compose_scale_plan_unsafe"


def test_empty_plan_is_refused(tmp_path):
    adapter, _ = _make_adapter(
        tmp_path, ScriptedRunner(scale_output="")
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "compose_scale_plan_unsafe"


def test_plan_changing_network_is_refused(tmp_path):
    runner = ScriptedRunner(
        scale_output=(
            f"Network {EXPECTED_PROJECT_NAME}_default Created\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-3 Started"
        )
    )
    adapter, _ = _make_adapter(tmp_path, runner)

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "compose_scale_plan_unsafe"


def test_scale_in_requires_drain_confirmation_before_commands(tmp_path):
    adapter, runner = _make_adapter(tmp_path)

    result = adapter.preview(
        _decision(ScalingAction.SCALE_IN, 2, 1, 0.1)
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "scale_in_drain_not_confirmed"
    assert runner.calls == []


def test_scale_in_dry_run_targets_worker_service(tmp_path):
    runner = ScriptedRunner(
        scale_output=(
            f"Container {EXPECTED_PROJECT_NAME}-worker-1 Running\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-2 Stopping\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-2 Stopped\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-2 Removing\n"
            f"Container {EXPECTED_PROJECT_NAME}-worker-2 Removed"
        )
    )
    adapter, runner = _make_adapter(tmp_path, runner)

    result = adapter.preview(
        _decision(ScalingAction.SCALE_IN, 2, 1, 0.1),
        scale_in_drain_confirmed=True,
    )

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    command = [c for c, _ in runner.calls if "--scale" in c][0]
    assert command[-2:] == ["worker=1", "worker"]
    assert "--no-recreate" in command


def test_hold_runs_no_external_commands(tmp_path):
    adapter, runner = _make_adapter(tmp_path)

    result = adapter.preview(
        _decision(ScalingAction.HOLD, 2, 2, 0.5)
    )

    assert result.status is ScalePreviewStatus.NOOP
    assert runner.calls == []


def test_invalid_decision_runs_no_external_commands(tmp_path):
    adapter, runner = _make_adapter(tmp_path)

    result = adapter.preview(object())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "invalid_decision_type"
    assert runner.calls == []


def test_missing_compose_input_refuses_before_commands(tmp_path):
    adapter, runner = _make_adapter(tmp_path)
    (tmp_path / ENV_FILE_NAME).unlink()

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason.startswith("required_file_missing:")
    assert runner.calls == []


def test_compose_config_failure_stops_before_inspection(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path, ScriptedRunner(config_code=1)
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "compose_config_failed"
    assert len(runner.calls) == 1


def test_invalid_compose_json_fails_closed(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path, ScriptedRunner(config_payload="not-json")
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "compose_config_invalid_json"
    assert len(runner.calls) == 1


def test_missing_worker_service_is_refused(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(config_payload=json.dumps({"services": {"backend": {}}})),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_service_missing"
    assert len(runner.calls) == 1


def test_fixed_container_name_is_refused(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            config_payload=json.dumps(
                _worker_config({"container_name": "fixed-worker"})
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_has_fixed_container_name"
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "command",
    [
        ["celery", "-A", "celery_worker.celery", "beat"],
        ["python", "-m", "celery", "worker"],
        [],
        "celery worker",
    ],
)
def test_unconfirmed_worker_command_is_refused(tmp_path, command):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            config_payload=json.dumps(_worker_config({"command": command}))
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason in {
        "worker_command_configuration_invalid",
        "worker_command_not_confirmed",
    }
    assert len(runner.calls) == 1


def test_worker_host_ports_are_refused(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            config_payload=json.dumps(
                _worker_config({"ports": [{"target": 8000}]})
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_host_ports_not_allowed"
    assert len(runner.calls) == 1


def test_worker_image_mismatch_refuses_before_dry_run(tmp_path):
    runner = ScriptedRunner(
        config_payload=json.dumps(
            _worker_config({"image": "clinic-system-phase9:cd420ab"})
        )
    )
    adapter, runner = _make_adapter(tmp_path, runner)

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_image_mismatch"
    assert not any("--scale" in command for command, _ in runner.calls)


def test_worker_config_hash_mismatch_refuses_before_dry_run(tmp_path):
    runner = ScriptedRunner(
        ps_output=_worker_rows(hashes=["b" * 64, "b" * 64]),
    )
    adapter, runner = _make_adapter(tmp_path, runner)

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_config_hash_mismatch"
    assert not any("--scale" in command for command, _ in runner.calls)
def test_running_count_mismatch_prevents_scale_preview(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            ps_output=_worker_rows(
                names=[f"{EXPECTED_PROJECT_NAME}-worker-1"]
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "running_replica_count_mismatch"
    assert not any("--scale" in command for command, _ in runner.calls)


def test_nonrunning_or_unhealthy_worker_prevents_scale_preview(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            ps_output=_worker_rows(
                statuses=["Up 1 hour (healthy)", "Up 1 minute (unhealthy)"]
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_not_running"
    assert not any("--scale" in command for command, _ in runner.calls)


def test_noncontiguous_worker_names_fail_closed(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path,
        ScriptedRunner(
            ps_output=_worker_rows(
                names=[
                    f"{EXPECTED_PROJECT_NAME}-worker-1",
                    f"{EXPECTED_PROJECT_NAME}-worker-3",
                ]
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "worker_replica_identity_mismatch"
    assert not any("--scale" in command for command, _ in runner.calls)


def test_malformed_container_inspection_fails_closed(tmp_path):
    adapter, runner = _make_adapter(
        tmp_path, ScriptedRunner(ps_output="malformed-row")
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "docker_container_inspection_malformed"
    assert not any("--scale" in command for command, _ in runner.calls)


def test_nonzero_dry_run_exit_is_failure(tmp_path):
    adapter, _ = _make_adapter(
        tmp_path, ScriptedRunner(scale_code=1)
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "compose_worker_dry_run_failed"


def test_timeout_fails_closed(tmp_path):
    adapter, _ = _make_adapter(
        tmp_path,
        ScriptedRunner(
            exceptions={
                "scale": subprocess.TimeoutExpired("docker compose", 20)
            }
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "command_timeout"


def test_os_error_fails_closed(tmp_path):
    adapter, _ = _make_adapter(
        tmp_path,
        ScriptedRunner(exceptions={"scale": OSError("private error")}),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "command_execution_failed"


@pytest.mark.parametrize(
    "decision",
    [
        _decision(current=0, target=1),
        _decision(current=2, target=4),
        _decision(current=2, target=3, ratio=float("nan")),
        _decision(current=2, target=3, ratio=float("inf")),
        _decision(current=2, target=3, ratio=True),
    ],
)
def test_invalid_decision_inputs_are_refused_before_commands(tmp_path, decision):
    adapter, runner = _make_adapter(tmp_path)

    result = adapter.preview(decision)

    assert result.status is ScalePreviewStatus.REFUSED
    assert runner.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_replicas": 0},
        {"min_replicas": 3, "max_replicas": 2},
        {"max_replicas": True},
        {"command_timeout_seconds": 0},
        {"command_timeout_seconds": float("nan")},
        {"command_timeout_seconds": float("inf")},
        {"command_timeout_seconds": True},
    ],
)
def test_invalid_adapter_config_is_rejected(tmp_path, kwargs):
    values = {
        "project_root": tmp_path,
        "min_replicas": 1,
        "max_replicas": 4,
        "command_timeout_seconds": 20.0,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        WorkerComposeScalingAdapterConfig(**values)


def test_result_does_not_expose_raw_compose_output(tmp_path):
    marker = "simulated-private-compose-output"
    adapter, _ = _make_adapter(
        tmp_path,
        ScriptedRunner(
            scale_output=(
                f"{marker}\n"
                f"Container {EXPECTED_PROJECT_NAME}-worker-1 Running\n"
                f"Container {EXPECTED_PROJECT_NAME}-worker-2 Running\n"
                f"Container {EXPECTED_PROJECT_NAME}-worker-3 Creating\n"
                f"Container {EXPECTED_PROJECT_NAME}-worker-3 Created"
            )
        ),
    )

    result = adapter.preview(_decision())

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert not hasattr(result, "stdout")
    assert not hasattr(result, "stderr")
    assert marker not in repr(result)