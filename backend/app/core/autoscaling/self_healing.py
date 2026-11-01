from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from app.core.autoscaling.compose_adapter import (
    BACKEND_SERVICE_NAME,
    COMPOSE_FILES,
    ENV_FILE_NAME,
)
from app.core.autoscaling.nginx_routing import (
    NginxBackendRoutingAdapter,
)


_HEALTH_WAIT_TIMEOUT_SECONDS = 60

_NETWORK_READY_PROBE_SCRIPT = """
import sys
import time
import urllib.request

target = sys.argv[1]
url = f"http://{target}:5000/health/live"
deadline = time.monotonic() + 30.0
consecutive = 0

while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            if response.status == 200:
                consecutive += 1
                if consecutive >= 3:
                    raise SystemExit(0)
            else:
                consecutive = 0
    except Exception:
        consecutive = 0
    time.sleep(0.5)

raise SystemExit(1)
"""


class SelfHealingStatus(str, Enum):
    NOOP = "noop"
    REPAIR_NEEDED = "repair_needed"
    WAITING = "waiting"
    REPAIRED = "repaired"
    REFUSED = "refused"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SelfHealingResult:
    status: SelfHealingStatus
    reason: str
    desired_replicas: int | None = None
    target_backend: str | None = None
    previous_instance_id: str | None = None
    replacement_instance_id: str | None = None
    healthy_replicas: int | None = None
    exit_code: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in {
            SelfHealingStatus.NOOP,
            SelfHealingStatus.WAITING,
            SelfHealingStatus.REPAIRED,
        }

    @property
    def recoverable_preflight(self) -> bool:
        return self.status in {
            SelfHealingStatus.NOOP,
            SelfHealingStatus.REPAIR_NEEDED,
            SelfHealingStatus.WAITING,
        }


@dataclass(frozen=True, slots=True)
class _BackendContainer:
    instance_id: str
    name: str
    state: str
    status: str


@dataclass(frozen=True, slots=True)
class _FleetInspection:
    result: SelfHealingResult
    desired_names: tuple[str, ...] = ()
    containers: tuple[_BackendContainer, ...] = ()


