"""Controlled autoscaling orchestration with fail-closed reconciliation.

Scale-out requires an accepted dry-run preview. Scale-in may proceed only when
its initial preview is refused for the precise missing-drain confirmation; the
live actuator must then confirm drain before re-previewing and scaling. Unknown
outcomes remain pending and are never blindly retried as infrastructure changes.

A lease-guarded runtime persists operation intent before actuation and blocks a
new controller while an unresolved operation requires reconciliation.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from app.core.autoscaling.compose_adapter import ScalePreviewStatus
from app.core.autoscaling.coordinator import AutoscalingCycleStatus
from app.core.autoscaling.decision_engine import ScalingAction, ScalingDecision


_LOG = logging.getLogger(__name__)


class ReconciliationStatus(str, Enum):
    """Externally reportable result of one reconciliation-loop iteration."""

    DISABLED = "disabled"
    BUSY = "busy"
    HOLD = "hold"
    PREVIEW_BLOCKED = "preview_blocked"
    ACTUATION_APPLIED = "actuation_applied"
    ACTUATION_FAILED = "actuation_failed"
    ACTUATION_REFUSED = "actuation_refused"
    RECONCILED_APPLIED = "reconciled_applied"
    UNCERTAIN = "uncertain"
    SCALE_IN_DEFERRED = "scale_in_deferred"
    ACKNOWLEDGEMENT_FAILED = "acknowledgement_failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Sanitized result. Raw subprocess output and exception messages are omitted."""

    status: ReconciliationStatus
    reason: str
    evaluated_at: datetime | None = None
    controller_result: Any = None
    actuation_result: Any = None
    exception_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in {
            ReconciliationStatus.DISABLED,
            ReconciliationStatus.BUSY,
            ReconciliationStatus.HOLD,
            ReconciliationStatus.ACTUATION_APPLIED,
            ReconciliationStatus.RECONCILED_APPLIED,
        }


