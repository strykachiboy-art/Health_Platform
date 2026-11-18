"""GAP-C consolidated targeted security negatives (real ORM, signed JWT, no Docker).

These focused tests do NOT substitute for comprehensive 476-route / independent review.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from flask import g

from app import extensions
from app.core.enums.role_enums import Role
from app.core.exceptions import NotFoundError, ValidationError
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.backup.backup_service import BackupServiceError, create_full_backup
from app.core.storage.storage_service import profile_image_url
from app.modules.clinic.models.organization_model import (
    HospitalOrganization, TenantMembership, ClinicMembership,
)
from app.modules.patient.services.patient_service import get_patient, list_patients


class IsolatedRevocationStore:
    def exists(self, key):
        return 0

    def setex(self, key, ttl, value):
        assert isinstance(ttl, int) and ttl > 0
        return True


@pytest.fixture(autouse=True)
def isolated_revocation(app, monkeypatch):
    monkeypatch.setattr(extensions, 'redis_client', IsolatedRevocationStore())


@pytest.fixture()
def world(db, make_clinic, make_staff, make_patient, make_user):
    # Two real organization owners, two clinics inside Alpha, one in Beta.
    alpha = HospitalOrganization(id=uuid4(), name='GAPC final Alpha')
    beta = HospitalOrganization(id=uuid4(), name='GAPC final Beta')
    db.session.add_all((alpha, beta))
    db.session.flush()
    a = make_clinic(name='GAPC final Alpha HQ', organization_id=alpha.id,
                    is_headquarters=True)
    a2 = make_clinic(name='GAPC final Alpha site 2', organization_id=alpha.id,
                     parent_clinic_id=a.id)
    b = make_clinic(name='GAPC final Beta HQ', organization_id=beta.id,
                    is_headquarters=True)
    staff = make_staff(a, role=Role.DOCTOR)
    actor = staff.user
    org_only = make_user(role=Role.ADMIN)
    patient_a = make_patient(a)
    patient_a2 = make_patient(a2)
    patient_b = make_patient(b)
    db.session.add_all([
        TenantMembership(user_id=actor.id, organization_id=alpha.id,
                         role='member', is_active=True, status='active'),
        TenantMembership(user_id=org_only.id, organization_id=alpha.id,
                         role='organization_admin', is_active=True, status='active'),
    ])
    db.session.flush()  # Required composite-FK parent.
    db.session.add_all([
        ClinicMembership(user_id=actor.id, organization_id=alpha.id,
                         clinic_id=a.id, role='doctor', is_active=True),
        ClinicMembership(user_id=actor.id, organization_id=alpha.id,
                         clinic_id=a2.id, role='doctor', is_active=True),
    ])
    db.session.commit()
    return locals()


def auth(token):
    return {'Authorization': 'Bearer ' + token}


def test_two_tenant_three_clinic_patient_lookup_is_id_scoped(client, world):
    w = world
    token = issue_auth_tokens(w['actor'])['access_token']
    allowed = client.get(f"/api/v1/patients/{w['patient_a'].id}", headers=auth(token))
    foreign_site = client.get(f"/api/v1/patients/{w['patient_a2'].id}", headers=auth(token))
    foreign_tenant = client.get(f"/api/v1/patients/{w['patient_b'].id}", headers=auth(token))
    absent = client.get('/api/v1/patients/99999999', headers=auth(token))
    assert allowed.status_code == 200, allowed.get_data(as_text=True)[:300]
    assert foreign_site.status_code == absent.status_code == 404
    assert foreign_tenant.status_code == absent.status_code
    assert str(w['patient_b'].patient_number).encode() not in foreign_tenant.data


def test_two_tenant_three_clinic_patient_list_is_filtered(client, world):
    w = world
    token = issue_auth_tokens(w['actor'])['access_token']
    result = client.get('/api/v1/patients', headers=auth(token))
    assert result.status_code == 200, result.get_data(as_text=True)[:300]
    body = result.get_json()
    text = str(body)
    assert w['patient_a'].patient_number in text
    assert w['patient_a2'].patient_number not in text
    assert w['patient_b'].patient_number not in text


def test_unscoped_patient_list_service_denied_even_without_http(app):
    with app.app_context():
        with pytest.raises(ValidationError, match='Explicit clinic scope'):
            list_patients()


def test_patient_service_request_lookup_does_not_follow_foreign_id(app, world, monkeypatch):
    from app.core.utils import decorators
    w = world
    with app.test_request_context('/api/v1/patients'):
        g.current_user_id = w['actor'].id
        monkeypatch.setattr(decorators, 'get_current_clinic_id', lambda *, required: w['a'].id)
        assert get_patient(w['patient_a'].id).id == w['patient_a'].id
        with pytest.raises(NotFoundError):
            get_patient(w['patient_b'].id)
        with pytest.raises(NotFoundError):
            list_patients(clinic_id=w['b'].id)


def test_clinicless_organization_admin_cannot_get_patient(client, world):
    w = world
    token = issue_auth_tokens(
        w['org_only'], tenant_id=str(w['alpha'].id), auto_assign_clinic=False,
    )['access_token']
    reply = client.get(f"/api/v1/patients/{w['patient_a'].id}", headers=auth(token))
    # Missing clinic context can be surfaced as validation failure (422).
    assert reply.status_code in {401, 403, 404, 422}
    assert b'patient_number' not in reply.data


def test_membership_revocation_blocks_same_signed_patient_read(client, world, db):
    w = world
    token = issue_auth_tokens(w['actor'])['access_token']
    membership = db.session.query(TenantMembership).filter_by(
        user_id=w['actor'].id, organization_id=w['alpha'].id,
    ).one()
    membership.status = 'revoked'
    membership.is_active = False
    db.session.commit()
    reply = client.get(f"/api/v1/patients/{w['patient_a'].id}", headers=auth(token))
    assert reply.status_code == 401


@pytest.mark.parametrize('key', [
    '../private/database.dump',
    'clinical/secret.pdf',
    'profile_images/../secret.png',
    'profile_images/a.png?token=leak',
    'profile_images/a.png#fragment',
    'profile_images/%2e%2e/secret.png',
    'profile_images\\secret.png',
    'https://example.net/a.png',
    '/profile_images/a.png',
    'profile_images/a.svg',
])
def test_public_profile_url_only_accepts_public_image_category(app, key):
    with app.app_context():
        app.config['STORAGE_PUBLIC_BASE_URL']='https://cdn.example.com'
        with pytest.raises(ValidationError, match='public profile image storage key'):
            profile_image_url(key)


def test_public_profile_url_keeps_existing_public_profile_contract(app):
    with app.app_context():
        app.config['STORAGE_PUBLIC_BASE_URL'] = 'https://cdn.example.com'
        assert profile_image_url('profile_images/public.png') == (
            'https://cdn.example.com/profile_images/public.png'
        )


def test_backup_cannot_be_persisted_within_public_storage(app, tmp_path, monkeypatch):
    with app.app_context():
        public = tmp_path / 'public-storage'
        public.mkdir()
        called = []
        def unsafe_backup(**kwargs):
            called.append(kwargs)
            raise AssertionError('Database backup must never start for unsafe path')
        monkeypatch.setattr(
            'app.core.backup.backup_service.backup_database', unsafe_backup,
        )
        with pytest.raises(BackupServiceError, match='public storage'):
            create_full_backup(
                database_url='postgresql://synthetic-local-only',
                backup_root=public / 'dump', storage_root=public,
            )
        assert not called
        assert not (public / 'dump').exists()
