# Phase 19 — Offline Sensitive-Data Security Inventory

- Review date: 2026-10-16
- Reviewed source branch: `feat/phase19-offline-data-security`
- Reviewed mobile source baseline: `55daab92f3c68dafdadd1198f306e5fb9e6f8ec8`
- Architecture record: [ADR-001: Mobile Storage Architecture and Offline Boundaries](ADR-001-mobile-storage-and-offline-boundaries.md)
- Status: Gate 1 design accepted on 2026-10-16. ADR-001 is Accepted as the Phase 19 offline-storage and lifecycle design baseline. Remaining runtime implementation and verification, platform evidence, and rollout blockers for the wider Phase 19 plan remain outstanding.
- Slice 2 status: scoped runtime protections and focused local verification are complete, subject to the legacy-payload and platform limitations recorded in Section 7.

## 1. Purpose and boundaries

Phase 19 defines the security and lifecycle boundaries for sensitive local data in the Flutter mobile application. This inventory records what the reviewed source does, where data resides, which protections exist, and which policies need enforcement or platform evidence.

The five Phase 19 slices are:

1. Security inventory and design.
2. Protected local storage and credentials.
3. Retention, expiration and deletion.
4. Session invalidation and logout lifecycle.
5. Device security and final exit gate.

This document distinguishes **source-observed behavior**, **selected design policy**, **implemented behavior**, **locally verified behavior**, **platform-verified behavior**, and **production operation**. These statuses are not interchangeable.

The backend remains authoritative for authentication, authorization, clinic membership and clinical or financial state. Local presence of a record, session token, queue entry or encryption key does not grant continuing authorization.

The inventory does not approve general persistence of patient data, clinical notes or financial records. It does not establish whole-database SQLite encryption. The encrypted operation payload is a distinct layer from the SQLite database that contains the ciphertext.

## 2. Source inventory

