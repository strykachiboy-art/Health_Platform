from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func

from app.extensions import db
from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import (
    ConversationStatus,
    ConversationType,
    ParticipantRole,
    ParticipantStatus,
)
from app.core.enums.appointment_enums import AppointmentStatus
from app.core.enums.consultation_enums import ConsultationStatus
from app.core.enums.staff_enums import StaffStatus
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.utils.decorators import transactional

from app.modules.appointment.models.appointment_model import Appointment
from app.modules.clinic.services.clinic_service import (
    ensure_clinic_active,
)
from app.modules.consultation.models.consultation_model import Consultation
from app.core.auth.user.models.user_model import User
from app.modules.patient.models.patient_model import Patient
from app.modules.staff.models.staff_model import Staff

from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.models.message_model import Message
from app.modules.chat.services.chat_policy_service import (
    ChatPolicyService,
)
from app.modules.chat.services.chat_security_service import (
    ChatSecurityService,
)


DEFAULT_PAGE = 1
DEFAULT_PER_PAGE = 50
MAX_PER_PAGE = 500
MAX_INITIAL_PARTICIPANTS = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_positive_id(
    value: Any,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValidationError(
            f"Invalid {field_name}"
        )

    return value


def _validate_pagination(
    page: int,
    per_page: int,
) -> tuple[int, int]:
    if (
        isinstance(page, bool)
        or not isinstance(page, int)
        or page <= 0
    ):
        raise ValidationError(
            "Page must be a positive integer"
        )

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or per_page <= 0
    ):
        raise ValidationError(
            "Per-page must be a positive integer"
        )

    if per_page > MAX_PER_PAGE:
        raise ValidationError(
            f"Per-page cannot exceed {MAX_PER_PAGE}"
        )

    return page, per_page


def _normalize_enum(
    value: Any,
    enum_class,
    field_name: str,
):
    if isinstance(value, enum_class):
        return value

    try:
        return enum_class(value)
    except (TypeError, ValueError):
        raise ValidationError(
            f"Invalid {field_name}"
        )


def _normalize_optional_text(
    value: str | None,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        return None

    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} cannot exceed "
            f"{max_length} characters"
        )

    return value


def _get_user(
    user_id: int,
    *,
    clinic_id: int | None = None,
    active_only: bool = False,
) -> User:
    _validate_positive_id(
        user_id,
        "User ID",
    )

    statement = db.select(User).where(
        User.id == user_id,
    )

    if clinic_id is not None:
        statement = statement.where(
            User.clinic_id == clinic_id,
        )

    if active_only:
        statement = statement.where(
            User.is_active.is_(True),
        )

    user = db.session.execute(
        statement,
    ).scalar_one_or_none()

    if user is None:
        raise NotFoundError(
            f"User {user_id} not found"
        )

    return user


def _get_patient(
    patient_id: int,
    clinic_id: int,
    *,
    active_only: bool = False,
) -> Patient:
    _validate_positive_id(
        patient_id,
        "Patient ID",
    )

    statement = db.select(Patient).where(
        Patient.id == patient_id,
        Patient.clinic_id == clinic_id,
    )

    if active_only:
        statement = statement.where(
            Patient.is_active.is_(True),
        )

    patient = db.session.execute(
        statement,
    ).scalar_one_or_none()

    if patient is None:
        raise NotFoundError(
            f"Patient {patient_id} not found"
        )

    return patient


def _get_appointment(
    appointment_id: int,
    clinic_id: int,
) -> Appointment:
    _validate_positive_id(
        appointment_id,
        "Appointment ID",
    )

    appointment = db.session.execute(
        db.select(Appointment).where(
            Appointment.id == appointment_id,
            Appointment.clinic_id == clinic_id,
        ),
    ).scalar_one_or_none()

    if appointment is None:
        raise NotFoundError(
            f"Appointment {appointment_id} not found"
        )

    return appointment


