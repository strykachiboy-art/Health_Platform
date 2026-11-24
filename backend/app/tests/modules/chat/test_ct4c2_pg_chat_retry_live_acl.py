"""CT4C2: real PostgreSQL concurrent chat retry, sequence, and live ACL.

Reuses the immediately prior, unchanged CT4C1 disposable PostgreSQL fixture.
Only the CT4C2 test module is added. No production or persistent DB writes.
"""
from __future__ import annotations

import os
from threading import Event

import pytest
from sqlalchemy import func, select

from app.core.auth.user.models.user_model import User
from app.core.enums.chat_enums import ParticipantStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError
from app import create_app
from app.config import config_by_name
from app.extensions import db
from app.modules.chat.models.admission_case_chat_sync_request_model import (
    AdmissionCaseChatSyncRequest,
)
from app.modules.chat.models.admission_case_conversation_model import (
    AdmissionCaseConversation,
)
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services import case_chat_assignment_event_service as events
from app.modules.chat.services import case_conversation_creation_service as creation
from app.modules.chat.services.chat_security_service import ChatSecurityService
from app.modules.chat.tasks import case_chat_sync_task as scheduled
from app.modules.nursing.models.nursing_model import NursingHandoff
from app.modules.nursing.services import nursing_service
from app.modules.staff.models.staff_model import Staff
from app.tests.core.concurrency.concurrency_harness import PostgresConcurrencyHarness
from app.tests.modules.nursing.test_ct4c1_pg_handoff_concurrency import _seed_case


@pytest.fixture()
def ct4c2_app(monkeypatch):
    from sqlalchemy.engine import make_url

    url = os.environ.get('CONCURRENCY_TEST_DATABASE_URL', '')
    parsed = make_url(url)
    assert parsed.drivername == 'postgresql+psycopg2'
    assert parsed.database == 'ct4c2_disposable'
    assert parsed.username == 'ct4c2' and parsed.host == '127.0.0.1'
    assert str(parsed.port) == os.environ.get('CT4C2_PG_PORT')
    assert os.environ.get('TEST_REDIS_URL', '').endswith('/15')
    old = config_by_name['testing'].SQLALCHEMY_DATABASE_URI
    config_by_name['testing'].SQLALCHEMY_DATABASE_URI = url
    app = None
    try:
        app = create_app('testing')
        with app.app_context():
            from app import models_registry  # noqa: F401
            assert db.engine.dialect.name == 'postgresql'
            assert db.engine.url.database == 'ct4c2_disposable'
            db.create_all()  # Only disposable PostgreSQL. No migration.
        monkeypatch.setattr(
            events, 'ensure_admission_case_conversation',
            lambda **_kwargs: (_ for _ in ()).throw(
                RuntimeError('CT4C2 initial after-commit chat intentionally deferred')
            ),
        )
        yield app
    finally:
        if app is not None:
            with app.app_context():
                db.session.rollback()
                db.session.remove()
                db.drop_all()
                db.engine.dispose()
        config_by_name['testing'].SQLALCHEMY_DATABASE_URI = old


def _pending_handoff(ids):
    result = nursing_service.acknowledge_handoff(
        ids['handoffs']['incoming1'],
        clinic_id=ids['clinic_id'], actor_staff_id=ids['incoming1_id'],
    )
    assert result.id == ids['handoffs']['incoming1']
    db.session.rollback()
    queued = _queue(ids)
    assert (queued.request_sequence, queued.processed_sequence) == (1, 0)
    assert queued.authorization_handoff_id == ids['handoffs']['incoming1']
    assert queued.requested_by_user_id == ids['incoming1_user_id']


def _queue(ids):
    return db.session.execute(select(AdmissionCaseChatSyncRequest).where(
        AdmissionCaseChatSyncRequest.clinic_id == ids['clinic_id'],
        AdmissionCaseChatSyncRequest.admission_id == ids['admission_id'],
    )).scalar_one()


