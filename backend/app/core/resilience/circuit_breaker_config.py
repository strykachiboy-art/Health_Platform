from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def _coerce_bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()

        if normalized in _TRUE_VALUES:
            return True

        if normalized in _FALSE_VALUES:
            return False

    raise ValueError(f"{name} must be a boolean")


def _positive_int(value: Any, *, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")

    return value


def _positive_number(value: Any, *, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{name} must be a positive finite number")

    return float(value)


@dataclass(frozen=True)
class CircuitBreakerSettings:
    enabled: bool = True
    failure_threshold: int = 5
    open_seconds: float = 30.0
    probe_lease_seconds: float = 60.0
    state_ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        enabled = _coerce_bool(
            self.enabled,
            name="enabled",
        )
        failure_threshold = _positive_int(
            self.failure_threshold,
            name="failure_threshold",
        )
        open_seconds = _positive_number(
            self.open_seconds,
            name="open_seconds",
        )
        probe_lease_seconds = _positive_number(
            self.probe_lease_seconds,
            name="probe_lease_seconds",
        )
        state_ttl_seconds = _positive_int(
            self.state_ttl_seconds,
            name="state_ttl_seconds",
        )

        if state_ttl_seconds <= (
            open_seconds + probe_lease_seconds
        ):
            raise ValueError(
                "state_ttl_seconds must exceed open_seconds "
                "plus probe_lease_seconds"
            )

        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(
            self,
            "failure_threshold",
            failure_threshold,
        )
        object.__setattr__(
            self,
            "open_seconds",
            open_seconds,
        )
        object.__setattr__(
            self,
            "probe_lease_seconds",
            probe_lease_seconds,
        )
        object.__setattr__(
            self,
            "state_ttl_seconds",
            state_ttl_seconds,
        )

    @classmethod
    def from_mapping(
        cls,
        config: Mapping[str, Any],
    ) -> "CircuitBreakerSettings":
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")

        return cls(
            enabled=config.get(
                "CIRCUIT_BREAKER_ENABLED",
                True,
            ),
            failure_threshold=config.get(
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD",
                5,
            ),
            open_seconds=config.get(
                "CIRCUIT_BREAKER_OPEN_SECONDS",
                30,
            ),
            probe_lease_seconds=config.get(
                "CIRCUIT_BREAKER_PROBE_LEASE_SECONDS",
                60,
            ),
            state_ttl_seconds=config.get(
                "CIRCUIT_BREAKER_STATE_TTL_SECONDS",
                3600,
            ),
        )
