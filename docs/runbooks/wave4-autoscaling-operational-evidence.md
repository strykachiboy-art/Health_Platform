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
