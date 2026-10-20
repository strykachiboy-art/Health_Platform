"""Bounded, best-effort, fleet-shared HTTP request telemetry."""

from __future__ import annotations

import math
import re
import socket
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


API_REQUEST_METRICS_KEY_PREFIX = "observability:api:http:v1:"
API_REQUEST_INSTANCE_KEY_PREFIX = "observability:api:http-instance:v1:"
API_REQUEST_METRICS_BUCKET_SECONDS = 10
API_REQUEST_METRICS_RETENTION_SECONDS = 180

_LATENCY_BUCKETS = (
    (50.0, "latency_le_50_ms"),
    (100.0, "latency_le_100_ms"),
    (250.0, "latency_le_250_ms"),
    (500.0, "latency_le_500_ms"),
    (1000.0, "latency_le_1000_ms"),
)
_OVERFLOW_LATENCY_FIELD = "latency_gt_1000_ms"


def _finite_non_negative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError):
        return None

    if not math.isfinite(result) or result < 0:
        return None

    return result


def _bucket_start(timestamp: float, bucket_seconds: int) -> int:
    return int(timestamp // bucket_seconds) * bucket_seconds


def _bucket_key(bucket_start: int) -> str:
    return f"{API_REQUEST_METRICS_KEY_PREFIX}{bucket_start}"


def _valid_instance_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
        is not None
    )


def _instance_bucket_key(instance_id: str, bucket_start: int) -> str:
    return (
        f"{API_REQUEST_INSTANCE_KEY_PREFIX}"
        f"{instance_id}:{bucket_start}"
    )


def _is_health_route(route: object) -> bool:
    return (
        isinstance(route, str)
        and (route == "/health" or route.startswith("/health/"))
    )


def record_api_request_metric(
    *,
    client,
    route: str | None,
    status_code: int,
    duration_ms: float,
    now: float | None = None,
    instance_id: str | None = None,
) -> bool:
    """Record one request in a short-lived Redis bucket.

    Health endpoints count as telemetry samples, proving that the metrics
    instrumentation is active, but they do not count as application demand.
    This function deliberately fails open: metrics must never break a response.
    """
    if client is None or type(status_code) is not int:
        return False

    if not 100 <= status_code <= 599:
        return False

    duration = _finite_non_negative(duration_ms)
    if duration is None:
        return False

    if now is None:
        timestamp = time.time()
    else:
        parsed_timestamp = _finite_non_negative(now)
        if parsed_timestamp is None:
            return False
        timestamp = parsed_timestamp

    if instance_id is None:
        try:
            instance_id = socket.gethostname()
        except Exception:
            return False

    if not _valid_instance_id(instance_id):
        return False

    bucket_start = _bucket_start(
        timestamp,
        API_REQUEST_METRICS_BUCKET_SECONDS,
    )
    key = _bucket_key(bucket_start)
    instance_key = _instance_bucket_key(instance_id, bucket_start)
    is_application_request = (
        isinstance(route, str)
        and bool(route)
        and not _is_health_route(route)
    )

    try:
        pipeline = client.pipeline(transaction=False)

        # Write fleet aggregates and per-instance coverage together.
        # If either view becomes incomplete, the controller fails closed.
        for bucket_key in (key, instance_key):
            pipeline.hincrby(bucket_key, "telemetry_samples", 1)

            if is_application_request:
                pipeline.hincrby(bucket_key, "requests", 1)

                if status_code >= 500:
                    pipeline.hincrby(bucket_key, "errors_5xx", 1)

                pipeline.hincrbyfloat(
                    bucket_key,
                    "duration_ms_total",
                    duration,
                )

                for boundary, field in _LATENCY_BUCKETS:
                    if duration <= boundary:
                        pipeline.hincrby(bucket_key, field, 1)
                        break
                else:
                    pipeline.hincrby(
                        bucket_key,
                        _OVERFLOW_LATENCY_FIELD,
                        1,
                    )

            pipeline.expire(
                bucket_key,
                API_REQUEST_METRICS_RETENTION_SECONDS,
            )

        pipeline.execute()
        return True
    except Exception:
        return False


def _failed_snapshot(
    *,
    timestamp: float,
    reason: str,
    available: bool,
    window_seconds: int,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.fromtimestamp(
            timestamp,
            timezone.utc,
        ).isoformat(),
        "available": available,
        "healthy": False,
        "reason": reason,
        "window_seconds": window_seconds,
        "buckets_observed": 0,
        "telemetry_samples": 0,
        "request_count": 0,
        "requests_per_second": 0.0,
        "errors_5xx": 0,
        "error_5xx_rate": 0.0,
        "average_duration_ms": 0.0,
        "estimated_p95_duration_ms": None,
        "last_sample_at": None,
        "latency_histogram": {},
    }