def _get_consultation(
    consultation_id: int,
    clinic_id: int,
) -> Consultation:
    _validate_positive_id(
        consultation_id,
        "Consultation ID",
    )

    consultation = db.session.execute(
        db.select(Consultation).where(
            Consultation.id == consultation_id,
            Consultation.clinic_id == clinic_id,
        ),
    ).scalar_one_or_none()

    if consultation is None:
        raise NotFoundError(
            f"Consultation {consultation_id} not found"
        )

    return consultation


def _get_conversation(
    conversation_id: int,
    *,
    clinic_id: int | None = None,
    lock: bool = False,
) -> Conversation:
    _validate_positive_id(
        conversation_id,
        "Conversation ID",
    )

    statement = db.select(
        Conversation
    ).where(
        Conversation.id == conversation_id,
    )

    if clinic_id is not None:
        statement = statement.where(
            Conversation.clinic_id == clinic_id,
        )

    if lock:
        statement = statement.with_for_update()

    conversation = db.session.execute(
        statement,
    ).scalar_one_or_none()

    if conversation is None:
        raise NotFoundError(
            f"Conversation {conversation_id} not found"
        )

    return conversation


def _get_participant(
    participant_id: int,
    *,
    clinic_id: int | None = None,
    conversation_id: int | None = None,
    lock: bool = False,
) -> ConversationParticipant:
    _validate_positive_id(
        participant_id,
        "Participant ID",
    )

    statement = db.select(
        ConversationParticipant
    ).where(
        ConversationParticipant.id == participant_id,
    )

    if clinic_id is not None:
        statement = statement.where(
            ConversationParticipant.clinic_id == clinic_id,
        )

    if conversation_id is not None:
        statement = statement.where(
            ConversationParticipant.conversation_id
            == conversation_id,
        )

    if lock:
        statement = statement.with_for_update()

    participant = db.session.execute(
        statement,
    ).scalar_one_or_none()

    if participant is None:
        raise NotFoundError(
            f"Conversation participant "
            f"{participant_id} not found"
        )

    return participant


def _get_active_staff_for_user(
    user_id: int,
    clinic_id: int,
) -> Staff | None:
    return db.session.execute(
        db.select(Staff).where(
            Staff.user_id == user_id,
            Staff.clinic_id == clinic_id,
            Staff.status == StaffStatus.ACTIVE,
        ),
    ).scalar_one_or_none()


def _staff_has_patient_relationship(
    *,
    staff_id: int,
    clinic_id: int,
    patient_id: int,
) -> bool:
    appointment_exists = db.session.scalar(
        db.select(Appointment.id)
        .where(
            Appointment.clinic_id == clinic_id,
            Appointment.patient_id == patient_id,
            Appointment.staff_id == staff_id,
            Appointment.status
            != AppointmentStatus.CANCELLED,
        )
        .limit(1)
    )

    if appointment_exists is not None:
        return True

    consultation_exists = db.session.scalar(
        db.select(Consultation.id)
        .where(
            Consultation.clinic_id == clinic_id,
            Consultation.patient_id == patient_id,
            Consultation.staff_id == staff_id,
            Consultation.status
            != ConsultationStatus.CANCELLED,
        )
        .limit(1)
    )

    return consultation_exists is not None


def _validate_staff_patient_relationship(
    *,
    user_id: int,
    clinic_id: int,
    patient_id: int,
) -> None:
    staff = _get_active_staff_for_user(
        user_id,
        clinic_id,
    )

    if staff is None:
        return

    if not _staff_has_patient_relationship(
        staff_id=staff.id,
        clinic_id=clinic_id,
        patient_id=patient_id,
    ):
        raise ValidationError(
            "Staff member is not authorized to "
            "access this patient conversation"
        )


