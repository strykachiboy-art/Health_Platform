"""Worker-only, dry-run Compose scaling adapter."""

from __future__ import annotations

import json
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from app.core.autoscaling.compose_adapter import (
    COMPOSE_FILES,
    ENV_FILE_NAME,
    EXPECTED_PROJECT_NAME,
    ScalePreviewResult,
    ScalePreviewStatus,
)
from app.core.autoscaling.decision_engine import (
    ScalingAction,
    ScalingDecision,
)


def _worker_scale_dry_run_plan_is_safe(
    output: str,
    decision: ScalingDecision,
) -> bool:
    """Accept only the intended worker change with no retained-worker churn."""
    if not isinstance(output, str) or not output.strip():
        return False

    current = decision.current_replicas
    target = decision.target_replicas

    if type(current) is not int or type(target) is not int:
        return False

    if decision.action is ScalingAction.SCALE_OUT:
        if target != current + 1:
            return False
        retained = set(range(1, current + 1))
        changed_replica = target
        allowed = {"creating", "created", "starting", "started"}
        completion = {"created", "started"}
    elif decision.action is ScalingAction.SCALE_IN:
        if target != current - 1:
            return False
        retained = set(range(1, target + 1))
        changed_replica = current
        allowed = {"stopping", "stopped", "removing", "removed"}
        completion = {"removed"}
    else:
        return False

    prefix = f"{EXPECTED_PROJECT_NAME}-worker-".lower()
    resource_change_actions = {
        "building", "built", "creating", "created", "recreating",
        "recreated", "pulling", "pulled", "removing", "removed",
        "starting", "started", "stopping", "stopped",
    }

    seen_running: set[int] = set()
    saw_change = False
    saw_completion = False

    for line in output.splitlines():
        lowered = line.lower()
        marker = lowered.find("container ")

        if marker >= 0:
            fields = line[marker + len("container "):].split()
            if len(fields) < 2:
                return False

            name = fields[0].strip(" ,:;").lower()
            action = fields[1].strip(" ,:;").lower()

            if not name.startswith(prefix):
                return False

            suffix = name[len(prefix):]
            if not suffix.isdigit() or int(suffix) < 1:
                return False

            replica = int(suffix)

            if replica in retained:
                if action != "running":
                    return False
                seen_running.add(replica)
                continue

            if replica != changed_replica or action not in allowed:
                return False

            saw_change = True
            if action in completion:
                saw_completion = True
            continue

        for resource in ("network ", "volume ", "image "):
            resource_marker = lowered.find(resource)
            if resource_marker < 0:
                continue

            fields = lowered[resource_marker + len(resource):].split()
            if len(fields) >= 2:
                action = fields[1].strip(" ,:;").lower()
                if action in resource_change_actions:
                    return False

    return (
        seen_running == retained
        and saw_change
        and saw_completion
    )

@dataclass(frozen=True, slots=True)
class WorkerComposeScalingAdapterConfig:
    """Explicit Compose project, replica bounds, and timeout."""

    project_root: Path
    min_replicas: int
    max_replicas: int
    command_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        root = Path(self.project_root).resolve()

        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")

        object.__setattr__(self, "project_root", root)

        if type(self.min_replicas) is not int or self.min_replicas < 1:
            raise ValueError("min_replicas must be an integer >= 1")

        if (
            type(self.max_replicas) is not int
            or self.max_replicas < self.min_replicas
        ):
            raise ValueError(
                "max_replicas must be an integer >= min_replicas"
            )

        timeout = self.command_timeout_seconds

        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError(
                "command_timeout_seconds must be a finite number > 0"
            )

        try:
            timeout_value = float(timeout)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(
                "command_timeout_seconds must be a finite number > 0"
            ) from None

        if not math.isfinite(timeout_value) or timeout_value <= 0:
            raise ValueError(
                "command_timeout_seconds must be a finite number > 0"
            )


