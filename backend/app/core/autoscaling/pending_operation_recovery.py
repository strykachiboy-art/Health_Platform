"""Explicit recovery for a durable autoscaling intent after process/lease loss."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from app.core.autoscaling.controller_lease import (
    ControllerLeaseLost,
    ControllerReconciliationRequired,
    RedisControllerLease,
)
from app.core.autoscaling.live_actuator import (
    ComposeLiveScalingActuator,
    ScaleActuationResult,
    ScaleActuationStatus,
)

_LOG = logging.getLogger(__name__)


class PendingOperationRecoveryStatus(str, Enum):
    NO_PENDING_OPERATION = "no_pending_operation"
    RECOVERED_APPLIED = "recovered_applied"
    RECOVERED_NOT_APPLIED = "recovered_not_applied"
    RECOVERY_REQUIRED = "recovery_required"


@dataclass(frozen=True, slots=True)
class PendingOperationRecoveryResult:
    status: PendingOperationRecoveryStatus
    reason: str
    operation_id: str | None = None
    actuation_result: ScaleActuationResult | None = None

    @property
    def completed(self) -> bool:
        return self.status in {
            PendingOperationRecoveryStatus.NO_PENDING_OPERATION,
            PendingOperationRecoveryStatus.RECOVERED_APPLIED,
            PendingOperationRecoveryStatus.RECOVERED_NOT_APPLIED,
        }


class PendingOperationRecovery:
    """Reconcile one journal record while holding the controller lease.

    The recovery handler never issues a scale command. The actuator may restore
    admission for a scale-in's original, verified fleet by undraining it. Any
    ambiguity leaves the journal untouched and blocks normal autoscaling.
    """

    def __init__(
        self,
        lease: RedisControllerLease,
        actuator: ComposeLiveScalingActuator,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(getattr(lease, "read_pending_operation", None)):
            raise TypeError("lease must provide read_pending_operation()")
        if not callable(getattr(lease, "complete_pending_operation", None)):
            raise TypeError("lease must provide complete_pending_operation()")
        if not callable(getattr(actuator, "reconcile_pending_operation", None)):
            raise TypeError("actuator must provide reconcile_pending_operation()")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._lease = lease
        self._actuator = actuator
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def run_once(self) -> PendingOperationRecoveryResult:
        if not self._lease.is_owner():
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "controller_lease_not_owned",
            )
        try:
            intent = self._lease.read_pending_operation()
        except (ControllerLeaseLost, ControllerReconciliationRequired) as exc:
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "pending_intent_could_not_be_loaded:" + type(exc).__name__,
            )
        except Exception as exc:
            _LOG.exception("pending autoscaling intent could not be loaded")
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "pending_intent_read_failed:" + type(exc).__name__,
            )

        if intent is None:
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.NO_PENDING_OPERATION,
                "no_pending_operation",
            )

        operation_id = intent.get("operation_id")
        try:
            outcome = self._actuator.reconcile_pending_operation(intent)
        except Exception as exc:
            _LOG.exception("pending autoscaling intent reconciliation failed")
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "fleet_reconciliation_failed:" + type(exc).__name__,
                operation_id if isinstance(operation_id, str) else None,
            )

        if not isinstance(outcome, ScaleActuationResult) or outcome.status not in {
            ScaleActuationStatus.APPLIED,
            ScaleActuationStatus.FAILED,
        }:
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "fleet_state_not_proven_safe_to_clear_intent",
                operation_id if isinstance(operation_id, str) else None,
                outcome if isinstance(outcome, ScaleActuationResult) else None,
            )

        if not self._lease.is_owner():
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "controller_lease_lost_during_recovery",
                operation_id if isinstance(operation_id, str) else None,
                outcome,
            )

        at = self._clock()
        if (
            not isinstance(at, datetime)
            or at.tzinfo is None
            or at.utcoffset() is None
        ):
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "recovery_clock_must_be_timezone_aware",
                operation_id if isinstance(operation_id, str) else None,
                outcome,
            )
        try:
            cleared = self._lease.complete_pending_operation(
                succeeded=outcome.succeeded,
                at=at,
            )
        except Exception as exc:
            _LOG.exception("reconciled autoscaling intent completion failed")
            cleared = False
            completion_error = type(exc).__name__
        else:
            completion_error = "none"

        if not cleared:
            return PendingOperationRecoveryResult(
                PendingOperationRecoveryStatus.RECOVERY_REQUIRED,
                "verified_fleet_but_intent_clear_not_confirmed:" + completion_error,
                operation_id if isinstance(operation_id, str) else None,
                outcome,
            )

        status = (
            PendingOperationRecoveryStatus.RECOVERED_APPLIED
            if outcome.succeeded
            else PendingOperationRecoveryStatus.RECOVERED_NOT_APPLIED
        )
        return PendingOperationRecoveryResult(
            status,
            outcome.reason,
            operation_id if isinstance(operation_id, str) else None,
            outcome,
        )
