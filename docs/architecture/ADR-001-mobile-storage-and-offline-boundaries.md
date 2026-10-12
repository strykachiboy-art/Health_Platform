# ADR-001: Mobile Storage Architecture and Offline Boundaries

- Status: Proposed
- Date: 2026-10-12
- Evidence baseline: `915f90cb0f26dec671ab88adf212896a123b325c`

## Context

The Flutter application already has a Drift/SQLite database, a versioned
metadata schema, migration tests, and a platform-backed secure-storage
abstraction. These components must be extended rather than recreated.

At the evidence baseline:

- `AppDatabase` uses schema version 2.
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

## Scope and review

This record captures the current mobile storage boundary and the constraints
for subsequent offline work. Any future decision that changes the allowed
data categories, storage mechanism, encryption assumptions, or authoritative
ownership must be reviewed and documented before implementation.