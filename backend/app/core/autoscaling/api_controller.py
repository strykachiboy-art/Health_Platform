"""Read-only replica discovery and preview-only API autoscaling orchestration."""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.core.autoscaling.api_observation import (
    APIObservationAdapter,
    APIObservationResult,
)
from app.core.autoscaling.compose_adapter import EXPECTED_PROJECT_NAME
from app.core.autoscaling.coordinator import (
    AutoscalingCoordinator,
    AutoscalingCycleResult,
)
from app.core.autoscaling.decision_engine import ScalingObservation


@dataclass(frozen=True, slots=True)
class BackendReplicaSnapshot:
    """Observed running backend containers and their readiness evidence."""

    current_replicas: int | None
    healthy: bool
    reason: str
    backend_names: tuple[str, ...] = ()
    backend_instance_ids: tuple[str, ...] = ()
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class APIBackendAutoscalingCycleResult:
    """Combined replica, telemetry, and preview-cycle outcome."""

    replicas: BackendReplicaSnapshot
    metrics: dict[str, Any]
    cycle: AutoscalingCycleResult
    reason: str

    @property
    def ok(self) -> bool:
        return self.replicas.healthy and self.cycle.ok

    @property
    def status(self):
        return self.cycle.status


class ComposeBackendReplicaReader:
    """Read running backend replicas from Docker labels without mutation.

    Only the expected Compose project and backend service are inspected.
    Every observed backend must be running, healthy, and have a valid,
    contiguous Compose replica index before the snapshot is considered safe.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        project_name: str = EXPECTED_PROJECT_NAME,
        timeout_seconds: float = 5.0,
        runner: Callable = subprocess.run,
    ) -> None:
        root = Path(project_root).resolve()
        if not root.is_dir():
            raise ValueError("project_root must be an existing directory")
        if (
            not isinstance(project_name, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_name) is None
        ):
            raise ValueError("project_name must be a valid Docker Compose project name")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(float(timeout_seconds))
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and > 0")
        if not callable(runner):
            raise TypeError("runner must be callable")

        self.project_root = root
        self.project_name = project_name
        self.timeout_seconds = float(timeout_seconds)
        self._runner = runner

    def read(self) -> BackendReplicaSnapshot:
        """Read state only; this method cannot start, stop, or scale containers."""
        command = [
            "docker",
            "ps",
            "--filter",
            f"label=com.docker.compose.project={self.project_name}",
            "--filter",
            "label=com.docker.compose.service=backend",
            "--format",
            "{{.ID}}|{{.Names}}|{{.State}}|{{.Status}}",
        ]

        try:
            result = self._runner(
                command,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._failed("backend_replica_discovery_timeout")
        except Exception as exc:
            return self._failed(
                "backend_replica_discovery_failed",
                error_type=type(exc).__name__,
            )

        if (
            getattr(result, "returncode", None) != 0
            or not isinstance(getattr(result, "stdout", None), str)
        ):
            return self._failed("backend_replica_discovery_failed")

        output = result.stdout
        if not output.strip():
            return BackendReplicaSnapshot(
                current_replicas=0,
                healthy=False,
                reason="no_running_backends",
            )

        parsed: list[tuple[int, str, str, str, str]] = []
        replica_pattern = re.compile(
            rf"{re.escape(self.project_name)}-backend-(\d+)"
        )

        for line in output.splitlines():
            if not line.strip():
                continue

            parts = line.split("|", 3)
            if len(parts) != 4:
                return self._failed("backend_replica_snapshot_invalid")

            container_id, name, state, status = (
                part.strip() for part in parts
            )
            container_id = container_id.lower()
            if re.fullmatch(r"[0-9a-f]{12,64}", container_id) is None:
                return self._failed("backend_replica_identity_invalid")

            match = replica_pattern.fullmatch(name)
            if match is None:
                return self._failed("backend_replica_identity_invalid")

            try:
                index = int(match.group(1))
            except ValueError:
                return self._failed("backend_replica_identity_invalid")

            if index < 1 or state.lower() != "running":
                return self._failed("backend_replica_state_invalid")

            parsed.append(
                (
                    index,
                    container_id,
                    name,
                    state.lower(),
                    status.lower(),
                )
            )

        if not parsed:
            return BackendReplicaSnapshot(
                current_replicas=0,
                healthy=False,
                reason="no_running_backends",
            )

        ordered = sorted(parsed, key=lambda item: item[0])
        indices = [item[0] for item in ordered]
        instance_ids = tuple(item[1] for item in ordered)
        names = tuple(item[2] for item in ordered)

        if (
            len(set(indices)) != len(indices)
            or indices != list(range(1, len(indices) + 1))
            or len(set(instance_ids)) != len(instance_ids)
        ):
            return BackendReplicaSnapshot(
                current_replicas=len(parsed),
                healthy=False,
                reason="backend_replica_identity_invalid",
                backend_names=names,
                backend_instance_ids=instance_ids,
            )

        all_healthy = all(
            "(healthy)" in status and "(unhealthy)" not in status
            for _, _, _, _, status in parsed
        )

        if not all_healthy:
            return BackendReplicaSnapshot(
                current_replicas=len(parsed),
                healthy=False,
                reason="backend_health_not_confirmed",
                backend_names=names,
                backend_instance_ids=instance_ids,
            )

        return BackendReplicaSnapshot(
            current_replicas=len(parsed),
            healthy=True,
            reason="ok",
            backend_names=names,
            backend_instance_ids=instance_ids,
        )

    @staticmethod
    def _failed(
        reason: str,
        *,
        error_type: str | None = None,
    ) -> BackendReplicaSnapshot:
        return BackendReplicaSnapshot(
            current_replicas=None,
            healthy=False,
            reason=reason,
            error_type=error_type,
        )


class APIBackendAutoscalingController:
    """Feed shared API telemetry and observed replicas into the coordinator.

    This class has no live actuator. Its only possible external operation is
    the coordinator's existing Compose dry-run preview.
    """

    def __init__(
        self,
        *,
        replica_reader: ComposeBackendReplicaReader,
        observation_adapter: APIObservationAdapter,
        coordinator: AutoscalingCoordinator,
    ) -> None:
        if not callable(getattr(replica_reader, "read", None)):
            raise TypeError("replica_reader must provide read()")
        if not callable(getattr(observation_adapter, "collect", None)):
            raise TypeError("observation_adapter must provide collect()")
        if not callable(getattr(coordinator, "run_cycle", None)):
            raise TypeError("coordinator must provide run_cycle()")

        replica_project = getattr(replica_reader, "project_name", None)
        scaling_project = getattr(coordinator, "project_name", None)
        if (
            isinstance(replica_project, str)
            and isinstance(scaling_project, str)
            and replica_project != scaling_project
        ):
            raise ValueError(
                "replica discovery and scaling adapter must target the same Docker Compose project"
            )

        self._replica_reader = replica_reader
        self._observation_adapter = observation_adapter
        self._coordinator = coordinator

    def run_cycle(
        self,
        *,
        evaluated_at: datetime,
        scale_in_drain_confirmed: bool = False,
    ) -> APIBackendAutoscalingCycleResult:
        """Discover state, collect demand, and request a safe preview only."""
        try:
            snapshot = self._replica_reader.read()
        except Exception as exc:
            snapshot = BackendReplicaSnapshot(
                current_replicas=None,
                healthy=False,
                reason="backend_replica_discovery_failed",
                error_type=type(exc).__name__,
            )

        if not isinstance(snapshot, BackendReplicaSnapshot):
            snapshot = BackendReplicaSnapshot(
                current_replicas=None,
                healthy=False,
                reason="invalid_backend_replica_snapshot",
            )

        try:
            collected = self._observation_adapter.collect(
                current_replicas=snapshot.current_replicas,
                evaluated_at=evaluated_at,
                expected_instance_ids=(
                    snapshot.backend_instance_ids
                    if snapshot.healthy
                    else None
                ),
            )

            if not isinstance(collected, APIObservationResult):
                raise TypeError("invalid API observation result")

            observation = collected.observation
            metrics = dict(collected.metrics)
        except Exception as exc:
            observation = ScalingObservation(
                current_replicas=snapshot.current_replicas,
                load_ratio=None,
                observed_at=evaluated_at,
                healthy=False,
            )
            metrics = {
                "available": False,
                "healthy": False,
                "reason": "api_observation_failed",
                "error_type": type(exc).__name__,
            }

        metrics["backend_replica_healthy"] = snapshot.healthy
        metrics["backend_replica_reason"] = snapshot.reason
        metrics["backend_replica_count"] = snapshot.current_replicas

        if not snapshot.healthy:
            observation = replace(observation, healthy=False)

        cycle = self._coordinator.run_cycle(
            observation,
            evaluated_at=evaluated_at,
            scale_in_drain_confirmed=scale_in_drain_confirmed,
        )

        reason = (
            snapshot.reason
            if not snapshot.healthy
            else cycle.reason
        )

        return APIBackendAutoscalingCycleResult(
            replicas=snapshot,
            metrics=metrics,
            cycle=cycle,
            reason=reason,
        )
