# Wave 4 Gate 8 - System-Mixed Spike Resilience Evidence

| Evidence field | Value |
|---|---|
| Evidence date | 2026-10-28 |
| Repository | `strykachiboy-art/Health_Platform` |
| Branch | `feat/smtp-email-circuit-breaker-integration` |
| Source commit | `7e0e6ccd2744c750c21c7da7cc2b980c9da4128c` |
| Candidate image | `clinic-system-phase9:7e0e6cc` |
| Candidate image ID | `sha256:0a8d9d68c715d5476c7f6f9a90750d17310ed6f5f98c929d8e26a16e9ec9da12` |
| Candidate OCI revision | `7e0e6ccd2744c750c21c7da7cc2b980c9da4128c` |
| Candidate project | `w8head-7e0e6cc` |
| Candidate endpoint | `https://localhost:57566` |
| Scenario | `testing/scenarios/system_mixed.py` |
| Locust version | `2.46.5` |
| Spike users | **11** |
| Spike spawn rate | **11 users/second** |
| Spike duration | **90 seconds** |
| Transition | **0 -> 11 users in approximately one second** |
| Request failures | **0** |
| Recovery | **PASS on first recovery sample** |
| Gate 8 status | **GREEN** |

## Purpose and claim boundary

Wave 4 Gate 8 exercised a controlled sudden system-mixed traffic burst against immutable source:

```text
7e0e6ccd2744c750c21c7da7cc2b980c9da4128c
```

The Gate 8 objective was:

```text
GATE_8=SPIKE_RESILIENCE
OBJECTIVE=CONTROLLED_SUDDEN_BURST
SUCCESS=OVERLOAD_REMAINS_BOUNDED_AND_SYSTEM_RECOVERS
```

Gate 8 did not repeat the Gate 7 gradual capacity ladder.

Gate 7 had established:

```text
HIGHEST_STABLE_RUNG=9
FIRST_DEGRADATION_RUNG=11
DEGRADATION_CLASS=P95_LATENCY
```

Gate 8 therefore used the first previously observed degradation-level concurrency, 11 users, but changed the arrival pattern from gradual ramping to a sudden burst:

```text
0 -> 11 users
spawn_rate=11 users/second
duration=90 seconds
```

The Gate 8 claim is limited to the tested local candidate, system-mixed workload, dataset, host resources, and 11-user sudden-burst profile.

It does not claim an unlimited production spike ceiling.

## Candidate identity

```text
SOURCE_COMMIT=7e0e6ccd2744c750c21c7da7cc2b980c9da4128c
IMAGE=clinic-system-phase9:7e0e6cc
IMAGE_ID=sha256:0a8d9d68c715d5476c7f6f9a90750d17310ed6f5f98c929d8e26a16e9ec9da12
OCI_REVISION=7e0e6ccd2744c750c21c7da7cc2b980c9da4128c
PROJECT=w8head-7e0e6cc
ENDPOINT=https://localhost:57566
REDIS_PORT=57564
HTTP_PORT=57565
HTTPS_PORT=57566
```

The unrelated tracked working-tree modification:

```text
backend/app/core/web_routes.py
```

remained outside the candidate and was protected throughout the Gate 8 workflow by SHA-256:

```text
C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
```

The Gate 8 candidate was assembled from committed source rather than from the modified working tree.

## Isolation topology

Gate 8 used a fresh isolated Docker Compose project:

```text
w8head-7e0e6cc
```

Running candidate services:

```text
w8head-7e0e6cc-proxy-1
w8head-7e0e6cc-backend-1
w8head-7e0e6cc-backend-2
w8head-7e0e6cc-postgres-1
w8head-7e0e6cc-redis-1
```

Candidate-specific persistent resources:

```text
w8head-7e0e6cc-postgres-data
w8head-7e0e6cc-redis-data
w8head-7e0e6cc_default
```

The Gate 8 environment did not attach the primary fleet's PostgreSQL or Redis volumes and did not reuse the historical Gate 7 volumes.

Worker and beat services were intentionally not started because the defined Gate 8 workload exercised the web/system-mixed request path and additional background services would have changed host resource consumption without being required by the scenario.

## Database bootstrap

The candidate PostgreSQL database started fresh and was migrated using the current image.

Migration head:

```text
2fcd41e8c6ed
```

The candidate successfully reached the current migration revision before web load execution.

Both application backends were healthy before proxy traffic was enabled.

Initial candidate health:

```text
/health/live  = 200
/health/ready = 200

backend-1 = healthy
backend-2 = healthy
postgres  = healthy
redis     = healthy
```

## Benchmark dataset

Gate 8 used the repository-native synthetic benchmark seeder:

```text
testing/data/seed_load_test_data.py
```

The fixture profile intentionally reproduced the Gate 7 system-mixed benchmark shape rather than the larger seeder defaults.

Configured state:

```text
clinic=1
control_user=1
staff=12
patients=8
appointments=8
consultations=3
lab_tests=12
lab_orders=4
drugs=12
prescriptions=4
inventory_items=20
wards=2
beds_per_ward=10
ambulances=2
ambulance_trips=50
invoices=8
notifications=1000
conversations=50
messages=500
reports=100
user_devices=10
ai_credits=100000
```

Manifest validation reported:

```text
MANIFEST_DATASET_VERSION=1
MANIFEST_CLINIC_ID=1
MANIFEST_STAFF_COUNT=12
MANIFEST_PATIENT_COUNT=8
MANIFEST_APPOINTMENT_COUNT=8
MANIFEST_CONSULTATION_COUNT=3
MANIFEST_LAB_ORDER_COUNT=4
MANIFEST_PRESCRIPTION_COUNT=4
MANIFEST_INVOICE_COUNT=8
MANIFEST_STAFF_FIRST=1
MANIFEST_PATIENT_FIRST=2
BENCHMARK_MANIFEST_CONTRACT=PASS
```

Locust anchors:

```text
LOCUST_STAFF_ID=1
LOCUST_LAB_PATIENT_ID=2
LOCUST_PRESCRIPTION_PATIENT_ID=2
LOCUST_PATIENT_ID=2
```

Tokens and passwords remained outside repository artifacts and were not printed.

## Pre-spike fixture and harness discoveries

Several pre-spike attempts were retained as setup/defect-discovery evidence and are not counted as Gate 8 spike evidence.

### PowerShell login payload quoting

The first control-user authentication probe failed because an inline PowerShell/curl JSON argument was not transported reliably.

The retry used a temporary request file with `curl --data-binary`.

Result:

```text
AUTH_CURL_EXIT=0
AUTH_HTTP_CODE=200
AUTH_SUCCESS=True
ACCESS_TOKEN_PRESENT=True
REFRESH_TOKEN_PRESENT=True
CONTROL_USER_AUTH=PASS
```

This was a harness/probe formatting problem, not an authentication defect.

### Locust import-path failure

An initial Locust attempt failed before virtual users started:

```text
ModuleNotFoundError: No module named 'common'
```

`system_mixed.py` imports modules under `testing/common`, but placing `testing` directly on `PYTHONPATH` is unsafe because `testing/locust.py` can shadow the installed Locust package.

The accepted launcher pattern therefore used:

```text
PYTHONPATH=<repo>\backend
TESTING_ON_PYTHONPATH=NO
```

and an external temporary Python launcher that imported installed Locust before adding the testing directory to `sys.path`.

Verified runtime:

```text
locust 2.46.5
Python 3.12.10
LOCUST_LAUNCHER=PASS
```

The launcher and token file were deleted after execution.

### Missing benchmark clinic settings

The first successful one-user workload run exposed one missing synthetic fixture row:

```text
GET /api/v1/settings/clinic
HTTP 404
{"error":"Clinic settings not found","success":false}
```

That run produced 30 requests with one failure and was rejected as baseline evidence.

The application already contained the transactional repository service:

```text
ensure_clinic_settings(clinic_id)
```

No application-code change was required.

The isolated Gate 8 clinic was initialized through that existing application service.

Verification:

```text
CLINIC_SETTINGS_ID=1
CLINIC_SETTINGS_CLINIC_ID=1
CLINIC_SETTINGS_INITIALIZATION=PASS
POST_SETTINGS_COUNT=1
CLINIC_SETTINGS_ROW=PASS
LIVE_HTTP_CODE=200
READY_HTTP_CODE=200
CANDIDATE_HEALTH=PASS
```

No production or Gate 7 database was modified.

## Accepted pre-spike baseline

Run ID:

```text
gate8-baseline-final-20261028T073105Z-7e0e6cc
```

Profile:

```text
users=1
spawn_rate=1
duration=20s
wait_time=2-4s
AI=disabled
extra_access_control=disabled
preauthenticated_tokens=yes
TLS_verification=yes
```

Result:

