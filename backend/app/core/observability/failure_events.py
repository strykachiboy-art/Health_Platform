from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


FAILURE_EVENTS_KEY = (
    "observability:failure:events:v1"
)

FAILURE_EVENT_MAX_ITEMS = 200
FAILURE_EVENT_TTL_SECONDS = 7 * 24 * 60 * 60

FAILURE_EVENT_TYPES = frozenset(
    {
        "http.5xx",
        "application.error",
        "celery.task_failure",
        "celery.task_retry",
        "circuit.opened",
        "circuit.half_open",
        "circuit.closed",
        "circuit.rejected",
        "circuit.state_store_degraded",
    }
)

MAX_FIELD_LENGTH = 256


def _utcnow() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _safe_text(
    value: Any,
) -> str | None:
    if value is None:
        return None

    value = str(value)
    value = (
        value
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )

    if not value:
        return None

    return value[:MAX_FIELD_LENGTH]


def _validate_event_type(
    event_type: str,
) -> str:
    value = _safe_text(event_type)

    if value not in FAILURE_EVENT_TYPES:
        raise ValueError(
            f"Unsupported failure event type: {event_type}"
        )

    return value


def record_failure_event(
    *,
    redis_client,
    event_type: str,
    component: str,
    severity: str = "error",
    timestamp: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    method: str | None = None,
    route: str | None = None,
    status: int | None = None,
    error_type: str | None = None,
    task_name: str | None = None,
    task_id: str | None = None,
    circuit_name: str | None = None,
    provider: str | None = None,
    operation: str | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    generation: int | None = None,
    failure_count: int | None = None,
    failure_threshold: int | None = None,
    opened_until_ms: int | None = None,
    failure_class: str | None = None,
) -> dict[str, Any] | None:
    if redis_client is None:
        return None

    event_type = _validate_event_type(
        event_type
    )

    component = _safe_text(component)

    if component is None:
        raise ValueError(
            "component is required"
        )

    severity = (
        _safe_text(severity)
        or "error"
    )

    event = {
        "event_id": uuid4().hex,
        "timestamp": (
            timestamp
            or _utcnow()
        ),
        "event_type": event_type,
        "component": component,
        "severity": severity,
        "request_id": _safe_text(
            request_id
        ),
        "trace_id": _safe_text(
            trace_id
        ),
        "method": _safe_text(method),
        "route": _safe_text(route),
        "status": status,
        "error_type": _safe_text(
            error_type
        ),
        "task_name": _safe_text(
            task_name
        ),
        "task_id": _safe_text(
            task_id
        ),
        "circuit_name": _safe_text(
            circuit_name
        ),
        "provider": _safe_text(
            provider
        ),
        "operation": _safe_text(
            operation
        ),
        "previous_state": _safe_text(
            previous_state
        ),
        "new_state": _safe_text(
            new_state
        ),
        "generation": generation,
        "failure_count": failure_count,
        "failure_threshold": failure_threshold,
        "opened_until_ms": opened_until_ms,
        "failure_class": _safe_text(
            failure_class
        ),
    }

    payload = json.dumps(
        event,
        sort_keys=True,
        separators=(",", ":"),
    )

    try:
        redis_client.lpush(
            FAILURE_EVENTS_KEY,
            payload,
        )

        redis_client.ltrim(
            FAILURE_EVENTS_KEY,
            0,
            FAILURE_EVENT_MAX_ITEMS - 1,
        )

        redis_client.expire(
            FAILURE_EVENTS_KEY,
            FAILURE_EVENT_TTL_SECONDS,
        )
    except Exception:
        return None

    return event


def collect_failure_events(
    *,
    redis_client,
    limit: int = 50,
) -> list[dict[str, Any]]:
    events, _ = collect_failure_events_with_status(
        redis_client=redis_client,
        limit=limit,
    )
    return events


def collect_failure_events_with_status(
    *,
    redis_client,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], bool]:
    if redis_client is None:
        return [], False

    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit <= 0
    ):
        raise ValueError(
            "limit must be a positive integer"
        )

    limit = min(
        limit,
        FAILURE_EVENT_MAX_ITEMS,
    )

    try:
        raw_events = redis_client.lrange(
            FAILURE_EVENTS_KEY,
            0,
            limit - 1,
        )
    except Exception:
        return [], False

    events = []

    for raw_event in raw_events:
        if isinstance(
            raw_event,
            bytes,
        ):
            raw_event = raw_event.decode(
                "utf-8",
                errors="replace",
            )

        try:
            event = json.loads(
                str(raw_event)
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if not isinstance(
            event,
            dict,
        ):
            continue

        events.append(event)

    return events, True


def summarize_failure_events(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(
        events,
        list,
    ):
        raise TypeError(
            "events must be a list"
        )

    valid_events = [
        event
        for event in events
        if isinstance(
            event,
            dict,
        )
    ]

    component_counts = Counter(
        str(
            event.get(
                "component",
                "unknown",
            )
        )
        for event in valid_events
    )

    event_type_counts = Counter(
        str(
            event.get(
                "event_type",
                "unknown",
            )
        )
        for event in valid_events
    )

    severity_counts = Counter(
        str(
            event.get(
                "severity",
                "unknown",
            )
        )
        for event in valid_events
    )

    latest_failure_at = None

    if valid_events:
        latest_failure_at = (
            valid_events[0].get(
                "timestamp"
            )
        )

    return {
        "recent_failure_count": len(
            valid_events
        ),
        "by_component": dict(
            sorted(
                component_counts.items()
            )
        ),
        "by_event_type": dict(
            sorted(
                event_type_counts.items()
            )
        ),
        "by_severity": dict(
            sorted(
                severity_counts.items()
            )
        ),
        "latest_failure_at": (
            latest_failure_at
        ),
    }