| Data asset | Classification and owner | Current location and purpose | Current lifetime and controls | Gap or required decision |
|---|---|---|---|---|
| Access token and refresh token | Authentication secrets belonging to the authenticated user/session | JSON session record in `SecureKeyValueStorage`; token models and request arguments also exist in memory. The bearer token is passed as a GoRouter route argument to patient directory, registration and detail screens and held in their widget properties. | The secure-storage record is retained while locally authenticated. The session coordinator clears local session during logout and on definitive refresh-token rejection. | A locally restored record does not establish current server validity. Test expiry, definitive rejection, local deletion failure, mounted screens and every route retaining a token. |
| User ID, role, clinic context and refresh marker | Sensitive session and authorization metadata | Stored alongside tokens in the secure session record and represented in application memory. Used to restore session state and scope replay. | Refresh-in-progress metadata is persisted to support recovery. Account switching is expected to require ending the old session first. | A general route-level invalidation guarantee is not established by the session record. Account or clinic changes must not leave old patient views visible or permit cross-context replay. |
| Google ID token | Short-lived authentication credential | Acquired and passed to the Google-mobile login exchange in memory. | No application-level persistent storage was identified in the reviewed sign-in flow. Native SDK retention is outside this source-only finding. | Keep transient. Do not add it to SQLite, `AppMetadata`, the operation queue or diagnostic messages. |
| Password-login input supported by remote data source | Email address and password supplied as authentication credentials | `AuthRemoteDataSource.login` accepts email and password and sends them to the backend. The reviewed Google sign-in screen did not establish a password UI path. | Arguments and encoded request body are transient application/request data in the reviewed path. | No password-login UI or call path was identified in the reviewed runtime entry point, router or Google sign-in screen; re-check if a password flow is wired into the app. Never persist passwords in the session record, SQLite, `AppMetadata`, the queue or logs. |
| Patient directory results and search state | Sensitive personal data belonging to patients | `PatientDirectoryScreen` holds patient models, search text, paging state and errors in widget state and controllers. | Search resets the result list. The search controller is disposed with the screen. No persistent patient cache was identified in this screen. | Authorization changes and logout need explicit route and screen invalidation. Widget disposal is not proof of physical memory zeroization. |
| Patient detail snapshot | Sensitive personal and potentially clinical data | `PatientDetailScreen` holds a `Patient` model and display/error state in memory. | Refresh failures with HTTP 401, 403 or 404 clear the displayed patient. Other failures can retain a snapshot marked stale, with editing restricted. | Verify that session invalidation removes the entire reachable sensitive view, not only a model cleared by one refresh failure. |
| Patient edit form values | Sensitive demographics and clinical details, including allergies, chronic conditions and identifiers | `PatientProfileEditScreen` creates text controllers from the patient model. Values are submitted through the remote data source in memory. | Controllers are disposed when the widget is disposed. No persistent form cache was identified in this screen. | Test mounted-form behavior when authorization changes, abandoned values, navigation state, submission uncertainty and failure paths. |
| Patient registration form and submission | Sensitive personal and potentially clinical data owned by the patient and processed in an authorized user/clinic context | `PatientRegistrationScreen` holds demographic and clinical form controllers, submission state, a created `Patient` model after success and server field errors. Values are passed to `PatientRemoteDataSource` and the request body in memory. | Controllers are disposed when the screen is disposed. No persistent registration-form cache was identified in the reviewed screen. `ApiException` retains parsed error metadata, not the complete raw response body; server-provided messages must still be treated as potentially sensitive. | Test invalidation while the form is mounted, abandoned values, navigation state and authorization changes. Do not persist form data as a recovery fallback without separate approval. |
| Offline appointment request payload | Potentially sensitive clinical and operational data owned by the originating user and clinic context | `OperationQueuePayload` stores AES-256-GCM ciphertext, nonce, authentication tag and encryption scheme/key version in SQLite. `OperationPayloadCrypto` obtains the encryption key through secure storage. | Authenticated associated data binds the encrypted payload to operation identity and user/clinic context. Decryption fails closed if the key is missing or authentication fails. | Approved design decision: exclude optional `reason` and `notes` from persisted offline payloads by default because they may contain sensitive clinical information. Later inclusion requires documented workflow necessity and explicit approval, followed by review of protection, access, retention and deletion requirements. The producer now omits both fields from new payloads; focused tests assert their absence from `toPayload()` and the decrypted queued payload. Existing legacy queued payloads are not rewritten. Payload encryption does not encrypt the complete SQLite database. |
| Queue metadata | Operational identifiers and ownership/lifecycle metadata | `OperationQueueEntry` stores operation ID/type, idempotency key, owner, clinic, state, attempts, timestamps, next-attempt time and classified failure category. | Ready selection is scoped to the active owner/clinic and due queued or retry-scheduled entries with payloads. Unknown outcomes and reconciliation states have explicit lifecycle values. | Metadata is not encrypted by the payload encryption layer. Implementation and verification remain outstanding for the selected terminal-retention, unresolved-review and authorized-discard controls in Section 5. Keep metadata minimized and privacy expectations documented. Never store raw errors or clinical payloads in queue metadata. |
| Per-context encryption key | Secret material required to recover encrypted queue payloads | Obtained through `SecureKeyValueStorage` using a key name derived from context identifiers rather than storing raw identifiers in the key name. | The reviewed payload crypto uses key version 1 and fails closed when a key is absent or invalid. No general key deletion or rotation method was identified in the reviewed source. | Implement and verify dependency-aware key cleanup and recoverable failure reporting as specified in Section 5.4. The reviewed implementation uses key version 1 and does not establish rotation or multi-version migration; never delete a key needed by retained data. |
| `AppMetadata` | Non-sensitive local application metadata only | Drift table containing key, value and optional update timestamp. | Database comments explicitly prohibit authentication secrets and clinical or financial records. | Keep this restriction. Do not use the table for tokens, patient snapshots, form drafts, plaintext queue payloads or encryption keys. |
| Decrypted payloads and temporary request values | Potentially sensitive plaintext, including patient or clinical fields | Created transiently when encrypted operation payloads are decrypted and when API request bodies are assembled. | Application-owned references are expected to remain transient; ordinary Dart memory management does not guarantee secure zeroization. | Minimize lifetime and copies. Do not log plaintext or introduce new persistent copies. Clear application-owned references where practical after use. |
| API exceptions and server validation errors | Potentially sensitive operational metadata; may echo user-supplied values | `ApiException` can retain kind, message, URI, status, cause, parsed field errors, server message and server code. API request bodies and bearer tokens exist transiently in the networking path. | `toString()` returns kind, message and optional status, not URI, cause, parsed details or raw body. A mocked HTTP 422 regression at `55daab9` checks sentinel redaction while parsed UI details remain accessible. Native, plugin, transport and OS diagnostics were not verified. | Treat server-supplied messages and field values as untrusted and potentially sensitive. Preserve safe diagnostic formatting and avoid credentials, clinical payloads and raw response bodies in logs or persisted diagnostics. Native, plugin and OS logging requires separate verification. |

