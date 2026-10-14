from __future__ import annotations

import base64
import importlib

import pytest

import app.config as app_config
from app import create_app


def test_production_config_reads_secret_environment_values(
    monkeypatch,
):
    secret_key = "production-secret-test-value"
    jwt_secret_key = "production-jwt-secret-test-value"

    monkeypatch.setenv(
        "SECRET_KEY",
        secret_key,
    )
    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        jwt_secret_key,
    )

    config_module = importlib.reload(
        app_config
    )

    try:
        assert (
            config_module.ProductionConfig.SECRET_KEY
            == secret_key
        )

        assert (
            config_module.ProductionConfig.JWT_SECRET_KEY
            == jwt_secret_key
        )
    finally:
        importlib.reload(app_config)


@pytest.mark.parametrize(
    "missing_secret",
    [
        "SECRET_KEY",
        "JWT_SECRET_KEY",
    ],
)
def test_production_rejects_missing_required_secret(
    monkeypatch,
    missing_secret,
):
    monkeypatch.setenv(
        "SECRET_KEY",
        "production-secret-test-value",
    )

    monkeypatch.setenv(
        "JWT_SECRET_KEY",
        "production-jwt-secret-test-value",
    )

    monkeypatch.delenv(
        missing_secret,
        raising=False,
    )

    expected_name = (
        "SECRET_KEY"
        if missing_secret == "SECRET_KEY"
        else "JWT_SECRET_KEY"
    )

    with pytest.raises(
        RuntimeError,
        match=(
            rf"Production {expected_name} "
            r"is not configured"
        ),
    ):
        create_app("production")

def _set_valid_production_environment(monkeypatch):
    import base64

    integration_key = base64.urlsafe_b64encode(b'i' * 32).decode('ascii')
    monkeypatch.setenv('INTEGRATION_ENCRYPTION_KEY', integration_key)
    monkeypatch.setattr(
        app_config.ProductionConfig,
        'INTEGRATION_ENCRYPTION_KEY',
        integration_key,
    )

    monkeypatch.setenv("SECRET_KEY", "test-production-secret")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-production-jwt-secret")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://clinic:password@localhost:5432/clinic_system",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://clinic.example")
    monkeypatch.setenv(
        "BACKUP_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"b" * 32).decode("ascii"),
    )
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_VERSION", "1")


@pytest.mark.parametrize(
    "backup_key, version, expected_message",
    [
        (
            None,
            "1",
            "Production BACKUP_ENCRYPTION_KEY is not configured",
        ),
        (
            "ab",
            "1",
            "BACKUP_ENCRYPTION_KEY must be valid URL-safe base64",
        ),
        (
            "c2hvcnQ=",
            "1",
            "BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes",
        ),
        (
            "YmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmI=",
            "0",
            "BACKUP_ENCRYPTION_KEY_VERSION must be between 1 and 65535",
        ),
    ],
)
def test_production_rejects_invalid_backup_encryption_configuration(
    monkeypatch,
    backup_key,
    version,
    expected_message,
):
    _set_valid_production_environment(monkeypatch)

    if backup_key is None:
        monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    else:
        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", backup_key)

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY_VERSION", version)

    with pytest.raises(RuntimeError, match=expected_message):
        create_app("production")
