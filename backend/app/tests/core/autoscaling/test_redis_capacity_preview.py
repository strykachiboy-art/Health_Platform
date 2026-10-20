from __future__ import annotations

import pytest

from app.core.autoscaling.redis_capacity_preview import (
    RedisCapacityPreviewConfig,
    RedisCapacityPreviewStatus,
    preview_redis_capacity,
)


def config(**overrides):
    # Synthetic test values only; not deployment sizing recommendations.
    values = {
        "proposed_redis_maxmemory_bytes": 256_000_000,
        "proposed_container_memory_limit_bytes": 384_000_000,
        "redis_overhead_headroom_bytes": 128_000_000,
        "minimum_linux_available_reserve_bytes": 1_000_000_000,
        "minimum_docker_daemon_reserve_bytes": 1_000_000_000,
    }
    values.update(overrides)
    return RedisCapacityPreviewConfig(**values)


def snapshot(**overrides):
    # Snapshot fixture only. Production values must come from a fresh,
    # validated collection and an explicit runtime sizing proposal.
    values = {
        "docker_daemon_memory_total_bytes": 4_062_822_400,
        "linux_memory_total_bytes": 4_062_822_400,
        "linux_memory_available_bytes": 1_759_162_368,
        "redis_container_memory_current_bytes": 68_886_528,
        "redis_container_memory_limit_bytes": 0,
        "redis_used_memory_bytes": 2_377_648,
        "redis_used_memory_peak_bytes": 2_422_264,
        "redis_maxmemory_bytes": 0,
        "aof_enabled": True,
        "data_volume_mounted": True,
    }
    values.update(overrides)
    return values


def preview(runtime=None, policy=None):
    return preview_redis_capacity(
        snapshot() if runtime is None else runtime,
        config=config() if policy is None else policy,
    )


def test_explicit_proposal_fits_all_budgets_without_actuation():
    result = preview()

    assert result.status is RedisCapacityPreviewStatus.ACCEPTED
    assert result.ok is True
    assert result.reason == "capacity_preview_accepted_no_changes_performed"
    assert result.container_memory_headroom_bytes == 128_000_000
    assert result.additional_container_memory_commitment_bytes == (
        384_000_000 - 68_886_528
    )
    assert result.estimated_linux_available_after_growth_bytes == (
        1_759_162_368 - (384_000_000 - 68_886_528)
    )
    assert result.observed_redis_maxmemory_bytes == 0
    assert result.observed_container_memory_limit_bytes == 0
    assert result.actuation_allowed is False
    assert not hasattr(result, "scale_command")


