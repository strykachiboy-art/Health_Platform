from __future__ import annotations
import pytest

from app.core.observability.alerts import (
    evaluate_operational_alerts,
)


def _healthy_snapshot():
    return {
        "healthy": True,
        "components": {
            "readiness": {
                "healthy": True,
            },
            "redis": {
                "healthy": True,
                "memory_capacity_metrics_healthy": True,
                "memory_capacity_metrics_reason": "ok",
                "max_memory_bytes": 1000,
                "max_memory_policy": "noeviction",
                "memory_limit_configured": True,
                "memory_limit_usage_ratio": 0.4,
                "memory_limit_remaining_bytes": 600,
            },
            "celery": {
                "healthy": True,
                "broker_healthy": True,
                "workers_healthy": True,
                "queue_depths": {
                    "celery": 10,
                },
                "worker_utilization_percent": 40.0,
                "long_running_active_count": 0,
            },
            "system": {
                "healthy": True,
                "cpu": {
                    "percent": 30.0,
                },
                "memory": {
                    "percent": 40.0,
                },
                "swap": {
                    "percent": 5.0,
                },
            },
        },
    }


def test_healthy_snapshot_has_no_alerts():
    alerts = evaluate_operational_alerts(
        _healthy_snapshot()
    )

    assert alerts == []


def test_readiness_failure_is_critical():
    snapshot = _healthy_snapshot()

    snapshot["components"]["readiness"][
        "healthy"
    ] = False

    alerts = evaluate_operational_alerts(
        snapshot
    )

    assert alerts == [
        {
            "code": "readiness_failed",
            "severity": "critical",
            "component": "readiness",
            "message": (
                "Application readiness check failed."
            ),
            "observed_value": None,
            "threshold": None,
        }
    ]


def test_redis_and_celery_dependency_failures_are_critical():
    snapshot = _healthy_snapshot()

    snapshot["components"]["redis"][
        "healthy"
    ] = False

    snapshot["components"]["celery"][
        "broker_healthy"
    ] = False

    snapshot["components"]["celery"][
        "workers_healthy"
    ] = False

    alerts = evaluate_operational_alerts(
        snapshot
    )

    codes = {
        alert["code"]
        for alert in alerts
    }

    assert codes == {
        "redis_unhealthy",
        "celery_broker_unhealthy",
        "celery_workers_unavailable",
    }

    assert all(
        alert["severity"] == "critical"
        for alert in alerts
    )


def test_queue_depth_warning_and_critical_thresholds():
    snapshot = _healthy_snapshot()

    snapshot["components"]["celery"][
        "queue_depths"
    ] = {
        "warning-queue": 1000,
        "critical-queue": 5000,
    }

    alerts = evaluate_operational_alerts(
        snapshot
    )

    assert {
        alert["code"]
        for alert in alerts
    } == {
        "celery_queue_depth_high",
        "celery_queue_depth_critical",
    }

    observed = {
        (
            alert["code"],
            alert["observed_value"],
            alert["threshold"],
        )
        for alert in alerts
    }

    assert observed == {
        (
            "celery_queue_depth_high",
            1000,
            1000,
        ),
        (
            "celery_queue_depth_critical",
            5000,
            5000,
        ),
    }


def test_system_pressure_generates_warnings():
    snapshot = _healthy_snapshot()

    snapshot["components"]["system"]["cpu"][
        "percent"
    ] = 95.0

    snapshot["components"]["system"]["memory"][
        "percent"
    ] = 92.0

    snapshot["components"]["system"]["swap"][
        "percent"
    ] = 60.0

    alerts = evaluate_operational_alerts(
        snapshot
    )

    assert {
        alert["code"]
        for alert in alerts
    } == {
        "system_cpu_high",
        "system_memory_high",
        "system_swap_high",
    }

    assert all(
        alert["severity"] == "warning"
        for alert in alerts
    )


def test_celery_pressure_generates_warnings():
    snapshot = _healthy_snapshot()

    snapshot["components"]["celery"][
        "worker_utilization_percent"
    ] = 95.0

    snapshot["components"]["celery"][
        "long_running_active_count"
    ] = 2

    alerts = evaluate_operational_alerts(
        snapshot
    )

    assert {
        alert["code"]
        for alert in alerts
    } == {
        "celery_worker_utilization_high",
        "celery_long_running_tasks",
    }

    assert all(
        alert["severity"] == "warning"
        for alert in alerts
    )


def test_redis_unbounded_memory_capacity_generates_warning():
    snapshot = _healthy_snapshot()
    redis = snapshot["components"]["redis"]
    redis.update(
        {
            "max_memory_bytes": 0,
            "memory_limit_configured": False,
            "memory_limit_usage_ratio": None,
            "memory_limit_remaining_bytes": None,
        }
    )

    alerts = evaluate_operational_alerts(snapshot)

    assert alerts == [
        {
            "code": "redis_memory_limit_unconfigured",
            "severity": "warning",
            "component": "redis",
            "message": (
                "Redis has no configured maxmemory limit; "
                "host and container capacity require review."
            ),
            "observed_value": 0,
            "threshold": None,
            "resource": "memory",
        }
    ]


