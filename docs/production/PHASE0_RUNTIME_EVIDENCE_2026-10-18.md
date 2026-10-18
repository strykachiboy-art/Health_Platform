# Phase 0 Runtime Evidence Record — 2026-10-18

## Status and scope

- **Record type:** Supplemental local runtime evidence.
- **Runtime snapshot timestamp (host local):** 2026-10-18 12:54:56 +02:00.
- **Observed repository branch:** `feat/hie-endpoint-allowlist-enforcement`.
- **Source HEAD at the time of observation:** `ced0f45363d9b0a16a2faedb40f88ff0ba8654bd`.
**Compose project:** `clinic-system-phase9`.
**Phase 0 status:** PARTIAL / BLOCKED.

This record supplements `PHASE0_BASELINE_2026-10-18.md`. It does not replace that historical baseline or establish a production deployment.

All observations below refer to the local Docker environment. No real environment values were read for the runtime snapshot. Synthetic values were used for Compose model validation and the proxy-only recreation.

## 1. Source-control state

At snapshot time:

- Local branch and remote branch HEAD matched the source revision recorded above.
- Tracked worktree changes: none.
- Staged changes: none before preparation of this supplemental record.
- Untracked paths preserved:
  - `apply_hie_bounded_dns_patch.ps1`
  - `mobile/android/build/`
  - `phase19-slice3a-maintenance-trigger.ps1`

No application or deployment source files were changed during the read-only snapshot.

## 2. Compose configuration and Nginx verification

The following results were captured during the same operator session on 2026-10-18:

- Phase 6 Compose merged-model validation using synthetic values: PASS.
- Phase 9 Compose merged-model validation using synthetic values: PASS.
- Nginx `-t`: syntax OK; configuration test successful; process exit code 0.
- Proxy security regression after the loopback binding change: 9 passed in 0.28 seconds.
- Phase 9 proxy recreation: PASS using `docker compose up -d --no-deps --force-recreate proxy`.
- Temporary synthetic environment file: removed after use.

The standalone Nginx syntax check used read-only mounts and a network-isolated temporary container. It supplied a local placeholder mapping for the `backend` upstream name only so Nginx could parse the configuration without network access.

The `--no-deps` Compose operation explicitly targeted the proxy service only.

## 3. Proxy runtime smoke tests

### Host port bindings

The active proxy's Docker port bindings were inspected and matched the required loopback-only contract:

| Container port | Host address and port | Result |
|---|---|---|
| `80/tcp` | `127.0.0.1:19080` | PASS |
| `8443/tcp` | `127.0.0.1:19443` | PASS |

These bindings restrict host-published access to loopback rather than all host network interfaces.

### HTTP redirect

Request:

`http://localhost:19080/health/live`

Observed response:

- HTTP status: `308 Permanent Redirect`
- `Location`: `https://localhost:19443/health/live`

Result: PASS.

A request using the IP literal as the HTTP hostname previously received an empty reply. The configuration explicitly rejects hosts other than `localhost`; the repeat test with the configured hostname succeeded.

### HTTPS health routing

Request:

`https://localhost:19443/health/live`

Observed result: HTTP 200.

Result: PASS. The local request used `curl --insecure`, so certificate chain and trust validation were deliberately bypassed. This verifies local TLS connectivity and the health-route response, not client trust of the certificate.

## 4. Six-container runtime snapshot

Snapshot timestamp: `2026-10-18T12:54:56+02:00`.

| Container | State | Docker health | Restart count | Started at (UTC) | Exit code | OOM killed |
|---|---|---|---:|---|---:|---|
| `clinic-system-phase9-backend-1` | running | healthy | 0 | `2026-10-18T08:52:09.887120996Z` | 0 | no |
| `clinic-system-phase9-worker-1` | running | not configured | 0 | `2026-10-18T08:52:09.977543726Z` | 0 | no |
| `clinic-system-phase9-beat-1` | running | not configured | 0 | `2026-10-18T08:52:09.813884472Z` | 0 | no |
| `clinic-system-phase9-postgres-1` | running | healthy | 0 | `2026-10-18T08:52:09.952487018Z` | 0 | no |
| `clinic-system-phase9-redis-1` | running | healthy | 0 | `2026-10-18T08:52:09.71665804Z` | 0 | no |
| `clinic-system-phase9-proxy-1` | running | not configured | 0 | `2026-10-18T10:40:37.285432579Z` | 0 | no |