class ComposeBackendSelfHealer:
    """Repair one missing/dead/unhealthy Compose backend per controller cycle."""

    def __init__(
        self,
        *,
        project_root: Path,
        project_name: str,
        min_replicas: int,
        max_replicas: int,
        routing_adapter: NginxBackendRoutingAdapter,
        ownership_guard: Callable[[], bool],
        readiness_probe: Callable[[str, str], bool] | None = None,
        runner: Callable = subprocess.run,
        command_timeout_seconds: float = 120.0,
    ) -> None:
        root = Path(project_root).resolve()

        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")

        if (
            not isinstance(project_name, str)
            or re.fullmatch(
                r"[a-z0-9][a-z0-9_-]*",
                project_name,
            )
            is None
        ):
            raise ValueError("project_name must be a valid Compose project")

        if type(min_replicas) is not int or min_replicas < 1:
            raise ValueError("min_replicas must be an integer >= 1")

        if (
            type(max_replicas) is not int
            or max_replicas < min_replicas
        ):
            raise ValueError(
                "max_replicas must be an integer >= min_replicas"
            )

        routing_project = getattr(
            routing_adapter,
            "project_name",
            None,
        )
        routing_file = getattr(
            routing_adapter,
            "upstream_file",
            None,
        )
        verify_replicas = getattr(
            routing_adapter,
            "verify_replicas",
            None,
        )
        quarantine = getattr(routing_adapter, "quarantine", None)
        restore_quarantined = getattr(
            routing_adapter,
            "restore_quarantined",
            None,
        )
        quarantined_backends = getattr(
            routing_adapter,
            "quarantined_backends",
            None,
        )

        if (
            routing_project != project_name
            or not isinstance(routing_file, Path)
            or not callable(verify_replicas)
            or not callable(quarantine)
            or not callable(restore_quarantined)
            or not callable(quarantined_backends)
        ):
            raise ValueError(
                "routing adapter must target the same Compose project "
                "and provide upstream_file, verify_replicas(), quarantine(), "
                "restore_quarantined(), and quarantined_backends()"
            )

        if not callable(ownership_guard):
            raise TypeError("ownership_guard must be callable")

        if readiness_probe is not None and not callable(readiness_probe):
            raise TypeError("readiness_probe must be callable")

        if not callable(runner):
            raise TypeError("runner must be callable")

        if (
            isinstance(command_timeout_seconds, bool)
            or not isinstance(
                command_timeout_seconds,
                (int, float),
            )
            or not math.isfinite(
                float(command_timeout_seconds)
            )
            or command_timeout_seconds <= 0
        ):
            raise ValueError(
                "command_timeout_seconds must be finite and > 0"
            )

        self.project_root = root
        self.project_name = project_name
        self.min_replicas = min_replicas
        self.max_replicas = max_replicas
        self.routing_adapter = routing_adapter
        self.ownership_guard = ownership_guard
        self.command_timeout_seconds = float(
            command_timeout_seconds
        )
        self._runner = runner
        self._readiness_probe = (
            readiness_probe or self._probe_network_readiness
        )

        self._route_pattern = re.compile(
            r"^\s*server\s+("
            + re.escape(project_name)
            + r"-backend-([1-9][0-9]*))"
            + r":5000 resolve max_fails=1 "
            + r"fail_timeout=10s(?:\s+down)?;\s*$"
        )

    def inspect(self) -> SelfHealingResult:
        """Read fleet health only; no Docker mutation is performed."""
        return self._inspect().result

    def reconcile_once(self) -> SelfHealingResult:
        "Repair at most one failed backend with route quarantine and readiness gating."
        inspection = self._inspect()
        result = inspection.result

        quarantined = self.routing_adapter.quarantined_backends()
        if quarantined is None:
            return self._result(
                SelfHealingStatus.REFUSED,
                "route_quarantine_state_unavailable",
                desired=result.desired_replicas,
                target=result.target_backend,
                healthy=result.healthy_replicas,
            )
        if len(quarantined) > 1:
            return self._result(
                SelfHealingStatus.REFUSED,
                "multiple_quarantined_backends_not_supported",
                desired=result.desired_replicas,
                healthy=result.healthy_replicas,
            )

        if result.status is SelfHealingStatus.WAITING:
            return result

        if result.status is SelfHealingStatus.NOOP:
            if not quarantined:
                return result
            desired = result.desired_replicas
            target = quarantined[0]
            if type(desired) is not int:
                return self._result(
                    SelfHealingStatus.REFUSED,
                    "repair_target_invalid",
                    desired=desired,
                    target=target,
                )
            return self._restore_ready_quarantine(
                inspection,
                desired=desired,
                target=target,
                previous_id=None,
                exit_code=None,
                success_reason=(
                    "quarantined_backend_reactivated_after_network_readiness"
                ),
            )

        if result.status is not SelfHealingStatus.REPAIR_NEEDED:
            return result

        desired = result.desired_replicas
        target = result.target_backend
        if type(desired) is not int or target is None:
            return self._result(
                SelfHealingStatus.REFUSED,
                "repair_target_invalid",
                desired=desired,
            )
        if quarantined and quarantined[0] != target:
            return self._result(
                SelfHealingStatus.REFUSED,
                "quarantined_backend_does_not_match_repair_target",
                desired=desired,
                target=target,
                healthy=result.healthy_replicas,
            )

        if not quarantined:
            route_check = self.routing_adapter.verify_replicas(desired)
            if not route_check.confirmed:
                return self._result(
                    SelfHealingStatus.REFUSED,
                    "route_set_not_safe_for_self_heal",
                    desired=desired,
                    target=target,
                )

        if not self._owns_controller():
            return self._result(
                SelfHealingStatus.REFUSED,
                "controller_lease_not_owned_before_self_heal",
                desired=desired,
                target=target,
            )

        before_by_name = {
            item.name: item
            for item in inspection.containers
        }
        target_before = before_by_name.get(target)
        previous_id = (
            target_before.instance_id
            if target_before is not None
            else None
        )

        if not quarantined:
            quarantine_result = self.routing_adapter.quarantine(target)
            if not quarantine_result.confirmed:
                return self._result(
                    (
                        SelfHealingStatus.REFUSED
                        if quarantine_result.prior_state_restored is True
                        else SelfHealingStatus.UNCERTAIN
                    ),
                    "failed_backend_route_quarantine_not_confirmed",
                    desired=desired,
                    target=target,
                    previous_id=previous_id,
                    healthy=result.healthy_replicas,
                    exit_code=quarantine_result.exit_code,
                )

        if not self._owns_controller():
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "controller_lease_lost_after_backend_quarantine",
                desired=desired,
                target=target,
                previous_id=previous_id,
            )

        if target_before is not None:
            remove_result, remove_error = self._invoke(
                ["docker", "rm", "-f", target],
                timeout=min(self.command_timeout_seconds, 30.0),
            )
            if (
                remove_error is not None
                or remove_result is None
                or remove_result.returncode != 0
            ):
                return self._result(
                    SelfHealingStatus.FAILED,
                    "failed_backend_removal_failed",
                    desired=desired,
                    target=target,
                    previous_id=previous_id,
                    healthy=result.healthy_replicas,
                    exit_code=getattr(remove_result, "returncode", None),
                )

        if not self._owns_controller():
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "controller_lease_lost_after_failed_backend_removal",
                desired=desired,
                target=target,
                previous_id=previous_id,
            )

        command = [
            *self._compose_base(),
            "up",
            "-d",
            "--no-deps",
            "--no-recreate",
            "--no-build",
            "--wait",
            "--wait-timeout",
            str(_HEALTH_WAIT_TIMEOUT_SECONDS),
            "--scale",
            f"{BACKEND_SERVICE_NAME}={desired}",
            BACKEND_SERVICE_NAME,
        ]
        compose_result, compose_error = self._invoke(
            command,
            timeout=self.command_timeout_seconds,
        )

        post = self._inspect()
        post_result = post.result
        if not self._owns_controller():
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "controller_lease_lost_after_self_heal_command",
                desired=desired,
                target=target,
                previous_id=previous_id,
                exit_code=getattr(compose_result, "returncode", None),
            )

        post_by_name = {
            item.name: item
            for item in post.containers
        }
        replacement = post_by_name.get(target)
        replacement_id = (
            replacement.instance_id
            if replacement is not None
            else None
        )

        if (
            post_result.status is SelfHealingStatus.NOOP
            and replacement_id is not None
            and (previous_id is None or replacement_id != previous_id)
            and self._survivors_unchanged(
                before_by_name,
                post_by_name,
                target=target,
            )
        ):
            reason = (
                "backend_replaced_quarantine_restored_and_route_set_verified"
                if (
                    compose_error is None
                    and compose_result is not None
                    and compose_result.returncode == 0
                )
                else (
                    "backend_replacement_verified_after_compose_error_"
                    "quarantine_restored"
                )
            )
            return self._restore_ready_quarantine(
                post,
                desired=desired,
                target=target,
                previous_id=previous_id,
                exit_code=getattr(compose_result, "returncode", None),
                success_reason=reason,
            )

        if post_result.status is SelfHealingStatus.WAITING:
            return self._result(
                SelfHealingStatus.WAITING,
                "replacement_backend_health_starting_route_quarantined",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement_id,
                healthy=post_result.healthy_replicas,
                exit_code=getattr(compose_result, "returncode", None),
            )
        if compose_error == "command_timeout":
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "self_heal_compose_outcome_unknown_route_quarantined",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement_id,
                healthy=post_result.healthy_replicas,
            )
        if (
            compose_error is not None
            or compose_result is None
            or compose_result.returncode != 0
        ):
            return self._result(
                SelfHealingStatus.FAILED,
                "self_heal_compose_failed_route_quarantined",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement_id,
                healthy=post_result.healthy_replicas,
                exit_code=getattr(compose_result, "returncode", None),
            )
        return self._result(
            SelfHealingStatus.UNCERTAIN,
            "self_heal_post_state_not_confirmed_route_quarantined",
            desired=desired,
            target=target,
            previous_id=previous_id,
            replacement_id=replacement_id,
            healthy=post_result.healthy_replicas,
            exit_code=compose_result.returncode,
        )

    def _restore_ready_quarantine(
        self,
        inspection: _FleetInspection,
        *,
        desired: int,
        target: str,
        previous_id: str | None,
        exit_code: int | None,
        success_reason: str,
    ) -> SelfHealingResult:
        by_name = {
            item.name: item
            for item in inspection.containers
        }
        replacement = by_name.get(target)
        if replacement is None or not self._container_is_healthy(replacement):
            return self._result(
                SelfHealingStatus.WAITING,
                "quarantined_backend_not_yet_healthy",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=(
                    replacement.instance_id
                    if replacement is not None
                    else None
                ),
                healthy=inspection.result.healthy_replicas,
                exit_code=exit_code,
            )

        survivor = self._select_healthy_survivor(
            by_name,
            target=target,
        )
        if survivor is None:
            return self._result(
                SelfHealingStatus.REFUSED,
                "no_healthy_backend_survivor_for_network_readiness_probe",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement.instance_id,
                healthy=inspection.result.healthy_replicas,
                exit_code=exit_code,
            )
        if not self._readiness_probe(survivor, target):
            return self._result(
                SelfHealingStatus.WAITING,
                "replacement_network_readiness_pending_route_quarantined",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement.instance_id,
                healthy=inspection.result.healthy_replicas,
                exit_code=exit_code,
            )
        if not self._owns_controller():
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "controller_lease_lost_before_quarantine_restore",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement.instance_id,
                healthy=inspection.result.healthy_replicas,
                exit_code=exit_code,
            )

        restore = self.routing_adapter.restore_quarantined(target)
        if not restore.confirmed:
            return self._result(
                (
                    SelfHealingStatus.FAILED
                    if restore.prior_state_restored is True
                    else SelfHealingStatus.UNCERTAIN
                ),
                "quarantined_backend_route_restore_not_confirmed",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement.instance_id,
                healthy=inspection.result.healthy_replicas,
                exit_code=restore.exit_code,
            )
        if not self.routing_adapter.verify_replicas(desired).confirmed:
            return self._result(
                SelfHealingStatus.UNCERTAIN,
                "restored_backend_route_set_not_verified",
                desired=desired,
                target=target,
                previous_id=previous_id,
                replacement_id=replacement.instance_id,
                healthy=inspection.result.healthy_replicas,
                exit_code=exit_code,
            )
        return self._result(
            SelfHealingStatus.REPAIRED,
            success_reason,
            desired=desired,
            target=target,
            previous_id=previous_id,
            replacement_id=replacement.instance_id,
            healthy=desired,
            exit_code=exit_code,
        )

    @staticmethod
    def _container_is_healthy(item: _BackendContainer) -> bool:
        return (
            item.state == "running"
            and "(healthy)" in item.status
            and "(unhealthy)" not in item.status
        )

    def _select_healthy_survivor(
        self,
        by_name: dict[str, _BackendContainer],
        *,
        target: str,
    ) -> str | None:
        candidates = sorted(
            name
            for name, item in by_name.items()
            if name != target and self._container_is_healthy(item)
        )
        return candidates[0] if candidates else None

    def _probe_network_readiness(
        self,
        survivor: str,
        target: str,
    ) -> bool:
        completed, error = self._invoke(
            [
                "docker",
                "exec",
                survivor,
                "python",
                "-c",
                _NETWORK_READY_PROBE_SCRIPT,
                target,
            ],
            timeout=min(self.command_timeout_seconds, 40.0),
        )
        return (
            error is None
            and completed is not None
            and completed.returncode == 0
        )

    def _inspect(self) -> _FleetInspection:
        desired_names = self._read_desired_route_names()

        if desired_names is None:
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.REFUSED,
                    "generated_backend_route_file_invalid",
                )
            )

        desired = len(desired_names)

        if not (
            self.min_replicas
            <= desired
            <= self.max_replicas
        ):
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.REFUSED,
                    "desired_route_count_out_of_bounds",
                    desired=desired,
                ),
                desired_names=desired_names,
            )

        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            (
                "label=com.docker.compose.project="
                f"{self.project_name}"
            ),
            "--filter",
            (
                "label=com.docker.compose.service="
                f"{BACKEND_SERVICE_NAME}"
            ),
            "--format",
            "{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}",
        ]

        completed, error = self._invoke(
            command,
            timeout=min(
                self.command_timeout_seconds,
                10.0,
            ),
        )

        if (
            error is not None
            or completed is None
            or completed.returncode != 0
        ):
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.FAILED,
                    "backend_inventory_unavailable",
                    desired=desired,
                    exit_code=getattr(
                        completed,
                        "returncode",
                        None,
                    ),
                ),
                desired_names=desired_names,
            )

        containers = []
        seen_names = set()

        for raw in (completed.stdout or "").splitlines():
            if not raw.strip():
                continue

            parts = [
                part.strip()
                for part in raw.split("|", 3)
            ]

            if len(parts) != 4:
                return _FleetInspection(
                    self._result(
                        SelfHealingStatus.REFUSED,
                        "backend_inventory_malformed",
                        desired=desired,
                    ),
                    desired_names=desired_names,
                )

            instance_id, name, state, status = parts

            if (
                re.fullmatch(
                    r"[0-9a-fA-F]{12,64}",
                    instance_id,
                )
                is None
                or name in seen_names
                or self._backend_index(name) is None
            ):
                return _FleetInspection(
                    self._result(
                        SelfHealingStatus.REFUSED,
                        "backend_inventory_identity_invalid",
                        desired=desired,
                    ),
                    desired_names=desired_names,
                )

            seen_names.add(name)
            containers.append(
                _BackendContainer(
                    instance_id=instance_id.lower(),
                    name=name,
                    state=state.lower(),
                    status=status.lower(),
                )
            )

        unexpected = [
            item.name
            for item in containers
            if item.name not in desired_names
        ]

        if unexpected:
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.REFUSED,
                    "unexpected_backend_replica_present",
                    desired=desired,
                ),
                desired_names=desired_names,
                containers=tuple(containers),
            )

        by_name = {
            item.name: item
            for item in containers
        }

        healthy = []
        waiting = []
        failed = []

        for name in desired_names:
            item = by_name.get(name)

            if item is None:
                failed.append(name)
                continue

            if (
                item.state == "running"
                and "(healthy)" in item.status
                and "(unhealthy)" not in item.status
            ):
                healthy.append(name)
                continue

            if (
                item.state in {
                    "created",
                    "restarting",
                }
                or (
                    item.state == "running"
                    and (
                        "health: starting" in item.status
                        or "(starting)" in item.status
                    )
                )
            ):
                waiting.append(name)
                continue

            failed.append(name)

        if waiting:
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.WAITING,
                    "backend_health_transition_in_progress",
                    desired=desired,
                    healthy=len(healthy),
                ),
                desired_names=desired_names,
                containers=tuple(containers),
            )

        if not failed:
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.NOOP,
                    "backend_fleet_healthy",
                    desired=desired,
                    healthy=len(healthy),
                ),
                desired_names=desired_names,
                containers=tuple(containers),
            )

        if desired > 1 and not healthy:
            return _FleetInspection(
                self._result(
                    SelfHealingStatus.REFUSED,
                    "no_healthy_backend_survivor_for_safe_repair",
                    desired=desired,
                    healthy=0,
                ),
                desired_names=desired_names,
                containers=tuple(containers),
            )

        return _FleetInspection(
            self._result(
                SelfHealingStatus.REPAIR_NEEDED,
                (
                    "failed_backend_replacement_required"
                    if by_name.get(failed[0]) is not None
                    else "missing_backend_replacement_required"
                ),
                desired=desired,
                target=failed[0],
                previous_id=(
                    by_name[failed[0]].instance_id
                    if by_name.get(failed[0]) is not None
                    else None
                ),
                healthy=len(healthy),
            ),
            desired_names=desired_names,
            containers=tuple(containers),
        )

    def _read_desired_route_names(
        self,
    ) -> tuple[str, ...] | None:
        try:
            text = self.routing_adapter.upstream_file.read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeError):
            return None

        names = []

        for line in text.splitlines():
            if not line.strip():
                continue

            match = self._route_pattern.fullmatch(line)

            if match is None:
                return None

            names.append(match.group(1))

        if (
            not names
            or len(names) != len(set(names))
        ):
            return None

        expected = [
            f"{self.project_name}-backend-{index}"
            for index in range(1, len(names) + 1)
        ]

        return (
            tuple(names)
            if names == expected
            else None
        )

    def _backend_index(
        self,
        name: str,
    ) -> int | None:
        match = re.fullmatch(
            re.escape(self.project_name)
            + r"-backend-([1-9][0-9]*)",
            name,
        )

        return (
            int(match.group(1))
            if match is not None
            else None
        )

    def _compose_base(self) -> list[str]:
        command = [
            "docker",
            "compose",
            "--project-name",
            self.project_name,
            "--env-file",
            ENV_FILE_NAME,
        ]

        for name in COMPOSE_FILES:
            command.extend(
                [
                    "-f",
                    name,
                ]
            )

        return command

    def _invoke(
        self,
        command: list[str],
        *,
        timeout: float,
    ):
        try:
            completed = self._runner(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None, "command_timeout"
        except Exception:
            return None, "command_execution_failed"

        if (
            type(
                getattr(
                    completed,
                    "returncode",
                    None,
                )
            )
            is not int
        ):
            return None, "invalid_command_result"

        return completed, None

    def _owns_controller(self) -> bool:
        try:
            return self.ownership_guard() is True
        except Exception:
            return False

    @staticmethod
    def _survivors_unchanged(
        before: dict[str, _BackendContainer],
        after: dict[str, _BackendContainer],
        *,
        target: str,
    ) -> bool:
        for name, original in before.items():
            if name == target:
                continue

            current = after.get(name)

            if (
                current is None
                or current.instance_id
                != original.instance_id
            ):
                return False

        return True

    @staticmethod
    def _result(
        status: SelfHealingStatus,
        reason: str,
        *,
        desired: int | None = None,
        target: str | None = None,
        previous_id: str | None = None,
        replacement_id: str | None = None,
        healthy: int | None = None,
        exit_code: int | None = None,
    ) -> SelfHealingResult:
        return SelfHealingResult(
            status=status,
            reason=reason,
            desired_replicas=desired,
            target_backend=target,
            previous_instance_id=previous_id,
            replacement_instance_id=replacement_id,
            healthy_replicas=healthy,
            exit_code=exit_code,
        )