def _validate_context_links(
    clinic_id: int,
    patient_id: int | None = None,
    appointment_id: int | None = None,
    consultation_id: int | None = None,
):
    patient = None
    appointment = None
    consultation = None

    if patient_id is not None:
        patient = _get_patient(
            patient_id,
            clinic_id,
            active_only=True,
        )

    if appointment_id is not None:
        appointment = _get_appointment(
            appointment_id,
            clinic_id,
        )

    if consultation_id is not None:
        consultation = _get_consultation(
            consultation_id,
            clinic_id,
        )

    if (
        appointment is not None
        and patient is not None
        and appointment.patient_id != patient.id
    ):
        raise ValidationError(
            "Appointment does not belong to "
            "the selected patient"
        )

    if (
        consultation is not None
        and patient is not None
        and consultation.patient_id != patient.id
    ):
        raise ValidationError(
            "Consultation does not belong to "
            "the selected patient"
        )

    if (
        appointment is not None
        and consultation is not None
        and consultation.appointment_id is not None
        and consultation.appointment_id != appointment.id
    ):
        raise ValidationError(
            "Appointment and consultation do not match"
        )

    return patient, appointment, consultation


def _active_participant_count(
    conversation_id: int,
) -> int:
    return db.session.execute(
        db.select(
            func.count(
                ConversationParticipant.id
            )
        ).where(
            ConversationParticipant.conversation_id
            == conversation_id,
            ConversationParticipant.status.in_(
                (
                    ParticipantStatus.PENDING,
                    ParticipantStatus.ACCEPTED,
                )
            ),
        )
    ).scalar_one()


def _active_admin_count(
    conversation_id: int,
) -> int:
    return db.session.execute(
        db.select(
            func.count(
                ConversationParticipant.id
            )
        ).where(
            ConversationParticipant.conversation_id
            == conversation_id,
            ConversationParticipant.role
            == ParticipantRole.ADMIN,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )
    ).scalar_one()


def _build_direct_key(
    clinic_id: int,
    user_ids: list[int],
) -> str:
    normalized_ids = sorted(
        set(user_ids)
    )

    if len(normalized_ids) != 2:
        raise ValidationError(
            "A direct conversation requires "
            "exactly two users"
        )

    raw_key = (
        f"{clinic_id}:"
        f"{normalized_ids[0]}:"
        f"{normalized_ids[1]}"
    )

    return hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()


def _get_active_direct_conversation(
    clinic_id: int,
    direct_key: str,
    *,
    lock: bool = False,
) -> Conversation | None:
    statement = db.select(
        Conversation
    ).where(
        Conversation.clinic_id == clinic_id,
        Conversation.conversation_type
        == ConversationType.DIRECT,
        Conversation.status
        == ConversationStatus.ACTIVE,
        Conversation.direct_key == direct_key,
    )

    if lock:
        statement = statement.with_for_update()

    return db.session.execute(
        statement,
    ).scalar_one_or_none()


def _validate_participant_status_transition(
    current_status: ParticipantStatus,
    new_status: ParticipantStatus,
) -> None:
    if current_status == new_status:
        return

    allowed = {
        ParticipantStatus.PENDING: {
            ParticipantStatus.ACCEPTED,
            ParticipantStatus.DECLINED,
        },
        ParticipantStatus.ACCEPTED: {
            ParticipantStatus.LEFT,
            ParticipantStatus.REMOVED,
        },
        ParticipantStatus.DECLINED: {
            ParticipantStatus.PENDING,
        },
        ParticipantStatus.LEFT: {
            ParticipantStatus.PENDING,
        },
        ParticipantStatus.REMOVED: {
            ParticipantStatus.PENDING,
        },
    }

    if new_status not in allowed.get(
        current_status,
        set(),
    ):
        raise ConflictError(
            f"Participant cannot transition from "
            f"'{current_status.value}' to "
            f"'{new_status.value}'"
        )


def _validate_read_message(
    conversation: Conversation,
    message_id: int,
) -> Message:
    _validate_positive_id(
        message_id,
        "Last read message ID",
    )

    message = db.session.execute(
        db.select(Message).where(
            Message.id == message_id,
            Message.clinic_id
            == conversation.clinic_id,
            Message.conversation_id
            == conversation.id,
        ),
    ).scalar_one_or_none()

    if message is None:
        raise NotFoundError(
            f"Message {message_id} not found"
        )

    return message


