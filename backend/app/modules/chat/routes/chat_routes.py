from __future__ import annotations

from datetime import datetime

from io import BytesIO

from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity
from pydantic import ValidationError as PydanticValidationError

from app.extensions import db
from app.core.auth.user.models.user_model import User
from app.core.enums.chat_enums import (
    ParticipantRole,
    ParticipantStatus,
)
from app.core.exceptions import (
    DomainError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import get_current_clinic_id, login_required

from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.schemas.conversation_schema import (
    ConversationCreate,
    ConversationListResponse,
    ConversationPaginationQuery,
    ConversationParticipantCreate,
    ConversationParticipantListResponse,
    ConversationParticipantPaginationQuery,
    ConversationParticipantReadStateUpdate,
    ConversationParticipantResponse,
    ConversationParticipantUpdate,
    ConversationResponse,
    ConversationStatusUpdate,
    ConversationUpdate,
)
from app.modules.chat.schemas.message_schema import (
    MessageCreate,
    MessageListResponse,
    MessagePaginationQuery,
    MessageResponse,
    MessageUpdate,
)
from app.modules.chat.services.chat_security_service import (
    ChatSecurityService,
)
from app.modules.chat.services.case_conversation_link_service import (
    get_admission_case_conversation_link,
    validate_case_conversation_context,
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
from app.modules.chat.services.message_search_service import (
    search_messages,
)
from app.modules.chat.services.chat_media_service import (
    create_media_message,
    read_media_attachment,
)
from app.modules.chat.services.message_service import (
    create_message,
    delete_message,
    edit_message,
    get_message,
    list_messages,
)


chat_bp = Blueprint(
    "internal_clinical_chat",
    __name__,
    url_prefix="/chat",
)


def _current_user() -> User:
    identity = get_jwt_identity()

    try:
        user_id = int(identity)
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid authentication identity"
        )

    if user_id <= 0:
        raise ValidationError(
            "Invalid authentication identity"
        )

    user = db.session.get(
        User,
        user_id,
    )

    if user is None:
        raise NotFoundError(
            "Authenticated user could not be resolved"
        )

    if not user.is_active:
        raise ValidationError(
            "User account is inactive"
        )

    ChatSecurityService.ensure_staff_clinic_consistency(
        user,
    )

    return user


def _current_user_id() -> int:
    return _current_user().id


def _current_clinic_id() -> int:
    return get_current_clinic_id(required=True)


def _sanitize_pydantic_errors(
    exc: PydanticValidationError,
) -> list[dict]:
    sanitized = []

    for error in exc.errors():
        item = dict(error)

        context = item.get("ctx")

        if isinstance(context, dict):
            context = dict(context)

            for key, value in context.items():
                if not isinstance(
                    value,
                    (
                        str,
                        int,
                        float,
                        bool,
                        type(None),
                    ),
                ):
                    context[key] = str(value)

            item["ctx"] = context

        sanitized.append(item)

    return sanitized


def _payload(schema):
    payload = request.get_json(
        silent=True,
    )

    if not isinstance(payload, dict):
        return (
            jsonify(
                {
                    "success": False,
                    "error": (
                        "Request body must be a JSON object"
                    ),
                }
            ),
            422,
        )

    try:
        return schema.model_validate(
            payload,
        )

    except PydanticValidationError as exc:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid request payload",
                    "details": _sanitize_pydantic_errors(
                        exc,
                    ),
                }
            ),
            422,
        )


def _query_payload(schema):
    try:
        data = request.args.to_dict()

        if "page" in data:
            data["page"] = int(
                data["page"]
            )

        if "per_page" in data:
            data["per_page"] = int(
                data["per_page"]
            )

        return schema.model_validate(
            data,
        )

    except PydanticValidationError as exc:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid query parameters",
                    "details": _sanitize_pydantic_errors(
                        exc,
                    ),
                }
            ),
            422,
        )

    except (
        ValueError,
        TypeError,
    ):
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Invalid query parameters",
                }
            ),
            422,
        )


def _domain_error(
    exc: DomainError,
):
    return (
        jsonify(
            {
                "success": False,
                "error": str(exc),
            }
        ),
        exc.status_code,
    )