def test_redis_memory_usage_below_configured_threshold_has_no_alert():
    snapshot = _healthy_snapshot()
    snapshot["components"]["redis"]["memory_limit_usage_ratio"] = 0.79

    alerts = evaluate_operational_alerts(
        snapshot,
        thresholds={
            "redis_memory_warning_percent": 80,
            "redis_memory_critical_percent": 95,
        },
    )

    assert alerts == []


def test_redis_memory_warning_threshold_is_configurable():
    snapshot = _healthy_snapshot()
    snapshot["components"]["redis"]["memory_limit_usage_ratio"] = 0.85

    alerts = evaluate_operational_alerts(
        snapshot,
        thresholds={
            "redis_memory_warning_percent": 80,
            "redis_memory_critical_percent": 95,
        },
    )

    assert alerts == [
        {
            "code": "redis_memory_capacity_high",
            "severity": "warning",
            "component": "redis",
            "message": (
                "Redis memory usage has reached "
                "the configured warning threshold."
            ),
            "observed_value": 85.0,
            "threshold": 80.0,
            "resource": "memory",
        }
    ]


def test_redis_memory_critical_threshold_takes_precedence():
    snapshot = _healthy_snapshot()
    snapshot["components"]["redis"]["memory_limit_usage_ratio"] = 0.97

    alerts = evaluate_operational_alerts(
        snapshot,
        thresholds={
            "redis_memory_warning_percent": 80,
            "redis_memory_critical_percent": 90,
        },
    )

    assert len(alerts) == 1
    assert alerts[0]["code"] == "redis_memory_capacity_critical"
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["observed_value"] == 97.0
    assert alerts[0]["threshold"] == 90.0


def test_redis_capacity_telemetry_unavailable_generates_warning():
    snapshot = _healthy_snapshot()
    redis = snapshot["components"]["redis"]
    redis["memory_capacity_metrics_healthy"] = False
    redis["memory_capacity_metrics_reason"] = (
        "redis_capacity_config_unavailable"
    )

    alerts = evaluate_operational_alerts(snapshot)

    assert len(alerts) == 1
    assert alerts[0]["code"] == "redis_capacity_metrics_unavailable"
    assert alerts[0]["severity"] == "warning"
    assert alerts[0]["component"] == "redis"
    assert alerts[0]["resource"] == "memory"


def test_missing_or_malformed_redis_capacity_fields_fail_closed():
    for mutation in (
        lambda redis: redis.pop("memory_capacity_metrics_healthy"),
        lambda redis: redis.update(
            {"memory_limit_usage_ratio": float("nan")}
        ),
        lambda redis: redis.update(
            {"memory_limit_configured": None}
        ),
    ):
        snapshot = _healthy_snapshot()
        mutation(snapshot["components"]["redis"])

        alerts = evaluate_operational_alerts(snapshot)

        assert len(alerts) == 1
        assert alerts[0]["code"] == "redis_capacity_metrics_unavailable"
        assert alerts[0]["severity"] == "warning"


@pytest.mark.parametrize(
    "thresholds",
    [
        {"redis_memory_warning_percent": True},
        {"redis_memory_warning_percent": 0},
        {"redis_memory_warning_percent": 101},
        {"redis_memory_critical_percent": float("inf")},
        {
            "redis_memory_warning_percent": 90,
            "redis_memory_critical_percent": 80,
        },
    ],
)
def test_invalid_redis_memory_thresholds_are_rejected(thresholds):
    with pytest.raises(ValueError):
        evaluate_operational_alerts(
            _healthy_snapshot(),
            thresholds=thresholds,
        )


def test_threshold_overrides_are_applied():
    snapshot = _healthy_snapshot()

    snapshot["components"]["celery"][
        "queue_depths"
    ] = {
        "celery": 25,
    }

    alerts = evaluate_operational_alerts(
        snapshot,
        thresholds={
            "queue_depth_warning": 20,
            "queue_depth_critical": 100,
        },
    )

    assert len(alerts) == 1

    assert alerts[0]["code"] == (
        "celery_queue_depth_high"
    )

    assert alerts[0]["observed_value"] == 25
    assert alerts[0]["threshold"] == 20


def test_invalid_snapshot_is_rejected():
    try:
        evaluate_operational_alerts(
            None
        )
    except TypeError as exc:
        assert str(exc) == (
            "snapshot must be a dictionary"
        )
    else:
        raise AssertionError(
            "Expected TypeError"
        )


def test_alerts_do_not_include_raw_exception_text():
    snapshot = _healthy_snapshot()

    snapshot["components"]["redis"][
        "healthy"
    ] = False

    snapshot["components"]["redis"][
        "error"
    ] = "password=super-secret-value"

    alerts = evaluate_operational_alerts(
        snapshot
    )

    serialized = str(alerts)

    assert (
        "super-secret-value"
        not in serialized
    )
    assert "password=" not in serialized
