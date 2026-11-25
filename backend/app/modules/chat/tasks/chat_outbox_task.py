from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import celery, socketio, db
from app.core.auth.user.models.user_model import User
from app.modules.clinic.models.clinic_model import Clinic
from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied, require_tenant_clinic_access,
)
from app.modules.chat.realtime.chat_socket import (
    user_room,
)
from app.modules.chat.services.chat_security_service import (
    ChatSecurityService,
)
from app.modules.chat.workers.chat_outbox_worker import (
    claim_pending_events,
    mark_event_failed,
    mark_event_processed,
    requeue_stale_processing_events,
)

EVENT_NAME_MAP = {
    "message.created": "message.created",
    "message.updated": "message.updated",
    "message.deleted": "message.deleted",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _emit_outbox_event(event) -> None:
    event_name = EVENT_NAME_MAP.get(
        event.event_type,
    )

    if event_name is None:
        raise ValueError(
            f"Unsupported chat outbox event type: "
            f"{event.event_type}"
        )

    payload = {
        **event.payload,
        "event_id": event.id,
        "clinic_id": event.clinic_id,
        "event_type": event.event_type,
    }

    conversation_id = payload.get(
        "conversation_id",
    )

    if not isinstance(
        conversation_id,
        int,
    ) or conversation_id <= 0:
        raise ValueError(
            "Chat outbox event requires a valid "
            "conversation_id"
        )

    recipient_user_ids = (
        ChatSecurityService
        .get_active_conversation_recipient_user_ids(
            event.clinic_id,
            conversation_id,
        )
    )

    clinic = db.session.get(Clinic, event.clinic_id)
    if clinic is None:
        raise ValueError("Chat event clinic unavailable")
    for user_id in recipient_user_ids:
        recipient = db.session.get(User, user_id)
        if recipient is None or not recipient.is_active:
            continue
        try:
            require_tenant_clinic_access(
                user_id=user_id,
                organization_id=clinic.organization_id,
                clinic_id=clinic.id,
            )
        except TenantAccessDenied:
            # Revoked or cross-tenant recipients never receive live events.
            continue
        socketio.emit(
            event_name,
            payload,
            room=user_room(
                event.clinic_id,
                user_id,
                recipient.token_version,
            ),
            namespace="/chat",
        )


@celery.task(
    name="process_chat_outbox",
)
def process_chat_outbox(
    limit: int = 50,
) -> dict[str, int]:
    events = claim_pending_events(
        limit=limit,
    )

    processed = 0
    failed = 0

    for event in events:
        try:
            _emit_outbox_event(
                event,
            )

            mark_event_processed(
                event_id=event.id,
                clinic_id=event.clinic_id,
                processed_at=_utcnow(),
            )

            processed += 1

        except Exception as exc:
            failed += 1

            try:
                mark_event_failed(
                    event_id=event.id,
                    clinic_id=event.clinic_id,
                    error=str(exc),
                )
            except Exception:
                pass

    return {
        "claimed": len(events),
        "processed": processed,
        "failed": failed,
    }


@celery.task(
    name="recover_stale_chat_outbox",
)
def recover_stale_chat_outbox(
    stale_after_seconds: int = 300,
    limit: int = 50,
) -> dict[str, int]:
    events = requeue_stale_processing_events(
        stale_after_seconds=stale_after_seconds,
        limit=limit,
    )

    return {
        "requeued": len(events),
    }