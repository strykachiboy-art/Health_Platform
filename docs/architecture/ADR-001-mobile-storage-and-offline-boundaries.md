# ADR-001: Mobile Storage Architecture and Offline Boundaries

- Status: Proposed
- Date: 2026-10-12
- Evidence baseline: `00c199325be61647ba7da328c2412c506863021b`

## Context

The Flutter application already has a Drift/SQLite database, a versioned
metadata schema, migration tests, and a platform-backed secure-storage
abstraction. These components must be extended rather than recreated.

At the evidence baseline:

- `AppDatabase` uses schema version 2. Its existing v1-to-v2 upgrade runs the migration steps inside a transaction.
- A focused migration failure-injection test verifies that a thrown failure rolls back the `updated_at` column addition, preserves the existing `app.locale=en` metadata, and leaves SQLite `user_version` at 1. This is evidence for the tested exception/transaction path, not a device-crash or power-loss guarantee.
- A default-connection integration test verifies that metadata survives closing and reopening the database in the tested development environment. This is not a cross-platform device-security guarantee.
- Inspection of `mobile/lib` at this baseline found no application-runtime references to `AppDatabase` or `appMetadata` outside the database implementation/generated files. The database is not yet wired into an application-runtime consumer, so production retention, deletion, and logout-cleanup behavior for this metadata store has not been exercised.
- `AppMetadata` stores client-local metadata and is explicitly restricted
  from holding authentication secrets or clinical records.
- `SecureKeyValueStorage` is intended for small secrets, such as session
  tokens. It is not a general application-state or offline-database store.
- Authentication and patient workflows use the backend for authoritative
  operations and data.
- The mobile sync directories contain no identified queue implementation.
- The repository roadmap assigns offline queue/idempotency to Phase 16,
  synchronization to Phase 17, conflict resolution to Phase 18, and
  sensitive offline-data security to Phase 19.

This record documents the current boundaries and the constraints that
future storage and offline features must satisfy. It does not claim that
the future queue or synchronization engine has been implemented.

## Decisions

### 1. The backend remains authoritative

The backend is authoritative for identity, authorization, clinic
membership, clinical records, financial records, and server-owned state.

Local data must not grant additional privileges. The client must not
bypass server authorization or treat cached permissions as authoritative.
Server validation remains required for submitted operations.

### 2. Storage responsibilities remain separate

**Drift/SQLite**

The existing database remains the foundation for approved client-local
metadata and future explicitly approved persistence needs. The current
`AppMetadata` table remains restricted to non-sensitive metadata.

**Platform secure storage**

The existing `SecureKeyValueStorage` abstraction remains the boundary for
small secrets that are appropriate for platform secure storage. It is not
a substitute for a general local database or an offline-operation queue.

**Backend API**

Remote data sources continue to obtain authoritative application data
and submit server-owned state changes through backend contracts.

A new storage framework or second database must not be introduced without
a demonstrated need and an explicit design review.

### 3. Sensitive local data is not approved by default

The existence of SQLite does not approve persisting patient records,
clinical content, financial data, credentials, or other sensitive payloads.

The current source baseline does not establish application-level
encryption of the SQLite database at rest. This record therefore makes
no such security claim.

Before sensitive data or sensitive operation payloads are cached locally,
a dedicated design and verification gate must establish appropriate
protection, key handling, access restrictions, retention, deletion,
logout behavior, and recovery expectations.

### 4. Schema evolution must preserve existing data

Existing migrations and historical schema snapshots must not be rewritten
merely to simplify migration history.

New schema changes must use explicit forward migrations and focused tests
for supported upgrade paths, schema consistency, and preservation of
existing records.

Migration failures must remain visible. Persistent user data must not be
silently discarded as a recovery strategy.

### 5. Offline queueing remains a separate implementation phase

The actual offline queue is not part of this documentation slice.

When the designated roadmap phase implements it, the design must address:

- Durable operation identifiers and idempotency keys.
- Duplicate detection and safe repeated delivery.
- Bounded retry behavior and backoff.
- Uncertain outcomes and reconciliation before retrying unsafe actions.
- Server-side authorization and validation after reconnection.
- Explicit treatment of expired sessions and changed clinic context.
- Conflict handling that does not silently overwrite authoritative data.
- Data minimization, retention, and protection of persisted operation payloads.

Clinical and financial operations must not be replayed blindly when the
client cannot establish whether the previous attempt succeeded.

Sensitive queue payloads must not be added to the current metadata table.
Their persistence requires the dedicated security and synchronization
design gates described above.

### 6. Failure handling must be explicit

Storage read, write, initialization, migration, and secure-storage failures
must not trigger silent deletion, plaintext credential fallback, or
unverified success reporting.

Future local persistence and synchronization must define which failures
are retryable, require reconciliation, require user intervention, or must
fail closed.

### 7. Privacy and lifecycle rules precede new caches

Before introducing a local cache or queue containing sensitive information,
the implementation must define data ownership, expiration, retention,
deletion, logout cleanup, session invalidation, account/clinic changes,
and behavior when authorization changes.

Offline availability does not imply indefinite local retention or continued
authorization to submit a previously queued action.

### 8. Read freshness and connectivity semantics

- Device connectivity is a local network signal. It does not prove that the
  API is reachable or that the backend is healthy.
- Receiving an HTTP response establishes that a response arrived. It does
  not establish that an operation succeeded; an HTTP error response remains
  a response, not a successful operation.
- If the patient-detail view has a previously fetched patient and a refresh
  fails for any reason other than HTTP 401, 403, or 404, it may retain that
  snapshot in memory only while clearly marking it as potentially stale.
  Editing and patient-status changes remain unavailable until a successful
  server read confirms the current record.
- An HTTP 401, 403, or 404 while refreshing patient details clears the
  displayed patient snapshot rather than continuing to show data after
  access is rejected or the requested record is reported unavailable.
- A successful server read clears the stale indication and restores only
  the actions permitted by the resulting current application state.
- This in-memory presentation behavior is not a persistent cache and does
  not establish a general offline patient-read capability. It does not
  survive process termination and must never authorize a mutation.
- AppMetadata remains limited to approved non-sensitive metadata. This
  decision does not approve persistent patient, clinical, financial, or
  sensitive operation payloads.

## Consequences

- Existing Drift and secure-storage components remain in place.
- This record does not change database schema or runtime behavior.
- No sensitive clinical-data cache is approved by this record.
- No offline queue, retry worker, synchronization engine, or conflict
  resolver is claimed to exist.
- Future work must implement and test the corresponding contracts before
  marking those capabilities implemented or verified.
- Documentation and status claims must distinguish planned, implemented,
  verified, committed, pushed, and production-operated behavior.

## Phase 10.3 validation evidence and limitations

Closeout validation date: 2026-10-13.
Source commit under test: `d5f587a8ae3af88f0a44c42b70f28a66af2ece9d`.

The final local Flutter gate completed with no analyzer issues and 78 passing
tests across nine focused test files:

| Test file | Result |
| --- | ---: |
| `test/core/security/secure_key_value_storage_test.dart` | 7 passed |
| `test/features/auth/data/auth_session_store_test.dart` | 17 passed |
| `test/features/auth/data/auth_session_coordinator_test.dart` | 28 passed |
| `test/features/auth/data/auth_remote_data_source_test.dart` | 6 passed |
| `test/features/auth/data/auth_remote_session_operations_test.dart` | 7 passed |
| `test/features/auth/presentation/google_login_screen_test.dart` | 6 passed |
| `test/core/storage/app_database_schema_test.dart` | 1 passed |
| `test/core/storage/app_database_test.dart` | 3 passed |
| `test/drift/app_database/migration_test.dart` | 3 passed |

`flutter analyze --no-pub` reported no issues. `git diff --check` also passed
before this documentation-only reconciliation. These are local development
test results, not production-operation evidence.

