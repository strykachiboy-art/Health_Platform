"""CHAT-F3.S2: synthetic-only crash and unknown-outcome routed tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import json
import os

import pytest
from cryptography.fernet import Fernet

from app.core.enums.chat_enums import MessageStatus
from app.core.exceptions import ValidationError
from app.extensions import db
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.modules.chat.models.message_model import Message
from app.modules.chat.services.chat_media_storage_lifecycle import (
    cleanup_private_media, rotate_encrypted_blob,
)
from app.tests.modules.chat.test_chat_media_routes import PNG, PDF, _setup, _post
from app.tests.modules.chat.test_chat_routes import (
    _auth_headers, _make_accessible_conversation,
)


def _case(app, tmp_path, clinic, user, make_user, make_conversation,
          make_conversation_participant):
    root = _setup(app, tmp_path)
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    return root, conversation


def test_outbox_failure_after_ciphertext_write_rolls_back_and_unlinks(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    with patch(
        'app.modules.chat.services.chat_media_service.create_outbox_event',
        side_effect=RuntimeError('synthetic outbox interruption'),
    ):
        response = _post(client, app, user, conversation)
    assert response.status_code == 500
    assert db.session.query(MessageAttachment).count() == 0
    assert db.session.query(Message).filter(
        Message.conversation_id == conversation.id,
    ).count() == 0
    assert not list(root.rglob('*.bin'))


def test_ciphertext_fsync_failure_leaves_no_orphan_or_message(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    with patch(
        'app.modules.chat.services.chat_media_service.os.fsync',
        side_effect=OSError('synthetic durability interruption'),
    ):
        response = _post(client, app, user, conversation)
    assert response.status_code == 500
    assert db.session.query(MessageAttachment).count() == 0
    assert db.session.query(Message).filter(
        Message.conversation_id == conversation.id,
    ).count() == 0
    assert not list(root.rglob('*.bin'))


def test_unknown_outcome_retry_after_commit_during_key_rotation_exactly_once(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    request_id = str(uuid4())
    # Discard the first successful HTTP response to model unknown outcome.
    first = _post(client, app, user, conversation, request_id=request_id)
    assert first.status_code == 201, first.get_json()
    msg_id = first.get_json()['data']['id']
    ciphertext, = tuple(root.rglob('*.bin'))
    original = ciphertext.read_bytes()
    old = app.config['CHAT_MEDIA_FERNET_KEY']
    app.config.update({
        'CHAT_MEDIA_FERNET_KEY': Fernet.generate_key().decode(),
        'CHAT_MEDIA_FERNET_LEGACY_KEYS': json.dumps({'1': old}),
    })
    replay = _post(client, app, user, conversation, request_id=request_id)
    assert replay.status_code == 200, replay.get_json()
    assert replay.get_json()['data']['id'] == msg_id
    assert ciphertext.read_bytes() == original
    assert db.session.query(MessageAttachment).count() == 1
    assert db.session.query(Message).filter(
        Message.conversation_id == conversation.id,
    ).count() == 1
    aid = db.session.query(MessageAttachment).one().id
    download = client.get(
        f'/api/v1/chat/messages/{msg_id}/attachments/{aid}',
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 200
    assert download.data == PNG


def test_replayed_uuid_with_different_media_remains_conflict_after_rotation(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    request_id = str(uuid4())
    assert _post(client, app, user, conversation, request_id=request_id).status_code == 201
    ciphertext, = tuple(root.rglob('*.bin'))
    original = ciphertext.read_bytes()
    old = app.config['CHAT_MEDIA_FERNET_KEY']
    app.config.update({
        'CHAT_MEDIA_FERNET_KEY': Fernet.generate_key().decode(),
        'CHAT_MEDIA_FERNET_LEGACY_KEYS': json.dumps({'1': old}),
    })
    response = _post(
        client, app, user, conversation, request_id=request_id,
        kind='document', data=PDF, mime='application/pdf', name='synthetic.pdf',
    )
    assert response.status_code == 409
    assert ciphertext.read_bytes() == original
    assert db.session.query(MessageAttachment).count() == 1


def test_atomic_rekey_replace_failure_keeps_old_ciphertext_downloadable(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    response = _post(client, app, user, conversation)
    assert response.status_code == 201, response.get_json()
    msg = response.get_json()['data']
    att = db.session.get(MessageAttachment, msg['attachments'][0]['id'])
    path = root / att.storage_key
    original = path.read_bytes()
    old = app.config['CHAT_MEDIA_FERNET_KEY']
    primary = Fernet.generate_key().decode()
    legacy = json.dumps({'1': old})
    app.config.update({
        'CHAT_MEDIA_FERNET_KEY': primary,
        'CHAT_MEDIA_FERNET_LEGACY_KEYS': legacy,
    })
    with patch(
        'app.modules.chat.services.chat_media_storage_lifecycle.os.replace',
        side_effect=OSError('synthetic atomic replacement failure'),
    ):
        with pytest.raises(OSError, match='synthetic atomic replacement failure'):
            rotate_encrypted_blob(
                root=root, storage_key=att.storage_key,
                primary_key=primary, legacy_json=legacy, checksum=att.checksum,
            )
    assert path.read_bytes() == original
    assert not list(path.parent.glob('.*.tmp'))
    download = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{att.id}",
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 200 and download.data == PNG


def test_invalid_legacy_configuration_denies_access_but_keeps_ciphertext(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    response = _post(client, app, user, conversation)
    assert response.status_code == 201
    msg = response.get_json()['data']
    path, = tuple(root.rglob('*.bin'))
    before = path.read_bytes()
    app.config['CHAT_MEDIA_FERNET_LEGACY_KEYS'] = '[not-json'
    denied = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{msg['attachments'][0]['id']}",
        headers=_auth_headers(app, user),
    )
    assert denied.status_code == 422
    assert path.read_bytes() == before
    assert db.session.query(MessageAttachment).count() == 1


def test_deleted_message_route_denies_and_cleanup_is_operator_gated(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    root, conversation = _case(
        app, tmp_path, clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    response = _post(client, app, user, conversation)
    assert response.status_code == 201
    msg = response.get_json()['data']
    att = db.session.get(MessageAttachment, msg['attachments'][0]['id'])
    path = root / att.storage_key
    row = db.session.get(Message, msg['id'])
    row.status = MessageStatus.DELETED
    row.deleted_at = datetime.now(timezone.utc)
    db.session.commit()
    denied = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{att.id}",
        headers=_auth_headers(app, user),
    )
    assert denied.status_code == 404
    age = (datetime.now(timezone.utc) - timedelta(days=2)).timestamp()
    os.utime(path, (age, age))
    preview = cleanup_private_media(clinic_id=clinic.id)
    assert preview['eligible'] == 1
    assert preview['deleted_message'] == 1
    assert preview['deleted'] == 0 and path.exists()
    with pytest.raises(ValidationError):
        cleanup_private_media(clinic_id=clinic.id, dry_run=False)
    assert path.exists()
    app.config['CHAT_MEDIA_MAINTENANCE_ENABLED'] = True
    deleted = cleanup_private_media(clinic_id=clinic.id, dry_run=False)
    assert deleted['deleted'] == 1 and not path.exists()
    assert db.session.get(MessageAttachment, att.id) is not None
