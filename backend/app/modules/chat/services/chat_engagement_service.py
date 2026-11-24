"""P24G final chat engagement: bounded participant-only reactions and user mentions.

Existing chat messages, membership/security policies and mention model are reused.
No new persistence tables, notification fanout, socket events, or offline replay.
"""
from __future__ import annotations

from sqlalchemy import func

from app.core.enums.chat_enums import MessageStatus, MessageType, ParticipantStatus
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.models.message_model import Message
from app.modules.chat.models.message_reaction_model import MessageReaction
from app.modules.chat.models.message_mention_model import MessageMention
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.services.mention_service import create_mention
from app.core.enums.chat_enums import MentionType

ALLOWED_REACTIONS = frozenset(('like', 'heart', 'ack', 'thanks'))
MAX_PAGE = 100


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 2147483648:
        raise ValidationError(f'Invalid {name}')


def _message(message_id: int, clinic_id: int, user_id: int, *, lock=False) -> Message:
    _positive(message_id, 'message_id')
    _positive(clinic_id, 'clinic_id')
    _positive(user_id, 'user_id')
    # The existing chat security gate enforces active user, clinic, case-chat
    # authorization and ACCEPTED membership. Never trust a caller's clinic ID.
    checked = ChatSecurityService.ensure_user_can_access_message(user_id, message_id)
    if checked.clinic_id != clinic_id:
        raise NotFoundError('Message not found')
    if lock:
        checked = db.session.execute(
            db.select(Message).where(
                Message.id == message_id,
                Message.clinic_id == clinic_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if checked is None:
            raise NotFoundError('Message not found')
    if checked.status in (MessageStatus.DELETED, MessageStatus.FAILED, MessageStatus.PENDING) \
            or checked.deleted_at is not None or checked.message_type == MessageType.SYSTEM:
        raise ConflictError('Message is not available for engagement')
    return checked


def _reaction(value: str) -> str:
    if not isinstance(value, str) or value not in ALLOWED_REACTIONS:
        raise ValidationError('Unsupported reaction')
    return value


def _page(page: int, per_page: int) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValidationError('Invalid page')
    if isinstance(per_page, bool) or not isinstance(per_page, int) or not 1 <= per_page <= MAX_PAGE:
        raise ValidationError('Invalid per_page')


def reaction_dict(item: MessageReaction) -> dict:
    return {
        'id': item.id, 'clinic_id': item.clinic_id,
        'message_id': item.message_id, 'user_id': item.user_id,
        'reaction': item.reaction,
    }


@transactional
def add_reaction(*, message_id: int, clinic_id: int, user_id: int, reaction: str) -> MessageReaction:
    _reaction(reaction)
    message = _message(message_id, clinic_id, user_id, lock=True)
    existing = db.session.execute(db.select(MessageReaction).where(
        MessageReaction.message_id == message.id,
        MessageReaction.clinic_id == clinic_id,
        MessageReaction.user_id == user_id,
        MessageReaction.reaction == reaction,
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    item = MessageReaction(
        clinic_id=clinic_id, message_id=message.id,
        user_id=user_id, reaction=reaction,
    )
    db.session.add(item)
    db.session.flush()
    create_audit_log(
        action=AuditAction.CREATE, entity_type='MessageReaction', entity_id=item.id,
        description=f'Reaction added to chat message {message_id}',
        user_id=user_id, clinic_id=clinic_id,
        new_value={'message_id': message_id, 'reaction': reaction},
    )
    return item


@transactional
def delete_reaction(*, message_id: int, clinic_id: int, user_id: int, reaction_id: int) -> None:
    _positive(reaction_id, 'reaction_id')
    _message(message_id, clinic_id, user_id, lock=True)
    item = db.session.execute(db.select(MessageReaction).where(
        MessageReaction.id == reaction_id,
        MessageReaction.message_id == message_id,
        MessageReaction.clinic_id == clinic_id,
        MessageReaction.user_id == user_id,
    )).scalar_one_or_none()
    if item is not None:
        create_audit_log(
            action=AuditAction.DELETE, entity_type='MessageReaction', entity_id=item.id,
            description=f'Reaction removed from chat message {message_id}',
            user_id=user_id, clinic_id=clinic_id,
            old_value={'message_id': message_id, 'reaction': item.reaction},
        )
        db.session.delete(item)


def list_reactions(*, message_id: int, clinic_id: int, user_id: int, page: int = 1,
                   per_page: int = 50) -> dict:
    _page(page, per_page)
    _message(message_id, clinic_id, user_id)
    filters = (MessageReaction.message_id == message_id, MessageReaction.clinic_id == clinic_id)
    total = db.session.execute(db.select(func.count(MessageReaction.id)).where(*filters)).scalar_one()
    items = db.session.execute(db.select(MessageReaction).where(*filters)
        .order_by(MessageReaction.id.asc()).offset((page - 1) * per_page).limit(per_page)).scalars().all()
    pages = (total + per_page - 1) // per_page
    return {'data': [reaction_dict(item) for item in items], 'page': page,
            'per_page': per_page, 'total': total, 'pages': pages,
            'has_next': page < pages, 'has_previous': page > 1 and total > 0}


def list_verified_mentions(*, message_id: int, clinic_id: int, user_id: int,
                           page: int = 1, per_page: int = 50) -> dict:
    _page(page, per_page)
    _message(message_id, clinic_id, user_id)
    # Project only USER mentions: pre-existing patient/group metadata is not
    # disclosed through this mobile-specific engagement endpoint.
    filters = (
        MessageMention.message_id == message_id,
        MessageMention.clinic_id == clinic_id,
        MessageMention.mention_type == MentionType.USER,
    )
    total = db.session.execute(db.select(func.count(MessageMention.id)).where(*filters)).scalar_one()
    items = db.session.execute(db.select(MessageMention).where(*filters)
        .order_by(MessageMention.id.asc()).offset((page - 1) * per_page).limit(per_page)).scalars().all()
    pages = (total + per_page - 1) // per_page
    return {'data': [
        {'id': m.id, 'message_id': m.message_id, 'clinic_id': m.clinic_id,
         'mention_type': 'user', 'mentioned_user_id': m.mentioned_user_id,
         'position_start': m.position_start, 'position_end': m.position_end}
        for m in items
    ], 'page': page, 'per_page': per_page, 'total': total, 'pages': pages,
        'has_next': page < pages, 'has_previous': page > 1 and total > 0}


def add_user_mention(*, message_id: int, clinic_id: int, user_id: int,
                     mentioned_user_id: int) -> dict:
    _positive(mentioned_user_id, 'mentioned_user_id')
    message = _message(message_id, clinic_id, user_id)
    if message.sender_id != user_id or message.message_type != MessageType.TEXT:
        raise ConflictError('Only the author may mention a participant on a text message')
    # Disallow arbitrary user/patient/group identifier probing or disclosure.
    target = db.session.execute(db.select(ConversationParticipant).where(
        ConversationParticipant.conversation_id == message.conversation_id,
        ConversationParticipant.clinic_id == clinic_id,
        ConversationParticipant.user_id == mentioned_user_id,
        ConversationParticipant.status == ParticipantStatus.ACCEPTED,
    )).scalar_one_or_none()
    if target is None or mentioned_user_id == user_id:
        raise NotFoundError('Mention target not available')
    # The established service owns validation, uniqueness and audit logging.
    mention = create_mention(
        message_id=message_id, clinic_id=clinic_id, actor_user_id=user_id,
        mention_type=MentionType.USER, mentioned_user_id=mentioned_user_id,
    )
    return {'id': mention.id, 'message_id': mention.message_id,
            'clinic_id': mention.clinic_id, 'mention_type': 'user',
            'mentioned_user_id': mention.mentioned_user_id,
            'position_start': mention.position_start, 'position_end': mention.position_end}
