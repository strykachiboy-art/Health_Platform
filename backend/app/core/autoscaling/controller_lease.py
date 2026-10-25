"""Redis-backed ownership guard for the autoscaling reconciliation controller.

This module deliberately does not enable or start a controller. The caller must
explicitly construct a guarded loop and an enabled runner. A lease reduces normal
concurrent-controller risk; it is not a fencing token for an external Docker API.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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

_COMPLETE_OPERATION_SCRIPT = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
local current = redis.call('get', KEYS[2])
if current == ARGV[2] then
    if ARGV[3] ~= '' then redis.call('set', KEYS[3], ARGV[3]) end
    redis.call('set', KEYS[4], ARGV[4])
    return redis.call('del', KEYS[2])
end
-- The caller may have lost the EVAL reply after the script committed. Treat
-- only the exact same durable completion marker as an idempotent success.
if not current and redis.call('get', KEYS[4]) == ARGV[4] then return 2 end
return 0
"""

_MARK_SCALE_COMMAND_STARTED_SCRIPT = """
-- MARK_SCALE_COMMAND_STARTED: no scale CLI invocation before this CAS succeeds.
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
if redis.call('get', KEYS[2]) ~= ARGV[2] then return 0 end
redis.call('set', KEYS[2], ARGV[3])
return 1
"""


class ControllerLeaseLost(RuntimeError):
    """Raised when controller ownership cannot be confirmed."""


