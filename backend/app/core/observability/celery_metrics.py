from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from weakref import WeakSet
from typing import Any

from celery.signals import (
    before_task_publish,
    task_postrun,
    task_prerun,
    task_retry,
    task_revoked,
)

from app import extensions
from app.core.observability.failure_events import (
    record_failure_event,
)


CELERY_METRICS_STATE_KEY = "_clinic_celery_metrics"

CELERY_AGGREGATE_KEY = (
    "observability:celery:aggregate:v1"
)

CELERY_TASK_PREFIX = (
    "observability:celery:task:v1:"
)

DEFAULT_QUEUE = "celery"
DEFAULT_INSPECT_TIMEOUT = 1.0
DEFAULT_LONG_RUNNING_THRESHOLD_SECONDS = 10.0

_REGISTERED_CELERY_APPS: WeakSet = WeakSet()
TASK_PUBLISHED_AT_HEADER = "clinic_published_at"
_TASK_START_TIMES: dict[str, float] = {}


def _to_int(
    value: Any,
    default: int = 0,
) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_redis_client():
    return extensions.redis_client


def _get_celery_app_from_task(task):
    return getattr(task, "app", None)


def _task_is_registered(task) -> bool:
    celery_app = _get_celery_app_from_task(task)

    if celery_app is None:
        return False

    return celery_app in _REGISTERED_CELERY_APPS


def _task_name(task) -> str:
    name = getattr(task, "name", None)

    if isinstance(name, str) and name.strip():
        return name.strip()

    fallback = getattr(
        task,
        "__name__",
        "unknown",
    )

    return (
        fallback
        if isinstance(fallback, str)
        else "unknown"
    )


def _task_metric_key(
    task_name: str,
) -> str:
    return (
        f"{CELERY_TASK_PREFIX}"
        f"{task_name}"
    )


def _increment_redis_metric(
    key: str,
    field: str,
    amount: int | float = 1,
) -> None:
    redis = _get_redis_client()

    if redis is None:
        return

    try:
        if isinstance(amount, float):
            redis.hincrbyfloat(
                key,
                field,
                amount,
            )
        else:
            redis.hincrby(
                key,
                field,
                amount,
            )
    except Exception:
        return


def _set_redis_metric(
    key: str,
    field: str,
    value: Any,
) -> None:
    redis = _get_redis_client()

    if redis is None:
        return

    try:
        redis.hset(
            key,
            field,
            value,
        )
    except Exception:
        return


def _record_task_runtime(
    task,
    task_id: str | None,
    state: str | None,
) -> None:
    if not _task_is_registered(task):
        return

    if not task_id:
        return

    started_at = _TASK_START_TIMES.pop(
        str(task_id),
        None,
    )

    if started_at is None:
        return

    duration_ms = (
        time.perf_counter() - started_at
    ) * 1000.0

    task_name = _task_name(task)

    aggregate_key = CELERY_AGGREGATE_KEY
    task_key = _task_metric_key(
        task_name
    )

    _increment_redis_metric(
        aggregate_key,
        "executed_tasks",
    )

    _increment_redis_metric(
        aggregate_key,
        "runtime_ms_total",
        duration_ms,
    )

    _increment_redis_metric(
        task_key,
        "executed_tasks",
    )

    _increment_redis_metric(
        task_key,
        "runtime_ms_total",
        duration_ms,
    )

    if duration_ms > (
        DEFAULT_LONG_RUNNING_THRESHOLD_SECONDS
        * 1000.0
    ):
        _increment_redis_metric(
            aggregate_key,
            "long_running_tasks",
        )

        _increment_redis_metric(
            task_key,
            "long_running_tasks",
        )

    if state == "SUCCESS":
        _increment_redis_metric(
            aggregate_key,
            "succeeded_tasks",
        )

        _increment_redis_metric(
            task_key,
            "succeeded_tasks",
        )

    elif state == "FAILURE":
        _increment_redis_metric(
            aggregate_key,
            "failed_tasks",
        )

        _increment_redis_metric(
            task_key,
            "failed_tasks",
        )

        record_failure_event(
            redis_client=_get_redis_client(),
            event_type="celery.task_failure",
            component="celery",
            severity="error",
            error_type="TaskFailure",
            task_name=task_name,
            task_id=str(task_id),
        )

    _set_redis_metric(
        aggregate_key,
        "last_runtime_ms",
        duration_ms,
    )

    _set_redis_metric(
        aggregate_key,
        "last_completed_at",
        _utcnow().isoformat(),
    )

    _set_redis_metric(
        task_key,
        "last_runtime_ms",
        duration_ms,
    )

    _set_redis_metric(
        task_key,
        "last_completed_at",
        _utcnow().isoformat(),
    )

    redis = _get_redis_client()

    if redis is None:
        return

    try:
        current_task_max = _to_float(
            redis.hget(
                task_key,
                "max_runtime_ms",
            )
        )

        if duration_ms > current_task_max:
            _set_redis_metric(
                task_key,
                "max_runtime_ms",
                duration_ms,
            )

        current_aggregate_max = _to_float(
            redis.hget(
                aggregate_key,
                "max_runtime_ms",
            )
        )

        if duration_ms > current_aggregate_max:
            _set_redis_metric(
                aggregate_key,
                "max_runtime_ms",
                duration_ms,
            )

    except Exception:
        return