def _serialize_conversation(
    conversation,
):
    return ConversationResponse.model_validate(
        conversation,
    ).model_dump(
        mode="json",
    )


def _serialize_participant(
    participant,
):
    return ConversationParticipantResponse.model_validate(
        participant,
    ).model_dump(
        mode="json",
    )


def _serialize_message(
    message,
):
    return MessageResponse.model_validate(
        message,
    ).model_dump(
        mode="json",
    )


def _serialize_conversation_list(
    result,
):
    response = ConversationListResponse(
        data=[
            ConversationResponse.model_validate(
                item,
            )
            for item in result["items"]
        ],
        page=result["page"],
        per_page=result["per_page"],
        total=result["total"],
        pages=result["pages"],
        has_next=result["has_next"],
        has_previous=result["has_previous"],
    )

    return response.model_dump(
        mode="json",
    )


def _serialize_participant_list(
    result,
):
    response = ConversationParticipantListResponse(
        data=[
            ConversationParticipantResponse.model_validate(
                item,
            )
            for item in result["items"]
        ],
        page=result["page"],
        per_page=result["per_page"],
        total=result["total"],
        pages=result["pages"],
        has_next=result["has_next"],
        has_previous=result["has_previous"],
    )

    return response.model_dump(
        mode="json",
    )


def _serialize_message_list(
    result,
):
    response = MessageListResponse(
        data=[
            MessageResponse.model_validate(
                item,
            )
            for item in result["items"]
        ],
        page=result["page"],
        per_page=result["per_page"],
        total=result["total"],
        pages=result["pages"],
        has_next=result["has_next"],
        has_previous=result["has_previous"],
    )

    return response.model_dump(
        mode="json",
    )


def _ensure_conversation_admin(
    conversation_id: int,
    clinic_id: int,
    user_id: int,
):
    conversation = (
        ChatSecurityService
        .ensure_user_can_access_conversation(
            user_id,
            conversation_id,
        )
    )

    if conversation.clinic_id != clinic_id:
        raise NotFoundError(
            "Conversation not found"
        )

    participant = db.session.execute(
        db.select(
            ConversationParticipant
        ).where(
            ConversationParticipant.conversation_id
            == conversation.id,
            ConversationParticipant.clinic_id
            == clinic_id,
            ConversationParticipant.user_id
            == user_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )
    ).scalar_one_or_none()

    if participant is None:
        raise NotFoundError(
            "Conversation participant not found"
        )

    if participant.role != ParticipantRole.ADMIN:
        raise ValidationError(
            "Only conversation administrators can perform this operation"
        )

    return conversation


def _ensure_participant_update_allowed(
    conversation_id: int,
    participant_id: int,
    clinic_id: int,
    user_id: int,
    *,
    role,
    status,
):
    participant = db.session.execute(
        db.select(
            ConversationParticipant
        ).where(
            ConversationParticipant.id
            == participant_id,
            ConversationParticipant.conversation_id
            == conversation_id,
            ConversationParticipant.clinic_id
            == clinic_id,
        )
    ).scalar_one_or_none()

    if participant is None:
        raise NotFoundError(
            "Conversation participant not found"
        )

    conversation = (
        ChatSecurityService
        .ensure_user_can_access_conversation(
            user_id,
            conversation_id,
        )
    )

    if conversation.clinic_id != clinic_id:
        raise NotFoundError(
            "Conversation not found"
        )

    actor_participant = (
        ChatSecurityService
        .ensure_user_is_participant(
            user_id,
            conversation,
        )
    )

    is_self = (
        participant.user_id == user_id
    )

    if role is None and status is None:
        raise ValidationError(
            "At least one participant field must be provided"
        )

    if role is not None:
        if is_self:
            raise ValidationError(
                "Users cannot change their own conversation role"
            )

        if (
            actor_participant.role
            != ParticipantRole.ADMIN
        ):
            raise ValidationError(
                "Only conversation administrators can change participant roles"
            )

    if status is not None:
        if is_self:
            allowed_self_statuses = {
                ParticipantStatus.ACCEPTED,
                ParticipantStatus.DECLINED,
                ParticipantStatus.LEFT,
            }

            if status not in allowed_self_statuses:
                raise ValidationError(
                    "Users can only accept, decline, or leave their own participation"
                )

        elif (
            actor_participant.role
            != ParticipantRole.ADMIN
        ):
            raise ValidationError(
                "Only conversation administrators can change another participant's status"
            )

    return participant


