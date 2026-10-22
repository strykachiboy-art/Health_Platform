# Wave 1 — Infrastructure Verification Evidence

**Evidence date:** 2026-10-21
**Repository:** `strykachiboy-art/clinic-system-pro`
**Branch:** `feat/hie-endpoint-allowlist-enforcement`
**HEAD checkpoint:** `1e15f2154126933c7dd9e71224ad4761a8b89a12`
**Environment:** Local Docker multi-container execution on a development host.
**Status:** Local runtime verification evidence. Production operation is not claimed.

## 1. Executive summary

Wave 1 has runtime evidence for multi-instance load balancing, safe manual
retirement and recovery, shared Redis behaviour, distributed rate limiting,
authenticated cross-instance Socket.IO delivery, JWT revocation across
replicas, backend ingress isolation, and the current API-tier database
connection budget.

At the original evidence date (2026-10-21), automatic scale-out and scale-in
were not verified. That statement describes the earlier state of the inspected
controller/coordinator/Compose adapter and is superseded for local live-scaling
status by the 2026-10-22 evidence addendum in Section 13. Production operation
is still not claimed.

## 2. Backup and migration

Backup:
- Path: `C:\Users\HP\ClinicSystemBackups\clinic-system-phase9-before-wave1-20261021-165419.dump`
- Size: 634883 bytes
- SHA-256: `a659f777b2a85d62f65310856c166371eae245baf21cbdd6d44b3dac5d9f64af`
- Archive validation passed in the recorded local execution.
- Backup was outside the repository; the temporary dump container was removed.

Migration:
- Database: `clinic_system_phase9`
- Starting revision observed: `f3b9a84c2d71`
- Currency migration: `f19b3a7d6c42 -> d2c7f4a91b36`
- Merge revision/head: `2fcd41e8c6ed`
- Post-migration revision: `2fcd41e8c6ed`, the sole head.
- `invoices.currency` existed and was nullable (`YES`).
- Migration was not repeated during these Wave 1 probes.

Application image:
- Tag: `clinic-system-phase9:wave1-local-20261021-r1`
- Image ID: `sha256:fb3c3ce062966c1e894c6fad1106a3ad6ffa66b03253a0161e38e291b6dcc203`

## 3. Inbound routing and lifecycle

- Nginx dynamic upstream resolution and upstream-address logging were inspected.
- Forty HTTPS `/health/live` requests returned HTTP 200 and logs showed
  requests routed to multiple backend addresses.
- Subsequent proxy traffic reached all three replica addresses.
- Health, readiness, and the authentication-protected dashboard API route
  returned expected statuses during the checks.

Safe manual retirement and recovery:
- Authenticated drain request returned HTTP 200.
- Drain state reached zero active HTTP requests and zero active sockets.
- Readiness became HTTP 503 while draining.
- Normal application requests to the draining instance returned HTTP 503
  with `Retry-After: 1`.
- Proxy requests remained successful during drain.
- With the drained instance stopped, 60 proxy status requests produced zero
  failures.
- The same container was restarted and returned to `running|healthy`.
- After recovery, another 60 proxy status requests produced zero failures.
- Backend 3 was restarted, not removed. The legacy Phase 9 proxy was not stopped.

Result: `SAFE_RETIREMENT_AND_RECOVERY=PASS`

This section establishes manual retirement and recovery. Automatic scale-in
was separately exercised through the live controller on 2026-10-22; see
Section 13.

## 4. Shared Redis and distributed login rate limiting

All three active API replicas reported:
- Redis host `redis`, port `6379`, database `0`.
- `limits.storage.redis.RedisStorage` as rate-limit storage.
- `socketio.redis_manager.RedisManager` as the Socket.IO manager.
- `AUTH_LOGIN_RATE_LIMIT='5 per minute'`.

Cross-instance Redis:
- Backend 1 wrote and read back a unique marker.
- Backend 2 read the same value.
- The explicit cleanup paste failed to execute as a PowerShell block;
  the marker had a 60-second TTL and was expected to expire.
- Explicit deletion of that marker was not independently confirmed.

Result: `CROSS_INSTANCE_REDIS_READ=PASS`
Cleanup qualification: TTL fallback, not verified explicit deletion.

Distributed login rate limiting:
- Six invalid login requests were split across backend 1 and backend 2.
- Observed statuses: `400,400,400,400,400,429`.
- All six request metrics reported zero database queries.
- Both tested replicas remained healthy.

Result: `DISTRIBUTED_RATE_LIMIT_PASS=True`

