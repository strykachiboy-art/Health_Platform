from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.autoscaling.compose_adapter import (
    ScalePreviewResult,
    ScalePreviewStatus,
)
from app.core.autoscaling.coordinator import (
    AutoscalingCycleResult,
    AutoscalingCycleStatus,
)
from app.core.autoscaling.decision_engine import (
    ScalingAction,
    ScalingDecision,
)
from app.core.autoscaling.reconciliation_loop import (
    AutoscalingReconciliationLoop,
    ReconciliationStatus,
)


AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def decision(action=ScalingAction.SCALE_OUT, current=2, target=3):
    return ScalingDecision(
        action,
        current,
        target,
        "sustained_demand",
        0.9 if action is ScalingAction.SCALE_OUT else 0.1,
    )


def preview(*, accepted=True, current=2, target=3):
    return ScalePreviewResult(
        status=(
            ScalePreviewStatus.DRY_RUN_ACCEPTED
            if accepted
            else ScalePreviewStatus.REFUSED
        ),
        reason=(
            "dry_run_validated_no_infrastructure_change"
            if accepted
            else "compose_scale_plan_unsafe"
        ),
        current_replicas=current,
        target_replicas=target,
        observed_running_replicas=current,
        exit_code=0,
        command=("docker", "compose", "scale", "--dry-run"),
    )


def cycle_result(
    action=ScalingAction.SCALE_OUT,
    *,
    status=AutoscalingCycleStatus.PREVIEW_ACCEPTED,
    current=2,
    target=3,
    accepted=True,
    reason="test_cycle",
):
    decision_value = decision(action, current, target)
    cycle = AutoscalingCycleResult(
        status=status,
        reason=reason,
        decision=decision_value,
        preview=(
            preview(accepted=accepted, current=current, target=target)
            if status
            in {
                AutoscalingCycleStatus.PREVIEW_ACCEPTED,
                AutoscalingCycleStatus.PREVIEW_REFUSED,
                AutoscalingCycleStatus.PREVIEW_FAILED,
            }
            else None
        ),
    )
    return SimpleNamespace(
        cycle=cycle,
        replicas=SimpleNamespace(
            current_replicas=current,
            healthy=True,
            reason="ok",
        ),
    )


class FakeController:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def run_cycle(self, *, evaluated_at, scale_in_drain_confirmed=False):
        self.calls.append((evaluated_at, scale_in_drain_confirmed))
        if self.results:
            return self.results.pop(0)
        return cycle_result(
            action=ScalingAction.HOLD,
            status=AutoscalingCycleStatus.HOLD,
            reason="scale_action_pending",
        )


class FakeActuator:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    def apply(self, selected_decision):
        self.calls.append(selected_decision)
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return SimpleNamespace(
            succeeded=True,
            may_acknowledge_decision=True,
            status=SimpleNamespace(value="applied"),
            observed_replicas=selected_decision.target_replicas,
        )


def actuation(*, succeeded=True, may_ack=True, observed=3):
    status = "applied" if succeeded else "failed"
    return SimpleNamespace(
        succeeded=succeeded,
        may_acknowledge_decision=may_ack,
        status=SimpleNamespace(value=status),
        observed_replicas=observed,
    )


def make_loop(
    *,
    enabled=True,
    controller_results=None,
    actuator=None,
    ack_error=None,
):
    controller = FakeController(controller_results or [])
    selected_actuator = actuator or FakeActuator()
    acknowledgements = []

    def acknowledge(*, succeeded, at):
        if ack_error is not None:
            raise ack_error
        acknowledgements.append((succeeded, at))

    loop = AutoscalingReconciliationLoop(
        controller=controller,
        actuator=selected_actuator,
        acknowledge_scale_result=acknowledge,
        enabled=enabled,
        clock=lambda: AT,
    )
    return loop, controller, selected_actuator, acknowledgements


def test_disabled_by_default_never_calls_controller_or_actuator():
    loop, controller, actuator, acknowledgements = make_loop(enabled=False)

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.DISABLED
    assert result.ok
    assert controller.calls == []
    assert actuator.calls == []
    assert acknowledgements == []


def test_scale_out_is_applied_then_acknowledged():
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[cycle_result()],
    )

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.ACTUATION_APPLIED
    assert result.ok
    assert len(controller.calls) == 1
    assert controller.calls[0][1] is False
    assert len(actuator.calls) == 1
    assert acknowledgements == [(True, AT)]


