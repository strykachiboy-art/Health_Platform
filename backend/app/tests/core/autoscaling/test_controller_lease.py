from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from app.core.autoscaling.controller_lease import (
    AutoscalingControllerRunner,
    ControllerLeaseLost,
    ControllerReconciliationRequired,
    ControllerRunnerStatus,
    LeaseGuardedAcknowledgement,
    LeaseGuardedActuator,
    RedisControllerLease,
)
from app.core.autoscaling.decision_engine import ScalingAction, ScalingDecision


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.renewals = 0
        self._lock = threading.Lock()
        self.lose_completion_reply_once = False

    def set(self, key, value, *, nx=False, px=None):
        with self._lock:
            if nx and key in self.values:
                return None
            self.values[key] = value
            return True

    def get(self, key):
        with self._lock:
            return self.values.get(key)

    def eval(self, script, numkeys, *args):
        with self._lock:
            if "MARK_SCALE_COMMAND_STARTED" in script:
                lease_key, operation_key, token, expected_record, updated_record = args
                if (
                    self.values.get(lease_key) == token
                    and self.values.get(operation_key) == expected_record
                ):
                    self.values[operation_key] = updated_record
                    return 1
                return 0

            if "KEYS[4]" in script:
                lease_key, operation_key, last_scale_key, completed_key, token, record, last_scale_at, completion = args
                if self.values.get(lease_key) != token:
                    return 0
                if self.values.get(operation_key) == record:
                    if last_scale_at:
                        self.values[last_scale_key] = last_scale_at
                    self.values[completed_key] = completion
                    del self.values[operation_key]
                    if self.lose_completion_reply_once:
                        self.lose_completion_reply_once = False
                        raise TimeoutError("simulated lost Redis completion reply")
                    return 1
                if operation_key not in self.values and self.values.get(completed_key) == completion:
                    return 2
                return 0

            key, token, *extra = args
            if "pexpire" in script.lower():
                if self.values.get(key) == token:
                    self.renewals += 1
                    return 1
                return 0
            if "del" in script.lower():
                if self.values.get(key) == token:
                    del self.values[key]
                    return 1
                return 0
        raise AssertionError("Unexpected Redis script")


class FakeLease:
    def __init__(self, *, acquire_ok=True):
        self.held = False
        self.acquire_ok = acquire_ok
        self.acquire_calls = 0
        self.release_calls = 0
        self.release_result = True
        self.pending_operation = False

    def begin_operation(self, decision):
        if not self.held:
            raise ControllerLeaseLost("not owner")
        if self.pending_operation:
            raise ControllerReconciliationRequired("pending operation")
        self.pending_operation = True
        return "fake-operation"

    def has_pending_operation(self):
        return self.pending_operation

    def complete_pending_operation(self, *, succeeded, at):
        if not self.held:
            return False
        self.pending_operation = False
        if succeeded:
            self.last_successful_scale_at = at
        return True

    def acquire(self):
        self.acquire_calls += 1
        if not self.acquire_ok or self.held:
            return False
        self.held = True
        return True

    def is_owner(self):
        return self.held

    def release(self):
        self.release_calls += 1
        was_held = self.held
        self.held = False
        return bool(self.release_result and was_held)


def scale_decision():
    return ScalingDecision(
        ScalingAction.SCALE_OUT,
        2,
        3,
        "sustained_demand",
        0.95,
    )


class FakeActuator:
    def __init__(self, callback=None):
        self.callback = callback
        self.calls = []

    def apply(self, decision):
        self.calls.append(decision)
        if self.callback is not None:
            self.callback()
        return "applied"


class FakeLoop:
    def __init__(self, actuator, acknowledge, callback=None, *, enabled=True):
        self._actuator = actuator
        self._acknowledge = acknowledge
        self.enabled = enabled
        self.interval_seconds = 0.01
        self.callback = callback
        self.calls = 0

    def run_once(self):
        self.calls += 1
        if self.callback is not None:
            self.callback()
        return "cycle"


def make_runner(lease, *, callback=None, enabled=True, loop_enabled=True):
    actuator = LeaseGuardedActuator(FakeActuator(), lease)
    acknowledge = LeaseGuardedAcknowledgement(lambda *args, **kwargs: None, lease)
    loop = FakeLoop(actuator, acknowledge, callback=callback, enabled=loop_enabled)
    runner = AutoscalingControllerRunner(loop, lease, enabled=enabled, interval_seconds=0.01)
    return runner, loop