def _parse_datetime_query(
    name: str,
):
    value = request.args.get(name)

    if value is None:
        return None

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{name} cannot be empty"
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        raise ValidationError(
            f"{name} must be a valid ISO 8601 datetime"
        )

    if parsed.tzinfo is None:
        raise ValidationError(
            f"{name} must include timezone information"
        )

    return parsed


def _parse_search_int(
    name: str,
):
    value = request.args.get(name)

    if value is None:
        return None

    value = value.strip()

    if not value:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    try:
        parsed = int(value)
    except ValueError:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    if parsed <= 0:
        raise ValidationError(
            f"{name} must be a positive integer"
        )

    return parsed


def _parse_message_search():
    query = request.args.get(
        "query",
    )

    if query is None:
        raise ValidationError(
            "Search query is required"
        )

    query = query.strip()

    if not query:
        raise ValidationError(
            "Search query cannot be empty"
        )

    if len(query) > 200:
        raise ValidationError(
            "Search query cannot exceed 200 characters"
        )

    page = request.args.get(
        "page",
        "1",
    )

    per_page = request.args.get(
        "per_page",
        "50",
    )

    try:
        page = int(page)
        per_page = int(per_page)
    except ValueError:
        raise ValidationError(
            "Page and per_page must be integers"
        )

    if page <= 0:
        raise ValidationError(
            "Page must be a positive integer"
        )

    if per_page <= 0:
        raise ValidationError(
            "Per-page must be a positive integer"
        )

    if per_page > 500:
        raise ValidationError(
            "Per-page cannot exceed 500"
        )

    conversation_id = _parse_search_int(
        "conversation_id",
    )

    sender_id = _parse_search_int(
        "sender_id",
    )

    start_at = _parse_datetime_query(
        "start_at",
    )

    end_at = _parse_datetime_query(
        "end_at",
    )

    return {
        "query": query,
        "page": page,
        "per_page": per_page,
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "message_type": request.args.get(
            "message_type",
        ),
        "status": request.args.get(
            "status",
        ),
        "start_at": start_at,
        "end_at": end_at,
    }


@chat_bp.post("/conversations")
@login_required
def create_chat_conversation():
    payload = _payload(
        ConversationCreate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        if payload.avatar_storage_key is not None:
            raise ValidationError(
                "Conversation avatars are not supported by the current conversation service"
            )

        conversation = create_conversation(
            clinic_id=clinic_id,
            created_by_id=user_id,
            conversation_type=payload.conversation_type,
            title=payload.title,
            description=payload.description,
            patient_id=payload.patient_id,
            appointment_id=payload.appointment_id,
            consultation_id=payload.consultation_id,
            participant_user_ids=payload.participant_user_ids,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_conversation(
                    conversation,
                ),
            }
        ), 201

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get("/admissions/<int:admission_id>/conversation")
@login_required
def get_admission_case_conversation_route(admission_id: int):
    """Read only: canonical admission link plus CURRENT case-chat authorization.

    Missing links, mismatched clinic/admission/patient, expired assignment,
    discharged case and nonmember callers fail closed. No creation or grants.
    """
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()
        link = get_admission_case_conversation_link(
            clinic_id=clinic_id, admission_id=admission_id,
        )
        if link is None:
            raise NotFoundError("Case conversation not found")
        # Enforce live membership and case eligibility before
        # validating the link, so discharged cases return 404.
        conversation = get_conversation(
            conversation_id=link.conversation_id,
            clinic_id=clinic_id,
            user_id=user_id,
        )
        admission, canonical = validate_case_conversation_context(
            clinic_id=clinic_id, admission_id=admission_id,
            conversation_id=link.conversation_id,
        )
        if conversation.id != canonical.id or conversation.patient_id != admission.patient_id:
            raise NotFoundError("Case conversation not found")
        return jsonify({
            "success": True,
            "data": {
                "clinic_id": clinic_id,
                "admission_id": admission.id,
                "patient_id": admission.patient_id,
                "conversation_id": conversation.id,
                "conversation": _serialize_conversation(conversation),
            },
        }), 200
    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get("/conversations")