def test_scale_in_is_deferred_and_never_bypasses_drain_gate():
    selected = cycle_result(
        action=ScalingAction.SCALE_IN,
        status=AutoscalingCycleStatus.PREVIEW_REFUSED,
        current=3,
        target=2,
        accepted=False,
        reason="scale_in_drain_not_confirmed",
    )
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[selected, selected],
    )

    first = loop.run_once(evaluated_at=AT)
    second = loop.run_once(
        evaluated_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc),
    )

    assert first.status is ReconciliationStatus.SCALE_IN_DEFERRED
    assert second.status is ReconciliationStatus.SCALE_IN_DEFERRED
    assert actuator.calls == []
    assert acknowledgements == []


def test_preview_refusal_for_scale_out_is_acknowledged_as_no_mutation():
    refused = cycle_result(
        status=AutoscalingCycleStatus.PREVIEW_REFUSED,
        accepted=False,
        reason="compose_scale_plan_unsafe",
    )
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[refused],
    )

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.PREVIEW_BLOCKED
    assert actuator.calls == []
    assert acknowledgements == [(False, AT)]


def test_confirmed_actuator_failure_is_acknowledged_as_failure():
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[cycle_result()],
        actuator=FakeActuator([actuation(succeeded=False, observed=2)]),
    )

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.ACTUATION_FAILED
    assert acknowledgements == [(False, AT)]


def test_uncertain_outcome_is_not_retried_and_target_health_reconciles_it():
    hold_after_apply = cycle_result(
        action=ScalingAction.HOLD,
        status=AutoscalingCycleStatus.HOLD,
        current=3,
        target=3,
        reason="scale_action_pending",
    )
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[
            cycle_result(current=2, target=3),
            hold_after_apply,
        ],
        actuator=FakeActuator([actuation(succeeded=False, may_ack=False)]),
    )

    first = loop.run_once(evaluated_at=AT)
    later = datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc)
    second = loop.run_once(evaluated_at=later)

    assert first.status is ReconciliationStatus.UNCERTAIN
    assert second.status is ReconciliationStatus.RECONCILED_APPLIED
    assert len(actuator.calls) == 1
    assert acknowledgements == [(True, later)]


def test_uncertain_result_with_old_count_remains_pending_without_retry():
    old_count = cycle_result(
        action=ScalingAction.HOLD,
        status=AutoscalingCycleStatus.HOLD,
        current=2,
        target=2,
        reason="scale_action_pending",
    )
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[
            cycle_result(current=2, target=3),
            old_count,
        ],
        actuator=FakeActuator([actuation(succeeded=False, may_ack=False)]),
    )

    first = loop.run_once(evaluated_at=AT)
    second = loop.run_once(
        evaluated_at=datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc),
    )

    assert first.status is ReconciliationStatus.UNCERTAIN
    assert second.status is ReconciliationStatus.UNCERTAIN
    assert len(actuator.calls) == 1
    assert acknowledgements == []


def test_actuator_exception_preserves_pending_recommendation():
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[cycle_result()],
        actuator=FakeActuator(error=TimeoutError("private detail")),
    )

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.UNCERTAIN
    assert result.exception_type == "TimeoutError"
    assert "private detail" not in repr(result)
    assert acknowledgements == []


def test_invalid_timestamp_fails_closed_before_controller_call():
    loop, controller, actuator, acknowledgements = make_loop()

    result = loop.run_once(evaluated_at=datetime(2026, 1, 1))

    assert result.status is ReconciliationStatus.ERROR
    assert result.reason == "timezone_aware_timestamp_required"
    assert controller.calls == []
    assert actuator.calls == []
    assert acknowledgements == []


def test_failed_acknowledgement_does_not_resend_mutation():
    loop, controller, actuator, acknowledgements = make_loop(
        controller_results=[cycle_result()],
        ack_error=RuntimeError("private detail"),
    )

    result = loop.run_once(evaluated_at=AT)

    assert result.status is ReconciliationStatus.ACKNOWLEDGEMENT_FAILED
    assert result.exception_type == "RuntimeError"
    assert len(actuator.calls) == 1
    assert acknowledgements == []