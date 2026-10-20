from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.core.observability import redis_metrics


def _redis_mock() -> Mock:
    redis = Mock()
    redis.config_get.return_value = {
        "maxmemory": "0",
        "maxmemory-policy": "noeviction",
    }

    redis.info.side_effect = [
        {
            "redis_version": "7.2.4",
            "uptime_in_seconds": 3600,
            "keyspace_hits": 800,
            "keyspace_misses": 200,
            "db0": {
                "keys": 100,
                "expires": 40,
                "expired": 12,
            },
            "db1": {
                "keys": 50,
                "expires": 10,
                "expired": 3,
            },
        },
        {
            "used_memory": 1024,
            "used_memory_peak": 2048,
            "used_memory_rss": 4096,
            "mem_fragmentation_ratio": 1.25,
        },
        {
            "connected_clients": 12,
            "blocked_clients": 2,
            "tracking_clients": 1,
        },
        {
            "instantaneous_ops_per_sec": 75,
            "total_commands_processed": 10000,
            "total_connections_received": 500,
            "rejected_connections": 3,
            "evicted_keys": 4,
            "expired_keys": 15,
            "keyspace_hits": 800,
            "keyspace_misses": 200,
            "total_net_input_bytes": 100000,
            "total_net_output_bytes": 200000,
            "total_reads_processed": 3000,
            "total_writes_processed": 1800,
            "instantaneous_input_kbps": 12.5,
            "instantaneous_output_kbps": 25.0,
        },
        {
            "used_cpu_sys": 12.5,
            "used_cpu_user": 8.75,
            "used_cpu_sys_children": 1.5,
            "used_cpu_user_children": 0.75,
        },
    ]

    return redis


def test_collect_redis_metrics_returns_core_metrics():
    redis = _redis_mock()

    result = redis_metrics.collect_redis_metrics(
        client=redis,
    )

    assert result["healthy"] is True
    assert result["redis_version"] == "7.2.4"
    assert result["uptime_seconds"] == 3600

    assert result["connected_clients"] == 12
    assert result["blocked_clients"] == 2
    assert result["tracking_clients"] == 1

    assert result["used_memory_bytes"] == 1024
    assert result["used_memory_peak_bytes"] == 2048
    assert result["used_memory_rss_bytes"] == 4096
    assert result["memory_fragmentation_ratio"] == 1.25
    assert result["max_memory_bytes"] == 0
    assert result["max_memory_policy"] == "noeviction"
    assert result["memory_limit_configured"] is False
    assert result["memory_limit_usage_ratio"] is None
    assert result["memory_limit_remaining_bytes"] is None
    assert result["memory_capacity_metrics_healthy"] is True
    assert result["memory_capacity_metrics_reason"] == "ok"

    assert result["instantaneous_ops_per_sec"] == 75
    assert result["total_commands_processed"] == 10000
    assert result["total_connections_received"] == 500
    assert result["rejected_connections"] == 3
    assert result["evicted_keys"] == 4
    assert result["expired_keys"] == 15


def test_collect_redis_metrics_calculates_memory_limit_utilization():
    redis = _redis_mock()
    redis.config_get.return_value = {
        "maxmemory": "8192",
        "maxmemory-policy": "allkeys-lru",
    }

    result = redis_metrics.collect_redis_metrics(client=redis)

    assert result["memory_capacity_metrics_healthy"] is True
    assert result["max_memory_bytes"] == 8192
    assert result["max_memory_policy"] == "allkeys-lru"
    assert result["memory_limit_configured"] is True
    assert result["memory_limit_usage_ratio"] == pytest.approx(0.125)
    assert result["memory_limit_remaining_bytes"] == 7168


@pytest.mark.parametrize(
    ("config", "expected_reason"),
    [
        ({"maxmemory-policy": "noeviction"}, "invalid_maxmemory"),
        (
            {"maxmemory": "unlimited", "maxmemory-policy": "noeviction"},
            "invalid_maxmemory",
        ),
        (
            {"maxmemory": "-1", "maxmemory-policy": "noeviction"},
            "invalid_maxmemory",
        ),
        ({"maxmemory": "8192"}, "invalid_maxmemory_policy"),
    ],
)
def test_collect_redis_metrics_marks_invalid_capacity_config_unhealthy(
    config, expected_reason
):
    redis = _redis_mock()
    redis.config_get.return_value = config

    result = redis_metrics.collect_redis_metrics(client=redis)

    assert result["healthy"] is True
    assert result["memory_capacity_metrics_healthy"] is False
    assert result["memory_capacity_metrics_reason"] == expected_reason

    if expected_reason == "invalid_maxmemory_policy":
        assert result["max_memory_bytes"] == 8192
        assert result["memory_limit_usage_ratio"] == pytest.approx(0.125)
    else:
        assert result["max_memory_bytes"] is None
        assert result["memory_limit_usage_ratio"] is None