| Metric | Result |
|---|---:|
| Requests | 38 |
| Failures | **0** |
| Median | 46 ms |
| Average | 113.4405 ms |
| p95 | **200 ms** |
| RPS | 2.3633 |
| Locust exit | 0 |
| Post-baseline liveness | 200 |
| Post-baseline readiness | 200 |
| Decision | **PASS** |

```text
BASELINE_EXIT_CODE=0
BASELINE_REQUESTS=38
BASELINE_FAILURES=0
BASELINE_MEDIAN_MS=46
BASELINE_AVG_MS=113.44053947342266
BASELINE_P95_MS=200
BASELINE_RPS=2.3632684326789177
POST_BASELINE_LIVE_HTTP=200
POST_BASELINE_READY_HTTP=200
PRE_SPIKE_BASELINE=PASS
```

This baseline established that the workload, fixture, authentication, TLS, candidate routing, and application responses were valid before the sudden burst.

## Gate 8 controlled sudden burst

Run ID:

```text
gate8-spike11-20261028T073140Z-7e0e6cc
```

Spike profile:

```text
SPIKE_USERS=11
SPIKE_SPAWN_RATE=11
SPIKE_DURATION=90s
SPIKE_TRANSITION=0_TO_11_APPROX_ONE_SECOND
```

Locust confirmed:

```text
Ramping to 11 users at a rate of 11.00 per second
All users spawned: {"SystemMixedUser": 11}
```

This is the accepted Gate 8 workload.

## Spike results

| Metric | Result |
|---|---:|
| Users | 11 |
| Requests | **1571** |
| Failures | **0** |
| Median | **91 ms** |
| Average | **166.0828 ms** |
| p95 | **490 ms** |
| p99 | **1400 ms** |
| Maximum | **3243.09 ms** |
| RPS | **17.9396** |
| Locust failure rows | **0** |
| Locust exception rows | **0** |
| Locust exit | **0** |

Evidence:

```text
SPIKE_LOCUST_EXIT_CODE=0

SPIKE_REQUESTS=1571
SPIKE_FAILURES=0
SPIKE_MEDIAN_MS=91
SPIKE_AVG_MS=166.08284277524882
SPIKE_P95_MS=490
SPIKE_P99_MS=1400
SPIKE_MAX_MS=3243.0912000054377
SPIKE_RPS=17.939579282805727

LOCUST_FAILURE_ROW_COUNT=0
LOCUST_EXCEPTION_ROW_COUNT=0
```

No application request failure, Locust exception, or uncontrolled error condition was observed during the defined sudden burst.

## Latency distribution

Aggregate spike percentiles:

```text
p50   = 91 ms
p66   = 130 ms
p75   = 170 ms
p80   = 200 ms
p90   = 340 ms
p95   = 490 ms
p98   = 780 ms
p99   = 1400 ms
p99.9 = 3100 ms
max   ~= 3200 ms
```

The tail increased under the sudden arrival event, but the increase remained bounded: requests continued completing, no request failures were recorded, no exception rows were emitted, and infrastructure remained healthy.

## Immediate post-spike health

Immediately after Locust completed:

```text
IMMEDIATE_LIVE_HTTP=200
IMMEDIATE_READY_HTTP=200

backend-1=healthy
backend-2=healthy
postgres=healthy
redis=healthy
```

The system therefore remained serviceable immediately after the defined spike.

## Recovery evidence

The first recovery sample reported:

```text
RECOVERY_SAMPLE=1
LIVE=200
READY=200
B1=healthy
B2=healthy
POSTGRES=healthy
REDIS=healthy
```

Result:

```text
SYSTEM_RECOVERED=True
```

No extended recovery period was required.

## Restart evidence

Restart deltas across the burst:

```text
BACKEND1_RESTART_DELTA=0
BACKEND2_RESTART_DELTA=0
PROXY_RESTART_DELTA=0
```

Therefore:

```text
NO_RESTART_STORM=True
```

There was no crash loop or process-restart storm.

## Post-spike resource snapshot

```text
proxy:
CPU=0.00%
MEM=3.789 MiB / 3.784 GiB
MEM_PCT=0.10%
PIDS=2

backend-1:
CPU=0.06%
MEM=201.1 MiB / 3.784 GiB
MEM_PCT=5.19%
PIDS=2

backend-2:
CPU=0.04%
MEM=201.2 MiB / 3.784 GiB
MEM_PCT=5.19%
PIDS=2

postgres:
CPU=0.00%
MEM=83.39 MiB / 3.784 GiB
MEM_PCT=2.15%
PIDS=15

redis:
CPU=0.29%
MEM=6.238 MiB / 3.784 GiB
MEM_PCT=0.16%
PIDS=6
```

