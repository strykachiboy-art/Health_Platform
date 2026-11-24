"""G05: focused real JWT/DB endpoint tests, no production activation."""
from __future__ import annotations

import time

from app.core.auth.user.models.super_admin_mfa_model import (
    SuperAdminMfaEnrollment, SuperAdminMfaRecoveryCode,
)
from app.core.auth.user.services.super_admin_mfa_otp import totp_at_step
from app.core.enums.role_enums import Role
from app.extensions import db


_BASE = "/api/v1/auth/mfa/enrollment"


def _ready(app):
    app.config["AUTH_SUPER_ADMIN_MFA_ENROLLMENT_ENABLED"] = True
    app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"] = "g05-only-fixture-pepper-" * 3
    app.config["INTEGRATION_ENCRYPTION_KEY_VERSION"] = 1
    app.config["AUTH_SUPER_ADMIN_LOGIN_MFA_POLICY"] = "disabled"


def test_disabled_status_never_queries_missing_schema(app, client, make_user, auth_headers_for):
    user = make_user(role=Role.SUPER_ADMIN)
    res = client.get(_BASE + "/status", headers=auth_headers_for(user))
    assert res.status_code == 200
    assert res.json["data"] == {"enrollment_enabled": False, "enrolled": None}


def test_disabled_begin_fails_closed(app, client, make_user, auth_headers_for):
    user = make_user(role=Role.SUPER_ADMIN)
    res = client.post(_BASE + "/begin", headers=auth_headers_for(user), json={"current_password": "supersecret"})
    assert res.status_code == 503


def test_enrollment_requires_authentication(app, client):
    _ready(app)
    assert client.get(_BASE + "/status").status_code == 401
    assert client.post(_BASE + "/begin", json={"current_password": "supersecret"}).status_code == 401
    assert client.post(_BASE + "/confirm", json={"code": "123456"}).status_code == 401


def test_normal_role_cannot_enroll(app, client, make_user, auth_headers_for):
    _ready(app)
    doctor = make_user(role=Role.DOCTOR)
    res = client.post(_BASE + "/begin", headers=auth_headers_for(doctor), json={"current_password": "supersecret"})
    assert res.status_code == 403


def test_wrong_password_denied_without_pending_record(app, client, make_user, auth_headers_for):
    _ready(app)
    user = make_user(role=Role.SUPER_ADMIN)
    res = client.post(_BASE + "/begin", headers=auth_headers_for(user), json={"current_password": "wrong"})
    assert res.status_code == 422
    assert db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=user.id).count() == 0


def test_enrollment_one_time_codes_and_session_revocation(app, client, make_user, auth_headers_for):
    _ready(app)
    user = make_user(role=Role.SUPER_ADMIN)
    headers = auth_headers_for(user)
    before = user.token_version
    begun = client.post(_BASE + "/begin", headers=headers, json={"current_password": "supersecret"})
    assert begun.status_code == 200, begun.get_data(as_text=True)
    secret = begun.json["data"]["setup_secret"]
    assert len(secret) == 52
    assert "setup_secret" not in str(client.get(_BASE + "/status", headers=headers).json)
    code = totp_at_step(secret, int(time.time()) // 30)
    confirmed = client.post(_BASE + "/confirm", headers=headers, json={"code": code})
    assert confirmed.status_code == 200, confirmed.get_data(as_text=True)
    payload = confirmed.json["data"]
    assert payload["reauthentication_required"] is True
    assert len(set(payload["recovery_codes"])) == 10
    assert len(set(payload["recovery_codes"])) == len(payload["recovery_codes"])
    codes = db.session.query(SuperAdminMfaRecoveryCode).all()
    assert len(codes) == 10
    assert not any(item.code_digest in payload["recovery_codes"] for item in codes)
    db.session.refresh(user)
    assert user.token_version == before + 1
    assert client.get(_BASE + "/status", headers=headers).status_code == 401


def test_invalid_confirmation_not_enrolled(app, client, make_user, auth_headers_for):
    _ready(app)
    user = make_user(role=Role.SUPER_ADMIN)
    headers = auth_headers_for(user)
    assert client.post(_BASE + "/begin", headers=headers, json={"current_password": "supersecret"}).status_code == 200
    res = client.post(_BASE + "/confirm", headers=headers, json={"code": "bad-code"})
    assert res.status_code == 422
    row = db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=user.id).one()
    assert row.confirmed_at is None


def test_google_only_super_admin_cannot_use_password_renewal(app, client, make_user, auth_headers_for):
    _ready(app)
    user = make_user(role=Role.SUPER_ADMIN)
    user.password_hash = None
    db.session.commit()
    res = client.post(_BASE + "/begin", headers=auth_headers_for(user), json={"current_password": "supersecret"})
    assert res.status_code == 422
    assert db.session.query(SuperAdminMfaEnrollment).filter_by(user_id=user.id).count() == 0