class ControllerReconciliationRequired(RuntimeError):
    """An unresolved scaling intent blocks new mutations until reviewed."""


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
        self.operation_key = f"{key}:pending-operation"
        self.last_successful_scale_key = f"{key}:last-successful-scale-at"
        self.last_completed_operation_key = f"{key}:last-completed-operation"
        self.ttl_ms = max(1, int(ttl_seconds * 1000))
        self.renew_interval_seconds = float(renew_interval_seconds)
        self._state_lock = threading.RLock()
        self._token: str | None = None
        self._owned = False
        self._stop_event = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        # Kept only for this process; Redis remains the durable recovery barrier.
        self._operation_raw: str | None = None

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

    def has_pending_operation(self) -> bool:
        """Return true on any persistent intent or on Redis read failure."""
        try:
            return self._redis.get(self.operation_key) is not None
        except Exception:
            _LOG.exception("autoscaling pending-operation check failed closed")
            return True

    def read_pending_operation(self) -> dict[str, Any] | None:
        """Load and validate the durable intent while this process owns the lease.

        This supports an explicit recovery command after a controller restart.
        Invalid records and Redis failures preserve the barrier and fail closed.
        """
        with self._state_lock:
            if not self._is_owner_locked():
                raise ControllerLeaseLost("controller lease not held before reading pending intent")
            try:
                value = self._redis.get(self.operation_key)
            except Exception:
                _LOG.exception("autoscaling pending-operation read failed")
                raise ControllerReconciliationRequired(
                    "could not read pending operation; recovery remains required"
                ) from None

            if value is None:
                if self._operation_raw is not None:
                    raise ControllerReconciliationRequired(
                        "pending intent disappeared before its completion was confirmed"
                    )
                return None

            if isinstance(value, bytes):
                try:
                    raw = value.decode("utf-8")
                except UnicodeDecodeError:
                    raise ControllerReconciliationRequired(
                        "pending intent is not valid UTF-8"
                    ) from None
            elif isinstance(value, str):
                raw = value
            else:
                raise ControllerReconciliationRequired(
                    "pending intent has an invalid Redis value type"
                )

            try:
                record = json.loads(raw)
                action = record["action"]
                current = record["current_replicas"]
                target = record["target_replicas"]
                operation_id = record["operation_id"]
                created_at = datetime.fromisoformat(record["created_at"])
                schema_version = record.get("schema_version") if type(record) is dict else None
                phase_started_at = (
                    datetime.fromisoformat(record["scale_command_started_at"])
                    if type(record) is dict and record.get("phase") == "scale_command_started"
                    else None
                )
                valid = (
                    type(record) is dict
                    and type(record.get("schema_version")) is int
                    and record["schema_version"] in {1, 2}
                    and (
                        (record["schema_version"] == 1 and "phase" not in record)
                        or (
                            record["schema_version"] == 2
                            and record.get("phase") in {"preparing", "scale_command_started"}
                            and (
                                record["phase"] != "scale_command_started"
                                or (
                                    isinstance(phase_started_at, datetime)
                                    and phase_started_at.tzinfo is not None
                                    and phase_started_at.utcoffset() is not None
                                )
                            )
                        )
                    )
                    and isinstance(operation_id, str)
                    and bool(operation_id)
                    and action in {"scale_out", "scale_in"}
                    and type(current) is int
                    and type(target) is int
                    and current >= 1
                    and target == current + (1 if action == "scale_out" else -1)
                    and target >= 1
                    and isinstance(record.get("reason", ""), str)
                    and isinstance(created_at, datetime)
                    and created_at.tzinfo is not None
                    and created_at.utcoffset() is not None
                )
            except (KeyError, TypeError, ValueError, OverflowError):
                valid = False
                record = None

            if not valid or not isinstance(record, dict):
                raise ControllerReconciliationRequired(
                    "pending intent is malformed; operator reconciliation is required"
                )
            if self._operation_raw is not None and self._operation_raw != raw:
                raise ControllerReconciliationRequired(
                    "pending intent changed unexpectedly; operator reconciliation is required"
                )
            self._operation_raw = raw
            if not self._is_owner_locked():
                raise ControllerLeaseLost(
                    "controller lease lost while reading pending intent"
                )
            public_record = dict(record)
            if schema_version == 1:
                # Legacy intents predate the pre-mutation CAS. Their phase is
                # unknown; an original-state snapshot cannot safely clear them.
                public_record["phase"] = "unknown"
            return public_record

    def get_last_successful_scale_at(self) -> datetime | None:
        """Load the persisted cooldown anchor; malformed values fail closed."""
        try:
            value = self._redis.get(self.last_successful_scale_key)
        except Exception:
            _LOG.exception("autoscaling last-success timestamp read failed")
            raise ControllerReconciliationRequired(
                "could not read persistent autoscaling cooldown"
            ) from None
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                raise ControllerReconciliationRequired(
                    "persistent autoscaling cooldown is not valid UTF-8"
                ) from None
        if not isinstance(value, str):
            raise ControllerReconciliationRequired(
                "persistent autoscaling cooldown has an invalid type"
            )
        try:
            at = datetime.fromisoformat(value)
        except (TypeError, ValueError, OverflowError):
            raise ControllerReconciliationRequired(
                "persistent autoscaling cooldown is malformed"
            ) from None
        if at.tzinfo is None or at.utcoffset() is None:
            raise ControllerReconciliationRequired(
                "persistent autoscaling cooldown must be timezone-aware"
            )
        return at.astimezone(timezone.utc)

    def begin_operation(self, decision: Any) -> str:
        """Persist scaling intent before an external Docker mutation can begin.

        The record has no TTL. If ownership is lost or the outcome is uncertain,
        a future runner must refuse new mutations until an operator reconciles it.
        """
        action = getattr(getattr(decision, "action", None), "value", None)
        current = getattr(decision, "current_replicas", None)
        target = getattr(decision, "target_replicas", None)
        if (
            action not in {"scale_out", "scale_in"}
            or type(current) is not int
            or type(target) is not int
            or current < 1
            or target != current + (1 if action == "scale_out" else -1)
            or target < 1
        ):
            raise ValueError("decision must describe an exact one-replica scale transition")

        with self._state_lock:
            if not self._is_owner_locked():
                raise ControllerLeaseLost("controller lease not held before intent persistence")
            token = self._token
            if token is None:
                raise ControllerLeaseLost("controller lease token unavailable")
            operation_id = secrets.token_urlsafe(18)
            reason = getattr(decision, "reason", "")
            if not isinstance(reason, str):
                reason = ""
            record = json.dumps(
                {
                    "schema_version": 2,
                    "phase": "preparing",
                    "operation_id": operation_id,
                    "action": action,
                    "current_replicas": current,
                    "target_replicas": target,
                    "reason": reason[:120],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            try:
                accepted = self._redis.set(self.operation_key, record, nx=True)
            except Exception:
                _LOG.exception("autoscaling operation intent persistence failed")
                raise ControllerReconciliationRequired(
                    "could not persist operation intent; scaling was not started"
                ) from None
            if not accepted:
                raise ControllerReconciliationRequired(
                    "unresolved scaling intent exists; scaling was not started"
                )
            self._operation_raw = record
            if not self._is_owner_locked():
                raise ControllerLeaseLost(
                    "controller lease lost after intent persistence; scaling was not started"
                )
            return operation_id

    def mark_scale_command_started(self) -> bool:
        """Persist a phase transition before the live scale command is invoked.

        Recovery may infer "not applied" from the original fleet only while
        this durable phase remains "preparing". A lost CAS response fails closed.
        """
        with self._state_lock:
            token = self._token
            raw = self._operation_raw
            if token is None or raw is None or not self._is_owner_locked():
                return False
            try:
                record = json.loads(raw)
                if (
                    type(record) is not dict
                    or record.get("schema_version") != 2
                    or record.get("phase") != "preparing"
                ):
                    return False
                record["phase"] = "scale_command_started"
                record["scale_command_started_at"] = datetime.now(timezone.utc).isoformat()
                updated = json.dumps(
                    record, sort_keys=True, separators=(",", ":")
                )
                accepted = self._redis.eval(
                    _MARK_SCALE_COMMAND_STARTED_SCRIPT,
                    2,
                    self.key,
                    self.operation_key,
                    token,
                    raw,
                    updated,
                )
            except Exception:
                _LOG.exception("autoscaling operation phase transition failed closed")
                return False
            if int(accepted or 0) != 1:
                return False
            if self._token != token or self._operation_raw != raw:
                return False
            self._operation_raw = updated
            return True

    def complete_pending_operation(
        self, *, succeeded: bool, at: datetime
    ) -> bool:
        """Atomically record the known outcome/cooldown and clear this exact intent.

        A durable completion marker makes retry safe if Redis commits the Lua
        script but the client loses its response. The script never clears a
        different intent and only the current lease owner can complete it.
        """
        if type(succeeded) is not bool:
            raise ValueError("succeeded must be a bool")
        if (
            not isinstance(at, datetime)
            or at.tzinfo is None
            or at.utcoffset() is None
        ):
            raise ValueError("at must be a timezone-aware datetime")
        completed_at = at.astimezone(timezone.utc).isoformat()
        last_scale_at = completed_at if succeeded else ""

        with self._state_lock:
            record = self._operation_raw
            token = self._token
            if record is None:
                return not self.has_pending_operation()
            if not self._is_owner_locked() or token is None:
                return False
        try:
            operation_id = json.loads(record)["operation_id"]
        except (TypeError, ValueError, KeyError):
            raise ControllerReconciliationRequired(
                "cannot complete malformed pending intent"
            ) from None
        completion = json.dumps(
            {
                "schema_version": 1,
                "operation_id": operation_id,
                "operation_record": record,
                "succeeded": succeeded,
                "completed_at": completed_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            result = self._redis.eval(
                _COMPLETE_OPERATION_SCRIPT,
                4,
                self.key,
                self.operation_key,
                self.last_successful_scale_key,
                self.last_completed_operation_key,
                token,
                record,
                last_scale_at,
                completion,
            )
        except Exception:
            _LOG.exception("autoscaling operation intent completion failed closed")
            return False
        if int(result or 0) not in {1, 2}:
            return False
        with self._state_lock:
            if self._token != token or self._operation_raw != record:
                return False
            self._operation_raw = None
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
        # Persist first. If this fails, do not call the external actuator.
        self.lease.begin_operation(decision)
        self._require_owner("after intent persistence and before actuation")
        result = self._actuator.apply(decision)
        # Intent remains durable until a known result is acknowledged. If the
        # lease disappeared during the external call, never erase the intent.
        self._require_owner("after actuation; outcome must be reconciled")
        return result

    def reconcile_pending_operation(self, decision: Any) -> Any:
        self._require_owner("before pending-operation reconciliation")

        reader = getattr(self.lease, "read_pending_operation", None)
        if not callable(reader):
            raise ControllerReconciliationRequired(
                "durable pending-operation reader unavailable"
            )

        operation = reader()
        if type(operation) is not dict:
            raise ControllerReconciliationRequired(
                "durable pending operation unavailable for reconciliation"
            )

        action = getattr(getattr(decision, "action", None), "value", None)
        current = getattr(decision, "current_replicas", None)
        target = getattr(decision, "target_replicas", None)

        if (
            operation.get("action") != action
            or type(current) is not int
            or type(target) is not int
            or type(operation.get("current_replicas")) is not int
            or type(operation.get("target_replicas")) is not int
            or operation.get("current_replicas") != current
            or operation.get("target_replicas") != target
        ):
            raise ControllerReconciliationRequired(
                "durable intent does not match the pending decision"
            )

        reconcile = getattr(
            self._actuator, "reconcile_pending_operation", None
        )
        if not callable(reconcile):
            raise ControllerReconciliationRequired(
                "actuator does not support pending-operation reconciliation"
            )

        result = reconcile(operation)
        self._require_owner(
            "after pending-operation reconciliation; outcome must remain durable"
        )
        return result


class LeaseGuardedAcknowledgement:
    """Protect reconciliation acknowledgements with the same lease instance."""

    def __init__(self, callback: Callable[..., Any], lease: RedisControllerLease) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback
        self.lease = lease
        self._callback_completed = False
        self._callback_result = None
        self._callback_succeeded: bool | None = None
        self._callback_at: datetime | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self.lease.is_owner():
            raise ControllerLeaseLost("controller lease not held before acknowledgement")
        # Keep the callback's original outcome/time. A lost Redis reply must not
        # cause the decision-engine callback to run twice or shift the cooldown.
        if not self._callback_completed:
            self._callback_result = self._callback(*args, **kwargs)
            self._callback_succeeded = kwargs.get("succeeded")
            self._callback_at = kwargs.get("at")
            self._callback_completed = True
        if not self.lease.is_owner():
            raise ControllerLeaseLost("controller lease lost during acknowledgement")
        if type(self._callback_succeeded) is not bool or not isinstance(
            self._callback_at, datetime
        ):
            raise ControllerReconciliationRequired(
                "acknowledgement must carry succeeded and aware at fields"
            )
        if not self.lease.complete_pending_operation(
            succeeded=self._callback_succeeded,
            at=self._callback_at,
        ):
            raise ControllerReconciliationRequired(
                "operation intent could not be safely completed; acknowledgement remains latched"
            )
        result = self._callback_result
        self._callback_completed = False
        self._callback_result = None
        self._callback_succeeded = None
        self._callback_at = None
        return result


class ControllerRunnerStatus(str, Enum):
    DISABLED = "disabled"
    BUSY = "busy"
    LEASE_NOT_ACQUIRED = "lease_not_acquired"
    OWNERSHIP_LOST = "ownership_lost"
    RECOVERY_REQUIRED = "recovery_required"
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
            elif self.lease.has_pending_operation():
                status = ControllerRunnerStatus.RECOVERY_REQUIRED
                detail = "pending_scale_operation_requires_operator_reconciliation"
            else:
                self.loop.run_once()
                cycles = 1
                if not self.lease.is_owner():
                    status = ControllerRunnerStatus.OWNERSHIP_LOST
                elif self.lease.has_pending_operation():
                    status = ControllerRunnerStatus.RECOVERY_REQUIRED
                    detail = "pending_scale_operation_requires_operator_reconciliation"
                else:
                    status = ControllerRunnerStatus.CYCLE_COMPLETE
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
            elif self.lease.has_pending_operation():
                status = ControllerRunnerStatus.RECOVERY_REQUIRED
                detail = "pending_scale_operation_requires_operator_reconciliation"
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
                if (
                    status is ControllerRunnerStatus.STOPPED
                    and self.lease.has_pending_operation()
                ):
                    status = ControllerRunnerStatus.RECOVERY_REQUIRED
                    detail = "pending_scale_operation_requires_operator_reconciliation"
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
