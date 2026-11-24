"""Dedicated disposable PostgreSQL gate for the existing Super Admin MFA provider.

When run outside the SA-SEC-01 G02B disposable-Postgres runner, this module
skips rather than using the application's configured development database.
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.fernet import Fernet
from flask import Flask

from app.core.auth.user.models.user_model import User
from app.core.auth.user.services.super_admin_mfa_otp import (
    generate_totp_secret,
    recovery_code_digest,
    totp_at_step,
)
from app.core.auth.user.services.super_admin_mfa_provider import (
    DatabaseSuperAdminRecoveryFactorProvider,
)
from app.core.enums.role_enums import Role
from app.core.security.encryption import encrypt_credentials
from app.extensions import db


BASE_DIR = Path(__file__).resolve().parents[4]  # backend/app -> backend
MIGRATIONS = BASE_DIR.parent / "migrations" / "versions"
ENROLLMENT = "super_admin_mfa_enrollments"
RECOVERY = "super_admin_mfa_recovery_codes"


def _migration():
    path = MIGRATIONS / "bc12026a0001_super_admin_mfa_enrollment.py"
    spec = importlib.util.spec_from_file_location("sa_sec_01_g02b_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "bc12026a0001"
    assert module.down_revision == "f7a9e20bd613"
    return module


@pytest.fixture(scope="module")
def pg_mfa():
    url = os.environ.get("SA_SEC_01_G02B_DISPOSABLE_PG_URL", "")
    if not url:
        pytest.skip("Disposable PostgreSQL gate is not enabled")
    parsed = sa.engine.make_url(url)
    assert parsed.get_backend_name() == "postgresql"
    assert parsed.host in ("localhost", "127.0.0.1")
    # This database is supplied ONLY by the runner's owned disposable Docker container.
    assert parsed.database == "sa_sec_01_g02b"
    engine = sa.create_engine(url, pool_pre_ping=True)
    # Fail closed if the supplied database already has application tables.
    # Never migrate or seed a pre-existing database, even if someone manually
    # launches this test with the disposable gate environment variable set.
    with engine.connect() as conn:
        existing_tables = conn.execute(sa.text(
            "SELECT count(*) FROM pg_catalog.pg_tables "
            "WHERE schemaname = 'public'"
        )).scalar_one()
        assert existing_tables == 0, "Disposable PostgreSQL database is not empty"
    app = Flask("sa_sec_01_g02b_disposable_postgres")
    app.config.update(
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        INTEGRATION_ENCRYPTION_KEY=Fernet.generate_key().decode("ascii"),
        INTEGRATION_ENCRYPTION_KEY_VERSION=1,
        SUPER_ADMIN_MFA_RECOVERY_PEPPER="SA-SEC-01-G02B-DISPOSABLE-TEST-PEPPER-ONLY-2026",
    )
    db.init_app(app)
    try:
        # Create a minimal FK target; never touch any application/production DB.
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            conn.execute(sa.text("INSERT INTO users (id) VALUES (1)"))
            migration = _migration()
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
        # Register every mapped class; no other schema is created.
        __import__("app.models_registry")  # register all mapped classes
        with app.app_context():
            secret = generate_totp_secret()
            encrypted = encrypt_credentials({"super_admin_totp_secret": secret})
            pepper = app.config["SUPER_ADMIN_MFA_RECOVERY_PEPPER"]
            code = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
            with engine.begin() as conn:
                conn.execute(sa.text(
                    "INSERT INTO super_admin_mfa_enrollments "
                    "(id,user_id,encrypted_totp_secret,encryption_key_version,binding_id,"
                    " confirmed_at,pending_expires_at,last_accepted_step,created_at,updated_at) "
                    "VALUES (1,1,:encrypted,1,'pg-disposable-binding',now(),NULL,NULL,now(),now())"
                ), {"encrypted": encrypted})
                conn.execute(sa.text(
                    "INSERT INTO super_admin_mfa_recovery_codes "
                    "(id,enrollment_id,code_digest,consumed_at,created_at) "
                    "VALUES (1,1,:digest,NULL,now())"
                ), {"digest": recovery_code_digest(code=code, pepper=pepper)})
        yield app, engine, secret, code
    finally:
        engine.dispose()


def test_postgres_migration_matches_provider_models(pg_mfa):
    _, engine, _, _ = pg_mfa
    inspector = sa.inspect(engine)
    assert ENROLLMENT in inspector.get_table_names()
    assert RECOVERY in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns(ENROLLMENT)}
    assert columns == {
        "id", "user_id", "encrypted_totp_secret", "encryption_key_version",
        "binding_id", "confirmed_at", "pending_expires_at", "last_accepted_step",
        "created_at", "updated_at",
    }
    fks = inspector.get_foreign_keys(RECOVERY)
    assert len(fks) == 1 and fks[0]["referred_table"] == ENROLLMENT
    assert fks[0]["options"].get("ondelete") == "CASCADE"
    idx = inspector.get_indexes(ENROLLMENT)
    assert any(i["unique"] and i["column_names"] == ["user_id"] for i in idx)
    with engine.begin() as conn:
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                conn.execute(sa.text(
                    "INSERT INTO super_admin_mfa_enrollments "
                    "(user_id,encrypted_totp_secret,encryption_key_version,binding_id,"
                    " created_at,updated_at) "
                    "VALUES (1,'duplicate',1,'duplicate',now(),now())"
                ))


def _concurrent_factor_attempts(app, secret, code, *, is_totp):
    # Each request uses a distinct Flask application context and SQLAlchemy
    # scoped session; only the database row lock is shared.
    barrier = threading.Barrier(2)

    def worker():
        with app.app_context():
            try:
                user = User(id=1, role=Role.SUPER_ADMIN, is_active=True)
                provider = DatabaseSuperAdminRecoveryFactorProvider()
                barrier.wait(timeout=15)
                success = provider.verify_recovery_factor(user, code)
                db.session.commit()
                return success
            except BaseException:
                db.session.rollback()
                raise
            finally:
                db.session.remove()

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(worker) for _ in range(2)]
        return [f.result(timeout=30) for f in tasks]


def test_postgres_rejects_parallel_totp_step_replay(pg_mfa):
    app, engine, secret, _ = pg_mfa
    # Stay within the same step across both requests; no fixed clock injection
    # into production methods. A one-step skew is accepted by the provider.
    now = int(time.time())
    phase = now % 30
    if phase > 12:
        # Enter the beginning of the next TOTP period rather than allowing a
        # step to expire between the two real database requests.
        time.sleep(33 - phase)
    now = int(time.time())
    assert 0 <= now % 30 <= 14, "TOTP concurrency test clock window drifted"
    code = totp_at_step(secret, now // 30)
    results = _concurrent_factor_attempts(app, secret, code, is_totp=True)
    assert sorted(results) == [False, True]
    with engine.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT last_accepted_step FROM super_admin_mfa_enrollments WHERE id=1"
        )).scalar_one() == now // 30


def test_postgres_rejects_parallel_recovery_code_reuse(pg_mfa):
    app, engine, secret, code = pg_mfa
    results = _concurrent_factor_attempts(app, secret, code, is_totp=False)
    assert sorted(results) == [False, True]
    with engine.connect() as conn:
        assert conn.execute(sa.text(
            "SELECT COUNT(*) FROM super_admin_mfa_recovery_codes "
            "WHERE id=1 AND consumed_at IS NOT NULL"
        )).scalar_one() == 1
