"""SA-SEC-01 G03: real Redis-backed MFA contract and anti-bypass tests.

The runner provisions its own loopback-only Redis 8 and uses the application's
existing disposable in-memory test database. No production MFA activation.
"""
from __future__ import annotations

import time

import pytest
from flask_jwt_extended import create_access_token, decode_token

from app.core.auth.user.services.super_admin_mfa_provider import (
    begin_pending_enrollment,
    confirm_pending_enrollment,
)
from app.core.auth.user.services.super_admin_mfa_otp import totp_at_step
from app.core.auth.user.services.super_admin_login_mfa_service import (
    login_mfa_policy,
    maybe_start_login_challenge,
    require_token_issuance_authority,
)
from app.core.auth.user.services.user_service import issue_auth_tokens
from app.core.enums.role_enums import Role
from app.core.exceptions import ServiceUnavailableError, ValidationError
from app.extensions import db
from app import extensions


@pytest.fixture()
def ready(app):
    app.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "required"
    app.config["AUTH_SUPER_ADMIN_LOGIN_MFA_CHALLENGE_TTL_SECONDS"] = 300
    app.config["INTEGRATION_ENCRYPTION_KEY_VERSION"] = 1
    app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"] = "G03-disposable-recovery-pepper-only-" * 3
    assert extensions.redis_client is not None
    extensions.redis_client.ping()
    return app


