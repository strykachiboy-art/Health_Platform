# Wave 4 Gate 10 - System-Mixed Controlled Failure Recovery Evidence

| Evidence field | Value |
|---|---|
| Evidence date | 2026-10-29 |
| Repository | `strykachiboy-art/Health_Platform` |
| Branch | `feat/smtp-email-circuit-breaker-integration` |
| Control HEAD | `510f8f8721407ef606a32666e9601ca14729b86d` |
| Tested runtime source | `44e31087d5463be8582857b1422f2bdd017f49d7` |
| Candidate image | `clinic-system-g9soak:44e3108` |
| Candidate image ID | `sha256:1ccd00654305395ea8f4013c213a95cc0d591fe68917aead3281bf16e2f6790f` |
| Candidate OCI revision | `44e31087d5463be8582857b1422f2bdd017f49d7` |
| Candidate project | `g9soak-44e3108-20261029003907` |
| Candidate endpoint | `https://localhost:57767` |
| Scenario | `testing/scenarios/system_mixed.py` |
| Locust version | `2.46.5` |
| Users | **9** |
| Spawn rate | **1 user/second** |
| Duration | **8 minutes** |
| Deliberate failure | `backend-1` stopped during active load |
| Failure hold | approximately **90 seconds** |
| Backend recovery | **28.55 seconds** |
| Requests | **7,956** |
| Request failures | **0** |
| Locust exceptions | **0** |
| Median | **53 ms** |
| Average | **88.1981 ms** |
| Aggregate p95 | **220 ms** |
| Aggregate p99 | **550 ms** |
| Aggregate RPS | **16.6745** |
| Gate 10 status | **GREEN** |

## Purpose and claim boundary

Wave 4 Gate 10 validated application-level backend failover and recovery under active system-mixed load.

The gate answers the operational question:

```text
If one application backend is deliberately removed during live load,
does the surviving backend continue serving successfully, can the failed
backend recover and rejoin routing, and does the application/domain state
remain correct afterward?
```

This gate is specifically an application-backend failover and recovery proof.

It is not a PostgreSQL disaster-recovery drill, Redis disaster-recovery drill, host failover test, or universal production availability claim.

Gate 8 established spike resilience.

Gate 9 established sustained 9-user soak stability.

Gate 10 adds a distinct proof: deliberate application-backend failure during load, continued service through the surviving backend, backend recovery, routing re-entry, and unchanged core benchmark-domain data.

## Source and runtime identity

The official Gate 10 execution used the same application runtime source already accepted by Gate 9:

```text
RUNTIME_SOURCE=44e31087d5463be8582857b1422f2bdd017f49d7
IMAGE=clinic-system-g9soak:44e3108
IMAGE_ID=sha256:1ccd00654305395ea8f4013c213a95cc0d591fe68917aead3281bf16e2f6790f
OCI_REVISION=44e31087d5463be8582857b1422f2bdd017f49d7
PROJECT=g9soak-44e3108-20261029003907
ENDPOINT=https://localhost:57767
```

The branch control HEAD during Gate 10 was:

```text
510f8f8721407ef606a32666e9601ca14729b86d
```

The only committed differences between the Gate 9 runtime source and the control HEAD were Gate 9 documentation files:

```text
docs/runbooks/README.md
docs/runbooks/wave4-gate9-system-mixed-soak-stability-evidence.md
```

Therefore:

```text
RUNTIME_CODE_CHANGED_SINCE_GATE9=NO
RUNTIME_SOURCE_EQUIVALENCE=PASS
```

## Protected working-tree state

The unrelated tracked local modification remained protected and outside the candidate:

```text
backend/app/core/web_routes.py
```

Required SHA-256:

```text
C483E7D6FF6B0216224E1B6713A54E727211D86B5B7D9093B9E4F27CE5BA7352
```

The hash matched before and after Gate 10.

The file remained dirty and uncommitted and was never staged.

```text
PROTECTED_WEB_ROUTES=PASS
PROTECTED_WEB_ROUTES_STAGED=NO
PROTECTED_WEB_ROUTES_DIRTY_PRESERVED=YES
```

## Candidate topology

Gate 10 reused the isolated Gate 9 candidate:

```text
g9soak-44e3108-20261029003907-proxy-1
g9soak-44e3108-20261029003907-backend-1
g9soak-44e3108-20261029003907-backend-2
g9soak-44e3108-20261029003907-postgres-1
g9soak-44e3108-20261029003907-redis-1
```

The primary `clinic-system` fleet was temporarily stopped during the controlled candidate run to avoid host-contention interference.

Only the five candidate containers were in the controlled scope.

## Candidate TLS runtime qualification

The retained Gate 9 proxy used a bind-mounted temporary certificate directory.

Before the official Gate 10 execution, the candidate temp TLS directory was requalified against the repository certificate and key.

