"""CT2C2: admission-locked reconciliation of existing chat participants.

Internal-only. Does not create channels, bypass chat settings, or create an
API. A separate CT2C3 integration gate will hook this into assignment changes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import ParticipantRole, ParticipantStatus
from app.core.exceptions import NotFoundError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.services.case_chat_membership_plan import (
    build_case_chat_membership_plan,
)
from app.modules.chat.services.case_conversation_link_service import (
    get_admission_case_conversation_link,
    validate_case_conversation_context,
)
from app.modules.chat.services.chat_policy_service import ChatPolicyService
from app.modules.clinic.services.clinic_service import ensure_clinic_active
from app.modules.nursing.services.care_team_assignment_service import (
    _require_assignment_admin,
)
from app.modules.nursing.services.nursing_service import _get_admission


def _now() -> datetime:
    return datetime.now(timezone.utc)


@transactional
def reconcile_admission_case_chat_membership(
    *, clinic_id: int, admission_id: int, actor_user_id: int,
) -> dict:
    """Replace stale membership with the CURRENT CT2A assignment roster.

    Admission row lock serializes against CT1 assignment writes. Audit and
    participant changes commit together. No participant can bypass the live
    case guard merely because sync has not yet happened.
    """
    actor = _require_assignment_admin(actor_user_id, clinic_id)
    admission = _get_admission(
        admission_id, clinic_id, lock=True, require_active=True,
    )
    ensure_clinic_active(clinic_id)
    link = get_admission_case_conversation_link(
        clinic_id=clinic_id, admission_id=admission.id,
    )
    if link is None:
        raise NotFoundError("Admission case conversation not found")
    _admission, conversation = validate_case_conversation_context(
        clinic_id=clinic_id, admission_id=admission.id,
        conversation_id=link.conversation_id,
    )

    plan = build_case_chat_membership_plan(
        clinic_id=clinic_id, admission_id=admission.id,
    )
    wanted = set(plan["eligible_user_ids"])
    # Revocation must be possible even when chat has been disabled. A
    # disabled policy must never be used to grant or revive memberships.
    if wanted:
        ChatPolicyService.ensure_chat_enabled(clinic_id)
        ChatPolicyService.ensure_group_conversation_allowed(
            clinic_id, len(wanted),
        )

    existing = db.session.execute(
        db.select(ConversationParticipant).where(
            ConversationParticipant.clinic_id == clinic_id,
            ConversationParticipant.conversation_id == conversation.id,
        ).order_by(ConversationParticipant.user_id).with_for_update()
    ).scalars().all()
    by_user = {row.user_id: row for row in existing}
    added = []
    revoked = []
    role_changes = []
    now = _now()

    for row in existing:
        if row.user_id not in wanted and row.status != ParticipantStatus.REMOVED:
            row.status = ParticipantStatus.REMOVED
            row.role = ParticipantRole.MEMBER
            row.removed_at = now
            row.left_at = None
            revoked.append(row.user_id)

    # Exactly one eligible clinician holds chat admin role. The initiating
    # clinic admin is not implicitly a member and cannot acquire access here.
    chat_admin_user_id = min(wanted) if wanted else None
    for user_id in sorted(wanted):
        new_role = (
            ParticipantRole.ADMIN if user_id == chat_admin_user_id
            else ParticipantRole.MEMBER
        )
        row = by_user.get(user_id)
        if row is None:
            row = ConversationParticipant(
                clinic_id=clinic_id,
                conversation_id=conversation.id,
                user_id=user_id,
                role=new_role,
                status=ParticipantStatus.ACCEPTED,
                joined_at=now,
            )
            db.session.add(row)
            added.append(user_id)
        else:
            if row.status != ParticipantStatus.ACCEPTED:
                row.status = ParticipantStatus.ACCEPTED
                row.joined_at = now
                row.left_at = None
                row.removed_at = None
                added.append(user_id)
            if row.role != new_role:
                row.role = new_role
                role_changes.append(user_id)

    db.session.flush()
    if added or revoked or role_changes:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="AdmissionCaseConversation",
            entity_id=link.id,
            clinic_id=clinic_id,
            patient_id=admission.patient_id,
            user_id=actor.id,
            description="Admission case chat membership reconciled",
            reason="Current authoritative primary clinician assignment roster",
            new_value={
                "admission_id": admission.id,
                "conversation_id": conversation.id,
                "eligible_user_ids": sorted(wanted),
                "added_user_ids": sorted(added),
                "revoked_user_ids": sorted(revoked),
                "role_changed_user_ids": sorted(set(role_changes)),
            },
        )
    return {
        "admission_id": admission.id,
        "conversation_id": conversation.id,
        "eligible_user_ids": sorted(wanted),
        "added_user_ids": sorted(added),
        "revoked_user_ids": sorted(revoked),
        "role_changed_user_ids": sorted(set(role_changes)),
        "changed": bool(added or revoked or role_changes),
    }
