"""GAP-B Gate 5 P0 targeted negatives. Does NOT certify all 476 endpoints.

Uses actual signed JWT, real Flask test client, SQLAlchemy/SQLite, and a
module-local synthetic Redis JTI store. Never weakens auth callbacks.
"""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from flask import session
from flask_jwt_extended import decode_token

from app.core.audit.queries.audit_queries import build_audit_query
from app.core.audit.services.audit_service import create_audit_log
from app.core.audit.models.audit_model import AuditLog
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.enums.reports_enums import ReportType, ReportFormat
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.auth.user.services.token_service import (
    revoke_user_tokens, REVOKED_TOKEN_PREFIX,
)
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.reports.models.reports_model import GeneratedReport
from app.modules.chat.realtime import chat_socket
from app.modules.chat.tasks import chat_outbox_task


class _JtiStore:
    def __init__(self):
        self.keys = set()

    def exists(self, key):
        return int(key in self.keys)

    def setex(self, key, ttl, value):
        assert isinstance(ttl, int) and ttl > 0
        self.keys.add(key)
        return True


@pytest.fixture(autouse=True)
def revocation_store(app, monkeypatch):
    from app import extensions
    store = _JtiStore()
    monkeypatch.setattr(extensions, 'redis_client', store)
    return store


@pytest.fixture()
def world(db, make_clinic, make_staff, make_user):
    alpha = HospitalOrganization(id=uuid4(), name='GAPB synthetic alpha')
    beta = HospitalOrganization(id=uuid4(), name='GAPB synthetic beta')
    db.session.add_all([alpha, beta])
    db.session.flush()
    a = make_clinic(name='GAPB A HQ', organization_id=alpha.id)
    a2 = make_clinic(name='GAPB A satellite', organization_id=alpha.id,
                     parent_clinic_id=a.id)
    b = make_clinic(name='GAPB B HQ', organization_id=beta.id)
    staff_a = make_staff(a, role=Role.ADMIN)
    staff_b = make_staff(b, role=Role.ADMIN)
    admin_a = staff_a.user
    admin_b = staff_b.user
    platform = make_user(role=Role.SUPER_ADMIN)
    for i, (org, user, role) in enumerate([
        (alpha, admin_a, 'organization_admin'),
        (beta, admin_b, 'organization_admin'),
    ], 1):
        db.session.add(TenantMembership(id=9200+i, organization_id=org.id,
            user_id=user.id, role=role, status='active', is_active=True))
    db.session.flush()
    for i, (org, clinic, user) in enumerate([
        (alpha, a, admin_a), (alpha, a2, admin_a), (beta, b, admin_b),
    ], 1):
        db.session.add(ClinicMembership(id=9300+i, organization_id=org.id,
            clinic_id=clinic.id, user_id=user.id, role='admin', is_active=True))
    db.session.commit()
    return dict(alpha=alpha,beta=beta,a=a,a2=a2,b=b,
                staff_a=staff_a, staff_b=staff_b,
                admin_a=admin_a, admin_b=admin_b, platform=platform)


def header(token):
    return {'Authorization': 'Bearer ' + token}


def test_audit_query_has_no_platform_global_scope(app):
    with pytest.raises(PermissionError):
        build_audit_query(actor_role=Role.SUPER_ADMIN, actor_clinic_id=None)
    with pytest.raises(PermissionError):
        build_audit_query(actor_role=Role.SUPER_ADMIN,
                          actor_clinic_id=1,target_clinic_id=2)


def test_unscoped_platform_jwt_cannot_list_clinical_reports_or_audit(client, world):
    pair=issue_auth_tokens(world['platform'])
    for path in ['/api/v1/reports', '/api/v1/audit-logs']:
        resp=client.get(path,headers=header(pair['access_token']))
        assert resp.status_code in (401,403,404,422), (path,resp.get_data(as_text=True)[:300])


