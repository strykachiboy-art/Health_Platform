from __future__ import annotations

import pytest

from app.core.resilience.circuit_breaker_config import (
    CircuitBreakerSettings,
)


def test_defaults_are_conservative():
    settings = CircuitBreakerSettings()

    assert settings.enabled is True
    assert settings.failure_threshold == 5
    assert settings.open_seconds == 30.0
    assert settings.probe_lease_seconds == 60.0
    assert settings.state_ttl_seconds == 3600


def test_from_mapping_accepts_environment_boolean_string():
    settings = CircuitBreakerSettings.from_mapping(
        {
            "CIRCUIT_BREAKER_ENABLED": "false",
            "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 7,
            "CIRCUIT_BREAKER_OPEN_SECONDS": 45,
            "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS": 12,
            "CIRCUIT_BREAKER_STATE_TTL_SECONDS": 7200,
        }
    )

    assert settings.enabled is False
    assert settings.failure_threshold == 7
    assert settings.open_seconds == 45.0
    assert settings.probe_lease_seconds == 12.0
    assert settings.state_ttl_seconds == 7200


@pytest.mark.parametrize(
    "kwargs",
    [
        {"enabled": "maybe"},
        {"failure_threshold": True},
        {"failure_threshold": 0},
        {"open_seconds": 0},
        {"open_seconds": float("inf")},
        {"probe_lease_seconds": -1},
        {"state_ttl_seconds": True},
        {
            "open_seconds": 30,
            "probe_lease_seconds": 10,
            "state_ttl_seconds": 40,
        },
    ],
)
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        CircuitBreakerSettings(**kwargs)