### Platform integration deferred

The final local gate did not run the following Android integration tests:

- `mobile/integration_test/secure_key_value_storage_integration_test.dart`
- `mobile/integration_test/app_database_integration_test.dart`

Launching the Android emulator caused severe memory pressure on the available
development machine. These tests remain deferred until a suitable device or
adequately resourced emulator is available. The local unit/widget and migration
results do not substitute for Android platform integration verification.

The `Continue with Google` flow was also not exercised end-to-end on an
Android device or emulator during this closeout. The local
`test/features/auth/presentation/google_login_screen_test.dart` suite passed
(6 tests), but that widget-test result does not verify the native Google
Sign-In interaction or the complete on-device authentication handoff. This
remains an environment-constrained verification gap, not a finding that
Google Sign-In is defective.

The secure-storage adapter's `resetOnError: false` and
`migrateOnAlgorithmChange: true` settings, along with Android backup-related
manifest configuration, have been reviewed at source level. Actual device
behavior has not been established by this closeout run.

### Runtime wiring limitation

No application-runtime consumer of `AppDatabase` or `appMetadata` was identified
in `mobile/lib` beyond database implementation/generated files at the inspected
baseline. Accordingly, application-level metadata retention, deletion, and
logout cleanup behavior remain unverified. The database must not be connected
to runtime code merely to close this phase; any wiring must follow a demonstrated
requirement and an explicit lifecycle design.

### Status classification

- **Implemented:** the existing secure-storage adapter boundary, session
  transition coordination safeguards, and Drift schema/migration code are
  present in source at the commit recorded above.
- **Verified locally:** the analyzer and the nine focused test files listed
  above passed in the development environment.
- **Deferred:** Android platform-backed integration tests.
- **Not implemented or approved here:** offline queueing, synchronization,
  conflict resolution, and sensitive clinical-data caching in SQLite.
- **Production operated:** no claim is made.

The ADR remains **Proposed**. This evidence update records validation and
limitations; it does not by itself constitute architectural approval.


## Phase 15 offline-foundation implementation evidence

Review date: 2026-10-13.

Reviewed runtime source commit: `f18b6491794f4e824f7876f372abd236599847a8`.

The following Phase 15 foundation slices are present at this commit:

- Slice 1 (`0158d6e`): device connectivity awareness and an app-level
  offline notice.
- Slice 2 (`1f35882`): shared API transport observation that distinguishes
  HTTP response receipt from a request that receives no response. It does
  not claim backend health or operation success and does not add retries.
- Slice 3 (`f18b649`): patient-detail stale-state labeling, blocking edits
  and status changes while the displayed record is unconfirmed, and clearing
  the displayed snapshot after HTTP 401, 403, or 404 during a refresh.

Local verification at Slice 3:

- Patient-detail test file: 12 tests passed.
- Full Flutter test suite: 178 tests passed.
- `flutter analyze --no-pub`: no issues found.
- `git diff --check`: passed before commit.

These results are development-environment test evidence, not Android
platform-integration or production-operation evidence. The implementation
establishes in-memory stale-read behavior only; it does not implement
persistent patient caching, an offline operation queue, synchronization,
or conflict resolution.

The ADR remains **Proposed**. This evidence update and Phase 15 roadmap
clarification do not by themselves authorize persistent sensitive local data.

## Phase 15 Slice 5: local-data eligibility inventory

Review date: 2026-10-13.

Source inventory commit: `9cd87c9fe186354b59cc86f0c1d46893a4c7f988`.

This was a read-only source inventory. It identified nine tracked,
non-generated Dart files under the mobile feature data directories and
remote-data boundaries for authentication, backend liveness, and patient
workflows. The corrected search found no `AppDatabase`, `appMetadata`,
`AppMetadata`, or `driftDatabase` references outside the database
implementation and generated schema files in `mobile/lib`.

The classifications below are the provisional eligibility decisions for
Phase 15. They do not change this ADR's Proposed status or constitute a
production security approval.

