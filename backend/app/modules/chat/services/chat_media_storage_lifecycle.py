"""CHAT-F3.S2: strict Fernet keyring and operator-gated private-media maintenance.

Not a background worker. By default this module only reports candidates.
A delete must be explicitly permitted by an operator-controlled config gate.
Deleting an encrypted file is NOT a guarantee of secure physical overwrite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import secrets
import stat

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_KEY_PATTERN = re.compile(r"^[0-9]+/[0-9a-f]{64}\.bin$")
_FILE_PATTERN = re.compile(r"^[0-9a-f]{64}\.bin$")


def build_media_keyring(primary: str | bytes, legacy_json: str | None = None):
    """New writes use primary; old ciphertext can read via pinned legacy keys.

    Legacy config is a JSON object mapping positive version strings to Fernet
    keys; keep it until explicit re-encryption and backup validation finish.
    """
    try:
        primary_key = primary.encode("ascii") if isinstance(primary, str) else primary
        first = Fernet(primary_key)
        raw = json.loads(legacy_json or "{}")
        if not isinstance(raw, dict) or len(raw) > 8:
            raise ValueError("Invalid legacy media key manifest")
        keys = [first]
        for version, value in sorted(raw.items()):
            if (not isinstance(version, str) or not version.isdecimal()
                    or int(version) <= 0 or not isinstance(value, str)):
                raise ValueError("Invalid legacy media key entry")
            previous = value.encode("ascii")
            if previous == primary_key:
                raise ValueError("Legacy key duplicates current media key")
            keys.append(Fernet(previous))
        return first, MultiFernet(keys) if len(keys) > 1 else first
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("Invalid private media encryption keyring") from exc


def _checked_path(root: Path, storage_key: str) -> Path:
    if not isinstance(storage_key, str) or not _KEY_PATTERN.fullmatch(storage_key):
        raise ValueError("Invalid storage key")
    if root.is_symlink():
        raise ValueError("Symlinked storage root refused")
    root = root.resolve()
    path = root.joinpath(*storage_key.split("/"))
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Symlinked private media path refused")
    if path.resolve().parent != (root / storage_key.split("/")[0]).resolve():
        raise ValueError("Private media path traversal")
    return path


def rotate_encrypted_blob(*, root: Path, storage_key: str,
                          primary_key: str | bytes, legacy_json: str,
                          checksum: str) -> bool:
    """Rotate one known encrypted file atomically, with checksum verification.

    Only callable by explicitly authorized maintenance code. Concurrent reads
    require the legacy key until rotation completes across all replicas.
    """
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Invalid media checksum")
    path = _checked_path(Path(root), storage_key)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Private media ciphertext unavailable")
    first, keyring = build_media_keyring(primary_key, legacy_json)
    encrypted = path.read_bytes()
    try:
        plaintext = keyring.decrypt(encrypted)
    except InvalidToken as exc:
        raise ValueError("Media ciphertext cannot be verified") from exc
    if sha256(plaintext).hexdigest() != checksum:
        raise ValueError("Media plaintext checksum mismatch")
    try:
        first.decrypt(encrypted)
        return False  # Already encrypted under current key; idempotent.
    except InvalidToken:
        pass
    tmp = path.with_name("." + path.name + "." + secrets.token_hex(10) + ".tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        with os.fdopen(os.open(tmp, flags, 0o600), "wb") as stream:
            stream.write(first.encrypt(plaintext))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if hasattr(os, "O_DIRECTORY"):
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def _private_root(app) -> Path:
    from app.core.exceptions import ValidationError

    raw = app.config.get("CHAT_MEDIA_PRIVATE_ROOT")
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise ValidationError("Private chat media root not configured")
    rawpath = Path(raw)
    if rawpath.is_symlink():
        raise ValidationError("Symlinked media root refused")
    root = rawpath.resolve()
    for key in ("STORAGE_ROOT", "STORAGE_PUBLIC_ROOT"):
        pub = app.config.get(key)
        if pub:
            public_root = Path(pub).resolve()
            if root == public_root or public_root in root.parents:
                raise ValidationError("Media root overlaps public storage")
    static = app.static_folder
    if static:
        static_root = Path(static).resolve()
        if root == static_root or static_root in root.parents:
            raise ValidationError("Media root overlaps static files")
    return root


def cleanup_private_media(*, clinic_id: int, dry_run: bool = True,
                          limit: int = 200, min_age_hours: int = 24) -> dict:
    """Inspect encrypted blobs for deleted/expired attachments and old orphans.

    No file deletion without CHAT_MEDIA_MAINTENANCE_ENABLED=True and
    dry_run=False. Never changes DB records or crosses the clinic directory.
    Candidate files must be older than min_age_hours (>= 24) to protect
    in-flight uploads and unknown-outcome retries.
    """
    from flask import current_app
    from app.core.exceptions import ValidationError
    from app.core.enums.chat_enums import MessageStatus
    from app.extensions import db
    from app.modules.chat.models.message_attachment_model import MessageAttachment
    from app.modules.chat.models.message_model import Message
    from app.modules.chat.services.chat_policy_service import ChatPolicyService

    if isinstance(clinic_id, bool) or not isinstance(clinic_id, int) or clinic_id <= 0:
        raise ValidationError("Invalid cleanup clinic")
    if type(dry_run) is not bool or type(limit) is not int or not 1 <= limit <= 500:
        raise ValidationError("Invalid media maintenance parameters")
    if type(min_age_hours) is not int or not 24 <= min_age_hours <= 720:
        raise ValidationError("Media cleanup grace window is unsafe")
    if not dry_run and current_app.config.get("CHAT_MEDIA_MAINTENANCE_ENABLED") is not True:
        raise ValidationError("Private media deletion has not been authorized")

    root = _private_root(current_app)
    folder = root / str(clinic_id)
    report = {"scanned": 0, "eligible": 0, "deleted": 0,
              "orphan": 0, "deleted_message": 0, "retained_expired": 0,
              "dry_run": dry_run}
    if not folder.exists():
        return report
    if folder.is_symlink() or not folder.is_dir() or folder.resolve().parent != root:
        raise ValidationError("Invalid clinic media directory")
    cutoff = ChatPolicyService.get_retention_cutoff(clinic_id)
    grace = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
    for path in sorted(folder.iterdir()):
        if report["scanned"] >= limit:
            break
        if path.is_symlink():
            raise ValidationError("Symlink found in private media directory")
        if not _FILE_PATTERN.fullmatch(path.name):
            continue  # Unknown entry: never delete arbitrarily.
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValidationError("Non-regular private media entry")
        report["scanned"] += 1
        if datetime.fromtimestamp(metadata.st_mtime, timezone.utc) > grace:
            continue
        key = f"{clinic_id}/{path.name}"
        record = db.session.execute(db.select(MessageAttachment, Message).join(
            Message, Message.id == MessageAttachment.message_id,
        ).where(
            MessageAttachment.clinic_id == clinic_id,
            MessageAttachment.storage_key == key,
            Message.clinic_id == clinic_id,
        )).one_or_none()
        reason = None
        if record is None:
            reason = "orphan"
        else:
            _attachment, message = record
            created = message.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            status_value = getattr(message.status, "value", message.status)
            if status_value == MessageStatus.DELETED.value:
                reason = "deleted_message"
            elif created < cutoff:
                reason = "retained_expired"
        if reason is None:
            continue
        report["eligible"] += 1
        report[reason] += 1
        if not dry_run:
            # Recheck the file identity to avoid following or replacing paths.
            again = path.lstat()
            if (not stat.S_ISREG(again.st_mode) or again.st_ino != metadata.st_ino
                    or again.st_size != metadata.st_size
                    or again.st_mtime_ns != metadata.st_mtime_ns):
                raise ValidationError("Media changed during maintenance")
            path.unlink()
            report["deleted"] += 1
    return report
