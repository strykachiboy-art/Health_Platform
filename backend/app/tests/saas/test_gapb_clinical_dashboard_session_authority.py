"""GAP-B signed-session adversarial tests for the main clinical dashboard.

No Redis, production data, Docker or migrations; real JWT blocklist callback,
full Flask routing, and explicit two-organization/three-clinic membership rows.
"""
from uuid import uuid4
from unittest.mock import Mock
import pytest
from flask_jwt_extended import create_access_token

from app import extensions
from app.core.enums.role_enums import Role
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.dashboard.routes import dashboard_route as routes


class IsolatedRevocationStore:
    def exists(self, key):
        return 0
    def setex(self, key, ttl, value):
        return True


@pytest.fixture(autouse=True)
def isolated_revocation(app, monkeypatch):
    monkeypatch.setattr(extensions, 'redis_client', IsolatedRevocationStore())


@pytest.fixture()
def world(db, make_clinic, make_user):
    alpha = HospitalOrganization(id=uuid4(), name='GAPB dashboard alpha')
    beta = HospitalOrganization(id=uuid4(), name='GAPB dashboard beta')
    db.session.add_all((alpha, beta))
    db.session.flush()
    a = make_clinic(name='GAPB Dashboard Alpha HQ', organization_id=alpha.id,
                    is_headquarters=True)
    a2 = make_clinic(name='GAPB Dashboard Alpha Clinic 2', organization_id=alpha.id,
                     parent_clinic_id=a.id)
    b = make_clinic(name='GAPB Dashboard Beta HQ', organization_id=beta.id,
                    is_headquarters=True)
    admin = make_user(a, role=Role.ADMIN)
    doctor = make_user(a, role=Role.DOCTOR)
    org_only = make_user(role=Role.ADMIN)
    patient = make_user(role=Role.PATIENT)
    super_admin = make_user(role=Role.SUPER_ADMIN)
    db.session.add_all([
        TenantMembership(user_id=admin.id, organization_id=alpha.id,
                         role='member', status='active', is_active=True),
        TenantMembership(user_id=doctor.id, organization_id=alpha.id,
                         role='member', status='active', is_active=True),
        TenantMembership(user_id=org_only.id, organization_id=alpha.id,
                         role='organization_admin', status='active', is_active=True),
    ])
    db.session.flush()
    db.session.add_all([
        ClinicMembership(user_id=admin.id, organization_id=alpha.id,
                         clinic_id=a.id, role='admin', is_active=True),
        ClinicMembership(user_id=doctor.id, organization_id=alpha.id,
                         clinic_id=a.id, role='doctor', is_active=True),
        ClinicMembership(user_id=doctor.id, organization_id=alpha.id,
                         clinic_id=a2.id, role='doctor', is_active=True),
    ])
    db.session.commit()
    return locals()


def headers(token):
    return {'Authorization': 'Bearer ' + token}


@pytest.fixture()
def dashboard_service_spy(monkeypatch):
    class Result:
        def model_dump(self, *, mode):
            assert mode == 'json'
            return {'context': {'scope': 'synthetic_test_only'}, 'metrics': []}
    spy = Mock(return_value=Result())
    monkeypatch.setattr(routes, 'get_dashboard', spy)
    return spy


def test_explicit_active_clinic_membership_allows_bound_staff_dashboard(client, world, dashboard_service_spy):
    admin = world['admin']
    token = issue_auth_tokens(admin)['access_token']
    r = client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 200, r.get_data(as_text=True)[:240]
    assert dashboard_service_spy.call_count == 1


def test_unscoped_admin_with_legacy_clinic_cannot_read_dashboard(client, world, dashboard_service_spy):
    admin = world['admin']
    token = issue_auth_tokens(admin, auto_assign_clinic=False)['access_token']
    r = client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 403, r.get_data(as_text=True)[:240]
    dashboard_service_spy.assert_not_called()


def test_org_admin_without_clinic_grant_cannot_read_dashboard(client, world, dashboard_service_spy):
    user = world['org_only']
    token = issue_auth_tokens(user, tenant_id=str(world['alpha'].id),
                              auto_assign_clinic=False)['access_token']
    r = client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 403
    dashboard_service_spy.assert_not_called()


def test_second_clinic_bound_token_does_not_read_legacy_first_clinic_dashboard(client, world, dashboard_service_spy):
    doctor=world['doctor']
    token=issue_auth_tokens(doctor, tenant_id=str(world['alpha'].id),
                            clinic_context_id=world['a2'].id)['access_token']
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 403
    dashboard_service_spy.assert_not_called()


def test_unscoped_super_admin_cannot_access_global_clinical_dashboard(client, world, dashboard_service_spy):
    token=issue_auth_tokens(world['super_admin'],auto_assign_clinic=False)['access_token']
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 403
    dashboard_service_spy.assert_not_called()


def test_patient_first_unscoped_personal_dashboard_remains_allowed(client, world, dashboard_service_spy):
    token=issue_auth_tokens(world['patient'])['access_token']
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 200, r.get_data(as_text=True)[:240]
    assert dashboard_service_spy.call_count == 1


def test_cross_tenant_forged_clinic_claims_denied_before_dispatch(client, world, dashboard_service_spy):
    admin=world['admin']
    claims={
        'role':'admin', 'token_version':admin.token_version,
        'tenant_id':str(world['alpha'].id),
        'clinic_context_id':world['b'].id,
    }
    token=create_access_token(identity=str(admin.id), additional_claims=claims)
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 401
    dashboard_service_spy.assert_not_called()


def test_revoked_clinic_grant_invalidates_old_dashboard_token(client, world, db, dashboard_service_spy):
    admin=world['admin']
    token=issue_auth_tokens(admin)['access_token']
    grant=db.session.query(ClinicMembership).filter_by(
        user_id=admin.id, clinic_id=world['a'].id).one()
    grant.is_active=False
    db.session.commit()
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 401
    dashboard_service_spy.assert_not_called()


def test_removed_tenant_membership_invalidates_old_dashboard_token(client, world, db, dashboard_service_spy):
    admin=world['admin']
    token=issue_auth_tokens(admin)['access_token']
    membership=db.session.query(TenantMembership).filter_by(
        user_id=admin.id, organization_id=world['alpha'].id).one()
    membership.status='revoked'
    membership.is_active=False
    db.session.commit()
    r=client.get('/api/v1/dashboard', headers=headers(token))
    assert r.status_code == 401
    dashboard_service_spy.assert_not_called()
