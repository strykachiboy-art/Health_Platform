"""Gate 10 real Redis revocation and outage using a valid tenant-bound token."""
from uuid import uuid4
from urllib.parse import urlsplit
import os
import pytest
import redis
from flask_jwt_extended import decode_token
from app import extensions
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.auth.user.services.token_service import REVOKED_TOKEN_PREFIX, revoke_token
from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership

LIST = '/api/v1/patients?page=1&per_page=50'

def headers(token):
    return {'Authorization': 'Bearer ' + token}

@pytest.fixture
def world(app, db, make_clinic, make_staff, make_patient):
    alpha = HospitalOrganization(id=uuid4(), name='Gate10 Redis CS FINAL synthetic Alpha')
    beta = HospitalOrganization(id=uuid4(), name='Gate10 Redis CS FINAL synthetic Beta')
    db.session.add_all([alpha, beta]); db.session.flush()
    own = make_clinic(name='Gate10 Redis CS FINAL Alpha HQ', organization_id=alpha.id, is_headquarters=True)
    other = make_clinic(name='Gate10 Redis CS FINAL Alpha other', organization_id=alpha.id, parent_clinic_id=own.id)
    foreign = make_clinic(name='Gate10 Redis CS FINAL Beta HQ', organization_id=beta.id, is_headquarters=True)
    actor = make_staff(own, role=Role.ADMIN).user
    db.session.add(TenantMembership(user_id=actor.id, organization_id=alpha.id, role='member', status='active', is_active=True)); db.session.flush()
    db.session.add(ClinicMembership(user_id=actor.id, organization_id=alpha.id, clinic_id=own.id, role='admin', is_active=True))
    patients = [make_patient(c, first_name='Synthetic', last_name='Gate10 Redis CS FINAL') for c in (own, other, foreign)]
    db.session.commit()
    with app.test_request_context():
        valid = issue_auth_tokens(actor, clinic_context_id=own.id)['access_token']
        claims = decode_token(valid)
    assert claims['tenant_id'] == str(alpha.id) and claims['clinic_context_id'] == own.id
    assert claims['role'] == actor.role.value and claims['token_version'] == actor.token_version
    assert db.session.query(ClinicMembership).filter_by(user_id=actor.id).count() == 1
    return dict(actor=actor, own=own, other=other, foreign=foreign, alpha=alpha, beta=beta, patients=patients, valid=valid)


def assert_own_list(client, w):
    result = client.get(LIST, headers=headers(w['valid']))
    assert result.status_code == 200, result.get_json()
    data = result.get_json()['data']
    assert data['total'] == 1
    assert [p['id'] for p in data['items']] == [w['patients'][0].id]
    for p in w['patients'][1:]:
        assert p.patient_number.encode() not in result.data


def assert_jwt_denied(response, w):
    assert response.status_code == 401, response.get_json()
    assert response.get_json()['msg'] == 'Token has been revoked'
    assert 'data' not in response.get_json()
    for p in w['patients']:
        assert p.patient_number.encode() not in response.data


def test_protected_request_uses_real_redis_revocation_path(app, client, world):
    url = os.environ['TEST_REDIS_URL']; parsed = urlsplit(url)
    assert app.testing and app.config['REDIS_URL'] == url
    assert parsed.scheme in {'redis', 'rediss'} and parsed.hostname
    actual = extensions.redis_client
    assert isinstance(actual, redis.Redis)
    cfg = actual.connection_pool.connection_kwargs
    independent = redis.Redis.from_url(url, socket_connect_timeout=5, socket_timeout=5)
    expected_cfg = independent.connection_pool.connection_kwargs
    assert all(cfg[k] == expected_cfg[k] for k in ('host', 'port', 'db'))
    expected_state = os.getenv('GATE10_REDIS_EXPECTED', 'healthy')
    assert expected_state in {'healthy', 'down'}
    if expected_state == 'down':
        with pytest.raises((redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
            actual.ping()
        try:
            assert_jwt_denied(client.get(LIST, headers=headers(world['valid'])), world)
        finally:
            independent.close()
        return
    assert actual.ping()
    assert independent.ping()
    with app.test_request_context():
        payload = decode_token(world['valid'])
    key = REVOKED_TOKEN_PREFIX + payload['jti']
    assert independent.exists(key) == 0
    assert_own_list(client, world)
    try:
        with app.app_context():
            revoke_token(payload)
        assert independent.exists(key) == 1 and independent.ttl(key) > 0
        assert_jwt_denied(client.get(LIST, headers=headers(world['valid'])), world)
        with app.test_request_context():
            fresh = issue_auth_tokens(world['actor'], clinic_context_id=world['own'].id)['access_token']
        assert fresh != world['valid']
        assert client.get(LIST, headers=headers(fresh)).status_code == 200
    finally:
        independent.delete(key); independent.close()
