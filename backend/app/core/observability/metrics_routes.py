from __future__ import annotations

import hmac
import math
from datetime import datetime
from collections.abc import Mapping
from typing import Any

from flask import Blueprint, Response, current_app, request

from app import extensions
from app.core.observability.api_request_metrics import (
    collect_api_request_metrics,
)


metrics_bp = Blueprint("observability_metrics", __name__)
_METRICS_TOKEN_MIN_LENGTH = 32
_WINDOW_SECONDS = 30


def _plain_response(body: str, status: int) -> Response:
    response = Response(
        body,
        status=status,
        content_type="text/plain; charset=utf-8",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _authorization_response() -> Response | None:
    token = current_app.config.get("OBSERVABILITY_METRICS_TOKEN")
    if (
        not isinstance(token, str)
        or len(token) < _METRICS_TOKEN_MIN_LENGTH
        or len(token) > 4096
        or token.strip() != token
        or not token.isascii()
    ):
        return _plain_response("metrics endpoint is not configured\n", 503)

    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {token}"
    if not supplied.isascii() or not hmac.compare_digest(supplied.encode("ascii"), expected.encode("ascii")):
        response = _plain_response("metrics authorization required\n", 401)
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    return None


def _safe_number(value: Any, *, integer: bool = False) -> tuple[int | float, bool]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0, False
    if integer and type(value) is not int:
        return 0, False
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        return 0, False
    if integer:
        return int(numeric), True
    return numeric, True


def _last_sample_timestamp(value: Any) -> tuple[float, bool]:
    if value is None:
        return 0.0, True
    if not isinstance(value, str):
        return 0.0, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return 0.0, False
        numeric = parsed.timestamp()
    except (OverflowError, OSError, TypeError, ValueError):
        return 0.0, False
    if not math.isfinite(numeric) or numeric < 0:
        return 0.0, False
    return numeric, True


def _render_metrics(
    snapshot: dict[str, Any],
    *,
    collection_success: bool,
) -> str:
    available = snapshot.get("available") is True
    healthy = snapshot.get("healthy") is True and available
    payload_valid = True

    window, valid = _safe_number(snapshot.get("window_seconds"), integer=True)
    payload_valid = payload_valid and valid and window > 0
    telemetry_samples, valid = _safe_number(snapshot.get("telemetry_samples"), integer=True)
    payload_valid = payload_valid and valid
    request_count, valid = _safe_number(snapshot.get("request_count"), integer=True)
    payload_valid = payload_valid and valid
    errors_5xx, valid = _safe_number(snapshot.get("errors_5xx"), integer=True)
    payload_valid = payload_valid and valid
    requests_per_second, valid = _safe_number(snapshot.get("requests_per_second"))
    payload_valid = payload_valid and valid
    error_ratio, valid = _safe_number(snapshot.get("error_5xx_rate"))
    payload_valid = payload_valid and valid and error_ratio <= 1.0
    average_duration, valid = _safe_number(snapshot.get("average_duration_ms"))
    payload_valid = payload_valid and valid

    raw_histogram = snapshot.get("latency_histogram", {})
    histogram_fields = (
        "latency_le_50_ms",
        "latency_le_100_ms",
        "latency_le_250_ms",
        "latency_le_500_ms",
        "latency_le_1000_ms",
        "latency_gt_1000_ms",
    )
    histogram: dict[str, int] = {}
    if not isinstance(raw_histogram, Mapping):
        raw_histogram = {}
        payload_valid = False
    for field in histogram_fields:
        value, valid = _safe_number(raw_histogram.get(field, 0), integer=True)
        histogram[field] = int(value)
        payload_valid = payload_valid and valid
    if sum(histogram.values()) != request_count:
        payload_valid = False

    last_sample, valid = _last_sample_timestamp(snapshot.get("last_sample_at"))
    payload_valid = payload_valid and valid
    healthy = healthy and payload_valid

    def gauge(name: str, value: int | float) -> str:
        return f"{name} {value}"

    lines = [
        "# HELP clinic_observability_exporter_up Whether the metrics endpoint rendered a response.",
        "# TYPE clinic_observability_exporter_up gauge",
        gauge("clinic_observability_exporter_up", 1),
        "# HELP clinic_http_telemetry_collection_success Whether the HTTP telemetry collector call completed without raising.",
        "# TYPE clinic_http_telemetry_collection_success gauge",
        gauge("clinic_http_telemetry_collection_success", int(collection_success)),
        "# HELP clinic_http_telemetry_available Whether the Redis-backed HTTP telemetry store is reachable.",
        "# TYPE clinic_http_telemetry_available gauge",
        gauge("clinic_http_telemetry_available", int(available)),
        "# HELP clinic_http_telemetry_healthy Whether recent HTTP telemetry is fresh and internally consistent.",
        "# TYPE clinic_http_telemetry_healthy gauge",
        gauge("clinic_http_telemetry_healthy", int(healthy)),
        "# HELP clinic_http_telemetry_samples_in_window HTTP request instrumentation samples, including non-demand health and scrape requests.",
        "# TYPE clinic_http_telemetry_samples_in_window gauge",
        gauge("clinic_http_telemetry_samples_in_window", telemetry_samples),
        "# HELP clinic_http_metric_window_seconds Nominal rolling HTTP metrics window in seconds.",
        "# TYPE clinic_http_metric_window_seconds gauge",
        gauge("clinic_http_metric_window_seconds", window),
        "# HELP clinic_http_requests_in_window Application demand requests in the rolling window; health and metrics scrapes are excluded.",
        "# TYPE clinic_http_requests_in_window gauge",
        gauge("clinic_http_requests_in_window", request_count),
        "# HELP clinic_http_requests_per_second Estimated application demand requests per second over the rolling window.",
        "# TYPE clinic_http_requests_per_second gauge",
        gauge("clinic_http_requests_per_second", requests_per_second),
        "# HELP clinic_http_5xx_errors_in_window HTTP 5xx errors in the rolling window.",
        "# TYPE clinic_http_5xx_errors_in_window gauge",
        gauge("clinic_http_5xx_errors_in_window", errors_5xx),
        "# HELP clinic_http_5xx_error_ratio Fraction of application demand requests returning HTTP 5xx in the rolling window.",
        "# TYPE clinic_http_5xx_error_ratio gauge",
        gauge("clinic_http_5xx_error_ratio", error_ratio),
        "# HELP clinic_http_request_duration_ms HTTP request duration distribution across fixed buckets in milliseconds.",
        "# TYPE clinic_http_request_duration_ms histogram",
    ]

    cumulative = 0
    for field, boundary in (
        ("latency_le_50_ms", "50"),
        ("latency_le_100_ms", "100"),
        ("latency_le_250_ms", "250"),
        ("latency_le_500_ms", "500"),
        ("latency_le_1000_ms", "1000"),
    ):
        cumulative += histogram[field]
        lines.append(f'clinic_http_request_duration_ms_bucket{{le="{boundary}"}} {cumulative}')
    lines.extend(
        [
            f'clinic_http_request_duration_ms_bucket{{le="+Inf"}} {request_count}',
            f"clinic_http_request_duration_ms_sum {average_duration * request_count}",
            f"clinic_http_request_duration_ms_count {request_count}",
            "# HELP clinic_http_telemetry_last_sample_timestamp_seconds Unix timestamp of the newest HTTP telemetry bucket.",
            "# TYPE clinic_http_telemetry_last_sample_timestamp_seconds gauge",
            gauge("clinic_http_telemetry_last_sample_timestamp_seconds", last_sample),
        ]
    )
    return "\n".join(lines) + "\n"


@metrics_bp.get("/metrics")
def prometheus_metrics() -> Response:
    """Expose low-cardinality HTTP telemetry in Prometheus text format."""
    authorization_failure = _authorization_response()
    if authorization_failure is not None:
        return authorization_failure

    collection_success = True
    try:
        snapshot = collect_api_request_metrics(
            client=extensions.redis_client,
            window_seconds=_WINDOW_SECONDS,
        )
        if not isinstance(snapshot, dict):
            raise TypeError("metrics collector returned a non-object")
    except Exception as exc:
        collection_success = False
        current_app.logger.warning(
            "metrics.collection_failed error_type=%s",
            type(exc).__name__,
            extra={
                "event": "metrics.collection_failed",
                "error_type": type(exc).__name__,
            },
        )
        snapshot = {
            "available": False,
            "healthy": False,
            "window_seconds": _WINDOW_SECONDS,
            "telemetry_samples": 0,
            "request_count": 0,
            "requests_per_second": 0.0,
            "errors_5xx": 0,
            "error_5xx_rate": 0.0,
            "average_duration_ms": 0.0,
            "last_sample_at": None,
            "latency_histogram": {},
        }

    body = _render_metrics(snapshot, collection_success=collection_success)
    response = Response(
        body,
        status=200,
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
