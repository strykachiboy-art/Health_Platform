"""Read-only Docker runtime collector for the Redis capacity pipeline.

Only explicitly selected Docker metadata is inspected. No container environment,
raw command output, or error message is included in returned snapshots.
This collector does not modify Docker, Redis, or persistence configuration.
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable


_IDENTITY_TEMPLATE = "{{.Name}}|{{.Config.Image}}"
_LABELS_TEMPLATE = (
    '{{index .Config.Labels "com.docker.compose.project"}}|'
    '{{index .Config.Labels "com.docker.compose.service"}}'
)
_STATE_TEMPLATE = (
    "{{.State.Status}}|{{.State.Running}}|{{.State.OOMKilled}}|"
    "{{.RestartCount}}|"
    "{{if .State.Health}}{{.State.Health.Status}}"
    "{{else}}not_configured{{end}}"
)
_MEMORY_LIMIT_TEMPLATE = "{{.HostConfig.Memory}}"
_DATA_MOUNT_TEMPLATE = (
    '{{range .Mounts}}{{if eq .Destination "/data"}}'
    "{{.Type}}|{{.RW}}{{end}}{{end}}"
)

_MEMINFO_PATTERN = re.compile(
    r"(?m)^(MemTotal|MemAvailable):[ \t]+([0-9]+)[ \t]+kB[ \t]*$"
)
_CONTAINER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class _CollectionFailure(Exception):
    def __init__(self, reason: str, error_type: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error_type = error_type


@dataclass(frozen=True, slots=True)
class DockerRedisRuntimeCollectorConfig:
    """Explicit identity checks for the Redis Compose deployment."""

    container_name: str
    compose_project: str
    compose_service: str
    expected_image: str
    command_timeout_seconds: float = 5.0
    docker_binary: str = "docker"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.container_name, str)
            or not _CONTAINER_NAME_PATTERN.fullmatch(self.container_name)
        ):
            raise ValueError("container_name is invalid")

        for name in (
            "compose_project",
            "compose_service",
            "expected_image",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(char in value for char in "\r\n|")
            ):
                raise ValueError(f"{name} must be a non-empty safe identifier")

        if (
            not isinstance(self.docker_binary, str)
            or not self.docker_binary.strip()
            or "\x00" in self.docker_binary
        ):
            raise ValueError("docker_binary must be a non-empty executable path")

        timeout = self.command_timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError("command_timeout_seconds must be finite and > 0")

        try:
            normalized_timeout = float(timeout)
        except (OverflowError, TypeError, ValueError):
            raise ValueError(
                "command_timeout_seconds must be finite and > 0"
            ) from None

        if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
            raise ValueError("command_timeout_seconds must be finite and > 0")

        object.__setattr__(
            self, "command_timeout_seconds", normalized_timeout
        )


class DockerRedisRuntimeSnapshotCollector:
    """Collect a timestamped and sanitized Redis runtime snapshot.

    The runner is injectable for deterministic tests. The default runner uses
    subprocess with argument lists, a finite timeout, and shell=False.
    Every executed Docker subcommand is limited to inspection or read-only
    container commands.
    """

    def __init__(
        self,
        *,
        config: DockerRedisRuntimeCollectorConfig,
        runner: Callable[..., Any] = subprocess.run,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(config, DockerRedisRuntimeCollectorConfig):
            raise TypeError(
                "config must be a DockerRedisRuntimeCollectorConfig"
            )
        if not callable(runner):
            raise TypeError("runner must be callable")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        self.config = config
        self._runner = runner
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def __call__(self) -> dict[str, Any]:
        """Return a runtime snapshot, or a sanitized unhealthy result."""
        try:
            sampled_at = self._clock()
        except Exception as exc:
            return self._failure(
                None,
                "collection_clock_failed",
                type(exc).__name__,
            )

        if not _aware_datetime(sampled_at):
            return self._failure(None, "invalid_collection_clock")

        timestamp = sampled_at.astimezone(timezone.utc).isoformat()

        try:
            return self._collect(timestamp)
        except _CollectionFailure as exc:
            return self._failure(
                timestamp,
                exc.reason,
                exc.error_type,
            )
        except Exception as exc:
            # Never forward arbitrary exception text or command diagnostics.
            return self._failure(
                timestamp,
                "runtime_collection_failed",
                type(exc).__name__,
            )

    def _collect(self, timestamp: str) -> dict[str, Any]:
        config = self.config
        docker = config.docker_binary
        container = config.container_name

        daemon_text = self._run_text(
            [docker, "info", "--format", "{{.MemTotal}}"],
            reason="docker_daemon_memory_query_failed",
        )
        daemon_memory = _parse_non_negative_integer(daemon_text)
        if daemon_memory is None or daemon_memory <= 0:
            raise _CollectionFailure("invalid_docker_daemon_memory")

        identity_text = self._inspect(_IDENTITY_TEMPLATE)
        identity_parts = identity_text.split("|")
        if len(identity_parts) != 2:
            raise _CollectionFailure("invalid_runtime_container_identity")

        observed_name = identity_parts[0].strip().lstrip("/")
        observed_image = identity_parts[1].strip()

        if observed_name != container:
            raise _CollectionFailure("runtime_container_identity_mismatch")
        if observed_image != config.expected_image:
            raise _CollectionFailure("runtime_image_mismatch")

        labels_text = self._inspect(_LABELS_TEMPLATE)
        labels = labels_text.split("|")
        if len(labels) != 2:
            raise _CollectionFailure("invalid_runtime_compose_labels")
        if labels[0].strip() != config.compose_project:
            raise _CollectionFailure("runtime_compose_project_mismatch")
        if labels[1].strip() != config.compose_service:
            raise _CollectionFailure("runtime_compose_service_mismatch")

        state_text = self._inspect(_STATE_TEMPLATE)
        state = state_text.split("|")
        if len(state) != 5:
            raise _CollectionFailure("invalid_runtime_container_state")

        status, running, oom_killed, restart_text, health_status = (
            value.strip() for value in state
        )

        if status != "running" or running.lower() != "true":
            raise _CollectionFailure("runtime_container_not_running")
        if oom_killed.lower() != "false":
            raise _CollectionFailure("runtime_container_oom_killed")
        if health_status.lower() != "healthy":
            raise _CollectionFailure("runtime_container_unhealthy")

        restart_count = _parse_non_negative_integer(restart_text)
        if restart_count is None:
            raise _CollectionFailure("invalid_container_restart_count")

        configured_limit_text = self._inspect(_MEMORY_LIMIT_TEMPLATE)
        configured_limit = _parse_non_negative_integer(
            configured_limit_text
        )
        if configured_limit is None:
            raise _CollectionFailure("invalid_configured_container_memory_limit")

        mount_text = self._inspect(_DATA_MOUNT_TEMPLATE)
        if mount_text.strip() != "volume|true":
            raise _CollectionFailure("redis_data_volume_missing_or_not_writable")

        meminfo_text = self._run_text(
            [docker, "exec", container, "cat", "/proc/meminfo"],
            reason="linux_memory_query_failed",
        )
        linux_memory = _parse_meminfo(meminfo_text)
        if linux_memory is None:
            raise _CollectionFailure("invalid_linux_memory_metrics")

        linux_total, linux_available = linux_memory
        if linux_available > linux_total:
            raise _CollectionFailure("linux_available_memory_exceeds_total")

        # Read cgroup v2 first, then use cgroup v1 only if that path is
        # genuinely unavailable. A readable but malformed value fails closed.
        cgroup_current_text = self._run_text(
            [
                docker,
                "exec",
                container,
                "cat",
                "/sys/fs/cgroup/memory.current",
            ],
            reason="cgroup_memory_usage_query_failed",
            allow_nonzero=True,
        )

        cgroup_v2 = cgroup_current_text is not None

        if cgroup_v2:
            current_text = cgroup_current_text
            limit_text = self._run_text(
                [
                    docker,
                    "exec",
                    container,
                    "cat",
                    "/sys/fs/cgroup/memory.max",
                ],
                reason="cgroup_memory_limit_query_failed",
                allow_nonzero=True,
            )
            if limit_text is None:
                raise _CollectionFailure("inconsistent_cgroup_version")
            cgroup_limit = _parse_cgroup_v2_limit(limit_text)
        else:
            current_text = self._run_text(
                [
                    docker,
                    "exec",
                    container,
                    "cat",
                    "/sys/fs/cgroup/memory/memory.usage_in_bytes",
                ],
                reason="cgroup_memory_usage_query_failed",
            )
            limit_text = self._run_text(
                [
                    docker,
                    "exec",
                    container,
                    "cat",
                    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
                ],
                reason="cgroup_memory_limit_query_failed",
            )
            cgroup_limit = _parse_cgroup_v1_limit(limit_text)

        current_container_memory = _parse_non_negative_integer(current_text)
        if current_container_memory is None:
            raise _CollectionFailure("invalid_cgroup_memory_usage")

        # The effective limit is the tighter positive limit from Docker's
        # HostConfig and the container's own cgroup. Zero means unbounded.
        positive_limits = [
            value
            for value in (configured_limit, cgroup_limit)
            if value is not None and value > 0
        ]
        effective_limit = min(positive_limits) if positive_limits else 0

        if (
            effective_limit > 0
            and current_container_memory > effective_limit
        ):
            raise _CollectionFailure(
                "container_usage_exceeds_effective_memory_limit"
            )

        persistence_text = self._run_text(
            [
                docker,
                "exec",
                container,
                "redis-cli",
                "--raw",
                "INFO",
                "persistence",
            ],
            reason="redis_persistence_query_failed",
        )
        persistence = _parse_redis_info(persistence_text)

        aof_enabled = _parse_non_negative_integer(
            persistence.get("aof_enabled")
        )
        if aof_enabled != 1:
            raise _CollectionFailure("redis_aof_disabled")

        if persistence.get("aof_last_write_status", "").lower() != "ok":
            raise _CollectionFailure("redis_aof_write_unhealthy")

        return {
            "timestamp": timestamp,
            "healthy": True,
            "reason": "ok",
            "container_name": container,
            "observed_image": observed_image,
            "compose_project": config.compose_project,
            "compose_service": config.compose_service,
            "container_restart_count": restart_count,
            "docker_daemon_memory_total_bytes": daemon_memory,
            "linux_memory_total_bytes": linux_total,
            "linux_memory_available_bytes": linux_available,
            "redis_container_memory_current_bytes": (
                current_container_memory
            ),
            "redis_container_memory_limit_bytes": effective_limit,
            "docker_configured_memory_limit_bytes": configured_limit,
            "aof_enabled": True,
            "aof_last_write_ok": True,
            "data_volume_mounted": True,
        }

    def _inspect(self, template: str) -> str:
        return self._run_text(
            [
                self.config.docker_binary,
                "inspect",
                "--type=container",
                "--format",
                template,
                self.config.container_name,
            ],
            reason="docker_container_inspection_failed",
        )

    def _run_text(
        self,
        command: list[str],
        *,
        reason: str,
        allow_nonzero: bool = False,
    ) -> str | None:
        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.config.command_timeout_seconds,
                shell=False,
            )
        except Exception as exc:
            raise _CollectionFailure(reason, type(exc).__name__) from None

        return_code = getattr(result, "returncode", None)
        if type(return_code) is not int:
            raise _CollectionFailure(reason, "InvalidCommandResult")

        if return_code != 0:
            if allow_nonzero:
                return None
            raise _CollectionFailure(reason, "CommandFailed")

        stdout = getattr(result, "stdout", None)
        if not isinstance(stdout, str):
            raise _CollectionFailure(reason, "InvalidCommandOutput")

        return stdout.strip()

    @staticmethod
    def _failure(
        timestamp: str | None,
        reason: str,
        error_type: str | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "healthy": False,
            "reason": reason,
        }
        if timestamp is not None:
            result["timestamp"] = timestamp
        if error_type is not None:
            result["error_type"] = error_type
        return result


def _aware_datetime(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _parse_non_negative_integer(value: object) -> int | None:
    if type(value) is int:
        return value if value >= 0 else None
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if (
        not normalized
        or not normalized.isascii()
        or not normalized.isdecimal()
    ):
        return None
    return int(normalized)


def _parse_meminfo(text: str) -> tuple[int, int] | None:
    matches = {
        match.group(1): int(match.group(2)) * 1024
        for match in _MEMINFO_PATTERN.finditer(text)
    }
    total = matches.get("MemTotal")
    available = matches.get("MemAvailable")

    if total is None or total <= 0 or available is None or available < 0:
        return None
    return total, available


def _parse_cgroup_v2_limit(text: str) -> int | None:
    normalized = text.strip().lower()
    if normalized == "max":
        return None

    value = _parse_non_negative_integer(normalized)
    if value is None or value == 0:
        raise _CollectionFailure("invalid_cgroup_memory_limit")
    return value


def _parse_cgroup_v1_limit(text: str) -> int | None:
    value = _parse_non_negative_integer(text)
    if value is None or value == 0:
        raise _CollectionFailure("invalid_cgroup_memory_limit")

    # The legacy cgroup v1 unbounded-limit sentinel is near 2^63.
    if value >= (1 << 60):
        return None
    return value


def _parse_redis_info(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key and value:
            parsed[key.strip()] = value.strip()

    return parsed