from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.extensions import db
from app.core.exceptions import ValidationError
from app.modules.chat.models.chat_outbox_model import ChatOutbox
from app.modules.chat.workers.chat_outbox_worker import (
    create_outbox_event,
)
from app.modules.chat.tasks import chat_outbox_task
from app.modules.chat.tasks.chat_outbox_task import (
    process_chat_outbox,
    recover_stale_chat_outbox,
)
from app.modules.chat.realtime.chat_socket import (
    user_room,
)


def _get_event(
    event_id: int,
) -> ChatOutbox:
    event = db.session.get(
        ChatOutbox,
        event_id,
    )

    assert event is not None

    return event


def _create_event(
    clinic,
    *,
    event_type: str = "message.created",
    conversation_id: int = 1,
    message_id: int | None = None,
    payload: dict | None = None,
):
    return create_outbox_event(
        clinic_id=clinic.id,
        event_type=event_type,
        payload=(
            payload
            if payload is not None
            else {
                "conversation_id": conversation_id,
                "message_id": message_id or 1,
                "sender_id": 1,
                "message_type": "text",
                "priority": "normal",
                "reply_to_message_id": None,
            }
        ),
        message_id=message_id,
    )


@pytest.fixture(autouse=True)
def authorized_outbox_recipient(
    monkeypatch,
    user,
):
    monkeypatch.setattr(
        chat_outbox_task.ChatSecurityService,
        "get_active_conversation_recipient_user_ids",
        lambda clinic_id, conversation_id: [
            user.id,
        ],
    )


@pytest.mark.parametrize(
    (
        "event_type",
        "expected_socket_event",
    ),
    [
        (
            "message.created",
            "message.created",
        ),
        (
            "message.updated",
            "message.updated",
        ),
        (
            "message.deleted",
            "message.deleted",
        ),
    ],
)
def test_process_chat_outbox_emits_and_processes_event(
    clinic,
    monkeypatch,
    user,
    event_type,
    expected_socket_event,
):
    emitted = []

    def fake_emit(
        event_name,
        payload,
        *,
        room,
        namespace,
    ):
        emitted.append(
            {
                "event_name": event_name,
                "payload": payload,
                "room": room,
                "namespace": namespace,
            }
        )

    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        fake_emit,
    )

    event = _create_event(
        clinic,
        event_type=event_type,
        conversation_id=123,
    )

    result = process_chat_outbox.run(
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "claimed": 1,
        "processed": 1,
        "failed": 0,
    }

    assert stored.status == "processed"
    assert stored.attempts == 1
    assert stored.processed_at is not None
    assert stored.last_error is None

    assert len(emitted) == 1

    emission = emitted[0]

    assert emission["event_name"] == expected_socket_event
    assert emission["namespace"] == "/chat"
    # Revoked sessions have a different room generation: exact versioned
    # room is now part of the security contract.
    assert emission["room"] == user_room(
        clinic.id,
        user.id,
        user.token_version,
    )

    assert emission["payload"] == {
        "event_id": event.id,
        "clinic_id": clinic.id,
        "event_type": event_type,
        **event.payload,
    }


def test_process_chat_outbox_marks_failed_event_for_retry(
    clinic,
    monkeypatch,
):
    def fail_emit(
        *args,
        **kwargs,
    ):
        raise RuntimeError(
            "Socket.IO unavailable"
        )

    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        fail_emit,
    )

    event = _create_event(
        clinic,
        conversation_id=321,
    )

    before = datetime.now(
        timezone.utc,
    )

    result = process_chat_outbox.run(
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "claimed": 1,
        "processed": 0,
        "failed": 1,
    }

    assert stored.status == "pending"
    assert stored.attempts == 1
    assert stored.processed_at is None
    assert stored.last_error == (
        "Socket.IO unavailable"
    )

    assert stored.available_at is not None

    if stored.available_at.tzinfo is None:
        available_at = stored.available_at.replace(
            tzinfo=timezone.utc,
        )
    else:
        available_at = stored.available_at

    assert available_at >= before


