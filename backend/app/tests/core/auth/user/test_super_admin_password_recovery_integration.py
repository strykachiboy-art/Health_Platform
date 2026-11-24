"""G04 real provider + existing reset API, using disposable SQLite and Redis.

No email is sent, no production policy is enabled, and the reset service is not
reimplemented. Every credential is synthetic and only lives in test fixtures.
"""
from __future__ import annotations

import time

import pytest
from flask_jwt_extended import create_access_token

from app import extensions, register_super_admin_recovery_provider
from app.core.auth.user.models.super_admin_mfa_model import SuperAdminMfaEnrollment
from app.core.auth.user.services import password_security_service as reset
from app.core.auth.user.services.recovery_factor_service import (
    _PROVIDER_EXTENSION_KEY,
    resolve_super_admin_recovery_factor_state,
)
from app.core.auth.user.services.super_admin_mfa_otp import totp_at_step
from app.core.auth.user.services.super_admin_mfa_provider import (
    DatabaseSuperAdminRecoveryFactorProvider,
    begin_pending_enrollment,
    confirm_pending_enrollment,
)
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError
from app.extensions import db


@pytest.fixture()
def ready(app, monkeypatch):
    # Test app starts in production-inert mode, then this test alone enables
    # recovery. This must never mutate global deployment configuration.
    assert _PROVIDER_EXTENSION_KEY not in app.extensions
    app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"] = "G04-isolated-recovery-pepper-" * 3
    app.config["INTEGRATION_ENCRYPTION_KEY_VERSION"] = 1
    app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "required"
    app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_MAX_FAILURES"] = 2
    register_super_admin_recovery_provider(app)
    assert isinstance(app.extensions[_PROVIDER_EXTENSION_KEY],
                      DatabaseSuperAdminRecoveryFactorProvider)
    assert extensions.redis_client is not None
    extensions.redis_client.ping()
    monkeypatch.setattr(reset, "queue_password_security_notice", lambda **kwargs: None)
    return app


def _enroll(user):
    secret = begin_pending_enrollment(user)
    step = int(time.time() // 30)
    codes = confirm_pending_enrollment(user, totp_at_step(secret, step - 1),
                                       now=(step - 1) * 30)
    db.session.commit()
    return secret, codes


def _confirm(client, token, factor=None, password="New-Super-Admin-Secret-123!"):
    payload = {
        "token": token,
        "new_password": password,
        "confirm_password": password,
    }
    if factor is not None:
        payload["recovery_factor"] = factor
    return client.post("/api/v1/auth/password-reset/confirm", json=payload)


def test_g04_test_app_keeps_default_policy_off(app):
    assert app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] == "disabled"
    assert _PROVIDER_EXTENSION_KEY not in app.extensions


def test_g04_bootstrap_rejects_missing_recovery_pepper(app):
    app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"] = None
    app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "required"
    with pytest.raises(RuntimeError, match="pepper"):
        register_super_admin_recovery_provider(app)
    assert _PROVIDER_EXTENSION_KEY not in app.extensions


def test_g04_bootstrap_rejects_invalid_policy(app):
    app.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "optional"
    with pytest.raises(RuntimeError, match="policy"):
        register_super_admin_recovery_provider(app)
    assert _PROVIDER_EXTENSION_KEY not in app.extensions


def test_g04_real_provider_registers_only_with_explicit_policy(ready, make_user):
    # Staged mode permits an unenrolled account; required mode fails closed.
    ready.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "if_enrolled"
    sa = make_user(role=Role.SUPER_ADMIN)
    assert resolve_super_admin_recovery_factor_state(sa).required is False
    _, _codes = _enroll(sa)
    state = resolve_super_admin_recovery_factor_state(sa)
    assert state.required is True
    assert state.provider_name == "database-totp-v1"
    assert isinstance(state.binding_digest, str) and len(state.binding_digest) == 64


