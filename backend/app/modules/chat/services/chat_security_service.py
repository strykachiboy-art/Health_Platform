from __future__ import annotations

from sqlalchemy import and_

from app.extensions import db
from app.core.exceptions import NotFoundError, ValidationError
from app.core.auth.user.models.user_model import User
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import (
    ConversationParticipant,
)
from app.modules.chat.models.message_model import Message
from app.core.enums.chat_enums import ParticipantStatus
from app.modules.chat.services.case_chat_access_guard import (
    ensure_case_chat_user_allowed,
    filter_case_chat_recipient_user_ids,
)


class ChatSecurityService:
    @staticmethod
    def _validate_id(
        value: int,
        field_name: str,
    ) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
        ):
            raise ValidationError(
                f"{field_name} must be a positive integer"
            )

        return value

    @classmethod
    def get_active_user(
        cls,
        user_id: int,
    ) -> User:
        user_id = cls._validate_id(
            user_id,
            "User ID",
        )

        user = db.session.get(
            User,
            user_id,
        )

        if user is None or not user.is_active:
            raise NotFoundError(
                "User not found"
            )

        return user

    @classmethod
    def ensure_same_clinic(
        cls,
        user: User,
        clinic_id: int,
    ) -> None:
        clinic_id = cls._validate_id(
            clinic_id,
            "Clinic ID",
        )

        if user.clinic_id != clinic_id:
            raise NotFoundError(
                "Resource not found"
            )

        if user.staff is not None:
            if user.staff.user_id != user.id:
                raise NotFoundError(
                    "Resource not found"
                )

            if user.staff.clinic_id != clinic_id:
                raise NotFoundError(
                    "Resource not found"
                )

    @classmethod
    def ensure_staff_clinic_consistency(
        cls,
        user: User,
    ) -> None:
        if user.staff is None:
            return

        if user.staff.user_id != user.id:
            raise NotFoundError(
                "User not found"
            )

        if user.clinic_id != user.staff.clinic_id:
            raise NotFoundError(
                "User not found"
            )

    @classmethod
    def get_conversation(
        cls,
        conversation_id: int,
    ) -> Conversation:
        conversation_id = cls._validate_id(
            conversation_id,
            "Conversation ID",
        )

        conversation = db.session.get(
            Conversation,
            conversation_id,
        )

        if conversation is None:
            raise NotFoundError(
                "Conversation not found"
            )

        return conversation

    @classmethod
    def get_participant(
        cls,
        conversation_id: int,
        user_id: int,
    ) -> ConversationParticipant:
        conversation_id = cls._validate_id(
            conversation_id,
            "Conversation ID",
        )

        user_id = cls._validate_id(
            user_id,
            "User ID",
        )

        participant = db.session.execute(
            db.select(ConversationParticipant)
            .where(
                and_(
                    ConversationParticipant.conversation_id
                    == conversation_id,
                    ConversationParticipant.user_id
                    == user_id,
                )
            )
            .limit(1)
        ).scalar_one_or_none()

        if participant is None:
            raise NotFoundError(
                "Conversation participant not found"
            )

        return participant

    @classmethod
    def ensure_participant_belongs_to_conversation(
        cls,
        participant: ConversationParticipant,
        conversation: Conversation,
    ) -> None:
        if (
            participant.conversation_id
            != conversation.id
        ):
            raise NotFoundError(
                "Conversation participant not found"
            )

        if (
            participant.clinic_id
            != conversation.clinic_id
        ):
            raise NotFoundError(
                "Conversation participant not found"
            )

    @classmethod
    def ensure_user_is_participant(
        cls,
        user_id: int,
        conversation: Conversation,
    ) -> ConversationParticipant:
        user = cls.get_active_user(
            user_id
        )

        cls.ensure_staff_clinic_consistency(
            user
        )

        cls.ensure_same_clinic(
            user,
            conversation.clinic_id,
        )

        participant = cls.get_participant(
            conversation.id,
            user.id,
        )

        cls.ensure_participant_belongs_to_conversation(
            participant,
            conversation,
        )

        if participant.clinic_id != user.clinic_id:
            raise NotFoundError(
                "Conversation participant not found"
            )

        status_value = getattr(
            participant.status,
            "value",
            participant.status,
        )

        if status_value != "accepted":
            raise NotFoundError(
                "Conversation participant not found"
            )

        ensure_case_chat_user_allowed(
            clinic_id=conversation.clinic_id,
            conversation_id=conversation.id,
            user_id=user.id,
        )

        return participant

    @classmethod
    def ensure_user_can_access_conversation(
        cls,
        user_id: int,
        conversation_id: int,
    ) -> Conversation:
        user = cls.get_active_user(
            user_id
        )

        cls.ensure_staff_clinic_consistency(
            user
        )

        conversation = cls.get_conversation(
            conversation_id
        )

        cls.ensure_same_clinic(
            user,
            conversation.clinic_id,
        )

        cls.ensure_user_is_participant(
            user.id,
            conversation,
        )

        return conversation
    
    @classmethod
    def get_active_conversation_recipient_user_ids(
      cls,
      clinic_id: int,
      conversation_id: int,
    ) -> list[int]:
      clinic_id = cls._validate_id(
        clinic_id,
        "Clinic ID",
    )

      conversation_id = cls._validate_id(
        conversation_id,
        "Conversation ID",
    )

      statement = (
         db.select(
            ConversationParticipant.user_id,
         )
         .join(
            User,
            User.id
            == ConversationParticipant.user_id,
         )
         .where(
            ConversationParticipant.clinic_id
            == clinic_id,
            ConversationParticipant.conversation_id
            == conversation_id,
            ConversationParticipant.status
            == ParticipantStatus.ACCEPTED,
            User.clinic_id
            == clinic_id,
            User.is_active.is_(True),
        )
         .order_by(
            ConversationParticipant.user_id.asc(),
        )
     )

      user_ids = list(
        db.session.execute(
            statement,
        ).scalars()
      )
      return filter_case_chat_recipient_user_ids(
          clinic_id=clinic_id,
          conversation_id=conversation_id,
          user_ids=user_ids,
      )

    @classmethod
    def get_message(
        cls,
        message_id: int,
    ) -> Message:
        message_id = cls._validate_id(
            message_id,
            "Message ID",
        )

        message = db.session.get(
            Message,
            message_id,
        )

        if message is None:
            raise NotFoundError(
                "Message not found"
            )

        return message

    @classmethod
    def ensure_message_integrity(
        cls,
        message: Message,
    ) -> Conversation:
        conversation = db.session.get(
            Conversation,
            message.conversation_id,
        )

        if conversation is None:
            raise NotFoundError(
                "Message not found"
            )

        if conversation.clinic_id != message.clinic_id:
            raise NotFoundError(
                "Message not found"
            )

        return conversation

    @classmethod
    def ensure_user_can_access_message(
        cls,
        user_id: int,
        message_id: int,
    ) -> Message:
        user = cls.get_active_user(
            user_id
        )

        cls.ensure_staff_clinic_consistency(
            user
        )

        message = cls.get_message(
            message_id
        )

        conversation = cls.ensure_message_integrity(
            message
        )

        cls.ensure_same_clinic(
            user,
            message.clinic_id,
        )

        cls.ensure_user_is_participant(
            user.id,
            conversation,
        )

        return message

    @classmethod
    def ensure_user_can_send_message(
        cls,
        user_id: int,
        conversation_id: int,
    ) -> Conversation:
        return cls.ensure_user_can_access_conversation(
            user_id,
            conversation_id,
        )

    @classmethod
    def ensure_user_owns_message(
        cls,
        user_id: int,
        message: Message,
    ) -> None:
        user_id = cls._validate_id(
            user_id,
            "User ID",
        )

        if message.sender_id != user_id:
            raise ValidationError(
                "You can only modify your own message"
            )

    @classmethod
    def ensure_user_can_edit_message(
        cls,
        user_id: int,
        message_id: int,
    ) -> Message:
        message = cls.ensure_user_can_access_message(
            user_id,
            message_id,
        )

        cls.ensure_user_owns_message(
            user_id,
            message,
        )

        return message

    @classmethod
    def ensure_user_can_delete_message(
        cls,
        user_id: int,
        message_id: int,
    ) -> Message:
        message = cls.ensure_user_can_access_message(
            user_id,
            message_id,
        )

        cls.ensure_user_owns_message(
            user_id,
            message,
        )

        return message

    @classmethod
    def ensure_user_can_access_attachment(
        cls,
        user_id: int,
        message_id: int,
    ) -> Message:
        return cls.ensure_user_can_access_message(
            user_id,
            message_id,
        )

    @classmethod
    def ensure_user_can_access_mention(
        cls,
        user_id: int,
        message_id: int,
    ) -> Message:
        return cls.ensure_user_can_access_message(
            user_id,
            message_id,
        )

    @classmethod
    def ensure_user_can_manage_participant(
        cls,
        user_id: int,
        conversation_id: int,
        participant_user_id: int,
    ) -> ConversationParticipant:
        conversation = cls.ensure_user_can_access_conversation(
            user_id,
            conversation_id,
        )

        participant_user_id = cls._validate_id(
            participant_user_id,
            "Participant user ID",
        )

        participant = cls.get_participant(
            conversation.id,
            participant_user_id,
        )

        cls.ensure_participant_belongs_to_conversation(
            participant,
            conversation,
        )

        return participant
