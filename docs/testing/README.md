## Current Project Testing State

The earlier phase sections in this document are retained as historical verification records.

The current backend verification track is Phase 9 resilience / failure injection.

Current focused evidence:

- resilience-all: ``273 passed in 120.49s (0:02:00)``
- backend E2E after lifecycle hardening: ``50 passed in 252.32s``
- encryption regression: ``131 passed in 139.23s``
- Gate 11 focused regression: ``11 tests in 133.72s``

Final full backend regression: `8,749 passed in 9,667.28s (2:41:07)`
with `0 failed` and `0 errors`.

Combined verified backend baseline: `9,022 passed` (`8,749` backend application + `273` resilience), with `0 failed`.

Concurrency verification: `7 passed in 65.92s`.

Backend current development boundary: **GREEN / VERIFIED / LOCKED**.

The final full backend regression is now the completed broad verification
evidence for the current backend development boundary.

# CLINIC SYSTEM PRO v5

# TESTING ARCHITECTURE & VERIFICATION MODEL

**Owner:** Stryka
**Primary Framework:** pytest
**Application Stack:** Flask Â· SQLAlchemy 2.x Â· PostgreSQL Â· Redis Â· Celery Â· Socket.IO Â· JWT Â· Google OAuth Â· Pydantic v2 Â· Alembic
**Load / Resilience Tooling:** pytest Â· Locust Â· dedicated resilience runners

---

## 1. PURPOSE

Clinic System Pro v5 uses testing as a production-safety and correctness verification system.

The objective is not maximum test count or line coverage.

The objective is to prove that the system:

* enforces business rules correctly
* enforces authentication and authorization correctly
* preserves tenant isolation
* validates API contracts
* maintains transactional and data integrity
* behaves correctly under failure and recovery
* exposes safe operational visibility
* handles background jobs and distributed dependencies correctly
* remains deterministic and reproducible
* can pass formal verification gates before advancing to the next project phase

Testing therefore follows **behavior verification**, **risk-based coverage**, and **phase-gated verification**.

---

## 2. TESTING PHILOSOPHY

The project follows these principles:

* **Test behavior, not implementation detail.**
* **Test business rules where they live.**
* **Test public API behavior at the route boundary.**
* **Test infrastructure components directly when they contain meaningful operational logic.**
* **Avoid redundant tests that prove the same behavior through multiple layers without additional confidence.**
* **Prefer confidence per test over raw test count.**
* **Keep tests deterministic and isolated.**
* **Do not rely on execution order.**
* **Do not allow leaked state between tests.**
* **Do not accept flaky tests as normal.**
* **Do not use coverage percentage as a substitute for behavioral verification.**
* **Every phase must have explicit verification evidence before it is closed.**

---

## 3. TEST ARCHITECTURE

The suite is organized around the architecture of the application rather than a single generic testing layer.

### 3.1 Domain and Service Verification

Service tests verify:

* business rules
* lifecycle transitions
* domain constraints
* transactional behavior
* authorization decisions
* tenant scoping
* state transitions
* conflict handling
* historical-read behavior
* idempotency
* domain-specific error behavior

Services are the primary location for business-rule verification.

---

### 3.2 Route / API Verification

Route tests verify the external API contract and security boundary.

They cover:

* authentication
* authorization
* role enforcement
* tenant isolation
* IDOR protection
* request validation
* response contracts
* pagination
* deterministic ordering
* status codes
* error handling
* lifecycle restrictions
* authenticated actor resolution
* security-sensitive route behavior

Routes are therefore tested directly rather than relying only on service tests.

---

### 3.3 Model and Schema Verification

Models and schemas are generally exercised through the application behavior that consumes them.

They are tested directly when they contain meaningful independent behavior, constraints, validators, or infrastructure logic.

The project does **not** require separate tests for every model field or every schema declaration when the same behavior is already verified through services and routes.

This prevents redundant test layers while preserving meaningful validation.

---

### 3.4 Core / Infrastructure Verification

Infrastructure components are tested directly when they provide operational or security behavior.

Examples include:

* request context and correlation IDs
* tracing
* health and readiness checks
* operational metrics
* alert evaluation
* alert state tracking
* alert delivery boundaries
* Redis metrics
* Celery metrics
* database metrics
* Socket.IO metrics
* failure event recording
* operational dashboards
* failure dashboards
* observability routes