def test_process_chat_outbox_retries_failed_event(
    clinic,
    monkeypatch,
):
    calls = {
        "count": 0,
    }

    def fail_once_then_succeed(
        *args,
        **kwargs,
    ):
        calls["count"] += 1

        if calls["count"] == 1:
            raise RuntimeError(
                "Temporary Socket.IO failure"
            )

    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        fail_once_then_succeed,
    )

    event = _create_event(
        clinic,
        conversation_id=456,
    )

    first_result = process_chat_outbox.run(
        limit=50,
    )

    first_stored = _get_event(
        event.id,
    )

    assert first_result == {
        "claimed": 1,
        "processed": 0,
        "failed": 1,
    }

    assert first_stored.status == "pending"
    assert first_stored.attempts == 1

    first_stored.available_at = (
        datetime.now(timezone.utc)
        - timedelta(seconds=1)
    )

    db.session.flush()

    second_result = process_chat_outbox.run(
        limit=50,
    )

    second_stored = _get_event(
        event.id,
    )

    assert second_result == {
        "claimed": 1,
        "processed": 1,
        "failed": 0,
    }

    assert second_stored.status == "processed"
    assert second_stored.attempts == 2
    assert second_stored.processed_at is not None
    assert second_stored.last_error is None

    assert calls["count"] == 2


def test_process_chat_outbox_rejects_unsupported_event_type(
    clinic,
    monkeypatch,
):
    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        lambda *args, **kwargs: None,
    )

    event = _create_event(
        clinic,
        event_type="unsupported.event",
        conversation_id=789,
    )

    result = process_chat_outbox.run(
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "claimed": 1,
        "processed": 0,
        "failed": 1,
    }

    assert stored.status == "pending"
    assert stored.attempts == 1
    assert stored.processed_at is None
    assert stored.last_error == (
        "Unsupported chat outbox event type: "
        "unsupported.event"
    )


def test_process_chat_outbox_rejects_missing_conversation_id(
    clinic,
    monkeypatch,
):
    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        lambda *args, **kwargs: None,
    )

    event = _create_event(
        clinic,
        payload={
            "message_id": 999,
            "sender_id": 1,
        },
    )

    result = process_chat_outbox.run(
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "claimed": 1,
        "processed": 0,
        "failed": 1,
    }

    assert stored.status == "pending"
    assert stored.attempts == 1
    assert stored.processed_at is None
    assert stored.last_error == (
        "Chat outbox event requires a valid "
        "conversation_id"
    )


def test_process_chat_outbox_respects_claim_limit(
    clinic,
    monkeypatch,
):
    emitted = []

    monkeypatch.setattr(
        chat_outbox_task.socketio,
        "emit",
        lambda *args, **kwargs: emitted.append(
            {
                "event_name": args[0],
                "payload": args[1],
            }
        ),
    )

    events = [
        _create_event(
            clinic,
            conversation_id=index,
        )
        for index in range(
            1,
            4,
        )
    ]

    result = process_chat_outbox.run(
        limit=2,
    )

    assert result == {
        "claimed": 2,
        "processed": 2,
        "failed": 0,
    }

    assert len(emitted) == 2

    first = _get_event(
        events[0].id,
    )
    second = _get_event(
        events[1].id,
    )
    third = _get_event(
        events[2].id,
    )

    assert first.status == "processed"
    assert second.status == "processed"
    assert third.status == "pending"

    assert first.attempts == 1
    assert second.attempts == 1
    assert third.attempts == 0


def test_recover_stale_chat_outbox_requeues_stale_event(
    clinic,
):
    event = _create_event(
        clinic,
        conversation_id=111,
    )

    event.status = "processing"
    event.attempts = 1
    event.updated_at = (
        datetime.now(timezone.utc)
        - timedelta(minutes=10)
    )
    event.available_at = (
        datetime.now(timezone.utc)
        - timedelta(minutes=10)
    )

    db.session.flush()

    result = recover_stale_chat_outbox.run(
        stale_after_seconds=300,
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "requeued": 1,
    }

    assert stored.status == "pending"
    assert stored.available_at is not None
    assert stored.last_error == (
        "Processing lease expired"
    )


def test_recover_stale_chat_outbox_does_not_requeue_recent_event(
    clinic,
):
    event = _create_event(
        clinic,
        conversation_id=222,
    )

    event.status = "processing"
    event.attempts = 1
    event.updated_at = datetime.now(
        timezone.utc,
    )
    event.available_at = datetime.now(
        timezone.utc,
    )

    db.session.flush()

    result = recover_stale_chat_outbox.run(
        stale_after_seconds=300,
        limit=50,
    )

    stored = _get_event(
        event.id,
    )

    assert result == {
        "requeued": 0,
    }

    assert stored.status == "processing"
    assert stored.attempts == 1
    assert stored.last_error is None


def test_process_chat_outbox_validates_claim_limit(
    clinic,
):
    _create_event(
        clinic,
        conversation_id=333,
    )

    with pytest.raises(
        ValidationError,
    ):
        process_chat_outbox.run(
            limit=0,
        )


def test_process_chat_outbox_empty_queue(
    clinic,
):
    result = process_chat_outbox.run(
        limit=50,
    )

    assert result == {
        "claimed": 0,
        "processed": 0,
        "failed": 0,
    }