def _handle_before_task_publish(
    sender=None,
    headers=None,
    **kwargs,
) -> None:
    """Stamp task publication time without making metrics a publish dependency."""
    if not isinstance(headers, dict):
        return

    try:
        headers[TASK_PUBLISHED_AT_HEADER] = time.time()
    except Exception:
        # Observability must never prevent task publication.
        return


def _get_task_published_at(task) -> float | None:
    """Read a valid producer timestamp from task message headers."""
    try:
        request = getattr(task, "request", None)
        headers = getattr(request, "headers", None)

        if not isinstance(headers, Mapping):
            return None

        value = headers.get(TASK_PUBLISHED_AT_HEADER)

        if isinstance(value, bool):
            return None

        published_at = float(value)
    except Exception:
        return None

    if not math.isfinite(published_at) or published_at <= 0:
        return None

    return published_at


def _record_task_queue_age(
    task,
    published_at: float,
) -> None:
    """Record publication-to-start delay in aggregate and per-task metrics."""
    if not _task_is_registered(task):
        return

    try:
        if isinstance(published_at, bool):
            return

        published_at = float(published_at)

        if not math.isfinite(published_at) or published_at <= 0:
            return

        observed_at = time.time()
        age_seconds = observed_at - published_at

        # A negative age indicates clock skew or an invalid timestamp.
        if not math.isfinite(age_seconds) or age_seconds < 0:
            return

        queue_age_ms = age_seconds * 1000.0

        metric_keys = (
            CELERY_AGGREGATE_KEY,
            _task_metric_key(_task_name(task)),
        )
    except Exception:
        return

    for key in metric_keys:
        _increment_redis_metric(key, "queue_age_samples")
        _increment_redis_metric(
            key,
            "queue_age_ms_total",
            queue_age_ms,
        )
        _set_redis_metric(
            key,
            "last_queue_age_ms",
            queue_age_ms,
        )
        _set_redis_metric(
            key,
            "last_queue_age_observed_at",
            observed_at,
        )

    redis = _get_redis_client()

    if redis is None:
        return

    for key in metric_keys:
        try:
            current_max = _to_float(
                redis.hget(key, "max_queue_age_ms")
            )

            if not math.isfinite(current_max) or current_max < 0:
                current_max = 0.0

            if queue_age_ms > current_max:
                _set_redis_metric(
                    key,
                    "max_queue_age_ms",
                    queue_age_ms,
                )
        except Exception:
            # Failed metric reads must not affect task execution.
            continue

