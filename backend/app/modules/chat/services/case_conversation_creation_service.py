"""CT2C1: create one admission-scoped patient case channel atomically.

Internal service only. No routes, background tasks, or post-creation reconciliation.
Existing chat models, policy controls, participant states and security rules
remain the only chat infrastructure. CT2C2/CT2C3 must add synchronization
and enforce current assignment eligibility at the chat access boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    ParticipantRole,
    ParticipantStatus,
)
from app.core.exceptions import ConflictError
from app.core.utils.decorators import transactional
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
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
def ensure_admission_case_conversation(
    *,
    clinic_id: int,
    admission_id: int,
    actor_user_id: int,
) -> tuple[Conversation, AdmissionCaseConversation, bool]:
    """Create/link an admission case channel and its initial clinical members.

    The initiating actor must be an active clinic admin, but is NOT silently
    added to the patient conversation. Only CT1-confirmed, CT2A-eligible
    primary clinicians become accepted members. This initial membership is
    not a substitute for the later CT2C2 reconciliation/access guard.

    Returns (conversation, link, created). Retries return the same active link.
    All writes and audits occur in a single transaction; no nested chat
    service commit can leave an unlinked channel behind after failure.
    """
    actor = _require_assignment_admin(actor_user_id, clinic_id)
    admission = _get_admission(
        admission_id, clinic_id, lock=True, require_active=True,
    )

    # Keep chat settings enforced on retries as well as creation.
    ensure_clinic_active(clinic_id)
    ChatPolicyService.ensure_chat_enabled(clinic_id)

    existing = get_admission_case_conversation_link(
        clinic_id=clinic_id, admission_id=admission.id,
    )
    if existing is not None:
        _admission, conversation = validate_case_conversation_context(
            clinic_id=clinic_id,
            admission_id=admission.id,
            conversation_id=existing.conversation_id,
        )
        return conversation, existing, False

    ChatPolicyService.ensure_conversation_creation_allowed(clinic_id)
    plan = build_case_chat_membership_plan(
        clinic_id=clinic_id, admission_id=admission.id,
    )
    member_user_ids = plan["eligible_user_ids"]
    if not member_user_ids:
        raise ConflictError(
            "Case conversation requires at least one active assigned clinician"
        )
    ChatPolicyService.ensure_group_conversation_allowed(
        clinic_id, len(member_user_ids),
    )

    conversation = Conversation(
        clinic_id=clinic_id,
        conversation_type=ConversationType.PATIENT,
        status=ConversationStatus.ACTIVE,
        created_by_id=actor.id,
        patient_id=admission.patient_id,
        title=f"Admission {admission.id} clinical case",
        description="Admission-scoped clinical coordination",
    )
    db.session.add(conversation)
    db.session.flush()

    # No general patient-chat members: ONLY verified primary clinicians.
    # The first eligible clinician serves as participant admin, while the
    # initiating clinic admin has no implicit access to this conversation.
    for index, user_id in enumerate(member_user_ids):
        db.session.add(ConversationParticipant(
            clinic_id=clinic_id,
            conversation_id=conversation.id,
            user_id=user_id,
            role=(ParticipantRole.ADMIN if index == 0 else ParticipantRole.MEMBER),
            status=ParticipantStatus.ACCEPTED,
            joined_at=_now(),
        ))
    db.session.flush()

    link = AdmissionCaseConversation(
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        admission_id=admission.id,
        conversation_id=conversation.id,
        linked_by_user_id=actor.id,
    )
    db.session.add(link)
    try:
        db.session.flush()
    except IntegrityError as exc:
        raise ConflictError("Concurrent case conversation linkage detected") from exc

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Conversation",
        entity_id=conversation.id,
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        user_id=actor.id,
        description="Admission case conversation created",
        reason="Authorized admission clinical coordination",
        new_value={
            "admission_id": admission.id,
            "conversation_id": conversation.id,
            "initial_member_count": len(member_user_ids),
        },
    )
    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="AdmissionCaseConversation",
        entity_id=link.id,
        clinic_id=clinic_id,
        patient_id=admission.patient_id,
        user_id=actor.id,
        description="Admission case conversation linked",
        reason="Canonical admission-case association",
        new_value={
            "admission_id": admission.id,
            "conversation_id": conversation.id,
        },
    )
    return conversation, link, True
