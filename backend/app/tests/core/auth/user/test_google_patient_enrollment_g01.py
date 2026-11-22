"""G01: verified Google owner, clinic and patient linkage tests (SQLite only)."""

from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app import extensions

from app.core.auth.user.models.user_model import User
from app.core.auth.user.models.user_auth_identity_model import UserAuthIdentity
from app.core.auth.user.schema.user_schema import GoogleUserInfoSchema
from app.core.auth.user.services import google_auth_service as google
from app.core.enums.role_enums import Role
from app.core.exceptions import ConflictError, ValidationError
from app.modules.patient.models.patient_model import Patient


def identity(subject='g01-new-subject', email='new-g01@example.com',
             first='Verified', last='Person'):
    return GoogleUserInfoSchema(
        provider_user_id=subject, email=email, first_name=first,
        last_name=last, email_verified=True,
    )


def test_first_google_login_creates_owned_profile_and_issued_clinic(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = str(clinic.id)
    result = google.authenticate_google_user(identity())
    user = db_session.get(User, result['user_id'])
    patient = user.patient
    assert user.role is Role.PATIENT
    assert user.clinic_id == clinic.id
    assert result['clinic_context_id'] == clinic.id
    assert patient is not None and patient.user_id == user.id
    assert patient.clinic_id == clinic.id and patient.is_active
    assert patient.first_name == 'Verified' and patient.last_name == 'Person'
    assert patient.email == user.email
    assert patient.patient_number
    assert db_session.execute(select(UserAuthIdentity).where(
        UserAuthIdentity.user_id == user.id,
        UserAuthIdentity.provider_user_id == 'g01-new-subject',
    )).scalar_one() is not None


def test_repeat_google_login_is_idempotent(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    first = google.authenticate_google_user(identity())
    second = google.authenticate_google_user(identity())
    assert first['user_id'] == second['user_id']
    patients = db_session.execute(select(Patient).where(
        Patient.user_id == first['user_id'])).scalars().all()
    assert len(patients) == 1


def test_existing_google_account_enrolls_on_next_login(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = None
    original = google.authenticate_google_user(identity())
    user = db_session.get(User, original['user_id'])
    assert user.patient is None and user.clinic_id is None
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    enrolled = google.authenticate_google_user(identity())
    db_session.expire_all()
    user = db_session.get(User, original['user_id'])
    assert enrolled['user_id'] == original['user_id']
    assert enrolled['clinic_context_id'] == clinic.id
    assert user.patient is not None and user.patient.clinic_id == clinic.id


def test_unassigned_without_explicit_clinic_remains_pending(app, db_session):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = None
    result = google.authenticate_google_user(identity())
    user = db_session.get(User, result['user_id'])
    assert user.patient is None and user.clinic_id is None
    assert result['clinic_context_id'] is None


def test_existing_verified_identity_uses_staff_assigned_clinic(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = None
    first = google.authenticate_google_user(identity())
    user = db_session.get(User, first['user_id'])
    user.clinic_id = clinic.id
    db_session.commit()
    second = google.authenticate_google_user(identity())
    assert second['clinic_context_id'] == clinic.id
    assert user.patient is not None and user.patient.clinic_id == clinic.id


def test_existing_local_email_is_not_auto_linked(app, db_session, make_user, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    user = make_user(clinic=clinic, role=Role.PATIENT, email='new-g01@example.com')
    with pytest.raises(ConflictError, match='existing method'):
        google.authenticate_google_user(identity())
    assert user.patient is None
    assert db_session.execute(select(UserAuthIdentity).where(
        UserAuthIdentity.user_id == user.id)).scalar_one_or_none() is None


def test_promoted_google_user_never_gets_patient_profile(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = None
    created = google.authenticate_google_user(identity())
    user = db_session.get(User, created['user_id'])
    user.role = Role.DOCTOR
    db_session.commit()
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    subsequent = google.authenticate_google_user(identity())
    assert subsequent['role'] == Role.DOCTOR.value
    assert user.patient is None and user.clinic_id is None


def test_inactive_clinic_denied_without_committed_user(app, db_session, suspended_clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = suspended_clinic.id
    with pytest.raises(ValidationError, match='not active'):
        google.authenticate_google_user(identity())
    db_session.rollback()
    assert db_session.execute(select(User).where(User.email == 'new-g01@example.com')).scalar_one_or_none() is None


@pytest.mark.parametrize('raw', ['-1', 'x', True, 0])
def test_invalid_default_clinic_denied(app, db_session, raw):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = raw
    with pytest.raises(ValidationError, match='invalid'):
        google.authenticate_google_user(identity())
    db_session.rollback()
    assert db_session.execute(select(Patient)).scalars().all() == []


def test_missing_verified_name_does_not_fabricate_patient(app, db_session, clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    result = google.authenticate_google_user(identity(first=None, last='Person'))
    user = db_session.get(User, result['user_id'])
    assert user.patient is None and user.clinic_id is None
    assert result['clinic_context_id'] is None


def test_existing_patient_clinic_mismatch_denied(app, db_session, clinic, make_clinic):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    first = google.authenticate_google_user(identity())
    user = db_session.get(User, first['user_id'])
    other = make_clinic()
    user.clinic_id = other.id
    db_session.commit()
    with pytest.raises(ConflictError, match='mismatch'):
        google.authenticate_google_user(identity())
    db_session.rollback()
    assert db_session.execute(select(Patient).where(Patient.user_id == user.id)).scalars().one().clinic_id == clinic.id


def test_new_google_patient_reaches_real_owned_portal(app, db_session, clinic, client, monkeypatch):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    result = google.authenticate_google_user(identity())
    headers = {'Authorization': 'Bearer ' + result['access_token']}
    revocation_backend = Mock()
    revocation_backend.exists.return_value = 0
    monkeypatch.setattr(extensions, 'redis_client', revocation_backend)
    response = client.get('/api/v1/patient-portal/me', headers=headers)
    assert revocation_backend.exists.called
    assert all(call.args[0].startswith('auth:revoked:')
               for call in revocation_backend.exists.call_args_list)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['data']['first_name'] == 'Verified'
    assert payload['data']['last_name'] == 'Person'
    assert 'patient_id' not in payload['data']


def test_google_without_clinic_is_denied_owned_records(app, db_session, client, monkeypatch):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = None
    result = google.authenticate_google_user(identity())
    headers = {'Authorization': 'Bearer ' + result['access_token']}
    revocation_backend = Mock()
    revocation_backend.exists.return_value = 0
    monkeypatch.setattr(extensions, 'redis_client', revocation_backend)
    response = client.get('/api/v1/patient-portal/me', headers=headers)
    assert revocation_backend.exists.called
    assert all(call.args[0].startswith('auth:revoked:')
               for call in revocation_backend.exists.call_args_list)
    assert response.status_code == 409
    payload = response.get_json()
    assert payload['success'] is False


def test_google_patient_portal_still_denies_explicitly_revoked_jwt(
    app, db_session, clinic, client, monkeypatch,
):
    app.config['GOOGLE_PATIENT_ENROLLMENT_CLINIC_ID'] = clinic.id
    result = google.authenticate_google_user(identity())
    revocation_backend = Mock()
    revocation_backend.exists.return_value = 1
    monkeypatch.setattr(extensions, 'redis_client', revocation_backend)
    headers = {'Authorization': 'Bearer ' + result['access_token']}
    response = client.get('/api/v1/patient-portal/me', headers=headers)
    assert response.status_code == 401
    assert revocation_backend.exists.called
    assert all(call.args[0].startswith('auth:revoked:')
               for call in revocation_backend.exists.call_args_list)
