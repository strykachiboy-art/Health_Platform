# Wave 4 Gate 9 - System-Mixed Soak Stability Evidence

| Evidence field | Value |
|---|---|
| Evidence date | 2026-10-29 |
| Repository | `strykachiboy-art/Health_Platform` |
| Branch | `feat/smtp-email-circuit-breaker-integration` |
| Source commit | `44e31087d5463be8582857b1422f2bdd017f49d7` |
| Source parent | `ebcd8f3d0839e99bd57bc3ea27b3f57b0bc988d2` |
| Candidate image | `clinic-system-g9soak:44e3108` |
| Candidate image ID | `sha256:1ccd00654305395ea8f4013c213a95cc0d591fe68917aead3281bf16e2f6790f` |
| Candidate OCI revision | `44e31087d5463be8582857b1422f2bdd017f49d7` |
| Candidate project | `g9soak-44e3108-20261029003907` |
| Candidate endpoint | `https://localhost:57767` |
| Scenario | `testing/scenarios/system_mixed.py` |
| Locust version | `2.46.5` |
| Soak users | **9** |
| Spawn rate | **1 user/second** |
| Soak duration | **30 minutes** |
| Requests | **30,352** |
| Request failures | **0** |
| Locust exceptions | **0** |
| Aggregate p95 | **210 ms** |
| Aggregate p99 | **410 ms** |
| Aggregate RPS | **16.8879** |
| Gate 9 status | **GREEN** |

## Purpose and claim boundary

Wave 4 Gate 9 validated sustained system-mixed stability at the previously established highest stable capacity rung:

```text
USERS=9
SPAWN_RATE=1
DURATION=30m
```

Gate 7 had established:

```text
HIGHEST_STABLE_RUNG=9
FIRST_DEGRADATION_RUNG=11
DEGRADATION_CLASS=P95_LATENCY
```

Gate 8 then demonstrated bounded behavior and immediate recovery under a sudden 11-user burst.

Gate 9 answers a different question:

```text
Can the optimized candidate sustain the accepted 9-user system-mixed profile
for 30 minutes while remaining within latency, throughput, memory,
health, restart, and proxy-routing bounds?
```

The Gate 9 claim is limited to the tested local candidate, dataset, host resources, system-mixed workload, and defined acceptance contract. It is not a universal production-capacity claim.

## Source and candidate identity

The accepted Gate 9 candidate was assembled from committed source only:

```text
SOURCE_COMMIT=44e31087d5463be8582857b1422f2bdd017f49d7
SOURCE_PARENT=ebcd8f3d0839e99bd57bc3ea27b3f57b0bc988d2
IMAGE=clinic-system-g9soak:44e3108
IMAGE_ID=sha256:1ccd00654305395ea8f4013c213a95cc0d591fe68917aead3281bf16e2f6790f
OCI_REVISION=44e31087d5463be8582857b1422f2bdd017f49d7
PROJECT=g9soak-44e3108-20261029003907
ENDPOINT=https://localhost:57767
HTTP_PORT=57766
HTTPS_PORT=57767
POSTGRES_PORT=57764
REDIS_PORT=57765
```

The unrelated tracked working-tree modification:

```text
backend/app/core/web_routes.py
```

remained outside the candidate and was protected throughout Gate 9 by SHA-256:

```text
C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
```

Final source integrity remained:

```text
FINAL_BRANCH=feat/smtp-email-circuit-breaker-integration
FINAL_HEAD=44e31087d5463be8582857b1422f2bdd017f49d7
FINAL_PROTECTED_WEB_ROUTES_SHA256=C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
```

## Candidate topology

Gate 9 used an isolated five-container candidate:

```text
g9soak-44e3108-20261029003907-proxy-1
g9soak-44e3108-20261029003907-backend-1
g9soak-44e3108-20261029003907-backend-2
g9soak-44e3108-20261029003907-postgres-1
g9soak-44e3108-20261029003907-redis-1
```

Candidate-specific data resources:

```text
POSTGRES_VOLUME_SOURCE=g9soak-44e3108-20261029003907-postgres-data
REDIS_VOLUME_SOURCE=g9soak-44e3108-20261029003907-redis-data
ISOLATED_DATA_VOLUMES=PASS
```

The current migration head was reached before load execution:

```text
MIGRATION_HEAD=2fcd41e8c6ed
DATABASE_MIGRATION=PASS
```

## Benchmark dataset

Gate 9 used the repository-native synthetic benchmark dataset.

Validated state:

```text
dataset_version=1
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
ambulances=2
invoices=8
notifications=1000
conversations=50
messages=500
reports=100
user_devices=10
ai_credits=100000

staff_first=1
patient_first=2
```

Clinic settings initialization also passed.

Tokens and passwords remained outside repository artifacts and were not printed.

## Persistent backend failover policy

Before Gate 9, the backend passive-failover policy was changed from:

```text
max_fails=2
```

to:

```text
max_fails=1
```

The tracked upstream bootstrap generated exactly two backend routes.

Gate 9 verified the effective policy before and after the accepted soak:

```text
UPSTREAM_MAX_FAILS1_COUNT=2
UPSTREAM_MAX_FAILS2_COUNT=0
FINAL_UPSTREAM_MAX_FAILS1_COUNT=2
FINAL_UPSTREAM_MAX_FAILS2_COUNT=0
UPSTREAM_POLICY_STABLE=PASS
```

## Gate 9 acceptance contract

The acceptance thresholds were held constant through diagnosis and rerun:

```text
REQUEST_FAILURES=0
LOCUST_EXCEPTIONS=0
RESTART_DELTA=0
FINAL_LIVE=200
FINAL_READY=200

RPS >= 10
THROUGHPUT_DROP <= 15%
HOST_FREE_MEMORY >= 512 MB

BACKEND_MEMORY_FAIL_ONLY_IF:
  growth > 150 MiB
  AND growth > 25%

NGINX_MAX_FAILS_1_COUNT=2
NGINX_MAX_FAILS_2_COUNT=0

OVERALL_P95 <= 660 ms
```

No threshold was relaxed to obtain the GREEN result.

## Stage A - fresh optimized candidate and baseline

Gate 9 Stage A created a fresh candidate from commit `44e3108`.

Candidate setup passed:

```text
CANDIDATE_SOURCE_FROM_COMMIT=PASS
CANDIDATE_IMAGE_BUILD=PASS
DATABASE_MIGRATION=PASS
BENCHMARK_MANIFEST=PASS
CLINIC_SETTINGS_INITIALIZATION=PASS
TWO_BACKENDS_HEALTHY=PASS
FRESH_UPSTREAM_BOOTSTRAP=PASS
NGINX_EFFECTIVE_ROUTES=PASS
CANDIDATE_HEALTH=PASS
```

The short pre-soak baseline recorded:

| Metric | Result |
|---|---:|
| Requests | 30 |
| Failures | 0 |
| Median | 130 ms |
| Average | 227.5435 ms |
| p95 | 580 ms |
| RPS | 1.8549 |
| Failure rows | 0 |
| Exception rows | 0 |
| Decision | **PASS** |

Stage A closed:

```text
GATE9_STAGE_A=GREEN
NEXT_STEP=RUN_9_USER_30_MINUTE_SOAK
```

## Initial Stage B run - retained latency-only failure

The first valid optimized 30-minute Stage B workload was retained rather than overwritten.

It recorded:

```text
requests=21696
failures=0
median=130 ms
average=296.5990 ms
p95=1100 ms
p99=2300 ms
RPS=12.0772
failure_rows=0
exception_rows=0
restart_delta=0
live=200
ready=200
```

The workload was functionally correct and available, but the run was rejected because:

```text
P95_LIMIT_MS=660
CURRENT_P95_MS=1100
GATE9_STATUS=RED_LATENCY_ONLY
```

The failed run remained evidence and was not deleted or rewritten.

## Latency differential diagnosis

A read-only differential analysis compared patched routes against untouched control routes.

The patched and unpatched route groups degraded similarly:

```text
PATCHED_ROUTE_COUNT=5
CONTROL_ROUTE_COUNT=8
PATCHED_MEDIAN_P95_RATIO=1.622
CONTROL_MEDIAN_P95_RATIO=1.684
PATCHED_MINUS_CONTROL_RATIO=-0.062
```

Host headroom had materially changed relative to the earlier reference run:

```text
PREVIOUS_HOST_FREE_MB_MIN=2099.5
CURRENT_HOST_FREE_MB_MIN=1379.37
HOST_FREE_MB_DELTA=-720.13
HOST_FREE_MB_DELTA_PCT=-34.3
```

The diagnostic therefore classified the 1100 ms result as:

```text
DIAGNOSTIC_CLASSIFICATION=SYSTEM_WIDE_RUNTIME_SLOWDOWN_SIGNAL
DIAGNOSTIC_REASON=PATCHED_AND_UNPATCHED_ROUTES_DEGRADED_SIMILARLY
HOST_HEADROOM_RUN_TO_RUN_SHIFT=MATERIAL
```

