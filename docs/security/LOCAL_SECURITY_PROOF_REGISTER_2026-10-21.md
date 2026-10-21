# Clinic System Pro â€” Local Security Proof Register

- **Evidence snapshot:** 21 October 2026
- **Repository branch:** `feat/hie-endpoint-allowlist-enforcement`
- **Post-Finding #2 commit:** `100c4725262f8fd4412d550545b6545054082068`
- **Commit message:** `fix: enforce per-invoice currency in billing`
**Publication at evidence capture:** Finding #2 was committed locally; GitHub publication had not yet been verified when this register was drafted.

## Purpose and limitations

Purpose. This register consolidates the local test and inspection evidence reported during the Clinic System Pro security-audit work as of 21 October 2026. It is an evidence log, not a certification that every finding is closed.

Evidence discipline. Counts and outcomes for findings other than Finding #2 are transcribed from earlier working-session results and were not all rerun as part of producing this document. â€œLocally verifiedâ€ means a local command/test result was reported; it does not mean CI passed, production was operated, or the finding is formally closed.

This document records proof and known gaps, using these distinctions:

- **Implemented** â€” code/config/test changes exist locally.
- **Locally verified** â€” a scoped local test or inspection result was reported passing.
- **Operational proof required** â€” the evidence needs a real deployment/restore/key-recovery exercise or comparable operational check.
- **Formally closed/dispositioned** â€” only when the official tracker status and supporting evidence justify it.

## Repository and commit checkpoint

- Branch: `feat/hie-endpoint-allowlist-enforcement`.
- Before this commit, HEAD was `890b259bb46544c52364a3815d5c15f30f8543f6`.
- Finding #2 commit: `100c4725262f8fd4412d550545b6545054082068` (`100c472` short hash).
- Exactly 14 Finding #2 files were staged and committed; staged-scope and commit-scope checks passed.
- Python compilation and `git diff --check` passed before commit. Git printed LF/CRLF conversion warnings; these did not fail the checks.
- The final Finding #2 focused billing regression passed **485 tests in 275.52 seconds**.
- Fresh disposable PostgreSQL database upgrade to the single Alembic head `2fcd41e8c6ed` passed. Verified: nullable `invoices.currency VARCHAR(3)`, `role` enum alignment, and `reporttype.WARD`.
- Migration upgrade/downgrade/re-upgrade round trip for `d2c7f4a91b36` passed on a disposable database.
- Existing `clinic_system_db` was not modified during these migration checks.
- At evidence capture, the Finding #2 commit had not yet been pushed. Verify remote publication separately. Other security/resilience/mobile files remained modified or untracked and must be preserved.

## Executive status

| Finding | Tracker state at last recorded checkpoint | Local proof snapshot | Next gap |
|---|---|---|---|
| 1 â€” Secrets and backend exposure | OPEN | Compose exposure/secrets hardening; about 13 focused security-contract tests reported. | Confirm exact test count; review deployment configuration and publish. |
| 2 â€” Payment amount and currency integrity | OPEN pending formal tracker update | 485 focused tests; disposable migration round trip and full-head upgrade passed; local commit 100c472. | Update tracker and follow publication workflow; not pushed. |
| 3 â€” Staff suspension enforcement | OPEN | 27 structural tests and 547 clinical-route regression tests reported passing. | Recheck scoped diff, rerun, commit/publish. |
| 4 â€” Emergency access controls | OPEN | 165 suite tests, 4 safety E2E tests and an expiry-route test reported passing. | Recheck implementation and E2E evidence; commit/publish. |
| 5 â€” Backup and integration encryption configuration | REASSESSMENT REQUIRED | 37 targeted encryption/Compose tests plus synthetic fail-closed check reported passing. | Reassess key/config controls and operational evidence. |
| 6 â€” Proxy trust, HSTS, rate limits and direct backend exposure | OPEN | 26 proxy/security tests and effective Compose inspection reported passing. | Verify trust/HSTS/rate-limit settings in target deployment. |
| 7 â€” Plaintext backup artifacts and restore safety | REASSESSMENT REQUIRED | 86 backup/restore tests reported passing. | Off-site survivability and actual recovery exercise still needed. |
| 8 â€” HIE production-provider readiness | DISPOSITIONED | Disposition recorded because no production HIE provider is operational. | Reassess if a provider is enabled. |
| 9 â€” Consult-template role and tenant scope | RESOLVED | Consult-template role/tenant-scope finding recorded resolved and merged. | Retain prior merge and regression evidence. |
| 10 â€” Inactive patient rules | OPEN | 8 service tests and 9 inactive-patient route E2E tests reported passing. | Review workflow paths, rerun, commit/publish. |
| 11 â€” CI coverage and required checks | OPEN | Prior full regression: 8,072 passed / 24 failed; blockers identified. | Fix CI causes and make intended checks required. |
| 12 â€” Backup key rotation, recovery and custody | REASSESSMENT REQUIRED | Rotation, recovery and custody requirements identified. | Operational key rotation/recovery proof remains outstanding. |

