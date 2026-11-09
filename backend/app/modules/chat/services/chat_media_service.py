"""Private CHAT-F1/F2 media transport on the canonical chat message/outbox model.

This module deliberately defaults to OFF. An explicit private storage root,
Fernet key, malware-scanning callback and (for audio) a media-probe callback
are required. Never serve the general/public profile image storage endpoint.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID
import os
import zipfile

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy import select
from werkzeug.datastructures import FileStorage

from app.core.audit.services.audit_service import create_audit_log
from app.core.enums.audit_enums import AuditAction
from app.core.enums.chat_enums import (
    AttachmentType, ConversationStatus, MessagePriority, MessageStatus,
    MessageType,
)
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.extensions import db
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.modules.chat.models.message_model import Message
from app.modules.chat.services.chat_content_validation_service import (
    ChatContentValidationService,
)
from app.modules.chat.services.chat_policy_service import ChatPolicyService
from app.modules.chat.services.chat_media_security_provider import (
    scan_with_clamd, probe_audio_with_ffprobe,
)
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.workers.chat_outbox_worker import create_outbox_event
from app.modules.clinic.services.clinic_service import ensure_clinic_active

_CHUNK = 64 * 1024
_HARD_MAX = 25 * 1024 * 1024
_MIME = {
    "image": {"image/png", "image/jpeg", "image/webp"},
    "audio": {"audio/ogg", "audio/wav", "audio/mp4"},
    "document": {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}
_TYPES = {
    "image": (MessageType.IMAGE, AttachmentType.IMAGE),
    "audio": (MessageType.AUDIO, AttachmentType.AUDIO),
    "document": (MessageType.FILE, AttachmentType.DOCUMENT),
}


def _configured() -> tuple[Path, Fernet, object, object]:
    if current_app.config.get("CHAT_MEDIA_ENABLED") is not True:
        raise ValidationError("Chat media is not enabled")
    raw = current_app.config.get("CHAT_MEDIA_PRIVATE_ROOT")
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise ValidationError("Private chat media storage is not configured")
    root = Path(raw).resolve()
    for key in ("STORAGE_ROOT", "STORAGE_PUBLIC_ROOT"):
        pub = current_app.config.get(key)
        if pub:
            pubpath = Path(pub).resolve()
            if root == pubpath or pubpath in root.parents:
                raise ValidationError("Chat media storage must be private")
    static = current_app.static_folder
    if static:
        staticpath = Path(static).resolve()
        if root == staticpath or staticpath in root.parents:
            raise ValidationError("Chat media storage must not be public")
    raw_key = current_app.config.get("CHAT_MEDIA_FERNET_KEY")
    if not isinstance(raw_key, (str, bytes)):
        raise ValidationError("Chat media encryption is not configured")
    try:
        fernet = Fernet(raw_key.encode("ascii") if isinstance(raw_key, str) else raw_key)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid chat media encryption configuration") from exc
    scanner = current_app.config.get("CHAT_MEDIA_SCAN_CALLBACK")
    if not callable(scanner) and current_app.config.get("CHAT_MEDIA_SCANNER") == "clamd":
        scanner = lambda data, mime: scan_with_clamd(
            data, mime,
            socket_path=current_app.config.get("CHAT_MEDIA_CLAMD_UNIX_SOCKET"),
            host=current_app.config.get("CHAT_MEDIA_CLAMD_HOST"),
            port=current_app.config.get("CHAT_MEDIA_CLAMD_PORT", 3310),
        )
    if not callable(scanner):
        raise ValidationError("Chat media scanner is not configured")
    probe = current_app.config.get("CHAT_MEDIA_AUDIO_PROBE_CALLBACK")
    if not callable(probe) and current_app.config.get("CHAT_MEDIA_AUDIO_PROBE") == "ffprobe":
        probe = lambda data, mime: probe_audio_with_ffprobe(
            data, mime,
            binary=current_app.config.get("CHAT_MEDIA_FFPROBE_BIN", "ffprobe"),
        )
    return root, fernet, scanner, probe


def _sniff(data: bytes, mime: str) -> bool:
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if mime == "image/webp":
        return data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if mime == "application/pdf":
        return data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                entries = z.infolist()
                if len(entries) > 2000 or sum(e.file_size for e in entries) > 80 * 1024 * 1024:
                    return False
                names = {e.filename for e in entries}
                return "[Content_Types].xml" in names and "word/document.xml" in names
        except (zipfile.BadZipFile, ValueError, OSError):
            return False
    if mime == "audio/ogg":
        return data.startswith(b"OggS")
    if mime == "audio/wav":
        return data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if mime == "audio/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    return False


def _read_upload(upload: FileStorage) -> bytes:
    if not isinstance(upload, FileStorage) or not upload.filename:
        raise ValidationError("A named media file is required")
    result = bytearray()
    while True:
        chunk = upload.stream.read(_CHUNK)
        if not chunk:
            break
        result.extend(chunk)
        if len(result) > _HARD_MAX:
            raise ValidationError("Chat media exceeds the absolute size limit")
    if not result:
        raise ValidationError("Chat media cannot be empty")
    return bytes(result)


def _verified_request_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("A valid client request UUID is required") from exc
    if parsed.version != 4 or str(parsed) != value.lower():
        raise ValidationError("A canonical random UUIDv4 is required")
    return str(parsed)


def _storage_path(root: Path, storage_key: str) -> Path:
    if not isinstance(storage_key, str):
        raise ValidationError("Invalid private media key")
    segments = storage_key.split("/")
    if (len(segments) != 2 or not segments[0].isdigit()
            or len(segments[1]) != 68 or not segments[1].endswith(".bin")
            or not all(c in "0123456789abcdef" for c in segments[1][:-4])):
        raise ValidationError("Invalid private media key")
    path = (root / segments[0] / segments[1]).resolve()
    if root not in path.parents:
        raise ValidationError("Invalid private media path")
    return path


def create_media_message(*, clinic_id: int, conversation_id: int,
                         sender_id: int, kind: str, upload: FileStorage,
                         request_id: str, caption: str | None = None) -> tuple[Message, bool]:
    """Create one canonical message+attachment+outbox transaction.

    PostgreSQL conversation row lock serializes same-conversation UUID retries.
    A timeout-after-commit can be reconciled via the same request UUID.
    """
    if kind not in _TYPES:
        raise ValidationError("Unsupported chat media category")
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0
           for i in (clinic_id, conversation_id, sender_id)):
        raise ValidationError("Invalid clinic, conversation or sender")
    request_id = _verified_request_id(request_id)
    root, fernet, scanner, audio_probe = _configured()
    clinic = ensure_clinic_active(clinic_id)
    settings = ChatPolicyService.get_settings(clinic_id)
    ChatPolicyService.ensure_chat_enabled(clinic_id, settings=settings)
    # Deny before reading potentially sensitive, costly bytes.
    authorized = ChatSecurityService.ensure_user_can_send_message(sender_id, conversation_id)
    if authorized.clinic_id != clinic_id or authorized.status != ConversationStatus.ACTIVE:
        raise NotFoundError("Active conversation not found")
    if kind == "audio":
        ChatPolicyService.ensure_voice_messages_allowed(clinic_id)
    mime = (upload.mimetype or "").lower().strip()
    if mime not in _MIME[kind]:
        raise ValidationError("Unsupported chat media MIME type")
    data = _read_upload(upload)
    if not _sniff(data, mime):
        raise ValidationError("Chat media signature does not match the declared type")
    _, attachment_type = _TYPES[kind]
    ChatPolicyService.ensure_attachment_allowed(clinic_id, len(data), attachment_type)
    if kind == "audio":
        if not callable(audio_probe):
            raise ValidationError("Audio validation is not configured")
        seconds = audio_probe(data, mime)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 180:
            raise ValidationError("Voice recording duration is invalid")
    if scanner(data, mime) is not True:
        raise ValidationError("Chat media failed the security scan")
    if caption is not None:
        if not isinstance(caption, str) or len(caption) > 5000:
            raise ValidationError("Invalid media caption")
        caption = caption.strip() or None
    if caption:
        caption = ChatContentValidationService.ensure_text_allowed(clinic_id, caption, settings=settings)
    filename = upload.filename.replace("\\", "/").split("/")[-1].strip()
    if not filename or len(filename) > 180 or any(ord(c) < 32 for c in filename):
        raise ValidationError("Invalid media filename")
    checksum = sha256(data).hexdigest()
    digest = sha256(f"{clinic_id}:{conversation_id}:{sender_id}:{request_id}".encode()).hexdigest()
    storage_key = f"{clinic_id}/{digest}.bin"
    saved_path = None
    try:
        conversation = db.session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.clinic_id == clinic_id
            ).with_for_update()
        ).scalar_one_or_none()
        if conversation is None or conversation.status != ConversationStatus.ACTIVE:
            raise NotFoundError("Active conversation not found")
        # Recheck live assignment after scanning, immediately before mutation.
        ChatSecurityService.ensure_user_can_send_message(sender_id, conversation_id)
        existing = db.session.execute(select(MessageAttachment).where(
            MessageAttachment.clinic_id == clinic_id,
            MessageAttachment.storage_key == storage_key,
        )).scalar_one_or_none()
        if existing is not None:
            if (existing.checksum != checksum or existing.mime_type != mime
                or existing.file_size_bytes != len(data)):
                raise ConflictError("Client request UUID was already used for different media")
            message = db.session.get(Message, existing.message_id)
            if (message is None or message.sender_id != sender_id or
                message.conversation_id != conversation_id):
                raise ConflictError("Client request UUID collision")
            db.session.commit()
            return message, False
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = _storage_path(root, storage_key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        with os.fdopen(os.open(path, flags, 0o600), "wb") as out:
            saved_path = path
            out.write(fernet.encrypt(data))
            out.flush()
            os.fsync(out.fileno())
        message_type, _ = _TYPES[kind]
        message = Message(clinic_id=clinic_id, conversation_id=conversation_id,
                          sender_id=sender_id, message_type=message_type,
                          content=caption, status=MessageStatus.PENDING,
                          priority=MessagePriority.NORMAL)
        db.session.add(message)
        db.session.flush()
        db.session.add(MessageAttachment(
            clinic_id=clinic_id, message_id=message.id,
            attachment_type=attachment_type, file_name=filename,
            mime_type=mime, file_size_bytes=len(data),
            checksum=checksum, storage_key=storage_key,
        ))
        create_outbox_event(
            clinic_id=clinic_id, event_type="message.created",
            payload={"conversation_id": conversation_id, "message_id": message.id,
                     "sender_id": sender_id, "message_type": message_type.value,
                     "priority": MessagePriority.NORMAL.value,
                     "reply_to_message_id": None},
            message_id=message.id, clinic_obj=clinic, message_obj=message,
        )
        now = datetime.now(timezone.utc)
        conversation.last_message_at = now
        conversation.updated_at = now
        create_audit_log(
            action=AuditAction.CREATE, entity_type="Message", entity_id=message.id,
            clinic_id=clinic_id,
            description=f"Chat media message {message.id} created",
            new_value={"conversation_id": conversation_id, "sender_id": sender_id,
                       "message_type": message_type.value, "media_bytes": len(data)},
        )
        db.session.commit()
        return message, True
    except BaseException:
        db.session.rollback()
        if saved_path is not None:
            try:
                saved_path.unlink(missing_ok=True)
            except OSError:
                # Fail closed; operational cleanup must alarm on orphaned files.
                current_app.logger.exception("Private media rollback cleanup failed")
        raise


def read_media_attachment(*, clinic_id: int, user_id: int, message_id: int,
                          attachment_id: int) -> tuple[bytes, MessageAttachment]:
    root, fernet, _scanner, _probe = _configured()
    if any(isinstance(i, bool) or not isinstance(i, int) or i <= 0
           for i in (clinic_id, user_id, message_id, attachment_id)):
        raise ValidationError("Invalid media lookup")
    ChatPolicyService.ensure_chat_enabled(clinic_id)
    message = ChatSecurityService.ensure_user_can_access_attachment(user_id, message_id)
    if message.clinic_id != clinic_id or message.status == MessageStatus.DELETED:
        raise NotFoundError("Media message not found")
    cutoff = ChatPolicyService.get_retention_cutoff(clinic_id)
    created = message.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created < cutoff:
        raise NotFoundError("Media message no longer available")
    attachment = db.session.execute(select(MessageAttachment).where(
        MessageAttachment.id == attachment_id,
        MessageAttachment.message_id == message_id,
        MessageAttachment.clinic_id == clinic_id,
    )).scalar_one_or_none()
    if attachment is None:
        raise NotFoundError("Media attachment not found")
    path = _storage_path(root, attachment.storage_key)
    try:
        encrypted = path.read_bytes()
        data = fernet.decrypt(encrypted)
    except (OSError, InvalidToken) as exc:
        raise NotFoundError("Media attachment unavailable") from exc
    if sha256(data).hexdigest() != attachment.checksum:
        raise NotFoundError("Media attachment failed integrity verification")
    return data, attachment