## 5. Live cross-instance Socket.IO delivery

An isolated synthetic clinic and user were created for the probe; no real
account or clinical record was used.

- Both backend containers were `running|healthy`.
- Both were configured with the Redis-backed Socket.IO manager.
- Both passed health and Engine.IO polling diagnostics.
- An authenticated client connected to backend 1 and a second authenticated
  client connected to backend 2.
- Both received the same unique test event published to the authenticated
  user's room through the shared Redis-backed manager.
- Probe ID, clinic ID, and user ID in each event were verified.
- Temporary clinic and user cleanup passed.
- The output confirmed no temporary probe rows remained.

Result: `LIVE_CROSS_INSTANCE_SOCKETIO=PASS`

This proves event delivery in the tested local topology. It does not prove
production outage recovery, reconnect behaviour during deployment, or
delivery guarantees during Redis failure.

## 6. Cross-instance JWT revocation

Using a disposable synthetic user and clinic:
- The token worked through backend 1 before revocation: HTTP 200.
- The token worked through backend 2 before revocation: HTTP 200.
- Backend 1 wrote the revocation marker to shared Redis.
- Backend 2 rejected the revoked token: HTTP 401.
- Temporary user, clinic, and Redis marker cleanup all passed.

Result: `CROSS_INSTANCE_JWT_REVOCATION=PASS`

Security finding: token creation and decoding emitted an
`InsecureKeyLengthWarning`; the HMAC-SHA256 signing key was 24 bytes, below
the library's recommended minimum of 32 bytes. The key was not printed.
Coordinate safe key remediation/rotation across all replicas, assess token
compatibility, and repeat authentication/revocation validation afterward.
Do not use the warning as evidence that the key mismatch caused Socket.IO
failures; both replicas' fingerprints matched.

## 7. Backend ingress isolation

All three active backend containers reported:
- Docker host port bindings: `{}`.
- Docker network: `clinic-system_default`.
- Health state: `running|healthy`.

The active proxy `clinic-system-proxy-1` had:
- `127.0.0.1:19180 -> container port 80`
- `127.0.0.1:19444 -> container port 8443`

The proxy and all three backends shared `clinic-system_default`.

Results:
- `BACKEND_DIRECT_HOST_INGRESS=PASS`
- `PROXY_TO_BACKEND_NETWORKING=PASS`
- `INGRESS_ISOLATION=PASS`

This is evidence for the current local host bindings, not a public production
ingress deployment.

## 8. Database connection capacity

### Active API stack

All three active API containers connected to:
- PostgreSQL server: `172.18.0.3:5432`
- Database: `clinic_system_phase9`
- `max_connections`: 100
- Superuser-reserved connections: 3
- Additional reserved connections: 0
- Ordinary connection capacity: 97

Observed snapshot:
- Client connections used: 4
- Active client connections: 1
- Idle client connections: 3

Each active API process reported SQLAlchemy `QueuePool`:
- Pool size: 3
- Max overflow: 2
- Timeout: 5 seconds
- Recycle: 1800 seconds
- Pool ceiling: 5 per process

For three API replicas:
- API pool ceiling: `3 x 5 = 15`
- Capacity remaining after accounting for the API pool ceiling alone:
  `97 - 15 = 82`

The current API-tier pool ceiling fits within the observed ordinary PostgreSQL
capacity. This does not yet constitute a complete platform connection budget
or establish a safe maximum autoscaled replica count.

### Separate legacy Phase 9 stack

The runtime inventory also showed legacy `clinic-system-phase9-*` containers.
Those containers connected to PostgreSQL at `172.20.0.5:5432`, also using
the database name `clinic_system_phase9`.

The server address differs from the active stack's `172.18.0.3`. These are
different PostgreSQL servers, so their connection pools must not be combined
in one server connection budget.

The legacy stack contained two API replicas, one Celery worker, and Beat.
Their inspected pools reported size 5, max overflow 10, and 30-second timeout.
The worker's effective concurrency and connection multiplication per child
were not proven by this diagnostic.

### Active worker and Beat topology

No `clinic-system-worker-*` or `clinic-system-beat-*` container was present
in the observed active `clinic-system` container inventory. The legacy worker
and Beat use their own Docker network and separate Redis/PostgreSQL services.
Their presence must not be treated as evidence that they consume jobs from
the active stack's separate Redis.