def _enroll(user):
    # Enrollment confirmation accepts a previous step; login must present a
    # different current code because successful TOTP steps are single-use.
    secret = begin_pending_enrollment(user)
    step = int(time.time() // 30)
    codes = confirm_pending_enrollment(user, totp_at_step(secret, step - 1), now=(step - 1) * 30)
    db.session.commit()
    return secret, codes


def _password_login(client, user):
    return client.post("/api/v1/auth/login", json={"email": user.email, "password": "supersecret"})


def _complete(client, challenge, factor):
    return client.post("/api/v1/auth/mfa/complete", json={
        "challenge_token": challenge,
        "factor": factor,
    })


def test_super_admin_password_login_requires_challenge_before_tokens(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    secret, _ = _enroll(sa)
    response = _password_login(client, sa)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["mfa_required"] is True
    assert data["method"] == "authenticator_or_recovery_code"
    assert 40 < len(data["challenge_token"]) < 256
    assert "access_token" not in data and "refresh_token" not in data
    assert sa.last_login_at is None
    assert secret not in str(data)


def test_super_admin_mfa_challenge_success_and_refresh(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    secret, _ = _enroll(sa)
    pending = _password_login(client, sa).get_json()["data"]
    factor = totp_at_step(secret, int(time.time() // 30))
    response = _complete(client, pending["challenge_token"], factor)
    assert response.status_code == 200
    data = response.get_json()["data"]
    access = decode_token(data["access_token"])
    refresh = decode_token(data["refresh_token"])
    assert access["sa_mfa_verified"] is True
    assert refresh["sa_mfa_verified"] is True
    assert access["sa_mfa_binding"] == refresh["sa_mfa_binding"]
    assert refresh["role"] == Role.SUPER_ADMIN.value
    assert sa.last_login_at is not None
    rotated = client.post("/api/v1/auth/refresh", headers={
        "Authorization": "Bearer " + data["refresh_token"],
    })
    assert rotated.status_code == 200
    assert decode_token(rotated.get_json()["data"]["access_token"])["sa_mfa_verified"] is True


def test_mfa_challenge_replay_and_wrong_factor_denied(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    secret, _ = _enroll(sa)
    challenge = _password_login(client, sa).get_json()["data"]["challenge_token"]
    bad = _complete(client, challenge, "ABCDEF")
    assert bad.status_code in (422, 429)
    assert "access_token" not in str(bad.get_json())
    replay = _complete(client, challenge, totp_at_step(secret, int(time.time() // 30)))
    assert replay.status_code in (422, 429)
    assert "access_token" not in str(replay.get_json())


def test_old_super_admin_token_cannot_bypass_required_mfa(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    legacy = create_access_token(identity=str(sa.id), additional_claims={
        "role": Role.SUPER_ADMIN.value, "token_version": sa.token_version,
    })
    response = client.get("/api/v1/auth/clinic-context", headers={
        "Authorization": "Bearer " + legacy,
    })
    assert response.status_code == 401


def test_role_promotion_token_cannot_bypass_required_mfa(ready, client, make_user):
    sa = make_user(role=Role.ADMIN)
    legacy = create_access_token(identity=str(sa.id), additional_claims={
        "role": Role.ADMIN.value, "token_version": sa.token_version,
    })
    sa.role = Role.SUPER_ADMIN
    _enroll(sa)
    response = client.get("/api/v1/auth/clinic-context", headers={
        "Authorization": "Bearer " + legacy,
    })
    assert response.status_code == 401


def test_mfa_enrollment_rotation_revokes_existing_tokens(ready, client, make_user):
    from app.core.auth.user.models.super_admin_mfa_model import SuperAdminMfaEnrollment
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    pending = _password_login(client, sa).get_json()["data"]
    result = _complete(client, pending["challenge_token"], codes[0])
    assert result.status_code == 200
    access = result.get_json()["data"]["access_token"]
    row = db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=sa.id).one()
    row.binding_id = "different-rotation-binding-not-valid-for-old-token"
    db.session.commit()
    response = client.get("/api/v1/auth/clinic-context", headers={
        "Authorization": "Bearer " + access,
    })
    assert response.status_code == 401


def test_single_use_recovery_code_cannot_complete_twice(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    first = _password_login(client, sa).get_json()["data"]["challenge_token"]
    assert _complete(client, first, codes[0]).status_code == 200
    second = _password_login(client, sa).get_json()["data"]["challenge_token"]
    assert _complete(client, second, codes[0]).status_code in (422, 429)


def test_required_policy_without_enrollment_fails_closed(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    response = _password_login(client, sa)
    assert response.status_code == 503
    assert "access_token" not in str(response.get_json())


def test_challenge_redis_outage_fails_closed(ready, client, make_user, monkeypatch):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    monkeypatch.setattr(extensions, "redis_client", None)
    response = _password_login(client, sa)
    assert response.status_code == 503
    assert "access_token" not in str(response.get_json())


def test_direct_token_issuance_denied_without_mfa_grant(ready, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    with pytest.raises(ValidationError, match="Additional privileged authentication"):
        issue_auth_tokens(sa)


def test_non_privileged_login_unaffected(ready, client, make_user):
    doctor = make_user(role=Role.DOCTOR)
    response = _password_login(client, doctor)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["role"] == Role.DOCTOR.value
    assert data["access_token"] and data["refresh_token"]
    assert "mfa_required" not in data


def test_staging_if_enrolled_allows_unenrolled_only(ready, client, make_user):
    ready.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "if_enrolled"
    sa = make_user(role=Role.SUPER_ADMIN)
    response = _password_login(client, sa)
    assert response.status_code == 200
    assert response.get_json()["data"]["access_token"]
    _enroll(sa)
    response = _password_login(client, sa)
    assert response.status_code == 200
    assert response.get_json()["data"]["mfa_required"] is True
    assert "access_token" not in response.get_json()["data"]


def test_disabled_policy_preserves_existing_login(ready, client, make_user):
    ready.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "disabled"
    sa = make_user(role=Role.SUPER_ADMIN)
    response = _password_login(client, sa)
    assert response.status_code == 200
    assert response.get_json()["data"]["access_token"]


def test_google_login_uses_same_challenge(ready, make_user, monkeypatch):
    from app.core.auth.user.schema.user_schema import GoogleUserInfoSchema
    from app.core.auth.user.services import google_auth_service
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    monkeypatch.setattr(google_auth_service, "_get_or_create_google_user", lambda info: (sa, False))
    info = GoogleUserInfoSchema(provider_user_id="provider-test", email=sa.email, email_verified=True)
    result = google_auth_service.authenticate_google_user(info)
    assert result["mfa_required"] is True
    assert "access_token" not in result and "refresh_token" not in result
    assert sa.last_login_at is None


def test_challenge_consumed_on_invalid_factor(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    token = _password_login(client, sa).get_json()["data"]["challenge_token"]
    assert _complete(client, token, "ABCDEF").status_code in (422, 429)
    assert _complete(client, token, "ABCDEF").status_code in (422, 429)


def test_wrong_schema_or_policy_does_not_issue_legacy_admin_tokens(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    ready.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "unexpected"
    response = _password_login(client, sa)
    assert response.status_code == 503
    assert "access_token" not in str(response.get_json())


def test_legacy_refresh_token_rejected_after_mfa_required(ready, client, make_user):
    from flask_jwt_extended import create_refresh_token
    sa = make_user(role=Role.SUPER_ADMIN)
    _enroll(sa)
    legacy = create_refresh_token(identity=str(sa.id), additional_claims={
        "token_version": sa.token_version,
    })
    response = client.post("/api/v1/auth/refresh", headers={
        "Authorization": "Bearer " + legacy,
    })
    assert response.status_code == 401


def test_expired_challenge_cannot_be_redeemed(ready, client, make_user):
    from app.core.auth.user.services.super_admin_login_mfa_service import _challenge_key
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    challenge = _password_login(client, sa).get_json()["data"]["challenge_token"]
    extensions.redis_client.delete(_challenge_key(challenge))
    result = _complete(client, challenge, codes[0])
    assert result.status_code in (422, 429)
    assert "access_token" not in str(result.get_json())


def test_token_version_change_invalidates_pending_challenge(ready, client, make_user):
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    challenge = _password_login(client, sa).get_json()["data"]["challenge_token"]
    sa.token_version += 1
    db.session.commit()
    result = _complete(client, challenge, codes[0])
    assert result.status_code in (422, 429)
    assert "access_token" not in str(result.get_json())


def test_verified_super_admin_token_can_change_clinic_context(ready, client, make_user, make_clinic):
    # Authenticated context changes must preserve the MFA-bound authority.
    from app.core.auth.user.services.clinic_context_service import select_clinic_context
    sa = make_user(role=Role.SUPER_ADMIN)
    _, codes = _enroll(sa)
    challenge = _password_login(client, sa).get_json()["data"]["challenge_token"]
    data = _complete(client, challenge, codes[0]).get_json()["data"]
    clinic = make_clinic()
    response = client.post("/api/v1/auth/clinic-context", json={"clinic_id": clinic.id}, headers={
        "Authorization": "Bearer " + data["access_token"],
    })
    assert response.status_code == 200
    replacement = response.get_json()["data"]
    assert decode_token(replacement["access_token"])["sa_mfa_verified"] is True