## Detailed finding register

### Finding 1 â€” Secrets and backend exposure

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

Local Compose hardening was implemented and the Compose security-contract suite was reported green (approximately 13 focused tests; exact count should be reconfirmed from the saved test output). The recorded scope included removing direct backend exposure and development hard-coded Phase 6 secrets.

**Remaining before closure/reassessment:**

Review the exact uncommitted Compose/security diff, rerun the focused tests from a saved command/output, verify production-facing deployment configuration, then commit/publish the finding-scoped change and update the tracker. A passing local contract suite is not a production deployment inspection.

### Finding 2 â€” Payment amount and currency integrity

**Tracker state at last recorded checkpoint:** OPEN pending formal tracker update

**Local proof recorded:**

Commit 100c4725262f8fd4412d550545b6545054082068 (message: â€œfix: enforce per-invoice currency in billingâ€) was created locally with exactly 14 reviewed files; commit scope was verified. The final integrated focused billing suite passed 485 tests in 275.52 seconds. The earlier migration round-trip passed on a disposable PostgreSQL database. A fresh disposable database upgraded to the single head 2fcd41e8c6ed; checks passed for nullable invoices.currency VARCHAR(3), role enum alignment, and reporttype.WARD. The existing clinic_system_db was not changed. Production call-site scan found the two create_invoice route calls and the service definition; both route calls forward payload.currency.

**Remaining before closure/reassessment:**

Verify that the remote branch contains commit 100c4725262f8fd4412d550545b6545054082068, then add/confirm the evidence in the official tracker. Formal closure still depends on the remaining tracker and validation requirements. Do not call the finding formally closed until the tracker and any required broader validation are updated.

### Finding 3 â€” Staff suspension enforcement

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

The staff-suspension structural suite was reported as 27 passed, and the clinical-route regression was reported as 547 passed. Related route changes and the staff-suspension test file were still present as local worktree changes in the last post-commit status snapshot.

**Remaining before closure/reassessment:**

Re-check current worktree diffs and test commands/output, ensure the local changes form a coherent finding-scoped change, run the agreed validation, then commit/publish. Confirm suspension checks cover all relevant clinical access paths and update tracker evidence.

### Finding 4 â€” Emergency access controls

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

The emergency-access suite was reported as 165 passed; the safety E2E suite as 4 passed; and a separate emergency-access-expiry route test passed. The expiry test file was still untracked in the last post-commit status snapshot.

**Remaining before closure/reassessment:**

Review the implementation and expiry-route test together, preserve the test artifact, rerun the scoped tests, and commit/publish the exact finding files. Production readiness still requires policy, audit logging and expiry behavior to be validated in the intended deployment context.

### Finding 5 â€” Backup and integration encryption configuration

**Tracker state at last recorded checkpoint:** REASSESSMENT REQUIRED

**Local proof recorded:**

Targeted encryption/Compose regression was reported as 37 passed, with an additional synthetic fail-closed check reported passing. The Compose file and a new Compose encryption contract test remained local worktree changes/untracked at the last snapshot.

**Remaining before closure/reassessment:**

Reassess the finding against the required secrets/key handling behavior, review current code/tests, save reproducible outputs, and confirm there is no plaintext fallback. Distinguish application/configuration tests from actual key custody, deployment secret management, and recovery operations.

### Finding 6 â€” Proxy trust, HSTS, rate limits and direct backend exposure

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

A focused proxy/security suite was reported as 26 passed, and effective Compose configuration inspection was reported completed. The proxy-security test and Compose configuration were still locally modified in the last status snapshot.

**Remaining before closure/reassessment:**

Reinspect trusted proxy configuration, forwarded-header trust boundaries, HSTS and rate-limit settings, and confirm the backend is not directly exposed in the effective target deployment. Rerun tests against the final configuration, commit/publish, and document deployment-specific verification.

### Finding 7 â€” Plaintext backup artifacts and restore safety

**Tracker state at last recorded checkpoint:** REASSESSMENT REQUIRED

**Local proof recorded:**

The local backup/restore test suite was reported as 86 passed.

**Remaining before closure/reassessment:**

The reported suite is not proof of off-site survivability or an actual disaster-recovery exercise. Record backup artifact protection, access controls, off-site/independent copy, restore test results, recovery keys availability, and agreed RPO/RTO evidence. Reassess the finding after operational proof is available.

### Finding 8 â€” HIE production-provider readiness

**Tracker state at last recorded checkpoint:** DISPOSITIONED

**Local proof recorded:**

The tracker entry was previously recorded as dispositioned because no production provider is currently operational; the provider extension point is not being treated as a live production integration.

**Remaining before closure/reassessment:**

Keep this disposition tied to the actual deployment/product state. Reopen/reassess if a production HIE provider is enabled; validate the provider-specific security and operational controls before launch.

