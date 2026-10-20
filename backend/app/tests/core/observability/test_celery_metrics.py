from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.observability import (
    celery_metrics,
)


class FakeTask:
    name = "example.task"

    def __init__(
        self,
        app,
    ):
        self.app = app


def _celery_app_mock():
    app = Mock()
    app.extensions = {}
    app.conf.get.return_value = "celery"
    app.control.inspect.return_value = Mock()

    return app


def _configure_inspector(
    app,
):
    inspector = app.control.inspect.return_value

    inspector.ping.return_value = {
        "worker-1": {
            "ok": "pong",
        }
    }

    inspector.stats.return_value = {
        "worker-1": {
            "pool": {
                "max-concurrency": 4,
            },
            "pid": 1234,
            "total": {
                "example.task": 10,
                "other.task": 5,
            },
            "rusage": {
                "utime": 1.5,
                "stime": 0.5,
                "maxrss": 2048,
            },
        }
    }

    inspector.active.return_value = {
        "worker-1": []
    }

    inspector.reserved.return_value = {
        "worker-1": []
    }

    inspector.scheduled.return_value = {
        "worker-1": []
    }

    inspector.registered.return_value = {
        "worker-1": [
            "example.task",
            "other.task",
        ]
    }


def test_init_celery_metrics_registers_state():
    app = _celery_app_mock()

    celery_metrics.init_celery_metrics(
        app
    )

    assert (
        celery_metrics.CELERY_METRICS_STATE_KEY
        in app.extensions
    )

    assert (
        app.extensions[
            celery_metrics.CELERY_METRICS_STATE_KEY
        ]["registered"]
        is True
    )


def test_collect_celery_metrics_returns_worker_metrics():
    app = _celery_app_mock()
    _configure_inspector(app)

    redis = Mock()

    redis.ping.return_value = True
    redis.llen.return_value = 3
    redis.hgetall.return_value = {}

    redis.scan_iter.return_value = iter([])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    assert result["healthy"] is True
    assert result["broker_healthy"] is True
    assert result["workers_healthy"] is True

    assert result["worker_count"] == 1
    assert result["worker_names"] == [
        "worker-1"
    ]

    assert result["total_concurrency"] == 4
    assert result["active_tasks"] == 0
    assert result["reserved_tasks"] == 0
    assert result["scheduled_tasks"] == 0

    assert (
        result["worker_utilization_percent"]
        == 0.0
    )

    assert result["queue_depths"] == {
        "celery": 3,
    }

    assert result["worker_pids"] == [
        1234
    ]

    assert result["worker_task_totals"] == {
        "example.task": 10,
        "other.task": 5,
    }


def test_collect_celery_metrics_counts_active_tasks():
    app = _celery_app_mock()
    _configure_inspector(app)

    inspector = app.control.inspect.return_value

    inspector.active.return_value = {
        "worker-1": [
            {
                "id": "task-1",
                "name": "example.task",
                "time_start": 1000.0,
            },
            {
                "id": "task-2",
                "name": "other.task",
                "time_start": 1001.0,
            },
        ]
    }

    redis = Mock()
    redis.ping.return_value = True
    redis.llen.return_value = 0
    redis.hgetall.return_value = {}
    redis.scan_iter.return_value = iter([])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    assert result["active_tasks"] == 2
    assert (
        result["worker_utilization_percent"]
        == 50.0
    )


def test_collect_celery_metrics_detects_long_running_tasks(
    monkeypatch,
):
    app = _celery_app_mock()
    _configure_inspector(app)

    inspector = app.control.inspect.return_value

    inspector.active.return_value = {
        "worker-1": [
            {
                "id": "task-1",
                "name": "example.task",
                "time_start": 100.0,
            }
        ]
    }

    monkeypatch.setattr(
        celery_metrics.time,
        "time",
        lambda: 120.0,
    )

    redis = Mock()
    redis.ping.return_value = True
    redis.llen.return_value = 0
    redis.hgetall.return_value = {}
    redis.scan_iter.return_value = iter([])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
        long_running_threshold_seconds=10,
    )

    assert (
        result["long_running_active_count"]
        == 1
    )

    task = result[
        "long_running_active_tasks"
    ][0]

    assert task["task_id"] == "task-1"
    assert task["name"] == "example.task"
    assert task["runtime_seconds"] == 20.0


