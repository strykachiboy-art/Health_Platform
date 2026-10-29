# Clinic System Pro v5 - Operational Runbooks

These runbooks support Phase 4 Observability / Operations.

## Operating principles

- Protect patient safety first.
- Do not expose PHI, credentials, tokens, request bodies, or secrets during troubleshooting.
- Use request IDs and trace IDs to correlate failures.
- Prefer read-only inspection before changing infrastructure state.
- Record operational actions in the incident record.
- Make the smallest safe recovery action necessary.
- Verify recovery after every intervention.

## Runbooks

### Wave 4 Autoscaling Evidence

- [Wave 4 autoscaling operational evidence](wave4-autoscaling-operational-evidence.md)

### Wave 4 Capacity and Resilience Evidence

- [Gate 6 system-mixed capacity evidence](wave4-gate6-system-mixed-capacity-evidence.md)
- [Gate 7 system-mixed capacity boundary evidence](wave4-gate7-system-mixed-capacity-evidence.md)
- [Gate 8 system-mixed spike resilience evidence](wave4-gate8-system-mixed-spike-resilience-evidence.md)
- [Gate 9 system-mixed soak stability evidence](wave4-gate9-system-mixed-soak-stability-evidence.md)
- [Gate 10 controlled failure recovery evidence](wave4-gate10-system-mixed-recovery-evidence.md)

### Observability Incident
Use for application failures, HTTP 5xx spikes, correlated trace failures, or unknown operational incidents.

### Redis Degradation
Use when Redis health, cache behavior, token revocation, rate limiting, Celery broker health, or Socket.IO coordination is degraded.

### Celery Degradation
Use when workers disappear, queues grow, tasks fail or retry, or long-running tasks accumulate.

### Database Degradation
Use when readiness checks fail, database latency increases, queries fail, or the application cannot establish healthy database connections.

## Standard verification

Liveness:

GET /health/live

Readiness:

GET /health/ready

Operational dashboard:

GET /api/v1/operations

Failure dashboard:

GET /api/v1/operations/failures

The operations endpoints require an authenticated Admin or Super Admin account.

## Incident correlation

Use:

- event_id
- request_id
- trace_id
- component
- event_type
- timestamp

Do not use request bodies, authorization headers, cookies, patient identifiers, or arbitrary exception messages as correlation data.

## Recovery closure

An incident is operationally recovered after:

1. The affected dependency is healthy.
2. Application readiness is healthy where applicable.
3. Failure volume is returning toward baseline.
4. Active critical alerts are resolved or understood.
5. The operator records the recovery action and verification result.


### Backup / Restore Security Drill
Use `docs/security/BACKUP_RESTORE_DRILL.md` for controlled encrypted backup
and restore operations, temporary plaintext cleanup, recovery evidence, and
restore verification.


### Security Operations
Use `docs/security/SECURITY_OPERATIONS.md` for encryption-key operations,
secret rotation, TLS renewal, configuration drift, and security incident evidence.

## Current Backend Resilience Position

Current resilience engineering covers controlled dependency failure, worker failure,
provider/storage failure, timeout behavior, unknown outcomes, idempotency, and recovery.

The current resilience baseline is ``66 passed in 95.74s``.
- [Gate 11 concurrency correctness evidence](wave4-gate11-concurrency-correctness-evidence.md)
