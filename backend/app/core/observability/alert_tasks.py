from __future__ import annotations

from typing import Any

from app import extensions
from app.core.observability.alert_delivery import (
    LoggingAlertDeliverySink,
    build_alert_delivery_events,
    deliver_alert_events,
)
from app.core.observability.alert_state import (
    record_operational_alerts,
)
from app.core.observability.alerts import (
    evaluate_operational_alerts,
)
from app.core.observability.operational_metrics import (
    collect_operational_snapshot,
)
from app.extensions import celery


@celery.task(name="refresh_operational_alert_state")
def refresh_operational_alert_state() -> dict[str, Any]:
    """Refresh persistent alert lifecycle state and emit lifecycle events."""
    snapshot = collect_operational_snapshot()
    if not isinstance(snapshot, dict):
        raise TypeError("operational snapshot must be a dictionary")

    timestamp = snapshot.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError("operational snapshot requires a timestamp")

    alerts = evaluate_operational_alerts(snapshot)
    state = record_operational_alerts(
        alerts,
        redis_client=extensions.redis_client,
        timestamp=timestamp,
    )
    events = build_alert_delivery_events(state)
    delivered_count = deliver_alert_events(
        events,
        sink=LoggingAlertDeliverySink(),
    )

    return {
        "success": True,
        "timestamp": state["timestamp"],
        "active_count": len(state["active"]),
        "new_count": len(state["new"]),
        "escalated_count": len(state["escalated"]),
        "resolved_count": len(state["resolved"]),
        "delivery_event_count": delivered_count,
    }