| Data category | Phase 15 decision | Required boundary |
| --- | --- | --- |
| Non-sensitive app-local settings and metadata | Eligible in principle, subject to a demonstrated purpose | Keep values minimal and non-sensitive. Define ownership and any necessary retention or deletion behavior before wiring a runtime consumer. `AppMetadata` is not currently connected to an application-runtime consumer. |
| Authentication credentials, access tokens, and refresh tokens | Not eligible for `AppMetadata` or a general offline cache | Keep appropriate small secrets within the existing platform secure-storage boundary. Do not duplicate them into general application data storage. |
| Patient details, patient directories, identifiers, and clinical content | Persistent local caching is not approved in Phase 15 | Requires a dedicated sensitive-data design and verification gate under Phase 19. The existing patient-detail snapshot is in-memory only, is labelled potentially stale after applicable refresh failures, and remains read-only until a successful server read. HTTP 401, 403, or 404 during refresh clears that snapshot. |
| Financial and payment records or payloads | Persistent local caching is not approved | Requires explicit data-protection, access, retention, deletion, and recovery review before implementation. |
| Authorization decisions, cached permissions, and clinic context used as an access grant | Must not become a source of authority | The backend remains authoritative. Persisted client state must not grant privileges or bypass fresh server authorization. |
| Backend liveness and API transport observations | Transient status only; not approved as an offline truth cache | A saved response cannot establish current backend health or current API reachability. |
| Durable queued-operation payloads and outbox records | Outside Phase 15 | Queue identifiers, idempotency, retries, and uncertain outcomes belong to Phase 16; synchronization belongs to Phase 17. Sensitive payload persistence also requires the applicable Phase 19 security and lifecycle gate. Do not store queue payloads in `AppMetadata`. |
| Other remote reference or lookup data | No eligible candidate identified by this inventory | No category-wide approval is granted. Review the actual response fields, sensitivity, ownership, purpose, freshness, and lifecycle before proposing persistence. |

**Slice 5 decision:** no persistent remote API payload is approved by this
inventory. The current source inventory provides no defensible reason to
introduce an API cache or wire `AppDatabase` into runtime code merely to
claim offline support. Reassess when a concrete, useful, non-sensitive
resource and its lifecycle requirements have been identified.

This is an inventory decision, not authorization to persist sensitive data.
The ADR remains **Proposed**. The result does not implement or approve a
durable queue, synchronization, conflict resolution, or a sensitive-data
cache, and makes no claim of production operation.

## Phase 15 foundation exit review and full mobile source audit

Review date: 2026-10-13.

Reviewed implementation and regression baseline:
`71e31e4ddb11e8e8c2a57a8ebd497766bc86fcd2`.

The final read-only inventory enumerated 28 tracked Dart files under
`mobile/lib`. The API/network pattern search reported 13 matching source
locations, and the data-boundary declaration search found the shared
`ApiClient` plus the authentication, backend-liveness, and patient remote
data sources. No additional useful, non-sensitive remote-data candidate
was identified for persistent offline caching.

The storage-reference search found the existing platform secure-storage
boundary, Drift `AppDatabase` / `AppMetadata` implementation and generated
schema, and authentication session-store use. No application-runtime
consumer of `AppDatabase` or `appMetadata` was found outside the database
implementation and generated schema files. This does not justify wiring the
database into the application merely to claim offline support.

### Phase 15 implementation evidence

The following behaviors are present in the reviewed source:

- Device connectivity and API communication are represented as distinct
  signals. HTTP response receipt does not imply backend health or operation
  success.
- Patient-detail refresh failures retain a previously loaded snapshot in
  memory only for applicable failures, label it potentially stale, and
  block editing and status changes until a successful server read.
- Patient-detail refresh responses of HTTP 401, 403, or 404 clear the
  displayed snapshot.
- Patient-directory refreshes retain same-query results in memory on
  applicable transient failures and visibly mark them as potentially stale.