@transactional
def create_conversation(
    clinic_id: int,
    created_by_id: int,
    conversation_type: ConversationType = ConversationType.DIRECT,
    title: str | None = None,
    description: str | None = None,
    patient_id: int | None = None,
    appointment_id: int | None = None,
    consultation_id: int | None = None,
    participant_user_ids: list[int] | None = None,
) -> Conversation:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        created_by_id,
        "Creator user ID",
    )

    ensure_clinic_active(clinic_id)

    ChatPolicyService.ensure_chat_enabled(
        clinic_id,
    )

    ChatPolicyService.ensure_conversation_creation_allowed(
        clinic_id,
    )

    conversation_type = _normalize_enum(
        conversation_type,
        ConversationType,
        "conversation type",
    )

    title = _normalize_optional_text(
        title,
        "Conversation title",
        200,
    )

    description = _normalize_optional_text(
        description,
        "Conversation description",
        1000,
    )

    creator = _get_user(
        created_by_id,
        clinic_id=clinic_id,
        active_only=True,
    )

    participant_user_ids = list(
        participant_user_ids or []
    )

    if len(participant_user_ids) > MAX_INITIAL_PARTICIPANTS:
        raise ValidationError(
            "Too many initial participants"
        )

    if len(participant_user_ids) != len(
        set(participant_user_ids)
    ):
        raise ValidationError(
            "Duplicate participant user IDs are not allowed"
        )

    for user_id in participant_user_ids:
        _validate_positive_id(
            user_id,
            "Participant user ID",
        )

    participant_ids = set(
        participant_user_ids
    )
    participant_ids.add(creator.id)

    _validate_context_links(
        clinic_id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        consultation_id=consultation_id,
    )

    if patient_id is not None:
        _validate_staff_patient_relationship(
            user_id=creator.id,
            clinic_id=clinic_id,
            patient_id=patient_id,
        )

    if conversation_type == ConversationType.DIRECT:
        ChatPolicyService.ensure_direct_conversation_allowed(
            clinic_id,
        )

        if len(participant_ids) != 2:
            raise ValidationError(
                "A direct conversation requires "
                "exactly two participants"
            )

        direct_key = _build_direct_key(
            clinic_id,
            list(participant_ids),
        )

        existing = _get_active_direct_conversation(
            clinic_id,
            direct_key,
        )

        if existing is not None:
            return existing

    else:
        direct_key = None

        ChatPolicyService.ensure_group_conversation_allowed(
            clinic_id,
            len(participant_ids),
        )

    for user_id in participant_ids:
        _get_user(
            user_id,
            clinic_id=clinic_id,
            active_only=True,
        )

    conversation = Conversation(
        clinic_id=clinic_id,
        conversation_type=conversation_type,
        status=ConversationStatus.ACTIVE,
        direct_key=direct_key,
        title=title,
        description=description,
        created_by_id=creator.id,
        patient_id=patient_id,
        appointment_id=appointment_id,
        consultation_id=consultation_id,
    )

    db.session.add(conversation)
    db.session.flush()

    for user_id in sorted(participant_ids):
        participant = ConversationParticipant(
            clinic_id=clinic_id,
            conversation_id=conversation.id,
            user_id=user_id,
            role=(
                ParticipantRole.ADMIN
                if user_id == creator.id
                else ParticipantRole.MEMBER
            ),
            status=(
                ParticipantStatus.ACCEPTED
                if user_id == creator.id
                else ParticipantStatus.PENDING
            ),
            joined_at=(
                _utcnow()
                if user_id == creator.id
                else None
            ),
        )

        db.session.add(participant)

    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="Conversation",
        entity_id=conversation.id,
        clinic_id=clinic_id,
        description=(
            f"Conversation {conversation.id} created"
        ),
        new_value={
            "conversation_type":
                conversation.conversation_type.value,
            "created_by_id":
                conversation.created_by_id,
            "participant_count":
                len(participant_ids),
            "patient_id":
                conversation.patient_id,
            "appointment_id":
                conversation.appointment_id,
            "consultation_id":
                conversation.consultation_id,
        },
    )

    return conversation


