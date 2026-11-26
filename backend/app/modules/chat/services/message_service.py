from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    MessagePriority,
    MessageStatus,
    MessageType,
)
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional
from app.core.idempotency.services.idempotency_service import normalize_idempotency_key, reserve_idempotency_operation
from app.core.offline_operations.services.disposition_service import assert_operation_not_disposed

from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_model import Message
from app.modules.chat.models.message_revision_model import MessageRevision
from app.modules.chat.services.chat_content_validation_service import (
    ChatContentValidationService,
)
from app.modules.chat.services.chat_policy_service import (
    ChatPolicyService,
)
from app.modules.chat.services.chat_security_service import (
    ChatSecurityService,
)
from app.modules.chat.workers.chat_outbox_worker import (
    create_outbox_event,
)
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value: Any,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"Invalid {field_name}"
        )

    return value


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page <= 0
    ):
        raise ValidationError(
            "Per-page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per-page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _normalize_enum(
    value: Any,
    enum_class,
    field_name: str,
):
    if isinstance(value, enum_class):
        return value

    try:
        return enum_class(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"Invalid {field_name}"
        )


def _normalize_content(
    content: str | None,
) -> str | None:
    if content is None:
        return None

    if not isinstance(content, str):
        raise ValidationError(
            "Message content must be a string"
        )

    content = content.strip()

    if not content:
        return None

    return content


