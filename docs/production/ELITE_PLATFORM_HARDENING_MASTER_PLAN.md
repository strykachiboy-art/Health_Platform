# Clinic System Pro — Elite Platform Hardening Master Plan

**Purpose:** A repo-backed implementation roadmap for making Clinic System Pro scalable, secure, resilient, observable, recoverable, and safe to operate.

**Document status:** Planning baseline. This document does not claim that every listed control has been implemented, verified, deployed, or operated in production.

**Core rule:** No feature is considered production-ready merely because code exists or a test suite passes. Verify the real execution path, failure behaviour, operational configuration, and recovery procedure.

---

## 1. Mission and engineering principles

Clinic System Pro is a healthcare platform. Changes must preserve clinical correctness, financial correctness, patient privacy, tenant isolation, and recoverability while improving capacity and reliability.

Non-negotiable principles:

1. Inspect current GitHub source and exact anchors before changing code or deployment configuration.
2. Treat GitHub as the source of truth for repository state; verify the current branch and HEAD before work.
3. Make small, reviewable slices. Do not implement this roadmap as one giant patch.
4. Preserve existing tests, safeguards, and unrelated work. Never weaken tests to obtain a green result.
5. Test actual execution paths. The existence of a helper, setting, configuration file, or test fixture does not prove that production code uses it.
6. Preserve idempotency, tenant isolation, auditability, and recovery semantics for clinical and financial operations.
7. Use measured workload and capacity evidence instead of arbitrary concurrency or performance targets.
8. Use the status vocabulary precisely: **IMPLEMENTED**, **VERIFIED**, **PRODUCTION-LIKE EXECUTED**, and **PRODUCTION OPERATED** are different states.
9. Do not report a deployment, rollback, restore drill, load test, or operational control as executed unless evidence shows it actually happened.
10. Document remaining limitations, assumptions, and operational risks.

## 2. Initial repository baseline

The initial review inspected the `feat/hie-endpoint-allowlist-enforcement` branch. Reconfirm the branch and current HEAD before using any finding below as a current-state assertion; the branch may differ from `main` and may not be deployed.

Observed in that review:

- Flask application with a Gunicorn runtime.
- Nginx reverse-proxy configuration.
- PostgreSQL as the production datastore.
- Redis supporting several shared infrastructure functions.
- Celery worker and Beat services.
- Redis-backed rate-limit storage.
- Flask-SocketIO configured with a Redis message queue outside testing.
- Health/readiness routes and request/database/Celery observability hooks.
- HIE endpoint-enforcement work on the inspected feature branch.
- A Gunicorn Docker command using the gevent worker class and `--workers 1`.
- An Nginx configuration proxying to a single backend service name.
- A production runbook describing production-like validation without establishing that multi-instance load balancing has been verified in production.

These are inspection findings, not proof of production deployment or full-platform enforcement.

Reference source files from the inspected branch:

- [Backend Dockerfile](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/Dockerfile)
- [Nginx configuration](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/deployment/nginx/nginx.conf)
- [Phase 6 Compose overlay](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/compose.phase6.yaml)
- [Application configuration](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/config.py)
- [Production deployment runbook](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/docs/production/DEPLOYMENT_RUNBOOK.md)

---

## Phase 0 — Establish the baseline and protect existing work

### Objectives

Create an auditable starting point before modifying application or infrastructure architecture.

### Work

1. Verify branch, HEAD, parent commit, remote, and worktree status.
2. Inventory Compose files, deployment overlays, Nginx configuration, container definitions, environment templates, worker commands, and CI workflows.
3. Record application, worker, Beat, proxy, database, and Redis image/runtime versions.
4. Record relevant test results, duration, failures, skips, and existing execution artifacts.
5. Inventory retries, timeouts, rate limits, idempotency controls, health checks, and graceful-shutdown behaviour.
6. Define exact file scope and preconditions for each proposed change.
7. Preserve unrelated modifications and untracked artifacts.
8. Record baseline CPU, memory, request latency, database connections, Redis use, task queue depth, and task age under representative workloads.

### Acceptance criteria

- Source state and evidence are recorded and reproducible.
- Each work slice has explicit scope and test requirements.
- Existing safeguards and tests remain intact.
- No patch is based on guessed source anchors.

## Phase 1 — Complete application, infrastructure, and network inventory

### Objectives

Find every real network boundary and important bottleneck before deciding on architecture.

### Work

Search actual execution paths for:

- HTTP clients and SDK-owned HTTP transports.
- SMTP/email connections and future SMS transports.
- Payment gateways, HIE/FHIR, Google OAuth, and AI-provider calls.
- Webhook senders/receivers, URL fetching, remote files, storage, and backups.
- PostgreSQL and Redis clients, connection pools, and service ports.
- Celery producers, queues, workers, Beat schedules, and task side effects.
- Socket.IO/WebSocket connections.
- Reverse proxies, public port bindings, service networks, and environment-driven endpoints.
- Uploads, long-running requests, streaming endpoints, expensive queries, and large responses.

