"""Non-mutating Redis runtime memory-capacity preview.

All proposed limits and reserve budgets must be supplied explicitly.
This module does not collect live metrics, edit configuration, or scale
infrastructure. It validates an operator-provided runtime snapshot.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class RedisCapacityPreviewStatus(str, Enum):
    ACCEPTED = "accepted"
    REFUSED = "refused"


@dataclass(frozen=True, slots=True)
class RedisCapacityPreviewConfig:
    """Explicit target sizing and resource-reserve inputs.

    The caller must calibrate these values for the deployment. No sizing
    defaults are supplied. All values are bytes.
    """

    proposed_redis_maxmemory_bytes: int
    proposed_container_memory_limit_bytes: int
    redis_overhead_headroom_bytes: int
    minimum_linux_available_reserve_bytes: int
    minimum_docker_daemon_reserve_bytes: int

    def __post_init__(self) -> None:
        positive = (
            "proposed_redis_maxmemory_bytes",
            "proposed_container_memory_limit_bytes",
        )
        non_negative = (
            "redis_overhead_headroom_bytes",
            "minimum_linux_available_reserve_bytes",
            "minimum_docker_daemon_reserve_bytes",
        )

        for name in positive:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer number of bytes")

        for name in non_negative:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer number of bytes")


@dataclass(frozen=True, slots=True)
class RedisCapacityPreviewResult:
    """Preview result with estimates, never a command or actuation grant."""

    status: RedisCapacityPreviewStatus
    reason: str
    proposed_redis_maxmemory_bytes: int
    proposed_container_memory_limit_bytes: int
    container_memory_headroom_bytes: int
    additional_container_memory_commitment_bytes: int | None = None
    estimated_linux_available_after_growth_bytes: int | None = None
    observed_redis_maxmemory_bytes: int | None = None
    observed_container_memory_limit_bytes: int | None = None
    observed_container_memory_current_bytes: int | None = None

    @property
    def ok(self) -> bool:
        return self.status is RedisCapacityPreviewStatus.ACCEPTED

    @property
    def actuation_allowed(self) -> bool:
        """A capacity preview never authorizes live configuration changes."""
        return False


_INTEGER_FIELDS = {
    "docker_daemon_memory_total_bytes": True,
    "linux_memory_total_bytes": True,
    "linux_memory_available_bytes": False,
    "redis_container_memory_current_bytes": False,
    "redis_container_memory_limit_bytes": False,
    "redis_used_memory_bytes": False,
    "redis_used_memory_peak_bytes": False,
    "redis_maxmemory_bytes": False,
}


def preview_redis_capacity(
    snapshot: Mapping[str, Any],
    *,
    config: RedisCapacityPreviewConfig,
) -> RedisCapacityPreviewResult:
    """Evaluate explicit Redis sizing against an observed resource snapshot.

    The projected memory growth assumes the Redis container may eventually
    use its entire proposed memory ceiling. The available-memory estimate is
    therefore conservative, but it is not a guarantee: other workloads,
    transient allocations, and changes after sampling can reduce headroom.
    """
    if not isinstance(config, RedisCapacityPreviewConfig):
        raise TypeError("config must be a RedisCapacityPreviewConfig")

    if not isinstance(snapshot, Mapping):
        return _refused("invalid_runtime_snapshot", config)

    values: dict[str, int] = {}
    for name, positive in _INTEGER_FIELDS.items():
        value = snapshot.get(name)
        if type(value) is not int or value < 0 or (positive and value == 0):
            return _refused("missing_or_invalid_runtime_snapshot", config)
        values[name] = value

    for name in ("aof_enabled", "data_volume_mounted"):
        if type(snapshot.get(name)) is not bool:
            return _refused("missing_or_invalid_persistence_snapshot", config)

    daemon_total = values["docker_daemon_memory_total_bytes"]
    linux_total = values["linux_memory_total_bytes"]
    linux_available = values["linux_memory_available_bytes"]
    container_current = values["redis_container_memory_current_bytes"]
    current_container_limit = values["redis_container_memory_limit_bytes"]
    redis_used = values["redis_used_memory_bytes"]
    redis_peak = values["redis_used_memory_peak_bytes"]
    current_redis_maxmemory = values["redis_maxmemory_bytes"]

    if linux_available > linux_total:
        return _refused("linux_available_memory_exceeds_total", config)

    if redis_peak < redis_used:
        return _refused("redis_peak_below_current_usage", config)

    if (
        current_container_limit > 0
        and container_current > current_container_limit
    ):
        return _refused("current_container_memory_exceeds_current_limit", config)

    if snapshot["aof_enabled"] is not True:
        return _refused("redis_aof_not_enabled", config)

    if snapshot["data_volume_mounted"] is not True:
        return _refused("redis_data_volume_missing", config)

    proposed_redis_maxmemory = config.proposed_redis_maxmemory_bytes
    proposed_container_limit = config.proposed_container_memory_limit_bytes
    overhead = config.redis_overhead_headroom_bytes

    if proposed_redis_maxmemory < max(redis_used, redis_peak):
        return _refused(
            "proposed_redis_limit_below_observed_usage_or_peak",
            config,
            values=values,
        )

    if proposed_redis_maxmemory + overhead > proposed_container_limit:
        return _refused(
            "redis_limit_plus_headroom_exceeds_container_limit",
            config,
            values=values,
        )

    if config.minimum_docker_daemon_reserve_bytes >= daemon_total:
        return _refused(
            "docker_memory_reserve_exceeds_capacity",
            config,
            values=values,
        )

    if (
        proposed_container_limit
        > daemon_total - config.minimum_docker_daemon_reserve_bytes
    ):
        return _refused(
            "docker_memory_reserve_would_be_violated",
            config,
            values=values,
        )

    if config.minimum_linux_available_reserve_bytes > linux_available:
        return _refused(
            "linux_memory_reserve_exceeds_available_memory",
            config,
            values=values,
        )

    # Current container usage is already reflected in Linux MemAvailable.
    # Estimate only the additional growth up to the proposed container limit.
    growth = proposed_container_limit - container_current

    if growth <= 0:
        return _refused(
            "proposed_container_limit_not_above_current_usage",
            config,
            values=values,
        )

    available_budget = (
        linux_available - config.minimum_linux_available_reserve_bytes
    )
    if growth > available_budget:
        return _refused(
            "linux_available_memory_budget_exceeded",
            config,
            values=values,
        )

    return RedisCapacityPreviewResult(
        status=RedisCapacityPreviewStatus.ACCEPTED,
        reason="capacity_preview_accepted_no_changes_performed",
        proposed_redis_maxmemory_bytes=proposed_redis_maxmemory,
        proposed_container_memory_limit_bytes=proposed_container_limit,
        container_memory_headroom_bytes=(
            proposed_container_limit - proposed_redis_maxmemory
        ),
        additional_container_memory_commitment_bytes=growth,
        estimated_linux_available_after_growth_bytes=linux_available - growth,
        observed_redis_maxmemory_bytes=current_redis_maxmemory,
        observed_container_memory_limit_bytes=current_container_limit,
        observed_container_memory_current_bytes=container_current,
    )


def _refused(
    reason: str,
    config: RedisCapacityPreviewConfig,
    *,
    values: Mapping[str, int] | None = None,
) -> RedisCapacityPreviewResult:
    values = values or {}
    return RedisCapacityPreviewResult(
        status=RedisCapacityPreviewStatus.REFUSED,
        reason=reason,
        proposed_redis_maxmemory_bytes=config.proposed_redis_maxmemory_bytes,
        proposed_container_memory_limit_bytes=(
            config.proposed_container_memory_limit_bytes
        ),
        container_memory_headroom_bytes=(
            config.proposed_container_memory_limit_bytes
            - config.proposed_redis_maxmemory_bytes
        ),
        observed_redis_maxmemory_bytes=values.get("redis_maxmemory_bytes"),
        observed_container_memory_limit_bytes=values.get(
            "redis_container_memory_limit_bytes"
        ),
        observed_container_memory_current_bytes=values.get(
            "redis_container_memory_current_bytes"
        ),
    )