from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.autoscaling.compose_adapter import ScalePreviewStatus
from app.core.autoscaling.decision_engine import ScalingAction, ScalingDecision
from app.core.autoscaling.live_actuator import (
    ComposeLiveScalingActuator,
    DockerExecBackendDrainAdapter,
    DrainOperationResult,
    ScaleActuationResult,
    ScaleActuationStatus,
)
from app.core.autoscaling.nginx_routing import RouteOperationResult


PROJECT = "clinic-system"
IMAGE = "clinic-system-phase9:wave1-local-20261021-r1"


def decision(action=ScalingAction.SCALE_OUT, current=3, target=4):
    return ScalingDecision(
        action, current, target, "test",
        0.95 if action is ScalingAction.SCALE_OUT else 0.1,
    )


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(["docker", "test"], returncode, stdout, "")


def inventory(count, *, bad_index=None, image=IMAGE):
    rows = []
    for i in range(1, count + 1):
        status = "Up 1 minute (healthy)"
        if i == bad_index:
            status = "Up 1 minute (unhealthy)"
        rows.append(f"{PROJECT}-backend-{i}|{image}|running|{status}")
    return "\n".join(rows)


def config_reply(image=IMAGE):
    return completed(json.dumps({"services": {"backend": {"image": image}}}))


class FakePreview:
    def __init__(self, tmp_path, status=ScalePreviewStatus.DRY_RUN_ACCEPTED):
        self.config = SimpleNamespace(
            project_root=Path(tmp_path).resolve(),
            project_name=PROJECT, min_replicas=2, max_replicas=5,
        )
        self.project_name = PROJECT
        self.status = status
        self.calls = []

    def preview(self, decision, *, scale_in_drain_confirmed=False):
        self.calls.append((decision, scale_in_drain_confirmed))
        return SimpleNamespace(
            status=self.status,
            reason="dry_run_validated_no_infrastructure_change",
            observed_running_replicas=decision.current_replicas,
            exit_code=0,
        )


class FakeDrain:
    project_name = PROJECT

    def __init__(self, *, confirmed=True, restored=True, events=None):
        self.confirmed, self.restored = confirmed, restored
        self.drain_calls, self.undrain_calls = [], []
        self.events = events if events is not None else []

    def drain_and_confirm(self, name):
        self.drain_calls.append(name)
        self.events.append(("drain", name))
        return DrainOperationResult(
            self.confirmed, self.restored, "test", 0 if self.confirmed else 1,
        )

    def undrain(self, name):
        self.undrain_calls.append(name)
        self.events.append(("undrain", name))
        return self.restored


class FakeRouting:
    project_name = PROJECT

    def __init__(self, *, confirmed=True, restored=True, verify_confirmed=True, events=None):
        self.confirmed = confirmed
        self.restored = restored
        self.verify_confirmed = verify_confirmed
        self.withdraw_calls, self.restore_calls = [], []
        self.verify_calls = []
        self.events = events if events is not None else []

    def withdraw(self, name):
        self.withdraw_calls.append(name)
        self.events.append(("withdraw", name))
        return RouteOperationResult(
            self.confirmed, self.restored, "test", 0 if self.confirmed else 1
        )

    def restore(self, name):
        self.restore_calls.append(name)
        self.events.append(("restore_routes", name))
        return RouteOperationResult(
            self.confirmed, None, "test", 0 if self.confirmed else 1
        )

    def verify_replicas(self, replica_count):
        self.verify_calls.append(replica_count)
        self.events.append(("verify_routes", replica_count))
        return RouteOperationResult(
            self.verify_confirmed, None,
            "route_set_verified" if self.verify_confirmed else "route_set_mismatch",
            0 if self.verify_confirmed else 1,
        )


class ScriptedRunner:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        if not self.replies:
            raise AssertionError("Unexpected subprocess call")
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def make_actuator(
    tmp_path, runner, *, enabled=True, drainer=None, routing=None,
    preview_status=ScalePreviewStatus.DRY_RUN_ACCEPTED,
    before_scale_command=None, scale_in_enabled=False,
):
    preview = FakePreview(tmp_path, preview_status)
    if routing is None:
        routing = FakeRouting()
    callback = before_scale_command
    if enabled and callback is None:
        callback = lambda: True
    actuator = ComposeLiveScalingActuator(
        preview,
        live_enabled=enabled,
        drain_adapter=drainer,
        scale_in_enabled=scale_in_enabled,
        routing_adapter=routing,
        before_scale_command=callback,
        runner=runner,
    )
    return actuator, preview


