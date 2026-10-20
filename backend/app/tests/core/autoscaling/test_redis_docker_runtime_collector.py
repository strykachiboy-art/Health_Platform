from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest

from app.core.autoscaling.redis_docker_runtime_collector import (
    DockerRedisRuntimeCollectorConfig,
    DockerRedisRuntimeSnapshotCollector,
)


CONTAINER = "clinic-system-phase9-redis-1"
PROJECT = "clinic-system-phase9"
SERVICE = "redis"
IMAGE = "redis:8"
NOW = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

MEMINFO = """\
MemTotal:        3967600 kB
MemFree:          141028 kB
MemAvailable:    1717932 kB
SwapTotal:             0 kB
SwapFree:              0 kB
"""

PERSISTENCE = """\
# Persistence
loading:0
async_loading:0
aof_enabled:1
aof_last_write_status:ok
"""


class FakeDockerRunner:
    def __init__(self, **overrides):
        self.calls = []
        self.overrides = dict(overrides)

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append((command, dict(kwargs)))

        key = self._classify(command)
        if key is None:
            return subprocess.CompletedProcess(
                command, 97, "", "private unrecognized-command detail"
            )

        defaults = {
            "daemon_info": "4062822400\n",
            "inspect_identity": f"/{CONTAINER}|{IMAGE}\n",
            "inspect_labels": f"{PROJECT}|{SERVICE}\n",
            "inspect_state": "running|true|false|0|healthy\n",
            "inspect_memory_limit": "0\n",
            "inspect_data_mount": "volume|true\n",
            "proc_meminfo": MEMINFO,
            "cgroup_v2_current": "68886528\n",
            "cgroup_v2_limit": "max\n",
            "cgroup_v1_current": "68886528\n",
            "cgroup_v1_limit": "9223372036854771712\n",
            "redis_persistence": PERSISTENCE,
        }
        spec = self.overrides.get(key, (0, defaults[key]))
        returncode, stdout = spec

        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout,
            "private command diagnostic must not escape",
        )

    @staticmethod
    def _classify(command):
        if len(command) >= 2 and command[1] == "info":
            return "daemon_info"

        if len(command) >= 2 and command[1] == "inspect":
            template = command[command.index("--format") + 1]
            if template == "{{.Name}}|{{.Config.Image}}":
                return "inspect_identity"
            if template == (
                '{{index .Config.Labels "com.docker.compose.project"}}|'
                '{{index .Config.Labels "com.docker.compose.service"}}'
            ):
                return "inspect_labels"
            if "RestartCount" in template:
                return "inspect_state"
            if template == "{{.HostConfig.Memory}}":
                return "inspect_memory_limit"
            if "/data" in template:
                return "inspect_data_mount"
            return None

        if len(command) >= 4 and command[1] == "exec":
            if command[3] == "cat":
                path = command[4]
                return {
                    "/proc/meminfo": "proc_meminfo",
                    "/sys/fs/cgroup/memory.current": "cgroup_v2_current",
                    "/sys/fs/cgroup/memory.max": "cgroup_v2_limit",
                    "/sys/fs/cgroup/memory/memory.usage_in_bytes": "cgroup_v1_current",
                    "/sys/fs/cgroup/memory/memory.limit_in_bytes": "cgroup_v1_limit",
                }.get(path)

            if (
                command[3] == "redis-cli"
                and command[4:] == ["--raw", "INFO", "persistence"]
            ):
                return "redis_persistence"

        return None


def collector(runner=None, *, clock=lambda: NOW, **config_overrides):
    if runner is None:
        runner = FakeDockerRunner()

    settings = {
        "container_name": CONTAINER,
        "compose_project": PROJECT,
        "compose_service": SERVICE,
        "expected_image": IMAGE,
        "command_timeout_seconds": 5,
    }
    settings.update(config_overrides)

    return DockerRedisRuntimeSnapshotCollector(
        config=DockerRedisRuntimeCollectorConfig(**settings),
        runner=runner,
        clock=clock,
    )


