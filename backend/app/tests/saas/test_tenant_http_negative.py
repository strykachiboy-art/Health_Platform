"""GAP-A: authenticated HTTP negative coverage for tenant-owned clinic routes.

Requires the existing app, auth_headers_for, make_clinic and make_user fixtures.
Tests run only under TestingConfig's isolated in-memory SQLite database.
No external/production database or worker connections are required by these tests.
"""
from uuid import uuid4

import pytest

from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership


class _IsolatedRevocationStore:
    """Only GAP-A HTTP tests use this synthetic revocation backend."""

    def __init__(self):
        self._revoked_keys = set()

    def exists(self, key):
        return int(key in self._revoked_keys)

    def setex(self, key, ttl, value):
        assert isinstance(ttl, int) and ttl > 0
        self._revoked_keys.add(key)
        return True


@pytest.fixture(autouse=True)
def _gapa_http_revocation_store(app, monkeypatch):
    # Do not alter Flask-JWT-Extended callbacks or production auth code.
    # Real JWT signatures, user status, and token_version remain enforced.
    from app import extensions

    store = _IsolatedRevocationStore()
    monkeypatch.setattr(extensions, 'redis_client', store)
    return store


@pytest.fixture()
def tenant_world(db, make_clinic, make_user, auth_headers_for):
    alpha = HospitalOrganization(id=uuid4(), name='GAPA Alpha synthetic')
    beta = HospitalOrganization(id=uuid4(), name='GAPA Beta synthetic')
    db.session.add_all([alpha, beta])
    db.session.flush()

    clinic_a = make_clinic(name='GAPA Alpha HQ', organization_id=alpha.id, is_headquarters=True)
    branch_a = make_clinic(name='GAPA Alpha Branch', organization_id=alpha.id,
                           parent_clinic_id=clinic_a.id)
    clinic_b = make_clinic(name='GAPA Beta HQ', organization_id=beta.id, is_headquarters=True)

    admin_a = make_user(clinic=clinic_a, role=Role.ADMIN)
    admin_b = make_user(clinic=clinic_b, role=Role.ADMIN)
    doctor_a = make_user(clinic=clinic_a, role=Role.DOCTOR)
    db.session.add_all([
        TenantMembership(id=10001, organization_id=alpha.id, user_id=admin_a.id, is_active=True),
        TenantMembership(id=10002, organization_id=beta.id, user_id=admin_b.id, is_active=True),
        TenantMembership(id=10003, organization_id=alpha.id, user_id=doctor_a.id, is_active=True),
    ])
    db.session.flush()

    return {
        'alpha': alpha, 'beta': beta,
        'clinic_a': clinic_a, 'branch_a': branch_a, 'clinic_b': clinic_b,
        'admin_a': admin_a, 'admin_b': admin_b, 'doctor_a': doctor_a,
        'header_a': auth_headers_for(admin_a),
        'header_b': auth_headers_for(admin_b),
        'header_doctor': auth_headers_for(doctor_a),
        'spoofed_header_a': auth_headers_for(admin_a, clinic_context_id=clinic_b.id),
    }


def _url(clinic_id):
    return f'/api/v1/clinics/{clinic_id}'


def test_authenticated_admin_reads_own_org_but_not_foreign(client, tenant_world):
    w = tenant_world
    allowed = client.get(_url(w['clinic_a'].id), headers=w['header_a'])
    assert allowed.status_code == 200, allowed.get_data(as_text=True)[:400]
    assert allowed.get_json()['data']['id'] == w['clinic_a'].id
    forbidden = client.get(_url(w['clinic_b'].id), headers=w['header_a'])
    assert forbidden.status_code == 403, forbidden.get_data(as_text=True)[:400]


def test_authenticated_admin_listing_stays_in_member_organization(client, tenant_world):
    w = tenant_world
    response = client.get('/api/v1/clinics', headers=w['header_a'])
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    ids = {item['id'] for item in response.get_json()['data']}
    assert ids == {w['clinic_a'].id, w['branch_a'].id}
    assert w['clinic_b'].id not in ids


def test_forged_clinic_context_cannot_read_foreign_org(client, tenant_world):
    w = tenant_world
    response = client.get(_url(w['clinic_b'].id), headers=w['spoofed_header_a'])
    assert response.status_code == 403, response.get_data(as_text=True)[:400]
    response = client.get('/api/v1/clinics', headers=w['spoofed_header_a'])
    assert response.status_code == 200
    assert w['clinic_b'].id not in {item['id'] for item in response.get_json()['data']}


@pytest.mark.parametrize('method,suffix,payload', [
    ('patch', '', {'name': 'GAPA injected name'}),
    ('patch', '/status', {'status': 'suspended'}),
    ('patch', '/ai-credits', {'amount': 5}),
    ('patch', '/branch-configuration', {'is_headquarters': False}),
    ('post', '/branches', {'name': 'GAPA injected branch'}),
    ('post', '/api-token/regenerate', None),
])
def test_foreign_org_mutations_fail_at_http_boundary(client, tenant_world, method, suffix, payload):
    w = tenant_world
    target = _url(w['clinic_b'].id) + suffix
    resp = getattr(client, method)(target, headers=w['header_a'], json=payload)
    assert resp.status_code == 403, f'{method} {target}: {resp.get_data(as_text=True)[:400]}'


