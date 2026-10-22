"""Redis-backed ownership guard for the autoscaling reconciliation controller.

This module deliberately does not enable or start a controller. The caller must
explicitly construct a guarded loop and an enabled runner. A lease reduces normal
concurrent-controller risk; it is not a fencing token for an external Docker API.
"""
from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class ControllerLeaseLost(RuntimeError):
    """Raised when controller ownership cannot be confirmed."""


class RedisControllerLease:
    """Renewable Redis lease using token-checked renewal and release."""

    def __init__(
        self,
        redis_client: Any,
        *,
        key: str = "clinic-system:autoscaling:controller:lease",
        ttl_seconds: float = 45.0,
        renew_interval_seconds: float = 10.0,
    ) -> None:
        if not key or not isinstance(key, str):
            raise ValueError("key must be a non-empty string")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if renew_interval_seconds <= 0 or renew_interval_seconds >= ttl_seconds:
            raise ValueError("renew_interval_seconds must be positive and less than ttl_seconds")
        if not callable(getattr(redis_client, "set", None)) or not callable(
            getattr(redis_client, "get", None)
        ) or not callable(getattr(redis_client, "eval", None)):
            raise TypeError("redis_client must provide set(), get(), and eval()")

        self._redis = redis_client
        self.key = key
        self.ttl_ms = max(1, int(ttl_seconds * 1000))
        self.renew_interval_seconds = float(renew_interval_seconds)
        self._state_lock = threading.RLock()
        self._token: str | None = None
        self._owned = False
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None

    @staticmethod
    def _token_matches(value: Any, token: str) -> bool:
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return False
        return value == token

    def _mark_lost_locked(self) -> None:
        self._owned = False
        self._stop_event.set()

    def _is_owner_locked(self) -> bool:
        token = self._token
        if not self._owned or token is None:
            return False
        try:
            current = self._redis.get(self.key)
        except Exception:
            _LOG.exception("autoscaling controller lease ownership check failed")
            self._mark_lost_locked()
            return False
        if not self._token_matches(current, token):
            self._mark_lost_locked()
            return False
        return True

    def is_owner(self) -> bool:
        """Confirm ownership against Redis; errors fail closed."""
        with self._state_lock:
            return self._is_owner_locked()

    def acquire(self) -> bool:
        """Acquire the lease and start renewal; contention returns False."""
        with self._state_lock:
            if self._token is not None:
                # A lease object that lost ownership must be released/reset before reuse.
                return self._is_owner_locked()
            token = secrets.token_urlsafe(32)
            try:
                accepted = self._redis.set(self.key, token, nx=True, px=self.ttl_ms)
            except Exception:
                _LOG.exception("autoscaling controller lease acquisition failed")
                return False
            if not accepted:
                return False

            self._token = token
            self._owned = True
            self._stop_event = threading.Event()
            thread = threading.Thread(
                target=self._heartbeat_loop,
                name="autoscaling-controller-lease-renewal",
                daemon=True,
            )
            self._heartbeat_thread = thread
            thread.start()
            return True

    def renew(self) -> bool:
        """Renew only if Redis still contains this instance's token."""
        with self._state_lock:
            token = self._token
            if not self._owned or token is None:
                return False
            try:
                renewed = self._redis.eval(
                    _RENEW_SCRIPT, 1, self.key, token, self.ttl_ms
                )
            except Exception:
                _LOG.exception("autoscaling controller lease renewal failed")
                self._mark_lost_locked()
                return False
            if int(renewed or 0) != 1:
                self._mark_lost_locked()
                return False
            return True

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.renew_interval_seconds):
            if not self.renew():
                return

    def release(self) -> bool:
        """Stop renewal and delete the Redis key only when its token matches."""
        with self._state_lock:
            token = self._token
            thread = self._heartbeat_thread
            self._stop_event.set()
            self._owned = False

        if token is None:
            return False
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.renew_interval_seconds * 2.0))

        released = False
        try:
            released = int(
                self._redis.eval(_RELEASE_SCRIPT, 1, self.key, token) or 0
            ) == 1
        except Exception:
            _LOG.exception("autoscaling controller lease release failed")
        finally:
            with self._state_lock:
                self._token = None
                self._heartbeat_thread = None
                self._owned = False
                self._stop_event.set()
        return released


class LeaseGuardedActuator:
    """Check lease ownership both before and after an actuator call."""

    def __init__(self, actuator: Any, lease: RedisControllerLease) -> None:
        if not callable(getattr(actuator, "apply", None)):
            raise TypeError("actuator must provide apply(decision)")
        self._actuator = actuator
        self.lease = lease

    def _require_owner(self, phase: str) -> None:
        if not self.lease.is_owner():
            raise ControllerLeaseLost(f"controller lease not held {phase}")

    def apply(self, decision: Any) -> Any:
        self._require_owner("before actuation")
        result = self._actuator.apply(decision)
        # If ownership disappeared during the external call, the result is uncertain.
        self._require_owner("after actuation; outcome must be reconciled")
        return result


class LeaseGuardedAcknowledgement:
    """Protect reconciliation acknowledgements with the same lease instance."""

    def __init__(self, callback: Callable[..., Any], lease: RedisControllerLease) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self.lease = lease

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self.lease.is_owner():
            raise ControllerLeaseLost("controller lease not held before acknowledgement")
        result = self._callback(*args, **kwargs)
        if not self.lease.is_owner():
            raise ControllerLeaseLost("controller lease lost during acknowledgement")
        return result


