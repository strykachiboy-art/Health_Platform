from __future__ import annotations

from flask_jwt_extended import decode_token

from app.core.auth.user.models.user_model import User
from app.core.auth.user.services import user_service
from app.core.enums.audit_enums import AuditAction
from app.core.enums.role_enums import Role
from app.core.exceptions import (
    ConflictError,
    ValidationError,
)

import pytest

from app.extensions import db
from app.modules.clinic.services.session_authority import TenantContextDenied
from app.modules.clinic.models.organization_model import (
    TenantMembership, ClinicMembership,
)


def _grant_superadmin_clinic(user, clinic):
    db.session.add(TenantMembership(
        user_id=user.id, organization_id=clinic.organization_id,
        role='member', status='active', is_active=True,
    ))
    db.session.flush()
    db.session.add(ClinicMembership(
        user_id=user.id, organization_id=clinic.organization_id,
        clinic_id=clinic.id, role=user.role.value, is_active=True,
    ))
    db.session.flush()


# ============================================================================
# GET USER
# ============================================================================


def test_get_user_returns_existing_user(
    user,
):
    result = user_service.get_user(user.id)

    assert result is user
    assert result.id == user.id
    assert result.email == user.email
    assert result.role == user.role


@pytest.mark.parametrize(
    "user_id",
    [
        0,
        -1,
        None,
        True,
        False,
        "1",
    ],
)
def test_get_user_rejects_invalid_user_id(
    user_id,
):
    with pytest.raises(
        ValidationError,
        match="User ID must be a positive integer",
    ):
        user_service.get_user(user_id)


def test_get_user_raises_when_user_does_not_exist(
    app,
):
    with pytest.raises(
        ValidationError,
        match=r"User 999999 not found",
    ):
        user_service.get_user(999999)


# ============================================================================
# GET USER BY EMAIL
# ============================================================================


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
    ],
)
def test_get_user_by_email_returns_none_for_empty_email(
    email,
):
    assert (
        user_service.get_user_by_email(email)
        is None
    )


def test_get_user_by_email_normalizes_email(
    user,
):
    result = user_service.get_user_by_email(
        f"  {user.email.upper()}  "
    )

    assert result is not None
    assert result.id == user.id


def test_get_user_by_email_returns_none_when_missing(
    app,
):
    result = user_service.get_user_by_email(
        "missing-user@example.com"
    )

    assert result is None


def test_get_user_by_email_does_not_match_unrelated_email(
    user,
):
    result = user_service.get_user_by_email(
        "another-user@example.com"
    )

    assert result is None


# ============================================================================
# REGISTER USER
# ============================================================================


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "invalid-email",
        "missing-at-symbol.com",
    ],
)
def test_register_user_rejects_invalid_email(
    app,
    email,
):
    with pytest.raises(
        ValidationError,
        match="A valid email is required",
    ):
        user_service.register_user(
            email,
            "StrongPass123",
            Role.PATIENT,
        )


@pytest.mark.parametrize(
    "password",
    [
        None,
        "",
        "short",
        "1234567",
    ],
)
def test_register_user_rejects_weak_password(
    app,
    password,
):
    with pytest.raises(
        ValidationError,
        match="Password must be at least 8 characters",
    ):
        user_service.register_user(
            "new-user@example.com",
            password,
            Role.PATIENT,
        )


@pytest.mark.parametrize(
    "role",
    [
        None,
        "patient",
        "admin",
        1,
    ],
)
def test_register_user_rejects_invalid_role(
    app,
    role,
):
    with pytest.raises(
        ValidationError,
        match="Invalid user role",
    ):
        user_service.register_user(
            "new-user@example.com",
            "StrongPass123",
            role,
        )