For each actual outbound connection, record the source file, protocol, destination, destination owner (fixed/operator/tenant configurable), credentials and data transmitted, TLS policy, timeouts, redirects, DNS/IP checks, size limits, retries, idempotency semantics, and tests.

For inbound operations, record auth requirements, tenant isolation, resource cost, rate limits, concurrency behaviour, and failure modes.

### Acceptance criteria

Produce a repository-backed inventory with statuses:

- IMPLEMENTED
- VERIFIED
- PRODUCTION-LIKE EXECUTED
- PRODUCTION OPERATED
- MISSING
- NOT APPLICABLE

Use no status without supporting evidence.

## Phase 2 — Implement and verify real inbound load balancing

### Objectives

Scale API capacity while preserving correctness, isolation, and recovery.

### Work

**Topology and routing**

1. Design a trusted ingress/load balancer routing to multiple API instances.
2. Confirm PostgreSQL, Redis, shared storage, Socket.IO, and operational tooling are ready for the selected replica count.
3. Configure upstream routing to healthy instances and document whether health checking is active, passive, or handled by an orchestrator.
4. Bound connection, request, send, and read times; configure request-body and connection limits.
5. Reject unexpected hostnames, restrict direct backend access, and correctly set trusted forwarding headers.
6. Drain instances safely before shutdown or deployment; define the behaviour of in-flight requests and long-lived connections.
7. Handle WebSocket upgrades and long-lived connections correctly.

**Application runtime**

1. Review Gunicorn worker class, process count, and concurrency using measured traffic, memory, CPU, and I/O behaviour.
2. Configure graceful worker shutdown and resource limits.
3. Prevent slow dependencies from exhausting all request capacity.
4. Ensure shared state needed across requests is not held only in process-local memory.
5. Account for the database connections multiplied by API replica and worker counts.

**Multi-instance correctness**

1. Verify JWT revocation and shared rate-limit state across instances.
2. Verify tenant and authorization context across replicas.
3. Confirm idempotency for operations that clients or proxies may retry after timeouts.
4. Verify uploads and generated files are available to all required instances.
5. Review process-local caches and mutable globals.

### Acceptance criteria

- Runtime evidence shows requests distributed across multiple instances.
- Unhealthy instances stop receiving new traffic as designed.
- Instance removal/restart does not corrupt clinical or financial state.
- Direct untrusted backend access is blocked where proxy-only ingress is required.
- Load balancing is demonstrated by tests, metrics, or execution artifacts, not inferred from the presence of Nginx.

## Phase 3 — Harden Socket.IO and real-time communication

### Work

1. Verify Redis message-queue behaviour across application instances.
2. Determine which transports are supported by clients (WebSocket, polling, or both).
3. If long polling remains enabled, verify session-affinity/routing requirements; alternatively consider WebSocket-only transport only if client and deployment requirements permit it.
4. Preserve authentication, origin checks, and tenant authorization.
5. Test reconnects during instance restart and temporary Redis interruption.
6. Verify message delivery without cross-tenant leakage or unintended duplication.
7. Bound connection counts, message sizes, heartbeats, and slow-client resource consumption.
8. Monitor connection count, reconnect rate, queue errors, and delivery failures.

### Acceptance criteria

- Real-time delivery works across instances.
- Reconnect and outage behaviour is verified.
- No cross-clinic delivery occurs.
- Proxy changes do not break existing Socket.IO handlers or auth.

## Phase 4 — Control database capacity and consistency

### Work

1. Inventory SQLAlchemy pool size, overflow, timeout, session lifecycle, and connection cleanup.
2. Calculate connection budget across API replicas, workers, Beat, migrations, monitoring, and operators.
3. Set pool limits within the PostgreSQL connection budget.
4. Consider PgBouncer only if measurements justify it and transaction-pooling semantics are compatible.
5. Measure slow queries, indexes, transaction length, and lock contention.
6. Review session scope and detached-instance behaviour.
7. Test concurrent updates to appointments, inventory, prescriptions, payments, and other critical records.
8. Preserve tenant isolation and authorization on all data access paths.
9. Use expand-and-contract migration approaches where old and new application versions must coexist.
10. Verify backup and restore compatibility before major schema/deployment transitions.
11. Ensure readiness checks do not amplify database load during an outage.

### Acceptance criteria

- Maximum planned process count fits the database connection budget.
- Concurrent operations preserve domain invariants.
- Failures do not cause silent loss or partial success.
- Migration and rollback compatibility is documented and tested where rollback is supported.

## Phase 5 — Harden Celery, Redis, and background processing

### Celery work

1. Inventory every task, queue, schedule, retry, delivery guarantee, and external side effect.
2. Tune concurrency and prefetch from measurements.
3. Preserve established acknowledgement and worker-loss recovery settings.
4. Make retryable tasks idempotent or protect side effects with durable deduplication.
5. Use bounded exponential backoff and jitter where appropriate.
6. Separate long-running, latency-sensitive, and resource-intensive tasks into queues only where justified.
7. Define task time limits and graceful shutdown behaviour.
8. Prevent permanent failures from retrying forever.
9. Monitor queue depth, oldest task age, runtime, retries, and failed-task volume.
10. Reconcile unknown outcomes when a worker fails after an external side effect.