def _enable_actual_chat(monkeypatch):
    # The CT4C1 fixture deliberately deferred chat after clinical commit.
    # Now restore the canonical service for the races, without changing source.
    monkeypatch.setattr(events, 'ensure_admission_case_conversation',
                        creation.ensure_admission_case_conversation)


def _deliver(ids):
    return events.deliver_pending_case_chat_sync(
        clinic_id=ids['clinic_id'], admission_id=ids['admission_id'],
        actor_user_id=ids['incoming1_user_id'],
    )


def _race(app, operation):
    harness = PostgresConcurrencyHarness(
        os.environ['CONCURRENCY_TEST_DATABASE_URL'],
        workers=2, barrier_timeout_seconds=25,
    )
    try:
        rows = harness.run_flask(app, operation)
    finally:
        harness.close()
    assert len(rows) == 2
    assert all(item.error is None for item in rows), [repr(item.error) for item in rows]
    assert len({item.backend_pid for item in rows}) == 2
    return [item.value for item in rows]


def _assert_single_synced_case(ids, *, generation=1):
    db.session.rollback()  # PostgreSQL READ COMMITTED fresh reads after workers.
    row = _queue(ids)
    assert (row.request_sequence, row.processed_sequence) == (
        generation, generation,
    )
    assert row.authorization_handoff_id == ids['handoffs']['incoming1']
    links = db.session.execute(select(AdmissionCaseConversation).where(
        AdmissionCaseConversation.admission_id == ids['admission_id'],
        AdmissionCaseConversation.clinic_id == ids['clinic_id'],
    )).scalars().all()
    assert len(links) == 1
    assert db.session.scalar(select(func.count()).select_from(Conversation)) == 1
    conversation_id = links[0].conversation_id
    members = db.session.execute(select(ConversationParticipant).where(
        ConversationParticipant.conversation_id == conversation_id,
    )).scalars().all()
    assert len(members) == len({member.user_id for member in members})
    accepted = {m.user_id for m in members if m.status == ParticipantStatus.ACCEPTED}
    assert accepted == {ids['incoming1_user_id']}
    ChatSecurityService.ensure_user_can_access_conversation(
        ids['incoming1_user_id'], conversation_id,
    )
    print('CT4C2_SINGLE_CANONICAL_CASE_AND_ACTIVE_ROSTER=PASS', flush=True)
    print('CT4C2_LATEST_GENERATION_ACKNOWLEDGED=PASS', flush=True)
    return conversation_id


def test_two_direct_retries_same_admission_no_duplicate_case(ct4c2_app, monkeypatch):
    with ct4c2_app.app_context():
        ids = _seed_case()
        _pending_handoff(ids)
        _enable_actual_chat(monkeypatch)
        outcomes = _race(ct4c2_app, lambda _index: _deliver(ids))
        assert len(outcomes) == 2
        assert all(item['pending'] is False for item in outcomes)
        assert all(item.get('acknowledged_sequence') in (None, 1) for item in outcomes)
        _assert_single_synced_case(ids)
        print('CT4C2_CONCURRENT_DIRECT_DELIVERY=PASS', flush=True)


def test_two_scheduled_retry_workers_no_duplicate_links(ct4c2_app, monkeypatch):
    with ct4c2_app.app_context():
        ids = _seed_case()
        _pending_handoff(ids)
        _enable_actual_chat(monkeypatch)
        outcomes = _race(
            ct4c2_app,
            lambda _index: scheduled.process_pending_case_chat_sync.run(limit=1),
        )
        assert all(item['failed'] == 0 and item['still_pending'] == 0 for item in outcomes)
        assert 1 <= sum(item['processed'] for item in outcomes) <= 2
        _assert_single_synced_case(ids)
        repeat = scheduled.process_pending_case_chat_sync.run(limit=1)
        assert repeat == {'claimed': 0, 'processed': 0, 'failed': 0, 'still_pending': 0}
        print('CT4C2_CONCURRENT_SCHEDULED_RETRY=PASS', flush=True)