def test_authenticated_report_list_does_not_reveal_beta(client, world, db):
    w=world
    db.session.add_all([
        GeneratedReport(clinic_id=w['a'].id, generated_by_id=w['staff_a'].id,
            report_type=ReportType.OVERVIEW, report_format=ReportFormat.PDF),
        GeneratedReport(clinic_id=w['b'].id, generated_by_id=w['staff_b'].id,
            report_type=ReportType.OVERVIEW, report_format=ReportFormat.PDF),
    ])
    db.session.commit()
    foreign_id = db.session.query(GeneratedReport).filter_by(clinic_id=w['b'].id).one().id
    pair=issue_auth_tokens(w['admin_a'])
    listing=client.get('/api/v1/reports',headers=header(pair['access_token']))
    assert listing.status_code==200,listing.get_data(as_text=True)[:300]
    content=listing.get_json()['data']
    assert content['total']==1, content
    assert all(r['clinic_id']==w['a'].id for r in content['items'])
    detail=client.get(f'/api/v1/reports/{foreign_id}',headers=header(pair['access_token']))
    assert detail.status_code==404,detail.get_data(as_text=True)[:250]
    assert 'report_type' not in detail.get_data(as_text=True)


def test_audit_listing_only_bound_clinic(client, world, db):
    w=world
    a=create_audit_log(action=AuditAction.VIEW,entity_type='GAPBProbe',
       entity_id=10,clinic_id=w['a'].id,description='Authorized synthetic record')
    b=create_audit_log(action=AuditAction.VIEW,entity_type='GAPBProbe',
       entity_id=20,clinic_id=w['b'].id,description='Foreign synthetic record')
    db.session.commit()
    pair=issue_auth_tokens(w['admin_a'])
    listing=client.get('/api/v1/audit-logs',headers=header(pair['access_token']))
    assert listing.status_code==200,listing.get_data(as_text=True)[:300]
    items=listing.get_json()['data']['items']
    assert any(row['id']==a.id for row in items)
    assert all(row['clinic_id']==w['a'].id for row in items)
    foreign=client.get(f'/api/v1/audit-logs/{b.id}',headers=header(pair['access_token']))
    assert foreign.status_code==404


def test_socket_rechecks_jti_after_initial_connection(app, world, revocation_store):
    w=world
    pair=issue_auth_tokens(w['admin_a'])
    claims=decode_token(pair['access_token'])
    with app.test_request_context('/socket.io/'):
        session['chat_user_id']=w['admin_a'].id
        session['chat_clinic_id']=w['a'].id
        session['chat_auth_claims']=claims
        assert chat_socket._get_socket_context()==(w['admin_a'].id,w['a'].id)
        revocation_store.keys.add(REVOKED_TOKEN_PREFIX + claims['jti'])
        with pytest.raises(ValueError,match='revoked'):
            chat_socket._get_socket_context()


def test_socket_rechecks_membership_after_revocation(app, world, db):
    from app.modules.clinic.models.organization_model import ClinicMembership
    w=world
    pair=issue_auth_tokens(w['admin_a'])
    claims=decode_token(pair['access_token'])
    with app.test_request_context('/socket.io/'):
        session['chat_user_id']=w['admin_a'].id
        session['chat_clinic_id']=w['a'].id
        session['chat_auth_claims']=claims
        assert chat_socket._get_socket_context()==(w['admin_a'].id,w['a'].id)
        grant=db.session.query(ClinicMembership).filter_by(
            user_id=w['admin_a'].id,clinic_id=w['a'].id).one()
        grant.is_active=False
        db.session.flush()
        with pytest.raises(ValueError,match='revoked|changed'):
            chat_socket._get_socket_context()


def test_chat_worker_filters_foreign_recipients_and_ignores_payload_clinic_override(
    world, monkeypatch
):
    w=world
    delivered=[]
    monkeypatch.setattr(chat_outbox_task.ChatSecurityService,
        'get_active_conversation_recipient_user_ids',
        lambda clinic_id,conversation_id:[w['admin_a'].id,w['admin_b'].id])
    monkeypatch.setattr(chat_outbox_task.socketio,'emit',
        lambda event,payload,**kwargs: delivered.append((event,payload,kwargs)))
    event=SimpleNamespace(id=992,event_type='message.created',clinic_id=w['a'].id,
        payload={'conversation_id':72,'clinic_id':w['b'].id})
    chat_outbox_task._emit_outbox_event(event)
    assert len(delivered)==1,delivered
    event_name,payload,kwargs=delivered[0]
    assert payload['clinic_id']==w['a'].id
    assert event_name=='message.created'
    assert kwargs['room']==chat_socket.user_room(
        w['a'].id,w['admin_a'].id,w['admin_a'].token_version)
