from __future__ import annotations

from datetime import datetime

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.enums.chat_enums import (
    AttachmentType,
    MessagePriority,
    MessageStatus,
    MessageType,
)


# ---------------------------------------------------------------------------
# Shared / Pagination
# ---------------------------------------------------------------------------

class MessagePaginationQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    per_page: int = Field(default=50, ge=1, le=500)

    status: MessageStatus | None = None
    message_type: MessageType | None = None
    priority: MessagePriority | None = None

    sender_id: int | None = Field(default=None, ge=1)
    reply_to_message_id: int | None = Field(default=None, ge=1)

    search: str | None = Field(
        default=None,
        max_length=200,
    )

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
    )

    @field_validator("search")
    @classmethod
    def normalize_search(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


# ---------------------------------------------------------------------------
# Message Schemas
# ---------------------------------------------------------------------------

class MessageCreate(BaseModel):
    message_type: MessageType = MessageType.TEXT

    content: str | None = Field(
        default=None,
        max_length=10_000,
    )

    reply_to_message_id: int | None = Field(
        default=None,
        ge=1,
    )

    priority: MessagePriority = MessagePriority.NORMAL

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
    )

    @field_validator("content")
    @classmethod
    def normalize_content(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


class MessageUpdate(BaseModel):
    content: str | None = Field(
        default=None,
        max_length=10_000,
    )

    model_config = ConfigDict(
        extra="forbid",
    )

    @field_validator("content")
    @classmethod
    def normalize_content(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None


class MessageStatusUpdate(BaseModel):
    status: MessageStatus

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
    )


class MessagePriorityUpdate(BaseModel):
    priority: MessagePriority

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
    )


# ---------------------------------------------------------------------------
# Message Response
# ---------------------------------------------------------------------------

class MessageAttachmentResponse(BaseModel):
    id: int
    attachment_type: AttachmentType
    file_name: str | None
    mime_type: str
    file_size_bytes: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class MessageResponse(BaseModel):
    attachments: list[MessageAttachmentResponse] = Field(default_factory=list)
    id: int
    clinic_id: int
    conversation_id: int
    sender_id: int

    message_type: MessageType
    content: str | None

    reply_to_message_id: int | None

    status: MessageStatus
    priority: MessagePriority

    created_at: datetime
    updated_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None

    model_config = ConfigDict(
        from_attributes=True,
        use_enum_values=True,
    )


class MessageListResponse(BaseModel):
    data: list[MessageResponse]

    page: int
    per_page: int
    total: int
    pages: int
    has_next: bool
    has_previous: bool


# ---------------------------------------------------------------------------
# Thread / Reply Schemas
# ---------------------------------------------------------------------------

class MessageReplyCreate(BaseModel):
    content: str | None = Field(
        default=None,
        max_length=10_000,
    )

    message_type: MessageType = MessageType.TEXT

    priority: MessagePriority = MessagePriority.NORMAL

    model_config = ConfigDict(
        extra="forbid",
        use_enum_values=True,
    )

    @field_validator("content")
    @classmethod
    def normalize_content(
        cls,
        value: str | None,
    ) -> str | None:
        if value is None:
            return None

        value = value.strip()
        return value or None