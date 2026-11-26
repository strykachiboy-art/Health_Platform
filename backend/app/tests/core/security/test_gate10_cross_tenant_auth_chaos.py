"""Gate 10 tenant-bound JWT and patient isolation, with positive access controls."""
from uuid import uuid4
import pytest
from flask_jwt_extended import create_access_token, decode_token
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import HospitalOrganization, TenantMembership, ClinicMembership
from app.modules.patient.models.patient_model import Patient

LIST = '/api/v1/patients?page=1&per_page=50'

def headers(token):
    return {'Authorization': 'Bearer ' + token}

@pytest.fixture
def world(app, db, make_clinic, make_staff, make_patient):
    alpha = HospitalOrganization(id=uuid4(), name='Gate10 CS FINAL synthetic Alpha')
    beta = HospitalOrganization(id=uuid4(), name='Gate10 CS FINAL synthetic Beta')
    db.session.add_all([alpha, beta]); db.session.flush()
    own = make_clinic(name='Gate10 CS FINAL Alpha HQ', organization_id=alpha.id, is_headquarters=True)
    other = make_clinic(name='Gate10 CS FINAL Alpha other', organization_id=alpha.id, parent_clinic_id=own.id)
    foreign = make_clinic(name='Gate10 CS FINAL Beta HQ', organization_id=beta.id, is_headquarters=True)
    actor = make_staff(own, role=Role.ADMIN).user
    db.session.add(TenantMembership(user_id=actor.id, organization_id=alpha.id, role='member', status='active', is_active=True)); db.session.flush()
    db.session.add(ClinicMembership(user_id=actor.id, organization_id=alpha.id, clinic_id=own.id, role='admin', is_active=True))
    patients = [make_patient(c, first_name='Synthetic', last_name='Gate10 CS FINAL') for c in (own, other, foreign)]
    db.session.commit()
    with app.test_request_context():
        valid = issue_auth_tokens(actor, clinic_context_id=own.id)['access_token']
        claims = decode_token(valid)
    assert claims['tenant_id'] == str(alpha.id) and claims['clinic_context_id'] == own.id
    assert claims['role'] == actor.role.value and claims['token_version'] == actor.token_version
    assert db.session.query(ClinicMembership).filter_by(user_id=actor.id).count() == 1
    return dict(actor=actor, own=own, other=other, foreign=foreign, alpha=alpha, beta=beta, patients=patients, valid=valid)


def forged(app, w, case):
    clinic, tenant = {'same_org': (w['other'], w['alpha']), 'foreign_clinic': (w['foreign'], w['alpha']), 'foreign_org': (w['foreign'], w['beta'])}[case]
    with app.test_request_context():
        token = create_access_token(identity=str(w['actor'].id), additional_claims={'role': w['actor'].role.value, 'token_version': w['actor'].token_version, 'tenant_id': str(tenant.id), 'clinic_context_id': clinic.id})
        claims = decode_token(token)
    assert claims['clinic_context_id'] == clinic.id and claims['tenant_id'] == str(tenant.id)
    return headers(token), w['patients'][1 if case == 'same_org' else 2]


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


CASES = ('same_org', 'foreign_clinic', 'foreign_org')

@pytest.mark.parametrize('case', CASES)
def test_forged_foreign_clinic_context_cannot_expand_patient_list(app, client, world, case):
    assert_own_list(client, world)
    bad, _ = forged(app, world, case)
    assert_jwt_denied(client.get(LIST, headers=bad), world)
    assert_own_list(client, world)

@pytest.mark.parametrize('case', CASES)
def test_forged_foreign_clinic_context_cannot_read_foreign_patient(app, client, world, case):
    valid = headers(world['valid'])
    own = client.get(f"/api/v1/patients/{world['patients'][0].id}", headers=valid)
    assert own.status_code == 200 and own.get_json()['data']['id'] == world['patients'][0].id
    bad, target = forged(app, world, case)
    url = f'/api/v1/patients/{target.id}'
    assert_jwt_denied(client.get(url, headers=bad), world)
    response = client.get(url, headers=valid)
    absent = client.get('/api/v1/patients/99999999', headers=valid)
    assert response.status_code == absent.status_code == 404
    assert target.patient_number.encode() not in response.data
    assert_own_list(client, world)

@pytest.mark.parametrize('case', CASES)
def test_forged_foreign_clinic_context_cannot_mutate_foreign_patient(app, client, db, world, case):
    valid = headers(world['valid'])
    bad, target = forged(app, world, case)
    own = world['patients'][0]; own_id = own.id; target_id = target.id
    before = target.is_active; own_before = own.is_active
    own_url = f'/api/v1/patients/{own_id}/status'; target_url = f'/api/v1/patients/{target_id}/status'
    control = client.patch(own_url, json={'is_active': not own_before}, headers=valid)
    assert control.status_code == 200, control.get_json()
    db.session.expire_all()
    assert db.session.get(Patient, own_id).is_active == (not own_before)
    assert_jwt_denied(client.patch(target_url, json={'is_active': not before}, headers=bad), world)
    db.session.expire_all()
    assert db.session.get(Patient, target_id).is_active == before
    denied = client.patch(target_url, json={'is_active': not before}, headers=valid)
    assert denied.status_code == 404, denied.get_json()
    db.session.expire_all()
    assert db.session.get(Patient, target_id).is_active == before
    assert db.session.get(Patient, own_id).is_active == (not own_before)
