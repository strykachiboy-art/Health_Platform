from __future__ import annotations

from app.core.observability import alert_tasks
from app.extensions import celery


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sets = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value
        return True

    def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)
        return 1

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def srem(self, key, value):
        values = self.sets.get(key, set())
        if value in values:
            values.remove(value)
            return 1
        return 0


def _alert(severity="warning"):
    return {
        "code": "system_cpu_high",
        "severity": severity,
        "component": "system",
        "message": "System CPU utilization is high.",
        "observed_value": 95.0,
        "threshold": 90.0,
    }


def test_alert_task_is_registered_and_scheduled_every_minute(app):
    entry = celery.conf.beat_schedule.get(
        "refresh-operational-alert-state-every-minute"
    )

    assert entry == {
        "task": "refresh_operational_alert_state",
        "schedule": 60.0,
    }
    assert alert_tasks.refresh_operational_alert_state.name == (
        "refresh_operational_alert_state"
    )
    registered_task = celery.tasks.get("refresh_operational_alert_state")
    assert registered_task is not None
    assert registered_task.name == "refresh_operational_alert_state"


def test_alert_task_tracks_new_escalated_and_resolved_lifecycle(
    monkeypatch,
    caplog,
):
    import logging

    redis = FakeRedis()
    monkeypatch.setattr(alert_tasks.extensions, "redis_client", redis)

    current = {
        "timestamp": "2026-10-23T10:00:00+00:00",
        "healthy": False,
        "components": {},
    }
    current_alerts = [_alert()]
    monkeypatch.setattr(
        alert_tasks,
        "collect_operational_snapshot",
        lambda: dict(current),
    )
    monkeypatch.setattr(
        alert_tasks,
        "evaluate_operational_alerts",
        lambda _snapshot: list(current_alerts),
    )

    caplog.set_level(
        logging.INFO,
        logger="app.core.observability.alert_delivery",
    )

    first = alert_tasks.refresh_operational_alert_state.run()
    assert first["success"] is True
    assert first["active_count"] == 1
    assert first["new_count"] == 1
    assert first["delivery_event_count"] == 1
    assert any('"event_type":"new"' in record.getMessage() for record in caplog.records)

    current["timestamp"] = "2026-10-23T10:01:00+00:00"
    current_alerts = [_alert("critical")]
    second = alert_tasks.refresh_operational_alert_state.run()
    assert second["active_count"] == 1
    assert second["escalated_count"] == 1
    assert second["delivery_event_count"] == 1
    assert any('"event_type":"escalated"' in record.getMessage() for record in caplog.records)

    current["timestamp"] = "2026-10-23T10:02:00+00:00"
    current_alerts = []
    third = alert_tasks.refresh_operational_alert_state.run()
    assert third["active_count"] == 0
    assert third["resolved_count"] == 1
    assert third["delivery_event_count"] == 1
    assert any('"event_type":"resolved"' in record.getMessage() for record in caplog.records)

    current["timestamp"] = "2026-10-23T10:03:00+00:00"
    fourth = alert_tasks.refresh_operational_alert_state.run()
    assert fourth["active_count"] == 0
    assert fourth["resolved_count"] == 0
    assert fourth["delivery_event_count"] == 0


def test_alert_task_does_not_swallow_snapshot_failure(monkeypatch):
    def fail_snapshot():
        raise RuntimeError("snapshot unavailable")

    monkeypatch.setattr(
        alert_tasks,
        "collect_operational_snapshot",
        fail_snapshot,
    )

    try:
        alert_tasks.refresh_operational_alert_state.run()
    except RuntimeError as exc:
        assert str(exc) == "snapshot unavailable"
    else:
        raise AssertionError("snapshot failure must propagate to Celery")


def test_alert_task_fails_when_redis_state_store_is_missing(monkeypatch):
    monkeypatch.setattr(alert_tasks.extensions, "redis_client", None)
    monkeypatch.setattr(
        alert_tasks,
        "collect_operational_snapshot",
        lambda: {
            "timestamp": "2026-10-23T10:00:00+00:00",
            "healthy": True,
            "components": {},
        },
    )
    monkeypatch.setattr(
        alert_tasks,
        "evaluate_operational_alerts",
        lambda _snapshot: [],
    )

    try:
        alert_tasks.refresh_operational_alert_state.run()
    except RuntimeError as exc:
        assert "Redis client is required" in str(exc)
    else:
        raise AssertionError("missing Redis state store must fail the task")