## 3. Storage boundaries

### Platform-backed secure storage

`SecureKeyValueStorage` is the boundary for small secrets, including the local session record and payload encryption keys. It is not a general-purpose database or sensitive-cache abstraction.

The reviewed Android secure-storage adapter specifies `resetOnError: false` and `migrateOnAlgorithmChange: true`; failures are intended to propagate rather than trigger silent deletion. This is source evidence, not proof of secure-storage behavior on a physical device or across every platform lifecycle.

Session cleanup and key cleanup are different operations. Clearing a session must not implicitly destroy queue data needed for later recovery. A secure-storage deletion failure must not be reported as successful cleanup.

### SQLite and Drift

`AppDatabase` is configured as `clinic_system_local` and has schema version 3. It contains:

- `AppMetadata`, restricted to non-sensitive application metadata.
- `OperationQueueEntry`, containing queue lifecycle and ownership metadata.
- `OperationQueuePayload`, containing ciphertext, nonce, authentication tag, encryption scheme and key version.

The payload table has a foreign-key relationship to its queue entry with cascade deletion. Queue deletion and payload deletion must still be tested together under the actual database transaction and failure path.

AES-256-GCM protection of the queue payload is application-level payload encryption. It is **not whole-database SQLite encryption**. Queue metadata, SQLite structure, database side files, device backups and a compromised runtime require separate consideration.

### In-memory UI and authentication flows

Patient directory/detail models, form controller values, authentication tokens, request arguments and decrypted payloads can exist in process memory. Clearing a model or disposing controllers is useful lifecycle hygiene but does not guarantee physical zeroization.

Logout, definitive authentication rejection, account changes and clinic-context changes need a common invalidation behavior so mounted views and navigation state cannot continue exposing data through a stale authorization context.

### Backend boundary

The backend is authoritative for identity, authorization, clinic membership and server-owned clinical/financial state. A client-side queue state is not evidence that a remote mutation succeeded or failed.

A transport timeout or missing response can leave an unknown server outcome. Before repeating an operation with a possible server-side effect, the client must establish that replay is safe under the operation's actual backend idempotency/reconciliation contract.

The reviewed client source does not establish a universal backend idempotency-key retention period. The local 30-day queue policy must not be treated as proof of server-side deduplication for 30 days.

## 4. Threat model

The inventory considers these failure and misuse scenarios:

- A former session leaves sensitive screens, forms or route arguments accessible after logout, token rejection, user switching or clinic switching.
- A queue entry is accessed or replayed under another user's or clinic's context.
- A retry after timeout duplicates a server-side action whose first outcome is unknown.
- Sensitive payload fields are persisted without a demonstrated need or leak into metadata, logs, exception messages or diagnostics.
- A database purge succeeds but a required key deletion fails, or a key is deleted while retained ciphertext still depends on it.
- A storage or migration error is mistaken for successful deletion or triggers a silent plaintext fallback.
- A backup, restore, reinstall or device-transfer path retains or exposes data despite source-level backup exclusions.
- A rooted, compromised or otherwise untrusted device accesses application data at runtime.

The required response is fail-closed where authorization, payload integrity, replay safety or cleanup completion cannot be established. This document does not claim that all threats are currently mitigated.

## 5. Accepted lifecycle and retention decisions

**Slice 1 design status:** Accepted on 2026-10-16 as the Phase 19 architecture baseline. The lifecycle rules below are approved design requirements, not evidence that runtime enforcement exists, has passed tests, or has operated in production.

### 5.1 Terminal queue retention