Infrastructure tests verify the behavior of the operational platform itself.

---

### 3.5 Resilience Verification

Resilience testing verifies system behavior during degraded or failed dependencies.

Coverage includes scenarios involving:

* HTTP failures
* Socket.IO behavior
* authentication degradation
* chat degradation
* Redis / Celery degradation
* audit behavior
* data integrity
* idempotency recovery
* recovery workflows
* failure during security-sensitive operations

Resilience tests are not substitutes for normal unit/integration tests. They verify behavior under abnormal system conditions.

---

### 3.6 Load and Performance Verification

Load tests verify system behavior under controlled synthetic traffic.

The load-test environment uses:

* dedicated synthetic users
* controlled load-test identifiers
* application metrics
* request latency measurement
* database query visibility
* Redis visibility
* AI/provider policy visibility where applicable

Real production users must never be used as load-test actors.

Performance results are treated as environment-specific measurements rather than permanent guarantees.

---

## 4. DATABASE AND STATE ISOLATION

Tests must execute with isolated mutable state.

The test environment is designed to prevent:

* cross-test contamination
* leaked transactions
* leaked sessions
* stale authentication state
* persistent Redis contamination
* ordering dependencies
* mutated fixtures affecting later tests
* database corruption across tests

Where the database isolation harness is used, the lifecycle is:

```text
CREATE APP CONTEXT
    â†“
CREATE TABLES IN SQLITE :memory:
    â†“
RUN TEST
    â†“
ROLLBACK / REMOVE SESSION
      â†“
DROP TABLES
```

The exact fixture implementation may evolve, but the invariant remains:

> A test must not silently alter the state observed by another test.

Shared infrastructure is only acceptable when the test explicitly controls and resets the state it uses.

---

## 5. SECURITY VERIFICATION MODEL

Security is not treated as a separate afterthought.

Security behavior is verified throughout the suite.

Tests cover, where applicable:

### Authentication

* access-token validation
* refresh-token behavior
* revocation
* suspended-user handling
* suspended-clinic handling
* Google OAuth flows
* authentication error behavior

### Authorization

* role-based access control
* privileged-role restrictions
* administrative boundaries
* emergency-access restrictions
* reviewer restrictions
* role-assignment rules

### Tenant Isolation

* same-clinic access
* cross-clinic denial
* clinic-scoped queries
* authenticated actor resolution
* client-supplied tenant/actor ID rejection

### IDOR Protection

Tests must verify that changing resource identifiers cannot bypass authorization or tenant boundaries.

### Emergency / Break-Glass Access

Emergency access verification must include:

* requester eligibility
* active staff/user/clinic requirements
* same-clinic patient resolution
* duplicate active/requested access prevention
* expiry behavior
* scope normalization
* scope limits
* grant duration limits
* reviewer restrictions
* audit integration
* downstream enforcement where the emergency grant is actually consumed

---

## 6. VALIDATION AND CONTRACT VERIFICATION

API validation is verified at the boundary.

Important rules include:

* strict typing where required
* explicit date handling
* bounded integer values
* bounded string lengths
* `extra="forbid"` where appropriate
* deterministic pagination
* deterministic ordering
* meaningful validation errors
* correct status-code mapping

Tests should verify behavior rather than merely asserting that a schema class exists.

---

## 7. TRANSACTION AND DATA-INTEGRITY VERIFICATION

The suite verifies:

* successful transactions
* rollback behavior
* conflict handling
* idempotency
* partial-failure recovery
* lifecycle consistency
* historical-read safety
* concurrent state transitions where applicable
* audit consistency
* outbox recovery
* persistence invariants

A failed operation must not leave the database in an invalid intermediate state.

---

## 8. OBSERVABILITY VERIFICATION

Observability is itself tested.

Current verification covers:

### Health

* liveness
* readiness
* database health
* Redis health

### Request Correlation

* request IDs
* trace IDs
* response correlation headers
* propagation into operational events

### Structured Operational Logging

* safe structured fields
* error classification
* request correlation
* trace correlation
* absence of PHI and secrets

### Metrics

* request metrics
* database metrics
* Redis metrics
* system metrics
* Celery metrics
* Socket.IO metrics
* operational snapshots

### Alerting

* alert thresholds
* severity
* alert fingerprints
* new alerts
* ongoing alerts
* escalations
* resolutions
* deduplication
* queue-specific alert identity

