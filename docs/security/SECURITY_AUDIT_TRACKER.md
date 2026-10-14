# Clinic System Pro - Consolidated Security Audit Tracker

## Tracker metadata

- **Current main commit reviewed for tracker alignment:** `0b41c3a6ae2a262bd039353175559ba0f68d4e63`
- **Original consolidated audit baseline:** `b2267a085048a8aed9dfe3cb07335cb741ac9e5d`
- **Tracker purpose:** Preserve finding status, evidence, limitations, and next actions across security-fix PRs.
- **Verification boundary:** This is a status register, not a new full security audit, penetration test, or production-readiness certification.

The baseline audit predates subsequent changes. Findings affected by later commits must be reassessed against current `main` before closure. Historical test results must not be represented as fresh results for the current complete repository.

## Status definitions

- **OPEN:** Remediation or policy decision remains outstanding; closure is not supported by current evidence.
- **REASSESSMENT REQUIRED:** Relevant code has changed since the original audit or the prior claim needs current-source verification. Do not assume either fixed or still exploitable until reviewed.
- **DISPOSITIONED - NOT OPERATIONAL:** The limitation has been explicitly documented, but the capability is not represented as production-ready.
- **RESOLVED - MERGED:** The scoped change is merged and its recorded verification evidence is available. This does not close adjacent findings.

## Finding register

| # | Risk in original audit | Current status | Evidence / limitation |
| --- | --- | --- | --- |
| 1 | Critical if reachable deployment uses fixed secrets and exposes the backend directly | **OPEN** | Original evidence: `compose.yaml`, production configuration and backend port exposure. Deployment use has not been established by this tracker. |
| 2 | High - payment amount and currency validation | **OPEN** | Original evidence: payment orchestration, webhook validation and Paystack gateway. Require one amount-unit contract, mandatory currency/amount checks and underpayment/missing-field rejection tests. |
| 3 | High - staff suspension not consistently enforced | **OPEN** | Original evidence: staff lifecycle service and clinical route/service guards. Define suspension/session-revocation policy and verify across clinical endpoints. |
| 4 | High - emergency grants not proven to guard ordinary clinical paths | **OPEN** | Original evidence: emergency-access assertion and consent guard call sites. Connect policy to intended API resource/action paths and test allow, deny, expiry and revocation through routes. |
| 5 | High - backup/integration encryption keys not demonstrated in the committed Compose contract | **REASSESSMENT REQUIRED** | Main has subsequent backup-related changes. Recheck `compose.yaml`, backend/worker/Beat secret injection and a production-like encryption-dependent smoke test before closure. |
| 6 | Medium - proxy trust, HSTS, rate limiting and direct backend access | **OPEN** | Original evidence: `nginx.conf`, HTTP security and limiter configuration. Verify the trusted proxy hop and behavior through the actual proxy path; block unintended direct access. |
| 7 | Medium - full backup may include plaintext file artifacts | **REASSESSMENT REQUIRED** | Main has subsequent changes to file backup, backup service, verification and restore code. Inspect current artifact contents and prove the sensitive file portion is encrypted or that the formally approved encrypted-volume boundary is enforced. |
| 8 | Medium - HIE implementation readiness | **DISPOSITIONED - NOT OPERATIONAL** | HIE readiness was explicitly documented in `docs/security/HIE_PROVIDER_READINESS.md` and merged in the PR #18 change line, base commit `4d0a9ad952f9aca611216a475547d41bba8bd885`. HIE remains an extension point; do not claim a registered, production-ready provider adapter without separate implementation and verification. |
| 9 | Medium - consultation-template role and tenant scope | **RESOLVED - MERGED** | PR [#19](https://github.com/strykachiboy-art/clinic-system-pro/pull/19) merged as `0b41c3a6ae2a262bd039353175559ba0f68d4e63`; feature commit `943f9fc36cb776903389a97cd54ccf14c8b09797`. Route/service change and `docs/security/ROLE_TENANT_SCOPE.md`; local focused regression: 258 passed. The two observed GitHub checks were build and dependency audit, not pytest. Scope is consultation templates only. |
| 10 | Medium - inactive-patient rules differ between clinical workflows | **OPEN - NEXT FINDING** | Original evidence: patient lifecycle, scheduling, consultation, laboratory and prescription validation. Confirm intended rules for new operations versus historical reads, then test affected API paths. No remediation is claimed here. |
| 11 | Medium - CI does not execute backend and Flutter test suites | **OPEN** | Current PR #19 checks observed were `build` and `dependency-audit`; those do not establish that pytest, Flutter analysis or Flutter tests ran in CI. Add and require appropriate CI gates separately. |
| 12 | Medium - backup key rotation and old-artifact recovery | **REASSESSMENT REQUIRED** | Backup/restore code changed after the original audit. Retest restoration of artifacts created under prior key versions, define key custody and retention, and document the recovery procedure before changing the status. |

## Evidence rules

1. A commit or merge proves the change entered the repository, not that every operational outcome was verified.
2. A local targeted test result is not a CI result. A build or dependency audit is not a backend or Flutter test suite.
3. Static source review does not establish exploitability in a live deployment, nor does it prove that a separately configured deployment has the same weakness.
4. Do not close a finding solely because related files changed. Record the reviewed commit, focused tests, relevant runtime/configuration evidence, and remaining limitations.
5. Keep remediation minimal, preserve fail-closed behavior, and add regression tests for security-sensitive changes.
6. Findings #5, #7 and #12 must be reassessed against the post-audit backup/restore implementation before any status change.
7. Findings #1-#7 and #10-#12 are not globally resolved by merging findings #8 or #9. Other authorization inconsistencies, external exposure and operational requirements remain separate.

## Next sequence

1. Verify this register on `main` and preserve the worktree boundaries.
2. Begin Finding #10 with current-source inspection and a policy decision before changing implementation.
3. Record the relevant evidence and update this register only after the scoped verification gate passes.