def test_refused_is_known_no_mutation_but_uncertain_is_not_acknowledgeable():
    refused = ScaleActuationResult(
        ScaleActuationStatus.REFUSED,
        "pre_action_state_not_safe",
        3,
        4,
    )
    uncertain = ScaleActuationResult(
        ScaleActuationStatus.UNCERTAIN,
        "post_action_state_ambiguous",
        3,
        4,
    )

    assert refused.outcome_known is True
    assert refused.succeeded is False
    assert refused.may_acknowledge_decision is True
    assert uncertain.outcome_known is False
    assert uncertain.may_acknowledge_decision is False


def test_live_actuation_is_disabled_by_default_without_commands(tmp_path):
    runner = ScriptedRunner([])
    preview = FakePreview(tmp_path)
    actuator = ComposeLiveScalingActuator(preview)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "live_actuation_disabled"
    assert runner.calls == [] and preview.calls == []


def test_scale_out_applies_only_after_target_is_healthy(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4)),
    ])
    actuator, preview = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.succeeded and result.may_acknowledge_decision
    assert result.observed_replicas == 4
    command = runner.calls[4][0]
    assert command[-8:] == [
        "up", "-d", "--no-deps", "--no-recreate",
        "--no-build", "--scale", "backend=4", "backend"
    ]
    assert preview.calls[0][1] is False


def test_scale_out_route_publication_failure_keeps_operation_uncertain(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4)),
    ])
    routing = FakeRouting(confirmed=False)
    actuator, _ = make_actuator(tmp_path, runner, routing=routing)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "target_state_verified_route_publication_unconfirmed"
    assert result.may_acknowledge_decision is False
    assert routing.restore_calls == [f"{PROJECT}-backend-4"]


def test_scale_out_is_refused_before_docker_when_route_adapter_is_missing(tmp_path):
    runner = ScriptedRunner([])
    preview = FakePreview(tmp_path)
    actuator = ComposeLiveScalingActuator(
        preview, live_enabled=True, before_scale_command=lambda: True, runner=runner,
    )

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "scale_out_routing_unavailable"
    assert runner.calls == []
    assert preview.calls == []


def test_pre_action_route_mismatch_refuses_scaling_without_mutation(tmp_path):
    runner = ScriptedRunner([config_reply(), completed(inventory(3))])
    routing = FakeRouting(verify_confirmed=False)
    actuator, _ = make_actuator(tmp_path, runner, routing=routing)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "pre_action_route_state_not_safe"
    assert routing.verify_calls == [3]
    assert not any("--scale" in command for command, _ in runner.calls)


