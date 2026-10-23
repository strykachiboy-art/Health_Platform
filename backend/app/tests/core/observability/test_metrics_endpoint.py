from __future__ import annotations

from unittest.mock import patch

from app.core.observability import metrics_routes
from app.core.observability.api_request_metrics import (
    API_REQUEST_INSTANCE_KEY_PREFIX,
    API_REQUEST_METRICS_KEY_PREFIX,
    record_api_request_metric,
)


_TEST_TOKEN = "test-observability-metrics-token-2026"


def _snapshot():
    return {
        "timestamp": "2026-10-23T03:00:00+00:00",
        "available": True,
        "healthy": True,
        "reason": "ok",
        "window_seconds": 30,
        "effective_window_seconds": 30.0,
        "buckets_observed": 3,
        "telemetry_samples": 16,
        "request_count": 10,
        "requests_per_second": 1.0 / 3.0,
        "errors_5xx": 2,
        "error_5xx_rate": 0.2,
        "average_duration_ms": 125.0,
        "estimated_p95_duration_ms": 250.0,
        "last_sample_at": "2026-10-23T03:00:00+00:00",
        "latency_histogram": {
            "latency_le_50_ms": 1,
            "latency_le_100_ms": 2,
            "latency_le_250_ms": 3,
            "latency_le_500_ms": 2,
            "latency_le_1000_ms": 1,
            "latency_gt_1000_ms": 1,
        },
    }


class _FakePipeline:
    def __init__(self):
        self.commands = []

    def hincrby(self, key, field, amount=1):
        self.commands.append(("hincrby", key, field, amount))
        return self

    def hincrbyfloat(self, key, field, amount):
        self.commands.append(("hincrbyfloat", key, field, amount))
        return self

    def expire(self, key, seconds):
        self.commands.append(("expire", key, seconds))
        return self

    def execute(self):
        return []


class _FakeRedis:
    def __init__(self):
        self.last_pipeline = None

    def pipeline(self, transaction=False):
        self.last_pipeline = _FakePipeline()
        return self.last_pipeline


def test_metrics_endpoint_fails_closed_when_token_missing(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = None

    with patch.object(metrics_routes, "collect_api_request_metrics") as collector:
        response = client.get("/metrics")

    assert response.status_code == 503
    assert "not configured" in response.get_data(as_text=True)
    collector.assert_not_called()


def test_metrics_endpoint_requires_exact_bearer_token(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = _TEST_TOKEN

    with patch.object(metrics_routes, "collect_api_request_metrics") as collector:
        missing = client.get("/metrics")
        wrong = client.get(
            "/metrics",
            headers={"Authorization": "Bearer wrong-token"},
        )
        wrong_scheme = client.get(
            "/metrics",
            headers={"Authorization": f"Basic {_TEST_TOKEN}"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert wrong_scheme.status_code == 401
    assert missing.headers["WWW-Authenticate"] == "Bearer"
    collector.assert_not_called()


def test_metrics_endpoint_rejects_non_ascii_authorization_without_500(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = _TEST_TOKEN

    with patch.object(metrics_routes, "collect_api_request_metrics") as collector:
        response = client.get("/metrics", headers={"Authorization": "Bearer \u00e9"})

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    collector.assert_not_called()

def test_metrics_endpoint_fails_closed_when_configured_token_is_non_ascii(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = "\u00e9" + ("x" * 31)

    with patch.object(metrics_routes, "collect_api_request_metrics") as collector:
        response = client.get("/metrics")

    assert response.status_code == 503
    assert "not configured" in response.get_data(as_text=True)
    collector.assert_not_called()


def test_metrics_endpoint_renders_prometheus_low_cardinality_metrics(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = _TEST_TOKEN

    with patch.object(
        metrics_routes,
        "collect_api_request_metrics",
        return_value=_snapshot(),
    ) as collector:
        response = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert "version=0.0.4" in response.headers["Content-Type"]
    assert response.headers["Cache-Control"] == "no-store"
    assert "clinic_observability_exporter_up 1" in body
    assert "clinic_http_telemetry_collection_success 1" in body
    assert "clinic_http_telemetry_available 1" in body
    assert "clinic_http_telemetry_healthy 1" in body
    assert "clinic_http_requests_in_window 10" in body
    assert "clinic_http_requests_per_second 0.3333333333333333" in body
    assert "clinic_http_5xx_errors_in_window 2" in body
    assert 'clinic_http_request_duration_ms_bucket{le="50"} 1' in body
    assert 'clinic_http_request_duration_ms_bucket{le="100"} 3' in body
    assert 'clinic_http_request_duration_ms_bucket{le="+Inf"} 10' in body
    assert "clinic_http_request_duration_ms_sum 1250.0" in body
    assert "clinic_http_request_duration_ms_count 10" in body
    assert 'request_id' not in body
    assert 'patient' not in body.lower()
    assert 'route=' not in body
    collector.assert_called_once_with(
        client=metrics_routes.extensions.redis_client,
        window_seconds=30,
    )


def test_metrics_collector_failure_stays_scrapeable_and_visible(app, client):
    app.config["OBSERVABILITY_METRICS_TOKEN"] = _TEST_TOKEN

    with patch.object(
        metrics_routes,
        "collect_api_request_metrics",
        side_effect=RuntimeError("do not expose this detail"),
    ):
        response = client.get(
            "/metrics",
            headers={"Authorization": f"Bearer {_TEST_TOKEN}"},
        )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "clinic_observability_exporter_up 1" in body
    assert "clinic_http_telemetry_collection_success 0" in body
    assert "clinic_http_telemetry_available 0" in body
    assert "clinic_http_telemetry_healthy 0" in body
    assert "do not expose this detail" not in body


def test_metrics_scrapes_are_telemetry_samples_but_not_application_demand():
    fake_redis = _FakeRedis()

    assert record_api_request_metric(
        client=fake_redis,
        route="/metrics",
        status_code=200,
        duration_ms=4.0,
        now=100.0,
        instance_id="backend-1",
    ) is True

    commands = fake_redis.last_pipeline.commands
    assert len(commands) > 0
    assert all(command[2] != "requests" for command in commands if command[0] == "hincrby")
    assert all(command[2] != "errors_5xx" for command in commands if command[0] == "hincrby")
    assert any(command[2] == "telemetry_samples" for command in commands if command[0] == "hincrby")
    assert any(key.startswith(API_REQUEST_METRICS_KEY_PREFIX) for _, key, *_ in commands)
    assert any(key.startswith(API_REQUEST_INSTANCE_KEY_PREFIX) for _, key, *_ in commands)


def test_metrics_route_prefix_is_recognized_as_non_demand():
    from app.core.observability.api_request_metrics import _is_metrics_route

    assert _is_metrics_route("/metrics") is True
    assert _is_metrics_route("/metrics/") is True
    assert _is_metrics_route("/metrics/custom") is True
    assert _is_metrics_route("/api/v1/patients") is False
    assert _is_metrics_route(None) is False
