from datetime import datetime, timezone

import pytest

from app.core.autoscaling.api_observation import (
    APIObservationAdapter,
    APIObservationConfig,
)
from app.core.autoscaling.decision_engine import (
    AutoscalingDecisionEngine,
    AutoscalingPolicy,
)
from app.core.observability.api_request_metrics import (
    API_REQUEST_METRICS_KEY_PREFIX,
    collect_api_request_metrics,
    record_api_request_metric,
)


BASE = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.operations = []

    def hincrby(self, key, field, amount):
        self.operations.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key, field, amount):
        self.operations.append(("hincrbyfloat", key, field, amount))
        return self

    def expire(self, key, seconds):
        self.operations.append(("expire", key, seconds))
        return self

    def execute(self):
        if self.redis.fail_execute:
            raise OSError("private Redis error")

        for operation in self.operations:
            name = operation[0]

            if name == "expire":
                continue

            _, key, field, amount = operation
            bucket = self.redis.data.setdefault(key, {})

            if name == "hincrby":
                bucket[field] = str(
                    int(bucket.get(field, "0")) + amount
                )
            else:
                bucket[field] = str(
                    float(bucket.get(field, "0")) + amount
                )

        return [True] * len(self.operations)


class FakeRedis:
    def __init__(self):
        self.data = {}
        self.ping_result = True
        self.ping_error = None
        self.fail_execute = False

    def pipeline(self, transaction=False):
        assert transaction is False
        return FakePipeline(self)

    def ping(self):
        if self.ping_error is not None:
            raise self.ping_error
        return self.ping_result

    def hgetall(self, key):
        return dict(self.data.get(key, {}))


def record(
    client,
    *,
    route="/api/v1/patients",
    status=200,
    duration=80.0,
    at=BASE + 5,
    instance_id=None,
):
    return record_api_request_metric(
        client=client,
        route=route,
        status_code=status,
        duration_ms=duration,
        now=at,
        instance_id=instance_id,
    )


def test_records_fleet_demand_and_health_telemetry_separately():
    redis = FakeRedis()

    assert record(redis) is True
    assert record(
        redis,
        route="/health/ready",
        duration=2.0,
        at=BASE + 6,
    ) is True

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
    )

    assert result["available"] is True
    assert result["healthy"] is True
    assert result["reason"] == "ok"
    assert result["telemetry_samples"] == 2
    assert result["request_count"] == 1
    assert result["requests_per_second"] == pytest.approx(1 / 30)
    assert result["errors_5xx"] == 0
    assert result["average_duration_ms"] == pytest.approx(80.0)
    assert result["estimated_p95_duration_ms"] == 100.0


def test_5xx_rate_and_open_ended_latency_are_reported_without_guessing():
    redis = FakeRedis()

    assert record(
        redis,
        status=503,
        duration=1200.0,
    ) is True

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
    )

    assert result["healthy"] is True
    assert result["request_count"] == 1
    assert result["errors_5xx"] == 1
    assert result["error_5xx_rate"] == 1.0
    assert result["estimated_p95_duration_ms"] is None


def test_missing_request_telemetry_fails_closed():
    redis = FakeRedis()

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
    )

    assert result["available"] is True
    assert result["healthy"] is False
    assert result["reason"] == "no_recent_http_telemetry"


def test_redis_unavailable_fails_closed():
    redis = FakeRedis()
    redis.ping_error = OSError("private Redis error")

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
    )

    assert result["available"] is False
    assert result["healthy"] is False
    assert result["reason"] == "redis_unavailable"


def test_malformed_metric_payload_fails_closed():
    redis = FakeRedis()
    redis.data[f"{API_REQUEST_METRICS_KEY_PREFIX}{BASE}"] = {
        "telemetry_samples": "not-an-integer",
    }

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
    )

    assert result["healthy"] is False
    assert result["reason"] == "invalid_metric_payload"


def test_recorder_never_propagates_redis_write_failure():
    redis = FakeRedis()
    redis.fail_execute = True

    assert record(redis) is False


def test_stale_samples_fail_closed():
    redis = FakeRedis()

    assert record(redis, at=BASE + 1) is True

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 25,
        max_sample_age_seconds=20,
    )

    assert result["healthy"] is False
    assert result["reason"] == "stale_http_telemetry"