Carryover: verify and restore the intended active worker/Beat topology in
the background-processing work, then complete the combined pool/concurrency
budget. Keep the active API replica maximum bounded by an explicit
capacity policy before enabling live automatic scaling.

## 9. Known open findings

### Dashboard route/template

The `/` handler attempted to render `dashboard/index.html`, which was absent
from the checked template paths. The root request returned HTTP 500 with
`TemplateNotFound: dashboard/index.html`.

This remains an application defect separate from the successful health/API
routing checks. It must be fixed or explicitly tracked; don't hide it in the
infrastructure report.

### JWT signing key length

The 24-byte HMAC key warning is a security finding. Coordinate a longer key
across replicas, assess token rotation/compatibility, and verify auth and
revocation afterward. Never print or commit the key.

### Background processing and database budget

The active stack's worker/Beat topology was not present in the observed
container inventory. Full platform and autoscaling-maximum capacity remain
open until the intended background processes and concurrency are verified.

## 10. Autoscaling status

The original 2026-10-21 source inspection found the autoscaling decision
engine, API controller, coordinator, and Compose adapter in a preview-only
state; the inspected Compose adapter validated a dry-run instead of applying
a live operation. That assessment predates the subsequent local live runner,
actuator, and Nginx routing work recorded in Section 13. Read this as the
historical state, not as the current local implementation status.

At the time of the original report on 2026-10-21, no automatic controller
had been demonstrated adding or removing replicas in response to workload.
The subsequent controller-driven live transitions are recorded in Section 13.

- Load balancing: VERIFIED in local runtime.
- Manual drain and recovery: VERIFIED in local runtime.
- Shared Redis marker read across replicas: VERIFIED; explicit marker deletion
  was not independently confirmed, with TTL expiry as fallback.
- Distributed login rate limiting: VERIFIED.
- Cross-instance Socket.IO delivery: VERIFIED in local runtime.
- Cross-instance JWT revocation: VERIFIED in local runtime.
- Backend ingress isolation: VERIFIED in local runtime.
- Current API-tier connection pool ceiling: VERIFIED.
- Full platform/future maximum connection budget: OPEN.
- Automatic scale-out/scale-in: LIVE-VERIFIED in local runtime on 2026-10-22; see Section 13. The precise 300-second scale-in window semantics remain not independently verified by that run.
- Production operation: NOT CLAIMED.

Historical next step at the time of the original report: implement a
controlled live actuator and automatic reconciliation loop, then prove
scale-out and safe scale-in. The local scale-out/in and routing results are
now documented in Section 13. Full platform and future maximum connection
capacity remain open; production operation is not claimed.

## 11. Source references

