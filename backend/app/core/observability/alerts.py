from __future__ import annotations

import math
from typing import Any


DEFAULT_THRESHOLDS = {
    "queue_depth_warning": 1000,
    "queue_depth_critical": 5000,
    "worker_utilization_warning_percent": 90.0,
    "cpu_warning_percent": 90.0,
    "memory_warning_percent": 90.0,
    # Disabled until a Redis-specific production capacity baseline is approved.
    "redis_memory_warning_percent": None,
    "redis_memory_critical_percent": None,
    "swap_warning_percent": 50.0,
    "long_running_task_warning_count": 1,
}


def _alert(
    *,
    code: str,
    severity: str,
    component: str,
    message: str,
    observed_value: Any = None,
    threshold: Any = None,
    resource: str | None = None,
) -> dict[str, Any]:
    result = {
        "code": code,
        "severity": severity,
        "component": component,
        "message": message,
        "observed_value": observed_value,
        "threshold": threshold,
    }

    if resource is not None:
        result["resource"] = resource

    return result


def _get_thresholds(
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    thresholds = dict(
        DEFAULT_THRESHOLDS
    )

    if overrides:
        for key, value in overrides.items():
            if key in thresholds:
                thresholds[key] = value

    return thresholds


def _validate_percentage_threshold(
    value: Any,
    *,
    name: str,
) -> float | None:
    """Validate an optional percentage; None means intentionally disabled."""
    if value is None:
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{name} must be None or a finite number in (0, 100]"
        )

    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(
            f"{name} must be None or a finite number in (0, 100]"
        ) from None

    if not math.isfinite(numeric) or numeric <= 0 or numeric > 100:
        raise ValueError(
            f"{name} must be None or a finite number in (0, 100]"
        )

    return numeric


