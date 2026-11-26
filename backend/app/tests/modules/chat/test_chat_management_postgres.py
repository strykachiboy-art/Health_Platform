"""Real PostgreSQL concurrency with a runner-owned disposable database only."""
import os
from urllib.parse import urlsplit
import pytest
from sqlalchemy import text
from app.config import config_by_name
from app import create_app
from app.extensions import db as database
from app.core.exceptions import ConflictError, NotFoundError
from app.core.enums.chat_enums import ConversationType, ParticipantRole, ParticipantStatus
from app.core.enums.role_enums import Role
from app.modules.chat.models.conversation_model import Conversation
from app.modules.chat.models.conversation_participant_model import ConversationParticipant
from app.modules.chat.services import conversation_service as svc
from app.modules.chat.services import chat_management_service as management
from app.tests.core.concurrency.concurrency_harness import PostgresConcurrencyHarness
from app.tests.modules.chat.test_chat_management_boundary import grant

@pytest.fixture
def app():
    url = os.environ.get('CONCURRENCY_TEST_DATABASE_URL', '')
    name = os.environ.get('CSP_CHAT_DISPOSABLE_DB', '')
    parsed = urlsplit(url)
    assert parsed.scheme == 'postgresql+psycopg' and parsed.hostname == '127.0.0.1'
    assert name.startswith('csp_chat_') and parsed.path == '/' + name
    config = config_by_name['testing']
    original = config.SQLALCHEMY_DATABASE_URI
    config.SQLALCHEMY_DATABASE_URI = url
    flask_app = create_app('testing')
    verified = False
    try:
        with flask_app.app_context():
            assert database.session.execute(text('select current_database()')).scalar_one() == name
            assert database.session.connection().get_isolation_level() == 'READ COMMITTED'
            verified = True
            database.session.rollback()
            from app import models_registry  # noqa: F401
            database.create_all()
            yield flask_app
    finally:
        # Runner owns the entire disposable container. No production DDL.
        with flask_app.app_context():
            database.session.rollback()
            database.session.remove()
            if verified:
                database.drop_all()
            database.engine.dispose()
        config.SQLALCHEMY_DATABASE_URI = original



def race(app, op):
    h = PostgresConcurrencyHarness(os.environ['CONCURRENCY_TEST_DATABASE_URL'], workers=2, barrier_timeout_seconds=20)
    try:
        results = h.run_flask(app, op)
    finally:
        h.close()
    assert len({r.backend_pid for r in results}) == 2 and all(r.backend_pid is not None for r in results)
    return results


def test_parallel_direct_create_deduplicates(app, clinic, make_user):
    a, b = make_user(clinic=clinic), make_user(clinic=clinic)
    cid, ids = clinic.id, [a.id,b.id]
    database.session.commit()
    results = race(app, lambda i: svc.create_conversation(cid, ids[i], participant_user_ids=[ids[1-i]]).id)
    assert all(r.error is None for r in results), repr(results)
    assert results[0].value == results[1].value
    database.session.expire_all()
    assert database.session.query(Conversation).filter_by(clinic_id=cid).count() == 1
    assert database.session.query(ConversationParticipant).filter_by(conversation_id=results[0].value).count() == 2


def test_parallel_admin_demotions_preserve_one(app, clinic, make_user):
    a, b = make_user(clinic=clinic), make_user(clinic=clinic)
    c = svc.create_conversation(clinic.id,a.id,ConversationType.GROUP,title='Synthetic',participant_user_ids=[b.id])
    members=database.session.query(ConversationParticipant).filter_by(conversation_id=c.id).all()
    for m in members:
        m.status=ParticipantStatus.ACCEPTED;m.role=ParticipantRole.ADMIN
    cid, conv, ids=clinic.id,c.id,[m.id for m in members]
    database.session.commit()
    results=race(app,lambda i:svc.update_participant(ids[i],cid,role=ParticipantRole.MEMBER).id)
    assert sum(r.error is None for r in results)==1 and sum(isinstance(r.error,ConflictError) for r in results)==1,repr(results)
    database.session.expire_all()
    assert database.session.query(ConversationParticipant).filter_by(conversation_id=conv,role=ParticipantRole.ADMIN,status=ParticipantStatus.ACCEPTED).count()==1


def test_parallel_invitation_decisions_one_wins(app, clinic, make_staff, make_clinic_settings):
    a,b=make_staff(clinic,role=Role.DOCTOR),make_staff(clinic,role=Role.NURSE)
    for s in (a,b):grant(database,clinic,s.user)
    make_clinic_settings(clinic=clinic,feature_flags={'chat_enabled':True})
    c=svc.create_conversation(clinic.id,a.user_id,participant_user_ids=[b.user_id])
    p=database.session.query(ConversationParticipant).filter_by(conversation_id=c.id,user_id=b.user_id).one()
    cid,uid,pid=clinic.id,b.user_id,p.id
    database.session.commit()
    results=race(app,lambda i:management.respond(cid,uid,pid,['accepted','declined'][i])['status'])
    assert sum(r.error is None for r in results)==1 and sum(isinstance(r.error,NotFoundError) for r in results)==1,repr(results)
    database.session.expire_all()
    winner=next(r.value for r in results if r.error is None)
    assert database.session.get(ConversationParticipant,pid).status.value==winner