### Finding 9 â€” Consult-template role and tenant scope

**Tracker state at last recorded checkpoint:** RESOLVED

**Local proof recorded:**

The tracker previously recorded this authorization/tenant-scope finding as resolved and merged.

**Remaining before closure/reassessment:**

Retain the merge and regression evidence referenced by the tracker. Reopen only if later changes regress role/tenant scoping.

### Finding 10 â€” Inactive patient rules

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

The inactive-patient service suite was reported as 8 passed and the route E2E suite as 9 passed. Relevant route modifications and the inactive-patient E2E file were still local/untracked in the last post-commit status snapshot.

**Remaining before closure/reassessment:**

Review all relevant patient-facing and clinical workflow paths, rerun the scoped service/E2E suites, preserve the outputs, then commit/publish the finding-specific code and test. Confirm the enforcement policy aligns with intended clinical/business exceptions.

### Finding 11 â€” CI coverage and required checks

**Tracker state at last recorded checkpoint:** OPEN

**Local proof recorded:**

A prior full regression run was reported with 8,072 passed and 24 failed. The failure categories identified were: 2 API-version tests expecting a code key; 8 unsupported-version tests with the same domain-error-code issue; 1 Windows-style backup-path test failing under Linux path semantics; 7 PostgreSQL concurrency tests missing CONCURRENCY_TEST_DATABASE_URL; 2 worker-crash E2E tests missing the clinic-system-phase9:49ae8cc Docker image; 2 XLSX tests and 2 PDF tests missing openpyxl/reportlab dependencies. The full-regression workflow was described as scheduled/manual rather than required on pull requests.

**Remaining before closure/reassessment:**

Fix implementation/config/dependency/infrastructure causes without weakening assertions. Provide the concurrency database in CI, build/pull the required worker image, include required export dependencies, make path validation portable, fix domain error-code propagation, and make appropriate checks required before claiming the CI gate is green.

### Finding 12 â€” Backup key rotation, recovery and custody

**Tracker state at last recorded checkpoint:** REASSESSMENT REQUIRED

**Local proof recorded:**

Requirements and gaps were identified, but no verified operational key-rotation/recovery/custody exercise was recorded in the evidence available for this report.

**Remaining before closure/reassessment:**

Define and demonstrate rotation, escrow/custody, authorized recovery, separation of duties, restore access to historical backups, and recovery validation. Record evidence and reassess the tracker only after an operational exercise.

## Finding #11 â€” previously observed CI failure inventory

The previously reported full-regression result was **8,072 passed / 24 failed**. The known failure inventory was:

1. 2 API-version tests expected a code key, but the domain error code was `None`.
2. 8 unsupported API-version tests hit the same domain-error-code issue.
3. 1 backup-path test treated `C:/outside.dump` as not absolute on Linux.
4. 7 PostgreSQL concurrency tests lacked `CONCURRENCY_TEST_DATABASE_URL`.
5. 2 worker-crash E2E tests could not find the Docker image `clinic-system-phase9:49ae8cc`.
6. 2 XLSX and 2 PDF tests failed because `openpyxl` and `reportlab` were absent from the locked backend requirements.

The workflow was previously described as scheduled/manual, not PR-required. These are identified blockers, not verified fixes. Rerun the current CI gate after making controlled fixes.

## Preserved local work after Finding #2 commit

The post-commit status output still showed other local edits and untracked artifacts, including route hardening for ambulance/lab/pharmacy/prescription/ward, `backend/app/extensions.py`, `compose.yaml`, proxy/staff tests, resilience scenarios/tests, Compose security/encryption tests, emergency-expiry and inactive-patient E2E tests, PowerShell scripts, Phase 9/system-mixed test outputs, and a mobile Android build report. This list reflects the snapshot at commit time and may change. Do not reset, clean, or broadly stage these files.

## Closure plan before returning to Flutter

1. Preserve the proof output for each finding in a reproducible evidence folder (command, environment, date, result, test count, elapsed time).
2. Review and validate each existing local implementation in small, finding-scoped batches; commit only explicitly reviewed files.
3. Fix and rerun the Finding #11 CI blockers without weakening tests, and make required checks enforceable where intended.
4. Obtain operational proof for Finding #7 (off-site backup survivability and restore) and Finding #12 (key rotation/custody/recovery).
5. Update `docs/security/SECURITY_AUDIT_TRACKER.md` only when the required evidence exists; retain #8 as dispositioned and #9 as resolved unless facts change.
6. Confirm final branch/worktree/remote status before switching back to Flutter. Update this register if publication outcome or tracker status changes.

## Evidence provenance

This report is based on the local terminal outputs and working-session notes supplied in the conversation. It does not independently rerun the tests for findings other than the specifically reported Finding #2 gate, and it does not claim production operation, external review, or tracker updates that have not occurred.
