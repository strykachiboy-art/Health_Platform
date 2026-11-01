from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.autoscaling.nginx_routing import (
    RouteOperationResult,
)
from app.core.autoscaling.self_healing import (
    ComposeBackendSelfHealer,
    SelfHealingStatus,
)


PROJECT = "clinic-system-phase9"


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(
        ["docker", "test"],
        returncode,
        stdout,
        "",
    )


def row(
    index,
    *,
    instance_id=None,
    state="running",
    status="Up 1 minute (healthy)",
):
    identity = instance_id or f"{index:012x}"
    return (
        f"{identity}|{PROJECT}-backend-{index}|"
        f"{state}|{status}"
    )


def inventory(*rows):
    return "\n".join(rows)


class ScriptedRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if not self.replies:
            raise AssertionError(
                f"unexpected command: {command!r}"
            )
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class FakeRouting:
    project_name = PROJECT

    def __init__(
        self,
        tmp_path: Path,
        *,
        replica_count=3,
        confirmed=True,
        quarantined=(),
    ):
        self.confirmed = confirmed
        self.verify_calls = []
        self.quarantine_calls = []
        self.restore_calls = []
        self._quarantined = set(quarantined)
        self.upstream_file = (
            tmp_path
            / "runtime"
            / "phase9"
            / "nginx-upstream"
            / "backend.conf"
        )
        self.upstream_file.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.upstream_file.write_text(
            "".join(
                (
                    f"    server {PROJECT}-backend-{index}:5000 "
                    "resolve max_fails=1 fail_timeout=10s;\n"
                )
                for index in range(
                    1,
                    replica_count + 1,
                )
            ),
            encoding="utf-8",
        )

    def verify_replicas(self, count):
        self.verify_calls.append(count)
        confirmed = self.confirmed and not self._quarantined
        return RouteOperationResult(
            confirmed,
            None,
            "route_set_verified" if confirmed else "route_set_mismatch",
            0 if confirmed else 1,
        )

    def quarantined_backends(self):
        return tuple(sorted(self._quarantined))

    def quarantine(self, container_name):
        self.quarantine_calls.append(container_name)
        if not self.confirmed:
            return RouteOperationResult(
                False, True, "route_quarantine_failed", 1
            )
        if self._quarantined and container_name not in self._quarantined:
            return RouteOperationResult(
                False, True, "another_backend_already_quarantined", 1
            )
        self._quarantined.add(container_name)
        return RouteOperationResult(
            True, None, "route_quarantine_confirmed", 0
        )

    def restore_quarantined(self, container_name):
        self.restore_calls.append(container_name)
        if not self.confirmed:
            return RouteOperationResult(
                False, True, "route_quarantine_restore_failed", 1
            )
        self._quarantined.discard(container_name)
        return RouteOperationResult(
            True, None, "route_quarantine_restored", 0
        )


def make_healer(
    tmp_path,
    runner,
    *,
    routing=None,
    owned=True,
    readiness=True,
):
    selected_routing = routing or FakeRouting(tmp_path)
    return ComposeBackendSelfHealer(
        project_root=tmp_path,
        project_name=PROJECT,
        min_replicas=2,
        max_replicas=4,
        routing_adapter=selected_routing,
        ownership_guard=(
            owned
            if callable(owned)
            else lambda: owned
        ),
        readiness_probe=(
            readiness
            if callable(readiness)
            else lambda _survivor, _target: readiness
        ),
        runner=runner,
    )


def test_healthy_fleet_is_noop_without_mutation(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(2),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.NOOP
    assert result.healthy_replicas == 3
    assert len(runner.calls) == 1


def test_health_starting_waits_without_replacement(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        status=(
                            "Up 2 seconds "
                            "(health: starting)"
                        ),
                    ),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.WAITING
    assert len(runner.calls) == 1


def test_exited_backend_is_removed_recreated_and_verified(
    tmp_path,
):
    old_id = "a" * 12
    new_id = "d" * 12
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id=old_id,
                        state="exited",
                        status="Exited (1) 5 seconds ago",
                    ),
                    row(3),
                )
            ),
            completed(),
            completed(),
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id=new_id,
                    ),
                    row(3),
                )
            ),
        ]
    )
    routing = FakeRouting(tmp_path)
    result = make_healer(
        tmp_path,
        runner,
        routing=routing,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REPAIRED
    assert result.target_backend == (
        f"{PROJECT}-backend-2"
    )
    assert result.previous_instance_id == old_id
    assert result.replacement_instance_id == new_id
    assert routing.verify_calls == [3, 3]
    assert runner.calls[1][0] == [
        "docker",
        "rm",
        "-f",
        f"{PROJECT}-backend-2",
    ]
    assert runner.calls[2][0][-11:] == [
        "up",
        "-d",
        "--no-deps",
        "--no-recreate",
        "--no-build",
        "--wait",
        "--wait-timeout",
        "60",
        "--scale",
        "backend=3",
        "backend",
    ]


def test_missing_backend_is_recreated_without_remove(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(3),
                )
            ),
            completed(),
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="f" * 12,
                    ),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REPAIRED
    assert result.previous_instance_id is None
    assert not any(
        command[:3]
        == ["docker", "rm", "-f"]
        for command, _kwargs in runner.calls
    )