This is a post-spike snapshot, not a claim about peak resource consumption during the burst.

It demonstrates that the candidate was not left in an obviously saturated state after load removal.

## Gate 7 comparison

Gate 7 and Gate 8 answer different questions.

Gate 7 used gradual arrival:

```text
spawn_rate=1 user/second
```

and identified:

```text
HIGHEST_STABLE_RUNG=9
FIRST_DEGRADATION_RUNG=11
```

The Gate 7 11-user run recorded:

```text
requests=1359
failures=0
median=110 ms
average=229.1958 ms
p95=800 ms
p99=2300 ms
RPS=15.4099
```

Gate 8 used a sudden 11-user arrival and recorded:

```text
requests=1571
failures=0
median=91 ms
average=166.0828 ms
p95=490 ms
p99=1400 ms
RPS=17.9396
```

The lower Gate 8 p95 must not be interpreted as a universal claim that sudden bursts are faster than gradual arrival.

The Gate 7 and Gate 8 evidence came from different immutable source revisions and separate candidate environments.

The relevant Gate 8 conclusion is narrower:

```text
At the defined 11-user sudden-burst profile,
overload remained bounded and the current candidate recovered cleanly.
```

## Security and source integrity

After the spike:

```text
FINAL_BRANCH=feat/smtp-email-circuit-breaker-integration
FINAL_HEAD=7e0e6ccd2744c750c21c7da7cc2b980c9da4128c
FINAL_PROTECTED_WEB_ROUTES_SHA256=C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
FINAL_SOURCE_LOCK=PASS
FINAL_PROTECTED_WEB_ROUTES=PASS
```

No credentials or tokens were printed.

Temporary authentication token files and the external Locust launcher were removed after execution.

The following remained unchanged:

```text
PRIMARY_FLEET_TOUCHED=NO
GATE7_FLEET_TOUCHED=NO
APPLICATION_SOURCE_CHANGED=NO
PRIMARY_ROUTING_CHANGED=NO
```

## Evidence artifacts

Gate 8 result directory:

```text
testing/results/gate8-spike-20261028T073105Z-7e0e6cc
```

The execution generated Locust CSV evidence for the accepted baseline and spike.

The accepted spike run ID was:

```text
gate8-spike11-20261028T073140Z-7e0e6cc
```

These artifacts should be preserved according to the repository benchmark-evidence policy.

## Final Gate 8 decision

| Criterion | Result |
|---|---|
| Exact current source locked | PASS |
| Isolated candidate | PASS |
| Pre-spike liveness/readiness | PASS |
| Benchmark workload validity | PASS |
| Accepted pre-spike baseline | PASS |
| Controlled sudden burst executed | YES |
| 0 -> 11 arrival achieved | YES |
| Request failures | **0** |
| Locust exceptions | **0** |
| Backend restart storm | **NO** |
| Proxy restart storm | **NO** |
| PostgreSQL loss | **NO** |
| Redis loss | **NO** |
| Immediate post-spike health | **PASS** |
| Recovery | **PASS - first sample** |
| Residual obvious saturation | **NO** |
| Protected source preserved | PASS |
| Overload bounded | **YES** |
| Gate 8 | **GREEN** |

Final closure:

```text
GATE8_ENTRY=PASS
PRE_SPIKE_BASELINE=PASS

CONTROLLED_SUDDEN_BURST_EXECUTED=YES
SPIKE_USERS=11
SPIKE_SPAWN_RATE=11
SPIKE_DURATION=90s

SPIKE_REQUESTS=1571
SPIKE_FAILURES=0
LOCUST_FAILURE_ROW_COUNT=0
LOCUST_EXCEPTION_ROW_COUNT=0

NO_RESTART_STORM=True
IMMEDIATE_POST_SPIKE_HEALTH=PASS
SYSTEM_RECOVERED=True

OVERLOAD_BOUNDED=True

PRIMARY_FLEET_TOUCHED=NO
GATE7_FLEET_TOUCHED=NO
APPLICATION_SOURCE_CHANGED=NO
PRIMARY_ROUTING_CHANGED=NO

GATE_8_SPIKE_RESILIENCE=COMPLETE
GATE8_EXIT=GREEN
```

Wave 4 Gate 8 is complete for the defined 11-user system-mixed sudden-burst resilience profile.
