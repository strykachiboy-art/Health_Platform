from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.core.autoscaling.worker_observation import (
    WorkerObservationAdapter,
    WorkerObservationConfig,
)


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _config(**overrides):
    values = {
        "target_outstanding_tasks_per_worker_slot": 2.0,
        "target_queue_age_ms": 2000.0,
        "max_metrics_age_seconds": 20.0,
        "max_queue_age_sample_age_seconds": 20.0,
    }
    values.update(overrides)
    return WorkerObservationConfig(**values)


def _metrics(**overrides):
    values = {
        "timestamp": (NOW - timedelta(seconds=1)).isoformat(),
        "healthy": True,
        "broker_healthy": True,
        "workers_healthy": True,
        "worker_count": 2,
        "worker_names": ["worker-a", "worker-b"],
        "total_concurrency": 4,
        "active_tasks": 1,
        "reserved_tasks": 1,
        "queue_depths": {"celery": 6},
        "queue_depths_healthy": True,
        "queue_depth_errors": {},
        "aggregate_task_metrics": {
            "queue_age_samples": 3,
            "last_queue_age_ms": 3000.0,
            "last_queue_age_observed_at": NOW.timestamp() - 2,
        },
    }
    values.update(overrides)
    return values


def _evaluate(
    metrics=None,
    *,
    config=None,
    current_replicas=2,
    expected_worker_names=None,
    collector_error=None,
    collector_return_marker=None,
    celery_app=None,
):
    calls = []

    def collector(**kwargs):
        calls.append(kwargs)

        if collector_error is not None:
            raise collector_error

        if collector_return_marker is not None:
            return collector_return_marker

        return deepcopy(_metrics() if metrics is None else metrics)

    adapter = WorkerObservationAdapter(
        redis_client=object(),
        celery_app=celery_app,
        config=config or _config(),
        metrics_collector=collector,
    )

    result = adapter.collect(
        current_replicas=current_replicas,
        evaluated_at=NOW,
        expected_worker_names=expected_worker_names,
    )

    return result, calls


def _reason(result):
    return result.metrics["worker_observation"]["reason"]


def test_observation_combines_backlog_with_fresh_queue_age():
    result, _ = _evaluate()

    assert result.observation.healthy is True
    assert result.observation.current_replicas == 2
    assert result.observation.observed_at == NOW

    # (6 queued + 1 active + 1 reserved) / (4 slots * 2 target) = 1.0.
    assert result.metrics["worker_observation"]["backlog_load_ratio"] == 1.0

    # A fresh 3000 ms queue age / 2000 ms target raises pressure to 1.5.
    assert result.metrics["worker_observation"]["queue_age_used"] is True
    assert result.metrics["worker_observation"]["queue_age_load_ratio"] == 1.5
    assert result.observation.load_ratio == pytest.approx(1.5)


def test_observation_uses_backlog_when_queue_age_is_stale():
    stale_aggregate = {
        "queue_age_samples": 3,
        "last_queue_age_ms": 90000.0,
        "last_queue_age_observed_at": NOW.timestamp() - 60,
    }

    result, _ = _evaluate(
        _metrics(aggregate_task_metrics=stale_aggregate),
    )

    assert result.observation.healthy is True
    assert result.observation.load_ratio == pytest.approx(1.0)
    assert result.metrics["worker_observation"]["queue_age_used"] is False
    assert result.metrics["worker_observation"]["queue_age_load_ratio"] is None


def test_queue_age_does_not_create_pressure_without_outstanding_work():
    aggregate = {
        "queue_age_samples": 2,
        "last_queue_age_ms": 90000.0,
        "last_queue_age_observed_at": NOW.timestamp() - 1,
    }

    result, _ = _evaluate(
        _metrics(
            active_tasks=0,
            reserved_tasks=0,
            queue_depths={"celery": 0},
            aggregate_task_metrics=aggregate,
        ),
    )

    assert result.observation.healthy is True
    assert result.observation.load_ratio == 0.0
    assert result.metrics["worker_observation"]["queue_age_used"] is False


def test_missing_queue_age_metrics_do_not_discard_valid_backlog():
    result, _ = _evaluate(_metrics(aggregate_task_metrics={}))

    assert result.observation.healthy is True
    assert result.observation.load_ratio == pytest.approx(1.0)
    assert result.metrics["worker_observation"]["queue_age_used"] is False