- [Elite Platform Hardening Master Plan](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/docs/production/ELITE_PLATFORM_HARDENING_MASTER_PLAN.md)
- [Application configuration](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/config.py)
- [Socket.IO handler](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/modules/chat/realtime/chat_socket.py)
- [JWT token service](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/core/auth/user/services/token_service.py)
- [Autoscaling API controller](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/core/autoscaling/api_controller.py)
- [Autoscaling coordinator](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/core/autoscaling/coordinator.py)
- [Compose scaling adapter](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/backend/app/core/autoscaling/compose_adapter.py)
- [Compose topology](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/compose.yaml)
- [Phase 9 Nginx configuration](https://github.com/strykachiboy-art/clinic-system-pro/blob/feat/hie-endpoint-allowlist-enforcement/deployment/nginx/nginx.phase9.conf)

## 12. Git handling

This report documents local runtime evidence and source references. It does
not assert production deployment.

- This report has been updated locally with the 2026-10-22 autoscaling evidence addendum.
- No files are staged automatically.
- No commit or push is authorized by this script.
- Existing modifications and untracked artifacts must be preserved.
- The migration must not be repeated for report preparation.

## 13. Wave 1 autoscaling live-verification addendum (2026-10-22)

This addendum supersedes the original autoscaling status statements in
Sections 1 and 10 for the local live execution described below. It does not
convert local verification into production operation.

### Environment and safety controls

- Environment: local Docker Compose development host.
- Application image: `clinic-system-phase9:wave1-local-20261021-r1`.
- Image ID: `sha256:fb3c3ce062966c1e894c6fad1106a3ad6ffa66b03253a0161e38e291b6dcc203`.
- The host-run controller used the live runner, Redis coordination lease,
  Compose scaling actuator, backend drain support, and Nginx runtime upstream
  routing adapter.
- Live scale-in was explicitly enabled only after live route withdrawal and
  restoration had been verified.
- For the scale-in closure test, the minimum replica count was temporarily
  set to three and the maximum to four. The controller was therefore not
  allowed to scale the fleet below three replicas during that test.

### Controller-driven scale-out and four-replica distribution

- The controller successfully scaled the backend fleet from three replicas
  to four. The fourth backend was created through the live scale-out path.
- All four backend containers were subsequently verified healthy and using
  the expected immutable application image.
- A follow-up traffic probe submitted 1,020 requests over 30 seconds at the
  configured 34 requests per second.
- All 1,020 responses were the expected HTTP `401`; no request errors were
  reported.
- Per-backend request-counter deltas were exactly 255 for each of backends
  1, 2, 3, and 4.

Result: `FOUR_REPLICA_TRAFFIC_VERIFIED=True`.

### Live Nginx route withdrawal and restoration

- The routing adapter withdrew backend 4 from Nginx's active upstream
  configuration and confirmed the resulting route set.
- During the withdrawal probe, 400 requests completed, all with HTTP `401`
  and no request errors.
- Per-backend request-counter deltas during that probe were: backend 1 = 134,
  backend 2 = 133, backend 3 = 133, and withdrawn backend 4 = 0.
- Backend 4 received no requests while withdrawn; all three surviving
  backends continued to receive traffic.
- The adapter restored all four routes and confirmed the restored configuration.
  The subsequent HTTPS preflight returned the expected status.

Result: `LIVE_WITHDRAW_RESTORE_TEST=PASS`.

### Controller-driven scale-in from four replicas to three

- Before the low-load driver began, the driver verified that the controller
  Redis lease was held and that the baseline HTTPS request returned `401`.
- The controller was running with live scale-in enabled, the verified-route
  gate enabled, and the temporary minimum replica count fixed at three.
- The low-load driver was configured for 1.0 request per second, with a
  maximum observation duration of 390 seconds.
- The driver submitted 60 requests; every response was HTTP `401`, with no
  request errors. The transition to three replicas was observed at
  `elapsed_seconds=124`.
- Final running backend names were exactly `clinic-system-backend-1`,
  `clinic-system-backend-2`, and `clinic-system-backend-3`.
- All three surviving backends were healthy, retained their identities, and
  matched the expected application image ID.
- Backend 4 was absent from the container inventory. Both the upstream file
  and expanded active Nginx configuration contained exactly backends 1, 2,
  and 3.
- Non-backend container identities were preserved.
- After the controller was stopped, Redis reported
  `EXISTS clinic-system:autoscaling:controller:lease = 0`, confirming that
  the controller lease was released.
- A final request using `https://localhost:19443/api/v1/patients` returned
  HTTP `401`. The hostname is intentional: the Nginx configuration accepts
  `localhost`, and the IP-address probe is not a valid substitute for that
  host/SNI check.

Result: `LIVE_SCALE_IN_VERIFICATION=PASS`.

**Scale-in window timing note:** the controller was configured with a
300-second scale-in window, but the fleet transition was observed 124 seconds
after the low-load driver started. This run confirms that the live controller
performed the four-to-three transition under low-load test conditions. It
does not, by itself, prove that the implementation required a fresh,
uninterrupted 300-second low-load interval. If that exact duration is a hard
acceptance requirement, the controller's window semantics should be inspected
and independently verified before claiming that subrequirement is closed.

### Regression and final state

- Lifecycle contract: 2 passed.
- Complete `backend/app/tests/core/autoscaling` regression: 513 passed,
  0 failed, in 5.62 seconds.
- `git diff --check` returned success. Git emitted line-ending conversion
  warnings for existing modified Python files; no whitespace-check failure
  was reported.
- Nginx configuration test returned exit code 0.
- The expanded active Nginx configuration matched the runtime upstream file.
- The controller lease was released, backend 4 was removed, and the remaining
  three-replica fleet remained healthy.

### Closure status

- Controller-driven scale-out: **LIVE-VERIFIED in this local environment**.
- Controller-driven scale-in: **LIVE-VERIFIED in this local environment**.
- Four-replica traffic distribution: **LIVE-VERIFIED**.
- Route withdrawal and restoration: **LIVE-VERIFIED**.
- Autoscaling test regression: **513 PASSED**.
- Exact 300-second scale-in window enforcement: **NOT INDEPENDENTLY VERIFIED
  by this run**, as described in the timing note above.
- Full platform/future maximum connection budget: **OPEN**.
- Production operation: **NOT CLAIMED**.

No production deployment or production operation is asserted by these results.