def test_new_generation_wins_against_stale_acknowledgement(ct4c2_app, monkeypatch):
    with ct4c2_app.app_context():
        ids = _seed_case()
        _pending_handoff(ids)
        committed = Event()

        def operation(worker_index):
            if worker_index == 0:
                handoff = db.session.get(NursingHandoff, ids['handoffs']['incoming1'])
                actor = db.session.get(Staff, ids['incoming1_id'])
                sequence = events.queue_handoff_case_chat_sync_in_clinical_transaction(
                    handoff=handoff, actor=actor,
                )
                db.session.commit()
                committed.set()
                return {'sequence': sequence}
            assert committed.wait(timeout=20), 'NEW_GENERATION_NOT_COMMITTED'
            acknowledged = events._acknowledge_request_if_current(
                clinic_id=ids['clinic_id'],
                admission_id=ids['admission_id'], expected_sequence=1,
            )
            return {'stale_ack': acknowledged}

        outcomes = _race(ct4c2_app, operation)
        assert outcomes[0]['sequence'] == 2
        assert outcomes[1]['stale_ack'] is False
        db.session.rollback()
        assert (_queue(ids).request_sequence, _queue(ids).processed_sequence) == (2, 0)
        _enable_actual_chat(monkeypatch)
        delivered = _deliver(ids)
        assert delivered['pending'] is False
        assert delivered['acknowledged_sequence'] == 2
        _assert_single_synced_case(ids, generation=2)
        print('CT4C2_NEWER_SEQUENCE_NOT_LOST_TO_STALE_ACK=PASS', flush=True)


def test_stale_membership_denied_while_reconciliation_races(ct4c2_app, monkeypatch):
    with ct4c2_app.app_context():
        ids = _seed_case()
        old_user_id = db.session.get(Staff, ids['outgoing_id']).user_id
        admin_user_id = db.session.execute(select(User.id).where(
            User.clinic_id == ids['clinic_id'], User.role == Role.ADMIN,
        )).scalar_one()
        _conversation, link, created = creation.ensure_admission_case_conversation(
            clinic_id=ids['clinic_id'], admission_id=ids['admission_id'],
            actor_user_id=admin_user_id,
        )
        assert created is True
        ChatSecurityService.ensure_user_can_access_conversation(
            old_user_id, link.conversation_id,
        )
        _pending_handoff(ids)
        # The old nurse remains an ACCEPTED stored participant until async sync.
        old_participant = db.session.execute(select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == link.conversation_id,
            ConversationParticipant.user_id == old_user_id,
        )).scalar_one()
        assert old_participant.status == ParticipantStatus.ACCEPTED
        with pytest.raises(NotFoundError):
            ChatSecurityService.ensure_user_can_access_conversation(
                old_user_id, link.conversation_id,
            )
        _enable_actual_chat(monkeypatch)

        def operation(worker_index):
            if worker_index == 0:
                return {'delivery': _deliver(ids)}
            try:
                ChatSecurityService.ensure_user_can_access_conversation(
                    old_user_id, link.conversation_id,
                )
            except NotFoundError:
                denied = True
            else:
                denied = False
            recipients = ChatSecurityService.get_active_conversation_recipient_user_ids(
                clinic_id=ids['clinic_id'], conversation_id=link.conversation_id,
            )
            return {'denied': denied, 'recipients': recipients}

        results = _race(ct4c2_app, operation)
        assert results[0]['delivery']['pending'] is False
        assert results[1]['denied'] is True
        assert old_user_id not in results[1]['recipients']
        cid = _assert_single_synced_case(ids)
        old = db.session.execute(select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == cid,
            ConversationParticipant.user_id == old_user_id,
        )).scalar_one()
        assert old.status == ParticipantStatus.REMOVED
        assert ChatSecurityService.get_active_conversation_recipient_user_ids(
            clinic_id=ids['clinic_id'], conversation_id=cid,
        ) == [ids['incoming1_user_id']]
        assert db.session.scalar(select(func.count()).select_from(AdmissionCaseConversation)) == 1
        print('CT4C2_LIVE_ACL_FAIL_CLOSED_DURING_SYNC=PASS', flush=True)
