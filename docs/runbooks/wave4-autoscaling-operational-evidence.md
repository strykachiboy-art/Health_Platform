# Wave 4 Autoscaling Operational Evidence

**Evidence date:** 2026-10-25  
**Repository:** `strykachiboy-art/Health_Platform`  
**Runtime branch:** `feat/hie-endpoint-allowlist-enforcement`  
**Controller source at runner launch:** [`3344e437da2a5862612d5741e9973a8ae955e21c`](https://github.com/strykachiboy-art/Health_Platform/commit/3344e437da2a5862612d5741e9973a8ae955e21c)  
**Candidate application source baseline:** `3f72a63ac05d709ae16b0c03900e30e1671196fc`  
**Rehearsal result:** `AUTOSCALER_2_TO_3_PASS`  
**Production operation:** NOT CLAIMED.

## 1. Purpose and status vocabulary

This ledger captures the route-aware autoscaling reconciliation correction and a controlled live scale-out from two to three backend replicas in the local candidate environment. It records enough environment identity, controller configuration, durable state, chronology, and independent verification to make a future reproduction more deliberate.

Keep these claims separate:

- **IMPLEMENTED:** code or procedure exists in source.
- **FOCUSED TESTS VERIFIED:** the named test execution passed.
- **LOCAL RUNTIME REHEARSAL VERIFIED:** a real local candidate completed the tested transition and independent inspection confirmed the expected fleet, routes, durable operation state, and traffic.
- **PRODUCTION OPERATED:** NOT established by this evidence.

The tests used synthetic unauthenticated requests. This document deliberately excludes credentials, resolved `.env` contents, patient data, database dumps, Redis dumps, and secret-bearing environment exports.

## 2. Source correction

Commit: [`3344e437da2a5862612d5741e9973a8ae955e21c`](https://github.com/strykachiboy-art/Health_Platform/commit/3344e437da2a5862612d5741e9973a8ae955e21c)  
Message: `fix autoscaling route-aware reconciliation`

Four-file source scope:

- `backend/app/core/autoscaling/controller_lease.py`
- `backend/app/core/autoscaling/reconciliation_loop.py`
- `backend/app/tests/core/autoscaling/test_controller_lease.py`
- `backend/app/tests/core/autoscaling/test_reconciliation_loop.py`

The lease-guarded actuator now reads the durable pending operation, verifies that the pending action and replica counts match the requested decision, and invokes the actuator's explicit `reconcile_pending_operation` method. Lease ownership is checked before and after reconciliation. Reconciliation does not repeat the scale mutation.

The reconciliation loop no longer treats target replica count and health alone as sufficient proof of success. The actuator must explicitly provide typed Boolean `may_acknowledge_decision` and `succeeded` results. Incomplete or uncertain results remain pending rather than being acknowledged solely from a replica snapshot.

### Source verification reported for the commit

- Python compilation: PASS.
- Focused autoscaling tests across `test_reconciliation_loop.py`, `test_controller_lease.py`, and `test_live_actuator.py`: **68 passed**.
- `git diff --check`: PASS.
- The source commit was pushed and its remote SHA was verified before runtime testing.

A prior Wave 1 evidence ledger records a 513-test autoscaling regression on 2026-10-22 at an earlier source checkpoint. That historical result is not being represented as a fresh 513-test run on commit `3344e437`.

## 3. Candidate environment identity

### Host and candidate paths

- Repository working directory: `C:\Users\HP\Documents\clinic-system`
- Candidate project root: `C:\Users\HP\AppData\Local\Temp\clinic-wave4-head-3f72a63`
- Compose project name: `w4head-3f72a63`
- Candidate configuration files present: `.env.phase6`, `compose.yaml`, `compose.phase6.yaml`, `compose.phase9.yaml`
- Host-side Nginx upstream file:
  `C:\Users\HP\AppData\Local\Temp\clinic-wave4-head-3f72a63\runtime\phase9\nginx-upstream\backend.conf`
- Runner log:
  `C:\Users\HP\AppData\Local\Temp\clinic-wave4-head-3f72a63\runtime\phase9\runner-wave4-3344e43-20261025-095125.log`
- Runner mode: foreground.
- PID observed during diagnostics: `10388`. This is a historical observation, not a permanent process identifier.
- The runner log contained `autoscaling preflight passed project=w4head-3f72a63 replicas=2 range=2..3 scale_in=disabled`.

### Image identity: do not conflate controller source and backend image

The application backend image was intentionally left on the candidate build while the host autoscaling controller ran from the corrected repository source.

- Candidate image tag: `clinic-system-candidate:3f72a63`
- Expected immutable image ID: `sha256:c0e35846273422571d82d6e3b3bdfb5a0a5217d0af7aaebe33b9a8050e81da03`
- Candidate image baseline commit: `3f72a63ac05d709ae16b0c03900e30e1671196fc`
- Host controller source at launch: `3344e437da2a5862612d5741e9973a8ae955e21c`

The candidate image was not rebuilt for this controller-only fix. All three final backend inspections matched the expected immutable image ID.

### Verified container names

- `w4head-3f72a63-backend-1`
- `w4head-3f72a63-backend-2`
- `w4head-3f72a63-backend-3`
- `w4head-3f72a63-proxy-1`
- `w4head-3f72a63-redis-1`

### Local endpoints used

- Proxy HTTPS endpoint: `https://localhost:55466`
- Redis URL: `redis://127.0.0.1:55464/0`
- Backend health endpoint, called from inside each backend container: `http://127.0.0.1:5000/health/live`

The local proxy used a local/self-signed certificate; the local `curl.exe` verification used `--insecure` for this test environment only. Re-check actual port bindings and certificate policy before reusing the environment. These values are not production endpoint settings.

## 4. Controller environment configuration

The foreground runner was launched with these controller environment variables:

| Variable | Value |
|---|---|
| `PHASE9_AUTOSCALING_ENABLED` | `true` |
| `PHASE9_AUTOSCALING_LIVE_ENABLED` | `true` |
| `PHASE9_AUTOSCALING_SCALE_IN_ENABLED` | `false` |
| `PHASE9_AUTOSCALING_PROJECT_NAME` | `w4head-3f72a63` |
| `PHASE9_AUTOSCALING_PROJECT_ROOT` | `C:\Users\HP\AppData\Local\Temp\clinic-wave4-head-3f72a63` |
| `PHASE9_AUTOSCALING_REDIS_URL` | `redis://127.0.0.1:55464/0` |
| `PHASE9_AUTOSCALING_LEASE_KEY` | `wave4-head-3f72a63:autoscaling:controller:lease` |
| `PHASE9_AUTOSCALING_MIN_REPLICAS` | `2` |
| `PHASE9_AUTOSCALING_MAX_REPLICAS` | `3` |
| `PHASE9_AUTOSCALING_TARGET_REQUESTS_PER_SECOND_PER_REPLICA` | `0.5` |
| `PHASE9_AUTOSCALING_SCALE_OUT_LOAD_RATIO` | `0.85` |
| `PHASE9_AUTOSCALING_SCALE_IN_LOAD_RATIO` | `0.30` |
| `PHASE9_AUTOSCALING_SCALE_OUT_WINDOW_SECONDS` | `20` |
| `PHASE9_AUTOSCALING_SCALE_IN_WINDOW_SECONDS` | `20` |
| `PHASE9_AUTOSCALING_COOLDOWN_SECONDS` | `0` |
| `PHASE9_AUTOSCALING_MAX_METRICS_AGE_SECONDS` | `30` |
| `PHASE9_AUTOSCALING_MAX_OBSERVATION_GAP_SECONDS` | `30` |
| `PHASE9_AUTOSCALING_LOOP_INTERVAL_SECONDS` | `2` |
| `PHASE9_AUTOSCALING_METRICS_WINDOW_SECONDS` | `20` |
| `PHASE9_AUTOSCALING_METRICS_MAX_SAMPLE_AGE_SECONDS` | `20` |
| `PHASE9_AUTOSCALING_LEASE_TTL_SECONDS` | `45` |
| `PHASE9_AUTOSCALING_LEASE_RENEW_INTERVAL_SECONDS` | `10` |
| `PHASE9_AUTOSCALING_LOG_LEVEL` | `INFO` |

The runner's Python import path was configured from the repository root:

```powershell
$env:PYTHONPATH="$repo\testing;$repo\backend"
```

## 5. Durable operation and routing contract

Exact Redis keys in candidate Redis database 0:

| Purpose | Key |
|---|---|
| Controller lease | `wave4-head-3f72a63:autoscaling:controller:lease` |
| Pending operation | `wave4-head-3f72a63:autoscaling:controller:lease:pending-operation` |
| Last completed operation | `wave4-head-3f72a63:autoscaling:controller:lease:last-completed-operation` |
| Last successful scale timestamp | `wave4-head-3f72a63:autoscaling:controller:lease:last-successful-scale-at` |

The expected three-backend upstream entries were:

```nginx
server w4head-3f72a63-backend-1:5000 resolve max_fails=2 fail_timeout=10s;
server w4head-3f72a63-backend-2:5000 resolve max_fails=2 fail_timeout=10s;
server w4head-3f72a63-backend-3:5000 resolve max_fails=2 fail_timeout=10s;
```

Both the host upstream file and effective Nginx configuration were checked. A container count, health snapshot, route file, or completion marker alone is not sufficient proof. The final pass required these states to agree and proxy logs to show backend 3 serving successful traffic.

## 6. Chronology and failure analysis

### Baseline reset

A controlled baseline reset moved the candidate fleet from three replicas to two using the live actuator. Scale-in was enabled only for that one-off reset; the normal runner stayed configured with scale-in disabled.

The reset was verified with two healthy backends and two configured routes, successful proxy health checks, and no remaining lease or pending operation at its completion.

The reset's durable completion record was:

- Timestamp: `2026-10-25T07:34:29.925240+00:00`
- Action: `scale_in`
- Current replicas: `3`
- Target replicas: `2`
- Reason: `controlled_baseline_reset_for_scale_out_rehearsal`

### Earlier failed rehearsal exposed the reconciliation defect

An earlier load run created backend 3, but it did not become a healthy, serving member of the effective upstream. The old reconciliation behavior could acknowledge an uncertain operation using replica/health state without proving that Nginx routed to the intended fleet.

That run was not accepted as a successful scale-out. Commit `3344e437da2a5862612d5741e9973a8ae955e21c` changed reconciliation to require explicit actuator confirmation of the pending operation, including the route-aware result.

### Corrected scale-out attempt

- Host controller source: `3344e437da2a5862612d5741e9973a8ae955e21c`.
- Preflight result: passed with two replicas, allowed range `2..3`, and scale-in disabled.
- Runner log: `runner-wave4-3344e43-20261025-095125.log`.
- Synthetic endpoint: `GET /api/v1/auth/clinic-context`.
- Expected unauthenticated response: HTTP `401`.
- Load driver configuration: nominal `4.5 requests/second` for `90 seconds`.
- Metrics window: `20 seconds`.
- Requests completed: **145**.
- Statuses: **145 × HTTP 401**.

The nominal request target was not achieved. Observed metric readings during the run were approximately `1.08–2.47 requests/second`. This was a scale-out trigger and recovery rehearsal, **not** a capacity benchmark.

During the attempt, backend 3 appeared but remained unhealthy. The route file continued to contain two backends, and the durable `scale_out` operation remained pending. No success was claimed from that intermediate state.

## 7. Resource pressure and health-check investigation

The Docker health check observed on all three backend containers was:

```text
Command: python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health/live')"
Interval: 10 seconds
Timeout: 5 seconds
Retries: 10
```

The recorded diagnostic results were:

- Host memory total: approximately `7.92 GB`.
- Free host memory: approximately `0.92 GB`.
- Timed `docker exec ... python -c` probes: approximately `2.213–7.655 seconds` end-to-end.
- `curl` to the same in-container health endpoint: HTTP 200, approximately `0.066–0.781 seconds` for the recorded samples.
- One resource snapshot showed backend 1 CPU usage around `70%`.
- Docker health history showed repeated `Health check exceeded timeout (5s)` records, including intermittent failures on backend 1.
- Later inspections showed all three backends `running|healthy`.

**Root-cause qualification:** limited host resources and health-check execution/scheduling overhead are credible contributors, but the evidence does not prove a single root cause. The `docker exec` measurements include Docker-exec overhead and are not a measurement of Python endpoint latency alone. The health-check timeout was not changed during this rehearsal.

## 8. Final reconciliation and independent pass evidence

The original durable pending operation was:

- Operation ID: `p3vwd1z0UMSmtYQBjOspPZwV`
- Action: `scale_out`
- Current replicas: `2`
- Target replicas: `3`
- Phase: `scale_command_started`
- Created at: `2026-10-25T07:57:09.016902+00:00`
- Scale command started at: `2026-10-25T07:57:23.681590+00:00`

Final state inspection returned:

| Verification | Result |
|---|---|
| Backend containers | `3` running |
| Backend health | `3/3` healthy |
| Backend image identity | All matched the expected immutable candidate image ID |
| Upstream file routes | Exactly 3 |
| Effective Nginx routes | Exactly 3, matching the upstream file |
| Last completed operation | `succeeded=true` |
| Last completed action | `scale_out` |
| Completion replica transition | `2` → `3` |
| Pending-operation key | Absent |
| Controller lease | Present while the runner was active |
| Final proxy health probes | `6/6` returned HTTP 200 |
| Backend 3 successful proxied requests | `2` HTTP 200s in proxy logs |

Last completed operation:

- Completion time: `2026-10-25T08:06:26.794788+00:00`
- Action: `scale_out`
- Current: `2`
- Target: `3`
- Reason: `scale_out_recommended`

Backend 3 IP observed by the verifier: `172.23.0.7`.

Successful proxy log entries for backend 3:

```text
2026-10-25T08:14:19+00:00 upstream=172.23.0.7:5000 status=200 request_time=0.072 upstream_response_time=0.072
2026-10-25T08:14:21+00:00 upstream=172.23.0.7:5000 status=200 request_time=0.053 upstream_response_time=0.053
```

The six subsequent proxy health checks were HTTP 200; elapsed times were approximately `0.285–0.364 seconds`.

Final verifier output:

```text
ALL_BACKENDS_HEALTHY_AND_EXPECTED_IMAGE=True
THREE_UPSTREAM_ROUTES_CONFIRMED=True
SCALE_OUT_COMPLETION_AND_REDIS_STATE_CONFIRMED=True
PROXY_HEALTH_200_COUNT=6/6
BACKEND_3_SERVED_SUCCESSFUL_PROXY_REQUEST=True
OVERALL=AUTOSCALER_2_TO_3_PASS
```

The final verifier itself made no file changes, container restarts, route changes, Redis-key modifications, or sustained load. The separate 90-second synthetic driver earlier in this chronology did generate load.

## 9. What this pass does and does not establish

### Established

- The corrected controller reconciled the uncertain live scale-out without resending the scale mutation.
- Durable completion records matched the exact `2 → 3` operation.
- The pending-operation key was absent after successful reconciliation.
- All three backends were healthy and used the expected immutable candidate image.
- The route file and effective Nginx configuration both contained the three intended backends.
- Backend 3 actually served successful traffic through the proxy.
- Six subsequent low-volume proxy health probes returned HTTP 200.

### Not established

- Production deployment or production operation.
- Scale-in using the corrected controller; it was intentionally disabled for the normal runner.
- Sustained achievement of `4.5 requests/second`, maximum capacity, or production performance.
- Definitive root cause for intermittent five-second health-check timeouts.
- Overall platform health or worker/Beat recovery. Worker and Beat containers had been observed exited earlier and require separate inspection.
- Confirmed shutdown of the foreground autoscaling runner after the pass. The last state inspection still showed the lease present. Re-query process and lease state before starting another runner.

Status: **LOCAL RUNTIME REHEARSAL VERIFIED; PRODUCTION OPERATION NOT CLAIMED.**

## 10. Safe re-entry checklist

1. Check branch, `HEAD`, remote SHA, staged changes, and relevant worktree scope before running commands. The controller source used in this rehearsal was `3344e437`; a later documentation commit changes branch `HEAD`, not the already running process's imported code.
2. Find any existing runner before starting another. PID `10388` was observed in this session, but a PID is only a historical observation.
3. Verify the candidate project root, Compose project, port bindings, backend image IDs, Redis URL, and upstream path against the current environment.
4. Read the exact lease, pending-operation, and last-completed Redis keys before acting. A lease may be expected while the controller owns it.
5. Never delete a pending-operation key manually. It can represent an unknown external outcome. Let route-aware reconciliation establish a known outcome or investigate the unresolved state.
6. Compare both the host upstream file and effective Nginx configuration.
7. Probe each backend directly, then perform low-volume proxy checks and confirm proxy logs show successful requests reaching backend 3.
8. Check host free memory before running another load test. This host had about `0.92 GB` free during diagnostics and did not reach the driver's nominal request target.
9. Keep normal runner scale-in disabled unless a separately controlled and approved test requires it.
10. Stop the foreground runner deliberately from its owning terminal, then re-check process count, lease, pending key, backend health, and routes. Do not infer successful shutdown from terminal closure alone.

### Read-only inspection commands

Re-check the source lock and candidate identity before using these values:

```powershell
docker exec w4head-3f72a63-redis-1 redis-cli -n 0 EXISTS wave4-head-3f72a63:autoscaling:controller:lease
docker exec w4head-3f72a63-redis-1 redis-cli -n 0 EXISTS wave4-head-3f72a63:autoscaling:controller:lease:pending-operation
docker exec w4head-3f72a63-redis-1 redis-cli -n 0 GET wave4-head-3f72a63:autoscaling:controller:lease:last-completed-operation
docker exec w4head-3f72a63-proxy-1 nginx -T
docker inspect w4head-3f72a63-backend-1 w4head-3f72a63-backend-2 w4head-3f72a63-backend-3
```

In PowerShell, capture Nginx's stderr without treating its informational syntax messages as automatic proof of a failed command; check the actual exit code.

## 11. Evidence handling and closure discipline

- Keep raw runner logs and runtime artifacts in their intended access-controlled location unless reviewed for safe publication.
- Never commit `.env.phase6`, credentials, credential-bearing database URLs, backup payloads, Redis dumps, or resolved environment exports.
- Keep IMPLEMENTED, FOCUSED TESTS VERIFIED, LOCAL RUNTIME REHEARSAL VERIFIED, and PRODUCTION OPERATED as separate claims.
- This document records the controlled local scale-out rehearsal only. It does not close all Wave 4 lifecycle, capacity, disaster-recovery, or production-release gates.