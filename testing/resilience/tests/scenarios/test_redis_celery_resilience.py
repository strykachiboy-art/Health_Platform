# load_tests/resilience/tests/scenarios/test_redis_celery_resilience.py

from resilience.scenarios.redis_celery_resilience import (
    test_chat_outbox_tasks_are_registered_and_scheduled,
    test_celery_broker_failure_leaves_notification_pending,
    test_celery_broker_recovers_without_duplicate_state_change,
    test_celery_beat_schedule_references_registered_tasks,
    test_redis_auth_fails_closed_during_outage,
    test_redis_auth_recovers_after_outage,
)


__all__ = [
    "test_chat_outbox_tasks_are_registered_and_scheduled",
    "test_redis_auth_fails_closed_during_outage",
    "test_redis_auth_recovers_after_outage",
    "test_celery_broker_failure_leaves_notification_pending",
    "test_celery_broker_recovers_without_duplicate_state_change",
    "test_celery_beat_schedule_references_registered_tasks",
]