def test_stale_celery_metrics_fail_closed():
    result, _ = _evaluate(
        _metrics(timestamp=(NOW - timedelta(seconds=60)).isoformat()),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "stale_celery_telemetry"


def test_future_celery_metrics_fail_closed():
    result, _ = _evaluate(
        _metrics(timestamp=(NOW + timedelta(seconds=1)).isoformat()),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "future_celery_telemetry"


@pytest.mark.parametrize(
    "field",
    [
        "healthy",
        "broker_healthy",
        "workers_healthy",
        "queue_depths_healthy",
    ],
)
def test_unhealthy_required_metric_flags_fail_closed(field):
    result, _ = _evaluate(_metrics(**{field: False}))

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None


def test_queue_depth_errors_fail_closed_even_if_health_flag_disagrees():
    result, _ = _evaluate(
        _metrics(queue_depth_errors={"celery": "RuntimeError"}),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "queue_depth_errors_present"


@pytest.mark.parametrize(
    "changes",
    [
        {
            "worker_count": 1,
            "worker_names": ["worker-a"],
        },
        {
            "worker_count": 2,
            "worker_names": ["worker-a", "worker-a"],
        },
        {
            "worker_count": 2,
            "worker_names": [],
        },
    ],
)
def test_incomplete_or_duplicate_worker_coverage_fails_closed(changes):
    result, _ = _evaluate(_metrics(**changes))

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "worker_coverage_mismatch"


def test_worker_count_must_match_configured_replica_count():
    result, _ = _evaluate(current_replicas=3)

    assert result.observation.healthy is False
    assert _reason(result) == "worker_coverage_mismatch"


def test_expected_worker_names_must_match_the_observed_workers():
    result, _ = _evaluate(
        expected_worker_names=["worker-a", "worker-c"],
    )

    assert result.observation.healthy is False
    assert _reason(result) == "worker_coverage_mismatch"


def test_matching_expected_worker_names_are_accepted():
    result, _ = _evaluate(
        expected_worker_names=["worker-a", "worker-b"],
    )

    assert result.observation.healthy is True
    assert result.observation.load_ratio == pytest.approx(1.5)


def test_invalid_expected_worker_names_fail_closed():
    result, _ = _evaluate(
        expected_worker_names=["worker-a", "worker-a"],
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_expected_worker_names"


@pytest.mark.parametrize(
    "field,value",
    [
        ("active_tasks", True),
        ("active_tasks", -1),
        ("reserved_tasks", True),
        ("reserved_tasks", -1),
    ],
)
def test_invalid_task_counts_fail_closed(field, value):
    result, _ = _evaluate(_metrics(**{field: value}))

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_task_counts"


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        True,
        float("nan"),
        float("inf"),
        "4",
    ],
)
def test_invalid_total_concurrency_fails_closed(value):
    result, _ = _evaluate(_metrics(total_concurrency=value))

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_worker_concurrency"


@pytest.mark.parametrize(
    "value",
    [
        -1,
        True,
        "6",
        float("inf"),
    ],
)
def test_invalid_queue_depth_value_fails_closed(value):
    result, _ = _evaluate(
        _metrics(queue_depths={"celery": value}),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_queue_depths"


def test_missing_configured_queue_fails_closed():
    result, _ = _evaluate(
        _metrics(queue_depths={"celery": 6}),
        config=_config(queue_names=("celery", "priority")),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "incomplete_queue_depth_coverage"


def test_configured_queue_names_are_forwarded_to_collector():
    result, calls = _evaluate(
        _metrics(queue_depths={"celery": 6, "priority": 2}),
        config=_config(queue_names=("celery", "priority")),
    )

    assert result.observation.healthy is True
    assert calls[0]["queue_names"] == ("celery", "priority")
    assert result.metrics["worker_observation"]["queued_tasks"] == 8


def test_collector_receives_client_and_optional_celery_app():
    celery_app = object()
    _, calls = _evaluate(celery_app=celery_app)

    assert calls[0]["celery_app"] is celery_app
    assert "client" in calls[0]


def test_metric_collection_exception_fails_closed():
    result, _ = _evaluate(
        collector_error=RuntimeError("Redis unavailable"),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "metric_collection_failed"
    assert result.metrics["error_type"] == "RuntimeError"


def test_invalid_collector_result_fails_closed():
    result, _ = _evaluate(collector_return_marker="not-a-dict")

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_metric_collector_result"


def test_invalid_current_replica_count_fails_closed():
    result, _ = _evaluate(current_replicas=0)

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None
    assert _reason(result) == "invalid_current_replicas"


def test_naive_evaluation_time_is_rejected():
    adapter = WorkerObservationAdapter(
        redis_client=object(),
        config=_config(),
        metrics_collector=lambda **kwargs: _metrics(),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.collect(
            current_replicas=2,
            evaluated_at=datetime(2026, 1, 1, 12, 0, 0),
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("target_outstanding_tasks_per_worker_slot", 0),
        ("target_outstanding_tasks_per_worker_slot", -1),
        ("target_outstanding_tasks_per_worker_slot", True),
        ("target_outstanding_tasks_per_worker_slot", float("nan")),
        ("target_queue_age_ms", 0),
        ("target_queue_age_ms", float("inf")),
        ("max_metrics_age_seconds", 0),
        ("max_queue_age_sample_age_seconds", -1),
    ],
)
def test_invalid_observation_configuration_is_rejected(field, value):
    with pytest.raises(ValueError):
        _config(**{field: value})


@pytest.mark.parametrize(
    "queue_names",
    [
        (),
        ("celery", ""),
        ("celery", "celery"),
    ],
)
def test_invalid_queue_name_configuration_is_rejected(queue_names):
    with pytest.raises(ValueError):
        _config(queue_names=queue_names)