### Failure Visibility

* HTTP 5xx events
* application errors
* Celery failures
* Celery retries
* safe failure-event fields
* failure summaries
* operational failure dashboards

### Operational APIs

* operational dashboard authorization
* failure dashboard authorization
* platform-scoped operational data
* PHI exclusion
* sensitive implementation-detail exclusion

### Background Job Visibility

Celery verification includes:

* broker health
* worker availability
* worker count
* worker concurrency
* active tasks
* reserved tasks
* scheduled tasks
* queue depth
* worker utilization
* long-running tasks
* registered task visibility
* task execution metrics
* failures
* retries
* revoked tasks

### Runbooks

Operational runbooks are treated as part of the verification model and must exist for supported operational failure classes.

---

## 9. TEST SUITE ORGANIZATION

The project uses focused suites alongside full-project regression testing.

Typical areas include:

```text
app/tests/
â”œâ”€â”€ core/
â”‚   â”œâ”€â”€ observability/
â”‚   â””â”€â”€ ...
â”œâ”€â”€ modules/
â”‚   â”œâ”€â”€ auth/
â”‚   â”œâ”€â”€ patient/
â”‚   â”œâ”€â”€ appointment/
â”‚   â”œâ”€â”€ consultation/
â”‚   â”œâ”€â”€ lab/
â”‚   â”œâ”€â”€ pharmacy/
â”‚   â”œâ”€â”€ prescription/
â”‚   â”œâ”€â”€ inventory/
â”‚   â”œâ”€â”€ billing/
â”‚   â”œâ”€â”€ ward/
â”‚   â”œâ”€â”€ ambulance/
â”‚   â”œâ”€â”€ hie/
â”‚   â”œâ”€â”€ ai/
â”‚   â”œâ”€â”€ reports/
â”‚   â”œâ”€â”€ notifications/
â”‚   â””â”€â”€ ...
â””â”€â”€ ...
```

The exact directory structure may evolve with the application architecture.

The invariant is that tests should remain discoverable by architectural responsibility.

---

## 10. PHASE-GATED VERIFICATION

Clinic System Pro v5 uses explicit verification gates.

### Phase 1 â€” Core Architecture / Security Foundation

Verification included:

* authentication
* authorization
* tenant isolation
* validation
* error handling
* transaction behavior
* lifecycle safety
* migration integrity
* security-sensitive access boundaries

---

### Phase 2 â€” Feature Hardening

Verification included the completed application modules and their associated:

* business rules
* route contracts
* security boundaries
* lifecycle behavior
* integration behavior

---

### Phase 3 â€” Resilience

The resilience phase established controlled verification for:

* dependency failures
* recovery behavior
* degraded services
* data integrity
* idempotency recovery
* failure-path security
* restoration behavior

The full regression suite previously reached:

```text
7,226 passed
0 failed
Duration: 10,455.86s
```

This is a **historical full-suite verification snapshot**, not a permanent assertion of the current test count.

---

### Phase 4 â€” Observability / Operations

Phase 4 is formally closed.

Exit review verified:

```text
Required observability files: 19 present
Operational runbooks: 5 present
Required operational routes: 4 registered
Observability tests: 128 passed
```

Exit status:

```text
PHASE 4 EXIT REVIEW: GREEN
```

Verified areas include:

* operational metrics
* structured logging
* request correlation
* tracing
* alerting
* alert state management
* alert delivery boundary
* failure dashboards
* background job visibility
* Redis visibility
* database health
* queue monitoring
* operational runbooks

---

### Historical Phase 5 â€” Production Readiness

Phase 5 is the next verification gate.

Planned verification includes:

* configuration review
* secret handling
* production deployment configuration
* security review
* logging review
* backup policy
* restore readiness
* migration readiness
* health checks
* startup behavior
* shutdown behavior
* deployment validation
* final regression verification

Phase 5 cannot be considered complete until its explicit verification gate passes.

---

## 11. VERIFIED CHECKPOINTS

Checkpoint results are recorded as time-specific evidence.