def get_conversation(
    conversation_id: int,
    clinic_id: int,
    user_id: int | None = None,
) -> Conversation:
    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
    )

    if user_id is not None:
        ChatSecurityService.ensure_user_can_access_conversation(
            user_id,
            conversation.id,
        )

    return conversation


def list_conversations(
    clinic_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
    status: ConversationStatus | None = None,
    conversation_type: ConversationType | None = None,
    patient_id: int | None = None,
    search: str | None = None,
    user_id: int | None = None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    if user_id is not None:
        user = db.session.get(
            User,
            user_id,
        )

        if (
            user is None
            or user.clinic_id != clinic_id
            or not user.is_active
        ):
            raise NotFoundError(
                f"User {user_id} not found"
            )

    status = (
        _normalize_enum(
            status,
            ConversationStatus,
            "conversation status",
        )
        if status is not None
        else None
    )

    conversation_type = (
        _normalize_enum(
            conversation_type,
            ConversationType,
            "conversation type",
        )
        if conversation_type is not None
        else None
    )

    if patient_id is not None:
        _validate_positive_id(
            patient_id,
            "Patient ID",
        )

    search = _normalize_optional_text(
        search,
        "Conversation search",
        200,
    )

    query = db.select(
        Conversation
    ).where(
        Conversation.clinic_id == clinic_id,
    )

    count_query = db.select(
        func.count(Conversation.id)
    ).where(
        Conversation.clinic_id == clinic_id,
    )

    if user_id is not None:
        query = query.join(
            ConversationParticipant,
            ConversationParticipant.conversation_id
            == Conversation.id,
        ).where(
            ConversationParticipant.user_id == user_id,
            ConversationParticipant.clinic_id == clinic_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )

        count_query = count_query.join(
            ConversationParticipant,
            ConversationParticipant.conversation_id
            == Conversation.id,
        ).where(
            ConversationParticipant.user_id == user_id,
            ConversationParticipant.clinic_id == clinic_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )

    if status is not None:
        query = query.where(
            Conversation.status == status,
        )
        count_query = count_query.where(
            Conversation.status == status,
        )

    if conversation_type is not None:
        query = query.where(
            Conversation.conversation_type
            == conversation_type,
        )
        count_query = count_query.where(
            Conversation.conversation_type
            == conversation_type,
        )

    if patient_id is not None:
        query = query.where(
            Conversation.patient_id == patient_id,
        )
        count_query = count_query.where(
            Conversation.patient_id == patient_id,
        )

    if search is not None:
        like = f"%{search}%"

        condition = db.or_(
            Conversation.title.ilike(like),
            Conversation.description.ilike(like),
        )

        query = query.where(condition)
        count_query = count_query.where(condition)

    if user_id is not None:
        query = query.distinct()

    total = db.session.execute(
        count_query,
    ).scalar_one()

    offset = (page - 1) * per_page

    items = db.session.execute(
        query
        .order_by(
            Conversation.updated_at.desc(),
            Conversation.id.desc(),
        )
        .offset(offset)
        .limit(per_page),
    ).scalars().unique().all()

    pages = (
        (total + per_page - 1) // per_page
        if total
        else 0
    )

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": page > 1 and total > 0,
    }