def _get_conversation(
    conversation_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> Conversation:
    _validate_positive_id(
        conversation_id,
        "Conversation ID",
    )

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    statement = db.select(
        Conversation
    ).where(
        Conversation.id == conversation_id,
        Conversation.clinic_id == clinic_id,
    )

    if lock:
        statement = statement.with_for_update()

    conversation = db.session.execute(
        statement
    ).scalar_one_or_none()

    if conversation is None:
        raise NotFoundError(
            f"Conversation {conversation_id} not found"
        )

    return conversation


def _get_message(
    message_id: int,
    clinic_id: int,
    *,
    lock: bool = False,
) -> Message:
    _validate_positive_id(
        message_id,
        "Message ID",
    )

    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    statement = db.select(
        Message
    ).where(
        Message.id == message_id,
        Message.clinic_id == clinic_id,
    )

    if lock:
        statement = statement.with_for_update()

    message = db.session.execute(
        statement
    ).scalar_one_or_none()

    if message is None:
        raise NotFoundError(
            f"Message {message_id} not found"
        )

    return message


def _validate_reply_message(
    conversation: Conversation,
    reply_to_message_id: int | None,
) -> Message | None:
    if reply_to_message_id is None:
        return None

    reply_to_message_id = _validate_positive_id(
        reply_to_message_id,
        "Reply message ID",
    )

    reply_to = db.session.execute(
        db.select(Message).where(
            Message.id == reply_to_message_id,
            Message.clinic_id == conversation.clinic_id,
            Message.conversation_id == conversation.id,
        )
    ).scalar_one_or_none()

    if reply_to is None:
        raise NotFoundError(
            f"Reply message {reply_to_message_id} not found"
        )

    return reply_to


def _ensure_message_can_be_sent(
    conversation: Conversation,
) -> None:
    if conversation.status != ConversationStatus.ACTIVE:
        raise ConflictError(
            "Messages cannot be sent to an inactive conversation"
        )


def _ensure_message_not_deleted(
    message: Message,
) -> None:
    if message.status == MessageStatus.DELETED:
        raise ConflictError(
            "Message has already been deleted"
        )


def _get_next_revision_number(
    message_id: int,
) -> int:
    current_max = db.session.execute(
        db.select(
            func.max(
                MessageRevision.revision_number
            )
        ).where(
            MessageRevision.message_id == message_id,
        )
    ).scalar_one()

    return int(current_max or 0) + 1


@transactional
def create_message(
    clinic_id: int,
    conversation_id: int,
    sender_id: int,
    content: str | None = None,
    message_type: MessageType = MessageType.TEXT,
    priority: MessagePriority = MessagePriority.NORMAL,
    reply_to_message_id: int | None = None,
    idempotency_key: str | None = None,
) -> Message:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        conversation_id,
        "Conversation ID",
    )

    _validate_positive_id(
        sender_id,
        "Sender ID",
    )

    clinic = ensure_clinic_active(
        clinic_id,
    )

    chat_settings = (
        ChatPolicyService.get_settings(
            clinic_id,
        )
    )

    ChatPolicyService.ensure_chat_enabled(
        clinic_id,
        settings=chat_settings,
    )

    conversation = ChatSecurityService.ensure_user_can_send_message(
        sender_id,
        conversation_id,
    )

    if conversation.clinic_id != clinic_id:
        raise NotFoundError(
            "Conversation not found"
        )

    _ensure_message_can_be_sent(
        conversation,
    )

    message_type = _normalize_enum(
        message_type,
        MessageType,
        "message type",
    )

    priority = _normalize_enum(
        priority,
        MessagePriority,
        "message priority",
    )

    if message_type == MessageType.SYSTEM:
        raise ValidationError(
            "System messages cannot be created through this operation"
        )

    content = _normalize_content(
        content,
    )

    if content is not None:
        content = (
            ChatContentValidationService.ensure_text_allowed(
                clinic_id,
                content,
                settings=chat_settings,
            )
        )

    if (
        message_type == MessageType.TEXT
        and content is None
    ):
        raise ValidationError(
            "Text messages require content"
        )

    reply_to = _validate_reply_message(
        conversation,
        reply_to_message_id,
    )

    record = None
    if idempotency_key is not None:
        # Ordinary text only; never grant offline authority through this header.
        if (conversation.conversation_type not in (ConversationType.DIRECT, ConversationType.GROUP)
                or conversation.patient_id is not None or conversation.appointment_id is not None
                or conversation.consultation_id is not None or message_type != MessageType.TEXT
                or priority != MessagePriority.NORMAL or reply_to_message_id is not None):
            raise ValidationError("Keyed chat is limited to ordinary non-case text")
        idempotency_key = normalize_idempotency_key(idempotency_key)
        assert_operation_not_disposed(clinic_id=clinic_id, user_id=sender_id, operation_type="chat.message.create", idempotency_key=idempotency_key)
        record, created = reserve_idempotency_operation(
            clinic_id=clinic_id, user_id=sender_id, operation="chat.message.create",
            idempotency_key=idempotency_key,
            request_payload={"conversation_id": conversation.id, "content": content,
                             "message_type": message_type.value, "priority": priority.value,
                             "reply_to_message_id": reply_to_message_id},
        )
        if not created:
            existing = db.session.get(Message, record.entity_id) if record.entity_type == "Message" and record.entity_id is not None else None
            if (existing is None or existing.clinic_id != clinic_id or existing.sender_id != sender_id
                    or existing.conversation_id != conversation.id or existing.status == MessageStatus.DELETED
                    or existing.content != content or existing.message_type != message_type or existing.priority != priority):
                raise ConflictError("Chat operation requires reconciliation", code="IDEMPOTENCY_RECORD_UNRESOLVED")
            ChatSecurityService.ensure_user_can_access_message(sender_id, existing.id)
            return existing
    message = Message(
        clinic_id=clinic_id,
        conversation_id=conversation.id,
        sender_id=sender_id,
        message_type=message_type,
        content=content,
        reply_to_message_id=(
            reply_to.id
            if reply_to is not None
            else None
        ),
        status=MessageStatus.PENDING,
        priority=priority,
    )

    db.session.add(
        message,
    )

    db.session.flush()

    create_outbox_event(
        clinic_id=clinic_id,
        event_type="message.created",
        payload={
            "conversation_id": conversation.id,
            "message_id": message.id,
            "sender_id": sender_id,
            "message_type": message.message_type.value,
            "priority": message.priority.value,
            "reply_to_message_id": message.reply_to_message_id,
        },
        message_id=message.id,
        clinic_obj=clinic,
        message_obj=message,
    )

    now = _utcnow()

    conversation.last_message_at = now
    conversation.updated_at = now

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Message",
        entity_id=message.id,
        clinic_id=clinic_id,
        description=(
            f"Message {message.id} created "
            f"in conversation {conversation.id}"
        ),
        new_value={
            "conversation_id": conversation.id,
            "sender_id": sender_id,
            "message_type": message.message_type.value,
            "priority": message.priority.value,
            "reply_to_message_id": message.reply_to_message_id,
        },
    )

    if record is not None:
        record.entity_type = "Message"
        record.entity_id = message.id
    return message


def get_message(
    message_id: int,
    clinic_id: int,
    user_id: int,
) -> Message:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    message = _get_message(
        message_id,
        clinic_id,
    )

    ChatSecurityService.ensure_user_can_access_message(
        user_id,
        message.id,
    )

    return message


