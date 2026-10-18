# Clinic System Pro — Phase 0 Baseline Supplement

**Evidence date:** 2026-10-18

**Disposition:** **CLOSED WITH DOCUMENTED LIMITATION**
**Execution classification:** Local Docker production-like workload; not production-operated evidence.

## 1. Accepted target run

- **Run ID:** `system-mixed-phase0-10u2r30-20261018-204454-53207e07`
- **Scenario:** `system_mixed`
- **Target configuration:** 10 users; spawn rate 2 users/second; 30-second Locust runtime
- **Host:** `https://localhost:19443` through the local TLS-enabled proxy
- **Repository branch:** `feat/hie-endpoint-allowlist-enforcement`
- **HEAD:** `986ec277fba12a9e54b488d6c1907c6556efc848`
- **Access-control endpoint:** excluded by default because it requires a `SUPER_ADMIN` role; no production authorization was changed.

## 2. Client and application results

| Metric | Result |
|---|---:|
| Locust requests counted as completed | 464 |
| Locust failures | 0 |
| Locust requests/second | 16.311 |
| Client average response time | 125.441 ms |
| Client median response time | 65 ms |
| Backend performance records | 469 |
| Backend HTTP failures (status >= 400) | 0 |
| Backend status distribution | 469 × HTTP 200 |
| Backend average duration | 57.095 ms |
| Backend p50 duration | 44.735 ms |
| Backend p95 duration | 129.202 ms |
| Backend p99 duration | 209.789 ms |
| Backend maximum duration | 284.651 ms |
| Average database time | 36.861 ms |
| Maximum database time | 223.795 ms |
| Average DB queries per request | 6.908 |
| Access-control endpoint requests | 0 |
| Locust JSON ↔ aggregate CSV counts | Match (464 requests, 0 failures) |
| Repaired baseline result validity | `true` |

The saved Locust JSON and aggregate CSV agree. The baseline runner's initial pass used an empty placeholder server log; the real Docker backend log was captured afterward and the result was repaired. The **repaired** result is the evidence of record. The PowerShell `Start-Process` wrapper did not reliably surface the runner process exit code, so it is not used as the acceptance signal; the saved Locust exit code is 0, the CSV reports zero failures, and the repaired server result is valid.

## 3. Reconciliation of 464 client-completed vs 469 backend records

The proxy recorded **469 API requests: 464 HTTP 200 and five HTTP 499**. The five 499 records were:

| Route | HTTP 499 count |
|---|---:|
| `GET /api/v1/appointments/staff/{staff_id}` | 2 |
| `GET /api/v1/lab/orders` | 1 |
| `GET /api/v1/notifications/` | 1 |
| `GET /api/v1/pharmacy/drugs` | 1 |
| **Total** | **5** |

The proxy 499 timestamps were between `2026-10-18T18:46:15.866587Z` and `2026-10-18T18:46:15.870492Z`. Locust's saved finish time is `2026-10-18T18:46:16.106251Z`, so all five 499 events occurred approximately 0.236–0.240 seconds before that timestamp. All 469 backend performance records were HTTP 200, had unique request IDs, and none was timestamped after Locust's recorded finish.

This provides a consistent reconciliation: five client connections were reported as closed by the proxy while their corresponding backend requests still completed successfully. The timing and counts are consistent with in-flight requests being cancelled near workload shutdown. The proxy log had no upstream request-ID/status detail, so the exact trigger for each disconnect cannot be proven more strongly than that. No application/server failure was observed in these records.

## 4. Infrastructure observations

| Measurement | Observation |
|---|---:|
| Host free RAM before run | 1,704 MiB |
| Lowest captured in-run host free RAM | 1,417 MiB |
| Host free RAM after run | 1,276 MiB |
| Periodic Celery `celery` list-depth samples | 7 samples; all 0 |
| Queue-depth sample errors | 0 |
| Celery `active` / `reserved` / `scheduled` snapshots before run | All empty; inspection exit 0 |
| Celery `active` / `reserved` / `scheduled` snapshots after run | All empty; inspection exit 0 |
| Final Celery list depth | 0 |

The container samples showed the backend at approximately 205.6–206.2 MiB memory, worker at approximately 568.5–569.0 MiB, beat at 173.1 MiB, PostgreSQL at approximately 196.0–197.7 MiB, Redis at approximately 23.3–23.6 MiB, and proxy at approximately 4.3–5.4 MiB. These are point-in-time samples from this workload, not sustained capacity limits or production resource guarantees.

### Queue-age qualification

**Queue age was not directly measured.** No pending messages were observed in seven roughly two-second queue-depth samples, the initial/final queue depth was zero, and Celery active/reserved/scheduled inspections were empty before and after the test. This supports reporting **“no backlog observed in sampled checks.”** It does not establish a numeric enqueue-to-start latency or exclude a transient backlog shorter than the sampling interval. Direct queue-age telemetry is deferred as an explicit follow-up; this supplement does not claim queue-age measurement was completed.

## 5. Acceptance decision

The controlled `system_mixed` target run is accepted as the Phase 0 local production-like baseline for the stated configuration because:

- the target workload executed;
- Locust recorded 464 completed requests and zero failures;
- the repaired server result contains 469 successful HTTP 200 backend records and zero server failures;
- Locust JSON and CSV request/failure counts agree;
- the five extra backend records reconcile with five proxy HTTP 499 client-connection closures on the same affected routes and near the workload finish boundary;
- no Celery backlog was observed by the available sampled checks; and
- the required run artifacts are present.

**Phase 0 disposition: CLOSED WITH DOCUMENTED LIMITATION.** The baseline is not evidence of production operation or a production capacity guarantee. Direct queue-age measurement remains a separate follow-up and must not be reported as complete.

## 6. Evidence artifacts

Repository-root relative paths for the accepted run:

- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07.json`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07_locust_stats.csv`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07_locust_stats_history.csv`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07_locust_failures.csv`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07_locust_exceptions.csv`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07-infrastructure.txt`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07-proxy-audit.stdout.tmp`
- `testing/results/system-mixed-phase0-10u2r30-20261018-204454-53207e07-proxy-audit.stderr.tmp`
- `logs/system-mixed-phase0-10u2r30-20261018-204454-53207e07-server.log`

The earlier failed target run and both successful smoke runs remain diagnostic/supporting artifacts and are not substituted for the accepted target run. The historical `docs/production/PHASE0_BASELINE_2026-10-18.md` was not modified by this supplemental report.