- Retain `acknowledged`, `rejected` and `cancelled` queue records and their associated encrypted payloads for **30 days after the terminal transition**, measured from the terminal record's `updatedAt`.
- After that period, terminal records are eligible for the existing transactional purge operation. The purge must delete the associated payload and queue record atomically. A failed transaction must not be reported as successful deletion.
- This is a **local retention policy**. It does not cancel a server-side action, authorize replay, or establish how long the backend remembers an idempotency key.
- The current purge method accepts a caller-supplied cutoff and does not itself establish a retention duration or run automatically. A configured policy, safe caller and scheduling/trigger behavior must be implemented and tested before this rule is considered enforced.
- Before rollout, verify that the selected 30-day terminal-retention rule complies with applicable product, legal, contractual, organizational and audit requirements. Any conflict blocks rollout until formally resolved.

### 5.2 Unresolved operations: review, recovery and explicit discard

The unresolved states are `queued`, `inFlight`, `retryScheduled`, `reconciliationRequired` and `userInterventionRequired`.

- An operation that remains unresolved for **30 days from `createdAt`** must enter a review-required condition and be excluded from automatic replay until reviewed. This is an age-based review trigger, **not an automatic deletion deadline**.
- Do not silently delete unresolved metadata or encrypted payloads because of age, logout, account switching, clinic switching, an expired local token, or a transient storage/network failure.
- The current ready-operation selection does not enforce this 30-day review guard. A durable guard and focused tests are required before the policy can be considered implemented. Until that guard and the applicable replay-safety checks exist, an aged operation must not be automatically delivered merely because it is otherwise due.
- An `inFlight` operation may have reached the server before interruption. A transport failure or missing response does not prove that the server did nothing. Treat such cases as unknown outcomes and use the supported reconciliation contract before retrying an action that may already have succeeded.
- Requeueing from reconciliation requires confirmation of the outcome. Returning from user intervention requires session and current user/clinic context revalidation. Preserve the operation's stable identity and idempotency key when a repeat attempt is established to be safe.
- The reviewed client source does not establish a universal backend idempotency-key validity period. Do not infer one from the 30-day local policy. Before replaying an aged or uncertain operation, verify the backend contract for that operation type. If safe replay cannot be established, fail closed and require authoritative reconciliation or an explicitly recorded manual disposition.
- Destructive discard is permitted only to the currently authenticated original operation owner, in the original user/clinic context, with authoritative permission for that operation type and explicit confirmation. If current permission cannot be verified, fail closed.
- When a server-side effect is possible, require authoritative reconciliation or an explicitly recorded manual disposition before deletion. Local deletion must never be presented as server cancellation.
- An operation known not to have been delivered may be cancelled through a valid lifecycle transition. If reconciliation is unavailable for an operation with a possible server-side effect, any manual disposition must make the unresolved outcome and its risks explicit.
- If the required authorization, disposition record, lifecycle transition or storage cleanup cannot complete, report failure and preserve the data needed for recovery rather than claiming a successful discard.

### 5.3 Logout, authentication changes and context ownership

- Logout must wait for active replay to stop, invalidate the local session, and leave replay paused. A successful remote logout is not a prerequisite for blocking local access to the ending session.
- Clear or make inaccessible sensitive mounted screen and form state when logout, authentication rejection, account change or clinic-context change invalidates its authority. Disposal of a widget alone is not a guarantee of physical memory zeroization.
- Normal logout does **not** automatically delete the offline queue or its encryption keys. Retention and deliberate destructive discard are separate operations.
- Retained entries must remain scoped to their original owner and clinic. Before payload decryption or replay, revalidate the active session and exact user/clinic ownership; a previously valid session is not continuing authorization.
- Failure to clear local session state or invalidate sensitive views must not be reported as a fully completed logout. The application must prevent old-session replay and access while the cleanup failure remains visible and recoverable.

### 5.4 Encryption-key lifecycle and destructive cleanup

- Never delete a context/key-version key while any retained encrypted payload depends on it. Logout alone and deletion of an individual terminal operation are not reasons to remove a shared context key.
- For deliberate context-wide destruction, authorize the scope, record the disposition, and transactionally remove the selected queue entries and payloads. Before removing any key version, verify that no surviving payload depends on that context/key version.
- Database deletion and platform secure-storage deletion are separate storage operations and cannot be assumed to form one atomic transaction. Commit the authorized database purge first; only then attempt key removal when the dependency check proves that removal is safe.
- If the database transaction fails, preserve the key. If the database purge succeeds but secure-storage key deletion fails, report cleanup as incomplete and make the key-removal step safely retryable. Never claim complete destructive cleanup while a required step is unconfirmed.
- Preserve keys needed by any retained work, including payloads using older key versions. Do not delete a key to make unreadable data appear deleted.
- Normal logout, terminal-record retention purge, single-operation discard and deliberate context-wide destruction must have distinct lifecycle semantics and tests.