def _handle_task_prerun(
    task_id=None,
    task=None,
    **kwargs,
) -> None:
    if task is None:
        return

    if not _task_is_registered(task):
        return

    if not task_id:
        return

    published_at = _get_task_published_at(task)
    if published_at is not None:
        _record_task_queue_age(
            task=task,
            published_at=published_at,
        )

    _TASK_START_TIMES[str(task_id)] = (
        time.perf_counter()
    )

    task_name = _task_name(task)

    _increment_redis_metric(
        CELERY_AGGREGATE_KEY,
        "started_tasks",
    )

    _increment_redis_metric(
        _task_metric_key(task_name),
        "started_tasks",
    )


def _handle_task_postrun(
    task_id=None,
    task=None,
    state=None,
    **kwargs,
) -> None:
    if task is None:
        return

    _record_task_runtime(
        task=task,
        task_id=task_id,
        state=state,
    )


def _handle_task_retry(
    sender=None,
    task_id=None,
    **kwargs,
) -> None:
    task = sender

    if task is None:
        return

    if not _task_is_registered(task):
        return

    task_name = _task_name(task)

    _increment_redis_metric(
        CELERY_AGGREGATE_KEY,
        "retry_events",
    )

    _increment_redis_metric(
        _task_metric_key(task_name),
        "retry_events",
    )

    record_failure_event(
        redis_client=_get_redis_client(),
        event_type="celery.task_retry",
        component="celery",
        severity="warning",
        error_type="TaskRetry",
        task_name=task_name,
        task_id=str(task_id) if task_id else None,
    )


def _handle_task_revoked(
    sender=None,
    task_id=None,
    **kwargs,
) -> None:
    task = sender

    if task is None:
        return

    if not _task_is_registered(task):
        return

    task_name = _task_name(task)

    _increment_redis_metric(
        CELERY_AGGREGATE_KEY,
        "revoked_tasks",
    )

    _increment_redis_metric(
        _task_metric_key(task_name),
        "revoked_tasks",
    )


def init_celery_metrics(
    celery_app,
) -> None:
    if celery_app in _REGISTERED_CELERY_APPS:
        return

    _REGISTERED_CELERY_APPS.add(
        celery_app
    )

    before_task_publish.connect(
        _handle_before_task_publish,
        weak=False,
        dispatch_uid=(
            "clinic_celery_metrics_before_task_publish"
        ),
    )

    task_prerun.connect(
        _handle_task_prerun,
        weak=False,
        dispatch_uid=(
            "clinic_celery_metrics_task_prerun"
        ),
    )

    task_postrun.connect(
        _handle_task_postrun,
        weak=False,
        dispatch_uid=(
            "clinic_celery_metrics_task_postrun"
        ),
    )

    task_retry.connect(
        _handle_task_retry,
        weak=False,
        dispatch_uid=(
            "clinic_celery_metrics_task_retry"
        ),
    )

    task_revoked.connect(
        _handle_task_revoked,
        weak=False,
        dispatch_uid=(
            "clinic_celery_metrics_task_revoked"
        ),
    )

    state = getattr(
        celery_app,
        "extensions",
        None,
    )

    if state is None or not isinstance(
        state,
        dict,
    ):
        state = {}
        celery_app.extensions = state

    state[CELERY_METRICS_STATE_KEY] = {
        "registered": True,
    }


def _collect_worker_stats(
    stats: dict[str, Any] | None,
) -> dict[str, Any]:
    if not stats:
        return {
            "worker_count": 0,
            "total_concurrency": 0,
            "worker_pids": [],
            "worker_task_totals": {},
            "worker_rusage": {},
        }

    total_concurrency = 0
    worker_pids: list[int] = []
    worker_task_totals: dict[str, int] = {}
    worker_rusage: dict[str, Any] = {}

    for worker_name, data in stats.items():
        if not isinstance(data, dict):
            continue

        pool = data.get("pool", {})

        if isinstance(pool, dict):
            total_concurrency += _to_int(
                pool.get(
                    "max-concurrency"
                )
            )

        pid = _to_int(
            data.get("pid")
        )

        if pid > 0:
            worker_pids.append(pid)

        totals = data.get("total", {})

        if isinstance(totals, dict):
            for task_name, count in totals.items():
                worker_task_totals[
                    task_name
                ] = (
                    worker_task_totals.get(
                        task_name,
                        0,
                    )
                    + _to_int(count)
                )

        rusage = data.get("rusage")

        if isinstance(rusage, dict):
            worker_rusage[
                worker_name
            ] = rusage

    return {
        "worker_count": len(stats),
        "total_concurrency": total_concurrency,
        "worker_pids": worker_pids,
        "worker_task_totals": worker_task_totals,
        "worker_rusage": worker_rusage,
    }


