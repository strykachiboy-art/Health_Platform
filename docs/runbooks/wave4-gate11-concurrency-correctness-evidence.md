# Wave 4 Gate 11 — Concurrency Correctness Evidence

## Status

**GREEN / CLOSED**

Gate 11 is the final Wave 4 correctness gate.

The gate exercised multiple tenants concurrently and verified:

- tenant-local clinical operations remain tenant-local,
- cross-tenant clinical operations are denied,
- tenant-local financial operations remain tenant-local,
- cross-tenant financial operations are denied,
- clinical relationships remain internally consistent,
- financial effects are neither duplicated nor leaked across tenants,
- PostgreSQL concurrency uses independent backend connections,
- authentication and JWT revocation checks remain active,
- no primary fleet or persistent production-like domain data is mutated,
- the protected unrelated `backend/app/core/web_routes.py` modification remains untouched.

## Source lock

Canonical repository:

`strykachiboy-art/Health_Platform`

Branch:

`feat/smtp-email-circuit-breaker-integration`

Gate 11 source commit:

`4567828eb951ef7404c218d2a59f9e7559d3cf83`

Protected unrelated tracked file:

`backend/app/core/web_routes.py`

Protected SHA256:

`C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352`

The protected file remained dirty, unstaged, and byte-for-byte unchanged throughout Gate 11.

## Gate contract

Gate 11 required true multi-tenant concurrency, not only isolated race tests.

The final concurrent workload used:

- 2 tenants,
- 8 concurrent workers,
- 2 legitimate clinical workers,
- 2 cross-tenant clinical denial workers,
- 2 legitimate financial workers,
- 2 cross-tenant financial denial workers.

The workload required distinct PostgreSQL backend connections and verified final database state after all workers completed.

## Stage A — Sequential baseline

### Clinical baseline

Existing E2E:

`backend/app/tests/e2e/test_patient_appointment_consultation.py`

Result:

`1 passed in 17.74s`

Evidence markers:

- `PHASE8_E2E_PATIENT_APPOINTMENT_CONSULTATION=PASS`
- `CLINICAL_TENANT_BASELINE=PASS`

This proved the existing sequential clinical tenant-isolation baseline before concurrency.

### Billing baseline contract drift

The existing Phase 8 billing E2E initially failed before Gate 11 concurrency started.

Observed result:

`422 Invalid request payload`

Root cause:

The current `CreateInvoiceRequest` requires a 3-letter `currency`, while the older Phase 8 E2E invoice payload omitted it.

This was classified as **test-contract drift**, not a Gate 11 concurrency failure.

The old Phase 8 test was not modified as part of Gate 11.

### Current billing contract probe

A temporary native E2E probe used the current invoice contract with:

`currency="NGN"`

Result:

`1 passed in 16.04s`

Evidence markers:

- `GATE11_CURRENT_BILLING_CONTRACT=PASS`
- `GATE11_FINANCIAL_TENANT_DENIAL=PASS`
- `GATE11_FINANCIAL_INVARIANT_BASELINE=PASS`
- `GATE11_STAGE_A=GREEN`

The temporary probe was removed after execution and left no retained repository mutation.

## Stage B — True multi-tenant concurrency

### Disposable infrastructure

The authoritative Stage B run used isolated disposable infrastructure:

PostgreSQL image:

`postgres:18`

PostgreSQL container:

`clinic-gate11-postgres-20261029T070600Z-6504`

PostgreSQL host port:

`62223`

Redis image:

`redis:8`

Redis container:

`clinic-gate11-redis-20261029T070600Z-6504`

Redis host port:

`62226`

Both containers were uniquely named per run.

Pre-existing Gate 11 containers were not touched.

Both disposable services passed health checks before the concurrency proof.

### Why Redis was required

JWT revocation is fail-closed.

The application token blocklist delegates to `token_service.is_token_revoked()`.

If Redis is unavailable or revocation storage cannot be checked, authentication fails closed and the token is treated as revoked.

An earlier Stage B harness attempt proved that real login could return HTTP 200 while subsequent protected requests returned HTTP 401 when the revocation store was not valid for the disposable runtime.

The authoritative V7 harness therefore used a dedicated disposable Redis instance and verified authentication before starting concurrency.

### Authentication preflight

The final run used the real application login endpoint for all four users.

Evidence markers:

- `GATE11_REAL_LOGIN_PREFLIGHT=PASS`
- `GATE11_JWT_REVOCATION_PREFLIGHT=PASS`

No authentication bypass or JWT blocklist bypass was used.

## Concurrent workload

The final workload launched 8 workers simultaneously.

### Clinical workers

Tenant A legitimate appointment creation:

`PASS`

Tenant B legitimate appointment creation:

`PASS`

Tenant A attempting a tenant B appointment:

`DENIED`

Tenant B attempting a tenant A appointment:

`DENIED`

### Financial workers

Tenant A legitimate payment:

`PASS`

Tenant B legitimate payment:

`PASS`

Tenant A attempting to pay tenant B invoice:

`DENIED`

Tenant B attempting to pay tenant A invoice:

`DENIED`

## PostgreSQL concurrency proof

Evidence:

`GATE11_WORKER_COUNT=8`

`GATE11_DISTINCT_BACKEND_PIDS=8`

This proves all 8 synchronized workers executed using 8 distinct PostgreSQL backend connections.

## Clinical invariants

The final database contained exactly the expected legitimate appointments.

Verified invariants:

- tenant A appointment remained owned by tenant A,
- tenant A appointment referenced tenant A patient,
- tenant A appointment referenced tenant A staff,
- tenant B appointment remained owned by tenant B,
- tenant B appointment referenced tenant B patient,
- tenant B appointment referenced tenant B staff,
- no appointment linked tenant A ownership to tenant B patient,
- no appointment linked tenant B ownership to tenant A patient,
- both legitimate appointments remained in the expected scheduled state.

Evidence marker:

`GATE11_CLINICAL_INVARIANTS=PASS`

## Financial invariants

Tenant A:

- invoice total: `100.00`,
- amount paid: `100.00`,
- final status: `PAID`,
- successful payments: exactly 1.

Tenant B:

- invoice total: `80.00`,
- amount paid: `80.00`,
- final status: `PAID`,
- successful payments: exactly 1.

Verified invariants:

- no cross-tenant payment mutation,
- no duplicate financial effect,
- no overpayment,
- per-invoice successful payment sum exactly matched `amount_paid`,
- invoice ownership remained tenant-local.

Evidence marker:

`GATE11_FINANCIAL_INVARIANTS=PASS`

## Authoritative Stage B result

Final pytest result:

`1 passed in 25.75s`

Final evidence markers:

```text
GATE11_REAL_LOGIN_PREFLIGHT=PASS
GATE11_JWT_REVOCATION_PREFLIGHT=PASS
GATE11_WORKER_COUNT=8
GATE11_DISTINCT_BACKEND_PIDS=8
GATE11_CLINICAL_LEGITIMATE_A=PASS
GATE11_CLINICAL_LEGITIMATE_B=PASS
GATE11_CLINICAL_CROSS_TENANT_DENIAL=PASS
GATE11_FINANCIAL_LEGITIMATE_A=PASS
GATE11_FINANCIAL_LEGITIMATE_B=PASS
GATE11_FINANCIAL_CROSS_TENANT_DENIAL=PASS
GATE11_CLINICAL_INVARIANTS=PASS
GATE11_FINANCIAL_INVARIANTS=PASS
GATE11_MULTI_TENANT_CONCURRENCY=PASS
TRUE_CONCURRENT_MULTI_TENANT_PROOF=PASS
GATE11_STAGE_B=GREEN
```

## Cleanup evidence

The final run removed:

- the temporary Gate 11 concurrent test,
- the disposable Redis container,
- the disposable PostgreSQL container.

Evidence markers:

```text
TEMPORARY_TEST_CLEANUP=PASS
DISPOSABLE_POSTGRES_CLEANUP=PASS
DISPOSABLE_REDIS_CLEANUP=PASS
EXISTING_GATE11_CONTAINERS_TOUCHED=NO
REPOSITORY_RETAINED_MUTATION=NO
```

## Source integrity after Gate 11

Final source remained:

`4567828eb951ef7404c218d2a59f9e7559d3cf83`

Protected file SHA256 remained:

`C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352`

The protected file remained unstaged and its dirty state was preserved.

## Earlier harness attempts

Earlier Stage B attempts were harness/infrastructure failures and are not functional Gate 11 failures.

They included:

1. fixed disposable container name collided with an existing container,
2. safe identity check correctly refused to delete a non-matching pre-existing container,
3. a malformed PowerShell container-suffix expression caused a parser failure before execution,
4. Windows PowerShell 5.1 converted expected `docker inspect` stderr into a terminating error,
5. direct token construction produced 401 responses before database access,
6. real login returned 200 but JWT revocation still failed closed because Redis was not isolated for the disposable runtime.

The authoritative V7 run corrected these harness issues and completed the intended workload successfully.

## Final decision

```text
TENANTS_CONCURRENT=2
CONCURRENT_WORKERS=8
DISTINCT_POSTGRES_CONNECTIONS=8
CROSS_TENANT_CLINICAL_DENIAL=PASS
CROSS_TENANT_FINANCIAL_DENIAL=PASS
CLINICAL_INVARIANTS=PASS
FINANCIAL_INVARIANTS=PASS
TRUE_MULTI_TENANT_CONCURRENCY=PASS
WAVE4_GATE11=GREEN
```

**Wave 4 Gate 11 is GREEN and ready for documentation commit/push.**

Because Gate 11 is the final Wave 4 gate, successful documentation commit/push closes Wave 4 testing.