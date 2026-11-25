"""GAP-B Gates 3–4. Real Flask/JWT/SQLite; synthetic revocation I/O only.

No test bypasses the JWT blocklist or suppresses authorization assertions.
"""
from uuid import uuid4
import pytest
from flask_jwt_extended import create_access_token, decode_token
from app.core.enums.role_enums import Role
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.clinic.services.tenant_boundary import (
    TenantAccessDenied, require_tenant_clinic_access, require_tenant_membership,
    list_authorized_clinics_for_user,
)
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.modules.clinic.services.membership_service import (
    grant_clinic_membership, revoke_tenant_membership, revoke_clinic_membership,
)


class IsolatedRevocationStore:
    def __init__(self):
        self.revoked = set()

    def exists(self, key):
        return int(key in self.revoked)

    def setex(self, key, ttl, value):
        assert isinstance(ttl, int) and ttl > 0
        self.revoked.add(key)
        return True


@pytest.fixture(autouse=True)
def revocation_store(app, monkeypatch):
    from app import extensions
    store = IsolatedRevocationStore()
    monkeypatch.setattr(extensions, 'redis_client', store)
    return store


def auth_header(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture()
def world(db, make_clinic, make_user):
    alpha = HospitalOrganization(id=uuid4(), name='GAPB alpha synthetic')
    beta = HospitalOrganization(id=uuid4(), name='GAPB beta synthetic')
    db.session.add_all([alpha, beta])
    db.session.flush()
    a = make_clinic(name='GAPB Alpha HQ', organization_id=alpha.id, is_headquarters=True)
    a2 = make_clinic(name='GAPB Alpha Clinic 2', organization_id=alpha.id,
                     parent_clinic_id=a.id)
    b = make_clinic(name='GAPB Beta HQ', organization_id=beta.id, is_headquarters=True)
    manager = make_user(clinic=a, role=Role.ADMIN)
    doctor = make_user(clinic=a, role=Role.DOCTOR)
    outsider = make_user(clinic=b, role=Role.DOCTOR)
    billing = make_user(role=Role.ACCOUNTANT)
    org_admin_only = make_user(role=Role.ADMIN)
    patient = make_user(role=Role.PATIENT)
    db.session.add_all([
        TenantMembership(id=201, organization_id=alpha.id, user_id=manager.id,
                         role='organization_admin', status='active', is_active=True),
        TenantMembership(id=202, organization_id=alpha.id, user_id=doctor.id,
                         role='member', status='active', is_active=True),
        TenantMembership(id=203, organization_id=beta.id, user_id=doctor.id,
                         role='member', status='active', is_active=True),
        TenantMembership(id=204, organization_id=beta.id, user_id=outsider.id,
                         role='member', status='active', is_active=True),
        TenantMembership(id=205, organization_id=alpha.id, user_id=billing.id,
                         role='billing_admin', status='active', is_active=True),
        TenantMembership(id=206, organization_id=alpha.id, user_id=org_admin_only.id,
                         role='organization_admin', status='active', is_active=True),
    ])
    db.session.flush()
    db.session.add_all([
        ClinicMembership(user_id=manager.id, clinic_id=a.id,
                         organization_id=alpha.id, role='admin', is_active=True),
        ClinicMembership(user_id=doctor.id, clinic_id=a.id,
                         organization_id=alpha.id, role='doctor', is_active=True),
        ClinicMembership(user_id=doctor.id, clinic_id=a2.id,
                         organization_id=alpha.id, role='doctor', is_active=True),
        ClinicMembership(user_id=outsider.id, clinic_id=b.id,
                         organization_id=beta.id, role='doctor', is_active=True),
    ])
    # Commit the synthetic starting state before HTTP requests. A denied
    # route is entitled to roll back its OWN transaction; it must not erase
    # uncommitted setup data and create a false JWT-revocation result.
    db.session.commit()
    return dict(alpha=alpha, beta=beta, a=a, a2=a2, b=b,
                manager=manager, doctor=doctor, outsider=outsider,
                billing=billing, org_admin_only=org_admin_only, patient=patient)


def test_distinct_tenant_and_clinic_grants(world):
    w=world
    assert require_tenant_membership(user_id=w['billing'].id,
        organization_id=w['alpha'].id).role == 'billing_admin'
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=w['billing'].id,
            organization_id=w['alpha'].id, clinic_id=w['a'].id)
    assert require_tenant_clinic_access(user_id=w['doctor'].id,
        organization_id=w['alpha'].id, clinic_id=w['a2'].id)==w['a2'].id
    assert {x.id for x in list_authorized_clinics_for_user(user_id=w['doctor'].id)} == {
        w['a'].id, w['a2'].id}