@pytest.mark.parametrize(
    "role",
    [
        Role.PATIENT,
        Role.DOCTOR,
        Role.NURSE,
        Role.PHARMACIST,
        Role.ADMIN,
    ],
)
def test_register_user_accepts_valid_role(
    app,
    role,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user = user_service.register_user(
        "new-user@example.com",
        "StrongPass123",
        role,
    )

    assert user.id is not None
    assert user.role is role


def test_register_user_normalizes_email(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user = user_service.register_user(
        "  NEW-USER@Example.COM  ",
        "StrongPass123",
        Role.PATIENT,
    )

    assert user.email == "new-user@example.com"
    assert user.role is Role.PATIENT
    assert user.id is not None


def test_register_user_assigns_clinic(
    app,
    clinic,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user = user_service.register_user(
        "clinic-user@example.com",
        "StrongPass123",
        Role.PATIENT,
        clinic_id=clinic.id,
    )

    assert user.clinic_id == clinic.id


def test_register_user_allows_no_clinic(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user = user_service.register_user(
        "global-user@example.com",
        "StrongPass123",
        Role.PATIENT,
    )

    assert user.clinic_id is None


def test_register_user_rejects_duplicate_email(
    user,
):
    with pytest.raises(
        ConflictError,
        match="already registered",
    ):
        user_service.register_user(
            user.email.upper(),
            "StrongPass123",
            Role.PATIENT,
        )


def test_register_user_sets_password(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user = user_service.register_user(
        "password-user@example.com",
        "StrongPass123",
        Role.PATIENT,
    )

    persisted_user = db_session.get(
        User,
        user.id,
    )

    assert persisted_user is not None
    assert persisted_user.check_password(
        "StrongPass123"
    )
    assert not persisted_user.check_password(
        "WrongPassword123"
    )


def test_register_user_stores_normalized_email_in_database(
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user_service.register_user(
        "  Persisted@Example.COM  ",
        "StrongPass123",
        Role.PATIENT,
    )

    persisted_user = (
        db_session.query(User)
        .filter_by(
            email="persisted@example.com"
        )
        .first()
    )

    assert persisted_user is not None


def test_register_user_creates_audit_log(
    app,
    monkeypatch,
):
    calls = []

    def fake_audit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        fake_audit,
    )

    user = user_service.register_user(
        "audited-user@example.com",
        "StrongPass123",
        Role.PATIENT,
    )

    assert len(calls) == 1

    audit = calls[0]

    assert audit["action"] == AuditAction.CREATE
    assert audit["entity_type"] == "User"
    assert audit["entity_id"] == user.id
    assert (
        audit["description"]
        == (
            "User registered: "
            "audited-user@example.com (patient)"
        )
    )


def test_register_user_audit_uses_normalized_email(
    app,
    monkeypatch,
):
    calls = []

    def fake_audit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        fake_audit,
    )

    user_service.register_user(
        "  AUDIT@Example.COM  ",
        "StrongPass123",
        Role.DOCTOR,
    )

    assert len(calls) == 1

    assert (
        calls[0]["description"]
        == (
            "User registered: "
            "audit@example.com (doctor)"
        )
    )


def test_register_user_returns_user_instance(
    app,
    monkeypatch,
):
    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.register_user(
        "instance-user@example.com",
        "StrongPass123",
        Role.PATIENT,
    )

    assert isinstance(
        result,
        User,
    )


# ============================================================================
# AUTHENTICATE USER
# ============================================================================


@pytest.mark.parametrize(
    "email,password",
    [
        (None, "StrongPass123"),
        ("", "StrongPass123"),
        ("user@example.com", None),
        ("user@example.com", ""),
    ],
)
def test_authenticate_user_requires_credentials(
    app,
    email,
    password,
):
    with pytest.raises(
        ValidationError,
        match="Email and password are required",
    ):
        user_service.authenticate_user(
            email,
            password,
        )


def test_authenticate_user_normalizes_email(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.authenticate_user(
        f"  {user.email.upper()}  ",
        "CorrectPass123",
    )

    assert result["user_id"] == user.id


def test_authenticate_user_rejects_unknown_user(
    app,
):
    with pytest.raises(
        ValidationError,
        match="Invalid email or password",
    ):
        user_service.authenticate_user(
            "missing@example.com",
            "StrongPass123",
        )


def test_authenticate_user_rejects_wrong_password(
    db_session,
    user,
):
    user.set_password(
        "CorrectPass123"
    )
    db_session.commit()

    with pytest.raises(
        ValidationError,
        match="Invalid email or password",
    ):
        user_service.authenticate_user(
            user.email,
            "WrongPass123",
        )


def test_authenticate_user_does_not_reveal_unknown_user_or_password_difference(
    db_session,
    user,
):
    user.set_password(
        "CorrectPass123"
    )
    db_session.commit()

    with pytest.raises(
        ValidationError,
        match="Invalid email or password",
    ):
        user_service.authenticate_user(
            user.email,
            "WrongPass123",
        )

    with pytest.raises(
        ValidationError,
        match="Invalid email or password",
    ):
        user_service.authenticate_user(
            "does-not-exist@example.com",
            "WrongPass123",
        )


def test_authenticate_user_rejects_inactive_user(
    db_session,
    user,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = False
    db_session.commit()

    with pytest.raises(
        ValidationError,
        match="This account has been deactivated",
    ):
        user_service.authenticate_user(
            user.email,
            "CorrectPass123",
        )


def test_authenticate_user_returns_expected_response(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = True
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    assert result["access_token"]
    assert result["refresh_token"]
    assert result["user_id"] == user.id
    assert result["role"] == user.role.value


def test_authenticate_user_access_token_contains_role_and_token_version(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = True
    user.token_version = 7
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    access_payload = decode_token(
        result["access_token"]
    )

    assert access_payload["sub"] == str(
        user.id
    )
    assert (
        access_payload["role"]
        == user.role.value
    )
    assert (
        access_payload["token_version"]
        == 7
    )


def test_authenticate_user_refresh_token_contains_token_version(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = True
    user.token_version = 11
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    refresh_payload = decode_token(
        result["refresh_token"]
    )

    assert refresh_payload["sub"] == str(
        user.id
    )
    assert (
        refresh_payload["token_version"]
        == 11
    )


def test_authenticate_user_uses_current_user_role_in_access_token(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.role = Role.DOCTOR

    user.set_password(
        "CorrectPass123"
    )

    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    result = user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    access_payload = decode_token(
        result["access_token"]
    )

    assert (
        access_payload["role"]
        == Role.DOCTOR.value
    )

    assert (
        result["role"]
        == Role.DOCTOR.value
    )


def test_authenticate_user_creates_login_audit(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = True
    db_session.commit()

    calls = []

    def fake_audit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        fake_audit,
    )

    user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    assert len(calls) == 1

    audit = calls[0]

    assert audit["action"] == AuditAction.LOGIN
    assert audit["entity_type"] == "User"
    assert audit["entity_id"] == user.id
    assert audit["user_id"] == user.id

    assert (
        audit["description"]
        == "User 'admin@test.com' logged in"
    )


def test_authenticate_user_login_audit_uses_normalized_email(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    db_session.commit()

    calls = []

    def fake_audit(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        fake_audit,
    )

    user_service.authenticate_user(
        f"  {user.email.upper()}  ",
        "CorrectPass123",
    )

    assert len(calls) == 1

    assert (
        calls[0]["description"]
        == "User 'admin@test.com' logged in"
    )


def test_authenticate_user_updates_last_login(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.is_active = True
    user.last_login_at = None
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    assert user.last_login_at is None

    user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    db_session.refresh(
        user
    )

    assert user.last_login_at is not None


def test_authenticate_user_updates_last_login_on_subsequent_login(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    db_session.refresh(
        user
    )

    first_login = user.last_login_at

    user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    db_session.refresh(
        user
    )

    second_login = user.last_login_at

    assert first_login is not None
    assert second_login is not None
    assert second_login >= first_login


def test_authenticate_user_does_not_change_token_version(
    app,
    db_session,
    user,
    monkeypatch,
):
    user.set_password(
        "CorrectPass123"
    )
    user.token_version = 3
    db_session.commit()

    monkeypatch.setattr(
        user_service,
        "create_audit_log",
        lambda **kwargs: None,
    )

    user_service.authenticate_user(
        user.email,
        "CorrectPass123",
    )

    db_session.refresh(
        user
    )

    assert user.token_version == 3
# ============================================================================
# ISSUE AUTH TOKENS
# ============================================================================


def test_issue_auth_tokens_returns_tokens_without_context_for_normal_user(
    app,
    user,
):
    result = user_service.issue_auth_tokens(
        user,
    )

    assert result["user_id"] == user.id
    assert result["role"] == user.role.value
    assert result["clinic_context_id"] is None

    access_payload = decode_token(
        result["access_token"],
    )
    refresh_payload = decode_token(
        result["refresh_token"],
    )

    assert access_payload["role"] == user.role.value
    assert access_payload["token_version"] == user.token_version
    assert "clinic_context_id" not in access_payload

    assert refresh_payload["token_version"] == user.token_version
    assert "clinic_context_id" not in refresh_payload


def test_issue_auth_tokens_adds_context_to_super_admin_tokens(
    app,
    make_user,
    make_clinic,
):
    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic()
    _grant_superadmin_clinic(user, clinic)

    result = user_service.issue_auth_tokens(
        user,
        clinic_context_id=clinic.id,
    )

    assert result["user_id"] == user.id
    assert result["role"] == Role.SUPER_ADMIN.value
    assert result["clinic_context_id"] == clinic.id
    assert result["tenant_id"] == str(clinic.organization_id)

    access_payload = decode_token(
        result["access_token"],
    )
    refresh_payload = decode_token(
        result["refresh_token"],
    )

    assert access_payload["clinic_context_id"] == clinic.id
    assert refresh_payload["clinic_context_id"] == clinic.id
    assert access_payload["tenant_id"] == str(clinic.organization_id)
    assert refresh_payload["tenant_id"] == str(clinic.organization_id)


def test_issue_auth_tokens_rejects_context_for_normal_user(
    user,
    clinic,
):
    with pytest.raises(
        TenantContextDenied,
        match="Clinic grant required",
    ):
        user_service.issue_auth_tokens(
            user,
            clinic_context_id=clinic.id,
        )


def test_issue_auth_tokens_rejects_inactive_clinic_context(
    make_user,
    make_clinic,
):
    from app.core.enums.clinic_enums import ClinicStatus

    user = make_user(
        role=Role.SUPER_ADMIN,
    )
    clinic = make_clinic(
        status=ClinicStatus.INACTIVE,
    )

    with pytest.raises(
        TenantContextDenied,
        match="Clinic unavailable",
    ):
        user_service.issue_auth_tokens(
            user,
            clinic_context_id=clinic.id,
        )


def test_issue_auth_tokens_rejects_inactive_user(
    make_user,
):
    user = make_user(
        is_active=False,
    )

    with pytest.raises(
        TenantContextDenied,
        match="Active authenticated user required",
    ):
        user_service.issue_auth_tokens(
            user,
        )


def test_issue_auth_tokens_rejects_invalid_user(
):
    with pytest.raises(
        TenantContextDenied,
        match="Active authenticated user required",
    ):
        user_service.issue_auth_tokens(
            object(),
        )
