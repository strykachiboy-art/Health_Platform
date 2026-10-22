from __future__ import annotations

import os
from datetime import timedelta

from dotenv import load_dotenv


load_dotenv()


basedir = os.path.abspath(
    os.path.dirname(__file__)
)


class Config:
    SECRET_KEY = os.environ.get(
        "SECRET_KEY",
        "dev-secret-change-me",
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///"
        + os.path.join(
            basedir,
            "..",
            "clinic.db",
        ),
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    JWT_SECRET_KEY = os.environ.get(
        "JWT_SECRET_KEY",
        "dev-jwt-secret-change-me",
    )

    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        hours=1
    )

    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=30
    )

    GOOGLE_CLIENT_ID = os.environ.get(
        "GOOGLE_CLIENT_ID"
    )

    GOOGLE_CLIENT_SECRET = os.environ.get(
        "GOOGLE_CLIENT_SECRET"
    )

    GOOGLE_REDIRECT_URI = os.environ.get(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:5000/api/v1/auth/google/callback",
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    REDIS_URL = os.environ.get(
        "REDIS_URL",
        "redis://localhost:6379/0",
    )

    CELERY_BROKER_URL = REDIS_URL

    CELERY_RESULT_BACKEND = REDIS_URL

    # ------------------------------------------------------------------
    # Upload / Storage
    # ------------------------------------------------------------------

    MAX_CONTENT_LENGTH = (
        16 * 1024 * 1024
    )

    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        "uploads",
    )

    STORAGE_ROOT = os.environ.get(
        "STORAGE_ROOT"
    )

    STORAGE_PUBLIC_BASE_URL = os.environ.get(
        "STORAGE_PUBLIC_BASE_URL"
    )

    MAX_PROFILE_IMAGE_SIZE_BYTES = (
        5 * 1024 * 1024
    )

    # ------------------------------------------------------------------
    # Backup & Disaster Recovery
    # ------------------------------------------------------------------

    BACKUP_ROOT = os.environ.get(
        "BACKUP_ROOT"
    )

    BACKUP_RETENTION_DAYS = int(
        os.environ.get(
            "BACKUP_RETENTION_DAYS",
            "30",
        )
    )

    BACKUP_MIN_RECOVERY_POINTS = int(
        os.environ.get(
            "BACKUP_MIN_RECOVERY_POINTS",
            "1",
        )
    )

    BACKUP_ENCRYPTION_KEY = os.environ.get(
        "BACKUP_ENCRYPTION_KEY"
    )

    BACKUP_ENCRYPTION_KEY_VERSION = os.environ.get(
        "BACKUP_ENCRYPTION_KEY_VERSION",
        "1",
    )

    # ------------------------------------------------------------------
    # Flask-Limiter
    # ------------------------------------------------------------------

    RATELIMIT_STORAGE_URI = REDIS_URL

    RATELIMIT_HEADERS_ENABLED = True
    # Configurable per-IP ceiling for the public payment-provider webhook.
    # Tune against observed provider delivery bursts before deployment.
    PAYMENT_WEBHOOK_RATE_LIMIT = os.environ.get(
        "PAYMENT_WEBHOOK_RATE_LIMIT",
        "300 per minute",
    )

    # ------------------------------------------------------------------
    # HTTP Security
    # ------------------------------------------------------------------

    CORS_ALLOWED_ORIGINS = os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "*",
    )

    SECURITY_HEADERS_ENABLED = True

    SECURITY_HSTS_ENABLED = False

    TRUSTED_PROXY_HOPS = 0

    # ------------------------------------------------------------------
    # Authentication Abuse Protection
    # ------------------------------------------------------------------

    AUTH_REGISTER_RATE_LIMIT = (
        "10 per minute"
    )

    AUTH_LOGIN_RATE_LIMIT = (
        "5 per minute"
    )

    AUTH_GOOGLE_RATE_LIMIT = (
        "10 per minute"
    )

    AUTH_REFRESH_RATE_LIMIT = (
        "20 per minute"
    )

    AUTH_LOGOUT_RATE_LIMIT = (
        "20 per minute"
    )

    # ------------------------------------------------------------------
    # AI
    # ------------------------------------------------------------------

    OPENAI_API_KEY = os.environ.get(
        "OPENAI_API_KEY"
    )

    OPENAI_MODEL = "gpt-4o-mini"
    AI_PROVIDER_TIMEOUT_SECONDS = 30

    AI_PROVIDER = "openai"

    AI_LOAD_TEST_MODE = False

    AI_LOAD_TEST_RATE_LIMIT = (
        "1000 per second"
    )

    # ------------------------------------------------------------------
    # HIE Endpoint Policy
    # ------------------------------------------------------------------
    # Comma-separated operator-approved DNS hostnames. Empty fails closed.
    HIE_ALLOWED_ENDPOINT_HOSTS = os.environ.get(
        "HIE_ALLOWED_ENDPOINT_HOSTS",
        "",
    )

    # ------------------------------------------------------------------
    # Integration Encryption
    # ------------------------------------------------------------------

    INTEGRATION_ENCRYPTION_KEY = os.environ.get(
        "INTEGRATION_ENCRYPTION_KEY"
    )

    INTEGRATION_ENCRYPTION_KEY_VERSION = os.environ.get(
        "INTEGRATION_ENCRYPTION_KEY_VERSION",
        "1",
    )

    INTEGRATION_ENCRYPTION_LEGACY_KEYS = os.environ.get(
        "INTEGRATION_ENCRYPTION_LEGACY_KEYS",
        "{}",
    )


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    SECRET_KEY = os.environ.get(
        "SECRET_KEY"
    )

    JWT_SECRET_KEY = os.environ.get(
        "JWT_SECRET_KEY"
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL"
    )

    REDIS_URL = os.environ.get(
        "REDIS_URL"
    )

    CELERY_BROKER_URL = REDIS_URL

    CELERY_RESULT_BACKEND = REDIS_URL

    CELERY_TASK_ACKS_LATE = True
    CELERY_TASK_REJECT_ON_WORKER_LOST = True
    CELERY_WORKER_PREFETCH_MULTIPLIER = 1

    RATELIMIT_STORAGE_URI = REDIS_URL

    CORS_ALLOWED_ORIGINS = os.environ.get(
        "CORS_ALLOWED_ORIGINS"
    )

    SECURITY_HEADERS_ENABLED = True

    SECURITY_HSTS_ENABLED = True

    TRUSTED_PROXY_HOPS = os.environ.get(
        "TRUSTED_PROXY_HOPS", "0"
    )

    # Provisional two-replica database budget. Recalculate before increasing
    # replicas, worker concurrency, pool size, or overflow.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": int(os.environ.get("SQLALCHEMY_POOL_SIZE", "3")),
        "max_overflow": int(os.environ.get("SQLALCHEMY_MAX_OVERFLOW", "2")),
        "pool_timeout": float(
            os.environ.get("SQLALCHEMY_POOL_TIMEOUT_SECONDS", "5")
        ),
        "pool_recycle": int(
            os.environ.get("SQLALCHEMY_POOL_RECYCLE_SECONDS", "1800")
        ),
        "pool_pre_ping": True,
    }

    DEBUG = False


