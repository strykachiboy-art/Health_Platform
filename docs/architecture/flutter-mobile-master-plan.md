# CLINIC SYSTEM PRO — FLUTTER MOBILE MASTER PLAN
## Implementation Roadmap, Verified Baseline, Security Boundaries and Release Strategy

**Project:** Clinic System Pro\
**Repository:** `strykachiboy-art/clinic-system-pro`\
**Client:** Flutter\
**Backend:** Flask / Python\
**Primary database:** PostgreSQL\
**Mobile local database:** Drift / SQLite\
**Status snapshot:** 29 October 2026\
**Current integration target:** `main`\
**Phase 19 implementation branch:** `feat/phase19-offline-data-security`\
**Recorded Phase 19 implementation commit:** `9f1dd0a62c031bfcae5e50ad7d36d9c66ed15b3a`\
**Phase 19 PR:** [#30 — merged](https://github.com/strykachiboy-art/clinic-system-pro/pull/30)\
**PR head at merge:** `dbbaab282ea6d18f45cf1d8d357ef2437f07d1b5`\
**PR merge commit:** `6a16a3d2bb7c1ab6bfbcd339d5afefb60f72b97d`\
**Current master-plan purpose:** Preserve the original roadmap, reflect implemented work and merge evidence, and guide the next source-verified phases without overstating readiness.\
**Backend handoff baseline:** `9,022/9,022` tests GREEN (`8,749` backend application + `273` resilience), `0 failed`.\
**Backend handoff repair commit:** `dac3d44c726f315633596dc10a93cebca48b9fe9`\
**Flutter continuation gate:** SATISFIED for new-PC development; physical-device and later production-operation evidence remain separate gates.

---

# 1. PROJECT VISION

Clinic System Pro is a secure, multi-clinic healthcare management platform. Flutter provides the mobile client, while the backend remains responsible for authoritative business rules, identity, authorization, clinical decisions, financial state and persistent server-owned records.

The objective is not merely to make the existing application run on a phone. The objective is to build a reliable healthcare mobile client that behaves safely under ordinary, interrupted, offline, concurrent and security-sensitive conditions.

The Flutter application must support appropriate clinical and operational workflows while preserving the backend's existing security boundaries.

The system must remain safe when:

- The device loses internet connectivity.
- The backend is temporarily unavailable.
- A request is submitted but the response is lost.
- A user signs out while work is being processed.
- The active user or clinic changes.
- A token expires or is rejected.
- A background operation fails.
- A local encryption key becomes unavailable.
- A database migration fails.
- A device is restarted during queued work.
- Two changes conflict.
- A replayed operation may already have succeeded on the server.

A polished interface is important, but correctness, privacy, clinical safety and recoverability take priority.

# 2. MASTER ARCHITECTURE PRINCIPLES

These principles apply to every current and future phase.

## 2.1 The backend is authoritative

The backend is the source of truth for:

- User identity and authentication.
- Roles, permissions and clinic membership.
- Patient and clinical records.
- Consultations, appointments and prescriptions.
- Pharmacy, laboratory, billing and financial records.
- Clinical safety decisions and emergency access.
- Authoritative operation outcomes.
- Conflict resolution and synchronization decisions.
- Server-side audit history.

A mobile screen, local database record, queue entry, cached token or encryption key must never independently grant access to protected data. All submitted operations remain subject to server-side authentication, authorization, validation and business rules.

## 2.2 Local data is not automatically approved

The existence of a local database does not authorize storing patient records, clinical notes, credentials, financial information or arbitrary API responses.

Every new persistent data category requires a documented decision covering:

- Purpose and necessity.
- Data owner and clinic context.
- Sensitivity.
- Permitted fields.
- Authorization and freshness.
- Retention and expiration.
- Deletion and recovery.
- Encryption and key handling.
- Logout and account-switching behavior.
- Verification evidence.

Persist only what a demonstrated workflow requires.

## 2.3 Storage responsibilities remain separate

**Drift / SQLite** is for approved local metadata and explicitly designed persistent mobile state. `AppMetadata` remains restricted to non-sensitive metadata.

**Platform-backed secure storage** is for small secrets, such as session credentials and encryption keys. It is not a replacement for a database or a general-purpose offline cache.

**Encrypted queue payload storage** is only for approved operation payloads under the offline-storage design. Application-level payload encryption does not mean that the entire SQLite database is encrypted.

**Backend API** is used to obtain authoritative application data and submit server-owned state changes.

Do not add another database, storage framework or encryption layer without a documented technical need and an explicit design review.

## 2.4 Connectivity and service availability are different

A device reporting that it is online does not prove the backend is reachable. A backend request receiving a response does not prove that the requested business operation succeeded. The client must distinguish device connectivity, request transport outcome and authoritative operation outcome.

## 2.5 Unknown outcomes must be handled explicitly

If the client submits an operation but cannot establish whether the server processed it, it must not blindly create another operation. The client should preserve stable operation identity, reconcile the uncertain result and retry only when the endpoint's idempotency contract makes the retry safe.

## 2.6 Security and clinical safety cannot depend on UI behavior alone

Disabling a button does not replace server authorization. Hiding a route does not replace access control. Showing an alert does not replace clinical safety enforcement. The backend remains responsible for enforcing protected behavior.

## 2.7 No unsupported completion claims

The project uses these status definitions:

- **PLANNED:** The work has been identified but has not been implemented or verified.
- **IMPLEMENTED:** The corresponding behavior exists in source code.
- **VERIFIED LOCALLY:** The specified tests or checks passed in the recorded development environment.
- **EMULATOR-VERIFIED:** The documented scenario passed on the specified emulator.
- **DESIGN ACCEPTED:** The architecture or policy decision has been formally accepted.
- **OPEN GATE:** Additional implementation, review or evidence remains necessary.
- **PRODUCTION OPERATED:** The behavior has been observed in an actual deployed environment with operational evidence.

These labels are not interchangeable. Passing tests on an emulator does not prove production operation or every possible device-security property.

# 3. TECHNOLOGY BASELINE

## Backend

The existing backend uses:

- Python and Flask.
- Flask-SQLAlchemy and SQLAlchemy 2.
- PostgreSQL.
- Flask-Migrate / Alembic.
- Redis.
- JWT authentication and Google OAuth.
- Pydantic v2 validation.
- Flask-SocketIO.
- Celery and background processing.
- Structured application errors and transactional workflows.

## Flutter

The mobile application uses the Flutter and Dart toolchain with packages including:

- Flutter and Flutter Test.
- `flutter_bloc`.
- `get_it`.
- `go_router`.
- `http` and `dio`.
- `connectivity_plus`.
- `flutter_secure_storage`.
- Drift and SQLite.
- `cryptography`.
- Google Sign-In.
- Freezed and JSON serialization tooling.
- Flutter integration testing.

The exact dependency and SDK versions must be read from `mobile/pubspec.yaml` and the repository's lockfile at the start of a technical phase. Do not update packages merely because newer versions exist.

## Current local-storage schema

The application database is configured as `clinic_system_local`. The current `AppDatabase.schemaVersion` is **3**.

The database includes:

- `AppMetadata` for non-sensitive application metadata.
- `OperationQueueEntry` for operation identity, ownership and lifecycle information.
- `OperationQueuePayload` for encrypted operation payloads and related cryptographic metadata.

Future schema changes must use explicit migrations and preserve existing data. Do not rewrite historical migrations to simplify the migration chain.

# 4. PROJECT STATUS AND VERIFIED BASELINE

The master plan is the roadmap, not the final source of truth for implementation status. The actual repository, current branch, tests and recorded evidence determine the status of individual features.

## 4.1 Backend prerequisite work — existing platform baseline

The preceding backend phases established the platform that Flutter depends on. The existing platform includes work covering core backend architecture and APIs, authentication and role-based authorization, multi-clinic tenant isolation, clinical safety and emergency-access controls, database transactions and migrations, auditability and security hardening, observability and operational controls, production-like testing infrastructure, background-task durability, end-to-end testing, failure injection and resilience testing, and encryption and data-protection hardening.

Historical evidence includes a backend full-regression checkpoint of **7,397 passed, 0 failed, 0 errors and 2 skipped** on 1 October 2026, as well as later targeted regression and resilience gates. The Phase 9 resilience suite was recorded at **66 passed** on 7 October 2026. Additional backend end-to-end verification was recorded after concurrency and test-infrastructure fixes.

These are historical checkpoints, not a claim that the entire backend was freshly rerun on 17 October 2026. Any future backend-sensitive change must run its applicable regression tests. The backend remains the authority for business rules, permissions and data integrity.

## 4.2 Flutter Phases 10–14 — foundational application and workflows

The Flutter application, routing, state-management and networking foundations exist. Authentication/session behavior and patient-facing workflows are present in the reviewed source, and later phases have extended their offline and lifecycle safeguards.

The current source includes patient directory/detail behavior, registration/editing flows, remote API communication, session-state handling and patient-view protections. The project also maintains tests and shared networking infrastructure.

The original roadmap includes feedback and internal chat/realtime functionality. Their complete feature coverage and phase-level acceptance criteria must be confirmed from the current source and test evidence before describing every subfeature as fully closed.

**Rule:** Do not rebuild the foundation simply because the original document still says “Implement.” Inspect the code first. Also do not mark a whole phase complete just because some files exist.

## 4.3 Phase 15 — Flutter offline-first foundation

**Status: VERIFIED LOCALLY for the inventoried client surface.**

The Phase 15 work established and tested a device-connectivity signal, a separate API communication observation, safe stale-state presentation for patient detail, restrictions on editing unconfirmed patient data, clearing the displayed patient snapshot after applicable authorization/not-found failures, and explicit boundaries for local persistence.

A source audit inventoried 28 tracked Dart files under `mobile/lib` at the recorded checkpoint. The resulting design did not approve general persistent caching of patient, clinical or financial records.

The current application must continue to distinguish in-memory stale display from a durable offline cache. Existing in-memory data must not authorize a mutation. The original Phase 15 checkpoint recorded 182 Flutter tests passed on 13 October 2026. Later overall verification supersedes that test count for the complete current Flutter suite.

## 4.4 Phase 16 — offline queue and idempotency

**Status: IMPLEMENTED in the current development line; related security and endpoint-contract gates remain relevant.**

The offline queue provides durable operation identity and lifecycle metadata, including operation IDs, idempotency keys, owner and clinic context, scheduling and failure classification. The queue design supports bounded retry behavior and distinguishes ordinary retryable failures from situations requiring reconciliation, review or user intervention.

The queue is not permission to replay arbitrary writes. Stable operation identity and a server-supported idempotency contract are required before a repeat delivery can be considered safe.

## 4.5 Phase 17 — synchronization and replay

**Status: IMPLEMENTED in the current development line; endpoint-by-endpoint rollout verification remains open.**

The mobile synchronization design connects queued local operations to authoritative backend processing through a replay mechanism. Replay must respect the currently authenticated user and clinic context, preserve stable operation identity, revalidate ownership before decrypting or delivering retained work, distinguish request failures from unknown operation outcomes, use bounded retries and backoff where appropriate, avoid blind repeat delivery of potentially completed clinical or financial actions, and preserve unresolved work for reconciliation or review.

Every replayable operation still requires a known, tested backend contract for authorization, idempotency and outcome reconciliation.

## 4.6 Phase 18 — conflict resolution

**Status: IMPLEMENTED in the current development line; complete contract and scenario coverage remain an audit gate.**

The synchronization workflow distinguishes ordinary retryable failures from uncertain results and reconciliation-required conditions. The system must not silently overwrite authoritative server data or treat a lost response as proof of failure. A conflict must produce a deterministic, tested disposition: safe retry, reconciliation, review or explicit user intervention.

Additional endpoint-specific scenarios must be verified before production rollout.

## 4.7 Phase 19 — offline sensitive-data security

**Status: Slices 1–5 implemented and tested to their recorded scope. Design accepted. Platform evidence obtained. Production rollout gates remain open.**

Phase 19 was divided into five slices:

1. Security inventory and architecture design.
2. Protected local storage and credentials.
3. Retention, expiration and deletion.
4. Session invalidation and logout lifecycle.
5. Device security and final exit gate.

### Storage and cryptographic protections

The queue payload layer uses AES-256-GCM application-level encryption. Payload records include cryptographic metadata such as nonce, authentication tag, scheme and key version. The encryption key is obtained through the platform-backed secure-storage abstraction. Decryption fails closed when the required key or valid authentication data is unavailable.

Encrypted payloads are bound to operation and user/clinic context through authenticated data. Ordinary queue metadata is not encrypted by this payload-encryption layer.

Optional appointment `reason` and `notes` fields are excluded from newly generated persisted offline payloads by default. Existing legacy payloads were not rewritten as part of this change.

**AES-256-GCM payload encryption is not whole-database SQLite encryption.** Protection against every compromised-device scenario has not been established.

### Retention and unresolved operations

The selected policy establishes:

- A 30-day retention period for terminal queue operations, measured from the terminal transition.
- Transactional deletion of eligible terminal queue entries and related payloads.
- Unresolved operations older than the 30-day review threshold are excluded from automatic replay and made available for review.
- An unresolved operation is not silently deleted merely because it has aged.
- Destructive discard requires the documented authenticated context and disposition rules.

The retention policy still requires applicable organizational, legal, contractual and audit approval before rollout.

### Session and account lifecycle

The current source includes protections for authenticated HTTP 401 invalidation, logout and immediate invalidation of sensitive session/view state, pausing and awaiting active replay during logout or context changes, stale sign-in race conditions, refreshed session user/clinic context changes, prevention of continuing a previous user's sensitive view after a relevant context transition, and preserving queued work and its keys across ordinary logout where recovery remains required.

Session removal and queued-operation deletion are different lifecycle actions. Ordinary logout must not accidentally destroy pending work or keys needed to recover it. Dependency-aware key cleanup checks whether retained payloads still require a key. Cleanup failure must remain visible and recoverable; it must not be reported as successful deletion.

### Final verification evidence

The final Phase 19 verification recorded:

- Full Flutter test suite: **318 passed**.
- `flutter analyze --no-pub`: **no issues found**.
- Focused local database regression: **9 passed**.
- Android 15 (API 35) Pixel 4 emulator secure-storage integration: **1 passed** for write, read through a second adapter, overwrite and delete.
- Android Drift database close/reopen integration: **1 passed** for metadata persistence and schema version 3.
- Android backup manifest/XML source validation: **passed** for the configured backup references and expected exclusions.
- Formatting, applicable analyzer checks, diff checks, exact commit scope and push/remote-SHA verification: **passed**.

The original Phase 19 implementation commit was `9f1dd0a62c031bfcae5e50ad7d36d9c66ed15b3a`. PR #30 subsequently included the CI-test correction and was merged on 17 October 2026. The PR head at merge was `dbbaab282ea6d18f45cf1d8d357ef2437f07d1b5`; the merge commit is `6a16a3d2bb7c1ab6bfbcd339d5afefb60f72b97d`. The merged PR is at https://github.com/strykachiboy-art/clinic-system-pro/pull/30.

The merge does not close the remaining rollout gates. These results are development and emulator evidence, not production-operation evidence.

### Remaining Phase 19 limits and gates

The current evidence does not establish actual Android backup/restore or device-to-device transfer behavior, physical-device behavior across supported Android versions, uninstall/reinstall recovery scenarios, every secure-key loss/migration/cleanup-failure path, hardware-backed key guarantees in every supported environment, protection against a rooted or otherwise compromised device, whole-database SQLite encryption, or production operation.

Before rollout, verify endpoint-specific backend idempotency and reconciliation contracts, destructive-cleanup authorization and server disposition, recoverable cleanup-failure paths, and all applicable retention-policy approvals. Additional platform evidence may be required by the intended rollout and compliance review.

The detailed security inventory is maintained at `docs/architecture/phase-19-offline-sensitive-data-inventory.md`. The accepted storage/offline architecture record is maintained at `docs/architecture/ADR-001-mobile-storage-and-offline-boundaries.md`.

# 5. PHASE ROADMAP

The following phases preserve the original 10–34 sequence. Current implementation status is included where evidence is available; planned work is not promoted to complete status without verification.

---

# PHASE 10 — FLUTTER FOUNDATION

**Status: Foundation exists; use the current source as the implementation baseline.**

Maintain and verify Flutter project structure, environment and application configuration, routing and navigation, state management and dependency injection, shared API transport and error handling, platform-backed secure storage, Drift/SQLite schema and migration infrastructure, test conventions and reusable test utilities, development configuration and safe handling of secrets.

Exit requirements for any remaining foundation work:

- Correct configuration for each supported environment.
- Deterministic navigation and error states.
- No sensitive values in logs or diagnostic messages.
- Migration tests for new database changes.
- Focused tests and analysis pass.
- No unnecessary second client architecture.

Do not rebuild working infrastructure without evidence of a defect or a documented need.

# PHASE 11 — FLUTTER AUTHENTICATION AND SESSION

**Status: Authentication/session functionality exists; Phase 19 adds and verifies important lifecycle safeguards.**

Maintain and verify authentication workflows already supported by the backend, Google authentication where required, safe session/token storage, session restoration and refresh behavior, logout and token invalidation, expired/rejected-session handling, user and clinic context changes, stale authentication response protection, navigation and mounted-screen invalidation, request authorization and error handling.

Acceptance requirements:

- A rejected session cannot continue to present an authorized state.
- Stale login results cannot replace a newer session.
- A user/clinic context switch cannot reuse old authorized views or replay work under the wrong context.
- Credentials must not fall back to plaintext persistence.
- Authentication failure must not be reported as success.

# PHASE 12 — FLUTTER CORE CLINICAL WORKFLOWS

**Status: Existing workflows are present; audit each domain for actual mobile feature coverage before declaring full parity.**

Cover applicable workflows for patients, appointments, consultations, laboratory, pharmacy, prescriptions, billing, ambulance, ward and other supported clinical operations. Every workflow must follow backend authorization and validation.

Acceptance requirements:

- Clear loading, empty, error and success states.
- Explicit treatment of stale or unconfirmed data.
- No unauthorized use of retained patient views.
- No client-side substitution for clinical or financial business rules.
- No unsafe automatic duplicate submission.
- Appropriate tests for authorization failures, invalid input and uncertain responses.

Do not assume every backend module is already available in the Flutter client. Verify each one.

# PHASE 13 — FLUTTER FEEDBACK

**Status: Retain existing implementation; validate current feature scope before adding new work.**

Provide the applicable client flows for feedback creation and browsing, authorized detail and lifecycle visibility, comments or reactions where supported, status presentation, validation and error handling, and user-visible outcome reporting.

Use the backend Feedback API as the authority for permitted actions and workflow state. Offline queueing must use the common queue and security design. Do not build a separate feedback retry framework.

# PHASE 14 — FLUTTER CHAT AND REALTIME

**Status: Retain existing implementation; verify feature coverage, reconnect behavior and current integration tests before phase closure.**

Maintain the applicable functionality for conversations and participants, message display and submission, realtime events, unread state, reactions, mentions and attachments where supported, reconnect behavior, message authorization and clinic scoping, and appropriate error handling.

Acceptance requirements:

- Reconnection must not silently duplicate messages.
- Realtime presence is not proof that a write was accepted.
- Protected chat content must not be exposed across clinic or participant boundaries.
- Sensitive notification and diagnostic content must be minimized.
- The mobile client must reuse the backend's existing chat and authorization rules.

# PHASE 15 — OFFLINE-FIRST FOUNDATION

**Status: VERIFIED LOCALLY for the inventoried mobile surface.**

Keep the following boundaries:

- Device connectivity is separate from backend reachability.
- API response receipt is separate from business-operation success.
- Stale data is visibly identified.
- Unconfirmed patient information cannot be treated as authoritative.
- Sensitive data is not persisted just because a workflow could benefit from offline access.
- Persistent caches require a documented category-specific approval.
- Retention and deletion requirements are established before adding persistent state.

Do not convert the currently inventoried in-memory patient snapshots into a general-purpose persistent cache without a new design and security review.

# PHASE 16 — OFFLINE QUEUE AND IDEMPOTENCY

**Status: Implementation exists; endpoint contracts and deployment conditions remain verification gates.**

Maintain stable operation identifiers, idempotency keys, durable queue metadata and approved payloads, bounded retries and backoff, duplicate detection, ownership and clinic context, explicit lifecycle and failure classification, safe persistence and recovery.

Required failure categories include automatic recovery, retryable, reconciliation required, user intervention required, and fail closed.

A retry must not create a new logical operation when it is meant to continue an existing one. Do not replay an unsafe clinical or financial action without a known endpoint contract establishing how duplication and uncertain outcomes are handled.

# PHASE 17 — SYNCHRONIZATION

**Status: Replay and synchronization mechanisms exist; complete contract-level evidence remains a rollout gate.**

Maintain synchronization between the local operation queue and authoritative backend state.

Required behavior:

- Revalidate the active user and clinic.
- Confirm ownership before decrypting or sending retained payloads.
- Use the existing operation identifier and idempotency key.
- Handle request timeouts and lost responses distinctly.
- Avoid unbounded automatic retries.
- Preserve unresolved operations for investigation.
- Report failures without exposing protected payloads.
- Pause safely during logout and context changes.
- Make server outcomes visible to the user when necessary.

A local acknowledgement is not authoritative confirmation that the backend committed a business operation.

# PHASE 18 — CONFLICT RESOLUTION

**Status: Reconciliation and unresolved-outcome mechanisms exist; confirm the full operation-type contract before rollout.**

Define deterministic behavior when local and server outcomes differ. The client must avoid silently overwriting authoritative server data, distinguish a true conflict from a temporary transport failure, preserve uncertain work until its outcome is known or explicitly disposed, prevent retry loops where the original action might already have succeeded, present meaningful review information, and record or retrieve an appropriate server disposition before destructive cleanup.

No automatic rule may discard unresolved clinical or financial work merely because it is inconvenient to retain.

# PHASE 19 — OFFLINE SENSITIVE-DATA SECURITY

**Status: Slices 1–5 implemented and tested to their recorded scope; design accepted; production gates remain open. PR #30 merged.**

Maintain the accepted security design and tested behavior:

- Source-level sensitive-data inventory.
- Approved storage boundaries.
- AES-256-GCM encryption of queued operation payloads.
- Secure-storage handling of small secrets and encryption keys.
- Context-bound decryption and fail-closed behavior.
- Default exclusion of optional appointment reason/notes from new offline payloads.
- 30-day terminal queue retention policy and transactional purge.
- Aged unresolved operations excluded from automatic replay and available for review.
- Explicit authorization and disposition requirements for destructive discard.
- Dependency-aware encryption-key cleanup.
- Session and navigation invalidation.
- Replay pause-and-wait during logout/context transitions.
- Android backup exclusions at source-configuration level.
- Integration evidence for specified emulator scenarios.

Keep the open gates visible:

- Legal, organizational and contractual approval of the retention policy.
- Endpoint-specific backend idempotency and reconciliation verification.
- Authorization and server-disposition verification for destructive cleanup.
- Cleanup-failure recovery.
- Actual backup/restore, device transfer and uninstall/reinstall evidence where required.
- Physical-device and key-lifecycle verification.
- Review of any additional platform or compliance requirements.

No production-readiness or whole-database-encryption claim is permitted on the basis of the current Phase 19 evidence alone.

# PHASE 20 — FLUTTER BREAK-GLASS AND CONSENT

**Status: Planned unless separate current source evidence establishes implementation.**

Integrate the backend's emergency access and consent behavior into the mobile client.

Scope: emergency access request, reviewer decision visibility where authorized, scope and expiration display, countdown/expiry behavior, consent presentation and acknowledgement where applicable, restricted emergency screens, revocation and denied/expired states, and audit-aware user experience.

Acceptance requirements:

- The server decides whether emergency access is allowed.
- A client-provided role or flag cannot grant emergency access.
- Scope and time limits remain enforced by the backend.
- Emergency access must not bypass clinical safety controls.
- The user must understand the restricted nature of the access.

# PHASE 21 — FLUTTER CLINICAL SAFETY

**Status: Planned unless separately verified.**

Integrate the mobile experience with the existing backend Clinical Safety subsystem.

Scope: clinical alerts, medication warnings, contraindication-related outcomes, required safety confirmations, blocking conditions, safe acknowledgement flows and alert lifecycle visibility where authorized.

Acceptance requirements:

- The backend remains authoritative for safety decisions.
- The Flutter client must not create a competing clinical safety engine.
- Blocking safety conditions cannot be bypassed through local state.
- Critical warnings must not disappear silently on refresh, navigation or reconnect.
- Safety outcomes must be handled correctly when the device is offline.

# PHASE 22 — FLUTTER NOTIFICATIONS

**Status: Planned unless separately verified.**

Implement the applicable notification experience: push notifications, in-app notification center, user preferences, secure deep links, workflow notifications, read/acknowledgement states where supported, and expired or unauthorized target handling.

Acceptance requirements:

- A notification is not proof that the underlying operation succeeded.
- Deep links require normal authorization after navigation.
- Notification previews must minimize sensitive patient and clinical content.
- A stale notification cannot independently authorize access.
- Delivery retry and duplicate behavior must be deliberate.

# PHASE 23 — DEVICE AND SESSION MANAGEMENT

**Status: Planned unless separately verified.**

Implement the applicable user experience for current-session visibility, device/session listing where supported by the backend, remote session revocation, secure logout, session-expiry explanations, reauthentication, account and clinic switching, and recovery of retained queue data after an ordinary logout.

Acceptance requirements:

- A revoked session is not treated as valid because tokens remain locally stored.
- Session revocation must follow the backend contract.
- Logging out must not cause unintended deletion of recoverable queued operations.
- Account switching must enforce operation ownership and clinic boundaries.

# PHASE 24 — FLUTTER UNIT AND INTEGRATION TESTING

**Status: Continuous testing is already present; expand coverage as later features are built.**

Test the applicable widgets, state management, API client, authentication and session transitions, secure-storage adapter, Drift database and schema migrations, queue stores and encryption, replay scheduler and worker, retention and deletion, conflict and reconciliation workflows, routing and stale-view invalidation, and platform-specific integration behavior.

Every defect fix should add or strengthen a test for the underlying behavior. Do not weaken a valid regression test simply to obtain a green result.

# PHASE 25 — FLUTTER OFFLINE AND RESILIENCE TESTING

**Status: Build on the existing failure-handling tests; complete wider offline/device scenarios before release.**

Test conditions including airplane mode, weak connectivity, high latency and jitter, packet loss, API timeouts, connection failures, HTTP 5xx responses, a request processed by the server whose response is lost, session expiry during replay, logout while replay is active, clinic or user context changes, duplicate submissions, interrupted synchronization, application termination and restart, database migration failure, missing or invalid encryption keys, key cleanup failure, conflicting server changes, and reconciliation after an unknown outcome.

Each scenario must produce a defined disposition rather than an ambiguous success or a silent data loss.

# PHASE 26 — FULL CROSS-PLATFORM END-TO-END

**Status: Planned as a comprehensive mobile release gate.**

Validate the complete system across its applicable components: Flutter, web client, Flask backend, PostgreSQL, Redis, Celery/background workers, Socket.IO/realtime delivery, and external provider integrations where applicable.

Verify complete user journeys, authorization boundaries, session invalidation, operation submission, persistence, replay, conflict handling and final outcomes. A collection of isolated unit tests is not a substitute for an integrated system test.

# PHASE 27 — PRODUCTION SECURITY TESTING

**Status: Planned as a dedicated release gate; existing backend security work is a prerequisite, not a substitute.**

Perform the required review of authentication and refresh behavior, authorization and IDOR, privilege escalation, cross-clinic access attempts, token and credential exposure, insecure local storage, sensitive-data leakage through logs, deep-link abuse, session races, queue ownership and context changes, operation replay and idempotency, encryption-key lifecycle, destructive cleanup authorization, offline/online transitions, rooted or compromised-device assumptions where relevant, and build configuration and release secrets.

Findings must be resolved or explicitly dispositioned by the appropriate security owner before release approval.

# PHASE 28 — BACKUP AND RESTORE DRILL

**Status: Backend restore-drill implementation and controlled execution are evidenced; mobile device-backup scenarios remain separate.**

The existing backend work includes a controlled restore drill covering a separate PostgreSQL target and filesystem target, restore confirmation and validation, authentication invalidation, outbox recovery and post-restore checks. This backend operational drill is not proof that Android backup/restore, device transfer or uninstall/reinstall behavior has been tested.

The mobile release checklist must separately establish the behavior of Android backup exclusions, device-to-device migration where applicable, uninstall/reinstall, database recreation and migration, secure-storage persistence and loss, recovery of retained queue data, and missing encryption keys and unresolved payloads.

The client must not claim recovery is possible when the required payload key is unavailable.

# PHASE 29 — RELEASE CANDIDATE FREEZE

**Status: Planned. Current Phase 19 completion does not mean the full application is release-ready.**

Freeze a release candidate only after the applicable requirements are met:

- Backend regression.
- Flutter test suite and static analysis.
- Database migration validation.
- Cross-platform integration/E2E.
- Offline and resilience testing.
- Security review.
- Backend restore-drill evidence.
- Mobile backup/device review where required.
- Credential and build-configuration review.
- Clinical safety and emergency-access verification.
- Retention policy and data lifecycle approval.
- Required documentation and open-risk review.

After freeze, only approved critical fixes should enter the candidate. Each fix requires targeted tests and a suitable regression gate.

# PHASE 30 — DEPLOYMENT

**Status: Planned; no production-deployment claim is made by development verification.**

Deploy according to the approved production architecture. Requirements include secure deployment configuration, TLS, protected secrets and encryption keys, database migration and rollback strategy, database and filesystem capacity, Redis and worker readiness, observability and alerting, backup arrangements, tenant isolation, mobile signing and release configuration, controlled distribution and release approvals.

Deployment must not begin until the applicable operational and compliance gates are approved.

# PHASE 31 — POST-DEPLOYMENT VALIDATION

**Status: Planned.**

After deployment, validate in the actual target environment: health checks, authentication and refresh, database connectivity and migrations, Redis and background workers, realtime delivery, core clinical workflows, notifications if enabled, queue processing and uncertain outcomes, session invalidation and logout, backup and recovery mechanisms, logs, metrics and alerts, and mobile release installation and startup.

Development tests and emulator results must not be presented as substitutes for this operational evidence.

# PHASE 32 — PRODUCTION OPERATIONS

**Status: Planned until the system is actually deployed and operational procedures are demonstrated.**

Establish repeatable operational processes for monitoring, incident response, backup and restore, security patches, dependency updates, credential and key management, audit review, performance monitoring, database and queue health, failed-operation investigation, user support, release management and operational reporting.

Production operation is an ongoing responsibility rather than a one-time deployment task.

# PHASE 33 — FUTURE DISTRIBUTED SCALE

**Status: Deferred until sufficient measured evidence justifies it.**

Potential future areas include service decomposition, distributed workloads, advanced caching, queue scaling, regional deployment, read replicas, distributed event processing and additional observability infrastructure.

No architecture expansion should be justified by speculation alone. Use measured bottlenecks, traffic, reliability evidence and explicit operational requirements.

# PHASE 34 — WHITE GLOVE / RUST

**Status: Future initiative; not a current Flutter prerequisite.**

The White Glove/Rust initiative remains a late-stage optimization or service-evolution project. Potential areas may include performance-critical processing, specialized workers or high-throughput workloads, but only where evidence demonstrates meaningful benefit.

The existing Python backend remains the primary application platform unless a documented technical evaluation supports a change. Introducing Rust merely because it is possible is not a valid reason.

---

# 6. PHASE EXIT AND CHANGE CONTROL

Every development phase must have explicit entry conditions, scope, acceptance criteria and evidence.

## Before changing code

1. Confirm the repository, branch and exact HEAD.
2. Inspect the relevant current source and tests.
3. Confirm the worktree and identify unrelated changes.
4. State the exact files and behavior in scope.
5. Record preconditions and test expectations.
6. Identify security, data lifecycle and backward-compatibility implications.

Do not patch from an old plan or old code excerpt without checking the current repository.

## During a slice

- Keep the slice small and independently testable.
- Preserve existing contracts unless a deliberate change is approved.
- Use SQLAlchemy/Drift/database patterns already established by the relevant subsystem.
- Avoid broad replacements and unrelated refactoring.
- Do not weaken tests, authorization checks or security assertions.
- Preserve unrelated user changes and generated/local files.
- Add regression tests for the specific failure mode.
- Use focused tests first; run broader regression gates at the agreed phase boundaries.

## Before commit

Verify that the current branch is correct, the expected HEAD has not changed unexpectedly, only approved files are staged, source and tests compile/analyze, applicable focused tests pass, `git diff --check` passes, the staged diff has been reviewed, no secrets or unrelated artifacts are included, and commit parent and scope are correct.

Push only after the commit lock passes. Confirm the remote branch SHA matches the intended commit. A pushed branch is not the same as an opened PR, and an opened PR is not the same as a merged PR.

# 7. LOCAL DEVELOPMENT AND HARDWARE REALITY

Development has been performed on a constrained Windows device. The current workflow uses system Python and PowerShell, and Flutter commands are run against the installed Flutter SDK.

Do not assume a project virtual environment exists. Do not assume the machine can run all containers and emulator workloads simultaneously. For backend work, use the documented repository-specific environment variables and disposable test services required by the relevant gate.

For Flutter work:

- Use `mobile/` as the Flutter package root.
- Use `flutter test` for the applicable test suite.
- Use `flutter analyze --no-pub` for the established analysis gate.
- Use focused integration tests when checking platform behavior.
- Confirm connected emulator/device IDs before execution.
- Keep generated build output out of the commit unless explicitly required.

For long regression suites, choose a safe execution order that accounts for memory, CPU, database and emulator resources without compromising the acceptance criteria.

# 8. DOCUMENTATION AND SOURCE-OF-TRUTH RULES

Use the following priority when documents disagree:

1. Current source on the exact branch and commit.
2. Current, reproducible test or integration evidence.
3. Accepted architecture records and reviewed security inventories.
4. Current repository roadmap and README.
5. Older plans, status summaries and initial estimates.

The master plan preserves goals and intended phase sequencing. It must not override source code or current verification evidence.

When implementation status changes:

- Update the relevant phase status.
- Record the evidence and its exact scope.
- Identify what remains unverified.
- Update the roadmap and relevant architecture documentation.
- Keep dates and commit references accurate.
- Remove stale contradictory claims only after inspecting the current text.
- Never label development evidence as production operation.

Phase status should distinguish implemented behavior, local verification, emulator/platform verification, open gates and real operational evidence.

# 9. CURRENT PRIORITIES

As of 17 October 2026, the immediate sequence is:

**Priority 1 — Add and review this master plan in the repository.**

Phase 19 PR #30 has already been merged into `main` on 17 October 2026. Its merge commit is `6a16a3d2bb7c1ab6bfbcd339d5afefb60f72b97d`; its head at merge was `dbbaab282ea6d18f45cf1d8d357ef2437f07d1b5`. Do not reopen or recreate that PR. Add this master plan as a separate documentation-only change, based on the latest `origin/main`, with exact-file staging and its own review.

**Priority 2 — Reconcile the master plan with current source and phase records.**

Inspect current source and phase records before making further roadmap claims. In particular, reconcile older statements about Flutter being a placeholder, the current state of Phases 10–19, the Phase 19 final verification evidence, and the accepted status of ADR-001. Keep documentation work separate from the already-merged Phase 19 implementation unless a deliberate, reviewed follow-up change is required.

**Priority 3 — Close remaining Phase 19 rollout gates.**

Review retention-policy approval, endpoint-level idempotency/reconciliation, destructive cleanup authorization and disposition checks, cleanup-failure recovery, and any additional platform evidence required for the intended rollout. Do not represent these as resolved merely because the focused tests pass or PR #30 was merged.

**Priority 4 — Proceed to Phase 20 only after scope and entry conditions are agreed.**

Inspect the existing backend break-glass and consent contracts, identify the exact Flutter source anchors, define tests, and implement small verified slices. Do not redesign or duplicate the backend's authorization or clinical safety logic.

**Priority 5 — Keep release preparation evidence-driven.**

Phases 24–29 establish the wider test and release gates. Phases 30–32 require actual deployment and operational evidence. Phases 33–34 remain future work rather than current release prerequisites.

# 10. FINAL PROJECT RULES

1. The backend remains authoritative.
2. Offline mode never bypasses authorization.
3. Sensitive data is not persisted without an approved need and design.
4. Secrets never fall back to plaintext storage.
5. Unknown outcomes are reconciled before unsafe retry.
6. User and clinic ownership is checked before replay.
7. Logout invalidates sensitive session state without accidentally destroying recoverable queued work.
8. Database and key cleanup must be explicit, dependency-aware and recoverable.
9. Tests must prove the stated behavior without weakening safeguards.
10. Implementation, verification, platform evidence and production operation remain separate statuses.
11. Every phase is closed by reviewed evidence, not by a heading in a plan.
12. Existing working architecture is reused unless source inspection establishes a reason to change it.
13. Unrelated changes and local artifacts must be preserved.
14. Every security-sensitive change must have a clear threat model, acceptance criteria and rollback/recovery expectations.
15. The current repository is the source of truth for what exists today; this master plan is the source of the intended roadmap.

---

**END OF MASTER PLAN**

**Status note:** This is the refined working roadmap as of 17 October 2026. It includes the Phase 15–19 implementation and verification evidence presently available, reflects the merge of Phase 19 PR #30, and preserves open rollout gates and the planned Phase 20–34 sequence. Phase-level completion claims for older mobile workflows should be reconciled against current source and recorded gates before the plan is treated as final repository documentation.