def test_observation_ratio_uses_fleet_rate_and_configured_replica_capacity():
    redis = FakeRedis()
    assert record(redis, at=BASE + 5) is True

    adapter = APIObservationAdapter(
        redis_client=redis,
        config=APIObservationConfig(
            target_requests_per_second_per_replica=1 / 60,
        ),
    )
    evaluated_at = datetime.fromtimestamp(
        BASE + 6,
        timezone.utc,
    )

    result = adapter.collect(
        current_replicas=2,
        evaluated_at=evaluated_at,
    )

    assert result.metrics["healthy"] is True
    assert result.observation.healthy is True
    assert result.observation.current_replicas == 2
    assert result.observation.load_ratio == pytest.approx(1.0)
    assert result.observation.observed_at == evaluated_at


def test_observation_is_unhealthy_when_metrics_are_missing():
    adapter = APIObservationAdapter(
        redis_client=FakeRedis(),
        config=APIObservationConfig(
            target_requests_per_second_per_replica=10.0,
        ),
    )

    result = adapter.collect(
        current_replicas=2,
        evaluated_at=datetime.fromtimestamp(BASE + 6, timezone.utc),
    )

    assert result.observation.healthy is False
    assert result.observation.load_ratio is None


def test_observation_rejects_naive_evaluation_time():
    adapter = APIObservationAdapter(
        redis_client=FakeRedis(),
        config=APIObservationConfig(
            target_requests_per_second_per_replica=10.0,
        ),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        adapter.collect(
            current_replicas=2,
            evaluated_at=datetime(2026, 1, 1),
        )


@pytest.mark.parametrize(
    "target",
    [0, -1, float("inf"), float("nan"), True],
)
def test_invalid_per_replica_target_is_rejected(target):
    with pytest.raises(ValueError):
        APIObservationConfig(
            target_requests_per_second_per_replica=target,
        )


def test_engine_holds_when_adapter_marks_observation_unhealthy():
    redis = FakeRedis()
    adapter = APIObservationAdapter(
        redis_client=redis,
        config=APIObservationConfig(
            target_requests_per_second_per_replica=10.0,
        ),
    )
    evaluated_at = datetime.fromtimestamp(BASE + 6, timezone.utc)
    result = adapter.collect(
        current_replicas=2,
        evaluated_at=evaluated_at,
    )

    engine = AutoscalingDecisionEngine(
        AutoscalingPolicy(
            min_replicas=2,
            max_replicas=5,
            scale_out_load_ratio=0.8,
            scale_in_load_ratio=0.2,
            scale_out_window_seconds=10,
            scale_in_window_seconds=10,
            cooldown_seconds=30,
            max_metrics_age_seconds=60,
            max_observation_gap_seconds=30,
        )
    )

    decision = engine.evaluate(
        result.observation,
        evaluated_at=evaluated_at,
    )

    assert decision.action.value == "hold"
    assert decision.reason == "metrics_unhealthy"
def test_expected_instance_coverage_requires_each_serving_replica():
    redis = FakeRedis()
    instance_ids = ("000000000001", "000000000002")

    for instance_id in instance_ids:
        assert record(
            redis,
            at=BASE + 5,
            instance_id=instance_id,
        ) is True

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
        expected_instance_ids=instance_ids,
    )

    assert result["available"] is True
    assert result["healthy"] is True
    assert result["reason"] == "ok"
    assert result["expected_instance_ids"] == list(instance_ids)
    assert result["reporting_instance_ids"] == list(instance_ids)
    assert result["missing_instance_ids"] == []


def test_missing_instance_coverage_fails_closed():
    redis = FakeRedis()
    expected_ids = ("000000000001", "000000000002")

    # Canary traffic is not evidence that either serving backend is reporting.
    assert record(
        redis,
        route="/api/v1/departments",
        status=401,
        at=BASE + 5,
        instance_id="abcdef123456",
    ) is True

    result = collect_api_request_metrics(
        client=redis,
        now=BASE + 7,
        expected_instance_ids=expected_ids,
    )

    assert result["available"] is True
    assert result["healthy"] is False
    assert result["reason"] == "incomplete_instance_coverage"
    assert result["expected_instance_ids"] == list(expected_ids)
    assert result["reporting_instance_ids"] == []
    assert result["missing_instance_ids"] == list(expected_ids)