def test_command_error_is_reconciled_against_actual_state(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(4)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.reason == "target_state_and_routes_verified_after_command_error"


def test_scale_command_timeout_keeps_scale_out_uncertain_when_old_inventory_is_seen(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        subprocess.TimeoutExpired(["docker", "compose", "up", "--scale"], 120),
        completed(inventory(3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "scale_command_execution_outcome_unknown"
    assert result.outcome_known is False
    assert result.may_acknowledge_decision is False
    assert sum(
        "--scale" in command and "--dry-run" not in command
        for command, _kwargs in runner.calls
    ) == 1


def test_scale_in_command_timeout_does_not_undrain_on_old_inventory_snapshot(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        subprocess.TimeoutExpired(["docker", "compose", "up", "--scale"], 120),
        completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, scale_in_enabled=True,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "scale_command_execution_outcome_unknown"
    assert result.may_acknowledge_decision is False
    assert drainer.drain_calls == [f"{PROJECT}-backend-3"]
    assert drainer.undrain_calls == []
    assert sum(
        "--scale" in command and "--dry-run" not in command
        for command, _kwargs in runner.calls
    ) == 1


def test_previous_state_after_command_failure_is_known_failure(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.FAILED
    assert result.outcome_known and result.may_acknowledge_decision


def test_unhealthy_target_remains_uncertain(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4, bad_index=4)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert not result.outcome_known and not result.may_acknowledge_decision


def test_scale_in_is_refused_by_default_before_any_inventory_or_drain_call(tmp_path):
    runner = ScriptedRunner([])
    drainer = FakeDrain()
    actuator, _ = make_actuator(tmp_path, runner, drainer=drainer)

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "scale_in_disabled"
    assert runner.calls == []
    assert drainer.drain_calls == []


def test_live_actuator_requires_a_durable_phase_callback_when_enabled(tmp_path):
    with pytest.raises(ValueError, match="durable scale-command phase callback"):
        ComposeLiveScalingActuator(FakePreview(tmp_path), live_enabled=True)


def test_scale_command_runs_only_after_durable_phase_callback(tmp_path):
    marker = {"confirmed": False}

    class MarkerCheckingRunner(ScriptedRunner):
        def __call__(self, command, **kwargs):
            if "--scale" in command and "--dry-run" not in command:
                assert marker["confirmed"] is True
            return super().__call__(command, **kwargs)

    runner = MarkerCheckingRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(4)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner,
        before_scale_command=lambda: marker.__setitem__("confirmed", True) or True,
    )

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.APPLIED
    assert marker["confirmed"] is True


def test_unconfirmed_durable_phase_callback_keeps_drain_for_recovery(tmp_path):
    drainer = FakeDrain()
    marker = {"phase_cas_committed_but_reply_was_lost": False}
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
    ])

    def ambiguous_phase_write():
        # Model the Redis CAS committing but the client not receiving success.
        marker["phase_cas_committed_but_reply_was_lost"] = True
        return False

    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, scale_in_enabled=True,
        before_scale_command=ambiguous_phase_write,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert marker["phase_cas_committed_but_reply_was_lost"] is True
    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "durable_scale_command_phase_not_confirmed_recovery_required"
    assert result.may_acknowledge_decision is False
    assert drainer.drain_calls == [f"{PROJECT}-backend-3"]
    # A failed/ambiguous phase transition must not mutate service admission
    # without confirmed controller ownership; explicit recovery owns cleanup.
    assert drainer.undrain_calls == []
    assert not any("scale" in call[0] for call in runner.calls)


def test_reconcile_pending_scale_out_confirms_target_or_original_without_scaling(tmp_path):
    target_runner = ScriptedRunner([
        config_reply(), completed(inventory(4)),
    ])
    target_actuator, _ = make_actuator(tmp_path, target_runner)
    intent = {
        "schema_version": 2, "phase": "preparing", "operation_id": "op-target",
        "action": "scale_out", "current_replicas": 3,
        "target_replicas": 4, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    applied = target_actuator.reconcile_pending_operation(intent)

    assert applied.status is ScaleActuationStatus.APPLIED
    assert applied.succeeded and applied.may_acknowledge_decision
    assert not any("scale" in call[0] for call in target_runner.calls)

    old_runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    old_actuator, _ = make_actuator(tmp_path, old_runner)
    failed = old_actuator.reconcile_pending_operation(intent)
    assert failed.status is ScaleActuationStatus.FAILED
    assert failed.reason == "pending_scale_out_command_not_started_original_state_verified"
    assert not any("scale" in call[0] for call in old_runner.calls)


def test_reconcile_target_fleet_requires_confirmed_route_publication(tmp_path):
    runner = ScriptedRunner([config_reply(), completed(inventory(4))])
    routing = FakeRouting(confirmed=False)
    actuator, _ = make_actuator(tmp_path, runner, routing=routing)
    intent = {
        "schema_version": 2, "phase": "scale_command_started",
        "operation_id": "op-route-failure", "action": "scale_out",
        "current_replicas": 3, "target_replicas": 4, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_operation_target_state_route_unconfirmed"
    assert result.may_acknowledge_decision is False
    assert routing.restore_calls == [f"{PROJECT}-backend-4"]
    assert not any("--scale" in command for command, _ in runner.calls)


def test_reconcile_legacy_intent_original_state_is_not_proof_of_non_application(tmp_path):
    runner = ScriptedRunner([config_reply(), completed(inventory(3))])
    actuator, _ = make_actuator(tmp_path, runner)
    legacy_intent = {
        "schema_version": 1, "operation_id": "legacy-op",
        "action": "scale_out", "current_replicas": 3,
        "target_replicas": 4, "reason": "legacy",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(legacy_intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_scale_out_original_state_but_command_outcome_unproven"
    assert not result.may_acknowledge_decision
    assert not any("scale" in call[0] for call in runner.calls)


def test_reconcile_started_scale_out_original_state_stays_uncertain(tmp_path):
    runner = ScriptedRunner([config_reply(), completed(inventory(3))])
    actuator, _ = make_actuator(tmp_path, runner)
    started_intent = {
        "schema_version": 2, "phase": "scale_command_started",
        "operation_id": "started-op", "action": "scale_out",
        "current_replicas": 3, "target_replicas": 4, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
        "scale_command_started_at": "2026-10-22T00:00:01+00:00",
    }

    result = actuator.reconcile_pending_operation(started_intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_scale_out_original_state_but_command_outcome_unproven"
    assert not result.may_acknowledge_decision
    assert not any("scale" in call[0] for call in runner.calls)


def test_reconcile_pending_scale_in_undrains_only_verified_original_topology(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3, bad_index=3)),
        completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, routing=FakeRouting(),
    )
    intent = {
        "schema_version": 2, "phase": "preparing", "operation_id": "op-in",
        "action": "scale_in", "current_replicas": 3,
        "target_replicas": 2, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.FAILED
    assert result.reason == "pending_scale_in_command_not_started_original_state_restored"
    assert drainer.undrain_calls == [f"{PROJECT}-backend-3"]
    assert not any("scale" in call[0] for call in runner.calls)


def test_reconcile_scale_in_target_requires_reduced_route_set(tmp_path):
    runner = ScriptedRunner([config_reply(), completed(inventory(2))])
    routing = FakeRouting(verify_confirmed=False)
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=FakeDrain(), routing=routing,
    )
    intent = {
        "schema_version": 2, "phase": "scale_command_started",
        "operation_id": "op-scale-in-route-mismatch", "action": "scale_in",
        "current_replicas": 3, "target_replicas": 2, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_operation_target_state_route_unconfirmed"
    assert result.may_acknowledge_decision is False
    assert routing.verify_calls == [2]
    assert routing.restore_calls == []


def test_reconcile_started_scale_in_does_not_undrain_when_command_outcome_is_unknown(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3, bad_index=3)),
    ])
    actuator, _ = make_actuator(tmp_path, runner, drainer=drainer)
    intent = {
        "schema_version": 2, "phase": "scale_command_started",
        "operation_id": "started-in", "action": "scale_in",
        "current_replicas": 3, "target_replicas": 2, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
        "scale_command_started_at": "2026-10-22T00:00:01+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_scale_in_command_outcome_unproven"
    assert drainer.undrain_calls == []
    assert not any("scale" in call[0] for call in runner.calls)


def test_reconcile_pending_operation_preserves_ambiguous_intent(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(2)),
    ])
    actuator, _ = make_actuator(tmp_path, runner)
    intent = {
        "schema_version": 1, "operation_id": "op-unknown",
        "action": "scale_out", "current_replicas": 3,
        "target_replicas": 4, "reason": "test",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert not result.may_acknowledge_decision
    assert not any("scale" in call[0] for call in runner.calls)


def test_recovery_refuses_replica_counts_outside_configured_bounds_without_commands(tmp_path):
    runner = ScriptedRunner([])
    actuator, _ = make_actuator(tmp_path, runner)
    intent = {
        "schema_version": 1, "operation_id": "op-out-of-bounds",
        "action": "scale_in", "current_replicas": 2,
        "target_replicas": 1, "reason": "corrupt_or_stale_record",
        "created_at": "2026-10-22T00:00:00+00:00",
    }

    result = actuator.reconcile_pending_operation(intent)

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert result.reason == "pending_operation_invalid_transition"
    assert runner.calls == []


def test_scale_in_is_refused_without_route_adapter(tmp_path):
    runner = ScriptedRunner([])
    drainer = FakeDrain()
    actuator = ComposeLiveScalingActuator(
        FakePreview(tmp_path),
        live_enabled=True,
        drain_adapter=drainer,
        scale_in_enabled=True,
        routing_adapter=None,
        before_scale_command=lambda: True,
        runner=runner,
    )
    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))
    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "scale_in_routing_unavailable"
    assert runner.calls == []
    assert drainer.drain_calls == []


def test_route_withdrawal_failure_never_starts_drain(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    drainer = FakeDrain()
    routing = FakeRouting(confirmed=False, restored=True)
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, routing=routing,
        scale_in_enabled=True,
    )
    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))
    assert result.status is ScaleActuationStatus.FAILED
    assert result.reason == "route_withdrawal_failed_prior_routes_restored"
    assert routing.withdraw_calls == [f"{PROJECT}-backend-3"]
    assert drainer.drain_calls == []
    # Read-only Compose config/inventory preflights are expected; no
    # preview or live scale command may run after route withdrawal fails.
    assert not any(
        "up" in command or "scale" in command
        for command, _kwargs in runner.calls
    )