def test_collect_celery_metrics_returns_task_metrics():
    app = _celery_app_mock()
    _configure_inspector(app)

    redis = Mock()

    redis.ping.return_value = True
    redis.llen.return_value = 0

    task_metrics = {
        (
            "observability:celery:task:v1:"
            "example.task"
        ): {
            "started_tasks": "10",
            "executed_tasks": "8",
            "succeeded_tasks": "7",
            "failed_tasks": "1",
            "retry_events": "2",
            "revoked_tasks": "0",
            "runtime_ms_total": "4000",
            "last_runtime_ms": "500",
            "max_runtime_ms": "900",
            "long_running_tasks": "1",
        },
        (
            "observability:celery:task:v1:"
            "other.task"
        ): {
            "started_tasks": "5",
            "executed_tasks": "5",
            "succeeded_tasks": "5",
            "failed_tasks": "0",
            "retry_events": "0",
            "revoked_tasks": "0",
            "runtime_ms_total": "2500",
            "last_runtime_ms": "600",
            "max_runtime_ms": "700",
            "long_running_tasks": "0",
        },
    }

    def hgetall(key):
        if (
            key
            == celery_metrics.CELERY_AGGREGATE_KEY
        ):
            return {}

        return task_metrics.get(
            str(key),
            {},
        )

    redis.hgetall.side_effect = hgetall

    redis.scan_iter.return_value = iter(
        task_metrics.keys()
    )

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    metrics = result["task_metrics"]

    assert metrics[
        "example.task"
    ]["executed_tasks"] == 8

    assert metrics[
        "example.task"
    ]["average_runtime_ms"] == 500.0

    assert metrics[
        "other.task"
    ]["executed_tasks"] == 5


def test_task_prerun_records_started_task(
    monkeypatch,
):
    app = _celery_app_mock()
    task = FakeTask(app)

    celery_metrics.init_celery_metrics(
        app
    )

    monkeypatch.setattr(
        celery_metrics.time,
        "perf_counter",
        lambda: 10.0,
    )

    redis = Mock()

    monkeypatch.setattr(
        celery_metrics,
        "_get_redis_client",
        lambda: redis,
    )

    celery_metrics._handle_task_prerun(
        task_id="task-1",
        task=task,
    )

    assert (
        celery_metrics._TASK_START_TIMES[
            "task-1"
        ]
        == 10.0
    )

    assert redis.hincrby.call_count >= 2


def test_task_postrun_records_success_runtime(
    monkeypatch,
):
    app = _celery_app_mock()
    task = FakeTask(app)

    celery_metrics.init_celery_metrics(
        app
    )

    celery_metrics._TASK_START_TIMES[
        "task-1"
    ] = 10.0

    monkeypatch.setattr(
        celery_metrics.time,
        "perf_counter",
        lambda: 10.75,
    )

    redis = Mock()
    redis.hget.return_value = "0"

    monkeypatch.setattr(
        celery_metrics,
        "_get_redis_client",
        lambda: redis,
    )

    celery_metrics._handle_task_postrun(
        task_id="task-1",
        task=task,
        state="SUCCESS",
    )

    assert (
        "task-1"
        not in celery_metrics._TASK_START_TIMES
    )

    calls = [
        call.args
        for call in (
            redis.hincrbyfloat.call_args_list
        )
    ]

    assert any(
        call[1] == "runtime_ms_total"
        and call[2] == 750.0
        for call in calls
    )


def test_task_retry_increments_retry_metrics():
    app = _celery_app_mock()
    task = FakeTask(app)

    celery_metrics.init_celery_metrics(
        app
    )

    redis = Mock()

    original_get_client = (
        celery_metrics._get_redis_client
    )

    celery_metrics._get_redis_client = (
        lambda: redis
    )

    try:
        celery_metrics._handle_task_retry(
            sender=task,
            task_id="task-1",
        )
    finally:
        celery_metrics._get_redis_client = (
            original_get_client
        )

    assert redis.hincrby.call_count >= 2