This evidence did not declare Gate 9 GREEN. It justified a controlled environment rerun without changing code or thresholds.

## Controlled environment qualification

Stale benchmark projects were stopped using reversible `docker stop` actions only.

The stale projects reached zero running containers:

```text
g9soak-ebcd8f3-20261028121129 = 0
w8head-7e0e6cc               = 0
w4head-22ee655                = 0
```

The primary `clinic-system` fleet was then temporarily stopped for benchmark isolation.

Before the accepted controlled soak:

```text
CONTROLLED_RUNNING_CONTAINER_COUNT=5
CONTROLLED_DOCKER_SCOPE=PASS
PRIMARY_FLEET_ISOLATED=YES
STALE_BENCHMARK_FLEETS_ISOLATED=YES
```

Only the five Gate 9 candidate containers remained running.

The controlled pre-run host headroom was:

```text
HOST_MEMORY_TOTAL_MB=8108.38
HOST_MEMORY_FREE_MB=2310.7
CONTROLLED_PRERUN_HOST_FREE_MB_MIN=2000
CONTROLLED_PRERUN_HOST_HEADROOM=PASS
```

The `2000 MB` value was an environment-comparability precondition for the controlled rerun. It did not replace or relax the formal Gate 9 host-memory acceptance floor of `512 MB`.

## Accepted controlled 30-minute soak

Accepted evidence prefix:

```text
gate9-controlled-9u-30m-44e3108
```

Profile:

```text
users=9
spawn_rate=1 user/second
duration=30m
resource_sampling=30s
TLS_verification=yes
preauthenticated_tokens=yes
```

Locust completion evidence:

```text
LOCUST_RUNTIME_LIMIT_REACHED=True
LOCUST_REPORTED_EXIT_CODE_ZERO=True
LOCUST_PROCESS_EXIT_CONSISTENT=YES
LOCUST_30M_COMPLETION=PASS
```

## Accepted soak results

| Metric | Result |
|---|---:|
| Requests | **30,352** |
| Failures | **0** |
| Median | **55 ms** |
| Average | **82.2927 ms** |
| p95 | **210 ms** |
| p99 | **410 ms** |
| RPS | **16.8879** |
| Locust failure rows | **0** |
| Locust exception rows | **0** |

Raw aggregate evidence:

```text
SOAK_REQUESTS=30352
SOAK_FAILURES=0
SOAK_MEDIAN_MS=55
SOAK_AVG_MS=82.2926520526256
SOAK_P95_MS=210
SOAK_P99_MS=410
SOAK_RPS=16.8879124939928
SOAK_FAILURE_ROW_COUNT=0
SOAK_EXCEPTION_ROW_COUNT=0
```

The hard latency gate passed with substantial margin:

```text
LIMIT_OVERALL_P95_MS=660
SOAK_P95_MS=210
LATENCY_GATE=PASS
```

## Throughput and latency drift

First and last five-minute windows:

```text
FIRST5_AVG_RPS=16.326
LAST5_AVG_RPS=17.168
THROUGHPUT_DROP_PCT=-5.15

FIRST5_AVG_REPORTED_P95_MS=295.5
LAST5_AVG_REPORTED_P95_MS=166.47
P95_DRIFT_PCT=-43.66
```

Throughput increased rather than degrading.

Latency also improved during the run.

Result:

```text
THROUGHPUT_STABILITY=PASS
```

## Resource evidence

Resource sampling:

```text
RESOURCE_SAMPLE_COUNT=52
HOST_FREE_MB_MIN=2240.04
CANDIDATE_TOTAL_MIB_MAX=518.04
FIRST5_BACKEND_COMBINED_MIB=417.84
LAST5_BACKEND_COMBINED_MIB=417.08
BACKEND_MEMORY_GROWTH_MIB=-0.77
BACKEND_MEMORY_GROWTH_PCT=-0.18
```

The backend memory footprint did not exhibit sustained growth.

Result:

```text
RESOURCE_BOUNDS=PASS
```

## Health and restart stability

Pre-soak:

```text
PRE_SOAK_LIVE_HTTP=200
PRE_SOAK_READY_HTTP=200
```

Final health:

```text
FINAL_LIVE_HTTP=200
FINAL_READY_HTTP=200
```

All five candidate containers remained running.

Backends, PostgreSQL, and Redis remained healthy.

Restart evidence:

```text
backend-1 restart delta = 0
backend-2 restart delta = 0
proxy restart delta     = 0
postgres restart delta  = 0
redis restart delta     = 0

RESTART_DELTA_TOTAL=0
RESTART_STABILITY=PASS
HEALTH_DURING_SOAK=PASS
```