@pytest.mark.parametrize(
    "invalid_value",
    [None, -1, True, 1.5, "NaN", "1.5"],
)
def test_collect_redis_metrics_marks_invalid_used_memory(invalid_value):
    redis = _redis_mock()
    responses = list(redis.info.side_effect)
    responses[1]["used_memory"] = invalid_value
    redis.info.side_effect = responses

    result = redis_metrics.collect_redis_metrics(client=redis)

    assert result["healthy"] is True
    assert result["memory_capacity_metrics_healthy"] is False
    assert result["memory_capacity_metrics_reason"] == "invalid_used_memory"
    assert result["memory_limit_usage_ratio"] is None
    assert result["memory_limit_remaining_bytes"] is None


def test_collect_redis_metrics_handles_restricted_capacity_config():
    redis = _redis_mock()
    redis.config_get.side_effect = PermissionError(
        "private configuration diagnostic"
    )

    result = redis_metrics.collect_redis_metrics(client=redis)

    assert result["healthy"] is True
    assert result["memory_capacity_metrics_healthy"] is False
    assert (
        result["memory_capacity_metrics_reason"]
        == "redis_capacity_config_unavailable"
    )
    assert result["memory_capacity_config_error_type"] == "PermissionError"
    assert "private configuration diagnostic" not in repr(result)


def test_collect_redis_metrics_propagates_redis_command_failure():
    redis = _redis_mock()
    redis.ping.side_effect = TimeoutError("private Redis diagnostic")

    with pytest.raises(TimeoutError):
        redis_metrics.collect_redis_metrics(client=redis)

    redis.info.assert_not_called()


def test_collect_redis_metrics_timestamp_is_current_and_timezone_aware():
    redis = _redis_mock()
    before = datetime.now(timezone.utc)

    result = redis_metrics.collect_redis_metrics(client=redis)

    after = datetime.now(timezone.utc)
    observed_at = datetime.fromisoformat(result["timestamp"])

    assert observed_at.tzinfo is not None
    assert before <= observed_at <= after


def test_collect_redis_metrics_calculates_cache_hit_ratio():
    redis = _redis_mock()

    result = redis_metrics.collect_redis_metrics(
        client=redis,
    )

    assert result["keyspace_hits"] == 800
    assert result["keyspace_misses"] == 200
    assert result["cache_hit_ratio"] == pytest.approx(
        0.8
    )


def test_collect_redis_metrics_aggregates_keyspace():
    redis = _redis_mock()

    result = redis_metrics.collect_redis_metrics(
        client=redis,
    )

    assert result["key_count"] == 150
    assert result["expiring_key_count"] == 50
    assert result["expired_key_count"] == 15


def test_collect_redis_metrics_returns_network_metrics():
    redis = _redis_mock()

    result = redis_metrics.collect_redis_metrics(
        client=redis,
    )

    assert result["total_net_input_bytes"] == 100000
    assert result["total_net_output_bytes"] == 200000
    assert result["total_reads_processed"] == 3000
    assert result["total_writes_processed"] == 1800
    assert result["instantaneous_input_kbps"] == 12.5
    assert result["instantaneous_output_kbps"] == 25.0


def test_collect_redis_metrics_returns_cpu_metrics():
    redis = _redis_mock()

    result = redis_metrics.collect_redis_metrics(
        client=redis,
    )

    assert result["used_cpu_sys_seconds"] == 12.5
    assert result["used_cpu_user_seconds"] == 8.75
    assert (
        result["used_cpu_sys_children_seconds"]
        == 1.5
    )
    assert (
        result["used_cpu_user_children_seconds"]
        == 0.75
    )


def test_collect_redis_metrics_can_count_key_prefixes():
    redis = _redis_mock()

    redis.scan_iter.side_effect = [
        iter(
            [
                "auth:revoked:1",
                "auth:revoked:2",
            ]
        ),
        iter(
            [
                "session:1",
            ]
        ),
    ]

    result = redis_metrics.collect_redis_metrics(
        client=redis,
        key_prefixes=[
            "auth:revoked:",
            "session:",
        ],
    )

    assert result["key_prefix_counts"] == {
        "auth:revoked:": 2,
        "session:": 1,
    }


def test_count_keys_by_prefix_requires_initialized_client(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.extensions.redis_client",
        None,
    )

    with pytest.raises(RuntimeError):
        redis_metrics.count_keys_by_prefix(
            ["auth:revoked:"],
            client=None,
        )


def test_count_keys_by_prefix_rejects_invalid_scan_count():
    redis = Mock()

    with pytest.raises(ValueError):
        redis_metrics.count_keys_by_prefix(
            ["auth:revoked:"],
            client=redis,
            scan_count=0,
        )


def test_count_keys_by_prefix_rejects_empty_prefix():
    redis = Mock()

    with pytest.raises(ValueError):
        redis_metrics.count_keys_by_prefix(
            [""],
            client=redis,
        )