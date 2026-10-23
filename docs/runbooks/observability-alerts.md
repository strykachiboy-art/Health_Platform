# Operational Alert Evaluation and Delivery

## What runs

Celery Beat schedules `refresh_operational_alert_state` once per minute. The
registered task collects the platform operational snapshot, evaluates the
existing readiness, Redis, Celery, CPU, memory, and swap thresholds, records
alert lifecycle state in Redis, and emits lifecycle events to the application
logger.

## Lifecycle and dashboard

The Redis-backed state distinguishes new, ongoing, escalated, and resolved
conditions. Only `new`, `escalated`, and `resolved` transitions are emitted as
`operational_alert` log records; repeated ongoing conditions do not generate a
notification on every minute tick. The Admin/Super Admin operations dashboard
reads the active alert state. A failed snapshot or unavailable Redis state store
fails the Celery task so the worker records an unsuccessful task execution
rather than claiming the alert refresh succeeded.

## Log routing and severity

Each event is emitted as a compact JSON payload with an `event` value of
`operational_alert` and a nested `alert` object containing the lifecycle event
ID, type, severity, component, resource where applicable, timestamp, observed
value, threshold, and occurrence count. `critical` alerts use CRITICAL log
level; `warning` alerts use WARNING; other supported severities use INFO.

Production must forward these logs to an access-controlled centralized log
sink and configure detection/routing for `operational_alert` records at WARNING
and CRITICAL levels. Validate that the receiver actually notifies the intended
on-call operator. This code supplies the event and log sink integration; it
does not claim that an external paging service, dashboard, or log receiver has
been configured or exercised.

## Operator response

Use `docs/runbooks/observability-incident.md` for scope, correlation, safe
containment, recovery verification, and incident closure. Review the operations
and failure dashboards; correlate event ID, request ID/trace ID when present,
component, and timestamp. Never copy secrets or PHI into incident records.

## Verification evidence

Record the commit SHA, targeted test result, deployment revision, Celery Beat
and worker status, observed alert event ID, receiving log target, and a witnessed
end-to-end notification test. Code-level tests do not by themselves prove
production delivery or on-call acknowledgement.
