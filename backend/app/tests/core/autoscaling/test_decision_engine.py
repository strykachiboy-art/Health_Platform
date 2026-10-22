from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
    ScalingAction,
    ScalingObservation,
)


T0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


def make_policy(**overrides):
    values = {
        "min_replicas": 2,
        "max_replicas": 5,
        "scale_out_load_ratio": 0.8,
        "scale_in_load_ratio": 0.4,
        "scale_out_window_seconds": 10,
        "scale_in_window_seconds": 10,
        "cooldown_seconds": 20,
        "max_metrics_age_seconds": 10,
        "max_observation_gap_seconds": 10,
    }
    values.update(overrides)
    return AutoscalingPolicy(**values)


def sample(
    engine,
    offset,
    ratio,
    *,
    replicas=2,
    healthy=True,
    observed_offset=None,
    evaluated_offset=None,
):
    observed = T0 + timedelta(
        seconds=offset if observed_offset is None else observed_offset
    )
    evaluated = T0 + timedelta(
        seconds=offset if evaluated_offset is None else evaluated_offset
    )
    return engine.evaluate(
        ScalingObservation(replicas, ratio, observed, healthy),
        evaluated_at=evaluated,
    )


def test_scale_out_requires_sustained_demand_and_adds_one_replica():
    engine = AutoscalingDecisionEngine(make_policy())

    assert sample(engine, 0, 0.9).reason == "scale_out_window_not_met"
    assert sample(engine, 5, 0.9).reason == "scale_out_window_not_met"

    decision = sample(engine, 10, 0.9)
    assert decision.action is ScalingAction.SCALE_OUT
    assert decision.current_replicas == 2
    assert decision.target_replicas == 3
    assert decision.reason == "scale_out_recommended"


def test_pending_recommendation_blocks_duplicates_until_acknowledged():
    engine = AutoscalingDecisionEngine(make_policy())
    sample(engine, 0, 0.9)
    assert sample(engine, 10, 0.9).action is ScalingAction.SCALE_OUT

    pending = sample(engine, 15, 0.95)
    assert pending.action is ScalingAction.HOLD
    assert pending.reason == "scale_action_pending"
    assert engine.pending_target_replicas == 3

    engine.record_scale_result(
        succeeded=True, at=T0 + timedelta(seconds=10)
    )
    after_ack = sample(engine, 15, 0.95, replicas=3)
    assert after_ack.action is ScalingAction.HOLD
    assert after_ack.reason == "scale_out_window_not_met"


def test_scale_in_requires_sustained_low_demand_and_removes_one_replica():
    engine = AutoscalingDecisionEngine(make_policy())

    assert sample(engine, 0, 0.3, replicas=3).reason == "scale_in_window_not_met"
    assert sample(engine, 5, 0.3, replicas=3).reason == "scale_in_window_not_met"

    decision = sample(engine, 10, 0.3, replicas=3)
    assert decision.action is ScalingAction.SCALE_IN
    assert decision.current_replicas == 3
    assert decision.target_replicas == 2


def test_hysteresis_band_resets_the_sustained_demand_window():
    engine = AutoscalingDecisionEngine(make_policy())
    sample(engine, 0, 0.9)
    sample(engine, 5, 0.9)

    middle = sample(engine, 6, 0.6)
    assert middle.action is ScalingAction.HOLD
    assert middle.reason == "within_hysteresis_band"
    assert sample(engine, 10, 0.9).reason == "scale_out_window_not_met"


def test_sparse_observations_reset_the_demand_window():
    engine = AutoscalingDecisionEngine(
        make_policy(max_observation_gap_seconds=5)
    )
    sample(engine, 0, 0.9)

    decision = sample(engine, 10, 0.9)
    assert decision.action is ScalingAction.HOLD
    assert decision.reason == "scale_out_window_not_met"


def test_duplicate_observation_does_not_advance_the_demand_window():
    engine = AutoscalingDecisionEngine(make_policy())
    assert sample(engine, 0, 0.9).reason == "scale_out_window_not_met"

    duplicate = sample(engine, 0, 0.9, evaluated_offset=10)
    assert duplicate.action is ScalingAction.HOLD
    assert duplicate.reason == "duplicate_observation"

    assert sample(engine, 10, 0.9).action is ScalingAction.SCALE_OUT


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"ratio": None}, "missing_load_ratio"),
        ({"ratio": float("nan")}, "invalid_load_ratio"),
        ({"ratio": float("inf")}, "invalid_load_ratio"),
        ({"ratio": -0.1}, "invalid_load_ratio"),
        ({"ratio": 0.9, "healthy": False}, "metrics_unhealthy"),
        ({"ratio": 0.9, "observed_offset": -11}, "stale_metrics"),
        (
            {"ratio": 0.9, "observed_offset": 1, "evaluated_offset": 0},
            "future_observation",
        ),
    ],
)
def test_unusable_metrics_fail_safe_to_hold(kwargs, expected_reason):
    engine = AutoscalingDecisionEngine(make_policy())
    ratio = kwargs.pop("ratio")
    decision = sample(engine, 0, ratio, **kwargs)

    assert decision.action is ScalingAction.HOLD
    assert decision.target_replicas == decision.current_replicas
    assert decision.reason == expected_reason
    assert engine.pending_target_replicas is None


