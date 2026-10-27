# Wave 4 Gate 7 - System-Mixed Capacity Boundary Evidence

| Evidence field | Value |
|---|---|
| Evidence date | 2026-10-27 |
| Repository | `strykachiboy-art/Health_Platform` |
| Branch | `feat/password-recovery-hardening` |
| Source commit | `22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11` |
| Candidate image | `clinic-system-phase9:22ee655` |
| Candidate image ID | `sha256:1a10692bf0fd1c91739981b0742ab6298a9386560138868102425dde00d05733` |
| Candidate OCI revision | `22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11` |
| Base image | `python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1` |
| Scenario | `testing/scenarios/system_mixed.py` |
| Locust version | `2.46.5` |
| Accepted rungs | `5, 7, 9` |
| Highest stable rung | **9 users** |
| First degradation rung | **11 users** |
| Degradation class | **p95 latency degradation** |
| Gate 7 ladder status | **COMPLETE** |

## Purpose and claim boundary

Wave 4 Gate 7 executed a controlled system-mixed load ladder against immutable source `22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11`.

Ladder policy:

```text
5 -> 7 -> 9 -> 11 -> 13 users
```

Each rung used 90 seconds, spawn rate 1 user/second, 2-4 second waits, pre-authenticated admin/clinical tokens, TLS verification, AI disabled, extra access-control disabled, exact source/image locks, health/restart monitoring, RAM sampling, and Locust failure/exception checks.

Escalation stopped when aggregate p95 exceeded 2x the previous accepted rung p95.

Gate 7 does not claim a universal production maximum. It records the highest stable rung and first tested degradation boundary for this local candidate/profile.

## Candidate identity

```text
SOURCE_COMMIT=22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11
IMAGE=clinic-system-phase9:22ee655
IMAGE_ID=sha256:1a10692bf0fd1c91739981b0742ab6298a9386560138868102425dde00d05733
OCI_REVISION=22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11
PROJECT=w4head-22ee655
ENDPOINT=https://localhost:56566
```

The candidate was built from a clean detached worktree. The unrelated tracked edit to `backend/app/core/web_routes.py` remained outside the candidate and was protected by SHA-256:

```text
C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
```

## Benchmark dataset

Relevant seeded state:

- clinic 1
- control user 1
- staff 12
- patients 8
- appointments 8
- consultations 3
- lab tests 12
- lab orders 4
- drugs 12
- prescriptions 4
- inventory items 20
- wards 2
- ambulances 2
- invoices 8
- notifications 1000
- conversations 50
- messages 500
- reports 100
- user devices 10
- AI credits 100000

Anchors:

```text
staff_first=1
patient_first=2
```

Tokens remained outside the repository and were never printed or committed.

## Pre-capacity defect discovery

The earlier `47cfdff2897f3fb1ad7aeff348d5482c156a6bb6` candidate exposed a transaction defect: `ensure_clinic_settings()` flushed a settings row but did not commit it, causing `GET /api/v1/settings/clinic` to return 404 during the initial load attempt.

The fix added the transactional boundary and a persistence regression test. Corrected commit:

```text
22ee6556aa7fab4822ffd9b96cf0f16a6afb4a11
fix(settings): commit initialized clinic settings
```

Focused settings verification:

```text
85 passed in 303.40s
```

The old run is defect-discovery evidence only, not capacity evidence.

## Accepted ladder

### Rung 5

Run ID: `gate7-rung5-20261027T131407Z-22ee655`

| Metric | Result |
|---|---:|
| Users | 5 |
| Requests | 808 |
| Failures | 0 |
| Median | 58 ms |
| Average | 98.6408 ms |
| p95 | **240 ms** |
| p99 | 630 ms |
| RPS | 9.3098 |
| Minimum free RAM | 2015 MB |
| Post-run free RAM | 2178 MB |
| Backend restart delta | 0 |
| TLS | HTTP 200 |
| Decision | **ACCEPTED** |

### Rung 7

Run ID: `gate7-rung7-20261027T132128Z-22ee655`

| Metric | Result |
|---|---:|
| Users | 7 |
| Requests | 1096 |
| Failures | 0 |
| Median | 60 ms |
| Average | 123.1806 ms |
| p95 | **370 ms** |
| p99 | 1100 ms |
| RPS | 12.4043 |
| Minimum free RAM | 2400 MB |
| Post-run free RAM | 2666 MB |
| p95 ratio vs rung 5 | 1.542x |
| Decision | **ACCEPTED** |

Rung-7 ceiling: 480 ms. Observed p95: 370 ms.

### Rung 9

Accepted run ID: `gate7-rung9-rerun-20261027T140605Z-22ee655`

