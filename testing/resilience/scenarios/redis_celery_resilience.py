from __future__ import annotations

import pytest
from celery.exceptions import OperationalError
from redis.exceptions import ConnectionError

from app import extensions
from app.core.enums.notification_enums import (
    NotificationChannel,
    NotificationStatus,
)
from app.core.notifications.services import (
    notification_service,
)
from app.extensions import celery, db
from app.modules.chat.tasks.chat_outbox_task import (
    process_chat_outbox,
    recover_stale_chat_outbox,
)


PROFILE_ENDPOINT = "/api/v1/profile/me"


def test_redis_auth_fails_closed_during_outage(
    client,
    user,
    auth_headers_for,
    monkeypatch,
):
    redis_client = extensions.redis_client

    def fail_exists(_key):
        raise ConnectionError(
            "Synthetic Redis outage"
        )

    monkeypatch.setattr(
        redis_client,
        "exists",
        fail_exists,
    )

    response = client.get(
        PROFILE_ENDPOINT,
        headers=auth_headers_for(user),
    )

    assert response.status_code == 401


def test_redis_auth_recovers_after_outage(
    client,
    user,
    auth_headers_for,
    monkeypatch,
):
    redis_client = extensions.redis_client

    calls = {
        "count": 0,
    }

    def fail_once_then_recover(_key):
        calls["count"] += 1

        if calls["count"] == 1:
            raise ConnectionError(
                "Synthetic transient Redis outage"
            )

        return 0

    monkeypatch.setattr(
        redis_client,
        "exists",
        fail_once_then_recover,
    )

    first_response = client.get(
        PROFILE_ENDPOINT,
        headers=auth_headers_for(user),
    )

    assert first_response.status_code == 401

    second_response = client.get(
        PROFILE_ENDPOINT,
        headers=auth_headers_for(user),
    )

    assert second_response.status_code == 200
    assert calls["count"] == 2


def test_celery_broker_failure_leaves_notification_pending(
    clinic,
    user,
    make_notification,
    monkeypatch,
):
    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.PENDING,
    )

    def fail_publish(_clinic_id, notification_id):
        raise OperationalError(
            "Synthetic Redis broker outage"
        )

    monkeypatch.setattr(
        notification_service.deliver_notification,
        "delay",
        fail_publish,
    )

    with pytest.raises(
        OperationalError,
        match="Synthetic Redis broker outage",
    ):
        notification_service.queue_notification_delivery(
            notification.id,
            clinic_id=clinic.id,
            user_id=user.id,
        )

    db.session.refresh(notification)

    assert notification.status == (
        NotificationStatus.PENDING
    )
    assert notification.retry_count == 0
    assert notification.sent_at is None
    assert notification.delivered_at is None
    assert notification.failed_at is None


def test_celery_broker_recovers_without_duplicate_state_change(
    clinic,
    user,
    make_notification,
    monkeypatch,
):
    notification = make_notification(
        clinic_id=clinic.id,
        user_id=user.id,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.PENDING,
    )

    attempts = {
        "count": 0,
    }

    def publish(_clinic_id, notification_id):
        attempts["count"] += 1

        if attempts["count"] == 1:
            raise OperationalError(
                "Synthetic transient broker failure"
            )

        assert notification_id == notification.id

        return object()

    monkeypatch.setattr(
        notification_service.deliver_notification,
        "delay",
        publish,
    )

    with pytest.raises(
        OperationalError,
        match="Synthetic transient broker failure",
    ):
        notification_service.queue_notification_delivery(
            notification.id,
            clinic_id=clinic.id,
            user_id=user.id,
        )

    result = (
        notification_service.queue_notification_delivery(
            notification.id,
            clinic_id=clinic.id,
            user_id=user.id,
        )
    )

    assert result is notification
    assert attempts["count"] == 2

    db.session.refresh(notification)

    assert notification.status == (
        NotificationStatus.PENDING
    )
    assert notification.retry_count == 0
    assert notification.sent_at is None
    assert notification.delivered_at is None


def test_celery_beat_schedule_references_registered_tasks(
    app,
):
    expected_tasks = {
        "check_upcoming_appointments",
        "mark_overdue_invoices",
        "reset_monthly_ai_usage",
    }

    scheduled_tasks = {
        entry["task"]
        for entry in celery.conf.beat_schedule.values()
    }

    assert expected_tasks.issubset(
        scheduled_tasks
    )

    registered_tasks = set(
        celery.tasks.keys()
    )

    assert expected_tasks.issubset(
        registered_tasks
    )

def test_chat_outbox_tasks_are_registered_and_scheduled(
    app,
):
    expected_schedule = {
        "process-chat-outbox-every-5-seconds": (
            process_chat_outbox.name,
            5.0,
        ),
        "recover-stale-chat-outbox-every-minute": (
            recover_stale_chat_outbox.name,
            60.0,
        ),
    }

    beat_schedule = celery.conf.beat_schedule
    registered_tasks = set(celery.tasks.keys())
    scheduled_tasks = {
        entry["task"]
        for entry in beat_schedule.values()
    }

    for entry_name, expected in expected_schedule.items():
        expected_task_name, expected_interval = expected

        assert entry_name in beat_schedule
        entry = beat_schedule[entry_name]

        assert entry["task"] == expected_task_name
        assert entry["schedule"] == expected_interval
        assert entry["task"] in scheduled_tasks
        assert entry["task"] in registered_tasks