class TestingConfig(Config):
    TESTING = True

    # Synthetic hosts used by deterministic HIE tests only.
    HIE_ALLOWED_ENDPOINT_HOSTS = (
        "fhir.partnerhealth.org",
        "api.epic.com",
    )

    JWT_SECRET_KEY = os.environ.get(
        "TEST_JWT_SECRET_KEY",
        "clinic-system-pro-test-jwt-secret-2026",
    )

    # ------------------------------------------------------------------
    # Isolated Test Database
    # ------------------------------------------------------------------

    SQLALCHEMY_DATABASE_URI = (
        "sqlite:///:memory:"
    )

    # ------------------------------------------------------------------
    # Isolated Test Redis
    # ------------------------------------------------------------------

    REDIS_URL = os.environ.get(
        "TEST_REDIS_URL",
        "redis://localhost:6379/15",
    )

    CELERY_BROKER_URL = REDIS_URL

    CELERY_RESULT_BACKEND = REDIS_URL

    RATELIMIT_STORAGE_URI = REDIS_URL

    RATELIMIT_ENABLED = False

    # ------------------------------------------------------------------
    # Test Encryption Key
    # ------------------------------------------------------------------

    INTEGRATION_ENCRYPTION_KEY = (
        "YOUR_VALID_FERNET_TEST_KEY_HERE"
    )

    MAX_PROFILE_IMAGE_SIZE_BYTES = (
        5 * 1024 * 1024
    )


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