def test_cross_organization_grants_rejected(world):
    w=world
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=w['doctor'].id,
            organization_id=w['beta'].id, clinic_id=w['a'].id)
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=w['doctor'].id,
            organization_id=w['beta'].id, clinic_id=w['b'].id)


def test_actual_access_and_refresh_tokens_bound_to_same_tenant(world):
    w=world
    pair=issue_auth_tokens(w['doctor'])
    a=decode_token(pair['access_token'])
    r=decode_token(pair['refresh_token'])
    assert a['tenant_id'] == r['tenant_id'] == str(w['alpha'].id)
    assert a['clinic_context_id'] == r['clinic_context_id'] == w['a'].id
    assert pair['tenant_id'] == str(w['alpha'].id)


def test_forged_tenant_and_clinic_claims_fail_at_jwt(client, world):
    w=world
    invalid=[
        {'tenant_id': str(w['alpha'].id), 'clinic_context_id': w['b'].id},
        {'tenant_id': str(w['beta'].id), 'clinic_context_id': w['b'].id},
        {'clinic_context_id': w['a'].id},
        {'tenant_id': 'not-a-uuid', 'clinic_context_id': w['a'].id},
        {'tenant_id': str(w['alpha'].id), 'clinic_context_id': True},
        {'tenant_id': str(w['alpha'].id), 'clinic_context_id': -1},
        {'tenant_id': str(w['alpha'].id), 'clinic_context_id': w['a'].id, 'role': 'admin'},
    ]
    for forged in invalid:
        claims={'role': 'doctor', 'token_version': w['doctor'].token_version, **forged}
        jwt=create_access_token(identity=str(w['doctor'].id), additional_claims=claims)
        result=client.get('/api/v1/auth/tenant-context',headers=auth_header(jwt))
        assert result.status_code==401, (forged, result.get_json())


def test_org_only_token_cannot_read_clinical_records(client, world):
    w=world
    tokens=issue_auth_tokens(w['billing'],tenant_id=str(w['alpha'].id),
                             auto_assign_clinic=False)
    response=client.get(f"/api/v1/clinics/{w['a'].id}",
        headers=auth_header(tokens['access_token']))
    assert response.status_code==403, response.get_data(as_text=True)[:200]
    # Even a user with explicit organization-admin rights cannot see charts
    # without a separate matching clinic grant.
    admin_tokens=issue_auth_tokens(w['org_admin_only'],
        tenant_id=str(w['alpha'].id), auto_assign_clinic=False)
    admin_response=client.get(f"/api/v1/clinics/{w['a'].id}",
        headers=auth_header(admin_tokens['access_token']))
    assert admin_response.status_code==403, admin_response.get_data(as_text=True)[:200]


def test_tenant_switch_rotates_old_token_and_remains_clinicless(client, world):
    w=world
    old=issue_auth_tokens(w['doctor'])
    result=client.post('/api/v1/auth/tenant-context',
         json={'organization_id':str(w['beta'].id)}, headers=auth_header(old['access_token']))
    assert result.status_code==200, result.get_data(as_text=True)[:300]
    new=result.get_json()['data']
    assert new['tenant_id']==str(w['beta'].id)
    assert new['clinic_context_id'] is None
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(old['access_token'])).status_code==401
    # Membership switch MUST invalidate old refresh as well as old access.
    assert client.post('/api/v1/auth/refresh',
        headers=auth_header(old['refresh_token'])).status_code==401
    fresh=client.get('/api/v1/auth/tenant-context',headers=auth_header(new['access_token']))
    assert fresh.status_code==200 and fresh.get_json()['data']['tenant_id']==str(w['beta'].id)


def test_multi_clinic_member_can_switch_only_explicit_grants(client, world):
    w=world
    old=issue_auth_tokens(w['doctor'])
    result=client.post('/api/v1/auth/tenant-context/clinic',
         json={'clinic_id':w['a2'].id},headers=auth_header(old['access_token']))
    assert result.status_code==200, result.get_data(as_text=True)[:300]
    new=result.get_json()['data']
    assert new['tenant_id']==str(w['alpha'].id)
    assert new['clinic_context_id']==w['a2'].id
    denied=client.post('/api/v1/auth/tenant-context/clinic',
         json={'clinic_id':w['b'].id},headers=auth_header(new['access_token']))
    assert denied.status_code==403
    # A failed switch did not revoke the existing context or expose Beta.
    still=client.get('/api/v1/auth/tenant-context',headers=auth_header(new['access_token']))
    assert still.status_code==200 and still.get_json()['data']['clinic_id']==w['a2'].id