class AutoscalingReconciliationLoop:
    """Single-process controller/actuator orchestration.

    ``acknowledge_scale_result`` should be wired to the coordinator's
    ``record_scale_result`` method.

    The nonblocking lock prevents overlapping iterations on this instance.
    Distributed ownership is supplied by the outer Redis lease runner; this
    loop-local lock is not a substitute for that lease or the durable operation
    intent barrier.
    """

    def __init__(
        self,
        *,
        controller: Any,
        actuator: Any,
        acknowledge_scale_result: Callable[..., None],
        enabled: bool = False,
        interval_seconds: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("enabled must be a bool")

        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not math.isfinite(float(interval_seconds))
            or interval_seconds <= 0
        ):
            raise ValueError("interval_seconds must be finite and > 0")

        if not callable(getattr(controller, "run_cycle", None)):
            raise TypeError("controller must provide run_cycle()")

        if not callable(getattr(actuator, "apply", None)):
            raise TypeError("actuator must provide apply()")

        if not callable(acknowledge_scale_result):
            raise TypeError("acknowledge_scale_result must be callable")

        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")

        self.enabled = enabled
        self.interval_seconds = float(interval_seconds)
        self._controller = controller
        self._actuator = actuator
        self._acknowledge = acknowledge_scale_result
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()

        self._pending_decision: ScalingDecision | None = None
        self._pending_kind: str | None = None
        self._confirmed_result: bool | None = None
        self._pending_actuation_result: Any = None
        self._pending_blocked = False
        self._pending_reconciled = False

    def run_once(
        self,
        *,
        evaluated_at: datetime | None = None,
    ) -> ReconciliationResult:
        """Perform one serialized cycle; this method never starts a worker."""
        if not self.enabled:
            return ReconciliationResult(
                ReconciliationStatus.DISABLED,
                "live_reconciliation_disabled",
            )

        if not self._lock.acquire(blocking=False):
            return ReconciliationResult(
                ReconciliationStatus.BUSY,
                "another_reconciliation_cycle_is_running",
            )

        try:
            at = evaluated_at if evaluated_at is not None else self._clock()

            if (
                not isinstance(at, datetime)
                or at.tzinfo is None
                or at.utcoffset() is None
            ):
                return ReconciliationResult(
                    ReconciliationStatus.ERROR,
                    "timezone_aware_timestamp_required",
                )

            # A known actuator outcome whose acknowledgement failed may be
            # retried as an acknowledgement only, never as another mutation.
            if self._pending_kind == "acknowledge_known_result":
                return self._retry_known_acknowledgement(
                    at,
                    actuation_result=self._pending_actuation_result,
                    blocked=self._pending_blocked,
                    reconciled=self._pending_reconciled,
                )

            controller_result, cycle, error_type = self._run_controller(at)

            if error_type is not None:
                if self._pending_decision is not None:
                    return ReconciliationResult(
                        ReconciliationStatus.UNCERTAIN,
                        "pending_actuation_could_not_be_reconciled",
                        evaluated_at=at,
                        exception_type=error_type,
                    )

                return ReconciliationResult(
                    ReconciliationStatus.ERROR,
                    "controller_cycle_failed",
                    evaluated_at=at,
                    exception_type=error_type,
                )

            if self._pending_decision is not None:
                return self._reconcile_pending(
                    at=at,
                    controller_result=controller_result,
                )

            if cycle is None:
                return ReconciliationResult(
                    ReconciliationStatus.ERROR,
                    "controller_result_invalid",
                    evaluated_at=at,
                    controller_result=controller_result,
                )

            decision = getattr(cycle, "decision", None)
            cycle_status = getattr(cycle, "status", None)

            if cycle_status is AutoscalingCycleStatus.HOLD:
                return ReconciliationResult(
                    ReconciliationStatus.HOLD,
                    getattr(cycle, "reason", "decision_engine_hold"),
                    evaluated_at=at,
                    controller_result=controller_result,
                )

            if not isinstance(decision, ScalingDecision):
                return ReconciliationResult(
                    ReconciliationStatus.ERROR,
                    "controller_returned_no_valid_scale_decision",
                    evaluated_at=at,
                    controller_result=controller_result,
                )

            if decision.action is ScalingAction.HOLD:
                return ReconciliationResult(
                    ReconciliationStatus.HOLD,
                    decision.reason,
                    evaluated_at=at,
                    controller_result=controller_result,
                )

            if decision.action not in {
                ScalingAction.SCALE_OUT,
                ScalingAction.SCALE_IN,
            }:
                return ReconciliationResult(
                    ReconciliationStatus.ERROR,
                    "unsupported_scale_action",
                    evaluated_at=at,
                    controller_result=controller_result,
                )

            preview = getattr(cycle, "preview", None)
            preview_accepted = (
                cycle_status is AutoscalingCycleStatus.PREVIEW_ACCEPTED
                and preview is not None
                and getattr(preview, "ok", False) is True
            )
            scale_in_drain_gate = (
                decision.action is ScalingAction.SCALE_IN
                and cycle_status is AutoscalingCycleStatus.PREVIEW_REFUSED
                and preview is not None
                and getattr(preview, "status", None) is ScalePreviewStatus.REFUSED
                and getattr(preview, "reason", None) == "scale_in_drain_not_confirmed"
            )

            # The only scale-in preview refusal allowed to reach the live
            # actuator is the exact missing-drain gate. The actuator itself
            # must drain, verify zero active HTTP/socket work, re-preview with
            # drain confirmation, and re-check inventory before applying scale.
            if (
                decision.action is ScalingAction.SCALE_IN
                and not scale_in_drain_gate
            ) or (
                decision.action is ScalingAction.SCALE_OUT
                and not preview_accepted
            ):
                # No live actuator command has been issued. This is a known
                # no-mutation outcome, not an uncertain external operation.
                self._pending_decision = decision
                self._pending_kind = "acknowledge_known_result"
                self._confirmed_result = False
                self._pending_actuation_result = None
                self._pending_blocked = True
                self._pending_reconciled = False
                return self._retry_known_acknowledgement(
                    at,
                    controller_result=controller_result,
                    blocked=True,
                )

            # Record intent before calling the actuator. If the call raises or
            # its result is ambiguous, a later cycle reconciles inventory and
            # never blindly sends the same mutation again.
            self._pending_decision = decision
            self._pending_kind = "actuation"
            self._confirmed_result = None

            try:
                actuation = self._actuator.apply(decision)
            except Exception as exc:
                return ReconciliationResult(
                    ReconciliationStatus.UNCERTAIN,
                    "actuator_exception_requires_reconciliation",
                    evaluated_at=at,
                    controller_result=controller_result,
                    exception_type=type(exc).__name__,
                )

            may_ack = getattr(actuation, "may_acknowledge_decision", None)
            succeeded = getattr(actuation, "succeeded", None)

            if type(may_ack) is not bool or type(succeeded) is not bool:
                return ReconciliationResult(
                    ReconciliationStatus.UNCERTAIN,
                    "actuator_result_contract_invalid",
                    evaluated_at=at,
                    controller_result=controller_result,
                    actuation_result=actuation,
                )

            if not may_ack:
                return ReconciliationResult(
                    ReconciliationStatus.UNCERTAIN,
                    "actuator_outcome_not_established",
                    evaluated_at=at,
                    controller_result=controller_result,
                    actuation_result=actuation,
                )

            self._confirmed_result = succeeded
            self._pending_kind = "acknowledge_known_result"
            self._pending_actuation_result = actuation
            self._pending_blocked = False
            self._pending_reconciled = False

            acknowledged = self._retry_known_acknowledgement(
                at,
                controller_result=controller_result,
                actuation_result=actuation,
            )

            if acknowledged.status is ReconciliationStatus.ACTUATION_APPLIED:
                return acknowledged

            if acknowledged.status is ReconciliationStatus.ACTUATION_FAILED:
                return acknowledged

            return acknowledged

        finally:
            self._lock.release()

    def _run_controller(self, at: datetime) -> tuple[Any, Any, str | None]:
        try:
            result = self._controller.run_cycle(
                evaluated_at=at,
                scale_in_drain_confirmed=False,
            )
        except Exception as exc:
            return None, None, type(exc).__name__

        cycle = getattr(result, "cycle", None)
        if cycle is None:
            return result, None, "InvalidControllerResult"

        return result, cycle, None

    def _retry_known_acknowledgement(
        self,
        at: datetime,
        *,
        controller_result: Any = None,
        actuation_result: Any = None,
        blocked: bool = False,
        reconciled: bool = False,
    ) -> ReconciliationResult:
        succeeded = self._confirmed_result

        if type(succeeded) is not bool:
            return ReconciliationResult(
                ReconciliationStatus.ERROR,
                "known_outcome_missing",
                evaluated_at=at,
                controller_result=controller_result,
                actuation_result=actuation_result,
            )

        try:
            self._acknowledge(succeeded=succeeded, at=at)
        except Exception as exc:
            return ReconciliationResult(
                ReconciliationStatus.ACKNOWLEDGEMENT_FAILED,
                "scale_result_acknowledgement_failed",
                evaluated_at=at,
                controller_result=controller_result,
                actuation_result=actuation_result,
                exception_type=type(exc).__name__,
            )

        self._clear_pending()

        if blocked:
            reason = (
                "preview_not_accepted_no_mutation"
            )
            return ReconciliationResult(
                ReconciliationStatus.PREVIEW_BLOCKED,
                reason,
                evaluated_at=at,
                controller_result=controller_result,
            )

        if (
            not succeeded
            and not reconciled
            and getattr(getattr(actuation_result, "status", None), "value", None)
            == "refused"
        ):
            return ReconciliationResult(
                ReconciliationStatus.ACTUATION_REFUSED,
                "actuation_refused_no_mutation",
                evaluated_at=at,
                controller_result=controller_result,
                actuation_result=actuation_result,
            )

        return ReconciliationResult(
            (
                ReconciliationStatus.RECONCILED_APPLIED
                if succeeded and reconciled
                else (
                    ReconciliationStatus.ACTUATION_APPLIED
                    if succeeded
                    else ReconciliationStatus.ACTUATION_FAILED
                )
            ),
            (
                "target_replica_count_confirmed_healthy"
                if succeeded and reconciled
                else (
                    "actuation_confirmed"
                    if succeeded
                    else "actuation_failed_outcome_known"
                )
            ),
            evaluated_at=at,
            controller_result=controller_result,
            actuation_result=actuation_result,
        )

    def _reconcile_pending(
        self,
        *,
        at: datetime,
        controller_result: Any,
    ) -> ReconciliationResult:
        if self._pending_kind == "scale_in_deferred":
            return ReconciliationResult(
                ReconciliationStatus.SCALE_IN_DEFERRED,
                "scale_in_still_requires_pre_preview_drain_coordination",
                evaluated_at=at,
                controller_result=controller_result,
            )

        decision = self._pending_decision
        snapshot = getattr(controller_result, "replicas", None)
        observed = getattr(snapshot, "current_replicas", None)
        healthy = getattr(snapshot, "healthy", False)

        if (
            decision is not None
            and healthy is True
            and type(observed) is int
            and observed == decision.target_replicas
        ):
            self._pending_kind = "acknowledge_known_result"
            self._confirmed_result = True
            self._pending_actuation_result = None
            self._pending_blocked = False
            self._pending_reconciled = True
            return self._retry_known_acknowledgement(
                at,
                controller_result=controller_result,
                reconciled=True,
            )

        # A missing, unhealthy, old, or unexpected replica count is not proof
        # that the operation failed. Keep the recommendation pending.
        return ReconciliationResult(
            ReconciliationStatus.UNCERTAIN,
            "target_replica_count_not_confirmed_healthy",
            evaluated_at=at,
            controller_result=controller_result,
        )

    def _clear_pending(self) -> None:
        self._pending_decision = None
        self._pending_kind = None
        self._confirmed_result = None
        self._pending_actuation_result = None
        self._pending_blocked = False
        self._pending_reconciled = False

    def run_forever(self, stop_event: Any) -> None:
        """Run periodic cycles when explicitly enabled and owned by one process.

        The caller supplies a standard stop event with ``is_set()`` and
        ``wait(timeout)``. No worker or thread is created by this method.
        """
        if not self.enabled:
            return

        if not callable(getattr(stop_event, "is_set", None)) or not callable(
            getattr(stop_event, "wait", None)
        ):
            raise TypeError("stop_event must provide is_set() and wait()")

        while not stop_event.is_set():
            result = self.run_once()
            _LOG.info(
                "autoscaling_reconciliation status=%s reason=%s",
                result.status.value,
                result.reason,
            )
            stop_event.wait(self.interval_seconds)