| Checkpoint                       | Result                              |
| -------------------------------- | ----------------------------------- |
| Clinical Safety suite            | 134 passed                          |
| Feedback suite                   | 95 passed                           |
| Historical Resilience Run        | 47 passed, 0 failed, 0 skipped      |
| Restore Drill                    | `success = true`                    |
| Restore Verification             | `post_restore_verification = true`  |
| Authentication Invalidation      | `authentication_invalidated = true` |
| Outbox Recovery                  | `outbox_recovery_completed = true`  |
| Phase 4 Observability            | 128 passed                          |
| Historical Full Regression Suite | 7,226 passed, 0 failed              |

These values represent recorded verification runs and should not be interpreted as immutable current totals.

---

## 12. STANDARD COMMANDS

### Run the full application suite

```powershell
$env:PYTHONPATH="$PWD\backend;$PWD\testing"
pytest -q
```

### Run the observability suite

```powershell
$env:PYTHONPATH="$PWD\backend;$PWD\testing"
pytest -q backend/app/tests/core/observability/
```

### Run the Phase 4 exit review

```powershell
python docs/security/scripts/phase4_observability_exit_review.py
```

### Run the pre-full-suite security verification

```powershell
python docs/security/scripts/pre_full_suite_verify.py
```

### Run resilience verification

```powershell
$env:PYTHONPATH="$PWD\backend;$PWD\testing"
python -m resilience.runners.resilience --scenario all
```

Specific scenarios may be selected when validating an individual failure domain.

---

## 13. FULL-SUITE POLICY

The full suite is the highest-level regression signal.

A green focused suite does **not** automatically imply that the entire project is green.

Likewise, a successful full suite does not replace specialized resilience, load, restore, or operational verification.

The project therefore uses:

```text
Focused Tests
      â†“
Feature / Module Verification
      â†“
Phase Verification
      â†“
Resilience / Operational Verification
      â†“
Full Regression Suite
      â†“
Production Readiness Gate
```

---

## 14. FAILURE POLICY

A failing test is a verification signal.

The expected process is:

```text
FAIL
 â†“
REPRODUCE
 â†“
CLASSIFY
 â†“
FIX
 â†“
RUN FOCUSED SUITE
 â†“
RUN RELATED REGRESSION
 â†“
UPDATE VERIFICATION EVIDENCE
```

Tests must not be weakened merely to make the suite green.

Skipped or xfailed tests require an explicit reason and must not silently hide an unfinished verification requirement.

---

## 15. ANTI-BLOAT POLICY

The project does not intentionally add tests simply to increase test count.

A test should justify its existence by verifying one or more of:

* a business rule
* a security boundary
* an integration contract
* a failure mode
* a recovery guarantee
* a data-integrity invariant
* an operational behavior
* a performance or resilience requirement

Duplicate tests that provide no additional confidence should be avoided.

The goal is a **small, powerful, deterministic verification system**.

---

## 16. DEFINITION OF TESTING DONE

A feature is not considered verified merely because its implementation exists.

Testing is considered complete when the relevant behavior has:

1. a defined verification surface
2. deterministic automated coverage where appropriate
3. security-boundary verification
4. failure-path verification where applicable
5. integration verification where applicable
6. phase-specific evidence
7. no known unresolved verification blocker

For phase closure, the required exit review must be GREEN.

---

## 17. SOURCE OF TRUTH

This document defines the project's testing architecture and verification philosophy.

Individual module tests define the detailed executable behavior.

Phase exit reviews define whether a project phase has formally passed its verification gate.

Recorded test results are time-specific evidence and must always be interpreted together with the command, environment, and scope that produced them.

## Current Verification Position

Older phase test counts in this document are historical snapshots.

Current verified evidence:

- final full backend regression: `8,749 passed in 9,667.28s (2:41:07)`,
  `0 failed`, `0 errors`
- concurrency verification: `7 passed in 65.92s`
- resilience-all: `273 passed in 120.49s (0:02:00)`
- Gate 11 focused regression: `11 tests in 133.72s`
- backend E2E after lifecycle hardening: `50 passed in 252.32s`
- encryption regression: `131 passed in 139.23s`

Phase 9 resilience verification covers controlled dependency failure, unknown
outcomes, idempotency, provider/storage failure, worker failure, and recovery.

Encryption verification covers integration credential encryption and key versioning,
encrypted backup artifacts, separate backup-key domains, encrypted restore, recovery,
and cryptographic failure/retry behavior.

The current backend development boundary is **GREEN / VERIFIED / LOCKED**.

The next engineering track is Phase 10 - Flutter Foundation.