def test_task_revoked_increments_revoked_metrics():
    app = _celery_app_mock()
    task = FakeTask(app)

    celery_metrics.init_celery_metrics(
        app
    )

    redis = Mock()

    original_get_client = (
        celery_metrics._get_redis_client
    )

    celery_metrics._get_redis_client = (
        lambda: redis
    )

    try:
        celery_metrics._handle_task_revoked(
            sender=task,
            task_id="task-1",
        )
    finally:
        celery_metrics._get_redis_client = (
            original_get_client
        )

    assert redis.hincrby.call_count >= 2


def test_collect_celery_metrics_fails_closed_when_queue_depth_read_fails():
    app = _celery_app_mock()
    _configure_inspector(app)

    redis = Mock()
    redis.ping.return_value = True
    redis.llen.side_effect = RuntimeError("queue depth unavailable")
    redis.hgetall.return_value = {}
    redis.scan_iter.return_value = iter([])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    assert result["broker_healthy"] is True
    assert result["queue_depths_healthy"] is False
    assert result["healthy"] is False
    assert "celery" not in result["queue_depths"]
    assert result["queue_depth_errors"] == {
        "celery": "RuntimeError",
    }


def test_collect_celery_metrics_fails_closed_on_invalid_queue_depth():
    app = _celery_app_mock()
    _configure_inspector(app)

    redis = Mock()
    redis.ping.return_value = True
    redis.llen.return_value = "not-an-integer"
    redis.hgetall.return_value = {}
    redis.scan_iter.return_value = iter([])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    assert result["broker_healthy"] is True
    assert result["queue_depths_healthy"] is False
    assert result["healthy"] is False
    assert "celery" not in result["queue_depths"]
    assert result["queue_depth_errors"] == {
        "celery": "ValueError",
    }
def test_collect_celery_metrics_rejects_invalid_timeout():
    app = _celery_app_mock()

    with pytest.raises(ValueError):
        celery_metrics.collect_celery_metrics(
            celery_app=app,
            client=Mock(),
            inspect_timeout=0,
        )


def test_collect_celery_metrics_rejects_invalid_long_running_threshold():
    app = _celery_app_mock()

    with pytest.raises(ValueError):
        celery_metrics.collect_celery_metrics(
            celery_app=app,
            client=Mock(),
            long_running_threshold_seconds=0,
        )


def test_collect_celery_metrics_rejects_invalid_queue_name():
    app = _celery_app_mock()
    _configure_inspector(app)

    redis = Mock()
    redis.ping.return_value = True

    with pytest.raises(ValueError):
        celery_metrics.collect_celery_metrics(
            celery_app=app,
            client=redis,
            queue_names=[""],
        )

def test_before_task_publish_stamps_publication_timestamp(monkeypatch):
    monkeypatch.setattr(celery_metrics.time, "time", lambda: 1234.5)
    headers = {}

    celery_metrics._handle_before_task_publish(
        sender="tests.queue_age_task",
        headers=headers,
    )

    assert headers == {
        celery_metrics.TASK_PUBLISHED_AT_HEADER: 1234.5,
    }


def test_before_task_publish_ignores_timestamp_generation_failure(monkeypatch):
    def fail_timestamp():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(celery_metrics.time, "time", fail_timestamp)
    headers = {}

    celery_metrics._handle_before_task_publish(headers=headers)

    assert headers == {}


def test_get_task_published_at_accepts_numeric_header():
    task = Mock()
    task.request.headers = {
        celery_metrics.TASK_PUBLISHED_AT_HEADER: "123.25",
    }

    assert celery_metrics._get_task_published_at(task) == 123.25


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        False,
        0,
        -1,
        "not-a-timestamp",
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
def test_get_task_published_at_rejects_invalid_header_values(value):
    task = Mock()
    task.request.headers = {
        celery_metrics.TASK_PUBLISHED_AT_HEADER: value,
    }

    assert celery_metrics._get_task_published_at(task) is None


@pytest.mark.parametrize("headers", [None, [], "not-a-mapping"])
def test_get_task_published_at_rejects_non_mapping_headers(headers):
    task = Mock()
    task.request.headers = headers

    assert celery_metrics._get_task_published_at(task) is None