@login_required
def list_chat_conversations():
    payload = _query_payload(
        ConversationPaginationQuery,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        result = list_conversations(
            clinic_id=clinic_id,
            page=payload.page,
            per_page=payload.per_page,
            status=payload.status,
            conversation_type=payload.conversation_type,
            patient_id=payload.patient_id,
            search=payload.search,
            user_id=user_id,
        )

        return jsonify(
            {
                "success": True,
                **_serialize_conversation_list(
                    result,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get(
    "/conversations/<int:conversation_id>"
)
@login_required
def get_chat_conversation(
    conversation_id: int,
):
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        conversation = get_conversation(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            user_id=user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_conversation(
                    conversation,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.patch(
    "/conversations/<int:conversation_id>"
)
@login_required
def update_chat_conversation(
    conversation_id: int,
):
    payload = _payload(
        ConversationUpdate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        _ensure_conversation_admin(
            conversation_id,
            clinic_id,
            user_id,
        )

        if payload.conversation_type is not None:
            raise ValidationError(
                "Conversation type cannot be changed after creation"
            )

        if payload.avatar_storage_key is not None:
            raise ValidationError(
                "Conversation avatars are not supported by the current conversation service"
            )

        fields = payload.model_dump(
            exclude_unset=True,
        )

        fields.pop(
            "conversation_type",
            None,
        )
        fields.pop(
            "avatar_storage_key",
            None,
        )

        conversation = update_conversation(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            **fields,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_conversation(
                    conversation,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.post(
    "/conversations/<int:conversation_id>/status"
)
@login_required
def update_chat_conversation_status(
    conversation_id: int,
):
    payload = _payload(
        ConversationStatusUpdate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        _ensure_conversation_admin(
            conversation_id,
            clinic_id,
            user_id,
        )

        conversation = update_conversation_status(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            new_status=payload.status,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_conversation(
                    conversation,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.post(
    "/conversations/<int:conversation_id>/participants"
)
@login_required
def add_chat_participant(
    conversation_id: int,
):
    payload = _payload(
        ConversationParticipantCreate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        actor_id = _current_user_id()

        participant = add_participant(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            actor_id=actor_id,
            user_id=payload.user_id,
            role=payload.role,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_participant(
                    participant,
                ),
            }
        ), 201

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get(
    "/conversations/<int:conversation_id>/participants"
)
@login_required
def list_chat_participants(
    conversation_id: int,
):
    payload = _query_payload(
        ConversationParticipantPaginationQuery,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        ChatSecurityService.ensure_user_can_access_conversation(
            user_id,
            conversation_id,
        )

        result = list_participants(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            page=payload.page,
            per_page=payload.per_page,
            status=payload.status,
        )

        return jsonify(
            {
                "success": True,
                **_serialize_participant_list(
                    result,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.patch(
    "/conversations/<int:conversation_id>/participants/<int:participant_id>"
)
@login_required
def update_chat_participant(
    conversation_id: int,
    participant_id: int,
):
    payload = _payload(
        ConversationParticipantUpdate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        _ensure_participant_update_allowed(
            conversation_id=conversation_id,
            participant_id=participant_id,
            clinic_id=clinic_id,
            user_id=user_id,
            role=payload.role,
            status=payload.status,
        )

        participant = update_participant(
            participant_id=participant_id,
            clinic_id=clinic_id,
            role=payload.role,
            status=payload.status,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_participant(
                    participant,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.post(
    "/conversations/<int:conversation_id>/leave"
)
@login_required
def leave_chat_conversation(
    conversation_id: int,
):
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        participant = leave_conversation(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            user_id=user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_participant(
                    participant,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.post(
    "/conversations/<int:conversation_id>/read"
)
@login_required
def update_chat_read_state(
    conversation_id: int,
):
    payload = _payload(
        ConversationParticipantReadStateUpdate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        participant = update_participant_read_state(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            user_id=user_id,
            last_read_message_id=(
                payload.last_read_message_id
            ),
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_participant(
                    participant,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.post(
    "/conversations/<int:conversation_id>/messages"
)
@login_required
def create_chat_message(
    conversation_id: int,
):
    payload = _payload(
        MessageCreate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        sender_id = _current_user_id()

        message = create_message(
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            content=payload.content,
            message_type=payload.message_type,
            priority=payload.priority,
            reply_to_message_id=(
                payload.reply_to_message_id
            ),
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_message(
                    message,
                ),
            }
        ), 201

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get(
    "/conversations/<int:conversation_id>/messages"
)
@login_required
def list_chat_messages(
    conversation_id: int,
):
    payload = _query_payload(
        MessagePaginationQuery,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        if (
            payload.priority is not None
            or payload.sender_id is not None
            or payload.reply_to_message_id is not None
        ):
            raise ValidationError(
                "One or more requested message filters are not supported by this endpoint"
            )

        result = list_messages(
            clinic_id=clinic_id,
            conversation_id=conversation_id,
            user_id=user_id,
            page=payload.page,
            per_page=payload.per_page,
            status=payload.status,
            message_type=payload.message_type,
            search=payload.search,
        )

        return jsonify(
            {
                "success": True,
                **_serialize_message_list(
                    result,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get(
    "/messages/search"
)
@login_required
def search_chat_messages():
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        params = _parse_message_search()

        result = search_messages(
            clinic_id=clinic_id,
            user_id=user_id,
            query=params["query"],
            page=params["page"],
            per_page=params["per_page"],
            conversation_id=params[
                "conversation_id"
            ],
            sender_id=params[
                "sender_id"
            ],
            message_type=params[
                "message_type"
            ],
            status=params[
                "status"
            ],
            start_at=params[
                "start_at"
            ],
            end_at=params[
                "end_at"
            ],
        )

        return jsonify(
            {
                "success": True,
                **_serialize_message_list(
                    result,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get(
    "/messages/<int:message_id>"
)
@login_required
def get_chat_message(
    message_id: int,
):
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        message = get_message(
            message_id=message_id,
            clinic_id=clinic_id,
            user_id=user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_message(
                    message,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.patch(
    "/messages/<int:message_id>"
)
@login_required
def edit_chat_message(
    message_id: int,
):
    payload = _payload(
        MessageUpdate,
    )

    if isinstance(payload, tuple):
        return payload

    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        if payload.content is None:
            raise ValidationError(
                "Message content is required"
            )

        message = edit_message(
            message_id=message_id,
            clinic_id=clinic_id,
            user_id=user_id,
            content=payload.content,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_message(
                    message,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.delete(
    "/messages/<int:message_id>"
)
@login_required
def delete_chat_message(
    message_id: int,
):
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()

        message = delete_message(
            message_id=message_id,
            clinic_id=clinic_id,
            user_id=user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": _serialize_message(
                    message,
                ),
            }
        ), 200

    except DomainError as exc:
        return _domain_error(exc)

# CHAT-F1/F2: the only authenticated chat-media upload/download entry points.
# Both use the canonical message, participant, case ACL and audit/outbox flow.
@chat_bp.post("/conversations/<int:conversation_id>/media")
@login_required
def upload_chat_media(conversation_id: int):
    try:
        clinic_id = _current_clinic_id()
        sender_id = _current_user_id()
        upload = request.files.get("file")
        if upload is None:
            raise ValidationError("A media file is required")
        message, created = create_media_message(
            clinic_id=clinic_id, conversation_id=conversation_id,
            sender_id=sender_id, kind=request.form.get("kind", ""),
            upload=upload, request_id=request.form.get("client_request_id", ""),
            caption=request.form.get("caption"),
        )
        return jsonify({"success": True, "data": _serialize_message(message)}), (201 if created else 200)
    except DomainError as exc:
        return _domain_error(exc)


@chat_bp.get("/messages/<int:message_id>/attachments/<int:attachment_id>")
@login_required
def download_chat_media(message_id: int, attachment_id: int):
    try:
        clinic_id = _current_clinic_id()
        user_id = _current_user_id()
        data, attachment = read_media_attachment(
            clinic_id=clinic_id, user_id=user_id,
            message_id=message_id, attachment_id=attachment_id,
        )
        response = send_file(
            BytesIO(data), mimetype=attachment.mime_type, as_attachment=True,
            download_name=attachment.file_name or "attachment",
        )
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "sandbox"
        return response
    except DomainError as exc:
        return _domain_error(exc)
