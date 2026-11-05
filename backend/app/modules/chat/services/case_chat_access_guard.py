"""CT2C2: fail-closed active-assignment gate for admission-linked case chat.

Only canonical admission case conversations are affected. Ordinary legacy chat
continues through the existing participant checks, without new privileges.
"""

from __future__ import annotations

from app.core.enums.chat_enums import ConversationStatus, ConversationType
from app.core.exceptions import DomainError, NotFoundError
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.services.case_chat_membership_plan import (
    build_case_chat_membership_plan,
)
from app.modules.chat.services.chat_policy_service import ChatPolicyService
from app.modules.nursing.services.nursing_service import _get_admission


def current_case_chat_eligible_user_ids(
    *, clinic_id: int, conversation_id: int,
) -> set[int] | None:
    """None for non-case chat; exact eligible staff IDs for a canonical case.

    A linked but inconsistent, disabled, or discharged case returns a DENIAL,
    never a fallback to ordinary chat membership. Unexpected DB failures also
    propagate as errors instead of allowing access.
    """
    link = db.session.execute(
        db.select(AdmissionCaseConversation).where(
            AdmissionCaseConversation.clinic_id == clinic_id,
            AdmissionCaseConversation.conversation_id == conversation_id,
        )
    ).scalar_one_or_none()
    if link is None:
        return None

    conversation = db.session.execute(
        db.select(Conversation).where(
            Conversation.id == link.conversation_id,
            Conversation.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if (
        conversation is None
        or conversation.patient_id != link.patient_id
        or conversation.conversation_type != ConversationType.PATIENT
        or conversation.status != ConversationStatus.ACTIVE
    ):
        raise NotFoundError("Case conversation unavailable")

    try:
        admission = _get_admission(
            link.admission_id, clinic_id, require_active=True,
        )
        if admission.patient_id != link.patient_id:
            raise NotFoundError("Case conversation unavailable")
        ChatPolicyService.ensure_chat_enabled(clinic_id)
        plan = build_case_chat_membership_plan(
            clinic_id=clinic_id, admission_id=admission.id,
        )
    except DomainError as exc:
        raise NotFoundError("Case conversation unavailable") from exc
    return set(plan["eligible_user_ids"])


def ensure_case_chat_user_allowed(
    *, clinic_id: int, conversation_id: int, user_id: int,
) -> None:
    eligible = current_case_chat_eligible_user_ids(
        clinic_id=clinic_id, conversation_id=conversation_id,
    )
    if eligible is not None and user_id not in eligible:
        raise NotFoundError("Conversation participant not found")


def filter_case_chat_recipient_user_ids(
    *, clinic_id: int, conversation_id: int, user_ids: list[int],
) -> list[int]:
    eligible = current_case_chat_eligible_user_ids(
        clinic_id=clinic_id, conversation_id=conversation_id,
    )
    if eligible is None:
        return user_ids
    return [user_id for user_id in user_ids if user_id in eligible]
