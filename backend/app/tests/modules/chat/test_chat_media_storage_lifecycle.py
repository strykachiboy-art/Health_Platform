"""CHAT-F3.S2: synthetic-only key rotation, retention and orphan safety."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import os

import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.core.exceptions import ValidationError
from app.extensions import db
from app.modules.chat.models.message_model import Message
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.modules.chat.services.chat_media_storage_lifecycle import (
    build_media_keyring, rotate_encrypted_blob, cleanup_private_media,
)
from app.tests.modules.chat.test_chat_media_routes import _setup, _post
from app.tests.modules.chat.test_chat_routes import (
    _auth_headers, _make_accessible_conversation,
)


def _conversation(clinic, user, make_user, make_conversation,
                  make_conversation_participant):
    return _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )[0]


def test_keyring_denies_invalid_legacy_and_uses_primary_for_new_writes():
    primary = Fernet.generate_key()
    old = Fernet.generate_key()
    first, ring = build_media_keyring(primary, json.dumps({"1": old.decode()}))
    assert ring.decrypt(Fernet(old).encrypt(b"test")) == b"test"
    assert Fernet(primary).decrypt(first.encrypt(b"new")) == b"new"
    for invalid in ('[]', '{"one":"invalid"}', '{"0":"x"}', '{"1":2}'):
        with pytest.raises(ValueError):
            build_media_keyring(primary, invalid)


def test_old_encrypted_route_read_and_one_blob_rotation(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root = _setup(app, tmp_path)
    legacy = app.config["CHAT_MEDIA_FERNET_KEY"]
    convo = _conversation(clinic, user, make_user, make_conversation,
                          make_conversation_participant)
    result = _post(client, app, user, convo)
    assert result.status_code == 201, result.get_json()
    msg = result.get_json()["data"]
    attachment = db.session.get(MessageAttachment, msg["attachments"][0]["id"])
    path = root / attachment.storage_key
    old_cipher = path.read_bytes()
    current = Fernet.generate_key().decode()
    app.config.update({
        "CHAT_MEDIA_FERNET_KEY": current,
        "CHAT_MEDIA_FERNET_LEGACY_KEYS": json.dumps({"1": legacy}),
    })
    # Canonical authenticated download remains valid during the rotation window.
    download = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{attachment.id}",
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 200
    assert sha256(download.data).hexdigest() == attachment.checksum
    assert rotate_encrypted_blob(
        root=root, storage_key=attachment.storage_key, primary_key=current,
        legacy_json=app.config["CHAT_MEDIA_FERNET_LEGACY_KEYS"],
        checksum=attachment.checksum,
    ) is True
    assert path.read_bytes() != old_cipher
    assert Fernet(current.encode()).decrypt(path.read_bytes()) == download.data
    assert rotate_encrypted_blob(
        root=root, storage_key=attachment.storage_key, primary_key=current,
        legacy_json=app.config["CHAT_MEDIA_FERNET_LEGACY_KEYS"],
        checksum=attachment.checksum,
    ) is False
    # Only after verification may operations retire the legacy key.
    app.config["CHAT_MEDIA_FERNET_LEGACY_KEYS"] = "{}"
    assert client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{attachment.id}",
        headers=_auth_headers(app, user),
    ).status_code == 200


def test_rotation_denies_wrong_checksum_without_rewriting(tmp_path):
    root = tmp_path / "private"
    folder = root / "1"
    folder.mkdir(parents=True)
    key = "1/" + "a" * 64 + ".bin"
    old = Fernet.generate_key()
    new = Fernet.generate_key()
    data = Fernet(old).encrypt(b"synthetic-only")
    path = folder / key.split("/")[1]
    path.write_bytes(data)
    with pytest.raises(ValueError):
        rotate_encrypted_blob(
            root=root, storage_key=key, primary_key=new,
            legacy_json=json.dumps({"1": old.decode()}), checksum="0" * 64,
        )
    assert path.read_bytes() == data
    with pytest.raises(ValueError):
        rotate_encrypted_blob(root=root, storage_key="../bad", primary_key=new,
                              legacy_json="{}", checksum="0" * 64)


def test_expired_ciphertext_cleanup_preview_and_explicit_gate(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root = _setup(app, tmp_path)
    convo = _conversation(clinic, user, make_user, make_conversation,
                          make_conversation_participant)
    reply = _post(client, app, user, convo)
    assert reply.status_code == 201, reply.get_json()
    msg = reply.get_json()["data"]
    row = db.session.get(Message, msg["id"])
    row.created_at = datetime.now(timezone.utc) - timedelta(days=120)
    db.session.commit()
    attachment = db.session.get(MessageAttachment, msg["attachments"][0]["id"])
    path = root / attachment.storage_key
    when = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (when, when))
    report = cleanup_private_media(clinic_id=clinic.id)
    assert report["eligible"] == 1 and report["deleted"] == 0
    assert report["retained_expired"] == 1 and path.exists()
    with pytest.raises(ValidationError):
        cleanup_private_media(clinic_id=clinic.id, dry_run=False)
    app.config["CHAT_MEDIA_MAINTENANCE_ENABLED"] = True
    confirmed = cleanup_private_media(clinic_id=clinic.id, dry_run=False)
    assert confirmed["deleted"] == 1 and not path.exists()
    assert db.session.get(MessageAttachment, attachment.id) is not None
    download = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{attachment.id}",
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 404


def test_old_orphan_cleanup_never_touches_recent_or_other_clinic(app, clinic, tmp_path):
    root = tmp_path / "orphan-root"
    app.config["CHAT_MEDIA_PRIVATE_ROOT"] = str(root)
    folder = root / str(clinic.id)
    folder.mkdir(parents=True)
    orphan = folder / ("b" * 64 + ".bin")
    recent = folder / ("c" * 64 + ".bin")
    orphan.write_bytes(b"encrypted orphan")
    recent.write_bytes(b"recent ciphertext")
    other = root / str(clinic.id + 1)
    other.mkdir()
    other_file = other / ("d" * 64 + ".bin")
    other_file.write_bytes(b"another clinic")
    when = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(orphan, (when, when))
    os.utime(other_file, (when, when))
    assert cleanup_private_media(clinic_id=clinic.id)["orphan"] == 1
    assert orphan.exists()
    app.config["CHAT_MEDIA_MAINTENANCE_ENABLED"] = True
    report = cleanup_private_media(clinic_id=clinic.id, dry_run=False)
    assert report["deleted"] == 1
    assert not orphan.exists() and recent.exists() and other_file.exists()


def test_cleanup_never_follows_symlink(app, clinic, tmp_path):
    root = tmp_path / "private"
    folder = root / str(clinic.id)
    folder.mkdir(parents=True)
    destination = tmp_path / "protected.bin"
    destination.write_bytes(b"do not touch")
    link = folder / ("a" * 64 + ".bin")
    try:
        link.symlink_to(destination)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks require OS privilege on this host")
    with pytest.raises(ValidationError):
        cleanup_private_media(clinic_id=clinic.id)
    assert destination.read_bytes() == b"do not touch"