def test_untrusted_foreign_parent_and_root_provisioning_refused(client, tenant_world):
    w = tenant_world
    wrong_parent = client.post('/api/v1/clinics', headers=w['header_a'],
                               json={'name': 'GAPA Wrong Parent', 'parent_clinic_id': w['clinic_b'].id})
    assert wrong_parent.status_code == 403, wrong_parent.get_data(as_text=True)[:400]
    root = client.post('/api/v1/clinics', headers=w['header_a'], json={'name': 'GAPA Rogue Root'})
    assert root.status_code == 403, root.get_data(as_text=True)[:400]


def test_foreign_branch_list_denied(client, tenant_world):
    w = tenant_world
    resp = client.get(_url(w['clinic_b'].id) + '/branches', headers=w['header_a'])
    assert resp.status_code == 403, resp.get_data(as_text=True)[:400]


def test_membership_revocation_fails_closed(client, tenant_world, db):
    from app.modules.clinic.models.organization_model import TenantMembership
    w = tenant_world
    membership = db.session.query(TenantMembership).filter_by(
        user_id=w['admin_a'].id, organization_id=w['alpha'].id).one()
    membership.is_active = False
    db.session.flush()
    own = client.get(_url(w['clinic_a'].id), headers=w['header_a'])
    assert own.status_code == 403, own.get_data(as_text=True)[:400]
    listed = client.get('/api/v1/clinics', headers=w['header_a'])
    assert listed.status_code == 200
    assert listed.get_json()['data'] == []


def test_non_admin_cannot_read_foreign_clinic_or_mutate(client, tenant_world):
    w = tenant_world
    own = client.get(_url(w['clinic_a'].id), headers=w['header_doctor'])
    foreign = client.get(_url(w['clinic_b'].id), headers=w['header_doctor'])
    mutation = client.patch(_url(w['clinic_a'].id), headers=w['header_doctor'],
                            json={'name': 'Cannot mutate'})
    assert own.status_code == 200, own.get_data(as_text=True)[:400]
    assert foreign.status_code == 403, foreign.get_data(as_text=True)[:400]
    assert mutation.status_code == 403, mutation.get_data(as_text=True)[:400]


def test_cross_tenant_denials_do_not_mutate_foreign_record(client, tenant_world, db):
    from app.modules.clinic.models.clinic_model import Clinic
    w = tenant_world
    initial = db.session.get(Clinic, w['clinic_b'].id)
    old = (initial.name, initial.parent_clinic_id, initial.ai_credits, initial.is_headquarters)
    for method, path, data in [
        ('patch', _url(initial.id), {'name': 'Changed'}),
        ('patch', _url(initial.id)+'/ai-credits', {'amount': 77}),
        ('patch', _url(initial.id)+'/branch-configuration', {'is_headquarters': False}),
    ]:
        response = getattr(client, method)(path, headers=w['header_a'], json=data)
        assert response.status_code == 403, response.get_data(as_text=True)[:300]
    db.session.expire_all()
    after = db.session.get(Clinic, w['clinic_b'].id)
    assert (after.name, after.parent_clinic_id, after.ai_credits, after.is_headquarters) == old


def test_revoked_jwt_is_rejected_before_clinic_authorization(
    client, tenant_world, _gapa_http_revocation_store
):
    from flask_jwt_extended import decode_token
    from app.core.auth.user.services.token_service import REVOKED_TOKEN_PREFIX

    headers = tenant_world['header_a']
    token = headers['Authorization'].split('Bearer ', 1)[1]
    jti = decode_token(token)['jti']
    _gapa_http_revocation_store.setex(REVOKED_TOKEN_PREFIX + jti, 300, '1')

    response = client.get(_url(tenant_world['clinic_a'].id), headers=headers)
    assert response.status_code == 401, response.get_data(as_text=True)[:400]
    assert response.get_json()['msg'] == 'Token has been revoked'


def test_missing_revocation_store_is_still_fail_closed(
    client, tenant_world, monkeypatch
):
    from app import extensions

    monkeypatch.setattr(extensions, 'redis_client', None)
    response = client.get(
        _url(tenant_world['clinic_a'].id), headers=tenant_world['header_a']
    )
    assert response.status_code == 401, response.get_data(as_text=True)[:400]
    assert response.get_json()['msg'] == 'Token has been revoked'


def test_revocation_connection_failure_is_still_fail_closed(
    client, tenant_world, _gapa_http_revocation_store, monkeypatch
):
    from redis.exceptions import ConnectionError

    def inaccessible(_key):
        raise ConnectionError('Synthetic unreachable revocation storage')

    monkeypatch.setattr(_gapa_http_revocation_store, 'exists', inaccessible)
    response = client.get(
        _url(tenant_world['clinic_a'].id), headers=tenant_world['header_a']
    )
    assert response.status_code == 401, response.get_data(as_text=True)[:400]
    assert response.get_json()['msg'] == 'Token has been revoked'
