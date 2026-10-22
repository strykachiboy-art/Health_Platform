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

Automatic scale-out and scale-in are **not verified**. The repository's
autoscaling controller/coordinator and Compose adapter are preview-only.
The agreed next step is to implement a controlled live actuator and automatic
reconciliation loop, then prove both scaling directions before Phase 2.

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

This proves manual retirement and recovery, not automatic scale-in.

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

GitHub source inspection found the autoscaling decision engine, API controller,
coordinator, and Compose scaling adapter. The controller/coordinator/adapter
are preview-only; the Compose adapter validates a dry-run instead of applying
a live scale operation.

No automatic controller was demonstrated adding or removing replicas in
response to workload.

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
- Automatic scale-out/scale-in: NOT VERIFIED.
- Production operation: NOT CLAIMED.

Next agreed step: implement a controlled live actuator and automatic
reconciliation loop; prove scale-out and safe scale-in with health, routing,
drain, database-capacity, failure-handling, and recovery evidence. Review
that evidence before proceeding to Phase 2.

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

- The report remains a local untracked file until reviewed.
- No files are staged automatically.
- No commit or push is authorized by this script.
- Existing modifications and untracked artifacts must be preserved.
- The migration must not be repeated for report preparation.