def test_redis_lease_acquisition_is_exclusive_and_release_is_token_safe():
    redis = FakeRedis()
    first = RedisControllerLease(redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5)
    second = RedisControllerLease(redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        assert first.is_owner() is True
        assert first.release() is True
        assert second.acquire() is True
        assert second.release() is True
    finally:
        first.release()
        second.release()


def test_intent_survives_restart_and_completion_retry_is_idempotent():
    redis = FakeRedis()
    first = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    assert first.acquire() is True
    first.begin_operation(scale_decision())
    assert redis.get("lease:pending-operation") is not None
    assert first.release() is True

    recovered_lease = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    assert recovered_lease.acquire() is True
    try:
        intent = recovered_lease.read_pending_operation()
        assert intent is not None
        assert intent["operation_id"]
        assert intent["action"] == "scale_out"
        assert intent["current_replicas"] == 2
        assert intent["target_replicas"] == 3
        assert intent["schema_version"] == 2
        assert intent["phase"] == "preparing"
        assert recovered_lease.mark_scale_command_started() is True
        intent = recovered_lease.read_pending_operation()
        assert intent is not None
        assert intent["phase"] == "scale_command_started"
        assert intent["scale_command_started_at"]

        completed_at = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        redis.lose_completion_reply_once = True
        assert recovered_lease.complete_pending_operation(
            succeeded=True, at=completed_at,
        ) is False
        assert redis.get("lease:pending-operation") is None
        assert recovered_lease.complete_pending_operation(
            succeeded=True, at=completed_at,
        ) is True
        assert recovered_lease.get_last_successful_scale_at() == completed_at
        assert redis.get("lease:last-completed-operation") is not None
    finally:
        recovered_lease.release()


def test_replaced_token_fails_closed_and_release_does_not_delete_new_owner():
    redis = FakeRedis()
    lease = RedisControllerLease(redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5)
    assert lease.acquire() is True
    redis.values["lease"] = "new-owner-token"
    assert lease.is_owner() is False
    assert lease.release() is False
    assert redis.get("lease") == "new-owner-token"


def test_renewal_fails_closed_after_token_replacement():
    redis = FakeRedis()
    lease = RedisControllerLease(redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5)
    assert lease.acquire() is True
    try:
        assert lease.renew() is True
        assert redis.renewals == 1
        redis.values["lease"] = "replacement"
        assert lease.renew() is False
        assert lease.is_owner() is False
    finally:
        lease.release()


def test_lease_rejects_invalid_timing_configuration():
    with pytest.raises(ValueError):
        RedisControllerLease(FakeRedis(), ttl_seconds=1, renew_interval_seconds=1)


def test_guarded_actuator_refuses_to_run_without_ownership():
    lease = FakeLease()
    actuator = FakeActuator()
    guarded = LeaseGuardedActuator(actuator, lease)
    with pytest.raises(ControllerLeaseLost):
        guarded.apply(scale_decision())
    assert actuator.calls == []


def test_guarded_actuator_detects_ownership_loss_during_external_call():
    lease = FakeLease()
    lease.held = True
    actuator = FakeActuator(callback=lambda: setattr(lease, "held", False))
    guarded = LeaseGuardedActuator(actuator, lease)
    with pytest.raises(ControllerLeaseLost):
        guarded.apply(scale_decision())
    assert len(actuator.calls) == 1
    assert actuator.calls[0].action is ScalingAction.SCALE_OUT


def test_operation_intent_is_persisted_before_actuation_and_cleared_for_known_result():
    redis = FakeRedis()
    lease = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    assert lease.acquire() is True
    during_actuation = []
    ack_observations = []
    actuator = FakeActuator(
        callback=lambda: during_actuation.append(lease.has_pending_operation())
    )
    try:
        guarded = LeaseGuardedActuator(actuator, lease)
        assert guarded.apply(scale_decision()) == "applied"
        assert during_actuation == [True]
        assert lease.has_pending_operation() is True

        acknowledgement = LeaseGuardedAcknowledgement(
            lambda **kwargs: ack_observations.append(lease.has_pending_operation()),
            lease,
        )
        acknowledgement(succeeded=True, at=datetime.now(timezone.utc))
        assert ack_observations == [True]
        assert lease.has_pending_operation() is False
    finally:
        lease.release()


def test_lease_loss_during_external_actuation_leaves_durable_recovery_barrier():
    redis = FakeRedis()
    lease = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    assert lease.acquire() is True
    actuator = FakeActuator(
        callback=lambda: redis.values.__setitem__("lease", "replacement-owner")
    )
    guarded = LeaseGuardedActuator(actuator, lease)

    with pytest.raises(ControllerLeaseLost):
        guarded.apply(scale_decision())

    assert len(actuator.calls) == 1
    assert lease.has_pending_operation() is True
    lease.release()
    assert redis.get("lease") == "replacement-owner"
    assert lease.has_pending_operation() is True


def test_guarded_acknowledgement_requires_ownership():
    lease = FakeLease()
    calls = []
    guarded = LeaseGuardedAcknowledgement(lambda value: calls.append(value), lease)
    with pytest.raises(ControllerLeaseLost):
        guarded("confirmed")
    assert calls == []


def test_disabled_runner_does_not_acquire_or_run():
    lease = FakeLease()
    runner, loop = make_runner(lease, enabled=False)
    result = runner.run_once()
    assert result.status is ControllerRunnerStatus.DISABLED
    assert lease.acquire_calls == 0
    assert loop.calls == 0


def test_runner_blocks_all_entrypoints_when_a_persisted_operation_needs_reconciliation():
    redis = FakeRedis()
    owner = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    assert owner.acquire() is True
    owner.begin_operation(scale_decision())
    assert owner.release() is True
    assert redis.get("lease:pending-operation") is not None

    new_lease = RedisControllerLease(
        redis, key="lease", ttl_seconds=2, renew_interval_seconds=0.5,
    )
    runner, loop = make_runner(new_lease)
    first = runner.run_once()
    assert first.status is ControllerRunnerStatus.RECOVERY_REQUIRED
    assert first.cycles == 0
    assert loop.calls == 0

    class StopImmediately:
        def is_set(self):
            return True
        def wait(self, timeout):
            return True

    second = runner.run_forever(StopImmediately())
    assert second.status is ControllerRunnerStatus.RECOVERY_REQUIRED
    assert second.cycles == 0
    assert loop.calls == 0
    assert new_lease.has_pending_operation() is True


def test_runner_runs_one_cycle_under_lease_and_releases_it():
    lease = FakeLease()
    runner, loop = make_runner(lease)
    result = runner.run_once()
    assert result.status is ControllerRunnerStatus.CYCLE_COMPLETE
    assert result.cycles == 1
    assert result.lease_released is True
    assert lease.acquire_calls == 1
    assert lease.release_calls == 1
    assert loop.calls == 1


def test_runner_does_not_run_when_lease_is_unavailable():
    lease = FakeLease(acquire_ok=False)
    runner, loop = make_runner(lease)
    result = runner.run_once()
    assert result.status is ControllerRunnerStatus.LEASE_NOT_ACQUIRED
    assert result.cycles == 0
    assert loop.calls == 0
    assert lease.release_calls == 0


def test_runner_stops_after_ownership_is_lost_during_cycle():
    lease = FakeLease()
    runner, loop = make_runner(lease, callback=lambda: setattr(lease, "held", False))
    result = runner.run_once()
    assert result.status is ControllerRunnerStatus.OWNERSHIP_LOST
    assert loop.calls == 1
    assert result.lease_released is False


def test_enabled_runner_rejects_loop_without_guarded_actuator():
    lease = FakeLease()
    loop = FakeLoop(FakeActuator(), LeaseGuardedAcknowledgement(lambda: None, lease))
    with pytest.raises(ValueError, match="LeaseGuardedActuator"):
        AutoscalingControllerRunner(loop, lease, enabled=True)


def test_lifecycle_holds_one_lease_across_multiple_cycles():
    lease = FakeLease()
    runner, loop = make_runner(lease)

    class StopAfterTwoWaits:
        def __init__(self):
            self.waits = 0

        def is_set(self):
            return self.waits >= 2

        def wait(self, timeout):
            self.waits += 1
            return False

    stop = StopAfterTwoWaits()
    result = runner.run_forever(stop)
    assert result.status is ControllerRunnerStatus.STOPPED
    assert result.cycles == 2
    assert loop.calls == 2
    assert lease.acquire_calls == 1
    assert lease.release_calls == 1
    assert result.lease_released is True


def test_run_forever_requires_a_stop_event_contract():
    lease = FakeLease()
    runner, _ = make_runner(lease)
    with pytest.raises(TypeError, match="stop_event"):
        runner.run_forever(object())