All six containers were running at capture time. Backend, PostgreSQL and Redis reported healthy. Worker, Beat and proxy do not have Docker health checks configured. Their lack of a Docker health status is not evidence of failure; separate runtime checks are needed for the services where required.

The restart count is the value for each current container object. The proxy had been explicitly recreated during this operator session, so its current restart count of zero must not be interpreted as proof that it was never recreated.

## 5. Point-in-time resource snapshot

Docker `stats --no-stream` returned the following values:

| Container | CPU | Memory used / limit | Memory % | PIDs |
|---|---:|---:|---:|---:|
| Backend | 35.69% | 170.5 MiB / 3.784 GiB | 4.40% | 2 |
| Worker | 0.32% | 443.9 MiB / 3.784 GiB | 11.46% | 5 |
| Beat | 0.00% | 157.7 MiB / 3.784 GiB | 4.07% | 1 |
| PostgreSQL | 0.01% | 49.21 MiB / 3.784 GiB | 1.27% | 12 |
| Redis | 7.44% | 20.59 MiB / 3.784 GiB | 0.53% | 6 |
| Proxy | 0.00% | 3.273 MiB / 3.784 GiB | 0.08% | 2 |

These measurements are a single point-in-time observation. They were not collected under a controlled, representative workload and must not be used as throughput, concurrency, saturation, or capacity claims.

## 6. Verification matrix

| Area | Status | Evidence and limitation |
|---|---|---|
| Phase 6 Compose interpolation/model | VERIFIED | Synthetic-value `config -q` passed |
| Phase 9 Compose interpolation/model | VERIFIED | Synthetic-value `config -q` passed |
| Nginx syntax | VERIFIED | `nginx -t` exit code 0 |
| Phase 9 proxy security tests | VERIFIED | 9 passed after the port binding change |
| HTTP redirect | PRODUCTION-LIKE EXECUTED | Local request returned 308 with the expected HTTPS Location |
| HTTPS backend health route | PRODUCTION-LIKE EXECUTED | Local TLS request returned HTTP 200; certificate trust was skipped |
| Proxy host binding | VERIFIED | Docker reports both host bindings on `127.0.0.1` |
| Six-container current state | VERIFIED | All running at the recorded snapshot |
| Worker and Beat health instrumentation | MISSING | Docker health checks are not configured |
| Representative performance baseline | MISSING | No controlled load, latency, throughput, or error-rate test in this record |
| Celery/Redis queue depth and task age | UNRESOLVED | The earlier baseline did not obtain these measurements |
| Exact artifact/build provenance | UNRESOLVED | This snapshot did not retain an image digest and build provenance record |
| Production deployment/operation | NOT ESTABLISHED | All runtime evidence is local, not evidence of actual production operation |

## 7. Phase 0 exit decision

Phase 0 remains **PARTIAL / BLOCKED**.

The proxy configuration and localhost routing defects discovered during the local inspection are now addressed and verified for this topology. The updated evidence closes those specific local configuration and smoke-test gaps, but it does not close Phase 0's representative-workload baseline requirement.

The next evidence gate should capture a controlled synthetic workload with a run ID, client concurrency, spawn rate, duration, request latency distribution, throughput, error rate, database connection/latency observations, Redis usage, and Celery queue depth/oldest task age. A single Docker stats sample must not be substituted for that workload evidence.

No claim of production readiness, production-operated status, or supported capacity is made by this record. The original Phase 0 baseline remains unchanged.