def _count_active_tasks(
    active: dict[str, Any] | None,
) -> int:
    if not active:
        return 0

    return sum(
        len(tasks)
        for tasks in active.values()
        if isinstance(tasks, list)
    )


def _count_reserved_tasks(
    reserved: dict[str, Any] | None,
) -> int:
    if not reserved:
        return 0

    return sum(
        len(tasks)
        for tasks in reserved.values()
        if isinstance(tasks, list)
    )


def _count_scheduled_tasks(
    scheduled: dict[str, Any] | None,
) -> int:
    if not scheduled:
        return 0

    return sum(
        len(tasks)
        for tasks in scheduled.values()
        if isinstance(tasks, list)
    )


def _find_long_running_tasks(
    active: dict[str, Any] | None,
    threshold_seconds: float,
) -> list[dict[str, Any]]:
    if not active:
        return []

    now = time.time()
    results: list[dict[str, Any]] = []

    for worker_name, tasks in active.items():
        if not isinstance(tasks, list):
            continue

        for task in tasks:
            if not isinstance(task, dict):
                continue

            started = task.get(
                "time_start"
            )

            if started is None:
                continue

            try:
                runtime = (
                    now - float(started)
                )
            except (TypeError, ValueError):
                continue

            if runtime < threshold_seconds:
                continue

            results.append(
                {
                    "worker": worker_name,
                    "task_id": task.get("id"),
                    "name": task.get("name"),
                    "runtime_seconds": runtime,
                }
            )

    return results


def _queue_age_metric_fields(
    values: Mapping[str, Any],
) -> dict[str, int | float]:
    """Build queue-age statistics from a Redis metrics hash."""
    samples = max(
        0,
        _to_int(values.get("queue_age_samples")),
    )
    total_ms = _to_float(values.get("queue_age_ms_total"))
    last_ms = _to_float(values.get("last_queue_age_ms"))
    max_ms = _to_float(values.get("max_queue_age_ms"))
    observed_at = _to_float(
        values.get("last_queue_age_observed_at")
    )

    if not math.isfinite(total_ms) or total_ms < 0:
        total_ms = 0.0
    if not math.isfinite(last_ms) or last_ms < 0:
        last_ms = 0.0
    if not math.isfinite(max_ms) or max_ms < 0:
        max_ms = 0.0
    if not math.isfinite(observed_at) or observed_at < 0:
        observed_at = 0.0

    return {
        "queue_age_samples": samples,
        "queue_age_ms_total": total_ms,
        "average_queue_age_ms": (
            total_ms / samples if samples else 0.0
        ),
        "last_queue_age_ms": last_ms,
        "max_queue_age_ms": max_ms,
        "last_queue_age_observed_at": observed_at,
    }