| Metric | Result |
|---|---:|
| Users | 9 |
| Requests | 1323 |
| Failures | 0 |
| Median | 73 ms |
| Average | 122.1110 ms |
| p95 | **330 ms** |
| p99 | 750 ms |
| RPS | 15.2378 |
| Minimum free RAM | 2723 MB |
| Post-run free RAM | 2797 MB |
| Backend restart delta | 0 |
| Proxy restart delta | 0 |
| Locust exceptions | 0 |
| Locust tracebacks | 0 |
| TLS | HTTP 200 |
| p95 ratio vs rung 7 | 0.892x |
| Decision | **ACCEPTED** |

Rung-9 ceiling: 740 ms. Observed p95: 330 ms.

Rung 9 became the highest stable accepted rung.

## Rung 11 - first degradation boundary

Run ID: `gate7-rung11-20261027T141631Z-22ee655`

| Metric | Result |
|---|---:|
| Users | 11 |
| Requests | 1359 |
| Failures | 0 |
| Median | 110 ms |
| Average | 229.1958 ms |
| p95 | **800 ms** |
| p99 | 2300 ms |
| RPS | 15.4099 |
| Minimum free RAM | 2408 MB |
| Post-run free RAM | 2778 MB |
| Backend restart delta | 0 |
| Proxy restart delta | 0 |
| Locust exceptions | 0 |
| Locust tracebacks | 0 |
| Previous accepted p95 | 330 ms |
| Degradation ceiling | 660 ms |
| p95 ratio vs rung 9 | **2.424x** |
| Decision | **NOT ACCEPTED - LATENCY BOUNDARY** |

The system remained functionally healthy: zero request failures, zero exceptions/tracebacks, healthy backends, zero restart delta, TLS 200, and ample RAM. The rejection is specifically a p95 latency degradation decision, not an availability, correctness, Redis, TLS, restart, or memory failure.

```text
2 x 330 ms = 660 ms
800 ms > 660 ms
HIGHEST_STABLE_RUNG=9
FIRST_DEGRADATION_RUNG=11
CAPACITY_BOUNDARY_FOUND=YES
ESCALATE_TO_RUNG_13=NO
```

Rung 13 was intentionally not executed.

## Invalid / non-capacity runs retained for audit context

- Early attempts blocked before load because free RAM was below the launch floor.
- `testing/locust.py` could shadow installed Locust when `testing` was on `PYTHONPATH`; accepted runs used `PYTHONPATH=<repo>\backend` plus an external launcher.
- An early rung-9 run failed because the CA bundle path under the disposable candidate temp tree disappeared during execution. This was a harness/runtime-artifact failure, not a capacity boundary.
- The live TLS certificate was recovered from the running endpoint to a stable Git-ignored path and independently verified with curl and Python Requests. Public certificate fingerprint:

```text
CA380E09E26D5A1B838E48DCDDAF8A85C05AAF703A7FEFF66BC33D487643BE72
```

No private key was copied or exposed.

- A later rung-9 run used expired pre-authenticated access tokens and recorded:

```text
REQUEST_COUNT=1588
FAILURE_COUNT=1569
ERROR={"msg":"Token has expired"}
```

Fresh admin and clinical access tokens were reissued without reseeding, changing token versions, mutating Redis, restarting containers, or printing tokens. Both were verified with HTTP 200 before the accepted rung-9 run.

## Infrastructure safety observation

The candidate runtime used disposable host paths. During Gate 7, bind-mount sources for certificate/storage paths were observed to disappear while running containers continued operating. Because restart/recreation would require those missing host sources, the candidate was not restarted or recreated after discovery.

This did not invalidate accepted load evidence because the live candidate stayed healthy, TLS remained verifiable, the CA bundle was moved to a stable path, restart deltas stayed zero, and accepted runs had zero failures/exceptions.

The deployment harness should be cleaned up before this temporary candidate is reused or restarted.

## Final Gate 7 decision

| Rung | Users | p95 | Failures | Decision |
|---|---:|---:|---:|---|
| 5 | 5 | 240 ms | 0 | ACCEPTED |
| 7 | 7 | 370 ms | 0 | ACCEPTED |
| 9 | 9 | 330 ms | 0 | ACCEPTED |
| 11 | 11 | 800 ms | 0 | FIRST DEGRADATION BOUNDARY |
| 13 | 13 | not run | - | STOPPED BY POLICY |

```text
GATE_7_LADDER_COMPLETE=YES
HIGHEST_STABLE_RUNG=9
FIRST_DEGRADATION_RUNG=11
DEGRADATION_CLASS=P95_LATENCY
CAPACITY_BOUNDARY_FOUND=YES
RUNG_13_EXECUTED=NO
```

Gate 7 is complete for the defined local system-mixed capacity ladder.