def test_invalid_observation_breaks_a_high_demand_streak():
    engine = AutoscalingDecisionEngine(make_policy())
    sample(engine, 0, 0.9)
    sample(engine, 5, 0.9)

    assert sample(engine, 6, None).reason == "missing_load_ratio"
    assert sample(engine, 10, 0.9).reason == "scale_out_window_not_met"


def test_minimum_and_maximum_replica_bounds_are_enforced():
    minimum_engine = AutoscalingDecisionEngine(make_policy())
    sample(minimum_engine, 0, 0.3, replicas=2)
    at_minimum = sample(minimum_engine, 10, 0.3, replicas=2)
    assert at_minimum.action is ScalingAction.HOLD
    assert at_minimum.reason == "minimum_replicas_reached"
    assert at_minimum.target_replicas == 2

    maximum_engine = AutoscalingDecisionEngine(
        make_policy(max_replicas=3)
    )
    sample(maximum_engine, 0, 0.9, replicas=3)
    at_maximum = sample(maximum_engine, 10, 0.9, replicas=3)
    assert at_maximum.action is ScalingAction.HOLD
    assert at_maximum.reason == "maximum_replicas_reached"
    assert at_maximum.target_replicas == 3


def test_success_starts_cooldown_but_failed_action_does_not():
    engine = AutoscalingDecisionEngine(make_policy())
    sample(engine, 0, 0.9)
    recommendation = sample(engine, 10, 0.9)
    assert recommendation.action is ScalingAction.SCALE_OUT

    engine.record_scale_result(
        succeeded=True, at=T0 + timedelta(seconds=10)
    )

    sample(engine, 15, 0.9, replicas=3)
    cooldown = sample(engine, 25, 0.9, replicas=3)
    assert cooldown.action is ScalingAction.HOLD
    assert cooldown.reason == "cooldown_active"

    expired = sample(engine, 30, 0.9, replicas=3)
    assert expired.action is ScalingAction.SCALE_OUT

    failed_engine = AutoscalingDecisionEngine(make_policy())
    sample(failed_engine, 0, 0.9)
    failed = sample(failed_engine, 10, 0.9)
    assert failed.action is ScalingAction.SCALE_OUT

    failed_engine.record_scale_result(
        succeeded=False, at=T0 + timedelta(seconds=10)
    )
    sample(failed_engine, 15, 0.9)
    retried = sample(failed_engine, 25, 0.9)
    assert retried.action is ScalingAction.SCALE_OUT


def test_result_cannot_be_acknowledged_without_pending_action():
    engine = AutoscalingDecisionEngine(make_policy())

    with pytest.raises(RuntimeError):
        engine.record_scale_result(succeeded=True, at=T0)

    sample(engine, 0, 0.9)
    sample(engine, 10, 0.9)

    with pytest.raises(ValueError):
        engine.record_scale_result(
            succeeded=True, at=T0 + timedelta(seconds=9)
        )


def test_restored_successful_scale_timestamp_preserves_cooldown_after_restart():
    engine = AutoscalingDecisionEngine(make_policy(cooldown_seconds=20))
    last_scale = T0 + timedelta(seconds=10)
    engine.restore_last_successful_scale(last_scale)

    # Cooldown is evaluated only after the sustained-demand window is met.
    first = sample(engine, 11, 0.95, replicas=2)
    assert first.action is ScalingAction.HOLD
    assert first.reason == "scale_out_window_not_met"

    result = sample(engine, 21, 0.95, replicas=2)

    assert result.action is ScalingAction.HOLD
    assert result.reason == "cooldown_active"
    assert engine.pending_target_replicas is None


def test_restored_successful_scale_timestamp_requires_aware_datetime():
    engine = AutoscalingDecisionEngine(make_policy())
    with pytest.raises(ValueError, match="timezone-aware"):
        engine.restore_last_successful_scale(datetime(2026, 10, 20, 10, 0))


def test_invalid_or_out_of_bounds_replica_count_holds():
    engine = AutoscalingDecisionEngine(make_policy())

    invalid = engine.evaluate(
        ScalingObservation(True, 0.9, T0),
        evaluated_at=T0,
    )
    assert invalid.action is ScalingAction.HOLD
    assert invalid.reason == "invalid_replica_count"

    out_of_bounds = engine.evaluate(
        ScalingObservation(6, 0.9, T0),
        evaluated_at=T0,
    )
    assert out_of_bounds.action is ScalingAction.HOLD
    assert out_of_bounds.reason == "replicas_out_of_bounds"


@pytest.mark.parametrize(
    "overrides",
    [
        {"min_replicas": 0},
        {"min_replicas": 3, "max_replicas": 2},
        {"scale_in_load_ratio": 0.8, "scale_out_load_ratio": 0.8},
        {"scale_in_load_ratio": -0.1},
        {"scale_out_load_ratio": float("nan")},
        {"scale_out_window_seconds": 0},
        {"scale_in_window_seconds": float("inf")},
        {"cooldown_seconds": -1},
        {"max_metrics_age_seconds": 0},
        {"max_observation_gap_seconds": -1},
    ],
)
def test_invalid_policy_is_rejected(overrides):
    with pytest.raises(ValueError):
        make_policy(**overrides)


def test_naive_evaluation_timestamp_is_rejected():
    engine = AutoscalingDecisionEngine(make_policy())
    naive = datetime(2026, 10, 20, 10, 0)

    with pytest.raises(ValueError):
        engine.evaluate(
            ScalingObservation(2, 0.9, T0),
            evaluated_at=naive,
        )