def test_task_prerun_passes_publication_time_to_queue_age_recorder(monkeypatch):
    app = _celery_app_mock()
    celery_metrics.init_celery_metrics(app)

    task = Mock()
    task.app = app
    task.name = "tests.queue_age_prerun"
    task.request.headers = {
        celery_metrics.TASK_PUBLISHED_AT_HEADER: "100.5",
    }

    record_queue_age = Mock()
    monkeypatch.setattr(
        celery_metrics,
        "_record_task_queue_age",
        record_queue_age,
    )
    monkeypatch.setattr(
        celery_metrics,
        "_increment_redis_metric",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        celery_metrics,
        "_set_redis_metric",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        celery_metrics.time,
        "perf_counter",
        lambda: 10.0,
    )

    task_id = "queue-age-prerun-test"
    try:
        celery_metrics._handle_task_prerun(
            task_id=task_id,
            task=task,
        )

        record_queue_age.assert_called_once_with(
            task=task,
            published_at=100.5,
        )
        assert celery_metrics._TASK_START_TIMES[task_id] == 10.0
    finally:
        celery_metrics._TASK_START_TIMES.pop(task_id, None)


def test_record_task_queue_age_updates_aggregate_and_task_metrics(monkeypatch):
    app = _celery_app_mock()
    celery_metrics.init_celery_metrics(app)

    task = Mock()
    task.app = app
    task.name = "tests.queue_age_recording"

    monkeypatch.setattr(celery_metrics.time, "time", lambda: 102.5)

    increments = []
    updates = []

    def capture_increment(key, field, amount=1):
        increments.append((key, field, amount))

    def capture_set(key, field, value):
        updates.append((key, field, value))

    redis = Mock()
    redis.hget.return_value = "0"

    monkeypatch.setattr(
        celery_metrics,
        "_increment_redis_metric",
        capture_increment,
    )
    monkeypatch.setattr(
        celery_metrics,
        "_set_redis_metric",
        capture_set,
    )
    monkeypatch.setattr(
        celery_metrics,
        "_get_redis_client",
        lambda: redis,
    )

    celery_metrics._record_task_queue_age(task=task, published_at=100.0)

    keys = {
        celery_metrics.CELERY_AGGREGATE_KEY,
        celery_metrics._task_metric_key(task.name),
    }

    sample_writes = {
        key for key, field, amount in increments
        if field == "queue_age_samples" and amount == 1
    }
    total_writes = {
        key for key, field, amount in increments
        if field == "queue_age_ms_total" and amount == 2500.0
    }
    last_writes = {
        key for key, field, value in updates
        if field == "last_queue_age_ms" and value == 2500.0
    }
    observed_at_writes = {
        key for key, field, value in updates
        if field == "last_queue_age_observed_at" and value == 102.5
    }
    max_writes = {
        key for key, field, value in updates
        if field == "max_queue_age_ms" and value == 2500.0
    }

    assert sample_writes == keys
    assert total_writes == keys
    assert last_writes == keys
    assert observed_at_writes == keys
    assert max_writes == keys
    assert redis.hget.call_count == 2


@pytest.mark.parametrize(
    "published_at",
    [
        True,
        0.0,
        -1.0,
        float("nan"),
        float("inf"),
        104.0,
    ],
)
def test_record_task_queue_age_skips_invalid_or_future_timestamps(
    monkeypatch,
    published_at,
):
    app = _celery_app_mock()
    celery_metrics.init_celery_metrics(app)

    task = Mock()
    task.app = app
    task.name = "tests.queue_age_invalid"

    increments = []
    updates = []

    monkeypatch.setattr(celery_metrics.time, "time", lambda: 103.5)
    monkeypatch.setattr(
        celery_metrics,
        "_increment_redis_metric",
        lambda *args, **kwargs: increments.append((args, kwargs)),
    )
    monkeypatch.setattr(
        celery_metrics,
        "_set_redis_metric",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        celery_metrics,
        "_get_redis_client",
        lambda: Mock(),
    )

    celery_metrics._record_task_queue_age(
        task=task,
        published_at=published_at,
    )

    assert increments == []
    assert updates == []