### 5.5 Device and backup protection

- Keep the reviewed Android backup and data-extraction exclusions as defence-in-depth controls.
- Treat manifest and XML inspection as **source-level evidence only** until behavior is verified on suitable devices and supported Android versions.
- These controls do not establish whole-database SQLite encryption, protection from rooted/compromised devices, or verified behavior across backup, restore, migration, reinstall and device-transfer scenarios.
- Do not claim those protections without matching implementation and platform-level evidence.

### 5.6 Implementation and verification gates

The accepted requirements below must be read alongside the current slice dispositions in Section 6. They are not a claim that every runtime mechanism remains unimplemented. Local implementation/test evidence does not by itself clear rollout gates.

- Preserve and regression-test the 30-day terminal-retention policy, its transactional purge, and the configured maintenance trigger. The current source contains a retention-policy wrapper, the replay worker invokes it, and the scheduler supports startup/resume maintenance.
- Preserve the default exclusion of appointment `reason` and `notes` across all payload-production paths. Any future inclusion requires documented workflow necessity and explicit approval.
- Preserve the age-based review guard that excludes unresolved work aged 30 days or more from automatic readiness. Review listing must remain scoped to the active owner and clinic and must not silently delete unresolved work.
- Verify backend idempotency and reconciliation guarantees for each replayable operation type. Unknown or unsupported guarantees must fail closed.
- Preserve confirmed-disposition discard, owner/context checks, payload/entry transactional deletion, dependency-aware key cleanup, and explicit reporting of incomplete key removal. The caller must validate the server-issued disposition and applicable authorization before invoking destructive cleanup.
- Preserve session/view invalidation on logout, definitive authentication rejection and authentication-context changes, together with replay pause/await and retained queue/key semantics.
- Keep Android manifest and XML backup exclusions as defence-in-depth controls. Integration evidence now covers two Android emulator scenarios; actual backup/restore, transfer, reinstall and other untested lifecycle behavior remain open.
### Rollout blockers and verification gates

1. Before rollout, verify that the selected 30-day terminal-retention rule complies with applicable product, legal, contractual, organizational and audit requirements. This is a deployment gate, not an unresolved choice of retention period; any conflict blocks rollout until formally resolved.
2. Identify the authoritative backend contract and validity window for each operation type's idempotency key. The local 30-day retention choice is not proof of backend deduplication for 30 days.
3. Destructive discard is permitted only to the currently authenticated original operation owner, in the original user/clinic context, with authoritative permission for that operation type and explicit confirmation. If current permission cannot be verified, fail closed. When a server-side effect is possible, require authoritative reconciliation or an explicitly recorded manual disposition before deletion; local deletion must never be presented as server cancellation.
4. Before rollout, verify that the release candidate preserves age-based retention, unresolved-operation review, authorized discard, database-failure handling and secure-storage key-removal failure handling. Local test success is not production-operation evidence.
5. The default exclusion of optional appointment `reason` and `notes` is implemented and covered by focused local tests at the reviewed source baseline. Confirm that a release candidate preserves the exclusion. Any future inclusion requires documented workflow necessity and explicit approval, followed by review of applicable protection, access, retention and deletion requirements.

## 6. Slice dispositions and final exit gate

### Slice 1 — security inventory and design

**Disposition: ACCEPTED.** ADR-001 and the sensitive-data inventory establish the approved offline-storage, ownership, retention, cleanup and lifecycle boundaries. Design acceptance does not itself authorize production rollout.

### Slice 2 — protected local storage and credentials

**Disposition: IMPLEMENTED / LOCALLY VERIFIED** for the scoped protections recorded earlier in this inventory. Platform secure storage is used for small secrets, and queued operation bodies use authenticated AES-256-GCM payload encryption with context binding. This is not whole-database SQLite encryption. Previously persisted legacy payloads were not rewritten; keep that limitation explicit.

### Slice 3 — retention, expiration and deletion