### Beat work

1. Ensure intended scheduler ownership and avoid uncoordinated Beat replicas.
2. Protect scheduled work from unsafe overlap.
3. Make scheduled tasks safe under restart and retry.
4. Test worker and scheduler downtime.

### Redis work

1. Define durability needs separately for each Redis use.
2. Separate Redis workloads/instances only when justified by workload or fault isolation.
3. Deliberately configure memory limits, eviction, persistence, authentication, and network access.
4. Define which data may safely be lost and which queue/coordination functions need stronger recovery.
5. Monitor memory, latency, connections, availability, and evictions.
6. Test Redis outage and recovery for each dependent feature.

### Acceptance criteria

- Backlogs recover without uncontrolled resource use.
- Retries do not duplicate external side effects.
- Scheduled jobs have safe ownership and overlap behaviour.
- Queue delays and worker failures trigger actionable alerts.

## Phase 6 — Implement platform-wide outbound network security

### Objectives

Prevent SSRF, unauthorized outbound access, and credential/data leakage.

### Shared policy

Classify destinations as:

1. Fixed trusted provider.
2. Operator-configured endpoint.
3. Tenant-configured endpoint.

Tenant configuration must never expand deployment-wide trust. Build a shared policy interface with protocol-specific adapters; HTTP, SMTP, and storage protocols must not be forced into an unsuitable generic transport.

### HTTP integrations

Review every live outbound HTTP execution path, including HIE/FHIR, payment gateways, Google OAuth, AI clients, and any additional APIs.

- Validate the destination before connecting.
- Reject unapproved schemes, hosts, ports, and malformed URL forms.
- Resolve DNS and reject prohibited IP ranges according to policy.
- Resist DNS rebinding by ensuring the actual socket connects to an approved address.
- Preserve TLS certificate and hostname verification.
- Reject redirects by default or revalidate every hop safely.
- Set explicit connection and read timeouts.
- Bound response sizes.
- Prevent authorization credentials from being sent to unintended hosts.
- Redact secrets and PHI from logs/errors.
- Retry only when operation semantics allow it.
- Preserve payment idempotency and unknown-outcome reconciliation.

### SMTP and messaging integrations

- Validate approved SMTP hosts/ports with protocol-appropriate rules.
- Configure STARTTLS or implicit TLS according to provider requirements.
- Verify certificates where TLS is required.
- Restrict tenant control over SMTP destinations.
- Enforce connection/operation timeouts and credential boundaries.
- Apply deployment-level egress restrictions.
- Treat unimplemented providers as unimplemented; do not claim protection of a transport that does not yet exist.

### Network-layer enforcement

Where supported by the deployment environment:

- Block unnecessary outbound traffic.
- Restrict cloud metadata, internal administration, private infrastructure, and other prohibited destinations.
- Allow only documented DNS and approved provider/database/Redis/storage destinations.
- Apply separate policies to API and worker services when their requirements differ.
- Verify the deployed network rules in an actual environment.

### Acceptance criteria

- Every live outbound integration has an explicit destination policy.
- The real provider execution path enforces it.
- DNS/IP, redirects, forbidden destinations, credential leakage, TLS, and timeout paths have focused tests.
- Tests cover transport behaviour, not merely helper-function output.

## Phase 7 — Overload protection and fair resource allocation

### Work

1. Review rate-limit rules, keys, and trusted client-IP handling.
2. Keep rate-limit state shared when API instances share capacity.
3. Define fair per-tenant limits for expensive operations.
4. Bound request concurrency and queues where appropriate.
5. Restrict upload sizes and safe request parsing.
6. Define deadlines for operations that can reasonably be bounded.
7. Isolate slow external calls from time-sensitive workflows.
8. Configure provider-specific connection/concurrency pools.
9. Reject excess work predictably instead of accepting unlimited work.
10. Return appropriate overload responses and retry guidance where safe.
11. Preserve capacity for critical clinical workflows when optional integrations degrade.
12. Tune limits from measured workloads.

### Acceptance criteria

- Overload causes controlled degradation rather than cascading failure.
- One tenant cannot monopolize shared capacity.
- Critical operations have defined capacity protection.
- Rate limiting remains correct across replicas.

## Phase 8 — Standardize timeouts, retries, and recovery

Use explicit failure classes:

- AUTOMATIC RECOVERY
- RETRYABLE
- RECONCILIATION REQUIRED
- USER INTERVENTION REQUIRED
- FAIL CLOSED

### Work