def _text_key(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ValueError("Invalid metric field name")


def _metric_integer(
    fields: Mapping[str, object],
    name: str,
) -> int:
    if name not in fields:
        return 0

    value = fields[name]
    if isinstance(value, bool):
        raise ValueError("Invalid integer metric")

    if isinstance(value, bytes):
        value = value.decode("ascii")

    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("Invalid integer metric")
        number = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text or not text.isdecimal():
            raise ValueError("Invalid integer metric")
        number = int(text)
    else:
        raise ValueError("Invalid integer metric")

    if number < 0:
        raise ValueError("Negative metric")
    return number


def _metric_float(
    fields: Mapping[str, object],
    name: str,
) -> float:
    if name not in fields:
        return 0.0

    value = fields[name]
    if isinstance(value, bool):
        raise ValueError("Invalid float metric")
    if isinstance(value, bytes):
        value = value.decode("ascii")

    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError("Invalid float metric") from None

    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid float metric")
    return number


def collect_api_request_metrics(
    *,
    client,
    now: float | None = None,
    window_seconds: int = 30,
    bucket_seconds: int = API_REQUEST_METRICS_BUCKET_SECONDS,
    max_sample_age_seconds: int = 20,
    expected_instance_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Read a bounded rolling request window from shared Redis telemetry."""
    for name, value in (
        ("window_seconds", window_seconds),
        ("bucket_seconds", bucket_seconds),
        ("max_sample_age_seconds", max_sample_age_seconds),
    ):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    if (
        window_seconds < bucket_seconds
        or window_seconds % bucket_seconds != 0
        or window_seconds + bucket_seconds
        > API_REQUEST_METRICS_RETENTION_SECONDS
    ):
        raise ValueError("Invalid API telemetry window configuration")

    if max_sample_age_seconds > window_seconds:
        raise ValueError("max_sample_age_seconds exceeds the window")

    if now is None:
        timestamp = time.time()
    else:
        parsed_timestamp = _finite_non_negative(now)
        if parsed_timestamp is None:
            raise ValueError("now must be a finite non-negative timestamp")
        timestamp = parsed_timestamp

    # Validate timestamp conversion before touching the metrics backend.
    timestamp_iso = datetime.fromtimestamp(
        timestamp,
        timezone.utc,
    ).isoformat()

    normalized_expected_ids: tuple[str, ...] | None = None
    if expected_instance_ids is not None:
        if (
            not isinstance(expected_instance_ids, (tuple, list))
            or not expected_instance_ids
            or any(
                not _valid_instance_id(instance_id)
                for instance_id in expected_instance_ids
            )
            or len(set(expected_instance_ids)) != len(expected_instance_ids)
        ):
            return _failed_snapshot(
                timestamp=timestamp,
                reason="invalid_expected_instance_ids",
                available=False,
                window_seconds=window_seconds,
            )
        normalized_expected_ids = tuple(expected_instance_ids)

    if client is None:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="redis_unavailable",
            available=False,
            window_seconds=window_seconds,
        )

    try:
        if not client.ping():
            return _failed_snapshot(
                timestamp=timestamp,
                reason="redis_unavailable",
                available=False,
                window_seconds=window_seconds,
            )
    except Exception:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="redis_unavailable",
            available=False,
            window_seconds=window_seconds,
        )

    request_count = 0
    errors_5xx = 0
    duration_total_ms = 0.0
    telemetry_samples = 0
    buckets_observed = 0
    latest_sample_bucket: int | None = None

    histogram_fields = [
        field for _, field in _LATENCY_BUCKETS
    ] + [_OVERFLOW_LATENCY_FIELD]
    histogram = {field: 0 for field in histogram_fields}

    current_bucket = _bucket_start(timestamp, bucket_seconds)
    bucket_count = window_seconds // bucket_seconds

    try:
        for offset in range(bucket_count):
            bucket = current_bucket - (offset * bucket_seconds)
            raw_hash = client.hgetall(_bucket_key(bucket))

            if not isinstance(raw_hash, Mapping):
                return _failed_snapshot(
                    timestamp=timestamp,
                    reason="invalid_metric_payload",
                    available=True,
                    window_seconds=window_seconds,
                )

            if not raw_hash:
                continue

            fields = {
                _text_key(key): value
                for key, value in raw_hash.items()
            }

            if "telemetry_samples" not in fields:
                raise ValueError("Missing telemetry sample counter")

            bucket_samples = _metric_integer(
                fields,
                "telemetry_samples",
            )
            if bucket_samples <= 0:
                raise ValueError("Invalid telemetry sample counter")

            bucket_requests = _metric_integer(fields, "requests")
            bucket_errors = _metric_integer(fields, "errors_5xx")
            bucket_duration = _metric_float(
                fields,
                "duration_ms_total",
            )
            bucket_histogram = {
                field: _metric_integer(fields, field)
                for field in histogram_fields
            }

            if (
                bucket_requests > bucket_samples
                or bucket_errors > bucket_requests
                or sum(bucket_histogram.values()) != bucket_requests
            ):
                raise ValueError("Inconsistent request metrics")

            telemetry_samples += bucket_samples
            request_count += bucket_requests
            errors_5xx += bucket_errors
            duration_total_ms += bucket_duration

            for field, count in bucket_histogram.items():
                histogram[field] += count

            buckets_observed += 1
            if (
                latest_sample_bucket is None
                or bucket > latest_sample_bucket
            ):
                latest_sample_bucket = bucket
    except Exception:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="invalid_metric_payload",
            available=True,
            window_seconds=window_seconds,
        )

    if not math.isfinite(duration_total_ms) or duration_total_ms < 0:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="invalid_metric_payload",
            available=True,
            window_seconds=window_seconds,
        )

    if telemetry_samples == 0 or latest_sample_bucket is None:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="no_recent_http_telemetry",
            available=True,
            window_seconds=window_seconds,
        )

    sample_age = timestamp - latest_sample_bucket
    if sample_age < 0 or sample_age > max_sample_age_seconds:
        return _failed_snapshot(
            timestamp=timestamp,
            reason="stale_http_telemetry",
            available=True,
            window_seconds=window_seconds,
        )

    coverage_metadata: dict[str, Any] = {}

    if normalized_expected_ids is not None:
        reporting_ids: list[str] = []
        missing_ids: list[str] = []

        try:
            for instance_id in normalized_expected_ids:
                has_recent_sample = False

                for offset in range(bucket_count):
                    instance_bucket = current_bucket - (
                        offset * bucket_seconds
                    )
                    raw_instance_hash = client.hgetall(
                        _instance_bucket_key(instance_id, instance_bucket)
                    )

                    if not isinstance(raw_instance_hash, Mapping):
                        raise ValueError("Invalid instance metric payload")

                    if not raw_instance_hash:
                        continue

                    instance_fields = {
                        _text_key(key): value
                        for key, value in raw_instance_hash.items()
                    }

                    if "telemetry_samples" not in instance_fields:
                        raise ValueError(
                            "Missing instance telemetry sample counter"
                        )

                    instance_samples = _metric_integer(
                        instance_fields,
                        "telemetry_samples",
                    )
                    if instance_samples <= 0:
                        raise ValueError(
                            "Invalid instance telemetry sample counter"
                        )

                    instance_sample_age = timestamp - instance_bucket
                    if (
                        0 <= instance_sample_age
                        <= max_sample_age_seconds
                    ):
                        has_recent_sample = True
                        break

                if has_recent_sample:
                    reporting_ids.append(instance_id)
                else:
                    missing_ids.append(instance_id)

        except Exception:
            return {
                **_failed_snapshot(
                    timestamp=timestamp,
                    reason="instance_coverage_read_failed",
                    available=True,
                    window_seconds=window_seconds,
                ),
                "expected_instance_ids": list(normalized_expected_ids),
                "reporting_instance_ids": list(reporting_ids),
                "missing_instance_ids": [
                    instance_id
                    for instance_id in normalized_expected_ids
                    if instance_id not in reporting_ids
                ],
            }

        coverage_metadata = {
            "expected_instance_ids": list(normalized_expected_ids),
            "reporting_instance_ids": reporting_ids,
            "missing_instance_ids": missing_ids,
        }

        if missing_ids:
            return {
                **_failed_snapshot(
                    timestamp=timestamp,
                    reason="incomplete_instance_coverage",
                    available=True,
                    window_seconds=window_seconds,
                ),
                **coverage_metadata,
            }

    estimated_p95 = None
    if request_count:
        target_rank = math.ceil(0.95 * request_count)
        cumulative = 0

        for boundary, field in _LATENCY_BUCKETS:
            cumulative += histogram[field]
            if cumulative >= target_rank:
                estimated_p95 = boundary
                break

        # If the p95 falls in the open-ended overflow bucket, report unknown
        # rather than inventing an upper latency bound.
        if estimated_p95 is None:
            cumulative += histogram[_OVERFLOW_LATENCY_FIELD]
            if cumulative < target_rank:
                return _failed_snapshot(
                    timestamp=timestamp,
                    reason="invalid_metric_payload",
                    available=True,
                    window_seconds=window_seconds,
                )

    result = {
        "timestamp": timestamp_iso,
        "available": True,
        "healthy": True,
        "reason": "ok",
        "window_seconds": window_seconds,
        "buckets_observed": buckets_observed,
        "telemetry_samples": telemetry_samples,
        "request_count": request_count,
        "requests_per_second": request_count / float(window_seconds),
        "errors_5xx": errors_5xx,
        "error_5xx_rate": (
            errors_5xx / request_count
            if request_count
            else 0.0
        ),
        "average_duration_ms": (
            duration_total_ms / request_count
            if request_count
            else 0.0
        ),
        "estimated_p95_duration_ms": estimated_p95,
        "last_sample_at": datetime.fromtimestamp(
            latest_sample_bucket,
            timezone.utc,
        ).isoformat(),
        "latency_histogram": histogram,
    }
    result.update(coverage_metadata)
    return result
