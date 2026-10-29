# Clinic System Pro v5 - Current Backend Architecture

## Current State

This document describes the current backend architecture at the implementation
freeze and documentation-audit baseline.

Implementation freeze baseline: ``f36db7ac715e8d772eb36aacc1b63e5df9194b94``

## Application Core

- Flask application factory under ``backend/app``
- SQLAlchemy 2.x
- PostgreSQL as authoritative application datastore
- Alembic / Flask-Migrate for schema evolution
- Pydantic v2 where typed validation is required

## Identity and Tenant Authorization

JWT clinic context is resolved server-side through ``resolve_effective_clinic_id()``.

StaffDepartment membership is the source of truth for staff departmental membership.

SUPER_ADMIN is the privileged actor for clinic select/clear behavior.

## Runtime Infrastructure

- Redis: token revocation, rate limiting, Celery transport, Socket.IO messaging
- Celery: asynchronous/background execution
- Flask-SocketIO: realtime chat transport
- production Socket.IO message queue: Redis
- production configuration: fail closed on required configuration failures

## Background Reliability

Production Celery durability uses late acknowledgement, worker-lost rejection,
and prefetch of one.

Provider idempotency boundaries address external side effects that may occur before
database completion state is committed.

## Phase 9 Failure Injection

Current failure-injection work covers dependency failure, unknown outcomes, timeouts,
worker failure, provider failure, storage failure, and recovery/idempotency behavior.

Resilience baseline: ``273 passed in 120.49s (0:02:00)``

## Gate 12 Failure-Injection Slices

- remote clinic: ``8d43deb``
- pharmacy: ``12e62e1``
- payment timeout: ``b183795``
- worker notification: ``8fd5281``
- database failure: ``2ff03476d5a9f223b152b5cef857bf0d691c4f``

## Encryption Domains

Integration credential encryption:
- ``INTEGRATION_ENCRYPTION_KEY``
- ``INTEGRATION_ENCRYPTION_KEY_VERSION``
- ``INTEGRATION_ENCRYPTION_LEGACY_KEYS``

Encrypted backup encryption:
- ``BACKUP_ENCRYPTION_KEY``
- ``BACKUP_ENCRYPTION_KEY_VERSION``

The two encryption domains use separate operational secrets.

## Backup and Restore

Final database backup artifacts are encrypted ciphertext with SHA-256 integrity
verification before restore.

Encrypted restore decrypts into a unique temporary directory, invokes ``pg_restore``,
and removes temporary plaintext content after completion or failure.

Encrypted restore never falls back to plaintext after a decryption failure.

## Current Migration

Alembic current head: ``c8e4f1a9d2b7``

The current head adds ``integration_configs.encryption_key_version``.

## Current Verification Evidence

Encryption regression: ``131 passed in 139.23s``

Gate 11 focused regression: ``11 tests in 133.72s``

Backend E2E after lifecycle hardening: ``50 passed in 252.32s``

## Final Backend Verification

Final full backend regression: `8,749 passed in 9,667.28s (2:41:07)`
with `0 failed` and `0 errors`.

Combined verified backend baseline: `9,022 passed` (`8,749` backend application + `273` resilience), with `0 failed`.

Concurrency verification: `7 passed in 65.92s` using the disposable
PostgreSQL concurrency environment on `127.0.0.1:55434`.

Backend current development boundary: **GREEN / VERIFIED / LOCKED**.

This regression is the broad completion evidence for the current backend
development boundary.
## Production Boundary

The current repository state is VERIFIED engineering evidence.

Actual production deployment, production secret custody, production operation,
and independent external security testing remain separate gates.