No restart storm, crash loop, or health loss occurred.

## Controlled result versus host-contended result

The same optimized source revision produced two materially different latency results under different host contention states:

| Run | Requests | Failures | p95 | RPS | Minimum host free RAM |
|---|---:|---:|---:|---:|---:|
| Host-contended Stage B | 21,696 | 0 | 1100 ms | 12.0772 | 1379.37 MB |
| Controlled Stage B | 30,352 | 0 | **210 ms** | **16.8879** | **2240.04 MB** |

The controlled rerun did not change application source, workload profile, acceptance thresholds, candidate image identity, dataset, or Gate 9 routing policy.

The evidence therefore supports the narrower conclusion:

```text
The earlier 1100 ms Stage B rejection was dominated by host/runtime contention.
It is not evidence of a reproducible application regression in commit 44e3108.
```

The rejected run remains part of the audit trail.

## Evidence artifacts

Controlled-run files:

```text
C:\Users\HP\AppData\Local\Temp\clinic-g9soak-44e3108-20261029003907\evidence\gate9-controlled-resource-samples-44e3108.csv

C:\Users\HP\AppData\Local\Temp\clinic-g9soak-44e3108-20261029003907\evidence\gate9-controlled-9u-30m-44e3108_stats.csv

C:\Users\HP\AppData\Local\Temp\clinic-g9soak-44e3108-20261029003907\evidence\gate9-controlled-9u-30m-44e3108_stats_history.csv
```

The earlier failed-run stats/history files were explicitly preserved before the controlled rerun.

## Post-run primary-fleet restoration

After Gate 9 evidence collection, the primary fleet was restored using the existing containers only.

Restoration result:

```text
PRIMARY_STARTED=clinic-system-postgres-1
PRIMARY_STARTED=clinic-system-redis-1
PRIMARY_STARTED=clinic-system-backend-1
PRIMARY_STARTED=clinic-system-backend-2
PRIMARY_STARTED=clinic-system-backend-3
PRIMARY_STARTED=clinic-system-proxy-1
```

Final primary state:

```text
clinic-system-postgres-1  running  restarts=0
clinic-system-redis-1     running  restarts=0
clinic-system-backend-1   running  restarts=0
clinic-system-backend-2   running  restarts=0
clinic-system-backend-3   running  restarts=0
clinic-system-proxy-1     running  restarts=0
```

Closure:

```text
PRIMARY_FLEET_RESTORED=PASS
FILES_CHANGED=NO
CONTAINERS_CREATED=NO
VOLUMES_CHANGED=NO
IMAGES_CHANGED=NO
```

## Final Gate 9 decision

Every hard Gate 9 acceptance condition passed in the controlled 30-minute run:

| Acceptance condition | Threshold | Observed | Decision |
|---|---:|---:|---|
| Request failures | 0 | 0 | PASS |
| Locust exceptions | 0 | 0 | PASS |
| Aggregate p95 | <= 660 ms | **210 ms** | PASS |
| RPS | >= 10 | **16.8879** | PASS |
| Throughput drop | <= 15% | **-5.15%** | PASS |
| Minimum free host RAM | >= 512 MB | **2240.04 MB** | PASS |
| Backend memory growth | fail only if >150 MiB AND >25% | **-0.77 MiB / -0.18%** | PASS |
| Restart delta | 0 | **0** | PASS |
| Final liveness | 200 | **200** | PASS |
| Final readiness | 200 | **200** | PASS |
| Nginx `max_fails=1` routes | 2 | **2** | PASS |
| Nginx `max_fails=2` routes | 0 | **0** | PASS |

Final closure:

```text
REQUEST_FAILURES=ZERO
LOCUST_EXCEPTIONS=ZERO
HEALTH_DURING_SOAK=PASS
RESTART_STABILITY=PASS
RESOURCE_BOUNDS=PASS
LATENCY_GATE=PASS
THROUGHPUT_STABILITY=PASS
UPSTREAM_POLICY_STABLE=PASS

OPTIMIZED_HEAD=44e31087d5463be8582857b1422f2bdd017f49d7
ACCEPTANCE_P95_LIMIT_MS=660

GATE9_CONTROLLED_STAGE_B=GREEN
GATE9_RERUN_STAGE_B=GREEN
GATE9_STAGE_B=GREEN
GATE9_30_MINUTE_SOAK=GREEN
```

Wave 4 Gate 9 is closed GREEN for the defined system-mixed 9-user, 30-minute sustained-soak profile.