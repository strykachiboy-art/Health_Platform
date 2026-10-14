from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy import text

from app import extensions
from app.core.backup.backup_encryption import (
    validate_backup_encryption_configuration,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_database() -> dict[str, object]:
    started_at = time.perf_counter()

    try:
        with extensions.db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        healthy = True
    except Exception:
        healthy = False

    return {
        "healthy": healthy,
        "latency_ms": (
            time.perf_counter() - started_at
        ) * 1000.0,
    }


def _check_redis() -> dict[str, object]:
    started_at = time.perf_counter()

    try:
        client = extensions.redis_client

        if client is None:
            raise RuntimeError(
                "Redis client is not initialized"
            )

        client.ping()
        healthy = True
    except Exception:
        healthy = False

    return {
        "healthy": healthy,
        "latency_ms": (
            time.perf_counter() - started_at
        ) * 1000.0,
    }


def _check_backup_encryption() -> dict[str, object]:
    started_at = time.perf_counter()

    try:
        validate_backup_encryption_configuration()
        healthy = True
    except Exception:
        # Do not expose key material or detailed configuration failures
        # through readiness status.
        healthy = False

    return {
        "required": True,
        "healthy": healthy,
        "latency_ms": (
            time.perf_counter() - started_at
        ) * 1000.0,
    }


def collect_readiness() -> dict[str, object]:
    database = _check_database()
    redis = _check_redis()
    backup_encryption = _check_backup_encryption()

    ready = bool(
        database["healthy"]
        and redis["healthy"]
        and backup_encryption["healthy"]
    )

    return {
        "timestamp": _utcnow(),
        "ready": ready,
        "database": database,
        "redis": redis,
        "backup_encryption": backup_encryption,
    }