def test_g04_missing_factor_is_denied_then_recovery_code_resets(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    old_version = sa.token_version
    issued = reset._issue_password_reset_token(sa)
    assert issued.recovery_factor_required is True
    assert _confirm(client, issued.token).status_code == 422
    assert sa.check_password("supersecret")
    response = _confirm(client, issued.token, codes[0])
    assert response.status_code == 200
    assert response.get_json()["data"]["reauthentication_required"] is True
    db.session.refresh(sa)
    assert sa.check_password("New-Super-Admin-Secret-123!")
    assert sa.token_version == old_version + 1
    assert DatabaseSuperAdminRecoveryFactorProvider().verify_recovery_factor(sa, codes[0]) is False
    assert _confirm(client, issued.token, codes[1]).status_code == 422
    assert issued.token not in str(response.get_json())


def test_g04_real_totp_factor_resets(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    secret, _codes = _enroll(sa)
    issued = reset._issue_password_reset_token(sa)
    code = totp_at_step(secret, int(time.time() // 30))
    response = _confirm(client, issued.token, code)
    assert response.status_code == 200
    assert sa.check_password("New-Super-Admin-Secret-123!")


def test_g04_reset_revokes_old_access_and_refresh_authority(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    old = create_access_token(identity=str(sa.id), additional_claims={
        "role": Role.SUPER_ADMIN.value, "token_version": sa.token_version,
    })
    issued = reset._issue_password_reset_token(sa)
    assert _confirm(client, issued.token, codes[0]).status_code == 200
    denied = client.get("/api/v1/auth/clinic-context", headers={
        "Authorization": "Bearer " + old,
    })
    assert denied.status_code == 401
    ready.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "required"
    login = client.post("/api/v1/auth/login", json={
        "email": sa.email, "password": "New-Super-Admin-Secret-123!",
    })
    assert login.status_code == 200
    assert login.get_json()["data"]["mfa_required"] is True
    assert "access_token" not in str(login.get_json())


def test_g04_bad_factor_attempt_limit_discards_reset_link(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    issued = reset._issue_password_reset_token(sa)
    assert _confirm(client, issued.token, "A" * 32).status_code == 422
    assert _confirm(client, issued.token, "B" * 32).status_code == 422
    assert _confirm(client, issued.token, codes[0]).status_code == 422
    assert sa.check_password("supersecret")


def test_g04_binding_rotation_invalidates_issued_reset(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    issued = reset._issue_password_reset_token(sa)
    row = db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=sa.id).one()
    row.binding_id = "g04-new-enrollment-binding-for-rotation"
    db.session.commit()
    assert _confirm(client, issued.token, codes[0]).status_code == 422
    assert _confirm(client, issued.token, codes[1]).status_code == 422
    assert sa.check_password("supersecret")


def test_g04_old_email_only_reset_cannot_survive_policy_tightening(
        ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    ready.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "disabled"
    issued = reset._issue_password_reset_token(sa)
    assert issued.recovery_factor_required is False
    ready.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "required"
    assert _confirm(client, issued.token, codes[0]).status_code == 422
    assert sa.check_password("supersecret")


def test_g04_required_recovery_without_enrollment_fails_closed(ready, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    with pytest.raises(ServiceUnavailableError):
        reset._issue_password_reset_token(sa)


def test_g04_required_recovery_without_provider_fails_closed(ready, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    ready.extensions.pop(_PROVIDER_EXTENSION_KEY)
    with pytest.raises(ServiceUnavailableError):
        reset._issue_password_reset_token(sa)


def test_g04_ordinary_user_still_resets_without_mfa(ready, client, make_user):
    doctor = make_user(role=Role.DOCTOR)
    issued = reset._issue_password_reset_token(doctor)
    assert issued.recovery_factor_required is False
    response = _confirm(client, issued.token)
    assert response.status_code == 200
    assert doctor.check_password("New-Super-Admin-Secret-123!")


def test_g04_bound_reset_remains_protected_after_policy_downgrade(
        ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    issued = reset._issue_password_reset_token(sa)
    ready.config["AUTH_SUPER_ADMIN_PASSWORD_RESET_MFA_POLICY"] = "disabled"
    assert _confirm(client, issued.token).status_code == 422
    assert _confirm(client, issued.token, codes[0]).status_code == 200