1. Set connection and operation deadlines for each dependency.
2. Classify transient and permanent failures.
3. Use bounded exponential backoff with jitter where appropriate.
4. Prevent synchronized retry storms and define a retry budget across layers.
5. Add circuit breakers only where repeated dependency failure and recovery semantics justify them.
6. Use bulkheads/concurrency limits to isolate failing providers.
7. Make unsafe retries impossible for non-idempotent operations.
8. Reconcile operations with unknown remote outcomes.
9. Persist critical workflow transitions durably.
10. Verify degradation and recovery after dependencies return.
11. Ensure exception handling never converts failure into false success.

### Acceptance criteria

- Unknown outcomes are not silently treated as success/failure.
- Payments and other external side effects remain idempotent or reconcilable.
- Repeated failure does not cause retry amplification.
- Recovery is demonstrated by focused tests.

## Phase 9 — Observability and incident response

### Instrument

**Application:** request rate, latency percentiles, status codes, in-flight requests, worker utilization, auth failures, rate limits, and error rates.

**Infrastructure:** CPU, memory, disk, file descriptors, network saturation, PostgreSQL pool/connection use, slow queries, locks, Redis latency/memory/connections, Celery depth/task age/retries, Nginx upstream errors, and Socket.IO connectivity.

**Security:** rejected outbound destinations, DNS/IP-policy failures, auth denials, repeated abuse patterns, and secret/configuration failures.

### Work

1. Define service-level indicators and objectives based on measured workloads and business needs.
2. Create dependency and application dashboards.
3. Create actionable alerts with severity, ownership, and response guidance.
4. Correlate requests and background jobs where applicable.
5. Redact secrets, tokens, credentials, and PHI.
6. Synchronize timestamps and define audit retention/access policies.
7. Document incident triage, escalation, evidence preservation, and recovery.
8. Verify observability during partial dependency failure.

### Acceptance criteria

- Operators can locate the failing dependency and affected workflows.
- Alerts are actionable and not excessively noisy.
- Logs/metrics do not expose secrets or unnecessary clinical data.

## Phase 10 — Storage, backups, and disaster recovery

### Work

1. Identify authoritative durable storage for every document and artifact.
2. Avoid reliance on replaceable container filesystems for durable uploads/artifacts.
3. Define storage permissions and path restrictions.
4. Encrypt backups and verify key custody, key separation, versioning, and recovery access.
5. Define backup frequency, retention, and failure-domain/off-site separation.
6. Agree recovery-point and recovery-time objectives with the business.
7. Execute restoration drills in isolated environments.
8. Verify restored data integrity and application compatibility.
9. Test recovery from database, storage, and deployment failure.
10. Prevent unsafe overlap of backup/restore operations.
11. Alert on missed or unverifiable backups.
12. Never report backup success without evidence of completion and integrity.

### Acceptance criteria

- A documented restore path has been executed successfully.
- Recovery objectives are supported by measured evidence.
- Encryption failures fail closed.
- Restore results pass agreed integrity checks.

## Phase 11 — Health, readiness, and graceful lifecycle management

### Work

1. Distinguish liveness from readiness.
2. Keep liveness checks lightweight and focused on process health.
3. Make readiness reflect dependencies needed to serve relevant traffic.
4. Avoid health checks that amplify load during outages.
5. Define startup, shutdown, and draining behaviour.
6. Drain traffic before instance removal.
7. Define in-flight request and real-time connection behaviour.
8. Allow Celery tasks to finish or recover under their delivery guarantees.
9. Fail startup on invalid required configuration.
10. Test rolling restarts and overlapping deployment versions.
11. Ensure only ready instances receive traffic.
12. Define service behaviour during PostgreSQL/Redis unavailability.

### Acceptance criteria

- Unready instances do not receive new traffic.
- Graceful shutdown is tested under load.
- In-flight work completes or remains recoverable by design.
- Readiness does not hide important dependency failures.

## Phase 12 — Performance and capacity engineering

### Work

Build repeatable performance tests for representative workflows:

- Authentication and authorization.
- Patient search and retrieval.
- Appointment scheduling.
- Consultation and clinical records.
- Prescriptions and pharmacy.
- Inventory updates.
- Billing and payment initiation.
- Reporting.
- Chat/Socket.IO.
- Background notifications.
- AI and external-provider calls.

Test:

1. Baseline load.
2. Gradual ramp-up.
3. Expected peak.
4. Burst/spike load.
5. Sustained soak load.
6. Dependency slowdown.
7. Worker restart under backlog.
8. Application-instance loss.
9. Controlled Redis/database interruption.
10. Recovery after fault injection.

Measure throughput, latency distribution, error rate, resource use, database connections, queue depth/task age, Redis health, recovery time, and resulting data correctness.

Agree target workloads and pass/fail objectives before making performance thresholds authoritative. Establish headroom from measurements.

### Acceptance criteria

- Tests are repeatable and detect regressions.
- Capacity and saturation limits are documented.
- Failure/load testing preserves clinical and financial correctness.
- Capacity claims rely on recorded results.

## Phase 13 — Security and tenant isolation under load

### Work

