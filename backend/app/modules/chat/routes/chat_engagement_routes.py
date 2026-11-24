"""Register P24G bounded engagement routes on the *existing* chat blueprint."""
from __future__ import annotations

from flask import jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError as SchemaError

from app.core.exceptions import DomainError
from app.core.utils.decorators import login_required
from app.modules.chat.services.chat_engagement_service import (
    add_reaction, add_user_mention, delete_reaction, list_reactions,
    list_verified_mentions, reaction_dict,
)


class ReactionBody(BaseModel):
    reaction: str = Field(min_length=1, max_length=32)
    model_config = ConfigDict(extra='forbid')


class UserMentionBody(BaseModel):
    mentioned_user_id: int = Field(ge=1)
    model_config = ConfigDict(extra='forbid')


def register_chat_engagement_routes(blueprint, current_user_id, current_clinic_id):
    def _fail(exc):
        if isinstance(exc, (SchemaError, ValueError)):
            return jsonify({'success': False, 'error': 'Invalid engagement input'}), 422
        if isinstance(exc, DomainError):
            return jsonify({'success': False, 'error': str(exc)}), exc.status_code
        raise exc

    def _pagination():
        try:
            p = int(request.args.get('page', '1'))
            n = int(request.args.get('per_page', '50'))
            if set(request.args) - {'page', 'per_page'}:
                raise ValueError('Unknown pagination fields')
            return p, n
        except (TypeError, ValueError) as exc:
            raise ValueError('Invalid pagination fields') from exc

    @blueprint.get('/messages/<int:message_id>/reactions')
    @login_required
    def get_chat_message_reactions(message_id):
        try:
            page, per_page = _pagination()
            data = list_reactions(message_id=message_id, clinic_id=current_clinic_id(),
                                  user_id=current_user_id(), page=page, per_page=per_page)
            return jsonify({'success': True, **data}), 200
        except (DomainError, SchemaError, ValueError) as exc:
            return _fail(exc)

    @blueprint.post('/messages/<int:message_id>/reactions')
    @login_required
    def add_chat_message_reaction(message_id):
        try:
            payload = ReactionBody.model_validate(request.get_json(silent=True) or {})
            item = add_reaction(message_id=message_id, clinic_id=current_clinic_id(),
                                user_id=current_user_id(), reaction=payload.reaction)
            return jsonify({'success': True, 'data': reaction_dict(item)}), 200
        except (DomainError, SchemaError, ValueError) as exc:
            return _fail(exc)

    @blueprint.delete('/messages/<int:message_id>/reactions/<int:reaction_id>')
    @login_required
    def remove_chat_message_reaction(message_id, reaction_id):
        try:
            delete_reaction(message_id=message_id, reaction_id=reaction_id,
                            clinic_id=current_clinic_id(), user_id=current_user_id())
            return jsonify({'success': True}), 200
        except DomainError as exc:
            return _fail(exc)

    @blueprint.get('/messages/<int:message_id>/mentions')
    @login_required
    def get_chat_message_mentions(message_id):
        try:
            page, per_page = _pagination()
            data = list_verified_mentions(message_id=message_id, clinic_id=current_clinic_id(),
                                          user_id=current_user_id(), page=page, per_page=per_page)
            return jsonify({'success': True, **data}), 200
        except (DomainError, SchemaError, ValueError) as exc:
            return _fail(exc)

    @blueprint.post('/messages/<int:message_id>/mentions')
    @login_required
    def create_chat_message_mention(message_id):
        try:
            payload = UserMentionBody.model_validate(request.get_json(silent=True) or {})
            data = add_user_mention(message_id=message_id,
                                    clinic_id=current_clinic_id(), user_id=current_user_id(),
                                    mentioned_user_id=payload.mentioned_user_id)
            return jsonify({'success': True, 'data': data}), 201
        except (DomainError, SchemaError, ValueError) as exc:
            return _fail(exc)
