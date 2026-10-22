from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.autoscaling.controller_lease import ControllerLeaseLost, ControllerReconciliationRequired
from app.core.autoscaling.live_actuator import ScaleActuationResult, ScaleActuationStatus
from app.core.autoscaling.pending_operation_recovery import (
    PendingOperationRecovery,
    PendingOperationRecoveryStatus,
)
from app.core.autoscaling.recovery_service import build_recovery_service

AT = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
INTENT = {
    "schema_version": 1,
    "operation_id": "test-operation",
    "action": "scale_out",
    "current_replicas": 2,
    "target_replicas": 3,
    "reason": "sustained_demand",
    "created_at": "2026-10-22T11:59:00+00:00",
}


class FakeLease:
    def __init__(self, intent=INTENT, *, owner=True, clear=True):
        self.intent = intent
        self.owner = owner
        self.clear = clear
        self.completed = []

    def is_owner(self):
        return self.owner

    def read_pending_operation(self):
        if not self.owner:
            raise ControllerLeaseLost("lost")
        return self.intent

    def complete_pending_operation(self, *, succeeded, at):
        if not self.owner or not self.clear:
            return False
        self.completed.append((succeeded, at))
        self.intent = None
        return True


class FakeActuator:
    def __init__(self, outcome, callback=None):
        self.outcome = outcome
        self.callback = callback
        self.calls = []

    def reconcile_pending_operation(self, intent):
        self.calls.append(intent)
        if self.callback:
            self.callback()
        return self.outcome


def result(status, reason="test_outcome"):
    return ScaleActuationResult(status, reason, 2, 3, observed_replicas=3)


def test_no_pending_intent_does_not_call_actuator():
    lease = FakeLease(intent=None)
    actuator = FakeActuator(result(ScaleActuationStatus.UNCERTAIN))

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.NO_PENDING_OPERATION
    assert recovered.completed
    assert actuator.calls == []
    assert lease.completed == []


def test_verified_applied_state_clears_intent_and_records_cooldown():
    lease = FakeLease()
    actuator = FakeActuator(result(ScaleActuationStatus.APPLIED))

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERED_APPLIED
    assert recovered.completed
    assert recovered.operation_id == "test-operation"
    assert lease.intent is None
    assert lease.completed == [(True, AT)]
    assert len(actuator.calls) == 1


def test_verified_original_state_clears_intent_without_starting_cooldown():
    lease = FakeLease()
    actuator = FakeActuator(result(ScaleActuationStatus.FAILED, "original_state_restored"))

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERED_NOT_APPLIED
    assert lease.intent is None
    assert lease.completed == [(False, AT)]


@pytest.mark.parametrize(
    "status",
    [ScaleActuationStatus.UNCERTAIN, ScaleActuationStatus.REFUSED],
)
def test_unproven_outcome_leaves_intent_untouched(status):
    lease = FakeLease()
    actuator = FakeActuator(result(status))

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERY_REQUIRED
    assert not recovered.completed
    assert lease.intent == INTENT
    assert lease.completed == []


def test_lease_loss_during_recovery_leaves_intent_untouched():
    lease = FakeLease()
    actuator = FakeActuator(
        result(ScaleActuationStatus.APPLIED),
        callback=lambda: setattr(lease, "owner", False),
    )

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERY_REQUIRED
    assert recovered.reason == "controller_lease_lost_during_recovery"
    assert lease.intent == INTENT
    assert lease.completed == []


def test_intent_clear_failure_keeps_recovery_required():
    lease = FakeLease(clear=False)
    actuator = FakeActuator(result(ScaleActuationStatus.APPLIED))

    recovered = PendingOperationRecovery(lease, actuator, clock=lambda: AT).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERY_REQUIRED
    assert "intent_clear_not_confirmed" in recovered.reason
    assert lease.intent == INTENT
    assert lease.completed == []


def test_recovery_clock_must_be_timezone_aware():
    lease = FakeLease()
    actuator = FakeActuator(result(ScaleActuationStatus.APPLIED))

    recovered = PendingOperationRecovery(
        lease, actuator, clock=lambda: datetime(2026, 10, 22, 12, 0)
    ).run_once()

    assert recovered.status is PendingOperationRecoveryStatus.RECOVERY_REQUIRED
    assert lease.intent == INTENT
    assert lease.completed == []


def test_recovery_service_requires_explicit_opt_in_before_connecting(tmp_path):
    calls = []
    with pytest.raises(RuntimeError, match="explicit pending-operation recovery opt-in"):
        build_recovery_service(
            {"PHASE9_AUTOSCALING_RECOVERY_ENABLED": "false"},
            redis_client_factory=lambda *args, **kwargs: calls.append((args, kwargs)),
        )
    assert calls == []


def test_recovery_service_builds_dependencies_without_running_docker(tmp_path):
    class FakeRedisClient:
        def __init__(self):
            self.closed = False
            self.redis_calls = []

        def set(self, *args, **kwargs):
            self.redis_calls.append(("set", args, kwargs))
            raise AssertionError("Redis access is not expected during dependency construction")

        def get(self, *args, **kwargs):
            self.redis_calls.append(("get", args, kwargs))
            raise AssertionError("Redis access is not expected during dependency construction")

        def eval(self, *args, **kwargs):
            self.redis_calls.append(("eval", args, kwargs))
            raise AssertionError("Redis access is not expected during dependency construction")

        def close(self):
            self.closed = True

    client = FakeRedisClient()
    runner_calls = []
    for filename in (".env.phase6", "compose.yaml", "compose.phase6.yaml", "compose.phase9.yaml"):
        (tmp_path / filename).write_text("placeholder\n", encoding="utf-8")
    env = {
        "PHASE9_AUTOSCALING_RECOVERY_ENABLED": "true",
        "PHASE9_AUTOSCALING_PROJECT_NAME": "clinic-system",
        "PHASE9_AUTOSCALING_PROJECT_ROOT": str(tmp_path),
        "PHASE9_AUTOSCALING_REDIS_URL": "redis://127.0.0.1:16379/0",
        "PHASE9_AUTOSCALING_MIN_REPLICAS": "2",
        "PHASE9_AUTOSCALING_MAX_REPLICAS": "4",
    }

    runtime = build_recovery_service(
        env,
        redis_client_factory=lambda *args, **kwargs: client,
        command_runner=lambda *args, **kwargs: runner_calls.append((args, kwargs)),
    )
    try:
        assert runtime.lease.key == "clinic-system:autoscaling:controller:lease"
        assert runtime.actuator.live_enabled is True
        assert runtime.actuator.drain_adapter is not None
        assert runner_calls == []
    finally:
        runtime.close()
    assert client.closed is True
    assert client.redis_calls == []
