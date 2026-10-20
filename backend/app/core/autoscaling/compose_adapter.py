from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

from app.core.autoscaling.decision_engine import (
    ScalingAction,
    ScalingDecision,
)


EXPECTED_PROJECT_NAME = "clinic-system-phase9"
BACKEND_SERVICE_NAME = "backend"
ENV_FILE_NAME = ".env.phase6"
COMPOSE_FILES = (
    "compose.yaml",
    "compose.phase6.yaml",
    "compose.phase9.yaml",
)


class ScalePreviewStatus(str, Enum):
    """Outcome of a non-mutating Compose scale preview."""

    NOOP = "noop"
    REFUSED = "refused"
    FAILED = "failed"
    DRY_RUN_ACCEPTED = "dry_run_accepted"



def _scale_dry_run_plan_is_safe(
    output: str,
    decision: ScalingDecision,
) -> bool:
    """Fail closed unless Compose proposes only the intended replica change."""
    if not isinstance(output, str) or not output.strip():
        return False

    if decision.action is ScalingAction.SCALE_OUT:
        expected_name = f"{EXPECTED_PROJECT_NAME}-backend-{decision.target_replicas}"
        allowed_actions = {"creating", "created", "starting", "started"}
        completion_action = "started"
    elif decision.action is ScalingAction.SCALE_IN:
        expected_name = f"{EXPECTED_PROJECT_NAME}-backend-{decision.current_replicas}"
        allowed_actions = {"stopping", "stopped", "removing", "removed"}
        completion_action = "removed"
    else:
        return False

    resource_change_actions = {
        "building", "built", "creating", "created", "recreating", "recreated",
        "pulling", "pulled", "removing", "removed", "starting", "started",
        "stopping", "stopped",
    }

    saw_expected_action = False
    saw_completion = False

    for line in output.splitlines():
        lowered = line.lower()
        marker = lowered.find("container ")

        if marker >= 0:
            fields = line[marker + len("container "):].split()
            if len(fields) < 2:
                return False

            name = fields[0].strip(" ,:;")
            action = fields[1].strip(" ,:;").lower()

            # Refuse any other container or lifecycle operation, including
            # a recreation of a surviving backend.
            if name != expected_name or action not in allowed_actions:
                return False

            saw_expected_action = True
            if action == completion_action:
                saw_completion = True
            continue

        # Scaling the backend must not change shared supporting resources.
        for resource in ("network ", "volume ", "image "):
            marker = lowered.find(resource)
            if marker < 0:
                continue

            fields = lowered[marker + len(resource):].split()
            if len(fields) >= 2:
                action = fields[1].strip(" ,:;").lower()
                if action in resource_change_actions:
                    return False

    return saw_expected_action and saw_completion


@dataclass(frozen=True, slots=True)
class ComposeScalingAdapterConfig:
    """Explicit bounds and command timeout for the local Compose adapter."""

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
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError(
                "command_timeout_seconds must be a finite number > 0"
            )


@dataclass(frozen=True, slots=True)
class ScalePreviewResult:
    """Safe summary of an adapter decision; raw Compose output is not exposed."""

    status: ScalePreviewStatus
    reason: str
    current_replicas: int | None
    target_replicas: int | None
    observed_running_replicas: int | None = None
    exit_code: int | None = None
    command: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {
            ScalePreviewStatus.NOOP,
            ScalePreviewStatus.DRY_RUN_ACCEPTED,
        }