@transactional
def update_conversation(
    conversation_id: int,
    clinic_id: int,
    **fields,
) -> Conversation:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    ensure_clinic_active(clinic_id)

    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    allowed_fields = {
        "title",
        "description",
    }

    unknown_fields = (
        set(fields) - allowed_fields
    )

    if unknown_fields:
        raise ValidationError(
            "Unknown conversation field(s): "
            + ", ".join(
                sorted(unknown_fields)
            )
        )

    old_value = {}
    new_value = {}

    if "title" in fields:
        title = _normalize_optional_text(
            fields["title"],
            "Conversation title",
            200,
        )

        if title != conversation.title:
            old_value["title"] = conversation.title
            new_value["title"] = title
            conversation.title = title

    if "description" in fields:
        description = _normalize_optional_text(
            fields["description"],
            "Conversation description",
            1000,
        )

        if description != conversation.description:
            old_value["description"] = (
                conversation.description
            )
            new_value["description"] = description
            conversation.description = description

    if new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="Conversation",
            entity_id=conversation.id,
            clinic_id=clinic_id,
            description=(
                f"Conversation {conversation.id} updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return conversation


@transactional
def update_conversation_status(
    conversation_id: int,
    clinic_id: int,
    new_status: ConversationStatus,
) -> Conversation:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    ensure_clinic_active(clinic_id)

    new_status = _normalize_enum(
        new_status,
        ConversationStatus,
        "conversation status",
    )

    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    current_status = conversation.status

    if current_status == new_status:
        return conversation

    allowed_transitions = {
        ConversationStatus.ACTIVE: {
            ConversationStatus.ARCHIVED,
            ConversationStatus.CLOSED,
        },
        ConversationStatus.ARCHIVED: {
            ConversationStatus.ACTIVE,
            ConversationStatus.CLOSED,
        },
        ConversationStatus.CLOSED: set(),
    }

    if new_status not in allowed_transitions.get(
        current_status,
        set(),
    ):
        raise ConflictError(
            f"Conversation {conversation.id} cannot transition "
            f"from '{current_status.value}' to "
            f"'{new_status.value}'"
        )

    now = _utcnow()

    conversation.status = new_status

    if new_status == ConversationStatus.ARCHIVED:
        conversation.archived_at = now
        conversation.closed_at = None

    elif new_status == ConversationStatus.CLOSED:
        conversation.closed_at = now

    elif new_status == ConversationStatus.ACTIVE:
        conversation.archived_at = None
        conversation.closed_at = None

    create_audit_log(
        action=AuditAction.STATUS_CHANGE,
        entity_type="Conversation",
        entity_id=conversation.id,
        clinic_id=clinic_id,
        description=(
            f"Conversation status changed to "
            f"'{new_status.value}'"
        ),
        old_value={
            "status": current_status.value,
        },
        new_value={
            "status": new_status.value,
        },
    )

    return conversation


@transactional
def add_participant(
    conversation_id: int,
    clinic_id: int,
    actor_id: int,
    user_id: int,
    role: ParticipantRole = ParticipantRole.MEMBER,
) -> ConversationParticipant:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        actor_id,
        "Actor user ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    ensure_clinic_active(
        clinic_id,
    )

    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    ChatSecurityService.ensure_user_can_access_conversation(
        actor_id,
        conversation.id,
    )

    actor_participant = db.session.execute(
        db.select(
            ConversationParticipant
        ).where(
            ConversationParticipant.conversation_id
            == conversation.id,
            ConversationParticipant.clinic_id
            == clinic_id,
            ConversationParticipant.user_id
            == actor_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )
    ).scalar_one_or_none()

    if actor_participant is None:
        raise NotFoundError(
            "Accepted actor conversation participant not found"
        )

    if actor_participant.role != ParticipantRole.ADMIN:
        raise ValidationError(
            "Only conversation administrators can add participants"
        )

    if conversation.status == ConversationStatus.CLOSED:
        raise ConflictError(
            "Cannot add participants to a closed conversation"
        )

    role = _normalize_enum(
        role,
        ParticipantRole,
        "participant role",
    )

    user = _get_user(
        user_id,
        clinic_id=clinic_id,
        active_only=True,
    )

    if conversation.patient_id is not None:
        _validate_staff_patient_relationship(
            user_id=user.id,
            clinic_id=clinic_id,
            patient_id=conversation.patient_id,
        )

    existing = db.session.execute(
        db.select(
            ConversationParticipant
        )
        .where(
            ConversationParticipant.conversation_id
            == conversation.id,
            ConversationParticipant.user_id
            == user.id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if existing is not None:
        if existing.status in (
            ParticipantStatus.PENDING,
            ParticipantStatus.ACCEPTED,
        ):
            raise ConflictError(
                f"User {user.id} is already a participant"
            )

        existing.status = ParticipantStatus.PENDING
        existing.role = role
        existing.left_at = None
        existing.removed_at = None

        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="ConversationParticipant",
            entity_id=existing.id,
            clinic_id=clinic_id,
            description=(
                f"User {user.id} re-added to "
                f"conversation {conversation.id}"
            ),
            new_value={
                "status":
                    ParticipantStatus.PENDING.value,
                "role": role.value,
                "actor_id": actor_id,
            },
        )

        return existing

    active_count = _active_participant_count(
        conversation.id,
    )

    if conversation.conversation_type == (
        ConversationType.DIRECT
    ):
        raise ConflictError(
            "A direct conversation cannot have "
            "more than two participants"
        )

    ChatPolicyService.ensure_group_conversation_allowed(
        clinic_id,
        active_count + 1,
    )

    participant = ConversationParticipant(
        clinic_id=clinic_id,
        conversation_id=conversation.id,
        user_id=user.id,
        role=role,
        status=ParticipantStatus.PENDING,
    )

    db.session.add(
        participant,
    )

    db.session.flush()

    create_audit_log(
        action=AuditAction.CREATE,
        entity_type="ConversationParticipant",
        entity_id=participant.id,
        clinic_id=clinic_id,
        description=(
            f"User {user.id} added to "
            f"conversation {conversation.id}"
        ),
        new_value={
            "conversation_id": conversation.id,
            "user_id": user.id,
            "role": role.value,
            "status":
                ParticipantStatus.PENDING.value,
            "actor_id": actor_id,
        },
    )

    return participant


def list_participants(
    conversation_id: int,
    clinic_id: int,
    page: int = DEFAULT_PAGE,
    per_page: int = DEFAULT_PER_PAGE,
    status: ParticipantStatus | None = None,
):
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    page, per_page = _validate_pagination(
        page,
        per_page,
    )

    _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
    )

    status = (
        _normalize_enum(
            status,
            ParticipantStatus,
            "participant status",
        )
        if status is not None
        else None
    )

    query = db.select(
        ConversationParticipant
    ).where(
        ConversationParticipant.conversation_id
        == conversation_id,
        ConversationParticipant.clinic_id
        == clinic_id,
    )

    count_query = db.select(
        func.count(
            ConversationParticipant.id
        )
    ).where(
        ConversationParticipant.conversation_id
        == conversation_id,
        ConversationParticipant.clinic_id
        == clinic_id,
    )

    if status is not None:
        query = query.where(
            ConversationParticipant.status == status,
        )
        count_query = count_query.where(
            ConversationParticipant.status == status,
        )

    total = db.session.execute(
        count_query,
    ).scalar_one()

    offset = (page - 1) * per_page

    items = db.session.execute(
        query
        .order_by(
            ConversationParticipant.created_at.asc(),
            ConversationParticipant.id.asc(),
        )
        .offset(offset)
        .limit(per_page),
    ).scalars().all()

    pages = (
        (total + per_page - 1) // per_page
        if total
        else 0
    )

    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": (
            page > 1 and total > 0
        ),
    }