def test_scale_in_requires_drain_adapter(tmp_path):
    runner = ScriptedRunner([])
    actuator, preview = make_actuator(
        tmp_path, runner, scale_in_enabled=True,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.REFUSED
    assert result.reason == "scale_in_drain_unavailable"
    assert not runner.calls and not preview.calls


def test_scale_in_drains_then_previews_and_applies(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(2)),
    ])
    actuator, preview = make_actuator(
        tmp_path, runner, drainer=drainer, scale_in_enabled=True,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.APPLIED
    assert result.observed_replicas == 2
    assert drainer.drain_calls == ["clinic-system-backend-3"]
    assert drainer.undrain_calls == []
    assert preview.calls[0][1] is True


def test_scale_in_withdraws_routes_before_draining(tmp_path):
    events = []
    routing = FakeRouting(events=events)
    drainer = FakeDrain(events=events)
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(), completed(inventory(2)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, routing=routing,
        scale_in_enabled=True,
    )
    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))
    assert result.status is ScaleActuationStatus.APPLIED
    assert events[:3] == [
        ("verify_routes", 3),
        ("withdraw", f"{PROJECT}-backend-3"),
        ("drain", f"{PROJECT}-backend-3"),
    ]
    assert routing.restore_calls == []
    assert routing.verify_calls == [3, 2]