def test_redis_limit_and_overhead_must_fit_container_limit():
    result = preview(
        policy=config(
            proposed_redis_maxmemory_bytes=300_000_000,
            redis_overhead_headroom_bytes=100_000_000,
            proposed_container_memory_limit_bytes=384_000_000,
        )
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "redis_limit_plus_headroom_exceeds_container_limit"
    assert result.actuation_allowed is False


def test_proposed_redis_limit_must_cover_observed_peak():
    result = preview(
        policy=config(proposed_redis_maxmemory_bytes=2_000_000)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "proposed_redis_limit_below_observed_usage_or_peak"


def test_proposed_redis_limit_must_cover_current_usage():
    result = preview(
        snapshot(
            redis_used_memory_bytes=300_000_000,
            redis_used_memory_peak_bytes=300_000_000,
        ),
        config(
            proposed_redis_maxmemory_bytes=256_000_000,
            proposed_container_memory_limit_bytes=512_000_000,
            redis_overhead_headroom_bytes=128_000_000,
        ),
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "proposed_redis_limit_below_observed_usage_or_peak"


def test_current_container_usage_must_be_below_existing_hard_limit():
    result = preview(
        snapshot(
            redis_container_memory_current_bytes=500_000_000,
            redis_container_memory_limit_bytes=400_000_000,
        )
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "current_container_memory_exceeds_current_limit"


def test_container_target_must_be_above_current_usage():
    result = preview(
        policy=config(
            proposed_redis_maxmemory_bytes=32_000_000,
            proposed_container_memory_limit_bytes=64_000_000,
            redis_overhead_headroom_bytes=32_000_000,
        )
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "proposed_container_limit_not_above_current_usage"


def test_proposal_must_preserve_linux_available_memory_reserve():
    result = preview(
        policy=config(minimum_linux_available_reserve_bytes=1_600_000_000)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "linux_available_memory_budget_exceeded"


def test_linux_reserve_cannot_exceed_observed_available_memory():
    result = preview(
        policy=config(minimum_linux_available_reserve_bytes=2_000_000_000)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "linux_memory_reserve_exceeds_available_memory"


def test_proposal_must_preserve_docker_daemon_reserve():
    result = preview(
        policy=config(minimum_docker_daemon_reserve_bytes=3_800_000_000)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "docker_memory_reserve_would_be_violated"


def test_docker_reserve_cannot_equal_or_exceed_total_memory():
    result = preview(
        policy=config(minimum_docker_daemon_reserve_bytes=4_062_822_400)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "docker_memory_reserve_exceeds_capacity"


def test_linux_available_memory_cannot_exceed_total():
    result = preview(
        snapshot(linux_memory_available_bytes=4_062_822_401)
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "linux_available_memory_exceeds_total"


@pytest.mark.parametrize(
    "field,value",
    [
        ("docker_daemon_memory_total_bytes", True),
        ("docker_daemon_memory_total_bytes", 0),
        ("linux_memory_total_bytes", -1),
        ("linux_memory_available_bytes", True),
        ("redis_container_memory_current_bytes", -1),
        ("redis_container_memory_limit_bytes", False),
        ("redis_used_memory_bytes", -1),
        ("redis_used_memory_peak_bytes", -1),
        ("redis_maxmemory_bytes", -1),
    ],
)
def test_invalid_runtime_integer_metrics_fail_closed(field, value):
    result = preview(snapshot(**{field: value}))

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "missing_or_invalid_runtime_snapshot"
    assert result.actuation_allowed is False


def test_missing_runtime_field_fails_closed():
    runtime = snapshot()
    del runtime["linux_memory_available_bytes"]

    result = preview(runtime)

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "missing_or_invalid_runtime_snapshot"


def test_non_mapping_runtime_snapshot_fails_closed():
    result = preview_redis_capacity(None, config=config())

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "invalid_runtime_snapshot"


def test_peak_cannot_be_below_current_redis_usage():
    result = preview(
        snapshot(
            redis_used_memory_bytes=10_000_000,
            redis_used_memory_peak_bytes=9_000_000,
        )
    )

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "redis_peak_below_current_usage"


@pytest.mark.parametrize(
    "field,value",
    [
        ("aof_enabled", None),
        ("aof_enabled", 1),
        ("data_volume_mounted", None),
        ("data_volume_mounted", "true"),
    ],
)
def test_missing_or_malformed_persistence_evidence_fails_closed(field, value):
    result = preview(snapshot(**{field: value}))

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "missing_or_invalid_persistence_snapshot"


def test_aof_must_be_enabled():
    result = preview(snapshot(aof_enabled=False))

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "redis_aof_not_enabled"


def test_persistent_data_volume_must_be_mounted():
    result = preview(snapshot(data_volume_mounted=False))

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.reason == "redis_data_volume_missing"


@pytest.mark.parametrize(
    "overrides",
    [
        {"proposed_redis_maxmemory_bytes": 0},
        {"proposed_redis_maxmemory_bytes": True},
        {"proposed_container_memory_limit_bytes": 0},
        {"proposed_container_memory_limit_bytes": 12.5},
        {"redis_overhead_headroom_bytes": -1},
        {"redis_overhead_headroom_bytes": False},
        {"minimum_linux_available_reserve_bytes": -1},
        {"minimum_docker_daemon_reserve_bytes": -1},
    ],
)
def test_invalid_capacity_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        config(**overrides)


def test_wrong_policy_type_raises_clear_error():
    with pytest.raises(TypeError):
        preview_redis_capacity(snapshot(), config=None)


def test_preview_reports_refusal_without_authorizing_changes():
    result = preview(snapshot(aof_enabled=False))

    assert result.status is RedisCapacityPreviewStatus.REFUSED
    assert result.ok is False
    assert result.actuation_allowed is False