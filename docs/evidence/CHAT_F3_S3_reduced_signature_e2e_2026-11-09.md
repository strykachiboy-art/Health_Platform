# CHAT-F3.S3 — Disposable Media E2E Evidence

**Evidence date:** 2026-11-09
**Project:** Clinic System Pro / Health_Platform
**Scope:** CHAT-F3.S3 combined production-like media integration
**Result:** GREEN — reduced-signature integration only
**Production readiness:** PENDING
**Production chat media:** DISABLED

## 1. Source and test provenance

- Canonical repository: `strykachiboy-art/Health_Platform`
- Source branch: `feat/chat-f3-s2-storage-keyring-retention`
- Source commit: `11b7adeb694fd0865b431ef2413c2677be46ff0a`
- E2E runner: `CHAT_F3_S3_SocketIO_Diagnostics_One_Run.py`
- Runner SHA256: `7C1FD4F847ADD52424B41576D9B2046E0563F021FBFC395B1BA3AC2935E719AF`

The evidence below is transcribed from the successful operator-provided
terminal output. This documentation operation does not rerun the E2E.
The test transcript is not an independently signed CI artifact.

## 2. Integration environment

The test used disposable infrastructure isolated from existing clinic fleets:

- PostgreSQL 18 with isolated Alembic migrations
- Redis 8
- Real ClamAV engine, EICAR-only test signature database
- Real FFprobe audio decoding
- Two independent Flask backend replicas
- Shared private encrypted media storage
- Authenticated HTTP media routes and Socket.IO connections

Existing clinic containers and databases were not targeted by the runner.
Cleanup was attempted for disposable resources; completion was not
independently established by this evidence document.

## 3. Successful integration gates

The following checkpoints were reported as PASS:

```text
CHATF3_S3_DISPOSABLE_ALEMBIC_MIGRATIONS=PASS
CHATF3_S3_A_POSTGRES_REDIS_CLAMD_READY=PASS
CHATF3_S3_B_REAL_SCANNER_AND_AUDIO_DECODER=PASS
CHATF3_S3_C_TWO_BACKEND_REPLICAS_LIVE=PASS
CHATF3_S3_C1_SOCKET_ENGINEIO_HANDSHAKE=PASS
CHATF3_S3_C2_SOCKET_JWT_CONTEXT_PREFLIGHT=PASS
CHATF3_S3_C3_SOCKETIO_AUTHENTICATED_REPLICA_B=PASS
CHATF3_S3_D_HTTP_AUTH_ENCRYPTION_SHARED_VOLUME_IDEMPOTENCY=PASS
CHATF3_S3_E_CROSS_REPLICA_SOCKETIO_OUTBOX_DELIVERY=PASS
CHATF3_S3_F_REAL_FFPROBE_AUDIO_THROUGH_ROUTE=PASS
CHATF3_S3_G_MAINTENANCE_DRY_RUN_AND_IDEMPOTENT_ROTATION=PASS
CHATF3_S3_H_PARTICIPANT_AND_REDIS_JWT_REVOCATION=PASS
CHATF3_S3_SYNTHETIC_INTEGRATION=GREEN
```

## 4. Failure injection

The following failure scenarios passed:

```text
CHATF3_S3_SCANNER_OUTAGE_FAIL_CLOSED=PASS
CHATF3_S3_BACKEND1_CRASH_BACKEND2_DOWNLOAD=PASS
CHATF3_S3_REDIS_OUTAGE_FAIL_CLOSED=PASS
```

This validates fail-closed behavior for the tested ClamAV and Redis
outages, and successful media access via replica 2 following the
disposable replica 1 crash.

## 5. Final recorded result

```text
CHATF3_S3_ONE_RUN_REDUCED_SIGNATURE_INTEGRATION=GREEN
CHATF3_S3_REAL_CLAMD_ENGINE_EICAR_ONLY=PASS
CHATF3_S3_FULL_CLAMAV_SIGNATURES=NOT_TESTED
CHATF3_S3_AUDIO_MP4_E2E=TESTED_WAV_PIPE_COMPATIBILITY_DEFECT=OPEN
CHATF3_S3_FULL_PRODUCTION_E2E=NOT_YET_GREEN
CHATF3_S3_EXISTING_DOCKER_FLEETS=UNTOUCHED
CHATF3_S3_MAIN_REPOSITORY_AND_FLUTTER=UNCHANGED
CHATF3_S3_PRODUCTION_CLINICAL_MEDIA=REMAINS_DISABLED
```

The runner also reported:

```text
CHATF3_S3_OWNED_DISPOSABLE_RESOURCE_CLEANUP=ATTEMPTED
CHATF3_S3_UNRELATED_CONTAINERS_VOLUMES_NETWORKS=NOT_TARGETED
CHATF3_S3_GIT_COMMIT_PUSH=NO
```

The last line describes the E2E execution itself, before this separate
documentation commit.

## 6. Limitations and outstanding release gates

1. **Full ClamAV signatures:** not tested. EICAR-only validation must
   not be represented as production malware-scanning readiness.
2. **WAV audio compatibility:** the original WAV-over-pipe fixture
   exposed a duration/probing compatibility issue. Real AAC/MP4 audio
   passed, but WAV remains unresolved.
3. **Device release validation:** real Flutter device media smoke and
   applicable release checks remain outstanding.

Do not enable production chat media based solely on this reduced-signature
integration result.

## 7. Closure decision and next work

**CHAT-F3.S3 reduced-signature integration:** GREEN / evidence recorded.

**CHAT-F3 full production release:** NOT YET GREEN.

This is a documentation-only closure. It does not modify production
application logic, backend configuration, database schema, Flutter code,
or existing deployment infrastructure.

Continue the Master Flutter Roadmap V3, retaining the outstanding
CHAT-F media release requirements for the appropriate release gates.
