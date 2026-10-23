# Wave 3 Operational Evidence Ledger

**Evidence date:** 2026-10-23
**Branch:** `feat/hie-endpoint-allowlist-enforcement`
**Purpose:** Record implementation, test, and operational evidence for the three Wave 3 slices without equating code completion with deployment or production operation.

## Evidence vocabulary

- **Implemented** means the code or procedure is present in the repository.
- **Verified** means a named test suite or explicit integrity check passed.
- **Real local recovery executed** means a real backup was restored to a separately named local PostgreSQL target and the persisted report was compared against live target state.
- **Production operated** requires deployment-specific evidence such as the live scraper/log receiver/on-call route or a restore from the approved external failure domain. It is not inferred from tests or this workstation's restore drills.

This ledger records terminal results supplied during the Wave 3 work. Test output and operational reports remain local unless separately reviewed for safe publication. It contains no encryption-key values, credentials, database URLs, patient records, or backup payloads.

## Slice 1 â€” Authenticated metrics and request correlation

**Commit:** [`cbcce6c0bf349bdeec3404fb10b5f1bdf01384cd`](https://github.com/strykachiboy-art/clinic-system-pro/commit/cbcce6c0bf349bdeec3404fb10b5f1bdf01384cd)
**Message:** `feat(observability): expose authenticated HTTP metrics`
**Committed file scope:** 8 files. The commit added the metrics endpoint and request metric export, application/config wiring, endpoint tests, Compose environment wiring, NGINX request/trace correlation, and `docs/runbooks/observability-metrics.md`.

### Verification evidence

- Focused observability regression after hardening: **37 passed in 92.64 seconds**.
- The test coverage verifies the `/metrics` fail-closed behavior when the access token is absent or invalid, Prometheus text exposition, telemetry health visibility, low-cardinality output, and exclusion of request identifiers/patient details from metric labels.
- The runbook defines internal-only scraping, secret-backed Bearer authentication, Redis telemetry freshness/health checks, NGINX/backend correlation, and the boundary between code and deployment evidence.

### Still not demonstrated

- Compose configuration validation was not completed in the recorded run because required environment values were missing; no Compose-validation pass is claimed here.
- A production scraper, dashboard, centralized log sink, multi-replica routing/correlation check, and live metric alerting were not demonstrated by this slice's focused tests.

**Slice status:** IMPLEMENTED; FOCUSED TESTS VERIFIED; PRODUCTION OPERATION NOT CLAIMED.

## Slice 2 â€” Scheduled operational alert lifecycle

**Commit:** [`eaa942cd2b12641a0e1a6c7863602b2099438854`](https://github.com/strykachiboy-art/clinic-system-pro/commit/eaa942cd2b12641a0e1a6c7863602b2099438854)
**Message:** `feat(observability): schedule operational alert lifecycle`
**Committed file scope:** 7 files. The commit added the alert-delivery logger, scheduled alert task and lifecycle handling, Celery wiring, focused tests, and `docs/runbooks/observability-alerts.md`.

### Verification evidence

- Focused alert regression: **41 passed in 9.47 seconds**.
- Tests verify the task is registered and scheduled every 60 seconds, and cover `new`, `escalated`, and `resolved` lifecycle events, stable event payloads/order, severity mapping, and fail-closed behavior on missing Redis state or snapshot failures.
- Lifecycle events are emitted as structured `operational_alert` log records. Repeated `ongoing` conditions do not produce a notification every minute.

### Still not demonstrated

- External paging/notification service setup, centralized log receiver routing, a witnessed end-to-end notification to the intended on-call operator, and operator acknowledgement were not demonstrated.

**Slice status:** IMPLEMENTED; FOCUSED TESTS VERIFIED; EXTERNAL ALERT DELIVERY NOT CLAIMED.

## Slice 3 â€” Restore evidence, integrity, and recovery objectives

**Commit:** [`e0fd5f6cbfaf6f22d7f2f4e00dc983d096b3ff68`](https://github.com/strykachiboy-art/clinic-system-pro/commit/e0fd5f6cbfaf6f22d7f2f4e00dc983d096b3ff68)
**Message:** `feat(backup): record restore drill recovery evidence`
**Committed file scope:** 3 files: `backend/app/core/backup/restore_drill.py`, `backend/app/tests/core/backup/test_restore_drill.py`, and `docs/security/BACKUP_RESTORE_DRILL.md`.

### Automated verification

- Python compilation: **passed**.
- Focused restore drill regression: **24 passed in 11.56 seconds**.
- Entire `backend/app/tests/core/backup` test directory: **162 passed in 102.14 seconds**.
- Staged scope, `git diff --check`, commit scope, push, and remote-SHA verification passed for the Slice 3 code commit.

### Real local restore â€” legacy plaintext backup

- Original backup created at `2026-09-24T13:09:57Z`; original backup size and SHA-256 matched metadata, and `pg_restore --list` successfully read its PostgreSQL custom-format archive (1,130 listed entries).
- Full backup verification passed. The legacy metadata lacked encryption markers; a separate working copy explicitly marked `encrypted: false` was used. The original backup was not modified.
- Real restore to `clinic_system_w3_restore_20261023` passed in **9.11 seconds**.
- The persisted report was independently cross-checked against live queries to the target database. The report and live database matched on target database name, migration revision, user/clinic/audit/outbox counts, and zero `processing` outbox entries.
- Authentication invalidation, chat outbox recovery, and post-restore verification were reported complete.
- **File recovery limitation:** the backup manifest had **0 files / 0 payload bytes**. This run proves database recovery from that backup, not recovery of uploaded documents.
- Its RPO and RTO targets were recorded as `not_configured`; no target-compliance claim is made from this run.

### Real local restore â€” encrypted backup

- A new full backup was generated on **2026-10-23** from the verified loopback source. Its database artifact and file manifest were encrypted under backup key version `1`; the dedicated backup-encryption configuration validation and full-backup verification passed.
- Backup ID: `20261023T054021Z-ba06808e11ad4136b99feb17a5d9b386`.
- Real restore to `clinic_system_w3_encrypted_restore_20261023` passed in **8.938 seconds**.
- The execution output recorded successful authenticated decryption, database artifact size/SHA-256 verification, encrypted file-manifest/payload verification, post-restore database/report cross-check, authentication invalidation, outbox recovery, and post-restore verification.
- The source database state was checked before and after the restore and reported unchanged. The key material was not included in the report or source control.
- Live target/report values matched: migration revision `4d205e66d288`, 5,020 users, 2 clinics, 20,898 audit-log rows, 62 chat-outbox rows, and 0 rows still in `processing`.
- **File recovery limitation:** the source storage had no files at drill time, so this encrypted full backup also contained **0 file payloads / 0 bytes**. The encrypted manifest path was verified, but there is not yet a successful recovery demonstration of a real file payload.
- Evidence files were kept local, not committed: `artifacts/wave3-restore-drill-20261023/restore-drill-report.json`, `artifacts/wave3-encrypted-restore-drill-20261023/restore-drill-report.json`, and `artifacts/wave3-encrypted-restore-drill-20261023/encrypted-backup-evidence.json`.

### RPO/RTO status qualification

The legacy restore report correctly recorded RPO/RTO as `not_configured`. For the encrypted run, the operator supplied `3600` seconds (one hour) at the interactive RPO target prompt for this test run; the report measured backup age at `3.924` seconds and reported `RPO_STATUS: within_target`. The subsequent `.env` audit found no RPO value; it did not inspect the interactive prompt input, so that result is not a configuration discrepancy. This documents the test-run target and calculation, not a formally adopted operational RPO unless the service owner separately approves and records it. `rpo_authoritatively_verified` remains false: backup-age freshness is only a proxy, not a measurement of data loss at an incident recovery point. RTO remained `not_configured`.

### Still not demonstrated

- Restore of a real file payload from encrypted backup.
- Restore from a separately administered off-site or independent failure-domain copy.
- Formally adopted operational RPO/RTO values and owners, plus accepted target-compliance evidence; the 3600-second RPO was supplied for this test run and is not by itself proof of policy approval.
- Automatic historical backup-key ring selection; the current documented design uses one active key/version pair per process and requires the operator to retrieve the matching version from the separately controlled inventory.

**Slice status:** IMPLEMENTED; FOCUSED AND BACKUP-SUITE TESTS VERIFIED; REAL LOCAL LEGACY AND ENCRYPTED DATABASE RESTORES EXECUTED AND REPORT-CROSS-CHECKED; OFF-SITE RECOVERY AND FULL CLOSURE GATES OUTSTANDING.

## Wave 3 consolidated status

| Capability | Evidence-backed status | Remaining proof |
|---|---|---|
| Authenticated metrics export and request correlation | Focused tests passed; implementation committed | Compose check, deployed scraper, live correlation/log sink and alerting |
| Scheduled alert lifecycle | Focused tests passed; lifecycle output tested | Actual receiver/pager routing and witnessed on-call notification |
| Restore report and integrity evidence | Focused and full backup test suite passed | Approved recovery objectives and full operational closure evidence |
| Local legacy database recovery | Real restore and independent target/report cross-check passed | Does not cover encrypted restore or file payloads by itself |
| Local encrypted database recovery | Authenticated decryption and independent target/report cross-check passed | Current source storage had no files; file-payload recovery remains unproven |
| Independent/off-site recovery | Not executed | Retrieve a backup from an independently administered failure domain and restore it using controlled recovery-key custody |
| RPO/RTO compliance | Not yet accepted | Audit RPO source/approval; agree targets and owners; record accepted RTO result |

## Operational safety and closure rule

- Do not commit backup payloads, restored database dumps, `.env` content, encryption keys, key-bearing environment exports, or raw operational reports into source control. Keep reports access-controlled and publish only reviewed, non-secret summaries.
- The restore-drill target databases contain a copy of application data. Their retention or removal is a separate authorized operational action; this ledger does not direct automatic deletion.
- Do not equate **IMPLEMENTED**, **TESTS VERIFIED**, **REAL LOCAL RESTORE EXECUTED**, and **PRODUCTION OPERATED**.
- Wave 3 must remain **OPEN** until the off-site restore and agreed recovery-objective evidence are completed, and the operational alert receiver and metrics/log routing requirements are either proven or explicitly scoped as a separately accepted deployment responsibility.
