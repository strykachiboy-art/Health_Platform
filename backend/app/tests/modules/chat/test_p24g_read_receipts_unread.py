from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    MessageStatus,
    MessageType,
    ParticipantRole,
    ParticipantStatus,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.modules.chat.services.conversation_service import (
    get_conversation_unread_state,
    update_participant_read_state,
)


def _conversation(clinic, user, peer, make_conversation, make_conversation_participant):
    conversation = make_conversation(
        clinic=clinic,
        created_by=user,
        conversation_type=ConversationType.GROUP,
        status=ConversationStatus.ACTIVE,
    )
    owner = make_conversation_participant(
        clinic=clinic, conversation=conversation, user=user,
        role=ParticipantRole.ADMIN, status=ParticipantStatus.ACCEPTED,
    )
    make_conversation_participant(
        clinic=clinic, conversation=conversation, user=peer,
        status=ParticipantStatus.ACCEPTED,
    )
    return conversation, owner


def test_unread_server_count_filters_own_undelivered_deleted_and_system(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message,
):
    peer = make_user(clinic=clinic)
    conversation, owner = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    base = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    sent = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT,
        created_at=base,
    )
    edited = make_message(
        clinic, conversation, peer, status=MessageStatus.EDITED,
        created_at=base + timedelta(seconds=1),
    )
    make_message(clinic, conversation, user, status=MessageStatus.SENT)
    make_message(clinic, conversation, peer, status=MessageStatus.PENDING)
    make_message(clinic, conversation, peer, status=MessageStatus.FAILED)
    make_message(clinic, conversation, peer, status=MessageStatus.DELETED)
    make_message(
        clinic, conversation, peer, message_type=MessageType.SYSTEM,
        status=MessageStatus.SENT,
    )
    first = get_conversation_unread_state(conversation.id, clinic.id, user.id)
    assert first == {
        "conversation_id": conversation.id,
        "clinic_id": clinic.id,
        "user_id": user.id,
        "last_read_message_id": None,
        "unread_count": 2,
    }
    update_participant_read_state(conversation.id, clinic.id, user.id, sent.id)
    middle = get_conversation_unread_state(conversation.id, clinic.id, user.id)
    assert middle["unread_count"] == 1
    assert middle["last_read_message_id"] == sent.id
    update_participant_read_state(conversation.id, clinic.id, user.id, edited.id)
    assert get_conversation_unread_state(
        conversation.id, clinic.id, user.id,
    )["unread_count"] == 0
    assert owner.user_id == user.id


def test_backward_marking_rejected_and_same_mark_idempotent(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    base = datetime(2026, 11, 10, 10, 0, tzinfo=timezone.utc)
    older = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT, created_at=base,
    )
    later = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT,
        created_at=base + timedelta(minutes=2),
    )
    chosen = update_participant_read_state(
        conversation.id, clinic.id, user.id, later.id,
    )
    first_updated = chosen.updated_at
    repeated = update_participant_read_state(
        conversation.id, clinic.id, user.id, later.id,
    )
    assert repeated.last_read_message_id == later.id
    assert repeated.updated_at == first_updated
    with pytest.raises(ConflictError):
        update_participant_read_state(conversation.id, clinic.id, user.id, older.id)
    assert get_conversation_unread_state(
        conversation.id, clinic.id, user.id,
    )["last_read_message_id"] == later.id


def test_comparison_uses_created_at_then_id_not_global_id_only(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    base = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    newest_time_but_first_id = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT,
        created_at=base + timedelta(hours=1),
    )
    old_time_but_second_id = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT, created_at=base,
    )
    assert newest_time_but_first_id.id < old_time_but_second_id.id
    update_participant_read_state(
        conversation.id, clinic.id, user.id, newest_time_but_first_id.id,
    )
    with pytest.raises(ConflictError):
        update_participant_read_state(
            conversation.id, clinic.id, user.id, old_time_but_second_id.id,
        )
    assert get_conversation_unread_state(
        conversation.id, clinic.id, user.id,
    )["unread_count"] == 0


def test_tied_timestamps_compare_id_for_unread_and_backward_ack(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    instant = datetime(2026, 11, 10, tzinfo=timezone.utc)
    first = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT, created_at=instant,
    )
    second = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT, created_at=instant,
    )
    update_participant_read_state(conversation.id, clinic.id, user.id, first.id)
    assert get_conversation_unread_state(
        conversation.id, clinic.id, user.id,
    )["unread_count"] == 1
    update_participant_read_state(conversation.id, clinic.id, user.id, second.id)
    with pytest.raises(ConflictError):
        update_participant_read_state(conversation.id, clinic.id, user.id, first.id)


