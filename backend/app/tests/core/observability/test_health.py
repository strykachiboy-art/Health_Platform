from __future__ import annotations

import base64

from app.core.observability import health


def test_liveness_is_public(
    client,
):
    response = client.get(
        "/health/live"
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body == {
        "success": True,
        "status": "ok",
    }


def test_readiness_reports_ready_when_dependencies_are_healthy(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        health,
        "collect_readiness",
        lambda: {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "ready": True,
            "database": {
                "healthy": True,
                "latency_ms": 1.0,
            },
            "redis": {
                "healthy": True,
                "latency_ms": 1.0,
            },
        },
    )

    response = client.get(
        "/health/ready"
    )

    assert response.status_code == 200

    body = response.get_json()

    assert body == {
        "success": True,
        "status": "ready",
    }


def test_readiness_returns_service_unavailable_when_dependency_fails(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        health,
        "collect_readiness",
        lambda: {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "ready": False,
            "database": {
                "healthy": False,
                "latency_ms": 1.0,
            },
            "redis": {
                "healthy": True,
                "latency_ms": 1.0,
            },
        },
    )

    response = client.get(
        "/health/ready"
    )

    assert response.status_code == 503

    body = response.get_json()

    assert body == {
        "success": False,
        "status": "not_ready",
    }


def test_readiness_does_not_expose_dependency_details(
    client,
    monkeypatch,
):
    monkeypatch.setattr(
        health,
        "collect_readiness",
        lambda: {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "ready": False,
            "database": {
                "healthy": False,
                "latency_ms": 1.0,
            },
            "redis": {
                "healthy": False,
                "latency_ms": 1.0,
            },
        },
    )

    response = client.get(
        "/health/ready"
    )

    body = response.get_json()

    assert body == {
        "success": False,
        "status": "not_ready",
    }

def _mock_healthy_readiness_dependencies(monkeypatch):
    healthy = {
        "healthy": True,
        "latency_ms": 1.0,
    }
    monkeypatch.setattr(
        health,
        "_check_database",
        lambda: dict(healthy),
    )
    monkeypatch.setattr(
        health,
        "_check_redis",
        lambda: dict(healthy),
    )


def test_collect_readiness_fails_when_backup_key_is_missing(
    app,
    monkeypatch,
):
    app.config.pop("BACKUP_ENCRYPTION_KEY", None)
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    _mock_healthy_readiness_dependencies(monkeypatch)

    result = health.collect_readiness()

    assert result["ready"] is False
    assert result["backup_encryption"]["required"] is True
    assert result["backup_encryption"]["healthy"] is False


def test_collect_readiness_fails_when_backup_key_version_is_invalid(
    app,
    monkeypatch,
):
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"b" * 32).decode("ascii")
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "0"
    _mock_healthy_readiness_dependencies(monkeypatch)

    result = health.collect_readiness()

    assert result["ready"] is False
    assert result["backup_encryption"]["required"] is True
    assert result["backup_encryption"]["healthy"] is False


def test_collect_readiness_accepts_valid_backup_key(
    app,
    monkeypatch,
):
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"b" * 32).decode("ascii")
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"i" * 32).decode("ascii")
    )
    _mock_healthy_readiness_dependencies(monkeypatch)

    result = health.collect_readiness()

    assert result["ready"] is True
    assert result["backup_encryption"]["required"] is True
    assert result["backup_encryption"]["healthy"] is True