1. Test tenant isolation with multiple API instances.
2. Verify authoritative tenant-context and authorization paths.
3. Ensure caches and background jobs cannot leak cross-tenant data.
4. Verify revocation and rate limiting across replicas.
5. Audit shared-storage access control.
6. Restrict internal ports and services.
7. Preserve trusted-proxy and forwarding-header protections.
8. Manage production secrets through approved secret management.
9. Verify production CORS, TLS, security headers, and authentication settings.
10. Protect audit records.
11. Test error responses for unintended disclosure.
12. Review dependency security through the approved maintenance process.
13. Test authorization and concurrency on critical records.

### Acceptance criteria

- Scaling does not change authorization outcomes.
- Cross-tenant tests remain negative.
- Secrets and sensitive records are not exposed in logs/errors.
- Internal services are reachable only from approved network paths.

## Phase 14 — Safe delivery, rollback, and change management

### Work

1. Use immutable release artifacts identified by revision/digest.
2. Verify API, worker, and Beat artifact alignment and compatibility.
3. Pin and review runtime dependencies.
4. Keep environment secrets outside source control.
5. Validate configuration before accepting traffic.
6. Use compatible migration strategies for rolling deployment where needed.
7. Define compatibility expectations between adjacent release versions.
8. Require health/readiness/smoke checks before traffic acceptance.
9. Choose rollout strategy for the actual deployment environment.
10. Document rollback limitations for schema changes and external side effects.
11. Test rollback procedures where technically safe.
12. Record revision, artifact identity, migration revision, verification evidence, and rollout status.
13. Never claim rollback or production operation unless actually executed.

### Acceptance criteria

- Every release has a known identity and evidence trail.
- Failed readiness blocks traffic acceptance.
- Rollback accounts for schema compatibility and completed external side effects.
- Production operations are reported separately from repository and production-like verification.

## Phase 15 — Continuous testing and release gates

### Gate A: Source integrity

- Expected branch/base and file scope.
- Python compilation and relevant static checks.
- Dependency/security checks as configured.
- Diff validation.
- No accidental secrets or generated artifacts.

### Gate B: Focused component tests

- Destination policy and real transport.
- Configuration validation.
- Connection behaviour.
- Queue/retry logic.
- Health/readiness.
- Rate limiting and proxy trust.

### Gate C: Workflow regression

- APIs, billing/payment, authentication, authorization, clinical workflows, notifications, Socket.IO, background work, and database transactions.

### Gate D: Multi-instance deployment validation

- Multiple API instances and runtime traffic distribution.
- Health-based routing.
- Shared rate limits and auth/revocation state.
- Cross-instance Socket.IO delivery.
- Shared storage.
- Database connection budget.
- Graceful instance removal.

### Gate E: Fault injection and resilience

- Worker termination.
- Redis/PostgreSQL interruption.
- Provider timeout/connection failure.
- Partial outage and queue backlog.
- Retry storms.
- Unknown external outcomes.
- Restart and recovery.

### Gate F: Performance and capacity

- Baseline, ramp, peak, spike, soak, and recovery.
- Performance regression comparison.

### Gate G: Production-readiness evidence

- Approved artifact/configuration.
- Health/readiness and smoke tests.
- Backup/restore evidence.
- Working monitoring/alerts.
- Runbooks and rollback readiness.
- Required operational approvals.

### Acceptance criteria

A gate passes because the intended behaviour is evidenced, not simply because a configuration value exists. Keep focused tests fast during development and reserve expensive full regressions for appropriate milestones. Do not weaken assertions to obtain a green result.

## Phase 16 — Documentation and operational ownership

Required documentation:

1. Production architecture and trust-boundary diagram.
2. Deployment/environment configuration guide.
3. Load-balancing and scale-out runbook.
4. Gunicorn and Celery capacity-tuning guide.
5. Database connection-budget guide.
6. Redis and queue recovery runbook.
7. Socket.IO multi-instance guide.
8. Outbound destination policy and integration inventory.
9. Incident response and escalation guide.
10. Backup and restoration runbook.
11. Release, rollout, and rollback runbooks.
12. Performance-test procedure.
13. Security verification evidence.
14. Known limitations and operational dependencies.
15. Explicit status labels: implemented, verified, production-like executed, production operated.

Update documentation only after the corresponding evidence exists. Clearly distinguish planned work from verified controls.

---

## Phase 17 â€” Operational release and reliability policies

This cross-cutting phase makes release safety and ongoing reliability measurable. It refines Phases 2, 5, 9, 11, 14, 15, and 16; it does not replace their implementation work.

**Policy-setting rule:** do not copy arbitrary numeric defaults into every environment. For every interval, timeout, threshold, target, and cadence, record the selected value, rationale, environment, owner, and verification evidence in a versioned operational decision record. Derive values from the Phase 0 baseline, workload measurements, dependency behaviour, clinical/financial risk, and deployment capabilities. A policy is not complete while its required values remain unspecified.

### 17.1 Canary deployment and promotion policy â€” refines Phase 14

