from __future__ import annotations

import pytest
from sqlalchemy import event

from app.extensions import db
from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    ParticipantRole,
    ParticipantStatus,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.services.conversation_service import (
    add_participant,
    create_conversation,
    get_conversation,
    leave_conversation,
    list_conversations,
    list_participants,
    update_conversation,
    update_conversation_status,
    update_participant,
    update_participant_read_state,
)


def test_create_direct_conversation(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    assert conversation.id is not None
    assert conversation.clinic_id == clinic.id
    assert conversation.conversation_type == ConversationType.DIRECT
    assert conversation.status == ConversationStatus.ACTIVE
    assert conversation.created_by_id == user.id
    assert conversation.direct_key is not None
    assert len(conversation.direct_key) == 64


def test_create_direct_conversation_creates_two_participants(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    participants = db.session.execute(
        db.select(ConversationParticipant)
        .where(
            ConversationParticipant.conversation_id
            == conversation.id,
        )
        .order_by(
            ConversationParticipant.id.asc(),
        )
    ).scalars().all()

    assert len(participants) == 2

    creator = next(
        participant
        for participant in participants
        if participant.user_id == user.id
    )

    other = next(
        participant
        for participant in participants
        if participant.user_id == other_user.id
    )

    assert creator.role == ParticipantRole.ADMIN
    assert creator.status == ParticipantStatus.ACCEPTED
    assert creator.joined_at is not None

    assert other.role == ParticipantRole.MEMBER
    assert other.status == ParticipantStatus.PENDING
    assert other.joined_at is None


def test_create_direct_conversation_requires_exactly_two_users(
    clinic,
    user,
    no_audit,
):
    with pytest.raises(ValidationError):
        create_conversation(
            clinic_id=clinic.id,
            created_by_id=user.id,
            conversation_type=ConversationType.DIRECT,
            participant_user_ids=[],
        )


def test_create_direct_conversation_rejects_duplicate_users(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    with pytest.raises(ValidationError):
        create_conversation(
            clinic_id=clinic.id,
            created_by_id=user.id,
            conversation_type=ConversationType.DIRECT,
            participant_user_ids=[
                other_user.id,
                other_user.id,
            ],
        )


def test_create_direct_conversation_deduplicates_existing_chat(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    first = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    second = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    assert second.id == first.id
    assert second.direct_key == first.direct_key


def test_create_direct_conversation_is_order_independent(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    first = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    second = create_conversation(
        clinic_id=clinic.id,
        created_by_id=other_user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[user.id],
    )

    assert second.id == first.id
    assert second.direct_key == first.direct_key


def test_create_group_conversation(
    clinic,
    user,
    make_user,
    no_audit,
):
    user_two = make_user(clinic=clinic)
    user_three = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[
            user_two.id,
            user_three.id,
        ],
        title="Clinical Team",
        description="Clinical coordination",
    )

    assert conversation.conversation_type == ConversationType.GROUP
    assert conversation.status == ConversationStatus.ACTIVE
    assert conversation.title == "Clinical Team"
    assert conversation.description == "Clinical coordination"
    assert conversation.direct_key is None


def test_create_group_conversation_creates_all_participants(
    clinic,
    user,
    make_user,
    no_audit,
):
    user_two = make_user(clinic=clinic)
    user_three = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[
            user_two.id,
            user_three.id,
        ],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
    )

    assert result["total"] == 3
    assert len(result["items"]) == 3


def test_create_conversation_rejects_invalid_clinic(
    user,
    no_audit,
):
    with pytest.raises(NotFoundError):
        create_conversation(
            clinic_id=999999,
            created_by_id=user.id,
            conversation_type=ConversationType.GROUP,
            participant_user_ids=[],
        )


def test_create_conversation_rejects_invalid_creator(
    clinic,
    no_audit,
):
    with pytest.raises(NotFoundError):
        create_conversation(
            clinic_id=clinic.id,
            created_by_id=999999,
            conversation_type=ConversationType.GROUP,
            participant_user_ids=[],
        )


def test_create_conversation_rejects_invalid_participant(
    clinic,
    user,
    no_audit,
):
    with pytest.raises(NotFoundError):
        create_conversation(
            clinic_id=clinic.id,
            created_by_id=user.id,
            conversation_type=ConversationType.GROUP,
            participant_user_ids=[999999],
        )


def test_create_conversation_normalizes_text(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        title="  Clinical Team  ",
        description="  Patient coordination  ",
        participant_user_ids=[other_user.id],
    )

    assert conversation.title == "Clinical Team"
    assert conversation.description == "Patient coordination"


def test_create_conversation_rejects_blank_title(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        title="   ",
        participant_user_ids=[other_user.id],
    )

    assert conversation.title is None


def test_get_conversation(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    result = get_conversation(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        user_id=user.id,
    )

    assert result.id == conversation.id


def test_get_conversation_rejects_wrong_clinic(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(NotFoundError):
        get_conversation(
            conversation_id=conversation.id,
            clinic_id=999999,
            user_id=user.id,
        )


def test_get_conversation_rejects_non_participant(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    outsider = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(NotFoundError):
        get_conversation(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            user_id=outsider.id,
        )


def test_list_conversations_filters_by_user(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    outsider = make_user(clinic=clinic)

    first = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    create_conversation(
        clinic_id=clinic.id,
        created_by_id=outsider.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    result = list_conversations(
        clinic_id=clinic.id,
        user_id=user.id,
    )

    assert result["total"] == 1
    assert result["items"][0].id == first.id
    assert result["page"] == 1
    assert result["per_page"] == 50
    assert result["pages"] == 1
    assert result["has_next"] is False
    assert result["has_previous"] is False


def test_list_conversations_reuses_identity_mapped_user(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(
        clinic=clinic
    )

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[
            other_user.id,
        ],
    )

    db.session.get(
        type(user),
        user.id,
    )

    observed_selects = []

    def capture_selects(
        connection,
        cursor,
        statement,
        parameters,
        context,
        executemany,
    ):
        normalized = " ".join(
            statement.lower().split()
        )

        if normalized.startswith("select"):
            observed_selects.append(
                normalized
            )

    event.listen(
        db.engine,
        "before_cursor_execute",
        capture_selects,
    )

    try:
        result = list_conversations(
            clinic_id=clinic.id,
            user_id=user.id,
        )
    finally:
        event.remove(
            db.engine,
            "before_cursor_execute",
            capture_selects,
        )

    assert result["total"] == 1
    assert result["items"][0].id == conversation.id

    user_selects = [
        statement
        for statement in observed_selects
        if " from users " in f" {statement} "
    ]

    assert user_selects == []


def test_list_conversations_filters_by_status(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.ARCHIVED,
    )

    result = list_conversations(
        clinic_id=clinic.id,
        status=ConversationStatus.ARCHIVED,
        user_id=user.id,
    )

    assert result["total"] == 1
    assert result["items"][0].id == conversation.id


def test_list_conversations_filters_by_type(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    third_user = make_user(clinic=clinic)

    direct = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    group = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[third_user.id],
    )

    result = list_conversations(
        clinic_id=clinic.id,
        conversation_type=ConversationType.GROUP,
        user_id=user.id,
    )

    assert result["total"] == 1
    assert result["items"][0].id == group.id
    assert result["items"][0].id != direct.id


def test_list_conversations_search(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        title="Emergency Department",
        description="Clinical emergency coordination",
        participant_user_ids=[other_user.id],
    )

    result = list_conversations(
        clinic_id=clinic.id,
        search="Emergency",
        user_id=user.id,
    )

    assert result["total"] == 1
    assert result["items"][0].id == conversation.id


def test_update_conversation_title_and_description(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
        title="Old Title",
        description="Old Description",
    )

    updated = update_conversation(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        title="New Title",
        description="New Description",
    )

    assert updated.title == "New Title"
    assert updated.description == "New Description"


def test_update_conversation_rejects_unknown_fields(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(ValidationError):
        update_conversation(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            conversation_type=ConversationType.DIRECT,
        )


def test_update_conversation_can_clear_text_fields(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
        title="Clinical Team",
        description="Clinical coordination",
    )

    updated = update_conversation(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        title="   ",
        description=None,
    )

    assert updated.title is None
    assert updated.description is None


def test_conversation_status_active_to_archived(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    updated = update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.ARCHIVED,
    )

    assert updated.status == ConversationStatus.ARCHIVED
    assert updated.archived_at is not None
    assert updated.closed_at is None


def test_conversation_status_archived_to_active(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.ARCHIVED,
    )

    updated = update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.ACTIVE,
    )

    assert updated.status == ConversationStatus.ACTIVE
    assert updated.archived_at is None
    assert updated.closed_at is None


def test_conversation_status_active_to_closed(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    updated = update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.CLOSED,
    )

    assert updated.status == ConversationStatus.CLOSED
    assert updated.closed_at is not None


def test_closed_conversation_cannot_reopen(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.CLOSED,
    )

    with pytest.raises(ConflictError):
        update_conversation_status(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            new_status=ConversationStatus.ACTIVE,
        )


def test_add_participant_rejects_closed_conversation(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    target_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    update_conversation_status(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        new_status=ConversationStatus.CLOSED,
    )

    with pytest.raises(ConflictError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=user.id,
            user_id=target_user.id,
        )


def test_add_existing_participant_raises_conflict(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(ConflictError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=user.id,
            user_id=user.id,
        )


def test_add_participant_allows_admin_to_add_new_user(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    target_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    participant = add_participant(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        actor_id=user.id,
        user_id=target_user.id,
    )

    assert participant.id is not None
    assert participant.user_id == target_user.id
    assert participant.conversation_id == conversation.id
    assert participant.status == ParticipantStatus.PENDING
    assert participant.role == ParticipantRole.MEMBER


def test_add_participant_rejects_non_admin_actor(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    target_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    pending = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )["items"][0]

    update_participant(
        participant_id=pending.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    with pytest.raises(ValidationError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=other_user.id,
            user_id=target_user.id,
        )


def test_add_participant_rejects_non_participant_actor(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    outsider = make_user(clinic=clinic)
    target_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(NotFoundError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=outsider.id,
            user_id=target_user.id,
        )


def test_add_participant_rejects_wrong_clinic_actor(
    clinic,
    user,
    make_user,
    make_clinic,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    other_clinic = make_clinic()

    wrong_clinic_actor = make_user(
        clinic=other_clinic,
    )

    target_user = make_user(
        clinic=clinic,
    )

    with pytest.raises(NotFoundError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=wrong_clinic_actor.id,
            user_id=target_user.id,
        )


def test_add_participant_rejects_wrong_clinic_target(
    clinic,
    user,
    make_user,
    make_clinic,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    other_clinic = make_clinic()

    wrong_clinic_target = make_user(
        clinic=other_clinic,
    )

    with pytest.raises(NotFoundError):
        add_participant(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            actor_id=user.id,
            user_id=wrong_clinic_target.id,
        )


def test_add_participant_supports_custom_role(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)
    target_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    participant = add_participant(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        actor_id=user.id,
        user_id=target_user.id,
        role=ParticipantRole.ADMIN,
    )

    assert participant.user_id == target_user.id
    assert participant.role == ParticipantRole.ADMIN
    assert participant.status == ParticipantStatus.PENDING


def test_add_participant_readds_left_participant(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    pending = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )["items"][0]

    update_participant(
        participant_id=pending.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    update_participant(
        participant_id=pending.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.LEFT,
    )

    readded = add_participant(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        actor_id=user.id,
        user_id=other_user.id,
    )

    assert readded.id == pending.id
    assert readded.user_id == other_user.id
    assert readded.status == ParticipantStatus.PENDING
    assert readded.role == ParticipantRole.MEMBER
    assert readded.left_at is None
    assert readded.removed_at is None


def test_list_participants(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
    )

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["per_page"] == 50
    assert result["pages"] == 1
    assert result["has_next"] is False
    assert result["has_previous"] is False


def test_list_participants_filters_by_status(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    assert result["total"] == 1
    assert result["items"][0].user_id == other_user.id


def test_update_participant_status_pending_to_accepted(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    participant = result["items"][0]

    updated = update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    assert updated.status == ParticipantStatus.ACCEPTED
    assert updated.joined_at is not None
    assert updated.left_at is None
    assert updated.removed_at is None


def test_update_participant_role(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    participant = result["items"][0]

    updated = update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        role=ParticipantRole.ADMIN,
    )

    assert updated.role == ParticipantRole.ADMIN


def test_update_participant_rejects_invalid_status_transition(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    participant = result["items"][0]

    update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    with pytest.raises(ConflictError):
        update_participant(
            participant_id=participant.id,
            clinic_id=clinic.id,
            status=ParticipantStatus.PENDING,
        )


def test_update_participant_accept_then_leave(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    participant = result["items"][0]

    update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    updated = update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.LEFT,
    )

    assert updated.status == ParticipantStatus.LEFT
    assert updated.left_at is not None
    assert updated.removed_at is None


def test_leave_conversation(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    result = list_participants(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.PENDING,
    )

    participant = result["items"][0]

    update_participant(
        participant_id=participant.id,
        clinic_id=clinic.id,
        status=ParticipantStatus.ACCEPTED,
    )

    updated = leave_conversation(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        user_id=other_user.id,
    )

    assert updated.id == participant.id
    assert updated.status == ParticipantStatus.LEFT
    assert updated.left_at is not None


def test_last_active_admin_cannot_leave(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.GROUP,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(ConflictError):
        leave_conversation(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            user_id=user.id,
        )


def test_read_state_updates_last_read_message(
    clinic,
    user,
    make_user,
    make_message,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    message = make_message(
        clinic=clinic,
        conversation=conversation,
        sender=user,
    )

    updated = update_participant_read_state(
        conversation_id=conversation.id,
        clinic_id=clinic.id,
        user_id=user.id,
        last_read_message_id=message.id,
    )

    assert updated.user_id == user.id
    assert updated.last_read_message_id == message.id


def test_read_state_rejects_message_from_other_conversation(
    clinic,
    user,
    make_user,
    make_message,
    make_conversation,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    first = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    second = make_conversation(
        clinic=clinic,
        created_by=user,
        conversation_type=ConversationType.GROUP,
    )

    message = make_message(
        clinic=clinic,
        conversation=second,
        sender=user,
    )

    with pytest.raises(NotFoundError):
        update_participant_read_state(
            conversation_id=first.id,
            clinic_id=clinic.id,
            user_id=user.id,
            last_read_message_id=message.id,
        )


def test_read_state_rejects_missing_message(
    clinic,
    user,
    make_user,
    no_audit,
):
    other_user = make_user(clinic=clinic)

    conversation = create_conversation(
        clinic_id=clinic.id,
        created_by_id=user.id,
        conversation_type=ConversationType.DIRECT,
        participant_user_ids=[other_user.id],
    )

    with pytest.raises(NotFoundError):
        update_participant_read_state(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            user_id=user.id,
            last_read_message_id=999999,
        )


def test_create_conversation_respects_participant_limit(
    clinic,
    user,
    no_audit,
):
    participant_user_ids = list(range(1, 502))

    with pytest.raises(ValidationError):
        create_conversation(
            clinic_id=clinic.id,
            created_by_id=user.id,
            conversation_type=ConversationType.GROUP,
            participant_user_ids=participant_user_ids,
        )


def test_pagination_validation(
    clinic,
    no_audit,
):
    with pytest.raises(ValidationError):
        list_conversations(
            clinic_id=clinic.id,
            page=0,
        )

    with pytest.raises(ValidationError):
        list_conversations(
            clinic_id=clinic.id,
            per_page=0,
        )

    with pytest.raises(ValidationError):
        list_conversations(
            clinic_id=clinic.id,
            per_page=501,
        )


def test_participant_pagination_validation(
    clinic,
    conversation,
):
    with pytest.raises(ValidationError):
        list_participants(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            page=0,
        )

    with pytest.raises(ValidationError):
        list_participants(
            conversation_id=conversation.id,
            clinic_id=clinic.id,
            per_page=501,
        )