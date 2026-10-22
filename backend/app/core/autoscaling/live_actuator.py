from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Sequence

from app.core.autoscaling.compose_adapter import (
    BACKEND_SERVICE_NAME,
    COMPOSE_FILES,
    ENV_FILE_NAME,
    ScalePreviewStatus,
)
from app.core.autoscaling.decision_engine import ScalingAction, ScalingDecision


class ScaleActuationStatus(str, Enum):
    NOOP = "noop"
    REFUSED = "refused"
    FAILED = "failed"
    APPLIED = "applied"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ScaleActuationResult:
    status: ScaleActuationStatus
    reason: str
    current_replicas: int | None
    target_replicas: int | None
    observed_replicas: int | None = None
    exit_code: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ScaleActuationStatus.APPLIED

    @property
    def outcome_known(self) -> bool:
        return self.status is not ScaleActuationStatus.UNCERTAIN

    @property
    def may_acknowledge_decision(self) -> bool:
        return self.status in {
            ScaleActuationStatus.APPLIED,
            ScaleActuationStatus.FAILED,
        }


@dataclass(frozen=True, slots=True)
class DrainOperationResult:
    confirmed: bool
    restored: bool | None
    reason: str
    exit_code: int | None = None


_DRAIN_SCRIPT = r'''
import json
import os
import sys
import time
import urllib.error
import urllib.request

action = sys.argv[1]
timeout = float(sys.argv[2])
token = os.environ.get("PHASE9_BACKEND_DRAIN_TOKEN", "")

def finish(code, reason, **details):
    payload = {"ok": code == 0, "reason": reason}
    payload.update(details)
    print(json.dumps(payload, separators=(",", ":")))
    raise SystemExit(code)

if len(token) < 32:
    finish(20, "drain_control_unavailable")

def request(path, method):
    req = urllib.request.Request(
        "http://127.0.0.1:5000" + path,
        method=method,
        headers={"Authorization": "Bearer " + token},
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as response:
            raw, code = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, code = exc.read(), exc.code
    except Exception:
        return 599, {}
    try:
        return code, json.loads(raw.decode("utf-8"))
    except Exception:
        return code, {}

if action == "undrain":
    code, body = request("/health/undrain", "POST")
    if code != 200 or body.get("success") is not True:
        finish(30, "undrain_not_confirmed")
    if body.get("draining") is not False:
        finish(31, "undrain_state_not_confirmed")
    finish(0, "undrain_confirmed", draining=False)

if action != "drain":
    finish(40, "invalid_drain_action")

code, body = request("/health/drain", "POST")
if code != 200 or body.get("success") is not True:
    finish(21, "drain_request_rejected")

deadline = time.monotonic() + timeout
while True:
    code, state = request("/health/drain-status", "GET")
    if code != 200 or state.get("success") is not True:
        finish(22, "drain_status_unavailable")

    active_http = state.get("active_http_requests")
    active_sockets = state.get("active_socket_connections")

    if (
        type(active_http) is not int or type(active_sockets) is not int
        or active_http < 0 or active_sockets < 0
    ):
        finish(24, "drain_counts_invalid")

    if (
        state.get("draining") is True
        and state.get("ready_for_retirement") is True
        and active_http == 0 and active_sockets == 0
    ):
        break

    if time.monotonic() >= deadline:
        finish(23, "drain_timeout")
    time.sleep(0.5)

live_code, _ = request("/health/live", "GET")
ready_code, ready = request("/health/ready", "GET")
if live_code != 200 or ready_code != 503:
    finish(25, "drain_health_contract_failed")
if ready.get("status") != "draining":
    finish(26, "drain_readiness_not_confirmed")

finish(
    0, "drain_confirmed",
    draining=True,
    active_http_requests=0,
    active_socket_connections=0,
    ready_for_retirement=True,
)
'''