def test_cannot_read_undelivered_message(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message, db,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    pending = make_message(clinic, conversation, peer, status=MessageStatus.PENDING)
    # Make fixture records durable before the expected transactional rollback.
    db.session.commit()
    with pytest.raises(ConflictError):
        update_participant_read_state(
            conversation.id, clinic.id, user.id, pending.id,
        )
    assert get_conversation_unread_state(
        conversation.id, clinic.id, user.id,
    )["last_read_message_id"] is None


def test_owner_isolation_and_non_member_fail_closed(
    clinic, user, make_user, make_clinic, make_conversation,
    make_conversation_participant, make_message,
):
    peer = make_user(clinic=clinic)
    intruder = make_user(clinic=clinic)
    other_clinic = make_clinic()
    other_owner = make_user(clinic=other_clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    message = make_message(
        clinic, conversation, peer, status=MessageStatus.SENT,
    )
    with pytest.raises(NotFoundError):
        get_conversation_unread_state(conversation.id, clinic.id, intruder.id)
    with pytest.raises(NotFoundError):
        get_conversation_unread_state(
            conversation.id, other_clinic.id, other_owner.id,
        )
    with pytest.raises(NotFoundError):
        update_participant_read_state(
            conversation.id, other_clinic.id, other_owner.id, message.id,
        )


def test_unaccepted_participant_read_and_unread_denied(
    clinic, user, make_user, make_conversation, make_conversation_participant,
    make_message,
):
    peer = make_user(clinic=clinic)
    pending_user = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    make_conversation_participant(
        clinic, conversation, pending_user, status=ParticipantStatus.PENDING,
    )
    msg = make_message(clinic, conversation, peer, status=MessageStatus.SENT)
    with pytest.raises((NotFoundError, ConflictError)):
        get_conversation_unread_state(conversation.id, clinic.id, pending_user.id)
    with pytest.raises((NotFoundError, ConflictError)):
        update_participant_read_state(
            conversation.id, clinic.id, pending_user.id, msg.id,
        )


def test_unread_route_is_authoritative_and_authenticated(
    app, client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    msg = make_message(clinic, conversation, peer, status=MessageStatus.SENT)
    path = f"/api/v1/chat/conversations/{conversation.id}/unread"
    assert client.get(path).status_code == 401
    res = client.get(path, headers=auth_headers_for(user))
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["data"]["unread_count"] == 1
    assert data["data"]["user_id"] == user.id
    marked = client.post(
        f"/api/v1/chat/conversations/{conversation.id}/read",
        json={"last_read_message_id": msg.id},
        headers=auth_headers_for(user),
    )
    assert marked.status_code == 200
    assert client.get(path, headers=auth_headers_for(user)).get_json()["data"]["unread_count"] == 0


def test_backward_mark_route_conflicts_without_rewind(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for,
):
    peer = make_user(clinic=clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    first = make_message(clinic, conversation, peer, status=MessageStatus.SENT)
    second = make_message(clinic, conversation, peer, status=MessageStatus.SENT)
    path = f"/api/v1/chat/conversations/{conversation.id}/read"
    headers = auth_headers_for(user)
    assert client.post(path, json={"last_read_message_id": second.id}, headers=headers).status_code == 200
    backward = client.post(path, json={"last_read_message_id": first.id}, headers=headers)
    assert backward.status_code == 409
    data = client.get(
        f"/api/v1/chat/conversations/{conversation.id}/unread",
        headers=headers,
    ).get_json()["data"]
    assert data["last_read_message_id"] == second.id


def test_unread_route_rejects_foreign_scope_and_unaccepted(
    client, clinic, user, make_user, make_clinic, make_conversation,
    make_conversation_participant, auth_headers_for,
):
    peer = make_user(clinic=clinic)
    outsider = make_user(clinic=clinic)
    foreign_clinic = make_clinic()
    foreign_user = make_user(clinic=foreign_clinic)
    conversation, _ = _conversation(
        clinic, user, peer, make_conversation, make_conversation_participant,
    )
    path = f"/api/v1/chat/conversations/{conversation.id}/unread"
    assert client.get(path, headers=auth_headers_for(outsider)).status_code in (403, 404)
    assert client.get(path, headers=auth_headers_for(foreign_user)).status_code in (403, 404)