def _collect_task_metrics(
    client,
) -> dict[str, dict[str, Any]]:
    if client is None:
        return {}

    results: dict[str, dict[str, Any]] = {}

    try:
        keys = client.scan_iter(
            match=(
                f"{CELERY_TASK_PREFIX}*"
            ),
            count=1000,
        )

        for key in keys:
            key_string = str(key)

            if not key_string.startswith(
                CELERY_TASK_PREFIX
            ):
                continue

            name = key_string[
                len(CELERY_TASK_PREFIX):
            ]

            values = client.hgetall(key)

            if not isinstance(
                values,
                dict,
            ):
                continue

            executed = _to_int(
                values.get(
                    "executed_tasks"
                )
            )

            runtime_total = _to_float(
                values.get(
                    "runtime_ms_total"
                )
            )

            results[name] = {
                "started_tasks": _to_int(
                    values.get(
                        "started_tasks"
                    )
                ),
                "executed_tasks": executed,
                "succeeded_tasks": _to_int(
                    values.get(
                        "succeeded_tasks"
                    )
                ),
                "failed_tasks": _to_int(
                    values.get(
                        "failed_tasks"
                    )
                ),
                "retry_events": _to_int(
                    values.get(
                        "retry_events"
                    )
                ),
                "revoked_tasks": _to_int(
                    values.get(
                        "revoked_tasks"
                    )
                ),
                "runtime_ms_total": runtime_total,
                **_queue_age_metric_fields(values),
                "average_runtime_ms": (
                    runtime_total / executed
                    if executed
                    else 0.0
                ),
                "last_runtime_ms": _to_float(
                    values.get(
                        "last_runtime_ms"
                    )
                ),
                "max_runtime_ms": _to_float(
                    values.get(
                        "max_runtime_ms"
                    )
                ),
                "long_running_tasks": _to_int(
                    values.get(
                        "long_running_tasks"
                    )
                ),
            }

    except Exception:
        return {}

    return results