1. Select and document a release mechanism that can keep the stable version available while exposing a controlled subset of traffic to the candidate version. A container restart or ordinary rolling replacement is not, by itself, proof of canary capability.
2. Define environment-specific traffic stages, minimum observation/bake time at each stage, minimum sample size, and the authority allowed to promote, pause, or roll back.
3. Compare the canary with the stable version using service-level indicators and a stable control population where the platform supports it. Include request failures, latency percentiles, saturation, readiness, critical clinical workflow correctness, payment outcome integrity, and queue behaviour as applicable.
4. Define automatic stop/rollback criteria before each rollout. A breached critical safety, integrity, security, or agreed SLO signal blocks promotion even if aggregate traffic metrics look healthy.
5. Keep old and new application versions compatible with shared schema, events, cache formats, and queued tasks during the rollout. Use expand-and-contract migrations and feature flags where appropriate; do not depend on an unsafe database rollback.
6. Define rollback ownership, maximum decision time, and the verified route back to the stable artifact. Preserve evidence and perform post-deployment smoke tests before declaring success.
7. If the selected deployment cannot safely split traffic, record the gap and choose a supported deployment mechanism before claiming canary deployment is implemented.

**Acceptance criteria:** a production-like exercise demonstrates controlled exposure, an intentional failed-canary signal stops promotion, rollback returns traffic to the known-good version, data remains correct, and the promotion/rollback evidence is retained.

### 17.2 Load-balancer health-check contract â€” refines Phases 2 and 11

For each deployment environment, define and record:

- Probe type and endpoint: liveness versus readiness; active versus passive detection; and which component owns the check.
- Probe interval, per-probe timeout, consecutive failure threshold, recovery-success threshold, startup grace, and removal/re-entry behaviour.
- The maximum time from an instance becoming unhealthy to stopping new traffic, plus the traffic-drain period.
- Which dependencies readiness checks test, how each probe is bounded, and how check traffic behaves during a dependency outage.
- The consequences of a failed probe, including when to stop routing, restart a process, or leave the process running but not ready.

Keep liveness cheap and avoid turning readiness into a source of repeated database/Redis load during an outage. The current readiness route checks PostgreSQL, Redis, and backup-encryption configuration; measure and bound that work before selecting a high-frequency probe. Distinguish NGINX Open Source passive upstream failure handling from active health checking supplied by a different supported product or orchestrator; do not claim active checks unless the chosen component actually performs them.

**Acceptance criteria:** an instance failure and recovery are injected in a production-like environment; measured detection, routing removal, recovery, and draining times satisfy the approved decision record; a slow dependency does not cause probe-driven load amplification or restart flapping.

### 17.3 Graceful-shutdown time budget â€” refines Phases 5 and 11

Create an explicit shutdown budget for each workload type. The budget must account for:

1. Ingress deregistration and traffic-drain time.
2. Maximum permitted in-flight HTTP request duration and response completion.
3. Socket.IO/WebSocket disconnection or reconnect behaviour.
4. Gunicorn graceful worker shutdown and final resource cleanup.
5. Celery task completion, acknowledgement, retry/requeue, and worker-loss recovery.
6. A safety margin before the container/runtime's hard termination deadline.

Define a consistent timeout hierarchy so routing stops before shutdown, application graceful deadlines fit inside the container stop-grace period, and the hard kill deadline does not pre-empt intended recovery. Bound long-running tasks or give them an explicit resumable/recoverable contract. Do not wait indefinitely for a queue backlog to drain during a deployment. Align orchestration `stop_grace_period`, Gunicorn lifecycle settings, task time limits, and worker shutdown behaviour with the documented budget.

**Acceptance criteria:** under representative load and a controlled task backlog, the service drains within its budget; requests either complete or fail in a documented retry-safe way; unacknowledged work is recovered; no duplicate payment/clinical side effect is introduced; forced termination is only a final bounded fallback and is recorded.

### 17.4 Multi-instance cache consistency and invalidation â€” refines Phases 2, 4, and 14

1. Inventory actual caches before adding new cache technology. For each cache, record its owner, key format, tenant/security scope, source of truth, TTL, sensitivity, mutation paths, invalidation method, and acceptable staleness. If no cache exists for a data path, document that rather than inventing one.
2. Namespace keys by environment and schema/data version where rolling deployments can otherwise read incompatible entries. Ensure every key that contains tenant-scoped information includes an appropriate tenant boundary.
3. Choose invalidation per cache: explicit invalidation, short TTL, version/generation keys, refresh, or no caching for correctness-sensitive data. Define ordering with database commits so a failed transaction cannot publish invalid state.
4. For invalidation that must survive process restart or message loss, use a durable event/outbox or a reconciliation/TTL safety mechanism. Do not rely on ephemeral pub/sub alone as the sole correctness guarantee.
5. Define deploy-time handling for incompatible cached objects and serialization changes. A new release must not depend on an old replica understanding a new cache format during a rolling or canary deployment.
6. Test update/read races across two instances, tenant isolation, invalidation delivery failure, replica restart, and cache rebuild. Decide explicitly whether stale reads are allowed for each data class; critical clinical, authorization, inventory, and financial decisions must use an authoritative correctness boundary.

