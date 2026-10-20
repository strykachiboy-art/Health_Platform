from __future__ import annotations

from flask import request, session

from app.core.autoscaling.drain_state import (
    reserve_socket_connection,
    release_socket_connection,
)
from flask_jwt_extended import decode_token
from flask_socketio import emit, join_room, leave_room

from app.core.auth.user.services.clinic_context_service import resolve_effective_clinic_id
from app.core.enums.role_enums import Role
from app.core.auth.user.services.token_service import (
    is_token_revoked,
)
from app.extensions import socketio
from app.modules.chat.services.chat_security_service import (
    ChatSecurityService,
)


CHAT_NAMESPACE = "/chat"

CHAT_CONVERSATION_ROOM_PREFIX = "chat:conversation:"
CHAT_USER_ROOM_PREFIX = "chat:user:"


def conversation_room(
    clinic_id: int,
    conversation_id: int,
) -> str:
    return (
        f"{CHAT_CONVERSATION_ROOM_PREFIX}"
        f"{clinic_id}:{conversation_id}"
    )


def user_room(
    clinic_id: int,
    user_id: int,
) -> str:
    return (
        f"{CHAT_USER_ROOM_PREFIX}"
        f"{clinic_id}:{user_id}"
    )


def _authenticate_socket(
    auth: object,
) -> tuple[int, int]:
    if not isinstance(auth, dict):
        raise ValueError(
            "Authentication data is required"
        )

    access_token = auth.get(
        "access_token"
    )

    if (
        not isinstance(access_token, str)
        or not access_token.strip()
    ):
        raise ValueError(
            "Access token is required"
        )

    claims = decode_token(
        access_token
    )

    if is_token_revoked(claims):
        raise ValueError(
            "Authentication token has been revoked"
        )

    identity = claims.get("sub")

    try:
        user_id = int(identity)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Invalid authentication identity"
        ) from exc

    user = ChatSecurityService.get_active_user(
        user_id
    )

    ChatSecurityService.ensure_staff_clinic_consistency(
        user
    )

    clinic_id = resolve_effective_clinic_id(
        user_id=user.id,
        jwt_payload=claims,
    )

    if clinic_id is None:
        raise ValueError(
            "User is not associated with a clinic"
        )

    if user.role is not Role.SUPER_ADMIN:
        ChatSecurityService.ensure_same_clinic(
            user,
            clinic_id,
        )

    return user.id, clinic_id


def _get_socket_context() -> tuple[int, int]:
    user_id = session.get(
        "chat_user_id"
    )

    clinic_id = session.get(
        "chat_clinic_id"
    )

    if user_id is None or clinic_id is None:
        raise ValueError(
            "Socket authentication context is missing"
        )

    return (
        int(user_id),
        int(clinic_id),
    )


def _get_conversation_id(
    data: object,
) -> int:
    if not isinstance(data, dict):
        raise ValueError(
            "Invalid socket payload"
        )

    conversation_id = data.get(
        "conversation_id"
    )

    try:
        conversation_id = int(
            conversation_id
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Conversation ID must be an integer"
        ) from exc

    if conversation_id <= 0:
        raise ValueError(
            "Conversation ID must be positive"
        )

    return conversation_id


@socketio.on(
    "connect",
    namespace=CHAT_NAMESPACE,
)
def handle_connect(auth=None):
    sid = request.sid

    # Reserve the connection before authentication to close the race where
    # a drain begins while the connection handler is still executing.
    if not reserve_socket_connection(sid):
        return False

    session["backend_drain_socket_tracked"] = True

    try:
        user_id, clinic_id = _authenticate_socket(
            auth
        )

        session["chat_user_id"] = user_id
        session["chat_clinic_id"] = clinic_id

        join_room(
            user_room(
                clinic_id,
                user_id,
            )
        )

        emit(
            "chat.connected",
            {
                "user_id": user_id,
                "clinic_id": clinic_id,
            },
            room=sid,
        )

        return True

    except Exception:
        session.pop("backend_drain_socket_tracked", None)
        release_socket_connection(sid)
        return False


@socketio.on(
    "disconnect",
    namespace=CHAT_NAMESPACE,
)
def handle_disconnect():
    session.pop(
        "chat_user_id",
        None,
    )

    session.pop(
        "chat_clinic_id",
        None,
    )

    if session.pop("backend_drain_socket_tracked", False):
        release_socket_connection(request.sid)


@socketio.on(
    "conversation.join",
    namespace=CHAT_NAMESPACE,
)
def handle_conversation_join(data):
    try:
        user_id, clinic_id = _get_socket_context()

        conversation_id = _get_conversation_id(
            data
        )

        conversation = (
            ChatSecurityService
            .ensure_user_can_access_conversation(
                user_id,
                conversation_id,
            )
        )

        if conversation.clinic_id != clinic_id:
            raise ValueError(
                "Conversation does not belong to user clinic"
            )

        room = conversation_room(
            clinic_id,
            conversation.id,
        )

        join_room(room)

        emit(
            "conversation.joined",
            {
                "conversation_id": conversation.id,
                "user_id": user_id,
            },
            room=request.sid,
        )

    except Exception:
        emit(
            "chat.error",
            {
                "code": "conversation_join_failed",
                "message": "Unable to join conversation",
            },
            room=request.sid,
        )


@socketio.on(
    "conversation.leave",
    namespace=CHAT_NAMESPACE,
)
def handle_conversation_leave(data):
    try:
        user_id, clinic_id = _get_socket_context()

        conversation_id = _get_conversation_id(
            data
        )

        conversation = (
            ChatSecurityService
            .ensure_user_can_access_conversation(
                user_id,
                conversation_id,
            )
        )

        if conversation.clinic_id != clinic_id:
            raise ValueError(
                "Conversation does not belong to user clinic"
            )

        leave_room(
            conversation_room(
                clinic_id,
                conversation.id,
            )
        )

        emit(
            "conversation.left",
            {
                "conversation_id": conversation.id,
                "user_id": user_id,
            },
            room=request.sid,
        )

    except Exception:
        emit(
            "chat.error",
            {
                "code": "conversation_leave_failed",
                "message": "Unable to leave conversation",
            },
            room=request.sid,
        )