def list_messages(
    clinic_id: int,
    conversation_id: int,
    user_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
    status: MessageStatus | None = None,
    message_type: MessageType | None = None,
    search: str | None = None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        conversation_id,
        "Conversation ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    conversation = ChatSecurityService.ensure_user_can_access_conversation(
        user_id,
        conversation_id,
    )

    if conversation.clinic_id != clinic_id:
        raise NotFoundError(
            "Conversation not found"
        )

    status = (
        _normalize_enum(
            status,
            MessageStatus,
            "message status",
        )
        if status is not None
        else None
    )

    message_type = (
        _normalize_enum(
            message_type,
            MessageType,
            "message type",
        )
        if message_type is not None
        else None
    )

    if search is not None:
        if not isinstance(search, str):
            raise ValidationError(
                "Message search must be a string"
            )

        search = search.strip()

        if not search:
            search = None

        elif len(search) > 200:
            raise ValidationError(
                "Message search cannot exceed 200 characters"
            )

    query = db.select(
        Message
    ).where(
        Message.clinic_id == clinic_id,
        Message.conversation_id == conversation_id,
    )

    count_query = db.select(
        func.count(Message.id)
    ).where(
        Message.clinic_id == clinic_id,
        Message.conversation_id == conversation_id,
    )

    if status is not None:
        query = query.where(
            Message.status == status,
        )
        count_query = count_query.where(
            Message.status == status,
        )

    if message_type is not None:
        query = query.where(
            Message.message_type == message_type,
        )
        count_query = count_query.where(
            Message.message_type == message_type,
        )

    if search is not None:
        like = f"%{search}%"

        condition = Message.content.ilike(
            like
        )

        query = query.where(
            condition,
        )
        count_query = count_query.where(
            condition,
        )

    total = db.session.execute(
        count_query,
    ).scalar_one()

    offset = (
        page - 1
    ) * per_page

    items = db.session.execute(
        query
        .order_by(
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .offset(offset)
        .limit(per_page),
    ).scalars().unique().all()

    pages = (
        (total + per_page - 1) // per_page
        if total
        else 0
    )

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": (
            page > 1 and total > 0
        ),
    }


@transactional
def edit_message(
    message_id: int,
    clinic_id: int,
    user_id: int,
    content: str,
) -> Message:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    if not isinstance(content, str):
        raise ValidationError(
            "Message content must be a string"
        )

    content = content.strip()

    if not content:
        raise ValidationError(
            "Message content cannot be empty"
        )

    message = _get_message(
        message_id,
        clinic_id,
        lock=True,
    )

    ChatSecurityService.ensure_user_can_edit_message(
        user_id,
        message.id,
    )

    _ensure_message_not_deleted(
        message,
    )

    if message.message_type == MessageType.SYSTEM:
        raise ValidationError(
            "System messages cannot be edited"
        )

    ChatPolicyService.ensure_message_edit_allowed(
        clinic_id,
        message.created_at,
    )

    content = ChatContentValidationService.ensure_text_allowed(
        clinic_id,
        content,
    )

    if content == message.content:
        return message

    revision = MessageRevision(
        clinic_id=clinic_id,
        message_id=message.id,
        edited_by_id=user_id,
        revision_number=_get_next_revision_number(
            message.id,
        ),
        previous_content=message.content,
        previous_message_type=message.message_type.value,
    )

    db.session.add(
        revision,
    )

    message.content = content
    message.status = MessageStatus.EDITED
    message.edited_at = _utcnow()
    message.updated_at = _utcnow()

    create_outbox_event(
        clinic_id=clinic_id,
        event_type="message.updated",
        payload={
            "conversation_id": message.conversation_id,
            "message_id": message.id,
            "sender_id": message.sender_id,
            "status": message.status.value,
            "edited_at": message.edited_at.isoformat(),
        },
        message_id=message.id,
    )

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="Message",
        entity_id=message.id,
        clinic_id=clinic_id,
        description=(
            f"Message {message.id} edited"
        ),
        old_value={
            "content": revision.previous_content,
        },
        new_value={
            "content": content,
            "status": message.status.value,
        },
    )

    return message


@transactional
def delete_message(
    message_id: int,
    clinic_id: int,
    user_id: int,
) -> Message:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    message = _get_message(
        message_id,
        clinic_id,
        lock=True,
    )

    ChatSecurityService.ensure_user_can_delete_message(
        user_id,
        message.id,
    )

    _ensure_message_not_deleted(
        message,
    )

    ChatPolicyService.ensure_message_delete_allowed(
        clinic_id,
        message.created_at,
    )

    previous_status = message.status
    message.status = MessageStatus.DELETED
    message.deleted_at = _utcnow()
    message.updated_at = _utcnow()

    create_outbox_event(
        clinic_id=clinic_id,
        event_type="message.deleted",
        payload={
            "conversation_id": message.conversation_id,
            "message_id": message.id,
            "sender_id": message.sender_id,
            "status": message.status.value,
            "deleted_at": message.deleted_at.isoformat(),
        },
        message_id=message.id,
    )

    create_audit_log(
        action=AuditAction.DELETE,
        entity_type="Message",
        entity_id=message.id,
        clinic_id=clinic_id,
        description=(
            f"Message {message.id} deleted"
        ),
        old_value={
            "status": previous_status.value,
        },
        new_value={
            "status": MessageStatus.DELETED.value,
            "deleted_at": (
                message.deleted_at.isoformat()
            ),
        },
    )

    return message