class WorkerComposeScalingAdapter:
    """Validate and preview worker scaling without executing live scaling."""

    def __init__(
        self,
        config: WorkerComposeScalingAdapterConfig,
        *,
        runner: Callable = subprocess.run,
    ) -> None:
        if not isinstance(config, WorkerComposeScalingAdapterConfig):
            raise TypeError(
                "config must be a WorkerComposeScalingAdapterConfig"
            )

        if not callable(runner):
            raise TypeError("runner must be callable")

        self.config = config
        self._runner = runner

    def preview(
        self,
        decision: ScalingDecision,
        *,
        scale_in_drain_confirmed: bool = False,
    ) -> ScalePreviewResult:
        """Return a safe preview result; never perform a live scale."""
        if not isinstance(decision, ScalingDecision):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_decision_type",
            )

        if decision.action is ScalingAction.HOLD:
            return self._result(
                ScalePreviewStatus.NOOP,
                "hold_decision_no_command_needed",
                decision,
            )

        if decision.action not in {
            ScalingAction.SCALE_OUT,
            ScalingAction.SCALE_IN,
        }:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "unsupported_decision_action",
                decision,
            )

        # Scale-in needs separately verified drain confirmation.
        if (
            decision.action is ScalingAction.SCALE_IN
            and scale_in_drain_confirmed is not True
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "scale_in_drain_not_confirmed",
                decision,
            )

        current = decision.current_replicas
        target = decision.target_replicas

        if type(current) is not int or type(target) is not int:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_replica_count",
                decision,
            )

        if (
            current < self.config.min_replicas
            or current > self.config.max_replicas
            or target < self.config.min_replicas
            or target > self.config.max_replicas
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "replicas_out_of_configured_bounds",
                decision,
            )

        expected_target = (
            current + 1
            if decision.action is ScalingAction.SCALE_OUT
            else current - 1
        )

        if target != expected_target:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "target_must_change_by_exactly_one_replica",
                decision,
            )

        ratio = decision.load_ratio

        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_decision_load_ratio",
                decision,
            )

        try:
            numeric_ratio = float(ratio)
        except (OverflowError, TypeError, ValueError):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_decision_load_ratio",
                decision,
            )

        if not math.isfinite(numeric_ratio) or numeric_ratio < 0:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_decision_load_ratio",
                decision,
            )

        missing = [
            name
            for name in (ENV_FILE_NAME, *COMPOSE_FILES)
            if not (self.config.project_root / name).is_file()
        ]

        if missing:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "required_file_missing:" + ",".join(missing),
                decision,
            )

        base = self._compose_base()
        config_command = [*base, "config", "--format", "json"]

        config_result, error = self._invoke(config_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                command=config_command,
            )

        if config_result.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_config_failed",
                decision,
                exit_code=config_result.returncode,
                command=config_command,
            )

        try:
            resolved = json.loads(getattr(config_result, "stdout", ""))
        except (TypeError, ValueError):
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_config_invalid_json",
                decision,
                command=config_command,
            )

        services = (
            resolved.get("services")
            if isinstance(resolved, dict)
            else None
        )
        worker = services.get("worker") if isinstance(services, dict) else None

        if not isinstance(worker, dict):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_service_missing",
                decision,
                command=config_command,
            )

        if worker.get("container_name"):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_has_fixed_container_name",
                decision,
                command=config_command,
            )

        command_config = worker.get("command")

        if (
            not isinstance(command_config, list)
            or not command_config
            or any(not isinstance(part, str) for part in command_config)
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_command_configuration_invalid",
                decision,
                command=config_command,
            )

        executable = (
            command_config[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        )
        lowered_command = [part.lower() for part in command_config]

        if (
            executable != "celery"
            or "worker" not in lowered_command
            or "beat" in lowered_command
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_command_not_confirmed",
                decision,
                command=config_command,
            )

        ports = worker.get("ports") or []

        if not isinstance(ports, list):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_ports_configuration_invalid",
                decision,
                command=config_command,
            )

        if ports:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_host_ports_not_allowed",
                decision,
                command=config_command,
            )

        desired_image = worker.get("image")
        if not isinstance(desired_image, str) or not desired_image.strip():
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_image_unresolved",
                decision,
                command=config_command,
            )

        hash_command = [*base, "config", "--hash", "worker"]
        hash_result, error = self._invoke(hash_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                command=hash_command,
            )

        if hash_result.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_worker_hash_failed",
                decision,
                exit_code=hash_result.returncode,
                command=hash_command,
            )

        hash_output = getattr(hash_result, "stdout", "")
        hash_match = (
            re.search(
                r"(?m)^worker\s+([0-9a-f]{32,64})\s*$",
                hash_output.strip(),
            )
            if isinstance(hash_output, str)
            else None
        )

        if hash_match is None:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "compose_worker_hash_invalid",
                decision,
                command=hash_command,
            )

        expected_worker_hash = hash_match.group(1)

        ps_command = [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={EXPECTED_PROJECT_NAME}",
            "--filter",
            "label=com.docker.compose.service=worker",
            "--format",
            '{{.Names}}|{{.State}}|{{.Status}}|{{.Image}}|{{.Label "com.docker.compose.config-hash"}}',
        ]

        ps_result, error = self._invoke(ps_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                command=ps_command,
            )

        if ps_result.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "docker_container_inspection_failed",
                decision,
                exit_code=ps_result.returncode,
                command=ps_command,
            )

        ps_output = getattr(ps_result, "stdout", "")

        if not isinstance(ps_output, str):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "docker_container_inspection_malformed",
                decision,
                command=ps_command,
            )

        rows = [
            line.strip()
            for line in ps_output.splitlines()
            if line.strip()
        ]

        parsed: list[tuple[str, str, str, str, str]] = []

        for row in rows:
            fields = row.split("|")

            if len(fields) != 5 or any(not field.strip() for field in fields):
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "docker_container_inspection_malformed",
                    decision,
                    observed_running_replicas=len(rows),
                    command=ps_command,
                )

            name, state, status, image, config_hash = (
                field.strip() for field in fields
            )
            parsed.append(
                (
                    name,
                    state.lower(),
                    status.lower(),
                    image,
                    config_hash,
                )
            )

        if len(parsed) != current:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "running_replica_count_mismatch",
                decision,
                observed_running_replicas=len(parsed),
                command=ps_command,
            )

        if any(image != desired_image for _, _, _, image, _ in parsed):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_image_mismatch",
                decision,
                observed_running_replicas=len(parsed),
                command=ps_command,
            )

        if any(
            config_hash != expected_worker_hash
            for _, _, _, _, config_hash in parsed
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_config_hash_mismatch",
                decision,
                observed_running_replicas=len(parsed),
                command=ps_command,
            )

        names = [entry[0].lower() for entry in parsed]

        if len(set(names)) != len(names):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "duplicate_worker_container_names",
                decision,
                observed_running_replicas=len(parsed),
                command=ps_command,
            )

        prefix = f"{EXPECTED_PROJECT_NAME}-worker-".lower()
        replica_numbers: list[int] = []

        for name, state, status, image, config_hash in parsed:
            if not name.startswith(prefix):
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "unexpected_worker_container_name",
                    decision,
                    observed_running_replicas=len(parsed),
                    command=ps_command,
                )

            suffix = name[len(prefix):]

            if not suffix.isdigit() or int(suffix) < 1:
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "unexpected_worker_container_name",
                    decision,
                    observed_running_replicas=len(parsed),
                    command=ps_command,
                )

            replica_numbers.append(int(suffix))

            if (
                state != "running"
                or any(
                    marker in status
                    for marker in (
                        "unhealthy",
                        "restarting",
                        "exited",
                        "dead",
                    )
                )
            ):
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "worker_not_running",
                    decision,
                    observed_running_replicas=len(parsed),
                    command=ps_command,
                )

        if set(replica_numbers) != set(range(1, current + 1)):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "worker_replica_identity_mismatch",
                decision,
                observed_running_replicas=len(parsed),
                command=ps_command,
            )

        # There is deliberately no non-dry-run scale command in this class.
        scale_command = [
            *base[:2],
            "--dry-run",
            *base[2:],
            "up",
            "--no-deps",
            "--no-recreate",
            "--scale",
            f"worker={target}",
            "worker",
        ]

        scale_result, error = self._invoke(scale_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                observed_running_replicas=len(parsed),
                command=scale_command,
            )

        if scale_result.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_worker_dry_run_failed",
                decision,
                observed_running_replicas=len(parsed),
                exit_code=scale_result.returncode,
                command=scale_command,
            )

        plan_output = "\n".join(
            part
            for part in (
                getattr(scale_result, "stdout", ""),
                getattr(scale_result, "stderr", ""),
            )
            if isinstance(part, str)
        )

        if not _worker_scale_dry_run_plan_is_safe(plan_output, decision):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "compose_scale_plan_unsafe",
                decision,
                observed_running_replicas=len(parsed),
                exit_code=scale_result.returncode,
                command=scale_command,
            )

        return self._result(
            ScalePreviewStatus.DRY_RUN_ACCEPTED,
            "dry_run_validated_no_infrastructure_change",
            decision,
            observed_running_replicas=len(parsed),
            exit_code=scale_result.returncode,
            command=scale_command,
        )

    def _compose_base(self) -> list[str]:
        args = [
            "docker",
            "compose",
            "--project-name",
            EXPECTED_PROJECT_NAME,
            "--env-file",
            ENV_FILE_NAME,
        ]

        for compose_file in COMPOSE_FILES:
            args.extend(["-f", compose_file])

        return args

    def _invoke(
        self,
        command: Sequence[str],
    ) -> tuple[subprocess.CompletedProcess | None, str | None]:
        try:
            completed = self._runner(
                list(command),
                cwd=str(self.config.project_root),
                capture_output=True,
                text=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "command_timeout"
        except (OSError, subprocess.SubprocessError):
            return None, "command_execution_failed"

        return completed, None

    def _result(
        self,
        status: ScalePreviewStatus,
        reason: str,
        decision: ScalingDecision | None = None,
        *,
        observed_running_replicas: int | None = None,
        exit_code: int | None = None,
        command: Sequence[str] = (),
    ) -> ScalePreviewResult:
        return ScalePreviewResult(
            status=status,
            reason=reason,
            current_replicas=(
                decision.current_replicas if decision is not None else None
            ),
            target_replicas=(
                decision.target_replicas if decision is not None else None
            ),
            observed_running_replicas=observed_running_replicas,
            exit_code=exit_code,
            command=tuple(command),
        )