"""Read-only, fail-closed admission/case-conversation context validation.

CT2B deliberately contains no link write, chat creation, or membership grant.
CT2C must validate actor/chat policy/membership before persisting links.
"""

from __future__ import annotations

from app.core.enums.chat_enums import ConversationStatus, ConversationType
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.nursing.services.nursing_service import _get_admission


def _positive_id(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{label} must be a positive integer")
    return value


def validate_case_conversation_context(
    *, clinic_id: int, admission_id: int, conversation_id: int,
) -> tuple:
    """Check canonical context, not actor permissions.

    Caller must separately authorize user and chat policy. This helper is
    strictly for internal trusted use and grants no access by itself.
    """
    _positive_id(clinic_id, "clinic_id")
    _positive_id(admission_id, "admission_id")
    _positive_id(conversation_id, "conversation_id")

    admission = _get_admission(admission_id, clinic_id, require_active=True)
    conversation = db.session.execute(
        db.select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.clinic_id == clinic_id,
        )
    ).scalar_one_or_none()
    if conversation is None or conversation.patient_id != admission.patient_id:
        raise NotFoundError("Patient case conversation not found")
    if conversation.conversation_type != ConversationType.PATIENT:
        raise ConflictError("A patient case conversation is required")
    if conversation.status != ConversationStatus.ACTIVE:
        raise ConflictError("Case conversation must be active")
    return admission, conversation


def get_admission_case_conversation_link(
    *, clinic_id: int, admission_id: int,
) -> AdmissionCaseConversation | None:
    """Retrieve an existing association, including after discharge/archive.

    Caller is responsible for read authorization; no route calls this yet.
    """
    _positive_id(clinic_id, "clinic_id")
    _positive_id(admission_id, "admission_id")
    admission = _get_admission(admission_id, clinic_id, require_active=False)
    return db.session.execute(
        db.select(AdmissionCaseConversation).where(
            AdmissionCaseConversation.clinic_id == clinic_id,
            AdmissionCaseConversation.admission_id == admission.id,
            AdmissionCaseConversation.patient_id == admission.patient_id,
        )
    ).scalar_one_or_none()
