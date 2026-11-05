"""CT2D1: bounded, fail-closed Celery retry for admission case-chat sync.

The clinical assignment is already committed before this task runs. Only the
clinic administrator recorded with the *latest* queued clinical event may
authorize delivery. Invalid/revoked administrators leave the event pending;
there is no implicit system-admin impersonation or privilege escalation.
"""
from __future__ import annotations

import logging

from app.extensions import celery, db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.services.case_chat_assignment_event_service import (
    deliver_pending_case_chat_sync,
)

logger = logging.getLogger(__name__)


@celery.task(name="process_pending_case_chat_sync")
def process_pending_case_chat_sync(limit: int = 25) -> dict[str, int]:
    """Retry at most `limit` queued admissions without exposing patient IDs.

    Queue creation/reassignment is serialized by the locked admission row.
    Duplicate task attempts remain safe under the existing canonical unique
    link and idempotent reconciliation. No failed delivery is acknowledged.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Case chat sync retry limit must be 1..100")

    rows = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest)
        .where(
            AdmissionCaseChatSyncRequest.request_sequence
            > AdmissionCaseChatSyncRequest.processed_sequence,
        )
        .order_by(
            AdmissionCaseChatSyncRequest.requested_at.asc(),
            AdmissionCaseChatSyncRequest.id.asc(),
        )
        .limit(limit)
    ).scalars().all()
    snapshots = [
        (row.clinic_id, row.admission_id, row.requested_by_user_id)
        for row in rows
    ]
    # Do not hold the read transaction across the downstream chat transactions.
    db.session.rollback()

    processed = 0
    failed = 0
    still_pending = 0
    for clinic_id, admission_id, actor_user_id in snapshots:
        try:
            outcome = deliver_pending_case_chat_sync(
                clinic_id=clinic_id,
                admission_id=admission_id,
                actor_user_id=actor_user_id,
            )
            if outcome["pending"]:
                still_pending += 1
            else:
                processed += 1
        except Exception as exc:
            db.session.rollback()
            failed += 1
            logger.warning(
                "case_chat_sync.scheduled_retry_deferred error_type=%s",
                type(exc).__name__,
            )
    return {
        "claimed": len(snapshots),
        "processed": processed,
        "failed": failed,
        "still_pending": still_pending,
    }