def test_collects_fresh_sanitized_runtime_snapshot():
    runner = FakeDockerRunner()
    result = collector(runner). __call__()

    assert result["healthy"] is True
    assert result["reason"] == "ok"
    assert result["container_name"] == CONTAINER
    assert result["observed_image"] == IMAGE
    assert result["compose_project"] == PROJECT
    assert result["compose_service"] == SERVICE
    assert result["container_restart_count"] == 0
    assert result["docker_daemon_memory_total_bytes"] == 4_062_822_400
    assert result["linux_memory_total_bytes"] == 3_967_600 * 1024
    assert result["linux_memory_available_bytes"] == 1_717_932 * 1024
    assert result["redis_container_memory_current_bytes"] == 68_886_528
    assert result["redis_container_memory_limit_bytes"] == 0
    assert result["docker_configured_memory_limit_bytes"] == 0
    assert result["aof_enabled"] is True
    assert result["aof_last_write_ok"] is True
    assert result["data_volume_mounted"] is True
    assert datetime.fromisoformat(result["timestamp"]).utcoffset().total_seconds() == 0
    assert "private command diagnostic must not escape" not in repr(result)


@pytest.mark.parametrize(
    "key,value,expected_reason",
    [
        ("inspect_identity", (0, "/unexpected-redis|redis:8"), "runtime_container_identity_mismatch"),
        ("inspect_identity", (0, f"/{CONTAINER}|redis:7"), "runtime_image_mismatch"),
        ("inspect_labels", (0, "another-project|redis"), "runtime_compose_project_mismatch"),
        ("inspect_labels", (0, f"{PROJECT}|another-service"), "runtime_compose_service_mismatch"),
        ("inspect_state", (0, "exited|false|false|0|unhealthy"), "runtime_container_not_running"),
        ("inspect_state", (0, "running|true|true|0|healthy"), "runtime_container_oom_killed"),
        ("inspect_state", (0, "running|true|false|0|starting"), "runtime_container_unhealthy"),
        ("inspect_state", (0, "running|true|false|bad|healthy"), "invalid_container_restart_count"),
        ("inspect_memory_limit", (0, "-1"), "invalid_configured_container_memory_limit"),
        ("inspect_data_mount", (0, ""), "redis_data_volume_missing_or_not_writable"),
        ("inspect_data_mount", (0, "volume|false"), "redis_data_volume_missing_or_not_writable"),
        ("inspect_data_mount", (0, "bind|true"), "redis_data_volume_missing_or_not_writable"),
        ("daemon_info", (0, "not-a-number"), "invalid_docker_daemon_memory"),
        ("daemon_info", (0, "0"), "invalid_docker_daemon_memory"),
        ("proc_meminfo", (0, "MemTotal: 100 kB\n"), "invalid_linux_memory_metrics"),
        ("proc_meminfo", (0, "MemTotal: 100 kB\nMemAvailable: 101 kB\n"), "linux_available_memory_exceeds_total"),
        ("cgroup_v2_current", (0, "not-a-number"), "invalid_cgroup_memory_usage"),
        ("cgroup_v2_limit", (0, "zero"), "invalid_cgroup_memory_limit"),
        ("redis_persistence", (0, "# Persistence\naof_enabled:0\naof_last_write_status:ok\n"), "redis_aof_disabled"),
        ("redis_persistence", (0, "# Persistence\naof_enabled:1\naof_last_write_status:err\n"), "redis_aof_write_unhealthy"),
        ("redis_persistence", (0, "# Persistence\nloading:0\n"), "redis_aof_disabled"),
    ],
)
def test_invalid_runtime_evidence_fails_closed(key, value, expected_reason):
    result = collector(FakeDockerRunner(**{key: value}))()

    assert result["healthy"] is False
    assert result["reason"] == expected_reason
    assert "private command diagnostic must not escape" not in repr(result)


def test_cgroup_v1_fallback_is_supported():
    runner = FakeDockerRunner(
        cgroup_v2_current=(1, ""),
        cgroup_v1_current=(0, "70000000"),
        cgroup_v2_limit=(1, ""),
        cgroup_v1_limit=(0, "9223372036854771712"),
    )

    result = collector(runner)()

    assert result["healthy"] is True
    assert result["redis_container_memory_current_bytes"] == 70_000_000
    assert result["redis_container_memory_limit_bytes"] == 0


def test_finite_cgroup_limit_becomes_effective_limit():
    runner = FakeDockerRunner(
        cgroup_v2_limit=(0, "500000000"),
        inspect_memory_limit=(0, "600000000"),
    )

    result = collector(runner)()

    assert result["healthy"] is True
    assert result["redis_container_memory_limit_bytes"] == 500_000_000