class ControllerRunnerStatus(str, Enum):
    DISABLED = "disabled"
    BUSY = "busy"
    LEASE_NOT_ACQUIRED = "lease_not_acquired"
    OWNERSHIP_LOST = "ownership_lost"
    CYCLE_COMPLETE = "cycle_complete"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(frozen=True)
class ControllerRunnerResult:
    status: ControllerRunnerStatus
    cycles: int = 0
    lease_released: bool | None = None
    detail: str = ""


class AutoscalingControllerRunner:
    """Explicit runner that holds one Redis lease for its active lifecycle.

    Disabled by default. Enabling requires a reconciliation loop that is itself
    enabled and uses a guarded actuator and guarded acknowledgement sharing the
    exact same lease object. This class does not start itself or mutate Docker.
    """

    def __init__(
        self,
        reconciliation_loop: Any,
        lease: RedisControllerLease,
        *,
        enabled: bool = False,
        interval_seconds: float | None = None,
    ) -> None:
        if interval_seconds is None:
            interval_seconds = getattr(reconciliation_loop, "interval_seconds", 30.0)
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

        self.loop = reconciliation_loop
        self.lease = lease
        self.enabled = bool(enabled)
        self.interval_seconds = float(interval_seconds)
        self._run_lock = threading.Lock()

        if self.enabled:
            if getattr(reconciliation_loop, "enabled", False) is not True:
                raise ValueError("enabled runner requires an enabled reconciliation loop")
            actuator = getattr(reconciliation_loop, "_actuator", None)
            acknowledgement = getattr(reconciliation_loop, "_acknowledge", None)
            if not isinstance(actuator, LeaseGuardedActuator) or actuator.lease is not lease:
                raise ValueError("enabled runner requires a LeaseGuardedActuator using its lease")
            if not isinstance(acknowledgement, LeaseGuardedAcknowledgement) or acknowledgement.lease is not lease:
                raise ValueError("enabled runner requires a LeaseGuardedAcknowledgement using its lease")

    def run_once(self) -> ControllerRunnerResult:
        if not self.enabled:
            return ControllerRunnerResult(ControllerRunnerStatus.DISABLED)
        if not self._run_lock.acquire(blocking=False):
            return ControllerRunnerResult(ControllerRunnerStatus.BUSY)

        acquired = False
        released: bool | None = None
        cycles = 0
        detail = ""
        status = ControllerRunnerStatus.ERROR
        try:
            acquired = bool(self.lease.acquire())
            if not acquired:
                status = ControllerRunnerStatus.LEASE_NOT_ACQUIRED
            elif not self.lease.is_owner():
                status = ControllerRunnerStatus.OWNERSHIP_LOST
            else:
                self.loop.run_once()
                cycles = 1
                status = (
                    ControllerRunnerStatus.CYCLE_COMPLETE
                    if self.lease.is_owner()
                    else ControllerRunnerStatus.OWNERSHIP_LOST
                )
        except Exception as exc:
            detail = type(exc).__name__
            status = ControllerRunnerStatus.ERROR
            _LOG.exception("autoscaling controller runner cycle failed")
        finally:
            if acquired:
                try:
                    released = bool(self.lease.release())
                except Exception as exc:
                    released = False
                    if not detail:
                        detail = type(exc).__name__
                    if status not in (ControllerRunnerStatus.OWNERSHIP_LOST,):
                        status = ControllerRunnerStatus.ERROR
            self._run_lock.release()
        return ControllerRunnerResult(status, cycles, released, detail)

    def run_forever(self, stop_event: Any) -> ControllerRunnerResult:
        if not self.enabled:
            return ControllerRunnerResult(ControllerRunnerStatus.DISABLED)
        if not callable(getattr(stop_event, "is_set", None)) or not callable(
            getattr(stop_event, "wait", None)
        ):
            raise TypeError("stop_event must provide is_set() and wait(timeout)")
        if not self._run_lock.acquire(blocking=False):
            return ControllerRunnerResult(ControllerRunnerStatus.BUSY)

        acquired = False
        released: bool | None = None
        cycles = 0
        detail = ""
        status = ControllerRunnerStatus.ERROR
        try:
            acquired = bool(self.lease.acquire())
            if not acquired:
                status = ControllerRunnerStatus.LEASE_NOT_ACQUIRED
            elif not self.lease.is_owner():
                status = ControllerRunnerStatus.OWNERSHIP_LOST
            else:
                status = ControllerRunnerStatus.STOPPED
                while not stop_event.is_set():
                    if not self.lease.is_owner():
                        status = ControllerRunnerStatus.OWNERSHIP_LOST
                        break
                    self.loop.run_once()
                    cycles += 1
                    if not self.lease.is_owner():
                        status = ControllerRunnerStatus.OWNERSHIP_LOST
                        break
                    stop_event.wait(self.interval_seconds)
        except Exception as exc:
            detail = type(exc).__name__
            status = ControllerRunnerStatus.ERROR
            _LOG.exception("autoscaling controller runner lifecycle failed")
        finally:
            if acquired:
                try:
                    released = bool(self.lease.release())
                except Exception as exc:
                    released = False
                    if not detail:
                        detail = type(exc).__name__
                    if status is not ControllerRunnerStatus.OWNERSHIP_LOST:
                        status = ControllerRunnerStatus.ERROR
            self._run_lock.release()
        return ControllerRunnerResult(status, cycles, released, detail)