class DockerExecBackendDrainAdapter:
    """Confirm drain through authenticated container-local health endpoints."""

    def __init__(
        self,
        *,
        project_name: str,
        timeout_seconds: float = 30.0,
        runner: Callable = subprocess.run,
    ) -> None:
        if (
            not isinstance(project_name, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None
        ):
            raise ValueError("Invalid Docker Compose project name")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or not 1 <= float(timeout_seconds) <= 120
        ):
            raise ValueError("Drain timeout must be within 1..120 seconds")
        if not callable(runner):
            raise TypeError("runner must be callable")
        self.project_name = project_name
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner

    def _valid_name(self, name: str) -> bool:
        return (
            isinstance(name, str)
            and re.fullmatch(
                rf"{re.escape(self.project_name)}-backend-[1-9][0-9]*",
                name,
            ) is not None
        )

    def _invoke(self, name: str, action: str, timeout: float):
        command = [
            "docker", "exec", name, "python", "-c",
            _DRAIN_SCRIPT, action, str(self.timeout_seconds),
        ]
        try:
            result = self._runner(
                command, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except Exception:
            return None, "command_execution_failed"

        if type(getattr(result, "returncode", None)) is not int:
            return None, "invalid_command_result"
        try:
            payload = json.loads(result.stdout or "")
        except (TypeError, ValueError):
            return None, "invalid_drain_response"
        if not isinstance(payload, dict):
            return None, "invalid_drain_response"
        return (result, payload), None

    def undrain(self, container_name: str) -> bool:
        if not self._valid_name(container_name):
            return False
        response, error = self._invoke(container_name, "undrain", 10)
        if error is not None or response is None:
            return False
        result, payload = response
        return (
            result.returncode == 0
            and payload.get("ok") is True
            and payload.get("draining") is False
        )

    def drain_and_confirm(self, container_name: str) -> DrainOperationResult:
        if not self._valid_name(container_name):
            return DrainOperationResult(False, None, "invalid_backend_name")

        response, error = self._invoke(
            container_name, "drain", self.timeout_seconds + 10
        )
        if error is None and response is not None:
            result, payload = response
            if (
                result.returncode == 0
                and payload.get("ok") is True
                and payload.get("reason") == "drain_confirmed"
                and payload.get("draining") is True
                and payload.get("active_http_requests") == 0
                and payload.get("active_socket_connections") == 0
                and payload.get("ready_for_retirement") is True
            ):
                return DrainOperationResult(
                    True, None, "drain_confirmed", result.returncode
                )
            exit_code = result.returncode
        else:
            exit_code = None

        restored = self.undrain(container_name)
        return DrainOperationResult(
            False, restored, "drain_not_confirmed", exit_code
        )


@dataclass(frozen=True, slots=True)
class _ContainerSnapshot:
    name: str
    image: str
    state: str
    status: str


class ComposeLiveScalingActuator:
    """Opt-in actuator that verifies the actual Docker state after each action."""

    def __init__(
        self,
        preview_adapter,
        *,
        live_enabled: bool = False,
        drain_adapter: DockerExecBackendDrainAdapter | None = None,
        runner: Callable = subprocess.run,
    ) -> None:
        if not callable(getattr(preview_adapter, "preview", None)):
            raise TypeError("preview_adapter must provide preview()")
        if type(live_enabled) is not bool:
            raise ValueError("live_enabled must be bool")
        if not callable(runner):
            raise TypeError("runner must be callable")

        config = getattr(preview_adapter, "config", None)
        project = getattr(preview_adapter, "project_name", None)
        root = getattr(config, "project_root", None)
        config_project = getattr(config, "project_name", None)

        if (
            not isinstance(project, str)
            or project != config_project
            or not isinstance(root, Path)
            or not root.is_dir()
        ):
            raise ValueError("Preview adapter must expose a consistent project and root")
        if drain_adapter is not None and drain_adapter.project_name != project:
            raise ValueError("Drain and Compose adapters must target the same project")

        self.preview_adapter = preview_adapter
        self.project_name = project
        self.project_root = root
        self.live_enabled = live_enabled
        self.drain_adapter = drain_adapter
        self._runner = runner

    def apply(self, decision: ScalingDecision) -> ScaleActuationResult:
        if not isinstance(decision, ScalingDecision):
            return self._result(
                ScaleActuationStatus.REFUSED, "invalid_decision_type", None, None
            )

        current, target = decision.current_replicas, decision.target_replicas

        if decision.action is ScalingAction.HOLD:
            return self._result(
                ScaleActuationStatus.NOOP, "hold_decision_no_command_needed",
                current, target, observed=current,
            )
        if decision.action not in {ScalingAction.SCALE_OUT, ScalingAction.SCALE_IN}:
            return self._result(
                ScaleActuationStatus.REFUSED, "unsupported_decision_action",
                current, target,
            )
        if not self.live_enabled:
            return self._result(
                ScaleActuationStatus.REFUSED, "live_actuation_disabled",
                current, target,
            )

        scale_in = decision.action is ScalingAction.SCALE_IN
        config = getattr(self.preview_adapter, "config", None)
        minimum, maximum = getattr(config, "min_replicas", None), getattr(config, "max_replicas", None)

        if (
            type(current) is not int or type(target) is not int
            or type(minimum) is not int or type(maximum) is not int
            or not minimum <= current <= maximum
            or not minimum <= target <= maximum
            or (target != current - 1 if scale_in else target != current + 1)
        ):
            return self._result(
                ScaleActuationStatus.REFUSED, "invalid_replica_transition",
                current, target,
            )

        if scale_in and self.drain_adapter is None:
            return self._result(
                ScaleActuationStatus.REFUSED, "scale_in_drain_unavailable",
                current, target,
            )

        expected_image, error = self._resolved_backend_image()
        if error:
            return self._result(
                ScaleActuationStatus.FAILED, error, current, target
            )

        before, inventory_error = self._read_inventory()
        if inventory_error or not self._inventory_matches(before, current, expected_image):
            return self._result(
                ScaleActuationStatus.REFUSED, "pre_action_state_not_safe",
                current, target, observed=len(before) if before is not None else None,
            )

        drain_target = None
        if scale_in:
            drain_target = f"{self.project_name}-backend-{current}"
            try:
                drained = self.drain_adapter.drain_and_confirm(drain_target)
            except Exception:
                drained = DrainOperationResult(False, None, "drain_adapter_exception")
            if not isinstance(drained, DrainOperationResult):
                drained = DrainOperationResult(False, None, "invalid_drain_result")
            if not drained.confirmed:
                if drained.restored is True:
                    return self._result(
                        ScaleActuationStatus.FAILED,
                        "drain_not_confirmed_and_undrain_confirmed",
                        current, target, exit_code=drained.exit_code,
                    )
                return self._result(
                    ScaleActuationStatus.UNCERTAIN,
                    "drain_not_confirmed_and_undrain_uncertain",
                    current, target, exit_code=drained.exit_code,
                )

        try:
            preview = self.preview_adapter.preview(
                decision, scale_in_drain_confirmed=scale_in
            )
        except Exception:
            return self._abort_after_drain(
                drain_target, current, target, "preview_exception"
            )

        if not isinstance(getattr(preview, "status", None), ScalePreviewStatus):
            return self._abort_after_drain(
                drain_target, current, target, "invalid_preview_result"
            )

        if preview.status is not ScalePreviewStatus.DRY_RUN_ACCEPTED:
            status = (
                ScaleActuationStatus.FAILED
                if preview.status is ScalePreviewStatus.FAILED
                else ScaleActuationStatus.REFUSED
            )
            return self._abort_after_drain(
                drain_target, current, target,
                "preview_not_accepted:" + str(preview.reason),
                status=status,
                observed=getattr(preview, "observed_running_replicas", None),
                exit_code=getattr(preview, "exit_code", None),
            )

        image_after_preview, error = self._resolved_backend_image()
        inventory_after_preview, inventory_error = self._read_inventory()
        if (
            error
            or inventory_error
            or image_after_preview != expected_image
            or not self._inventory_matches(
                inventory_after_preview, current, expected_image
            )
        ):
            return self._abort_after_drain(
                drain_target, current, target, "state_changed_after_preview"
            )

        # The command is the live equivalent of the adapter's scale dry-run,
        # differing only by the dry-run flag.
        command = [
            *self._compose_base(), "scale", "--no-deps",
            f"{BACKEND_SERVICE_NAME}={target}",
        ]
        operation, operation_error = self._invoke(command, timeout=120)
        exit_code = operation.returncode if operation is not None else None

        after, inventory_error = self._read_inventory()
        if inventory_error:
            return self._result(
                ScaleActuationStatus.UNCERTAIN,
                "post_action_inventory_unavailable",
                current, target, exit_code=exit_code,
            )

        if self._inventory_matches(after, target, expected_image):
            return self._result(
                ScaleActuationStatus.APPLIED,
                "target_state_verified" if not operation_error and exit_code == 0
                else "target_state_verified_after_command_error",
                current, target, observed=len(after), exit_code=exit_code,
            )

        if self._inventory_matches(after, current, expected_image):
            if operation_error is None and exit_code == 0:
                # A successful CLI exit and unchanged containers disagree.
                # Do not acknowledge this recommendation.
                return self._result(
                    ScaleActuationStatus.UNCERTAIN,
                    "scale_command_success_state_not_changed",
                    current, target, observed=len(after), exit_code=exit_code,
                )

            if drain_target is not None and not self._cancel_drain(drain_target):
                return self._result(
                    ScaleActuationStatus.UNCERTAIN,
                    "scale_failed_but_undrain_uncertain",
                    current, target, observed=len(after), exit_code=exit_code,
                )

            return self._result(
                ScaleActuationStatus.FAILED,
                "previous_state_verified_target_not_reached",
                current, target, observed=len(after), exit_code=exit_code,
            )

        # Missing, unhealthy, partially-created, or unexpected state needs
        # an explicit reconciliation pass before the pending recommendation
        # can be acknowledged.
        return self._result(
            ScaleActuationStatus.UNCERTAIN, "post_action_state_ambiguous",
            current, target, observed=len(after), exit_code=exit_code,
        )

    def _abort_after_drain(
        self, name, current, target, reason, *,
        status=ScaleActuationStatus.FAILED, exit_code=None, observed=None,
    ):
        if name is None:
            return self._result(
                status, reason, current, target,
                observed=observed, exit_code=exit_code,
            )
        if self._cancel_drain(name):
            return self._result(
                status, reason, current, target,
                observed=observed, exit_code=exit_code,
            )
        return self._result(
            ScaleActuationStatus.UNCERTAIN, reason + "_and_undrain_uncertain",
            current, target, observed=observed, exit_code=exit_code,
        )

    def _cancel_drain(self, name: str) -> bool:
        try:
            return self.drain_adapter.undrain(name) is True
        except Exception:
            return False

    def _compose_base(self) -> list[str]:
        args = [
            "docker", "compose", "--project-name", self.project_name,
            "--env-file", ENV_FILE_NAME,
        ]
        for name in COMPOSE_FILES:
            args.extend(["-f", name])
        return args

    def _invoke(self, command: Sequence[str], *, timeout: float):
        try:
            result = self._runner(
                list(command), cwd=str(self.project_root),
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except Exception:
            return None, "command_execution_failed"
        if type(getattr(result, "returncode", None)) is not int:
            return None, "invalid_command_result"
        return result, None

    def _resolved_backend_image(self):
        result, error = self._invoke(
            [*self._compose_base(), "config", "--format", "json"],
            timeout=20,
        )
        if error or result is None:
            return None, "compose_config_unavailable"
        if result.returncode != 0:
            return None, "compose_config_failed"
        try:
            resolved = json.loads(result.stdout or "")
            image = resolved["services"][BACKEND_SERVICE_NAME]["image"]
        except (TypeError, ValueError, KeyError):
            return None, "compose_backend_image_unresolved"
        if not isinstance(image, str) or not image.strip():
            return None, "compose_backend_image_unresolved"
        return image, None

    def _read_inventory(self):
        command = [
            "docker", "ps", "-a",
            "--filter", f"label=com.docker.compose.project={self.project_name}",
            "--filter", f"label=com.docker.compose.service={BACKEND_SERVICE_NAME}",
            "--format", "{{.Names}}|{{.Image}}|{{.State}}|{{.Status}}",
        ]
        result, error = self._invoke(command, timeout=10)
        if error or result is None or result.returncode != 0:
            return None, "docker_inventory_failed"

        rows = []
        for line in (result.stdout or "").splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) != 4 or any(not value.strip() for value in parts):
                return None, "docker_inventory_malformed"
            rows.append(_ContainerSnapshot(
                parts[0].strip(), parts[1].strip(),
                parts[2].strip().lower(), parts[3].strip().lower(),
            ))
        return rows, None

    def _inventory_matches(self, rows, count: int, image: str) -> bool:
        if type(count) is not int or count < 1 or rows is None or len(rows) != count:
            return False
        expected = {
            f"{self.project_name}-backend-{i}" for i in range(1, count + 1)
        }
        names = [row.name for row in rows]
        if len(set(names)) != len(names) or set(names) != expected:
            return False
        return all(
            row.image == image and row.state == "running"
            and "(healthy)" in row.status and "(unhealthy)" not in row.status
            for row in rows
        )

    @staticmethod
    def _result(
        status, reason, current, target, *, observed=None, exit_code=None
    ):
        return ScaleActuationResult(
            status, reason, current, target, observed, exit_code
        )


@dataclass(frozen=True, slots=True)
class _ContainerSnapshot:
    name: str
    image: str
    state: str
    status: str