**Acceptance criteria:** multi-instance tests prove the documented freshness contract, no cross-tenant cache access occurs, invalidation loss has a bounded recovery path, and deploy/rollback version compatibility is verified.

### 17.5 Alert-fatigue and escalation policy â€” refines Phase 9

1. Page a human only when an alert is urgent, actionable, and represents actual or imminent user/service impact, or a critical security/data-integrity event requiring immediate action.
2. Give every alert a severity, owner/on-call route, runbook, action to take, and escalation path. Alerts without an action must be dashboards, tickets, or reviewed trend signals instead of pages.
3. Group related alerts by incident, deduplicate repeated signals, use bounded notification intervals, and define acknowledgement/escalation deadlines. Preserve the underlying event evidence.
4. Define time-bounded maintenance silences and suppression ownership. Never silently suppress critical security, clinical-safety, data-integrity, backup/restore, or deployment-rollback signals through a broad blanket rule.
5. Alert on user-visible SLO symptoms as well as actionable resource exhaustion; do not create pages for every raw cause if one symptom alert already represents the same incident.
6. Review alert volume, false positives, unactionable pages, missed incidents, and time-to-acknowledge on a regular schedule. Each change to a paging rule must be tested and have an owner.

**Acceptance criteria:** a simulated critical incident generates one actionable incident path with the expected escalation; repeated identical events are grouped; non-urgent signals do not page unnecessarily; security and data-integrity alerts cannot be hidden by normal maintenance suppression.

### 17.6 SLO and error-budget policy â€” refines Phases 9 and 14

1. Define service-level indicators around user-visible outcomes, not merely process uptime. Critical workflows (such as authentication, patient/clinical record access and writes, prescribing, and payment outcome integrity) may require distinct indicators and objectives.
2. For each SLO, document the measurement window, eligible traffic, valid/invalid events, exclusions, data source, owner, target, and error-budget calculation. Establish target values from business risk and measured baseline; do not invent a universal availability target for every workflow.
3. Define budget burn-rate warning and critical thresholds, who receives each signal, and whether it pauses a rollout, blocks promotion, opens an incident, or changes release priority.
4. When a critical release signal breaches its canary threshold, stop promotion or roll back. When the agreed error budget is exhausted, pause ordinary feature releases and prioritize reliability work until the policy's recovery condition is met.
5. Document narrowly scoped exceptions for urgent security fixes, regulatory obligations, and critical defect remediation. Exceptions require a named approver, explicit risk statement, mitigation, and recorded decision; they do not silently waive verification gates.
6. Review SLOs and error-budget policy after incidents and when service architecture or clinical risk changes. Use the policy to make reliability trade-offs, not to punish individuals.

**Acceptance criteria:** each in-scope SLO is measurable from retained evidence; simulated budget burn triggers the documented escalation/release response; a budget-exhaustion exercise demonstrates that ordinary feature promotion is paused and exceptions remain auditable.

### 17.7 Recurring chaos-engineering and recovery cadence â€” refines Phases 10, 15, and 16

Adopt this minimum operating cadence, adjusted only through a documented risk decision:

- **Every relevant change/release:** run the focused deterministic fault-injection and recovery tests associated with the changed boundary.
- **Monthly, non-production:** execute at least one controlled experiment against a high-risk dependency or boundary, rotating across worker loss, Redis degradation, database connectivity, outbound provider failure, network impairment, and instance removal as relevant.
- **Quarterly, non-production game day:** exercise a cross-domain failure and recovery workflow, including incident roles, evidence capture, communication, and the appropriate backup/restore or rollback scenario.
- **Production experiments:** prohibited by default until non-production exercises, monitoring, rollback/abort controls, ownership, and approvals are proven. Any later production experiment needs explicit authorization, smallest practical blast radius, suitable timing, and an immediate abort path.

Every experiment must declare a steady-state hypothesis, expected signals, affected scope, test identities/data, start/stop window, abort thresholds, responsible operator, recovery steps, and retained evidence. Stop immediately if clinical/financial correctness, tenant isolation, data integrity, security, or an agreed SLO is threatened. Assign every finding an owner and due date, add regression coverage, and rerun the failed experiment before closure. A calendar event or a fault command without measurable hypotheses and evidence does not count as a completed chaos exercise.

**Acceptance criteria:** the cadence is scheduled and owned; a representative experiment is executed end-to-end in non-production; abort and recovery paths are verified; findings are tracked to closure; production experiments remain gated by explicit authorization.

### 17.8 Operational decision record and policy review

For each policy above, retain: policy owner, approved values and rationale, environment/topology, last review date, source revision, test/runtime evidence, runbook link, exceptions, and next review date. Review after major architecture changes, material incidents, SLO changes, and at least quarterly. Keep the decision record free of credentials and patient data.

### 17.9 Reference guidance

Use these references as design guidance, then record Clinic System Pro-specific decisions and evidence rather than copying another organization's thresholds blindly:

