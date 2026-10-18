# Phase 0 Baseline Record - 2026-10-18

## Status

Phase 0 inventory: PARTIAL / BLOCKED.

Runtime observations below describe the local Docker environment at the
time of inspection. They do not establish that a production deployment
exists or that production capacity has been verified.

## 1. Source control

- Repository: strykachiboy-art/clinic-system-pro
- Branch: feat/hie-endpoint-allowlist-enforcement
- HEAD: 5e8f6071feec1b8f31242eef41600f2eb37598e9
- HEAD subject: docs: fix Phase 17 encoding in hardening plan
- Tracked staged and unstaged diffs: none at inspection
- GitHub comparison at this HEAD: four commits ahead of main, zero behind
- Untracked paths preserved:
  - apply_hie_bounded_dns_patch.ps1
  - mobile/android/build/
  - phase19-slice3a-maintenance-trigger.ps1

No application, infrastructure, or test files were changed during the
baseline inspection.

## 2. Observed local topology

Compose project: clinic-system-phase9

Container labels recorded these Compose files:

- compose.yaml
- compose.phase6.yaml
- compose.phase9.yaml

The local compose.phase9.yaml file is ignored by
.git/info/exclude. Its SHA-256 was:

C96F066E40AE54C4B09201E1F120EF19B02903978C2E5DAEFC83F6D57BEFA91B

The overlay references deployment/nginx/nginx.phase9.conf, which is
absent from the local Nginx directory and absent from the searched
tracked Git history.

The local mobile bridge is a separate Nginx container publishing
127.0.0.1:5000. The main Compose proxy is stopped.

This topology is a local Phase 9 environment, not proof of production
topology.

## 3. Confirmed proxy startup failure

Observed proxy exit code: 127.

Docker's recorded startup error identifies a failed bind mount for
deployment/nginx/nginx.phase9.conf. The source file was missing.
This is a container mount/startup failure, not a confirmed Nginx
syntax error.

The Phase 6 Nginx configuration redirects HTTP to localhost:18443.
The local Phase 9 overlay publishes localhost port 19443 for HTTPS.
Therefore, substituting the Phase 6 configuration unchanged would not
preserve the Phase 9 port contract.

No proxy restart or configuration change was performed.

## 4. Environment contract and reproducibility

The ignored root .env file contained 18 assignment names. None used
the PHASE6_ prefix. The checked-in .env.example contained 25 assignment
names using the general application configuration names.

The current process, user and machine environment scopes had none of the
17 required PHASE6_ variable names detected in compose.phase6.yaml and
compose.phase9.yaml.

A Compose metadata query using these files failed because required
PHASE6_ variables were unavailable in the current shell environment.
This does not prove how the existing containers were originally created.

The most recent 10,000-line shell-history scan found no env-file path
candidates. The source used to create the running containers remains
unresolved.

Secret values were not included in this record.

## 5. Container and application observations

Observed runtime versions:

- Python: 3.12.14
- Gunicorn: 23.0.0
- Celery: 5.6.3
- PostgreSQL: 18.6
- Redis: 8.10.2
- Nginx mobile bridge: 1.31.6

The running application image tag was clinic-system-phase9:49ae8cc.
The tag resolved to Git commit 49ae8ccf53bd56cafa216083defaf452e0e1ff3f,
dated 2026-10-05, which is an ancestor of the inspected HEAD.
The image had no OCI revision label, so exact build provenance remains
unverified.

Direct in-container probes returned HTTP 200 for:

- /health/live
- /health/ready

PostgreSQL observations:

- Current database: clinic_system_phase9
- Observed connections: 12
- max_connections: 100

Redis observations:

- PING succeeded
- Used memory: 2.10M
- Server maxmemory: 0B
- Connected clients: 21
- Selected database key count: 50
- Keys with expiry settings: 41

These are point-in-time observations, not load-test results.

## 6. Worker and resource observations

Celery inspect reported one online worker consuming the default celery
queue, with concurrency 4 and prefetch_count 4.

The worker's reported task total counters included one each for
check_upcoming_appointments, mark_overdue_invoices and
run_backup_retention. These are task counters, not queue-depth evidence.

A Docker resource snapshot reported:

- Backend: about 157.6 MiB
- Beat: about 149.7 MiB
- Worker: about 434.3 MiB
- PostgreSQL: about 48.75 MiB
- Redis: about 17.18 MiB

This snapshot was not collected under representative load.

The Redis queue-depth probe and Celery active/reserved/scheduled task
count probe failed because of command-line quoting in the diagnostic
script. Those measurements were not obtained and must not be inferred.

## 7. Verification matrix

Verified:

- Tracked worktree was clean.
- The listed untracked paths remained untouched.
- Backend liveness and readiness returned HTTP 200.
- PostgreSQL accepted a read-only connection.
- Redis PING and INFO queries succeeded.
- Celery inspection reached one worker.
- The stopped proxy's mount failure was identified.

Blocked or unresolved:

- Reproducible Compose configuration from the current shell.
- The intended tracked/untracked status of the local Phase 9 overlay.
- The missing Phase 9 Nginx configuration contract.
- Exact source revision used to build the application image.
- Redis queue depth and Celery active/reserved/scheduled task counts.
- Representative workload latency, throughput, errors, queue age and
  resource utilization.
- Production topology and production-operated status.

No new test suite or load benchmark was run during this inspection.
Historical test results must not be represented as a fresh run.

## 8. Phase 0 exit decision

Phase 0 inventory is captured, but the exit gate remains PARTIAL / BLOCKED.

Before claiming reproducible deployment readiness, establish an explicit
configuration source and a valid Phase 9 Nginx/port contract.

Before claiming a performance baseline, run a controlled synthetic
workload and capture its run ID, users, spawn rate, duration, server-side
latency, error rate, database measures, Redis state and worker queue
measures.

Do not guess proxy configuration, rebuild the image, restart containers,
change Redis policy, or delete local artifacts as part of this record.