def evaluate_operational_alerts(
    snapshot: dict[str, Any],
    *,
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        raise TypeError(
            "snapshot must be a dictionary"
        )

    resolved = _get_thresholds(
        thresholds
    )

    redis_memory_warning = _validate_percentage_threshold(
        resolved["redis_memory_warning_percent"],
        name="redis_memory_warning_percent",
    )
    redis_memory_critical = _validate_percentage_threshold(
        resolved["redis_memory_critical_percent"],
        name="redis_memory_critical_percent",
    )

    if (
        redis_memory_warning is not None
        and redis_memory_critical is not None
        and redis_memory_warning >= redis_memory_critical
    ):
        raise ValueError(
            "redis_memory_warning_percent must be less than "
            "redis_memory_critical_percent"
        )

    components = snapshot.get(
        "components",
        {},
    )

    if not isinstance(components, dict):
        raise ValueError(
            "snapshot components must be a dictionary"
        )

    alerts: list[dict[str, Any]] = []

    readiness = components.get(
        "readiness",
        {},
    )

    if not readiness.get(
        "healthy",
        False,
    ):
        alerts.append(
            _alert(
                code="readiness_failed",
                severity="critical",
                component="readiness",
                message=(
                    "Application readiness check failed."
                ),
            )
        )

    redis = components.get(
        "redis",
        {},
    )

    if not redis.get(
        "healthy",
        False,
    ):
        alerts.append(
            _alert(
                code="redis_unhealthy",
                severity="critical",
                component="redis",
                message=(
                    "Redis is unavailable or unhealthy."
                ),
            )
        )

    # Redis connectivity and Redis capacity telemetry are distinct:
    # missing capacity configuration must not be misreported as a dead Redis.
    if redis.get("healthy", False):
        capacity_unavailable = (
            redis.get("memory_capacity_metrics_healthy") is not True
            or redis.get("memory_capacity_metrics_reason") != "ok"
        )
        unbounded_memory = False

        max_memory = redis.get("max_memory_bytes")
        memory_policy = redis.get("max_memory_policy")
        limit_configured = redis.get("memory_limit_configured")
        usage_ratio = redis.get("memory_limit_usage_ratio")
        remaining_bytes = redis.get("memory_limit_remaining_bytes")

        if not capacity_unavailable:
            if (
                type(max_memory) is not int
                or max_memory < 0
                or not isinstance(memory_policy, str)
                or not memory_policy.strip()
            ):
                capacity_unavailable = True

            elif max_memory == 0 and limit_configured is False:
                # Redis uses maxmemory=0 to mean no Redis-level limit.
                if usage_ratio is None and remaining_bytes is None:
                    unbounded_memory = True
                else:
                    capacity_unavailable = True

            elif max_memory > 0 and limit_configured is True:
                if (
                    isinstance(usage_ratio, bool)
                    or not isinstance(usage_ratio, (int, float))
                ):
                    capacity_unavailable = True
                else:
                    try:
                        ratio = float(usage_ratio)
                    except (OverflowError, TypeError, ValueError):
                        capacity_unavailable = True
                    else:
                        if not math.isfinite(ratio) or ratio < 0:
                            capacity_unavailable = True
                        else:
                            usage_percent = ratio * 100.0

                            if (
                                redis_memory_critical is not None
                                and usage_percent >= redis_memory_critical
                            ):
                                alerts.append(
                                    _alert(
                                        code="redis_memory_capacity_critical",
                                        severity="critical",
                                        component="redis",
                                        message=(
                                            "Redis memory usage has reached "
                                            "the configured critical threshold."
                                        ),
                                        observed_value=usage_percent,
                                        threshold=redis_memory_critical,
                                        resource="memory",
                                    )
                                )
                            elif (
                                redis_memory_warning is not None
                                and usage_percent >= redis_memory_warning
                            ):
                                alerts.append(
                                    _alert(
                                        code="redis_memory_capacity_high",
                                        severity="warning",
                                        component="redis",
                                        message=(
                                            "Redis memory usage has reached "
                                            "the configured warning threshold."
                                        ),
                                        observed_value=usage_percent,
                                        threshold=redis_memory_warning,
                                        resource="memory",
                                    )
                                )
            else:
                capacity_unavailable = True

        if capacity_unavailable:
            alerts.append(
                _alert(
                    code="redis_capacity_metrics_unavailable",
                    severity="warning",
                    component="redis",
                    message=(
                        "Redis memory capacity cannot be assessed from "
                        "valid configuration telemetry."
                    ),
                    resource="memory",
                )
            )
        elif unbounded_memory:
            alerts.append(
                _alert(
                    code="redis_memory_limit_unconfigured",
                    severity="warning",
                    component="redis",
                    message=(
                        "Redis has no configured maxmemory limit; "
                        "host and container capacity require review."
                    ),
                    observed_value=0,
                    resource="memory",
                )
            )

    celery = components.get(
        "celery",
        {},
    )

    if not celery.get(
        "broker_healthy",
        False,
    ):
        alerts.append(
            _alert(
                code="celery_broker_unhealthy",
                severity="critical",
                component="celery",
                message=(
                    "Celery broker is unavailable or unhealthy."
                ),
            )
        )

    if not celery.get(
        "workers_healthy",
        False,
    ):
        alerts.append(
            _alert(
                code="celery_workers_unavailable",
                severity="critical",
                component="celery",
                message=(
                    "No healthy Celery workers are available."
                ),
            )
        )

    queue_depths = celery.get(
        "queue_depths",
        {},
    )

    if isinstance(
        queue_depths,
        dict,
    ):
        for queue_name, depth in queue_depths.items():
            if not isinstance(
                depth,
                (int, float),
            ):
                continue

            if depth >= resolved[
                "queue_depth_critical"
            ]:
                alerts.append(
                    _alert(
                        code="celery_queue_depth_critical",
                        severity="critical",
                        component="celery",
                        message=(
                            "Celery queue depth is critically high."
                        ),
                        observed_value=depth,
                        threshold=resolved[
                            "queue_depth_critical"
                        ],
                        resource=str(queue_name),
                    )
                )

            elif depth >= resolved[
                "queue_depth_warning"
            ]:
                alerts.append(
                    _alert(
                        code="celery_queue_depth_high",
                        severity="warning",
                        component="celery",
                        message=(
                            "Celery queue depth is high."
                        ),
                        observed_value=depth,
                        threshold=resolved[
                            "queue_depth_warning"
                        ],
                        resource=str(queue_name),
                    )
                )

    worker_utilization = celery.get(
        "worker_utilization_percent"
    )

    if isinstance(
        worker_utilization,
        (int, float),
    ) and worker_utilization >= resolved[
        "worker_utilization_warning_percent"
    ]:
        alerts.append(
            _alert(
                code="celery_worker_utilization_high",
                severity="warning",
                component="celery",
                message=(
                    "Celery worker utilization is high."
                ),
                observed_value=worker_utilization,
                threshold=resolved[
                    "worker_utilization_warning_percent"
                ],
            )
        )

    long_running_count = celery.get(
        "long_running_active_count",
        0,
    )

    if (
        isinstance(
            long_running_count,
            (int, float),
        )
        and long_running_count
        >= resolved[
            "long_running_task_warning_count"
        ]
    ):
        alerts.append(
            _alert(
                code="celery_long_running_tasks",
                severity="warning",
                component="celery",
                message=(
                    "Long-running Celery tasks are active."
                ),
                observed_value=long_running_count,
                threshold=resolved[
                    "long_running_task_warning_count"
                ],
            )
        )

    system = components.get(
        "system",
        {},
    )

    cpu = system.get(
        "cpu",
        {},
    )

    cpu_percent = cpu.get(
        "percent"
    )

    if isinstance(
        cpu_percent,
        (int, float),
    ) and cpu_percent >= resolved[
        "cpu_warning_percent"
    ]:
        alerts.append(
            _alert(
                code="system_cpu_high",
                severity="warning",
                component="system",
                message=(
                    "System CPU utilization is high."
                ),
                observed_value=cpu_percent,
                threshold=resolved[
                    "cpu_warning_percent"
                ],
            )
        )

    memory = system.get(
        "memory",
        {},
    )

    memory_percent = memory.get(
        "percent"
    )

    if isinstance(
        memory_percent,
        (int, float),
    ) and memory_percent >= resolved[
        "memory_warning_percent"
    ]:
        alerts.append(
            _alert(
                code="system_memory_high",
                severity="warning",
                component="system",
                message=(
                    "System memory utilization is high."
                ),
                observed_value=memory_percent,
                threshold=resolved[
                    "memory_warning_percent"
                ],
            )
        )

    swap = system.get(
        "swap",
        {},
    )

    swap_percent = swap.get(
        "percent"
    )

    if isinstance(
        swap_percent,
        (int, float),
    ) and swap_percent >= resolved[
        "swap_warning_percent"
    ]:
        alerts.append(
            _alert(
                code="system_swap_high",
                severity="warning",
                component="system",
                message=(
                    "System swap utilization is high."
                ),
                observed_value=swap_percent,
                threshold=resolved[
                    "swap_warning_percent"
                ],
            )
        )

    return alerts