def test_record_task_queue_age_does_not_raise_when_redis_fails(monkeypatch):
    app = _celery_app_mock()
    celery_metrics.init_celery_metrics(app)

    task = Mock()
    task.app = app
    task.name = "tests.queue_age_redis_failure"

    redis = Mock()
    redis.hincrby.side_effect = RuntimeError("Redis unavailable")
    redis.hincrbyfloat.side_effect = RuntimeError("Redis unavailable")
    redis.hset.side_effect = RuntimeError("Redis unavailable")
    redis.hget.side_effect = RuntimeError("Redis unavailable")

    monkeypatch.setattr(celery_metrics.time, "time", lambda: 102.0)
    monkeypatch.setattr(
        celery_metrics,
        "_get_redis_client",
        lambda: redis,
    )

    celery_metrics._record_task_queue_age(
        task=task,
        published_at=100.0,
    )


def test_queue_age_metric_fields_compute_average_and_sanitize_values():
    values = {
        "queue_age_samples": "2",
        "queue_age_ms_total": "2500",
        "last_queue_age_ms": "1500",
        "max_queue_age_ms": "2000",
        "last_queue_age_observed_at": "1234.5",
    }

    assert celery_metrics._queue_age_metric_fields(values) == {
        "queue_age_samples": 2,
        "queue_age_ms_total": 2500.0,
        "average_queue_age_ms": 1250.0,
        "last_queue_age_ms": 1500.0,
        "max_queue_age_ms": 2000.0,
        "last_queue_age_observed_at": 1234.5,
    }


def test_queue_age_metric_fields_normalize_invalid_values():
    values = {
        "queue_age_samples": "-2",
        "queue_age_ms_total": "nan",
        "last_queue_age_ms": "-1",
        "max_queue_age_ms": "inf",
        "last_queue_age_observed_at": "nan",
    }

    assert celery_metrics._queue_age_metric_fields(values) == {
        "queue_age_samples": 0,
        "queue_age_ms_total": 0.0,
        "average_queue_age_ms": 0.0,
        "last_queue_age_ms": 0.0,
        "max_queue_age_ms": 0.0,
        "last_queue_age_observed_at": 0.0,
    }


def test_collect_celery_metrics_exposes_queue_age_for_aggregate_and_tasks():
    app = _celery_app_mock()
    _configure_inspector(app)

    aggregate_values = {
        "queue_age_samples": "4",
        "queue_age_ms_total": "9000",
        "last_queue_age_ms": "3000",
        "max_queue_age_ms": "4000",
        "last_queue_age_observed_at": "100.25",
    }

    task_key = (
        "observability:celery:task:v1:"
        "tests.queue_age_collector"
    )
    task_values = {
        "queue_age_samples": "2",
        "queue_age_ms_total": "2500",
        "last_queue_age_ms": "1500",
        "max_queue_age_ms": "2000",
        "last_queue_age_observed_at": "101.5",
    }

    redis = Mock()
    redis.ping.return_value = True
    redis.llen.return_value = 0

    def hgetall(key):
        if key == celery_metrics.CELERY_AGGREGATE_KEY:
            return aggregate_values
        if str(key) == task_key:
            return task_values
        return {}

    redis.hgetall.side_effect = hgetall
    redis.scan_iter.return_value = iter([task_key])

    result = celery_metrics.collect_celery_metrics(
        celery_app=app,
        client=redis,
    )

    expected_aggregate = {
        "queue_age_samples": 4,
        "queue_age_ms_total": 9000.0,
        "average_queue_age_ms": 2250.0,
        "last_queue_age_ms": 3000.0,
        "max_queue_age_ms": 4000.0,
        "last_queue_age_observed_at": 100.25,
    }
    expected_task = {
        "queue_age_samples": 2,
        "queue_age_ms_total": 2500.0,
        "average_queue_age_ms": 1250.0,
        "last_queue_age_ms": 1500.0,
        "max_queue_age_ms": 2000.0,
        "last_queue_age_observed_at": 101.5,
    }

    aggregate = result["aggregate_task_metrics"]
    task_metrics = result["task_metrics"]["tests.queue_age_collector"]

    assert {
        key: aggregate[key] for key in expected_aggregate
    } == expected_aggregate

    assert {
        key: task_metrics[key] for key in expected_task
    } == expected_task
