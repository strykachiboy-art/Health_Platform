from __future__ import annotations

import threading

import pytest

from app.core.autoscaling.controller_lease import (
    AutoscalingControllerRunner,
    ControllerLeaseLost,
    ControllerRunnerStatus,
    LeaseGuardedAcknowledgement,
    LeaseGuardedActuator,
    RedisControllerLease,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.renewals = 0
        self._lock = threading.Lock()

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
        key, token, *extra = args
        with self._lock:
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
        guarded.apply("scale-out")
    assert actuator.calls == []


def test_guarded_actuator_detects_ownership_loss_during_external_call():
    lease = FakeLease()
    lease.held = True
    actuator = FakeActuator(callback=lambda: setattr(lease, "held", False))
    guarded = LeaseGuardedActuator(actuator, lease)
    with pytest.raises(ControllerLeaseLost):
        guarded.apply("scale-out")
    assert actuator.calls == ["scale-out"]


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