def test_tenant_status_revocation_rejects_existing_jwt(client, world, db):
    w=world
    issued=issue_auth_tokens(w['doctor'])
    tm=db.session.query(TenantMembership).filter_by(
        user_id=w['doctor'].id, organization_id=w['alpha'].id).one()
    tm.status='revoked'
    db.session.flush()
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(issued['access_token'])).status_code==401


def test_clinic_membership_revocation_rejects_existing_jwt(client, world, db):
    w=world
    issued=issue_auth_tokens(w['doctor'])
    grant=db.session.query(ClinicMembership).filter_by(
        user_id=w['doctor'].id, clinic_id=w['a'].id).one()
    grant.is_active=False
    db.session.flush()
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(issued['access_token'])).status_code==401


def test_token_version_change_rejects_previous_context(client, world):
    w=world
    issued=issue_auth_tokens(w['doctor'])
    w['doctor'].token_version+=1
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(issued['access_token'])).status_code==401


def test_role_change_claim_mismatch_rejects_previous_context(client, world):
    w=world
    issued=issue_auth_tokens(w['doctor'])
    w['doctor'].role=Role.ADMIN
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(issued['access_token'])).status_code==401


def test_patient_first_login_can_be_unscoped_but_cannot_choose_org(client, world):
    w=world
    issued=issue_auth_tokens(w['patient'])
    access=decode_token(issued['access_token'])
    assert 'tenant_id' not in access and 'clinic_context_id' not in access
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(issued['access_token'])).status_code==200
    denied=client.post('/api/v1/auth/tenant-context',
        json={'organization_id':str(w['alpha'].id)},
        headers=auth_header(issued['access_token']))
    assert denied.status_code==403


def test_org_admin_cannot_grant_other_tenant_or_wrong_role(world):
    w=world
    with pytest.raises(TenantAccessDenied):
        grant_clinic_membership(actor_id=w['manager'].id,
            user_id=w['doctor'].id, clinic_id=w['b'].id, role='doctor')
    with pytest.raises(TenantAccessDenied):
        grant_clinic_membership(actor_id=w['manager'].id,
            user_id=w['doctor'].id, clinic_id=w['a2'].id, role='admin')


def test_revocation_service_rotates_version_and_disables_grants(world, db):
    w=world
    before=w['doctor'].token_version
    revoke_tenant_membership(actor_id=w['manager'].id,
        user_id=w['doctor'].id, organization_id=w['alpha'].id)
    db.session.refresh(w['doctor'])
    assert w['doctor'].token_version==before+1
    tm=db.session.query(TenantMembership).filter_by(
        user_id=w['doctor'].id, organization_id=w['alpha'].id).one()
    assert tm.status=='revoked' and not tm.is_active
    grants=db.session.query(ClinicMembership).filter_by(
        user_id=w['doctor'].id, organization_id=w['alpha'].id).all()
    assert grants and all(not grant.is_active for grant in grants)
    with pytest.raises(TenantAccessDenied):
        require_tenant_clinic_access(user_id=w['doctor'].id,
            organization_id=w['alpha'].id, clinic_id=w['a'].id)


def test_missing_redis_is_still_fail_closed(client, world, monkeypatch):
    from app import extensions
    w=world
    token=issue_auth_tokens(w['doctor'])['access_token']
    monkeypatch.setattr(extensions, 'redis_client', None)
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(token)).status_code==401


def test_organization_admin_cannot_self_grant_clinical_access(world):
    w=world
    with pytest.raises(TenantAccessDenied):
        grant_clinic_membership(actor_id=w['org_admin_only'].id,
            user_id=w['org_admin_only'].id,
            clinic_id=w['a'].id, role='admin')


def test_revoked_membership_blocks_signed_refresh_token(client, world, db):
    w=world
    issued=issue_auth_tokens(w['doctor'])
    member=db.session.query(TenantMembership).filter_by(
        user_id=w['doctor'].id, organization_id=w['alpha'].id).one()
    member.is_active=False
    db.session.flush()
    response=client.post('/api/v1/auth/refresh',
        headers=auth_header(issued['refresh_token']))
    assert response.status_code==401