def test_failed_scale_in_restores_drain_on_verified_old_state(tmp_path):
    drainer = FakeDrain()
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
        config_reply(), completed(inventory(3)),
        completed(returncode=1), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, scale_in_enabled=True,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.FAILED
    assert drainer.undrain_calls == ["clinic-system-backend-3"]


def test_unconfirmed_drain_does_not_scale(tmp_path):
    drainer = FakeDrain(confirmed=False, restored=None)
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, drainer=drainer, scale_in_enabled=True,
    )

    result = actuator.apply(decision(ScalingAction.SCALE_IN, 3, 2))

    assert result.status is ScaleActuationStatus.UNCERTAIN
    assert not result.may_acknowledge_decision
    assert len(runner.calls) == 2


def test_preview_refusal_never_executes_scale_command(tmp_path):
    runner = ScriptedRunner([
        config_reply(), completed(inventory(3)),
    ])
    actuator, _ = make_actuator(
        tmp_path, runner, preview_status=ScalePreviewStatus.REFUSED
    )

    result = actuator.apply(decision())

    assert result.status is ScaleActuationStatus.REFUSED
    assert not any("scale" in call[0] for call in runner.calls)


def test_mismatched_drain_project_is_rejected(tmp_path):
    preview = FakePreview(tmp_path)
    drainer = FakeDrain()
    drainer.project_name = "clinic-system-phase9"

    with pytest.raises(ValueError, match="same project"):
        ComposeLiveScalingActuator(
            preview, live_enabled=True, drain_adapter=drainer,
        )


def test_drain_adapter_rejects_another_project_without_docker():
    runner = ScriptedRunner([])
    drainer = DockerExecBackendDrainAdapter(project_name=PROJECT, runner=runner)

    result = drainer.drain_and_confirm("clinic-system-phase9-backend-3")

    assert not result.confirmed
    assert result.reason == "invalid_backend_name"
    assert runner.calls == []


def test_drain_token_is_not_passed_in_process_arguments():
    payload = {
        "ok": True, "reason": "drain_confirmed", "draining": True,
        "active_http_requests": 0, "active_socket_connections": 0,
        "ready_for_retirement": True,
    }
    runner = ScriptedRunner([completed(json.dumps(payload))])
    drainer = DockerExecBackendDrainAdapter(project_name=PROJECT, runner=runner)

    result = drainer.drain_and_confirm("clinic-system-backend-3")

    assert result.confirmed
    command = runner.calls[0][0]
    assert command[:4] == ["docker", "exec", "clinic-system-backend-3", "python"]
    assert "PHASE9_BACKEND_DRAIN_TOKEN" in command[5]
    assert "Authorization: Bearer " not in " ".join(command)