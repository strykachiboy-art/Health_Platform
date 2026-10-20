from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling.compose_adapter import (
    ScalePreviewResult,
    ScalePreviewStatus,
)
from app.core.autoscaling.coordinator import (
    AutoscalingCoordinator,
    AutoscalingCycleStatus,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
    ScalingAction,
    ScalingObservation,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_engine():
    policy = AutoscalingPolicy(
        min_replicas=2,
        max_replicas=5,
        scale_out_load_ratio=0.8,
        scale_in_load_ratio=0.2,
        scale_out_window_seconds=10,
        scale_in_window_seconds=10,
        cooldown_seconds=30,
        max_metrics_age_seconds=60,
        max_observation_gap_seconds=30,
    )
    return AutoscalingDecisionEngine(policy)


def observation(at, *, replicas=2, ratio=0.9, healthy=True):
    return ScalingObservation(
        current_replicas=replicas,
        load_ratio=ratio,
        observed_at=at,
        healthy=healthy,
    )


def preview_result(
    status=ScalePreviewStatus.DRY_RUN_ACCEPTED,
    *,
    current=2,
    target=3,
    reason="dry_run_validated_no_infrastructure_change",
):
    return ScalePreviewResult(
        status=status,
        reason=reason,
        current_replicas=current,
        target_replicas=target,
        observed_running_replicas=current,
        exit_code=0,
        command=("docker", "compose", "scale", "--dry-run"),
    )


class FakePreviewAdapter:
    def __init__(self, result=None, error=None):
        self.result = result or preview_result()
        self.error = error
        self.calls = []

    def preview(self, decision, *, scale_in_drain_confirmed=False):
        self.calls.append((decision, scale_in_drain_confirmed))
        if self.error is not None:
            raise self.error
        return self.result


def test_hold_decision_does_not_call_preview_adapter():
    engine = make_engine()
    adapter = FakePreviewAdapter()
    coordinator = AutoscalingCoordinator(engine, adapter)

    result = coordinator.run_cycle(
        observation(START),
        evaluated_at=START,
    )

    assert result.status is AutoscalingCycleStatus.HOLD
    assert result.reason == "scale_out_window_not_met"
    assert result.preview is None
    assert adapter.calls == []


def test_sustained_demand_is_previewed_without_acknowledging_scale():
    engine = make_engine()
    adapter = FakePreviewAdapter(
        preview_result(current=2, target=3)
    )
    coordinator = AutoscalingCoordinator(engine, adapter)

    first = coordinator.run_cycle(
        observation(START),
        evaluated_at=START,
    )
    second_time = START + timedelta(seconds=10)
    second = coordinator.run_cycle(
        observation(second_time),
        evaluated_at=second_time,
    )

    assert first.status is AutoscalingCycleStatus.HOLD
    assert second.status is AutoscalingCycleStatus.PREVIEW_ACCEPTED
    assert second.ok is True
    assert second.decision is not None
    assert second.decision.action is ScalingAction.SCALE_OUT
    assert second.preview is not None
    assert second.preview.target_replicas == 3
    assert engine.pending_target_replicas == 3
    assert len(adapter.calls) == 1

    # The accepted dry-run did not apply a change. A subsequent cycle cannot
    # emit another recommendation until an actual result is acknowledged.
    later = second_time + timedelta(seconds=10)
    next_result = coordinator.run_cycle(
        observation(later),
        evaluated_at=later,
    )
    assert next_result.status is AutoscalingCycleStatus.HOLD
    assert next_result.reason == "scale_action_pending"
    assert len(adapter.calls) == 1


def test_unsafe_preview_is_reported_as_refused():
    engine = make_engine()
    adapter = FakePreviewAdapter(
        preview_result(
            ScalePreviewStatus.REFUSED,
            reason="compose_scale_plan_unsafe",
        )
    )
    coordinator = AutoscalingCoordinator(engine, adapter)

    coordinator.run_cycle(
        observation(START),
        evaluated_at=START,
    )
    at = START + timedelta(seconds=10)
    result = coordinator.run_cycle(
        observation(at),
        evaluated_at=at,
    )

    assert result.status is AutoscalingCycleStatus.PREVIEW_REFUSED
    assert result.ok is False
    assert result.reason == "compose_scale_plan_unsafe"
    assert engine.pending_target_replicas == 3


def test_stale_metrics_hold_without_calling_preview():
    engine = make_engine()
    adapter = FakePreviewAdapter()
    coordinator = AutoscalingCoordinator(engine, adapter)
    evaluated_at = START + timedelta(seconds=120)

    result = coordinator.run_cycle(
        observation(START),
        evaluated_at=evaluated_at,
    )

    assert result.status is AutoscalingCycleStatus.HOLD
    assert result.reason == "stale_metrics"
    assert adapter.calls == []


def test_scale_in_does_not_assume_drain_is_confirmed():
    engine = make_engine()
    adapter = FakePreviewAdapter(
        preview_result(
            ScalePreviewStatus.REFUSED,
            current=3,
            target=2,
            reason="scale_in_drain_not_confirmed",
        )
    )
    coordinator = AutoscalingCoordinator(engine, adapter)

    coordinator.run_cycle(
        observation(START, replicas=3, ratio=0.1),
        evaluated_at=START,
    )
    at = START + timedelta(seconds=10)
    result = coordinator.run_cycle(
        observation(at, replicas=3, ratio=0.1),
        evaluated_at=at,
    )

    assert result.status is AutoscalingCycleStatus.PREVIEW_REFUSED
    assert result.reason == "scale_in_drain_not_confirmed"
    assert adapter.calls[0][0].action is ScalingAction.SCALE_IN
    assert adapter.calls[0][1] is False


def test_decision_engine_exception_fails_closed():
    engine = make_engine()
    adapter = FakePreviewAdapter()
    coordinator = AutoscalingCoordinator(engine, adapter)

    result = coordinator.run_cycle(
        observation(START),
        evaluated_at=datetime(2026, 1, 1),
    )

    assert result.status is AutoscalingCycleStatus.ERROR
    assert result.reason == "decision_evaluation_failed"
    assert result.exception_type == "ValueError"
    assert adapter.calls == []


def test_preview_exception_does_not_expose_exception_message():
    engine = make_engine()
    adapter = FakePreviewAdapter(
        error=RuntimeError("private diagnostic detail")
    )
    coordinator = AutoscalingCoordinator(engine, adapter)

    coordinator.run_cycle(
        observation(START),
        evaluated_at=START,
    )
    at = START + timedelta(seconds=10)
    result = coordinator.run_cycle(
        observation(at),
        evaluated_at=at,
    )

    assert result.status is AutoscalingCycleStatus.ERROR
    assert result.reason == "compose_preview_exception"
    assert result.exception_type == "RuntimeError"
    assert "private diagnostic detail" not in repr(result)
    assert engine.pending_target_replicas == 3


def test_invalid_drain_confirmation_is_rejected_before_evaluation():
    engine = make_engine()
    adapter = FakePreviewAdapter()
    coordinator = AutoscalingCoordinator(engine, adapter)

    result = coordinator.run_cycle(
        observation(START),
        evaluated_at=START,
        scale_in_drain_confirmed=1,
    )

    assert result.status is AutoscalingCycleStatus.ERROR
    assert result.reason == "invalid_scale_in_drain_confirmation"
    assert adapter.calls == []