- AWS Builders Library, *Automating safe, hands-off deployments*: https://d1.awsstatic.com/builderslibrary/pdfs/automating-safe-hands-off-deployments-clareliguori.pdf
- Google SRE Workbook, *Error Budget Policy*: https://sre.google/workbook/error-budget-policy/
- Google SRE Workbook, *Monitoring*: https://sre.google/workbook/monitoring/
- Google SRE Workbook, *On-Call Engineer Best Practices*: https://sre.google/workbook/on-call/
- Principles of Chaos Engineering: https://principlesofchaos.org/

## Recommended implementation order

Execute the roadmap in controlled slices:

1. **Baseline and inventory:** Complete Phases 0–1.
2. **Topology design:** Review proxy, Gunicorn, Compose, database, Redis, Socket.IO, storage, and resource budgets before editing files.
3. **Controlled scale-out:** Implement multi-instance routing in a test environment and verify health-based routing, shared state, storage, Socket.IO, and database capacity.
4. **Worker capacity:** Tune Celery and Redis from evidence; verify idempotency, backlog recovery, scheduler ownership, and shutdown.
5. **Outbound security:** Inventory all live integrations and enforce policy through actual provider execution paths.
6. **Overload and resilience:** Add/verify overload protection, retry budgets, dependency isolation, and failure-recovery tests.
7. **Observability and recovery:** Verify metrics, alerts, readiness, backups, and restoration.
8. **Performance gate:** Execute multi-instance load, soak, fault-injection, and recovery tests.
9. **Release readiness:** Complete security and clinical regression, deployment verification, documentation, and rollback-readiness checks.

Do not start all phases simultaneously. Each slice must have bounded scope, explicit preconditions, targeted tests, a regression plan, and clear rollback considerations.

## Non-negotiable engineering rules

1. Inspect actual source before patching.
2. Never apply blind replacements or guessed anchors.
3. Preserve unrelated work and untracked files.
4. Keep changes small and reviewable.
5. Do not loosen production security settings to simplify tests.
6. Do not claim load balancing because Nginx exists.
7. Do not claim horizontal scalability because Celery has multiple workers.
8. Do not claim outbound protection because a validator or transport module exists.
9. Do not claim high availability because a process restarts automatically.
10. Do not claim disaster recovery because backups are generated.
11. Verify the real execution path and fault behaviour.
12. Preserve idempotency, tenant isolation, clinical correctness, and financial correctness.
13. Run focused tests before larger regression gates.
14. Run the appropriate regression gate before marking a milestone verified.
15. Stage and commit only reviewed, in-scope files.
16. Never claim production operation without production evidence.
17. Record failures and limitations instead of hiding them.
18. Define recovery and rollback behaviour before risky infrastructure changes.
19. Prefer measured, maintainable architecture over unnecessary complexity.
20. Keep durable operational evidence tied to a specific source revision and environment.

## Definition of done

- [ ] Real multi-instance API load balancing is configured and tested.
- [ ] Unhealthy instances are removed from traffic safely.
- [ ] API and worker capacity limits are measured and documented.
- [ ] Database connections remain within the connection budget.
- [ ] Redis-backed shared state behaves correctly across replicas.
- [ ] Socket.IO works correctly across instances.
- [ ] Shared storage is durable and accessible wherever required.
- [ ] Background tasks recover without unacceptable duplicate side effects.
- [ ] Scheduled tasks have safe ownership and overlap behaviour.
- [ ] Every live outbound integration has an appropriate destination policy.
- [ ] Real outbound execution paths enforce those policies.
- [ ] Overload and dependency failures produce controlled degradation.
- [ ] Critical clinical and financial workflows remain correct under fault.
- [ ] Metrics, dashboards, and actionable alerts are working.
- [ ] Backup restoration has been executed and verified.
- [ ] Deployment, graceful shutdown, and recovery procedures have been tested.
- [ ] Security, workflow, load, and fault-injection gates have passed.
- [ ] Release and rollback evidence is recorded.
- [ ] Remaining limitations and operational risks are explicit.
- [ ] Canary traffic promotion, bake-time, stop criteria, and rollback have been exercised for the chosen deployment topology.
- [ ] Load-balancer health-check type, interval, timeout, failure/recovery thresholds, startup grace, and draining policy are explicit and verified.
- [ ] HTTP, Socket.IO, Gunicorn/container, and Celery shutdown budgets are aligned and tested under load.
- [ ] The actual cache inventory and multi-instance freshness/invalidation contract are documented and tested where caches exist.
- [ ] Alert severity, deduplication, escalation, suppression, ownership, and fatigue review are operational.
- [ ] Critical SLOs and error-budget actions are approved and tested through an exercise.
- [ ] Chaos exercises have a defined cadence, hypothesis, bounded blast radius, abort criteria, retained evidence, and remediation ownership.

## Final principle

Clinic System Pro must be engineered so that additional load does not compromise correctness, one component failure does not automatically become a platform-wide failure, and releases are accepted because evidence supports them—not merely because deployment completed without an obvious error.