class ComposeScalingAdapter:
    """Preview Gate A decisions against the fixed local Compose deployment.

    This class deliberately has no live scale operation. It validates the
    merged project configuration and running replicas, then invokes Compose
    with --dry-run and --no-deps for the backend service only.
    """

    def __init__(
        self,
        config: ComposeScalingAdapterConfig,
        *,
        runner: Callable = subprocess.run,
    ) -> None:
        if not isinstance(config, ComposeScalingAdapterConfig):
            raise TypeError(
                "config must be a ComposeScalingAdapterConfig"
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
        """Preview an actionable decision without changing infrastructure."""
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

        # Do not permit scale-in based on low demand alone. The caller may
        # confirm this only after an independently implemented drain procedure
        # removes the retiring instance from routing and verifies drain completion.
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
        ratio = decision.load_ratio

        if type(current) is not int or type(target) is not int:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "invalid_replica_count",
                decision,
            )

        if not (
            self.config.min_replicas
            <= current
            <= self.config.max_replicas
            and self.config.min_replicas
            <= target
            <= self.config.max_replicas
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

        if (
            ratio is None
            or isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(float(ratio))
            or ratio < 0
        ):
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

        # Compose resolves the merged configuration. Its output may include
        # secret environment values, so it is kept in memory and never returned.
        config_command = [*base, "config", "--format", "json"]
        completed, error = self._invoke(config_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                command=config_command,
            )

        if completed.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_config_failed",
                decision,
                exit_code=completed.returncode,
                command=config_command,
            )

        try:
            resolved = json.loads(completed.stdout or "")
        except (TypeError, json.JSONDecodeError):
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_config_invalid_json",
                decision,
                exit_code=completed.returncode,
                command=config_command,
            )

        services = resolved.get("services") if isinstance(resolved, dict) else None
        backend = services.get(BACKEND_SERVICE_NAME) if isinstance(services, dict) else None

        if not isinstance(backend, dict):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "backend_service_missing",
                decision,
                command=config_command,
            )

        if backend.get("container_name"):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "backend_has_fixed_container_name",
                decision,
                command=config_command,
            )

        healthcheck = backend.get("healthcheck")
        if (
            not isinstance(healthcheck, dict)
            or healthcheck.get("disable") is True
            or not healthcheck.get("test")
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "backend_healthcheck_required",
                decision,
                command=config_command,
            )

        # Require the explicit container stop grace period, but do not override
        # the image CMD merely to restate Gunicorn's already-matching default.
        # Command drift can cause Compose to recreate surviving replicas.
        command_config = backend.get("command")

        if (
            backend.get("stop_grace_period") != "40s"
            or command_config not in (None, [])
        ):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "backend_shutdown_budget_not_safe",
                decision,
                command=config_command,
            )

        ports = backend.get("ports") or []
        if not isinstance(ports, list):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "backend_ports_configuration_invalid",
                decision,
                command=config_command,
            )

        for port in ports:
            if not isinstance(port, dict):
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "backend_ports_configuration_invalid",
                    decision,
                    command=config_command,
                )

            published = port.get("published")
            if published is not None and str(published).strip() != "":
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "backend_host_port_mapping_not_allowed",
                    decision,
                    command=config_command,
                )

        # Count only running backends associated with the fixed Compose project.
        # Container statuses are checked before a scale preview is allowed.
        ps_command = [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={EXPECTED_PROJECT_NAME}",
            "--filter",
            f"label=com.docker.compose.service={BACKEND_SERVICE_NAME}",
            "--format",
            "{{.Names}}|{{.State}}|{{.Status}}",
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

        rows = [
            line.strip()
            for line in (ps_result.stdout or "").splitlines()
            if line.strip()
        ]

        parsed_rows: list[tuple[str, str, str]] = []
        for row in rows:
            parts = row.split("|", 2)
            if len(parts) != 3 or not all(parts):
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "docker_container_inspection_malformed",
                    decision,
                    observed_running_replicas=len(rows),
                    command=ps_command,
                )
            parsed_rows.append((parts[0], parts[1].lower(), parts[2].lower()))

        observed_count = len(parsed_rows)

        if observed_count != current:
            return self._result(
                ScalePreviewStatus.REFUSED,
                "running_replica_count_mismatch",
                decision,
                observed_running_replicas=observed_count,
                command=ps_command,
            )

        for _name, state, status in parsed_rows:
            if state != "running":
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "backend_not_running",
                    decision,
                    observed_running_replicas=observed_count,
                    command=ps_command,
                )

            if "unhealthy" in status or "(healthy)" not in status:
                return self._result(
                    ScalePreviewStatus.REFUSED,
                    "backend_not_healthy",
                    decision,
                    observed_running_replicas=observed_count,
                    command=ps_command,
                )

        # There is intentionally no non-dry-run alternative in Gate B.
        scale_command = [
            *base,
            "scale",
            "--dry-run",
            "--no-deps",
            f"{BACKEND_SERVICE_NAME}={target}",
        ]
        scale_result, error = self._invoke(scale_command)

        if error:
            return self._result(
                ScalePreviewStatus.FAILED,
                error,
                decision,
                observed_running_replicas=observed_count,
                command=scale_command,
            )

        if scale_result.returncode != 0:
            return self._result(
                ScalePreviewStatus.FAILED,
                "compose_scale_dry_run_failed",
                decision,
                observed_running_replicas=observed_count,
                exit_code=scale_result.returncode,
                command=scale_command,
            )

        plan_output = "\
".join(
            part
            for part in (
                getattr(scale_result, "stdout", ""),
                getattr(scale_result, "stderr", ""),
            )
            if isinstance(part, str)
        )

        if not _scale_dry_run_plan_is_safe(plan_output, decision):
            return self._result(
                ScalePreviewStatus.REFUSED,
                "compose_scale_plan_unsafe",
                decision,
                observed_running_replicas=observed_count,
                exit_code=scale_result.returncode,
                command=scale_command,
            )

        return self._result(
            ScalePreviewStatus.DRY_RUN_ACCEPTED,
            "dry_run_validated_no_infrastructure_change",
            decision,
            observed_running_replicas=observed_count,
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