def test_tighter_docker_limit_wins_over_cgroup_limit():
    runner = FakeDockerRunner(
        cgroup_v2_limit=(0, "600000000"),
        inspect_memory_limit=(0, "300000000"),
    )

    result = collector(runner)()

    assert result["healthy"] is True
    assert result["redis_container_memory_limit_bytes"] == 300_000_000


def test_effective_limit_cannot_be_below_current_usage():
    runner = FakeDockerRunner(
        cgroup_v2_limit=(0, "60000000"),
        inspect_memory_limit=(0, "70000000"),
    )

    result = collector(runner)()

    assert result["healthy"] is False
    assert result["reason"] == "container_usage_exceeds_effective_memory_limit"


def test_failed_docker_command_does_not_leak_stderr():
    result = collector(
        FakeDockerRunner(daemon_info=(1, "private output"))
    )()

    assert result["healthy"] is False
    assert result["reason"] == "docker_daemon_memory_query_failed"
    assert result["error_type"] == "CommandFailed"
    assert "private output" not in repr(result)
    assert "private command diagnostic must not escape" not in repr(result)


def test_runner_exception_is_sanitized():
    def broken_runner(command, **kwargs):
        raise RuntimeError("private docker endpoint or credential")

    result = collector(broken_runner)()

    assert result["healthy"] is False
    assert result["reason"] == "docker_daemon_memory_query_failed"
    assert result["error_type"] == "RuntimeError"
    assert "private docker endpoint or credential" not in repr(result)


def test_command_timeout_fails_closed():
    def timeout_runner(command, **kwargs):
        raise subprocess.TimeoutExpired(
            command, kwargs.get("timeout"), output="private timeout output"
        )

    result = collector(timeout_runner)()

    assert result["healthy"] is False
    assert result["reason"] == "docker_daemon_memory_query_failed"
    assert result["error_type"] == "TimeoutExpired"
    assert "private timeout output" not in repr(result)


def test_invalid_clock_fails_closed_without_running_commands():
    runner = FakeDockerRunner()
    result = collector(
        runner,
        clock=lambda: datetime(2026, 10, 20, 10, 0),
    )()

    assert result["healthy"] is False
    assert result["reason"] == "invalid_collection_clock"
    assert runner.calls == []


def test_clock_exception_is_sanitized():
    def broken_clock():
        raise RuntimeError("private clock diagnostic")

    result = collector(clock=broken_clock)()

    assert result["healthy"] is False
    assert result["reason"] == "collection_clock_failed"
    assert result["error_type"] == "RuntimeError"
    assert "private clock diagnostic" not in repr(result)


def test_commands_are_read_only_and_never_use_a_shell():
    runner = FakeDockerRunner()
    result = collector(runner)()

    assert result["healthy"] is True
    assert runner.calls

    for command, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 5.0
        assert command[1] in {"info", "inspect", "exec"}
        if command[1] == "exec":
            assert command[3] in {"cat", "redis-cli"}


@pytest.mark.parametrize(
    "overrides",
    [
        {"container_name": ""},
        {"container_name": "--help"},
        {"compose_project": ""},
        {"compose_service": ""},
        {"expected_image": ""},
        {"command_timeout_seconds": 0},
        {"command_timeout_seconds": -1},
        {"command_timeout_seconds": True},
        {"command_timeout_seconds": float("nan")},
        {"command_timeout_seconds": float("inf")},
        {"docker_binary": ""},
    ],
)
def test_invalid_collector_configuration_is_rejected(overrides):
    values = {
        "container_name": CONTAINER,
        "compose_project": PROJECT,
        "compose_service": SERVICE,
        "expected_image": IMAGE,
        "command_timeout_seconds": 5,
        "docker_binary": "docker",
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        DockerRedisRuntimeCollectorConfig(**values)


def test_constructor_rejects_invalid_dependencies():
    config = DockerRedisRuntimeCollectorConfig(
        container_name=CONTAINER,
        compose_project=PROJECT,
        compose_service=SERVICE,
        expected_image=IMAGE,
    )

    with pytest.raises(TypeError):
        DockerRedisRuntimeSnapshotCollector(config=None)

    with pytest.raises(TypeError):
        DockerRedisRuntimeSnapshotCollector(config=config, runner=None)

    with pytest.raises(TypeError):
        DockerRedisRuntimeSnapshotCollector(config=config, clock=42)