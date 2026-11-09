"""CHAT-F1/F2: synthetic-only authorized encrypted chat media regression."""
from __future__ import annotations

import io
from uuid import uuid4

from cryptography.fernet import Fernet
from app.extensions import db
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.core.enums.chat_enums import ParticipantStatus
from app.modules.chat.models.message_model import Message
from app.tests.modules.chat.test_chat_routes import (
    _auth_headers,
    _make_accessible_conversation,
)

PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\x0dIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\x0bIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\xfe\x02\xfe\xa7\x86\x81\x84"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
PDF = b"%PDF-1.4\n1 0 obj <</Type/Catalog>> endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _setup(app, tmp_path):
    private_root = tmp_path / "chat-media-private"
    app.config.update({
        "CHAT_MEDIA_ENABLED": True,
        "CHAT_MEDIA_PRIVATE_ROOT": str(private_root),
        "CHAT_MEDIA_FERNET_KEY": Fernet.generate_key().decode(),
        "CHAT_MEDIA_SCAN_CALLBACK": lambda content, mime: bool(content) and bool(mime),
        "CHAT_MEDIA_AUDIO_PROBE_CALLBACK": lambda content, mime: 12.0,
    })
    return private_root


def _post(client, app, user, conversation, *, kind="image", data=PNG,
          mime="image/png", name="image.png", request_id=None):
    return client.post(
        f"/api/v1/chat/conversations/{conversation.id}/media",
        headers=_auth_headers(app, user),
        data={
            "kind": kind,
            "client_request_id": request_id or str(uuid4()),
            "file": (io.BytesIO(data), name, mime),
        },
        content_type="multipart/form-data",
    )


def test_chat_media_default_disabled(client, app, clinic, user, make_user,
                                     make_conversation, make_conversation_participant,
                                     no_audit):
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    app.config["CHAT_MEDIA_ENABLED"] = False
    response = _post(client, app, user, conversation)
    assert response.status_code == 422
    assert db.session.query(MessageAttachment).count() == 0


def test_encrypted_upload_download_and_idempotency(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    private = _setup(app, tmp_path)
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    request_id = str(uuid4())
    first = _post(client, app, user, conversation, request_id=request_id)
    assert first.status_code == 201, first.get_json()
    msg = first.get_json()["data"]
    assert msg["message_type"] == "image"
    assert len(msg["attachments"]) == 1
    aid = msg["attachments"][0]["id"]
    stored = list(private.rglob("*.bin"))
    assert len(stored) == 1
    assert PNG not in stored[0].read_bytes()
    duplicate = _post(client, app, user, conversation, request_id=request_id)
    assert duplicate.status_code == 200, duplicate.get_json()
    assert duplicate.get_json()["data"]["id"] == msg["id"]
    assert db.session.query(Message).filter(Message.conversation_id == conversation.id).count() == 1
    download = client.get(
        f"/api/v1/chat/messages/{msg['id']}/attachments/{aid}",
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 200
    assert download.data == PNG
    assert "no-store" in download.headers["Cache-Control"]
    assert download.headers["X-Content-Type-Options"] == "nosniff"


def test_media_rejects_mismatched_signature_and_duplicate_different_bytes(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _setup(app, tmp_path)
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    bad = _post(client, app, user, conversation, data=b"not really an image")
    assert bad.status_code == 422
    request_id = str(uuid4())
    assert _post(client, app, user, conversation, request_id=request_id).status_code == 201
    different = _post(client, app, user, conversation, request_id=request_id,
                      kind="document", data=PDF, mime="application/pdf", name="report.pdf")
    assert different.status_code == 409


def test_audio_policy_and_authorization(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _setup(app, tmp_path)
    conversation, other = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    voice = _post(client, app, user, conversation, kind="audio",
                  data=b"OggS" + b"\x00" * 60, mime="audio/ogg", name="note.ogg")
    assert voice.status_code == 201, voice.get_json()
    msg = voice.get_json()["data"]
    assert msg["message_type"] == "audio"
    aid = msg["attachments"][0]["id"]
    # Removing an accepted participant must immediately revoke download access.
    participant = db.session.execute(db.select(ConversationParticipant).where(
        ConversationParticipant.conversation_id == conversation.id,
        ConversationParticipant.user_id == other.id,
    )).scalar_one()
    participant.status = ParticipantStatus.REMOVED
    db.session.commit()
    revoked = client.get(f"/api/v1/chat/messages/{msg['id']}/attachments/{aid}",
                         headers=_auth_headers(app, other))
    assert revoked.status_code in (403, 404, 409, 422)
    # Anonymous users also cannot retrieve the voice attachment.
    anonymous = client.get(f"/api/v1/chat/messages/{msg['id']}/attachments/{aid}")
    assert anonymous.status_code == 401
    # Different message/attachment IDs must not be interchangeable.
    missing = client.get(f"/api/v1/chat/messages/{msg['id']}/attachments/{aid + 1000}",
                         headers=_auth_headers(app, user))
    assert missing.status_code == 404


def test_scan_failure_blocks_persistence(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _setup(app, tmp_path)
    app.config["CHAT_MEDIA_SCAN_CALLBACK"] = lambda *_args: False
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    response = _post(client, app, user, conversation)
    assert response.status_code == 422
    assert db.session.query(MessageAttachment).count() == 0


def test_pdf_document_and_unrelated_user_cannot_download(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _setup(app, tmp_path)
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    response = _post(client, app, user, conversation, kind="document", data=PDF,
                     mime="application/pdf", name="synthetic.pdf")
    assert response.status_code == 201, response.get_json()
    msg = response.get_json()["data"]
    assert msg["message_type"] == "file"
    assert msg["attachments"][0]["attachment_type"] == "document"
    aid = msg["attachments"][0]["id"]
    outsider = make_user(clinic=clinic)
    denied = client.get(f"/api/v1/chat/messages/{msg['id']}/attachments/{aid}",
                        headers=_auth_headers(app, outsider))
    assert denied.status_code in (403, 404, 409, 422)
    authorized = client.get(f"/api/v1/chat/messages/{msg['id']}/attachments/{aid}",
                            headers=_auth_headers(app, user))
    assert authorized.status_code == 200
    assert authorized.data == PDF
