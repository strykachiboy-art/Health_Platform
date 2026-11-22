"""WD07-B S09-A: clinic-assignment response proof without selection privilege.

These tests use the existing Flask testing database fixtures, not WD07B's live
isolated PostgreSQL or any external Google account.
"""

import pytest
from flask_jwt_extended import decode_token

from app.core.auth.user.services.user_service import (
    issue_auth_tokens,
)
from app.core.auth.user.services.clinic_context_service import (
    resolve_effective_clinic_id,
)
from app.core.enums.clinic_enums import ClinicStatus
from app.core.enums.role_enums import Role
from app.core.exceptions import ValidationError


ASSIGNED_ROLES = (
    Role.ADMIN,
    Role.DOCTOR,
    Role.NURSE,
    Role.PATIENT,
    Role.PHARMACIST,
    Role.LAB_TECHNICIAN,
    Role.RECEPTIONIST,
    Role.ACCOUNTANT,
    Role.PARAMEDIC,
    Role.EMT,
    Role.DRIVER,
    Role.AMBULANCE_DISPATCHER,
    Role.AMBULANCE_COORDINATOR,
    Role.OTHER,
)


@pytest.mark.parametrize('role', ASSIGNED_ROLES)
def test_assigned_role_response_contains_verified_clinic_without_jwt_selection_claim(
    app, make_user, clinic, role
):
    actor = make_user(clinic=clinic, role=role)
    issued = issue_auth_tokens(actor)

    assert issued['user_id'] == actor.id
    assert issued['role'] == role.value
    assert issued['clinic_context_id'] == clinic.id
    access = decode_token(issued['access_token'])
    refresh = decode_token(issued['refresh_token'])
    assert access['role'] == role.value
    assert access['token_version'] == actor.token_version
    assert refresh['token_version'] == actor.token_version
    # Staff must not gain Super Admin's client-selectable JWT clinic claim.
    assert 'clinic_context_id' not in access
    assert 'clinic_context_id' not in refresh
    # Backend authorization still independently resolves assignment from DB.
    assert resolve_effective_clinic_id(user_id=actor.id, jwt_payload=access) == clinic.id


@pytest.mark.parametrize('role', (Role.PATIENT, Role.DOCTOR, Role.OTHER))
def test_unassigned_role_still_returns_null_context(app, make_user, role):
    actor = make_user(role=role)
    issued = issue_auth_tokens(actor)
    assert issued['clinic_context_id'] is None
    assert 'clinic_context_id' not in decode_token(issued['access_token'])
    assert 'clinic_context_id' not in decode_token(issued['refresh_token'])


def test_super_admin_stays_system_scoped_until_explicit_selection(
    app, make_user, clinic
):
    actor = make_user(role=Role.SUPER_ADMIN)
    before = issue_auth_tokens(actor)
    assert before['clinic_context_id'] is None
    assert 'clinic_context_id' not in decode_token(before['access_token'])
    selected = issue_auth_tokens(actor, clinic_context_id=clinic.id)
    assert selected['clinic_context_id'] == clinic.id
    assert decode_token(selected['access_token'])['clinic_context_id'] == clinic.id
    assert decode_token(selected['refresh_token'])['clinic_context_id'] == clinic.id


@pytest.mark.parametrize('role', (Role.PATIENT, Role.DOCTOR, Role.ADMIN, Role.PARAMEDIC))
def test_non_superadmin_cannot_select_any_clinic_even_own(
    make_user, clinic, role
):
    actor = make_user(clinic=clinic, role=role)
    with pytest.raises(
        ValidationError,
        match='Only a super administrator can use a clinic context',
    ):
        issue_auth_tokens(actor, clinic_context_id=clinic.id)


@pytest.mark.parametrize('status', (ClinicStatus.INACTIVE, ClinicStatus.SUSPENDED))
def test_inactive_or_suspended_assigned_clinic_fails_closed(
    make_user, make_clinic, status
):
    clinic = make_clinic(status=status)
    actor = make_user(clinic=clinic, role=Role.PARAMEDIC)
    with pytest.raises(ValidationError, match='Assigned clinic is not active'):
        issue_auth_tokens(actor)


def test_staff_login_returns_verified_clinic_without_granting_jwt_selection(
    client, make_user, clinic
):
    actor = make_user(clinic=clinic, role=Role.DOCTOR)
    response = client.post('/api/v1/auth/login', json={
        'email': actor.email,
        'password': 'supersecret',
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body['success'] is True
    data = body['data']
    assert data['role'] == Role.DOCTOR.value
    assert data['clinic_context_id'] == clinic.id
    assert 'clinic_context_id' not in decode_token(data['access_token'])
    assert 'clinic_context_id' not in decode_token(data['refresh_token'])


def test_reassignment_is_reflected_only_in_new_token_response(
    app, db_session, make_user, make_clinic
):
    old_clinic = make_clinic(name='S09-A old synthetic clinic')
    new_clinic = make_clinic(name='S09-A new synthetic clinic')
    actor = make_user(clinic=old_clinic, role=Role.NURSE)
    before = issue_auth_tokens(actor)
    assert before['clinic_context_id'] == old_clinic.id
    actor.clinic_id = new_clinic.id
    db_session.flush()
    after = issue_auth_tokens(actor)
    assert after['clinic_context_id'] == new_clinic.id
    assert 'clinic_context_id' not in decode_token(after['access_token'])
    assert resolve_effective_clinic_id(
        user_id=actor.id,
        jwt_payload=decode_token(before['access_token']),
    ) == new_clinic.id