Verified SHA-256 values:

```text
localhost.crt:
821F9B3FD57E1B3E6E9B2A5CCA4C7AEEE0ADC6941BC1ED80F1795D3BA6E494FE

localhost.key:
00534B985782F9C23EC64F1885C83FB44022D37056BB910CD0145F9DECA122DF
```

The disposable candidate temp directory was rehydrated when required.

Repository TLS files were not changed.

This was candidate-runtime restoration only and did not alter tested application source.

## Locust harness qualification

A Windows/Python import-path collision was diagnosed before the accepted execution:

```text
testing/locust.py
```

could shadow the installed third-party `locust` package if the `testing` directory was placed directly on `PYTHONPATH`.

The accepted harness used a temporary launcher that imported the real installed Locust package first and then exposed `testing` so the scenario's `common.*` helpers remained importable.

Accepted preflight evidence:

```text
LOCUST_IMPORT_ORIGIN=C:\Users\HP\AppData\Local\Programs\Python\Python312\Lib\site-packages\locust\__init__.py
LOCUST_VERSION=2.46.5
LOCUST_COMMON_IMPORT_PREFLIGHT=PASS
LOCUST_HARNESS_PREFLIGHT=PASS
```

The temporary launcher changed no repository source.

## Gate 10 test profile

Official execution profile:

```text
USERS=9
SPAWN_RATE=1
DURATION=8m
PRE_FAILURE_STEADY_STATE=120s
FAILURE_TARGET=backend-1
FAILURE_ACTION=docker stop -t 1
FAILURE_HOLD≈90s
RECOVERY_ACTION=docker start backend-1
```

AI workload execution was disabled.

The scenario remained read-dominant for the domain-state verification contract.

## Proxy failover policy

The effective backend upstream policy was:

```text
NGINX_MAX_FAILS_1_COUNT=2
NGINX_MAX_FAILS_2_COUNT=0
```

Both backend routes used:

```text
max_fails=1
```

Pre-load health:

```text
PRE_LOAD_LIVE=200
PRE_LOAD_READY=200
```

## Deliberate failure injection

After the 120-second pre-failure steady-state window:

```text
INJECTION_UTC=2026-10-29T06:04:15.6935957Z
FAILURE_TARGET=g9soak-44e3108-20261029003907-backend-1
FAILURE_ACTION=docker stop -t 1
BACKEND1_FAILURE_INJECTED=YES
BACKEND2_SURVIVOR_RUNNING=YES
```

During the outage window, six health samples were collected.

Every sample returned:

```text
LIVE=200
READY=200
```

Result:

```text
OUTAGE_LIVE_READY_CONTINUITY=PASS
```

## Failover routing evidence

Persisted nginx logs proved the failed backend was encountered and the surviving backend continued receiving traffic.

Observed during the failure window:

```text
BACKEND1_UPSTREAM_REFERENCES_DURING_FAILURE=14
BACKEND2_REQUESTS_DURING_FAILURE_LOG_MATCHES=1590
BACKEND2_FAILOVER_DURING_FAILURE=PASS
```

An expected connection-refused upstream attempt was observed against the deliberately stopped backend.

Despite that injected failure, Locust recorded zero failed requests for the full run.

## Backend recovery

Recovery action:

```text
RECOVERY_ACTION_UTC=2026-10-29T06:05:50.3263415Z
BACKEND_RECOVERED_UTC=2026-10-29T06:06:19.9298424Z
BACKEND1_RECOVERY_SECONDS=28.55
```

The recovery contract allowed up to 90 seconds.

Result:

```text
BACKEND1_HEALTH_RECOVERY=PASS
```

Six post-recovery samples then recorded:

```text
LIVE=200
READY=200
backend-1=healthy
backend-2=healthy
```

Result:

```text
POST_RECOVERY_HEALTH=PASS
```

## Backend routing re-entry

Persisted proxy logs proved the recovered backend rejoined traffic while the survivor continued serving.

```text
BACKEND1_REQUESTS_AFTER_RECOVERY_LOG_MATCHES=2043
BACKEND2_REQUESTS_AFTER_RECOVERY_LOG_MATCHES=2042

BACKEND1_REJOIN_AFTER_RECOVERY=PASS
TWO_BACKEND_ROUTING_AFTER_RECOVERY=PASS
```

## Locust completion and request results

The PowerShell harness did not populate the child `ExitCode` property after the process completed, so closure was recovered from persisted Locust evidence rather than rerunning the workload.

The Locust log explicitly recorded:

```text
--run-time limit reached, shutting down
Shutting down (exit code 0)
```

Accepted aggregate result:

| Metric | Result |
|---|---:|
| Requests | **7,956** |
| Failures | **0** |
| Failure rows | **0** |
| Exception rows | **0** |
| Median | **53 ms** |
| Average | **88.1981 ms** |
| p95 | **220 ms** |
| p99 | **550 ms** |
| RPS | **16.6745** |

Closure:

```text
LOCUST_RUNTIME_LIMIT_REACHED=True
LOCUST_EXIT_ZERO_OBSERVED=True
LOCUST_COMPLETION=PASS
REQUEST_CONTINUITY=PASS
```

No load rerun was required.

No failure reinjection was required.

## Core domain data-state verification

A deterministic SHA-256 fingerprint of benchmark-domain tables was captured before the official run.

Before:

```text
E3D97990A35B59BE2B8F0AA32A1D97D14F925C712AFF00EA5309161B07BF5291
```

The retained candidate database was later brought back up without load or failure reinjection solely to obtain the missing post-run closure fingerprint.

After:

```text
E3D97990A35B59BE2B8F0AA32A1D97D14F925C712AFF00EA5309161B07BF5291
```

Result:

```text
DATA_STATE_UNCHANGED=YES
CORE_DOMAIN_DATA_RECOVERY=PASS
```

Operational/audit logs were not part of the immutable core-domain fingerprint because load execution can legitimately create operational evidence.

## Restart stability

Before the official run, all five candidate restart counts were zero.

After the official run and during salvage closure:

```text
postgres restart delta = 0
redis restart delta    = 0
backend-1 restart delta = 0
backend-2 restart delta = 0
proxy restart delta     = 0

RESTART_DELTA_TOTAL=0
UNEXPECTED_RESTARTS=ZERO
```

The deliberate `docker stop` / `docker start` of backend-1 was the intended operator action and did not produce an unexpected Docker restart count.

## Final candidate health

During closure the retained candidate was brought up without load or reinjection.

Final health:

```text
CANDIDATE_FINAL_LIVE=200
CANDIDATE_FINAL_READY=200
CANDIDATE_BACKEND1_HEALTH=healthy
CANDIDATE_BACKEND2_HEALTH=healthy
CANDIDATE_POSTGRES_HEALTH=healthy
CANDIDATE_REDIS_HEALTH=healthy
CANDIDATE_FINAL_HEALTH=PASS
```

## Primary fleet restoration

After both the official Gate 10 run and the closure pass, the original primary fleet was restored.

Final primary state:

```text
clinic-system-postgres-1  running  restart_count=0
clinic-system-redis-1     running  restart_count=0
clinic-system-backend-1   running  restart_count=0
clinic-system-backend-2   running  restart_count=0
clinic-system-backend-3   running  restart_count=0
clinic-system-proxy-1     running  restart_count=0

PRIMARY_LIVE=200
PRIMARY_READY=200
PRIMARY_FLEET_RESTORED=PASS
```

## Evidence artifacts

Official V6 evidence directory:

```text
C:\Users\HP\AppData\Local\Temp\clinic-gate10-recovery-20261029T060152Z-44e3108
```

Key artifacts:

```text
domain-fingerprint-before.txt
domain-fingerprint-after-salvage.txt
gate10-9u-recovery-44e3108_stats.csv
gate10-9u-recovery-44e3108_stats_history.csv
gate10-9u-recovery-44e3108_failures.csv
gate10-9u-recovery-44e3108_exceptions.csv
locust.stderr.txt
proxy-during-failure-salvage.log
proxy-after-recovery-salvage.log
```

Tokens and credentials were not printed into the runbook.

## Gate 10 closure

The accepted Gate 10 evidence is the original V6 execution plus the read-only/persistence-based salvage closure required after the PowerShell child-process `ExitCode` property was blank.

The workload was not rerun.

The failure was not reinjected.

Final closure:

```text
OFFICIAL_V6_LOAD_RERUN=NO
OFFICIAL_V6_FAILURE_REINJECTION=NO
LOCUST_EXIT_ZERO=PASS
REQUEST_FAILURES=0
LOCUST_EXCEPTIONS=0
FAILOVER_DURING_LOAD=PASS
BACKEND_RECOVERY_SECONDS=28.55
BACKEND_RECOVERY=PASS
APPLICATION_RECOVERY=PASS
DATA_STATE_RECOVERY=PASS
UNEXPECTED_RESTARTS=ZERO
PROTECTED_WEB_ROUTES_STAGED=NO
GATE10_CONTROLLED_RECOVERY=GREEN
WAVE4_GATE10=GREEN
GATE10_V6_EVIDENCE_SALVAGED=PASS
```

## Final decision

**Wave 4 Gate 10 is GREEN and closed from the accepted V6 controlled failure-recovery execution and its evidence-preserving closure pass.**

The demonstrated claim is bounded to application-backend failover/recovery under the tested 9-user system-mixed workload and retained synthetic benchmark state.