- Patient-directory HTTP 401 and 403 responses clear the displayed rows.
  Search changes invalidate the prior request and clear results for the
  previous query immediately. A successful refresh clears the stale state.
- The stale-display behavior is not persistent storage, does not survive
  process termination, and does not authorize an offline mutation.

### Local verification

At the reviewed commit:

- `flutter analyze --no-pub`: no issues found.
- Full Flutter test suite: 182 tests passed.
- The patient-directory focused suite: 7 tests passed, including transient
  refresh failure, HTTP 401/403 clearing, search invalidation, and successful
  retry clearing the stale warning.
- `git diff --check`: passed during the verified regression.
- The exact `main` remote SHA and expected untracked-path scope were verified.

These are development-environment results, not Android platform-integration
or production-operation evidence. No production claim is made.

### Phase 15 exit decision and limitations

**The Phase 15 foundation is complete for the currently inventoried mobile
data surface, with persistent offline application-data caching explicitly
deferred.** This decision reflects the lack of a concrete, useful,
non-sensitive remote payload that is eligible for caching under the current
data policy. It is not permission to persist patient, clinical, financial,
authentication, authorization, or sensitive operation data.

This closeout does not implement general persistent or cross-process offline
reads. The current patient behavior is an in-memory stale-display safeguard,
not an offline database or a general-purpose cache. Existing local metadata
storage remains unwired to an application-runtime consumer, and this closeout
does not authorize wiring it in the absence of a demonstrated requirement.

Any future cache proposal must identify the actual data fields and use,
then review sensitivity, ownership, source authority, freshness, authorization,
expiration, retention, deletion, logout/session lifecycle, and recovery before
implementation. Sensitive clinical and financial data remains subject to the
applicable Phase 19 design and verification gate. Offline queueing remains
Phase 16, synchronization Phase 17, and conflict resolution Phase 18.

The ADR remains **Proposed**. This exit evidence records the implemented
foundation, local test results, and limitations; it does not by itself
constitute architectural approval, platform-integration verification, or
production-operation evidence.

## Scope and review

This record captures the current mobile storage boundary and the constraints
for subsequent offline work. Any future decision that changes the allowed
data categories, storage mechanism, encryption assumptions, or authoritative
ownership must be reviewed and documented before implementation.

## Phase 19 current-state security inventory

