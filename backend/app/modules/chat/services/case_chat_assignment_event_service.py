"""CT2C3 durable, nonblocking clinical-assignment -> case-chat dispatch.

The *clinical* transaction enqueues the request. After it commits, delivery
attempts the existing CT2C1 case creation and CT2C2 membership reconciliation.
A chat outage leaves the request pending; live CT2C2 checks deny stale access.
No background worker or API route is installed in this slice.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.services.case_conversation_creation_service import (
    ensure_admission_case_conversation,
)
from app.modules.chat.services.case_chat_membership_sync_service import (
    reconcile_admission_case_chat_membership,
)
from app.modules.nursing.services.care_team_assignment_service import (
    _require_assignment_admin,
)
from app.modules.nursing.services.nursing_service import _get_admission

logger = logging.getLogger(__name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def queue_case_chat_sync_in_clinical_transaction(
    *, clinic_id: int, admission_id: int, actor_user_id: int,
) -> int:
    """Must be called while the locked clinical transaction is still open.

    Repeated events increment one admission-scoped sequence. Queue durability
    comes from the same commit as the audited CT1 assignment change.
    """
    _require_assignment_admin(actor_user_id, clinic_id)
    row = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == clinic_id,
            AdmissionCaseChatSyncRequest.admission_id == admission_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = AdmissionCaseChatSyncRequest(
            clinic_id=clinic_id,
            admission_id=admission_id,
            requested_by_user_id=actor_user_id,
            authorization_handoff_id=None,
            request_sequence=1,
            processed_sequence=0,
            requested_at=_utcnow(),
        )
        db.session.add(row)
    else:
        row.request_sequence += 1
        row.requested_by_user_id = actor_user_id
        row.authorization_handoff_id = None
        row.requested_at = _utcnow()
    db.session.flush()
    return row.request_sequence


def queue_handoff_case_chat_sync_in_clinical_transaction(*, handoff, actor) -> int:
    """Record nurse-originated provenance within the ACKNOWLEDGEMENT commit.

    This operation NEVER permits arbitrary nurse enqueues: the acknowledged
    handoff, incoming nurse and current assignment must all match.
    """
    from app.modules.chat.services.case_chat_handoff_authorization import (
        require_acknowledged_handoff_sync_authority,
    )
    require_acknowledged_handoff_sync_authority(
        clinic_id=handoff.clinic_id,
        admission_id=handoff.admission_id,
        actor_user_id=actor.user_id,
        handoff_id=handoff.id,
    )
    row = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == handoff.clinic_id,
            AdmissionCaseChatSyncRequest.admission_id == handoff.admission_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = AdmissionCaseChatSyncRequest(
            clinic_id=handoff.clinic_id, admission_id=handoff.admission_id,
            requested_by_user_id=actor.user_id,
            authorization_handoff_id=handoff.id,
            request_sequence=1, processed_sequence=0, requested_at=_utcnow(),
        )
        db.session.add(row)
    else:
        row.request_sequence += 1
        row.requested_by_user_id = actor.user_id
        row.authorization_handoff_id = handoff.id
        row.requested_at = _utcnow()
    db.session.flush()
    return row.request_sequence


@transactional
def _acknowledge_request_if_current(
    *, clinic_id: int, admission_id: int, expected_sequence: int,
) -> bool:
    """Never acknowledge a newer event using an older delivery result."""
    row = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == clinic_id,
            AdmissionCaseChatSyncRequest.admission_id == admission_id,
        ).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.request_sequence != expected_sequence:
        return False
    row.processed_sequence = expected_sequence
    row.processed_at = _utcnow()
    db.session.flush()
    return True


def deliver_pending_case_chat_sync(
    *, clinic_id: int, admission_id: int, actor_user_id: int,
) -> dict:
    """Authorized bounded retry of one admission's latest pending generation.

    Chat creation and reconciliation use their existing transactional services.
    If either fails, the pending generation remains intact. Successful delivery
    is idempotent, and stale acknowledgements never discard newer requests.
    """
    # Clinic-scoped admission existence remains mandatory, including queue miss.
    _get_admission(admission_id, clinic_id, require_active=False)
    row = db.session.execute(
        db.select(AdmissionCaseChatSyncRequest).where(
            AdmissionCaseChatSyncRequest.clinic_id == clinic_id,
            AdmissionCaseChatSyncRequest.admission_id == admission_id,
        )
    ).scalar_one_or_none()
    if row is None:
        _require_assignment_admin(actor_user_id, clinic_id)
        return {"pending": False, "changed": False}
    from app.core.exceptions import NotFoundError
    if row.requested_by_user_id != actor_user_id:
        raise NotFoundError("Case chat synchronization request not found")
    handoff_id = row.authorization_handoff_id
    if handoff_id is None:
        _require_assignment_admin(actor_user_id, clinic_id)
    else:
        from app.modules.chat.services.case_chat_handoff_authorization import (
            require_acknowledged_handoff_sync_authority,
        )
        require_acknowledged_handoff_sync_authority(
            clinic_id=clinic_id, admission_id=admission_id,
            actor_user_id=actor_user_id, handoff_id=handoff_id,
        )
    if row.request_sequence == row.processed_sequence:
        return {"pending": False, "changed": False}
    sequence = row.request_sequence

    _conversation, _link, created = ensure_admission_case_conversation(
        clinic_id=clinic_id, admission_id=admission_id,
        actor_user_id=actor_user_id, authorization_handoff_id=handoff_id,
    )
    reconciliation = reconcile_admission_case_chat_membership(
        clinic_id=clinic_id, admission_id=admission_id,
        actor_user_id=actor_user_id, authorization_handoff_id=handoff_id,
    )
    acknowledged = _acknowledge_request_if_current(
        clinic_id=clinic_id, admission_id=admission_id,
        expected_sequence=sequence,
    )
    return {
        "pending": not acknowledged,
        "changed": bool(created or reconciliation["changed"]),
        "created": bool(created),
        "acknowledged_sequence": sequence if acknowledged else None,
    }


def attempt_case_chat_sync_after_clinical_commit(
    *, clinic_id: int, admission_id: int, actor_user_id: int,
) -> None:
    """Best effort only: do not turn a successful clinical write into an error.

    A failed dispatch is *not* silently acknowledged: the durable request is
    still pending and can be retried using deliver_pending_case_chat_sync.
    Do not log patient, admission or clinic IDs in a failure message.
    """
    try:
        deliver_pending_case_chat_sync(
            clinic_id=clinic_id, admission_id=admission_id,
            actor_user_id=actor_user_id,
        )
    except Exception as exc:
        db.session.rollback()
        logger.warning(
            "case_chat_sync.deferred_after_clinical_commit error_type=%s",
            type(exc).__name__,
        )