def test_unhealthy_backend_is_replaced(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="a" * 12,
                        status="Up 1 minute (unhealthy)",
                    ),
                    row(3),
                )
            ),
            completed(),
            completed(),
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="b" * 12,
                    ),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REPAIRED


def test_route_mismatch_refuses_before_mutation(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(3),
                )
            ),
        ]
    )
    routing = FakeRouting(
        tmp_path,
        confirmed=False,
    )
    result = make_healer(
        tmp_path,
        runner,
        routing=routing,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REFUSED
    assert result.reason == (
        "route_set_not_safe_for_self_heal"
    )
    assert len(runner.calls) == 1


def test_lease_loss_before_mutation_refuses(tmp_path):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
        owned=False,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REFUSED
    assert len(runner.calls) == 1


def test_lease_loss_after_removal_is_uncertain(tmp_path):
    ownership = iter([True, True, False])
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(3),
                )
            ),
            completed(),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
        owned=lambda: next(ownership, False),
    ).reconcile_once()

    assert result.status is SelfHealingStatus.UNCERTAIN
    assert result.reason == (
        "controller_lease_lost_after_"
        "failed_backend_removal"
    )


def test_no_healthy_survivor_refuses_multi_replica_repair(
    tmp_path,
):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(
                        1,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(
                        2,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(
                        3,
                        state="exited",
                        status="Exited (1)",
                    ),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REFUSED
    assert result.reason == (
        "no_healthy_backend_survivor_for_safe_repair"
    )


def test_timeout_with_verified_replacement_is_repaired(
    tmp_path,
):
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="a" * 12,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(3),
                )
            ),
            completed(),
            subprocess.TimeoutExpired(
                ["docker", "compose"],
                120,
            ),
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="b" * 12,
                    ),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
    ).reconcile_once()

    assert result.status is SelfHealingStatus.REPAIRED
    assert result.reason == (
        "backend_replacement_verified_after_compose_error_quarantine_restored"
    )

def test_network_readiness_pending_keeps_replacement_quarantined(
    tmp_path,
):
    routing = FakeRouting(tmp_path, replica_count=3)
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(
                        2,
                        instance_id="a" * 12,
                        state="exited",
                        status="Exited (1)",
                    ),
                    row(3),
                )
            ),
            completed(),
            completed(),
            completed(
                inventory(
                    row(1),
                    row(2, instance_id="b" * 12),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
        routing=routing,
        readiness=False,
    ).reconcile_once()
    assert result.status is SelfHealingStatus.WAITING
    assert result.reason == (
        "replacement_network_readiness_pending_route_quarantined"
    )
    assert routing.quarantined_backends() == (
        f"{PROJECT}-backend-2",
    )
    assert routing.restore_calls == []


def test_resumed_healthy_quarantine_restores_without_replacing_again(
    tmp_path,
):
    target = f"{PROJECT}-backend-2"
    routing = FakeRouting(
        tmp_path,
        replica_count=3,
        quarantined=(target,),
    )
    runner = ScriptedRunner(
        [
            completed(
                inventory(
                    row(1),
                    row(2, instance_id="b" * 12),
                    row(3),
                )
            ),
        ]
    )
    result = make_healer(
        tmp_path,
        runner,
        routing=routing,
        readiness=True,
    ).reconcile_once()
    assert result.status is SelfHealingStatus.REPAIRED
    assert result.reason == (
        "quarantined_backend_reactivated_after_network_readiness"
    )
    assert routing.quarantined_backends() == ()
    assert routing.restore_calls == [target]
    assert len(runner.calls) == 1
