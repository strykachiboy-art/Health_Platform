"""P24G focused chat engagement routes; all data is synthetic/test isolated."""
from __future__ import annotations

from app.core.enums.chat_enums import (
    ConversationType, MessageStatus, ParticipantStatus,
)
from app.extensions import db

BASE = '/api/v1/chat/messages'


def _fixture(clinic, user, make_user, make_conversation,
             make_conversation_participant, make_message):
    peer = make_user(clinic=clinic)
    room = make_conversation(clinic=clinic, created_by=user,
                             conversation_type=ConversationType.GROUP)
    for actor in (user, peer):
        make_conversation_participant(clinic=clinic, conversation=room,
                                      user=actor, status=ParticipantStatus.ACCEPTED)
    message = make_message(clinic=clinic, conversation=room, sender=user,
                           status=MessageStatus.SENT)
    db.session.commit()
    return room, peer, message


def test_reaction_authoritative_create_repeat_list_and_delete(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    path = f'{BASE}/{message.id}/reactions'
    assert client.get(path).status_code == 401
    headers = auth_headers_for(peer)
    first = client.post(path, json={'reaction': 'ack'}, headers=headers)
    assert first.status_code == 200
    item = first.get_json()['data']
    assert item['user_id'] == peer.id
    assert item['message_id'] == message.id
    assert client.post(path, json={'reaction': 'ack'}, headers=headers).get_json()['data']['id'] == item['id']
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.get_json()['total'] == 1
    assert response.get_json()['data'][0]['reaction'] == 'ack'
    assert client.delete(f'{path}/{item["id"]}', headers=headers).status_code == 200
    assert client.delete(f'{path}/{item["id"]}', headers=headers).status_code == 200
    assert client.get(path, headers=headers).get_json()['total'] == 0


def test_reaction_rejects_unknown_and_missing_membership(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    outsider = make_user(clinic=clinic)
    db.session.commit()
    path = f'{BASE}/{message.id}/reactions'
    assert client.get(path, headers=auth_headers_for(outsider)).status_code in (403, 404)
    assert client.post(path, json={'reaction': 'unapproved'},
                       headers=auth_headers_for(peer)).status_code == 422
    assert client.get(f'{path}?per_page=1000', headers=auth_headers_for(peer)).status_code == 422


def test_reaction_cross_clinic_and_pending_deny(
    client, clinic, user, make_user, make_clinic, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    foreign = make_user(clinic=make_clinic())
    pending = make_user(clinic=clinic)
    make_conversation_participant(clinic=clinic, conversation=room,
                                  user=pending, status=ParticipantStatus.PENDING)
    db.session.commit()
    path = f'{BASE}/{message.id}/reactions'
    for actor in (foreign, pending):
        assert client.get(path, headers=auth_headers_for(actor)).status_code in (403, 404)


def test_reaction_deleted_message_disallowed(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    message.status = MessageStatus.DELETED
    db.session.commit()
    path = f'{BASE}/{message.id}/reactions'
    assert client.post(path, json={'reaction': 'like'},
                       headers=auth_headers_for(peer)).status_code == 409


def test_mentions_author_only_accepted_user_and_server_list(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    path = f'{BASE}/{message.id}/mentions'
    assert client.get(path).status_code == 401
    assert client.post(path, json={'mentioned_user_id': peer.id},
                       headers=auth_headers_for(peer)).status_code == 409
    result = client.post(path, json={'mentioned_user_id': peer.id},
                         headers=auth_headers_for(user))
    assert result.status_code == 201
    assert result.get_json()['data']['mentioned_user_id'] == peer.id
    visible = client.get(path, headers=auth_headers_for(peer))
    assert visible.status_code == 200
    assert visible.get_json()['total'] == 1
    assert visible.get_json()['data'][0]['mentioned_user_id'] == peer.id


def test_mentions_disallow_nonparticipant_and_foreign_target(
    client, clinic, user, make_user, make_clinic, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    outsider = make_user(clinic=clinic)
    foreign = make_user(clinic=make_clinic())
    db.session.commit()
    path = f'{BASE}/{message.id}/mentions'
    for actor in (outsider, foreign):
        assert client.post(path, json={'mentioned_user_id': actor.id},
                           headers=auth_headers_for(user)).status_code in (403, 404)


def test_mentions_and_reactions_are_not_open_to_invalid_inputs(
    client, clinic, user, make_user, make_conversation,
    make_conversation_participant, make_message, auth_headers_for, no_audit,
):
    room, peer, message = _fixture(clinic, user, make_user, make_conversation,
                                   make_conversation_participant, make_message)
    auth = auth_headers_for(user)
    assert client.post(f'{BASE}/{message.id}/mentions', json={'mentioned_user_id': 'oops'},
                       headers=auth).status_code == 422
    assert client.post(f'{BASE}/{message.id}/reactions', json={'reaction': ''},
                       headers=auth).status_code == 422