def test_tenant_and_clinic_switches_write_audit_evidence(client, world, db):
    from app.core.audit.models.audit_model import AuditLog
    w=world
    initial=issue_auth_tokens(w['doctor'])
    org_switch=client.post('/api/v1/auth/tenant-context',
        json={'organization_id':str(w['alpha'].id)},
        headers=auth_header(initial['access_token']))
    assert org_switch.status_code==200
    next_token=org_switch.get_json()['data']['access_token']
    clinic_switch=client.post('/api/v1/auth/tenant-context/clinic',
        json={'clinic_id':w['a2'].id}, headers=auth_header(next_token))
    assert clinic_switch.status_code==200
    records=db.session.query(AuditLog).filter(
        AuditLog.user_id==w['doctor'].id,
        AuditLog.description.in_([
            'Authorized organization-context switch',
            'Authorized clinical clinic-context switch',
        ])).all()
    assert len(records)==2
    assert {r.description for r in records}=={
        'Authorized organization-context switch',
        'Authorized clinical clinic-context switch',
    }
    assert all(r.evidence_sha256 and len(r.evidence_sha256)==64 for r in records)


def test_cross_tenant_clinic_switch_requires_tenant_selection_first(client, world, db):
    w=world
    # Grant the doctor Beta's site as well. Membership alone is insufficient
    # to switch directly from selected Alpha to a Beta clinic.
    from app.core.auth.user.models.user_model import User
    db.session.add(ClinicMembership(
        user_id=w['doctor'].id, clinic_id=w['b'].id,
        organization_id=w['beta'].id, role='doctor', is_active=True))
    # The new authorized grant exists before the intentionally denied
    # cross-tenant request performs its normal transaction rollback.
    db.session.commit()
    alpha_token=issue_auth_tokens(w['doctor'])['access_token']
    denied=client.post('/api/v1/auth/tenant-context/clinic',
        headers=auth_header(alpha_token),json={'clinic_id':w['b'].id})
    assert denied.status_code==403
    assert client.get('/api/v1/auth/tenant-context',
        headers=auth_header(alpha_token)).status_code==200
    beta=client.post('/api/v1/auth/tenant-context',
        headers=auth_header(alpha_token),
        json={'organization_id':str(w['beta'].id)})
    assert beta.status_code==200
    beta_token=beta.get_json()['data']['access_token']
    selected=client.post('/api/v1/auth/tenant-context/clinic',
        headers=auth_header(beta_token),json={'clinic_id':w['b'].id})
    assert selected.status_code==200
    assert selected.get_json()['data']['tenant_id']==str(w['beta'].id)
    assert selected.get_json()['data']['clinic_context_id']==w['b'].id



def test_successful_multiclinic_switch_audit_has_actor_scope_and_target(client, world, db):
    from app.core.audit.models.audit_model import AuditLog
    w = world
    pair = issue_auth_tokens(w['doctor'])
    response = client.post('/api/v1/auth/tenant-context/clinic',
        headers=auth_header(pair['access_token']), json={'clinic_id': w['a2'].id})
    assert response.status_code == 200, response.get_data(as_text=True)[:300]
    event = db.session.query(AuditLog).filter_by(
        user_id=w['doctor'].id,
        description='Authorized clinical clinic-context switch',
    ).one()
    assert event.clinic_id == w['a'].id
    assert event.new_value['tenant_id'] == str(w['alpha'].id)
    assert event.new_value['clinic_context_id'] == w['a2'].id
    assert event.evidence_sha256 and len(event.evidence_sha256) == 64


def test_membership_grant_and_revoke_other_site_have_valid_actor_audit(world, db):
    from app.core.audit.models.audit_model import AuditLog
    from app.modules.clinic.services.membership_service import (
        revoke_clinic_membership,
    )
    w = world
    # Organization administrator is assigned to Alpha HQ, while the
    # grant/revoke destination is a different clinic in that organization.
    prior_version = w['doctor'].token_version
    revoke_clinic_membership(actor_id=w['manager'].id,
        user_id=w['doctor'].id, clinic_id=w['a2'].id)
    grant_clinic_membership(actor_id=w['manager'].id,
        user_id=w['doctor'].id, clinic_id=w['a2'].id, role='doctor')
    db.session.refresh(w['doctor'])
    assert w['doctor'].token_version == prior_version + 2
    assert require_tenant_clinic_access(user_id=w['doctor'].id,
        organization_id=w['alpha'].id, clinic_id=w['a2'].id) == w['a2'].id
    records = db.session.query(AuditLog).filter(
        AuditLog.user_id == w['manager'].id,
        AuditLog.description.in_([
            'Explicit clinic membership granted',
            'Explicit clinic membership revoked',
        ]),
    ).all()
    assert len(records) == 2
    assert {event.description for event in records} == {
        'Explicit clinic membership granted',
        'Explicit clinic membership revoked',
    }
    for event in records:
        assert event.clinic_id == w['a'].id
        assert event.new_value['clinic_id'] == w['a2'].id
        assert event.new_value['organization_id'] == str(w['alpha'].id)
        assert event.evidence_sha256 and len(event.evidence_sha256) == 64