Review date: 2026-10-16. Source snapshot: [5be486b2a60304df0f40425fd6219efe80263570](https://github.com/strykachiboy-art/clinic-system-pro/commit/5be486b2a60304df0f40425fd6219efe80263570).

The current source inventory and selected lifecycle design are recorded in [phase-19-offline-sensitive-data-inventory.md](phase-19-offline-sensitive-data-inventory.md).

This note adds a current-state view without rewriting historical evidence. Statements in earlier sections describing a prior source baseline remain historical; they must not be read as claims about the current implementation. Earlier test and migration results also remain evidence only for the source commit and environment originally recorded. Gate 1 source reconciliation compared the cited snapshot `5be486b2a60304df0f40425fd6219efe80263570` with branch HEAD `3bcfa9698d0f05fbfd2e830ab5a0c5a90ac116d5`; only the ADR and Phase 19 inventory changed between those refs, so the reviewed application source remains the source snapshot.

### Current source findings

- The runtime wires `AppDatabase` to the durable operation queue. Schema version 3 contains `AppMetadata`, `OperationQueueEntry` and `OperationQueuePayload`.
- Offline appointment request payloads use AES-256-GCM. The encryption key is handled through platform-backed secure storage, and authenticated associated data binds encrypted content to operation identity and user/clinic context. This is payload-level encryption, not whole-database SQLite encryption.
- Queue metadata remains in ordinary SQLite columns. The queue store has a terminal purge primitive that accepts a caller-provided cutoff. The primitive does not itself establish or schedule a retention policy.
- The selected design retains terminal queue records and associated encrypted payloads for 30 days after the terminal transition. The runtime policy, safe trigger and verification are not yet established by this documentation change.
- The selected design requires unresolved operations aged 30 days from creation to enter review and be excluded from automatic replay until reviewed. The current ready-operation selection does not enforce this age-based review guard.
- The client lifecycle distinguishes queued, in-flight, retry-scheduled, reconciliation-required, acknowledged, rejected, user-intervention-required and cancelled operations. An in-flight operation may have reached the server before interruption; it must not be treated as a confirmed failure merely because its response was lost.
- A universal backend idempotency-key validity period has not been established in this review. The local 30-day retention policy is not proof of backend deduplication for 30 days. Replay of aged or uncertain work requires verification of the applicable operation-specific backend contract.
- Authentication tokens reside in the platform-backed session record. Patient directory, detail, edit and registration screens hold sensitive models or form values in memory. Source review of these screens does not prove automatic clearing across every route and session transition.
- The current logout path waits for active replay and leaves replay paused after logout. The encrypted queue and its keys are retained; logout is not an implicit destructive queue purge.
- Approved design decision: exclude optional appointment `reason` and `notes` from persisted offline payloads by default. Later inclusion requires documented workflow necessity and explicit approval, plus review of protection, access, retention and deletion requirements. The current producer still includes these fields when supplied; runtime enforcement and focused tests remain outstanding.
- Android manifest and backup/data-extraction exclusions exist at source level. They do not prove actual backup/restore behavior, whole-database encryption or protection against a compromised device.

### Selected Slice 1 lifecycle design

- Terminal states `acknowledged`, `rejected` and `cancelled`: 30-day local retention measured from the terminal transition, followed by eligibility for a transactional purge.
- Unresolved states: 30-day age-based review, without automatic deletion just because the age threshold is reached.
- Unknown outcome: reconcile before a potentially unsafe repeat delivery. Preserve stable operation identity and idempotency key when a repeat attempt is proven safe; otherwise fail closed.
- Explicit discard: require the authenticated original operation owner, the original user/clinic context, verified permission for the operation type and explicit confirmation. If a server-side effect may have occurred, require authoritative reconciliation or an explicitly recorded manual disposition before deletion. Local deletion is not server-side cancellation.
- Logout and context changes: stop and await active replay, keep replay paused, invalidate access to prior session-scoped views, and revalidate user/clinic ownership before decrypting or delivering retained work. Ordinary logout does not itself delete queue data or encryption keys.
- Key cleanup: retain keys needed by surviving encrypted payloads. After an authorized purge, check key dependencies before removing keys. Database deletion and secure-storage deletion are separate operations; incomplete cleanup must be reported and retriable.
- Appointment payload minimization: exclude optional `reason` and `notes` from persisted offline payloads by default. Later inclusion requires documented workflow necessity and explicit approval, followed by review of protection, access, retention and deletion requirements. This is a selected design policy, not yet runtime-enforced.
- Device controls: treat backup rules as source-level evidence until platform behavior has been verified on suitable devices and supported versions.

### Remaining verification and rollout gates

- Verify the selected 30-day retention rule against applicable product, organizational, legal, contractual and audit requirements before deployment.
- Verify the actual idempotency and reconciliation contract for each replayable operation type. Do not infer the backend validity window from local retention.
- Enforce the approved default exclusion of optional appointment `reason` and `notes` in the appointment queue producer, and test that these fields cannot enter persisted offline payloads by default. Any later inclusion requires documented workflow necessity and explicit approval, with the relevant protection, access, retention and deletion review.
- Implement and test the terminal purge policy and trigger, unresolved-operation review guard, explicit discard authorization, disposition evidence, dependency-aware key cleanup and failure recovery.
- Verify that sensitive views and forms become inaccessible following logout, authentication rejection, and user/clinic changes.
- Perform platform-level verification of secure storage, backup/restore, reinstall and device lifecycle behavior where supported. Unit and widget tests alone do not establish device behavior.

Status remains **Proposed**. This current-state note records source findings and selected design policy; it does not claim that outstanding runtime enforcement has been implemented, platform behavior has been verified, or production operation has occurred.