**Disposition: IMPLEMENTED IN SOURCE; preserve regression coverage and rollout limits.**

- The queue store defines a 30-day terminal-retention period measured from the terminal transition (`updatedAt`). The policy wrapper calls a transactional purge that deletes terminal entries and their encrypted payloads together.
- The replay worker invokes the terminal-retention policy before replay. The scheduler also supports configured maintenance on startup/resume. This does not promise scheduled execution while the application is not running.
- Ready-operation and next-retry selection exclude unresolved operations at or beyond the 30-day review age. A review listing is scoped to the active user and clinic; unresolved items are not deleted merely because they are old.
- The queue store implements a confirmed-disposition discard path, rejects context mismatch and acknowledged operations, deletes the entry and payload transactionally, checks key dependencies and reports pending key cleanup when secure-storage deletion fails. A separate context-key cleanup method supports retry after checking for retained payload dependencies.
- Before rollout, verify the actual backend idempotency/reconciliation contract for each operation type and confirm every destructive caller validates the server disposition and operation-specific authorization. Keep cleanup failure handling in the regression gate.

### Slice 4 — session invalidation and logout lifecycle

**Disposition: IMPLEMENTED / LOCALLY VERIFIED** in Slices 4A–4C. The committed source includes guarded patient navigation, invalidation on authenticated HTTP 401 and logout start, waiting for active replay before local cleanup completes, stale sign-in protection and rejection of refreshed sessions whose user or clinic context changes. The focused Slice 4 regressions passed at their respective gates. The queue and its keys remain retained on ordinary logout. Do not generalize these results to routes or platform flows that were not tested.

### Slice 5 — device security and final exit gate

**Disposition: ANDROID INTEGRATION TESTS PASSED; PLATFORM EVIDENCE PARTIAL.**

- On the Pixel 4 emulator reporting Android 15 (API 35), `secure_key_value_storage_integration_test.dart` passed **1 test** for write, read through a second adapter, overwrite and delete.
- On the same emulator, `app_database_integration_test.dart` passed **1 test** for metadata persistence across database close/reopen and schema version 3.
- The focused local database regression passed **9 tests** and database integration-test analysis reported no issues.
- Source validation passed for `allowBackup="false"`, both configured backup XML references, and the expected exclusions for legacy backup rules, cloud backup and device transfer.

These tests do not prove actual backup/restore, device transfer, uninstall/reinstall, physical-device behavior, hardware-backed key guarantees, every secure-key failure/migration scenario, whole-database SQLite encryption or protection against compromised devices. Record the final applicable regression result after it is run; do not claim production operation.

### Remaining rollout gates

- Verify the selected retention policy against applicable product, organizational, legal, contractual and audit requirements.
- Verify the authoritative backend idempotency/reconciliation contract for every operation type before enabling aged or uncertain replay.
- Preserve evidence for authorized discard, durable disposition, owner/context checks, database deletion failure and secure-storage key-removal failure.
- Obtain further platform evidence for actual backup/restore, transfer, reinstall, secure-key lifecycle and supported-device behavior where required.
- Do not describe Phase 19 as production-ready solely because source checks, local tests or emulator integrations passed. These are distinct evidence categories.
## 7. Evidence and limitations

### Slice 2 implementation and local verification evidence (2026-10-16)

