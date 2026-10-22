"""Preview-only orchestration for demand-responsive autoscaling."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from app.core.autoscaling.compose_adapter import (
    ComposeScalingAdapter,
    ScalePreviewResult,
    ScalePreviewStatus,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    ScalingAction,
    ScalingDecision,
    ScalingObservation,
)


class AutoscalingCycleStatus(str, Enum):
    """Outcome of one observation and preview cycle."""

    HOLD = "hold"
    PREVIEW_ACCEPTED = "preview_accepted"
    PREVIEW_REFUSED = "preview_refused"
    PREVIEW_FAILED = "preview_failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AutoscalingCycleResult:
    """Structured result without raw subprocess output or exception messages.

    PREVIEW_ACCEPTED means only that the Compose dry-run passed the adapter's
    checks. It does not mean that any infrastructure change was executed.
    """

    status: AutoscalingCycleStatus
    reason: str
    decision: ScalingDecision | None = None
    preview: ScalePreviewResult | None = None
    exception_type: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the cycle completed safely without a refusal or error."""
        return self.status in {
            AutoscalingCycleStatus.HOLD,
            AutoscalingCycleStatus.PREVIEW_ACCEPTED,
        }


class AutoscalingCoordinator:
    """Connect decision evaluation to the non-mutating Compose preview.

    This coordinator has no live scaling capability. It never treats a
    successful dry-run as an applied scale operation and never automatically
    acknowledges the decision engine's pending recommendation.

    If previewing raises unexpectedly, the recommendation remains pending.
    The caller must reconcile the outcome before acknowledging a scale result.
    """

    def __init__(
        self,
        decision_engine: AutoscalingDecisionEngine,
        preview_adapter: ComposeScalingAdapter,
    ) -> None:
        if not callable(getattr(decision_engine, "evaluate", None)):
            raise TypeError("decision_engine must provide evaluate()")
        if not callable(
            getattr(decision_engine, "record_scale_result", None)
        ):
            raise TypeError(
                "decision_engine must provide record_scale_result()"
            )
        if not callable(getattr(preview_adapter, "preview", None)):
            raise TypeError("preview_adapter must provide preview()")

        self._decision_engine = decision_engine
        self._preview_adapter = preview_adapter

    @property
    def project_name(self) -> str | None:
        """Configured Compose project, when the preview adapter exposes one."""
        value = getattr(self._preview_adapter, "project_name", None)
        return value if isinstance(value, str) else None

    def run_cycle(
        self,
        observation: ScalingObservation,
        *,
        evaluated_at: datetime,
        scale_in_drain_confirmed: bool = False,
    ) -> AutoscalingCycleResult:
        """Evaluate one observation and preview any actionable recommendation.

        The default is fail-closed for scale-in. The caller may set
        scale_in_drain_confirmed only after its independent drain procedure
        has verified routing removal and drain completion. This flag grants
        preview permission only; it does not enable live scaling.
        """
        if type(scale_in_drain_confirmed) is not bool:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="invalid_scale_in_drain_confirmation",
            )

        try:
            decision = self._decision_engine.evaluate(
                observation,
                evaluated_at=evaluated_at,
            )
        except Exception as exc:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="decision_evaluation_failed",
                exception_type=type(exc).__name__,
            )

        if not isinstance(decision, ScalingDecision):
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="decision_engine_invalid_result",
            )

        if decision.action is ScalingAction.HOLD:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.HOLD,
                reason=decision.reason,
                decision=decision,
            )

        if decision.action not in {
            ScalingAction.SCALE_OUT,
            ScalingAction.SCALE_IN,
        }:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="unsupported_decision_action",
                decision=decision,
            )

        try:
            preview = self._preview_adapter.preview(
                decision,
                scale_in_drain_confirmed=scale_in_drain_confirmed,
            )
        except Exception as exc:
            # Do not acknowledge the pending recommendation here. A thrown
            # exception must be reconciled before another scale is proposed.
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="compose_preview_exception",
                decision=decision,
                exception_type=type(exc).__name__,
            )

        if not isinstance(preview, ScalePreviewResult):
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.ERROR,
                reason="compose_preview_invalid_result",
                decision=decision,
            )

        if preview.status is ScalePreviewStatus.DRY_RUN_ACCEPTED:
            if not preview.ok:
                return AutoscalingCycleResult(
                    status=AutoscalingCycleStatus.ERROR,
                    reason="inconsistent_preview_result",
                    decision=decision,
                    preview=preview,
                )
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.PREVIEW_ACCEPTED,
                reason=preview.reason,
                decision=decision,
                preview=preview,
            )

        if preview.status is ScalePreviewStatus.REFUSED:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.PREVIEW_REFUSED,
                reason=preview.reason,
                decision=decision,
                preview=preview,
            )

        if preview.status is ScalePreviewStatus.FAILED:
            return AutoscalingCycleResult(
                status=AutoscalingCycleStatus.PREVIEW_FAILED,
                reason=preview.reason,
                decision=decision,
                preview=preview,
            )

        # An actionable decision unexpectedly producing NOOP is not accepted.
        return AutoscalingCycleResult(
            status=AutoscalingCycleStatus.ERROR,
            reason="unexpected_preview_status",
            decision=decision,
            preview=preview,
        )

    def record_scale_result(
        self,
        *,
        succeeded: bool,
        at: datetime,
    ) -> None:
        """Forward a real actuator result to the decision engine.

        Call this only after an actuator has established the outcome of the
        requested replica change. An accepted dry-run is not such an outcome.
        """
        self._decision_engine.record_scale_result(
            succeeded=succeeded,
            at=at,
        )
