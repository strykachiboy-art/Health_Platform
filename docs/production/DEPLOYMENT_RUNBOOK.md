# Deployment Runbook

## Purpose

This document defines the production deployment contract for Clinic
System Pro v5.

It describes the required deployment behavior without claiming that
production deployment has already occurred.

## Application

The production WSGI entry point is:

`backend/wsgi.py`

`backend/wsgi.py` passes `FLASK_ENV` to the application factory and uses
`production` when that environment variable is absent.

Production configuration fails closed when required production settings
are missing.

## Background Workers

Celery uses:

`backend/celery_worker.py`

The worker requires the production environment configuration when deployed
for production operation.

The worker uses the configured Redis broker and result backend and runs
tasks inside the Flask application context.

## Redis

Redis provides application infrastructure including:

- token revocation
- rate-limit storage
- Celery broker/result transport
- Socket.IO message queue

Redis availability must therefore be verified before accepting production
traffic.

## PostgreSQL

PostgreSQL is the production application datastore and authoritative
source of truth.

Production startup requires a PostgreSQL database URL.

Migration readiness must be verified before release.

## Socket.IO

Flask-SocketIO is part of the backend runtime.

Production deployment must preserve the configured Redis message queue and
same-origin/CORS security policy.

Socket.IO connectivity must be validated in the production-like
environment. Phase 6 verified Socket.IO handler registration under `/chat`
and completed an HTTPS/WebSocket/Socket.IO end-to-end connection test.

## Network and TLS

TLS termination and reverse-proxy behavior are deployment-environment
responsibilities.

The approved TLS procedure is documented in:

`docs/security/SECURITY_OPERATIONS.md`

Forwarded client information is trusted only when explicitly enabled through `TRUSTED_PROXY_HOPS`. The default is `0`. Set it to `1` only when a single trusted reverse proxy is the only client path to the backend and that proxy overwrites `X-Forwarded-For` with the connecting client address and sets `X-Forwarded-Proto`. The Phase 6 overlay uses this one-hop setting while removing the backend host-port publication. Do not enable proxy trust in a topology that permits untrusted direct backend access.

Production deployment must verify:

- HTTPS
- certificate validity
- hostname coverage
- certificate chain
- reverse-proxy/application connectivity
- health endpoints
- Socket.IO connectivity where applicable

## Compose configuration and environment inputs

The Phase 6 overlay requires explicit `PHASE6_*` interpolation variables.
These names are a separate deployment contract from the root `.env` file.

Create a local `.env.phase6` from `.env.phase6.example` when one does not
already exist. Fill every required blank with the approved environment
values. The `.env.phase6` file is ignored by Git and must not be committed.
If a local file already exists, reconcile its variable names manually;
do not overwrite existing credentials.

Validate the Phase 6 Compose model without printing the rendered
configuration:

```powershell
docker compose --env-file .env.phase6 -f compose.yaml -f compose.phase6.yaml config -q
```

For the local Phase 9 overlay, validate the complete merged model:

```powershell
docker compose --env-file .env.phase6 -f compose.yaml -f compose.phase6.yaml -f compose.phase9.yaml config -q
```

The Phase 9 overlay uses localhost ports 19080 and 19443 and mounts
`deployment/nginx/nginx.phase9.conf`. The overlay and the corresponding
Nginx configuration are versioned together.

`config -q` validates interpolation and the merged Compose model only.
It does not start or recreate containers, verify the application image,
validate actual secret formats, run migrations, or establish production
readiness. Do not print a rendered `docker compose config` output into
shared logs because it can contain resolved environment values.

## Startup

Before accepting traffic:

1. Load the approved production configuration.
2. Verify required secrets are available through approved secret
   management.
3. Start the application runtime.
4. Start required background workers.
5. Verify database connectivity.
6. Verify Redis connectivity.
7. Verify readiness.
8. Verify observability.
9. Execute deployment smoke tests.

## Shutdown

Production shutdown must be graceful.

The deployment environment must provide a controlled mechanism for:

- stopping new traffic
- allowing or terminating in-flight requests according to the approved
  policy
- stopping background workers safely
- allowing queued work to remain recoverable
- releasing application resources

Actual shutdown behavior must be verified in the production-like
environment.

## Deployment Order

A controlled deployment should establish:

1. approved release artifact
2. approved configuration and secrets
3. infrastructure/dependency availability
4. database migration state
5. application runtime
6. Celery workers
7. Socket.IO connectivity
8. health/readiness
9. smoke tests
10. traffic acceptance

## Rollback

Use:

`docs/production/ROLLBACK_PROCEDURE.md`

Rollback must be based on an identifiable known-good release and must
consider database compatibility.

## Evidence

Record:

- release identifier
- source revision
- artifact identity
- migration revision
- deployment timestamp
- environment
- configuration verification
- health/readiness results
- smoke-test results
- rollback status if applicable

Never record secret values.

## Phase 6 Executed Operational Validation

The Phase 6 production-like backend environment executed and passed the
following deployment-path validations:

- immutable backend/worker/Beat image alignment
- Linux containerized application serving
- HTTPS reverse proxy and TLS validation
- HTTPS health and readiness verification
- Socket.IO handler registration under `/chat`
- end-to-end HTTPS/WebSocket/Socket.IO connectivity
- graceful backend restart
- graceful worker restart
- graceful Beat restart
- Celery shutdown under a 10,000-task backlog

Phase 6 evidence is retained in:

- `artifacts/production-readiness/phase6/socketio-image-promotion-5957367.txt`
- `artifacts/production-readiness/phase6/celery-graceful-shutdown-5957367.txt`

These executions validate the production-like deployment path. They do not
constitute an actual production deployment or post-production smoke test.

## Environment-Dependent Gate

Actual production deployment execution remains pending the approved release
process, including production traffic acceptance and post-deployment
verification.

Rollback execution also remains pending and is governed by
`docs/production/ROLLBACK_PROCEDURE.md`.

## Current Security Configuration Contract

The current backend contains two separate application encryption domains.

Integration credential domain:

- ``INTEGRATION_ENCRYPTION_KEY``
- ``INTEGRATION_ENCRYPTION_KEY_VERSION``
- ``INTEGRATION_ENCRYPTION_LEGACY_KEYS``

Encrypted database-backup domain:

- ``BACKUP_ENCRYPTION_KEY``
- ``BACKUP_ENCRYPTION_KEY_VERSION``

The backup encryption key must remain distinct from the integration key.

Encrypted backups are ciphertext artifacts. Restore verifies ciphertext integrity,
decrypts into a unique temporary restore location, runs ``pg_restore``, and
removes temporary plaintext restore content after success or failure.

Decryption failure is fail-closed and must never fall back to plaintext restore.

These controls are VERIFIED at the repository level. Production secret custody,
rotation scheduling, and actual deployment remain separate infrastructure gates.
