from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from app.core.autoscaling.compose_adapter import (
    ComposeScalingAdapter,
    ComposeScalingAdapterConfig,
    ScalePreviewStatus,
)
from app.core.autoscaling.decision_engine import (
    ScalingAction,
    ScalingDecision,
)


HEALTHY_TWO = (
    "clinic-system-phase9-backend-1|running|Up 5 hours (healthy)|clinic-system-phase9:cd420ab\r\n"
    "clinic-system-phase9-backend-2|running|Up 2 hours (healthy)|clinic-system-phase9:cd420ab\r\n"
)
HEALTHY_THREE = HEALTHY_TWO + (
    "clinic-system-phase9-backend-3|running|Up 1 minute (healthy)|clinic-system-phase9:cd420ab\r\n"
)


def resolved_config(backend_overrides=None):
    backend = {
        "image": "clinic-system-phase9:cd420ab",
        "healthcheck": {"test": ["CMD", "check-ready"]},
        "ports": [],
        "stop_grace_period": "40s",
    }
    backend.update(backend_overrides or {})
    return json.dumps({"services": {"backend": backend, "worker": {}}})


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if not self.responses:
            raise AssertionError("Unexpected extra subprocess call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def completed(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )


def make_project(tmp_path, *, project_name="clinic-system-phase9"):
    for name in (
        ".env.phase6",
        "compose.yaml",
        "compose.phase6.yaml",
        "compose.phase9.yaml",
    ):
        (tmp_path / name).write_text("# fixture only\n", encoding="utf-8")

    return ComposeScalingAdapterConfig(
        project_root=tmp_path,
        min_replicas=2,
        max_replicas=5,
        project_name=project_name,
    )


def scale_decision(
    action=ScalingAction.SCALE_OUT,
    current=2,
    target=3,
    ratio=0.9,
):
    return ScalingDecision(
        action=action,
        current_replicas=current,
        target_replicas=target,
        reason="test_decision",
        load_ratio=ratio,
    )


def successful_responses(*, running=HEALTHY_TWO, dry_run_output=None):
    if dry_run_output is None:
        dry_run_output = "Container clinic-system-phase9-backend-3 Started"

    return [
        completed(resolved_config()),
        completed(running),
        completed(dry_run_output),
    ]


def test_scale_out_runs_compose_dry_run_for_backend_only(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(successful_responses())
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(scale_decision())

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.ok is True
    assert result.reason == "dry_run_validated_no_infrastructure_change"
    assert result.current_replicas == 2
    assert result.target_replicas == 3
    assert result.observed_running_replicas == 2
    assert len(runner.calls) == 3

    command, kwargs = runner.calls[2]
    assert command[:2] == ["docker", "compose"]
    assert "--project-name" in command
    assert "clinic-system-phase9" in command
    assert command[-9:] == [
        "--dry-run",
        "up",
        "-d",
        "--no-deps",
        "--no-recreate",
        "--no-build",
        "--scale",
        "backend=3",
        "backend",
    ]
    assert "worker=3" not in command
    assert "redis=3" not in command
    assert kwargs["cwd"] == str(tmp_path.resolve())
    assert kwargs["capture_output"] is True
    assert kwargs["check"] is False


def test_explicit_active_project_is_used_for_discovery_and_preview(tmp_path):
    config = make_project(tmp_path, project_name="clinic-system")
    image = "clinic-system-phase9:wave1-local-20261021-r1"
    running = (
        "clinic-system-backend-1|running|Up 5 hours (healthy)|"
        "clinic-system-phase9:wave1-local-20261021-r1\r\n"
        "clinic-system-backend-2|running|Up 2 hours (healthy)|"
        "clinic-system-phase9:wave1-local-20261021-r1\r\n"
    )
    runner = FakeRunner(
        [
            completed(resolved_config({"image": image})),
            completed(running),
            completed("Container clinic-system-backend-3 Started"),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    config_command = runner.calls[0][0]
    assert config_command[config_command.index("--project-name") + 1] == "clinic-system"
    inspect_command = runner.calls[1][0]
    assert "label=com.docker.compose.project=clinic-system" in inspect_command
    scale_command = runner.calls[2][0]
    assert scale_command[scale_command.index("--project-name") + 1] == "clinic-system"
    assert scale_command[-9:] == [
        "--dry-run", "up", "-d", "--no-deps",
        "--no-recreate", "--no-build", "--scale",
        "backend=3", "backend"
    ]


def test_resolved_image_mismatch_refuses_preview(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        [
            completed(
                resolved_config(
                    {"image": "clinic-system-phase9:wave1-local-20261021-r1"}
                )
            ),
            completed(HEALTHY_TWO),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "backend_image_mismatch"
    assert result.observed_running_replicas == 2
    assert len(runner.calls) == 2


def test_scale_in_preview_targets_one_fewer_backend(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        successful_responses(
            running=HEALTHY_THREE,
            dry_run_output="Container clinic-system-phase9-backend-3 Removed",
        )
    )
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(
        scale_decision(
            action=ScalingAction.SCALE_IN,
            current=3,
            target=2,
            ratio=0.2,
        ),
        scale_in_drain_confirmed=True,
    )

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.observed_running_replicas == 3
    assert runner.calls[2][0][-9:] == [
        "--dry-run", "up", "-d", "--no-deps",
        "--no-recreate", "--no-build", "--scale",
        "backend=2", "backend"
    ]
    assert "--dry-run" in runner.calls[2][0]


def test_scale_in_preview_requires_drain_confirmation(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner([])
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(
        scale_decision(
            action=ScalingAction.SCALE_IN,
            current=3,
            target=2,
            ratio=0.2,
        )
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "scale_in_drain_not_confirmed"
    assert runner.calls == []

def test_hold_decision_does_not_run_external_commands(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner([])
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(
        scale_decision(
            action=ScalingAction.HOLD,
            current=2,
            target=2,
            ratio=0.5,
        )
    )

    assert result.status is ScalePreviewStatus.NOOP
    assert result.reason == "hold_decision_no_command_needed"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("decision", "reason"),
    [
        (
            scale_decision(current=2, target=4),
            "target_must_change_by_exactly_one_replica",
        ),
        (
            scale_decision(current=2, target=6),
            "replicas_out_of_configured_bounds",
        ),
        (
            scale_decision(current=2, target=1),
            "replicas_out_of_configured_bounds",
        ),
        (
            scale_decision(ratio=float("nan")),
            "invalid_decision_load_ratio",
        ),
        (
            scale_decision(ratio=float("inf")),
            "invalid_decision_load_ratio",
        ),
        (
            scale_decision(ratio=-1),
            "invalid_decision_load_ratio",
        ),
        (
            scale_decision(ratio=None),
            "invalid_decision_load_ratio",
        ),
    ],
)
def test_invalid_decision_is_refused_before_subprocess(
    tmp_path, decision, reason
):
    config = make_project(tmp_path)
    runner = FakeRunner([])
    result = ComposeScalingAdapter(config, runner=runner).preview(decision)

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == reason
    assert runner.calls == []


def test_missing_env_file_is_refused_without_running_docker(tmp_path):
    config = make_project(tmp_path)
    (tmp_path / ".env.phase6").unlink()
    runner = FakeRunner([])

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason.startswith("required_file_missing:")
    assert runner.calls == []


def test_compose_config_failure_prevents_container_inspection(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        [completed(returncode=1, stderr="sensitive config output")]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "compose_config_failed"
    assert "sensitive config output" not in repr(result)
    assert len(runner.calls) == 1


def test_invalid_compose_json_is_not_passed_to_scale_command(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner([completed("not-json")])

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "compose_config_invalid_json"
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"container_name": "fixed-backend"}, "backend_has_fixed_container_name"),
        (
            {
                "healthcheck": {
                    "test": ["CMD", "check-ready"],
                    "disable": True,
                }
            },
            "backend_healthcheck_required",
        ),
        ({"healthcheck": None}, "backend_healthcheck_required"),
        ({"ports": [{"target": 5000, "published": "5000"}]},
         "backend_host_port_mapping_not_allowed"),
        ({"ports": "invalid"}, "backend_ports_configuration_invalid"),
        ({"stop_grace_period": "10s"}, "backend_shutdown_budget_not_safe"),
        ({"command": ["gunicorn", "wsgi:app"]}, "backend_shutdown_budget_not_safe"),
    ],
)
def test_unsafe_backend_configuration_is_refused(
    tmp_path, overrides, expected_reason
):
    config = make_project(tmp_path)
    runner = FakeRunner([completed(resolved_config(overrides))])

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == expected_reason
    assert len(runner.calls) == 1


def test_running_replica_count_mismatch_prevents_scale_preview(tmp_path):
    config = make_project(tmp_path)
    only_one_backend = "clinic-system-phase9-backend-1|running|Up 5 hours (healthy)|clinic-system-phase9:cd420ab\r\n"
    runner = FakeRunner(
        [completed(resolved_config()), completed(only_one_backend)]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision(current=2, target=3)
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "running_replica_count_mismatch"
    assert result.observed_running_replicas == 1
    assert len(runner.calls) == 2


@pytest.mark.parametrize(
    ("container_row", "expected_reason"),
    [
        (
            "clinic-system-phase9-backend-1|exited|Exited (1)|clinic-system-phase9:cd420ab",
            "backend_not_running",
        ),
        (
            "clinic-system-phase9-backend-1|running|Up 1 minute (unhealthy)|clinic-system-phase9:cd420ab",
            "backend_not_healthy",
        ),
        (
            "clinic-system-phase9-backend-1|running|Up 1 minute (health: starting)|clinic-system-phase9:cd420ab",
            "backend_not_healthy",
        ),
    ],
)
def test_nonhealthy_backend_prevents_scale_preview(
    tmp_path, container_row, expected_reason
):
    config = make_project(tmp_path)
    rows = (
        container_row
        + "\r\n"
        + "clinic-system-phase9-backend-2|running|Up 2 minutes (healthy)|clinic-system-phase9:cd420ab\r\n"
    )
    runner = FakeRunner([completed(resolved_config()), completed(rows)])

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == expected_reason
    assert len(runner.calls) == 2


def test_docker_inspection_failure_prevents_scale_preview(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        [
            completed(resolved_config()),
            completed(returncode=1, stderr="container inspection failed"),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "docker_container_inspection_failed"
    assert len(runner.calls) == 2


def test_scale_in_preview_refuses_plan_that_recreates_surviving_backend(tmp_path):
    config = make_project(tmp_path)
    unsafe_plan = "\n".join(
        [
            "[+] scale 3/3",
            "? Container clinic-system-phase9-backend-1 Recreated",
            "? Container clinic-system-phase9-backend-3 Removed",
        ]
    )
    runner = FakeRunner(
        [
            completed(resolved_config()),
            completed(HEALTHY_THREE),
            completed(unsafe_plan),
        ]
    )
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(
        scale_decision(action=ScalingAction.SCALE_IN, current=3, target=2, ratio=0.2),
        scale_in_drain_confirmed=True,
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.ok is False
    assert result.reason == "compose_scale_plan_unsafe"
    assert len(runner.calls) == 3


def test_scale_out_preview_refuses_plan_that_touches_worker(tmp_path):
    config = make_project(tmp_path)
    unsafe_plan = "\n".join(
        [
            "Container clinic-system-phase9-worker-1 Recreated",
            "Container clinic-system-phase9-backend-3 Started",
        ]
    )
    runner = FakeRunner(
        [
            completed(resolved_config()),
            completed(HEALTHY_TWO),
            completed(unsafe_plan),
        ]
    )
    adapter = ComposeScalingAdapter(config, runner=runner)

    result = adapter.preview(scale_decision())

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.ok is False
    assert result.reason == "compose_scale_plan_unsafe"
    assert len(runner.calls) == 3


def test_command_timeout_fails_closed(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        [subprocess.TimeoutExpired(["docker", "compose", "config"], 20)]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "command_timeout"
    assert len(runner.calls) == 1


def test_subprocess_os_error_fails_closed(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner([OSError("docker unavailable")])

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.status is ScalePreviewStatus.FAILED
    assert result.reason == "command_execution_failed"
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_replicas": 0, "max_replicas": 5},
        {"min_replicas": 3, "max_replicas": 2},
        {"min_replicas": True, "max_replicas": 5},
        {"min_replicas": 2, "max_replicas": False},
        {"min_replicas": 2, "max_replicas": 5, "command_timeout_seconds": 0},
        {"min_replicas": 2, "max_replicas": 5, "command_timeout_seconds": float("nan")},
        {"min_replicas": 2, "max_replicas": 5, "project_name": "../clinic-system"},
    ],
)
def test_invalid_adapter_configuration_is_rejected(tmp_path, kwargs):
    base = {
        "project_root": tmp_path,
        "min_replicas": 2,
        "max_replicas": 5,
        "command_timeout_seconds": 20,
    }
    base.update(kwargs)

    with pytest.raises(ValueError):
        ComposeScalingAdapterConfig(**base)


def test_result_does_not_expose_raw_subprocess_output(tmp_path):
    config = make_project(tmp_path)
    runner = FakeRunner(
        [
            completed(resolved_config()),
            completed(HEALTHY_TWO),
            completed("Container clinic-system-phase9-backend-3 Started"),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision()
    )

    assert result.ok
    assert "private output omitted" not in repr(result)
    assert result.exit_code == 0
def test_scale_out_preview_refuses_image_build_in_plan(tmp_path):
    config = make_project(tmp_path, project_name="clinic-system")
    image = "clinic-system-phase9:wave1-local-20261021-r1"
    running = "\r\n".join(
        [
            f"clinic-system-backend-{replica}|running|Up 1 minute (healthy)|{image}"
            for replica in (1, 2, 3)
        ]
    ) + "\r\n"
    dry_run = "\n".join(
        [
            "Container clinic-system-backend-1 Running",
            "Container clinic-system-backend-2 Running",
            "Container clinic-system-backend-3 Running",
            f"Image {image} Building",
            "Container clinic-system-backend-4 Creating",
            "Container clinic-system-backend-4 Started",
        ]
    )
    runner = FakeRunner(
        [
            completed(resolved_config({"image": image})),
            completed(running),
            completed(dry_run),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision(current=3, target=4, ratio=1.0)
    )

    assert result.status is ScalePreviewStatus.REFUSED
    assert result.reason == "compose_scale_plan_unsafe"

def test_scale_out_preview_allows_unchanged_running_survivors(tmp_path):
    config = make_project(tmp_path, project_name="clinic-system")
    image = "clinic-system-phase9:wave1-local-20261021-r1"
    running = "\r\n".join(
        [
            f"clinic-system-backend-{replica}|running|Up 1 minute (healthy)|{image}"
            for replica in (1, 2, 3)
        ]
    ) + "\r\n"
    dry_run = "\n".join(
        [
            "[+] scale 4/4",
            "? Container clinic-system-backend-1 Running",
            "? Container clinic-system-backend-2 Running",
            "? Container clinic-system-backend-3 Running",
            "? Container clinic-system-backend-4 Started",
        ]
    )

    runner = FakeRunner(
        [
            completed(resolved_config({"image": image})),
            completed(running),
            completed(dry_run),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision(current=3, target=4, ratio=1.0)
    )

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.ok is True
    assert result.observed_running_replicas == 3
    assert runner.calls[2][0][-9:] == [
        "--dry-run", "up", "-d", "--no-deps",
        "--no-recreate", "--no-build", "--scale",
        "backend=4", "backend"
    ]


def test_scale_in_preview_allows_unchanged_running_survivors(tmp_path):
    config = make_project(tmp_path)
    dry_run = "\n".join(
        [
            "[+] scale 3/3",
            "? Container clinic-system-phase9-backend-1 Running",
            "? Container clinic-system-phase9-backend-2 Running",
            "? Container clinic-system-phase9-backend-3 Removed",
        ]
    )

    runner = FakeRunner(
        [
            completed(resolved_config()),
            completed(HEALTHY_THREE),
            completed(dry_run),
        ]
    )

    result = ComposeScalingAdapter(config, runner=runner).preview(
        scale_decision(
            action=ScalingAction.SCALE_IN,
            current=3,
            target=2,
            ratio=0.2,
        ),
        scale_in_drain_confirmed=True,
    )

    assert result.status is ScalePreviewStatus.DRY_RUN_ACCEPTED
    assert result.ok is True
    assert result.observed_running_replicas == 3