Reviewed mobile source baseline: [55daab92f3c68dafdadd1198f306e5fb9e6f8ec8](https://github.com/strykachiboy-art/clinic-system-pro/commit/55daab92f3c68dafdadd1198f306e5fb9e6f8ec8).

**Slice 2 exit disposition — IMPLEMENTED / LOCALLY VERIFIED:** the scoped runtime protections and focused local verification passed. The combined five-suite Flutter regression passed 68 tests and was rerun at HEAD `3fb6cfd39fd89fba13359dcf5ae1b0e1de4b9408`; the mobile source tree was unchanged between that execution and the original source baseline. This does not establish physical-device behavior or production operation.

**Legacy-payload limitation:** new appointment queue payloads omit `reason` and `notes`; existing encrypted queue payloads are not rewritten. A potentially attempted request must not be changed while retaining its stable idempotency key, since that can violate the endpoint's payload-fingerprint contract. Keep legacy records unchanged until a migration or disposition policy can be implemented and tested safely.

- `a2fc102` (`fix(mobile): omit clinical free text from offline payloads`): `AppointmentCreateCommand.toPayload()` omits optional appointment `reason` and `notes`. Focused tests assert that neither field appears in producer output or in the decrypted persisted queue payload.
- `c34adf5` (`fix(mobile): harden offline payload credential filtering`): normalized credential filtering includes `Cookie`, `Set-Cookie`, `X-API-Key` and Google/identity-token field variants alongside the existing sensitive fields. Focused secure-storage tests cover key-read and key-write failure paths without a silent fallback.
- `55daab9` (`test(mobile): assert API exception diagnostic redaction`): a mocked HTTP 422 regression places sentinel markers in the server message, validation error, request body, raw response body and URI query. It asserts that `ApiException.toString()` does not expose those markers while parsed UI details remain available.
- Combined local Flutter regression at `55daab9`: **68 tests passed** across:
  - `test/core/sync/appointment_create_queue_producer_test.dart`
  - `test/core/sync/operation_payload_crypto_test.dart`
  - `test/core/sync/operation_queue_store_test.dart`
  - `test/core/security/secure_key_value_storage_test.dart`
  - `test/core/networking/api_client_test.dart`

These are local Flutter test results for the named suites. They do not establish physical-device secure-storage behavior, backup/restore behavior, native/plugin/OS logging safety, compromised-device protection, or production operation. Existing legacy queued payloads were not rewritten. Payload encryption is not whole-database SQLite encryption.

### Source evidence reviewed

- `SecureKeyValueStorage` provides a platform-backed small-secret boundary; Android configuration specifies `resetOnError: false` and `migrateOnAlgorithmChange: true`.
- The session store keeps its JSON record in secure storage and exposes explicit clear behavior. The coordinator distinguishes definitive token rejection from uncertain refresh/network outcomes.
- The operation queue persists lifecycle/ownership metadata separately from authenticated encrypted payloads. The payload crypto fails closed on missing keys or authentication failures.
- Replay is scoped to the active user and clinic. Persisted in-flight work must be treated as an unknown outcome after interruption until recovered through the appropriate operation contract.
- The patient directory, patient detail, patient-edit and patient-registration screens hold sensitive models or form values in memory. The reviewed screens do not establish a general persistent patient cache.
- Android manifest and backup/extraction XML controls were inspected at source level.
- A source scan for common Dart logging calls was reported to find no matching calls in `mobile/lib`. That scan does not prove that plugins, native code, dependencies, transport layers or operating-system diagnostics never log sensitive data.

### Primary source paths inspected

- Session and routing: `mobile/lib/main.dart`; `mobile/lib/core/routing/app_router.dart`; `mobile/lib/features/auth/data/auth_remote_data_source.dart`; `mobile/lib/features/auth/data/auth_session_coordinator.dart`; `mobile/lib/features/auth/data/auth_session_store.dart`; `mobile/lib/features/auth/presentation/google_login_screen.dart`.
- Local storage and queue: `mobile/lib/core/security/secure_key_value_storage.dart`; `mobile/lib/core/storage/app_database.dart`; `mobile/lib/core/sync/operation_lifecycle.dart`; `mobile/lib/core/sync/operation_payload_crypto.dart`; `mobile/lib/core/sync/operation_queue_store.dart`; `mobile/lib/core/sync/operation_replay_scheduler.dart`; `mobile/lib/core/sync/operation_replay_worker.dart`; `mobile/lib/core/sync/appointment_create_queue_producer.dart`.
- In-memory patient views and errors: `mobile/lib/features/patients/presentation/patient_directory_screen.dart`; `mobile/lib/features/patients/presentation/patient_detail_screen.dart`; `mobile/lib/features/patients/presentation/patient_profile_edit_screen.dart`; `mobile/lib/features/patients/presentation/patient_registration_screen.dart`; `mobile/lib/core/networking/api_exception.dart`.
- Android backup source controls: `mobile/android/app/src/main/AndroidManifest.xml`; `mobile/android/app/src/main/res/xml/backup_rules.xml`; `mobile/android/app/src/main/res/xml/data_extraction_rules.xml`.
- Behavior context: `mobile/test/core/sync/appointment_create_queue_producer_test.dart`; `mobile/test/core/sync/operation_payload_crypto_test.dart`. The combined local Flutter regression was executed at HEAD `55daab9` and passed 68 tests across the five named suites summarized above. These are local test results only; this documentation reconciliation adds no runtime verification.
### Verification limits

- The original Gate 1 inventory update did not modify application code or run Flutter tests. Subsequent Slice 2 implementation commits and locally executed test evidence are summarized above; this documentation reconciliation does not add runtime verification.
- Android platform evidence now covers basic secure-storage adapter operations and Drift database close/reopen persistence on an Android 15 (API 35) emulator. Backup/restore, reinstall, device transfer, secure-key failure/migration lifecycle and device-compromise behavior remain unverified.
- No whole-database SQLite encryption claim is made.
- The queue store now exposes the 30-day terminal-retention policy and transactional purge; the replay worker invokes the policy and the scheduler supports configured startup/resume maintenance. This does not guarantee periodic work while the app never runs.
- Current ready-operation and next-retry queries exclude work at or beyond the 30-day review age, and a context-scoped review listing exists. A complete user-facing review/disposition workflow and operation-specific backend contract still require appropriate evidence.
- A universal backend idempotency-key validity window has not been established by the reviewed client source. Verify each operation type's actual backend contract before enabling aged or uncertain replay.
- Dependency-aware context-key cleanup and retryable cleanup-pending results exist in the queue store. These source paths do not prove physical key erasure, every platform lifecycle, or that all destructive callers validate backend disposition and authorization correctly.
- **ADR-001 is Accepted for the design baseline.** This inventory records approved policy and source evidence, not runtime enforcement, platform verification or a production-operation claim.

### Slice 5 platform integration evidence (2026-10-17)

**Execution environment:** Windows development host; Flutter 3.47.7; Dart 3.13.5; Pixel 4 AVD reported as Android 15 (API 35), device ID `emulator-5554`.

**Android integration results — PASS:**

- `flutter test --no-pub integration_test/secure_key_value_storage_integration_test.dart -d emulator-5554`: **1 test passed**. The test exercised platform-plugin writes, reads from a second adapter instance, overwrite, and deletion.
- `flutter test --no-pub integration_test/app_database_integration_test.dart -d emulator-5554`: **1 test passed**. The test verified the default Drift connection persisted metadata across database close/reopen and that the schema-version assertion matched version 3.
- The stale schema-version assertions in the database integration test were corrected from 2 to 3 after comparison with `AppDatabase.schemaVersion`.
- `flutter analyze --no-pub integration_test/app_database_integration_test.dart`: no issues found.
- Focused local database regression covering `app_database_schema_test.dart`, `app_database_test.dart`, and `migration_test.dart`: **9 tests passed**.
- `dart format integration_test/app_database_integration_test.dart`: completed; no formatting changes were required.

**Android backup configuration — source check PASS:**

- `AndroidManifest.xml` declares `allowBackup="false"` and references both `@xml/backup_rules` and `@xml/data_extraction_rules`.
- XML parsing confirmed the expected exclusion entries for the checked legacy backup rules and both cloud-backup and device-transfer groups.

**Evidence boundary and remaining limitations:**

- The secure-storage test covers the tested adapter operations through the Android plugin. It does not independently establish hardware-backed key guarantees, secure-key loss/recovery, algorithm-migration failure behavior, or every OS/device lifecycle path.
- The database test establishes persistence across close/reopen for the tested metadata value. It does not establish whole-database SQLite encryption.
- Backup/restore, actual device-to-device transfer, uninstall/reinstall behavior, physical-device behavior, and protection on rooted or compromised devices were not exercised by these tests. The manifest and XML checks remain source-level evidence only for those scenarios.
- These results are development verification, not production-operation evidence.
- Phase 19 rollout gates described in Sections 5.6 and 6 remain controlling: the documented retention and review policies, endpoint-specific idempotency/reconciliation contracts, authorized destructive cleanup and disposition evidence, dependency-aware key cleanup, and applicable organizational/legal approval must not be considered cleared by these emulator tests.

**Slice 5 disposition:** the two scoped Android integration tests and the source-level backup configuration check passed. The deferred platform scenarios and wider rollout gates remain explicitly open; this evidence does not assert that Phase 19 is production-ready.
