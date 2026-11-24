"""Focused code-only MFA provider tests (disposable test database)."""

from __future__ import annotations

import time

import pytest

from app.core.auth.user.models.super_admin_mfa_model import (
    SuperAdminMfaEnrollment,
    SuperAdminMfaRecoveryCode,
)
from app.core.auth.user.services.super_admin_mfa_otp import (
    generate_recovery_codes,
    generate_totp_secret,
    match_totp_step,
    recovery_code_digest,
    totp_at_step,
)
from app.core.auth.user.services.super_admin_mfa_provider import (
    DatabaseSuperAdminRecoveryFactorProvider,
    begin_pending_enrollment,
    confirm_pending_enrollment,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.core.auth.user.services.recovery_factor_service import (
    register_super_admin_recovery_factor_provider,
    resolve_super_admin_recovery_factor_state,
)


@pytest.fixture()
def configured_mfa(app):
    app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"] = "unit-only-pepper-" * 4
    app.config["INTEGRATION_ENCRYPTION_KEY_VERSION"] = 1
    app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "if_enrolled"
    provider = DatabaseSuperAdminRecoveryFactorProvider()
    register_super_admin_recovery_factor_provider(app, provider)
    return provider


@pytest.fixture()
def sa(make_user):
    return make_user(role=Role.SUPER_ADMIN)


def _enroll(user, now=None):
    secret = begin_pending_enrollment(user)
    if now is None:
        now = int(time.time())
    code = totp_at_step(secret, now // 30)
    codes = confirm_pending_enrollment(user, code, now=now)
    return secret, codes


def test_generate_secret_uses_256_bits_and_valid_totp():
    secret = generate_totp_secret()
    assert len(secret) == 52
    assert totp_at_step(secret, 1).isdigit()
    assert len(totp_at_step(secret, 1)) == 6


def test_totp_accepts_one_skew_step_and_rejects_replay():
    secret = generate_totp_secret()
    code = totp_at_step(secret, 100)
    assert match_totp_step(secret=secret, code=code, now=99 * 30) == 100
    assert match_totp_step(secret=secret, code=code, now=100 * 30,
                           last_accepted_step=100) is None
    assert match_totp_step(secret=secret, code=code, now=103 * 30) is None


def test_totp_rejects_non_ascii_and_invalid_code():
    secret = generate_totp_secret()
    for bad in ("", "12345", "1234567", "１２３４５６", "nototp", None):
        assert match_totp_step(secret=secret, code=bad, now=300) is None


def test_recovery_codes_high_entropy_unique_and_digest_keyed():
    codes = generate_recovery_codes()
    assert len(codes) == len(set(codes)) == 10
    assert all(len(c) == 32 for c in codes)
    a = recovery_code_digest(code=codes[0], pepper="a" * 32)
    b = recovery_code_digest(code=codes[0], pepper="b" * 32)
    assert a != b and codes[0] not in a


def test_normal_user_cannot_begin_enrollment(make_user, configured_mfa):
    normal = make_user(role=Role.DOCTOR)
    with pytest.raises(ValidationError):
        begin_pending_enrollment(normal)


def test_unenrolled_super_admin_has_no_recovery_binding(sa, configured_mfa):
    provider = configured_mfa
    assert provider.get_recovery_binding(sa) is None
    state = resolve_super_admin_recovery_factor_state(sa)
    assert state.required is False


def test_pending_enrollment_not_a_verified_factor(sa, configured_mfa, db):
    secret = begin_pending_enrollment(sa)
    row = db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=sa.id).one()
    assert secret not in row.encrypted_totp_secret
    assert row.confirmed_at is None
    assert configured_mfa.get_recovery_binding(sa) is None


def test_invalid_totp_does_not_enroll(sa, configured_mfa):
    begin_pending_enrollment(sa)
    with pytest.raises(ValidationError):
        confirm_pending_enrollment(sa, "bad-code")
    assert configured_mfa.get_recovery_binding(sa) is None


def test_enrollment_verifies_factor_and_issues_hashed_codes(sa, configured_mfa, db):
    secret, codes = _enroll(sa)
    row = db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=sa.id).one()
    assert row.confirmed_at is not None
    assert row.binding_id == configured_mfa.get_recovery_binding(sa)
    stored = db.session.query(SuperAdminMfaRecoveryCode).filter_by(enrollment_id=row.id).all()
    assert len(stored) == len(codes) == 10
    assert all(item.code_digest not in codes for item in stored)
    state = resolve_super_admin_recovery_factor_state(sa)
    assert state.required is True
    assert state.provider_name == "database-totp-v1"
    assert state.binding_digest is not None


def test_confirmation_is_single_use(sa, configured_mfa):
    _enroll(sa)
    with pytest.raises(ValidationError):
        confirm_pending_enrollment(sa, "123456")


def test_cannot_replace_confirmed_enrollment_without_reauthentication(sa, configured_mfa):
    _enroll(sa)
    with pytest.raises(ValidationError):
        begin_pending_enrollment(sa)


def test_single_use_recovery_code(sa, configured_mfa, db):
    _, codes = _enroll(sa)
    assert configured_mfa.verify_recovery_factor(sa, codes[0]) is True
    assert configured_mfa.verify_recovery_factor(sa, codes[0]) is False
    assert configured_mfa.verify_recovery_factor(sa, codes[1]) is True
    db.session.flush()
    assert db.session.query(SuperAdminMfaRecoveryCode).filter(
        SuperAdminMfaRecoveryCode.consumed_at.is_not(None)
    ).count() == 2


def test_invalid_recovery_code_does_not_consume(sa, configured_mfa, db):
    _, codes = _enroll(sa)
    assert configured_mfa.verify_recovery_factor(sa, "A" * 32) is False
    assert configured_mfa.verify_recovery_factor(sa, codes[0]) is True
    assert db.session.query(SuperAdminMfaRecoveryCode).filter(
        SuperAdminMfaRecoveryCode.consumed_at.is_not(None)
    ).count() == 1


def test_missing_recovery_pepper_fails_closed(sa, configured_mfa, app):
    app.config.pop("SUPER_ADMIN_MFA_RECOVERY_PEPPER", None)
    with pytest.raises(ServiceUnavailableError):
        begin_pending_enrollment(sa)


def test_provider_does_not_accept_factor_from_normal_role(make_user, configured_mfa):
    normal = make_user(role=Role.DOCTOR)
    with pytest.raises(ValidationError):
        configured_mfa.verify_recovery_factor(normal, "123456")