def collect_celery_metrics(
    *,
    celery_app=None,
    client=None,
    queue_names: Iterable[str] | None = None,
    inspect_timeout: float = DEFAULT_INSPECT_TIMEOUT,
    long_running_threshold_seconds: float = (
        DEFAULT_LONG_RUNNING_THRESHOLD_SECONDS
    ),
) -> dict[str, Any]:
    if inspect_timeout <= 0:
        raise ValueError(
            "inspect_timeout must be greater than zero"
        )

    if long_running_threshold_seconds <= 0:
        raise ValueError(
            "long_running_threshold_seconds "
            "must be greater than zero"
        )

    if celery_app is None:
        celery_app = extensions.celery

    if client is None:
        client = _get_redis_client()

    init_celery_metrics(
        celery_app
    )

    inspector = celery_app.control.inspect(
        timeout=inspect_timeout
    )

    ping = None
    stats = None
    active = None
    reserved = None
    scheduled = None
    registered = None

    for attribute, target in (
        ("ping", "ping"),
        ("stats", "stats"),
        ("active", "active"),
        ("reserved", "reserved"),
        ("scheduled", "scheduled"),
        ("registered", "registered"),
    ):
        try:
            value = getattr(
                inspector,
                target,
            )()

            if attribute == "ping":
                ping = value
            elif attribute == "stats":
                stats = value
            elif attribute == "active":
                active = value
            elif attribute == "reserved":
                reserved = value
            elif attribute == "scheduled":
                scheduled = value
            elif attribute == "registered":
                registered = value

        except Exception:
            continue

    worker_stats = _collect_worker_stats(
        stats
    )

    worker_count = worker_stats[
        "worker_count"
    ]

    active_tasks = _count_active_tasks(
        active
    )

    reserved_tasks = _count_reserved_tasks(
        reserved
    )

    scheduled_tasks = _count_scheduled_tasks(
        scheduled
    )

    total_concurrency = worker_stats[
        "total_concurrency"
    ]

    utilization = (
        (
            active_tasks
            / total_concurrency
        )
        * 100.0
        if total_concurrency > 0
        else 0.0
    )

    broker_healthy = False

    if client is not None:
        try:
            broker_healthy = bool(
                client.ping()
            )
        except Exception:
            broker_healthy = False

    configured_queues = (
        list(queue_names)
        if queue_names is not None
        else [
            celery_app.conf.get(
                "task_default_queue",
                DEFAULT_QUEUE,
            )
        ]
    )

    queue_depths: dict[str, int] = {}
    queue_depths_healthy = (
        client is not None and bool(configured_queues)
    )
    queue_depth_errors: dict[str, str] = {}

    if client is not None:
        for queue_name in configured_queues:
            if (
                not isinstance(
                    queue_name,
                    str,
                )
                or not queue_name
            ):
                raise ValueError(
                    "Queue names must be "
                    "non-empty strings"
                )

            try:
                raw_depth = client.llen(queue_name)

                if (
                    isinstance(raw_depth, bool)
                    or not isinstance(raw_depth, int)
                    or raw_depth < 0
                ):
                    raise ValueError(
                        "Redis LLEN returned an invalid queue depth"
                    )

                queue_depths[queue_name] = raw_depth
            except Exception as exc:
                queue_depths_healthy = False
                queue_depth_errors[queue_name] = type(exc).__name__

    long_running_tasks = (
        _find_long_running_tasks(
            active,
            long_running_threshold_seconds,
        )
    )

    aggregate: dict[str, Any] = {}

    if client is not None:
        try:
            values = client.hgetall(
                CELERY_AGGREGATE_KEY
            )

            if isinstance(values, dict):
                executed = _to_int(
                    values.get(
                        "executed_tasks"
                    )
                )

                runtime_total = _to_float(
                    values.get(
                        "runtime_ms_total"
                    )
                )

                aggregate = {
                    "started_tasks": _to_int(
                        values.get(
                            "started_tasks"
                        )
                    ),
                    "executed_tasks": executed,
                    "succeeded_tasks": _to_int(
                        values.get(
                            "succeeded_tasks"
                        )
                    ),
                    "failed_tasks": _to_int(
                        values.get(
                            "failed_tasks"
                        )
                    ),
                    "retry_events": _to_int(
                        values.get(
                            "retry_events"
                        )
                    ),
                    "revoked_tasks": _to_int(
                        values.get(
                            "revoked_tasks"
                        )
                    ),
                    "runtime_ms_total": runtime_total,
                    **_queue_age_metric_fields(values),
                    "average_runtime_ms": (
                        runtime_total / executed
                        if executed
                        else 0.0
                    ),
                    "last_runtime_ms": _to_float(
                        values.get(
                            "last_runtime_ms"
                        )
                    ),
                    "max_runtime_ms": _to_float(
                        values.get(
                            "max_runtime_ms"
                        )
                    ),
                    "long_running_tasks": _to_int(
                        values.get(
                            "long_running_tasks"
                        )
                    ),
                }

        except Exception:
            aggregate = {}

    return {
        "timestamp": _utcnow().isoformat(),
        "healthy": (
            broker_healthy
            and worker_count > 0
            and queue_depths_healthy
        ),
        "broker_healthy": broker_healthy,
        "workers_healthy": (
            worker_count > 0
        ),
        "worker_count": worker_count,
        "worker_names": (
            sorted(
                ping.keys()
            )
            if isinstance(
                ping,
                dict,
            )
            else []
        ),
        "total_concurrency": total_concurrency,
        "active_tasks": active_tasks,
        "reserved_tasks": reserved_tasks,
        "scheduled_tasks": scheduled_tasks,
        "worker_utilization_percent": utilization,
        "queue_depths": queue_depths,
        "queue_depths_healthy": queue_depths_healthy,
        "queue_depth_errors": queue_depth_errors,
        "registered_task_counts": {
            worker: len(tasks)
            for worker, tasks in (
                registered.items()
                if isinstance(
                    registered,
                    dict,
                )
                else []
            )
            if isinstance(tasks, list)
        },
        "worker_task_totals": worker_stats[
            "worker_task_totals"
        ],
        "worker_pids": worker_stats[
            "worker_pids"
        ],
        "worker_rusage": worker_stats[
            "worker_rusage"
        ],
        "long_running_active_tasks": (
            long_running_tasks
        ),
        "long_running_active_count": len(
            long_running_tasks
        ),
        "aggregate_task_metrics": aggregate,
        "task_metrics": _collect_task_metrics(
            client
        ),
    }