@transactional
def update_participant(
    participant_id: int,
    clinic_id: int,
    *,
    role: ParticipantRole | None = None,
    status: ParticipantStatus | None = None,
) -> ConversationParticipant:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    ensure_clinic_active(clinic_id)

    participant = _get_participant(
        participant_id,
        clinic_id=clinic_id,
        lock=True,
    )

    conversation = _get_conversation(
        participant.conversation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    if conversation.status == ConversationStatus.CLOSED:
        raise ConflictError(
            "Cannot modify participants "
            "in a closed conversation"
        )

    old_value = {}
    new_value = {}

    if role is not None:
        role = _normalize_enum(
            role,
            ParticipantRole,
            "participant role",
        )

        if role != participant.role:
            old_value["role"] = participant.role.value
            new_value["role"] = role.value
            participant.role = role

    if status is not None:
        status = _normalize_enum(
            status,
            ParticipantStatus,
            "participant status",
        )

        _validate_participant_status_transition(
            participant.status,
            status,
        )

        if status == ParticipantStatus.LEFT:
            if participant.status == (
                ParticipantStatus.ACCEPTED
            ):
                if participant.role == ParticipantRole.ADMIN:
                    admin_count = _active_admin_count(
                        conversation.id,
                    )

                    if admin_count <= 1:
                        raise ConflictError(
                            "The last active conversation "
                            "administrator cannot leave"
                        )

        old_value["status"] = participant.status.value
        new_value["status"] = status.value

        participant.status = status

        now = _utcnow()

        if status == ParticipantStatus.ACCEPTED:
            if participant.joined_at is None:
                participant.joined_at = now

            participant.left_at = None
            participant.removed_at = None

        elif status == ParticipantStatus.LEFT:
            participant.left_at = now
            participant.removed_at = None

        elif status == ParticipantStatus.REMOVED:
            participant.removed_at = now
            participant.left_at = None

        elif status == ParticipantStatus.PENDING:
            participant.joined_at = None
            participant.left_at = None
            participant.removed_at = None

        elif status == ParticipantStatus.DECLINED:
            participant.left_at = None
            participant.removed_at = None

    if new_value:
        create_audit_log(
            action=AuditAction.UPDATE,
            entity_type="ConversationParticipant",
            entity_id=participant.id,
            clinic_id=clinic_id,
            description=(
                f"Conversation participant "
                f"{participant.id} updated"
            ),
            old_value=old_value,
            new_value=new_value,
        )

    return participant


@transactional
def leave_conversation(
    conversation_id: int,
    clinic_id: int,
    user_id: int,
) -> ConversationParticipant:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    ensure_clinic_active(clinic_id)

    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
        lock=True,
    )

    ChatSecurityService.ensure_user_can_access_conversation(
        user_id,
        conversation.id,
    )

    participant = db.session.execute(
        db.select(
            ConversationParticipant
        )
        .where(
            ConversationParticipant.conversation_id
            == conversation.id,
            ConversationParticipant.user_id
            == user_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if participant is None:
        raise NotFoundError(
            "Accepted conversation participant not found"
        )

    if participant.role == ParticipantRole.ADMIN:
        admin_count = _active_admin_count(
            conversation.id,
        )

        if admin_count <= 1:
            raise ConflictError(
                "The last active conversation "
                "administrator cannot leave"
            )

    participant.status = ParticipantStatus.LEFT
    participant.left_at = _utcnow()

    create_audit_log(
        action=AuditAction.UPDATE,
        entity_type="ConversationParticipant",
        entity_id=participant.id,
        clinic_id=clinic_id,
        description=(
            f"User {user_id} left "
            f"conversation {conversation.id}"
        ),
        old_value={
            "status":
                ParticipantStatus.ACCEPTED.value,
        },
        new_value={
            "status":
                ParticipantStatus.LEFT.value,
        },
    )

    return participant


@transactional
def update_participant_read_state(
    conversation_id: int,
    clinic_id: int,
    user_id: int,
    last_read_message_id: int,
) -> ConversationParticipant:
    _validate_positive_id(
        clinic_id,
        "Clinic ID",
    )

    _validate_positive_id(
        user_id,
        "User ID",
    )

    conversation = _get_conversation(
        conversation_id,
        clinic_id=clinic_id,
    )

    ChatSecurityService.ensure_user_can_access_conversation(
        user_id,
        conversation.id,
    )

    _validate_read_message(
        conversation,
        last_read_message_id,
    )

    participant = db.session.execute(
        db.select(
            ConversationParticipant
        )
        .where(
            ConversationParticipant.conversation_id
            == conversation.id,
            ConversationParticipant.clinic_id
            == clinic_id,
            ConversationParticipant.user_id
            == user_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if participant is None:
        raise NotFoundError(
            "Accepted conversation participant not found"
        )

    participant.last_read_message_id = (
        last_read_message_id
    )

    participant.updated_at = _utcnow()

    return participant