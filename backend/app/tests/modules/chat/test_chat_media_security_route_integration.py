"""CHAT-F3.S1 actual Flask route integration for configured media providers.

Real app/blueprints, canonical messages, ACLs and in-memory test DB;
synthetic clinical content and mocked provider I/O, never live malware or PHI.
"""
from __future__ import annotations

from unittest.mock import patch

from app.extensions import db
from app.modules.chat.models.message_attachment_model import MessageAttachment
from app.tests.modules.chat.test_chat_media_routes import _post, _setup, PNG
from app.tests.modules.chat.test_chat_routes import (
    _auth_headers,
    _make_accessible_conversation,
)


def _configured_providers(app, tmp_path):
    private = _setup(app, tmp_path)
    app.config.update(
        CHAT_MEDIA_SCAN_CALLBACK=None,
        CHAT_MEDIA_AUDIO_PROBE_CALLBACK=None,
        CHAT_MEDIA_SCANNER="clamd",
        CHAT_MEDIA_CLAMD_UNIX_SOCKET=None,
        CHAT_MEDIA_CLAMD_HOST="clamd.private",
        CHAT_MEDIA_CLAMD_PORT=3310,
        CHAT_MEDIA_AUDIO_PROBE="ffprobe",
        CHAT_MEDIA_FFPROBE_BIN="ffprobe",
    )
    return private


def _conversation(clinic, user, make_user, make_conversation,
                  make_conversation_participant):
    conversation, _ = _make_accessible_conversation(
        clinic, user, make_user, make_conversation,
        make_conversation_participant,
    )
    return conversation


def test_configured_clamd_route_accepts_scanned_encrypted_image(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    private = _configured_providers(app, tmp_path)
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    with patch(
        "app.modules.chat.services.chat_media_service.scan_with_clamd",
        return_value=True,
    ) as scan:
        response = _post(client, app, user, conversation)
    assert response.status_code == 201, response.get_json()
    scan.assert_called_once_with(
        PNG, "image/png", socket_path=None, host="clamd.private", port=3310,
    )
    message = response.get_json()["data"]
    saved = list(private.rglob("*.bin"))
    assert len(saved) == 1
    assert PNG not in saved[0].read_bytes()
    attachment_id = message["attachments"][0]["id"]
    download = client.get(
        f"/api/v1/chat/messages/{message['id']}/attachments/{attachment_id}",
        headers=_auth_headers(app, user),
    )
    assert download.status_code == 200
    assert download.data == PNG


def test_configured_clamd_route_rejects_malware_without_db_or_file(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    private = _configured_providers(app, tmp_path)
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    with patch(
        "app.modules.chat.services.chat_media_service.scan_with_clamd",
        return_value=False,
    ) as scan:
        response = _post(client, app, user, conversation)
    assert response.status_code == 422, response.get_json()
    scan.assert_called_once()
    assert db.session.query(MessageAttachment).count() == 0
    assert not list(private.rglob("*.bin"))


def test_configured_clamd_network_timeout_rejects_through_route(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    private = _configured_providers(app, tmp_path)
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    with patch(
        "app.modules.chat.services.chat_media_security_provider.socket.create_connection",
        side_effect=TimeoutError("synthetic scanner timeout"),
    ) as connect:
        response = _post(client, app, user, conversation)
    assert response.status_code == 422, response.get_json()
    connect.assert_called_once()
    assert db.session.query(MessageAttachment).count() == 0
    assert not list(private.rglob("*.bin"))


def test_configured_ffprobe_route_accepts_valid_duration_audio(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _configured_providers(app, tmp_path)
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    audio = b"OggS" + b"\x00" * 60
    with patch(
        "app.modules.chat.services.chat_media_service.probe_audio_with_ffprobe",
        return_value=12.5,
    ) as probe, patch(
        "app.modules.chat.services.chat_media_service.scan_with_clamd",
        return_value=True,
    ) as scan:
        response = _post(
            client, app, user, conversation, kind="audio", data=audio,
            mime="audio/ogg", name="synthetic.ogg",
        )
    assert response.status_code == 201, response.get_json()
    probe.assert_called_once_with(audio, "audio/ogg", binary="ffprobe")
    scan.assert_called_once_with(
        audio, "audio/ogg", socket_path=None, host="clamd.private", port=3310,
    )
    assert response.get_json()["data"]["message_type"] == "audio"


def test_configured_ffprobe_invalid_audio_blocks_scanner_and_persistence(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    private = _configured_providers(app, tmp_path)
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    with patch(
        "app.modules.chat.services.chat_media_service.probe_audio_with_ffprobe",
        return_value=None,
    ) as probe, patch(
        "app.modules.chat.services.chat_media_service.scan_with_clamd",
    ) as scan:
        response = _post(
            client, app, user, conversation, kind="audio",
            data=b"OggS" + b"\x00" * 60,
            mime="audio/ogg", name="synthetic.ogg",
        )
    assert response.status_code == 422, response.get_json()
    probe.assert_called_once()
    scan.assert_not_called()
    assert db.session.query(MessageAttachment).count() == 0
    assert not list(private.rglob("*.bin"))


def test_disabled_media_overrides_configured_scanner_and_probe(
    client, app, clinic, user, make_user, make_conversation,
    make_conversation_participant, no_audit, tmp_path,
):
    _configured_providers(app, tmp_path)
    app.config["CHAT_MEDIA_ENABLED"] = False
    conversation = _conversation(
        clinic, user, make_user, make_conversation, make_conversation_participant,
    )
    with patch(
        "app.modules.chat.services.chat_media_service.scan_with_clamd",
    ) as scan:
        response = _post(client, app, user, conversation)
    assert response.status_code == 422, response.get_json()
    scan.assert_not_called()
    assert db.session.query(MessageAttachment).count() == 0
