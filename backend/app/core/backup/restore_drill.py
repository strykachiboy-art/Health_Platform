from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from app.core.backup.backup_encryption import (
    read_backup_artifact_key_version,
)
from app.core.backup.restore_service import (
    RestoreError,
    restore_full_backup,
)


RESTORE_DRILL_CONFIRMATION = (
    "RESTORE_DRILL_APPROVED"
)

DEFAULT_TIMEOUT_SECONDS = 1800


class RestoreDrillError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RestoreDrillDatabaseVerification:
    database_name: str
    migration_revision: str | None
    user_count: int
    clinic_count: int
    audit_log_count: int
    chat_outbox_count: int
    processing_outbox_count: int


@dataclass(frozen=True, slots=True)
class RestoreDrillResult:
    success: bool
    backup_id: str
    backup_path: str
    target_database: str
    target_storage_root: str
    restored_file_count: int
    restored_total_size_bytes: int
    authentication_invalidated: bool
    outbox_recovery_completed: bool
    post_restore_verification_completed: bool
    database_verification: RestoreDrillDatabaseVerification
    drill_started_at: str
    completed_at: str
    duration_seconds: float
    backup_created_at: str | None
    backup_age_seconds_at_drill_start: float | None
    database_artifact_encrypted: bool
    database_artifact_key_version_hint: int | None
    database_artifact_decryption_authenticated: bool | None
    integrity_verification: dict[str, str]
    recovery_objectives: dict[str, Any]
    report_path: str | None


def _require_confirmation() -> None:
    confirmation = os.environ.get(
        "RESTORE_DRILL_CONFIRMATION"
    )

    if confirmation != RESTORE_DRILL_CONFIRMATION:
        raise RestoreDrillError(
            "Restore drill requires "
            "RESTORE_DRILL_CONFIRMATION="
            f"{RESTORE_DRILL_CONFIRMATION}"
        )


def _require_database_url(
    value: str | None,
    *,
    field_name: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise RestoreDrillError(
            f"{field_name} must be provided"
        )

    value = value.strip()

    if not value:
        raise RestoreDrillError(
            f"{field_name} cannot be empty"
        )

    try:
        url = make_url(
            value
        )
    except Exception as exc:
        raise RestoreDrillError(
            f"{field_name} is invalid"
        ) from exc

    if url.get_backend_name() != "postgresql":
        raise RestoreDrillError(
            f"{field_name} must use PostgreSQL"
        )

    if not url.database:
        raise RestoreDrillError(
            f"{field_name} must include a database name"
        )

    return value


def _database_identity(
    database_url: str,
) -> tuple[
    str | None,
    int | None,
    str | None,
]:
    url = make_url(
        database_url
    )

    return (
        url.host,
        url.port or 5432,
        url.database,
    )


def _validate_dedicated_database(
    *,
    source_database_url: str,
    target_database_url: str,
) -> None:
    source_identity = _database_identity(
        source_database_url
    )

    target_identity = _database_identity(
        target_database_url
    )

    if source_identity == target_identity:
        raise RestoreDrillError(
            "Restore drill target database must "
            "differ from the source database"
        )


def _validate_storage_target(
    *,
    target_storage_root: str | Path,
    source_storage_root: str | Path | None,
) -> Path:
    target = Path(
        target_storage_root
    ).expanduser().resolve()

    if target == Path(
        target.anchor
    ):
        raise RestoreDrillError(
            "Restore drill storage target is unsafe"
        )

    if source_storage_root is not None:
        source = Path(
            source_storage_root
        ).expanduser().resolve()

        if target == source:
            raise RestoreDrillError(
                "Restore drill storage target must "
                "differ from source storage"
            )

    if target.exists():
        raise RestoreDrillError(
            "Restore drill storage target must "
            "not already exist"
        )

    return target


def _resolve_executable(
    configured_path: str | None,
    default_name: str,
    *,
    field_name: str,
) -> str:
    candidate = (
        configured_path
        or default_name
    )

    resolved = shutil.which(
        candidate
    )

    if resolved is not None:
        return resolved

    path = Path(
        candidate
    )

    if (
        path.exists()
        and path.is_file()
    ):
        return str(
            path.resolve()
        )

    raise RestoreDrillError(
        f"{field_name} executable was not found"
    )


def _safe_database_dsn(
    database_url: str,
) -> tuple[Any, str]:
    url = make_url(
        database_url
    )

    safe_url = url.set(
        password=None
    )

    return (
        url,
        safe_url.render_as_string(
            hide_password=False
        ),
    )


def _run_psql(
    *,
    database_url: str,
    psql_path: str,
    sql: str,
    timeout_seconds: int,
) -> str:
    url, safe_dsn = _safe_database_dsn(
        database_url
    )

    environment = os.environ.copy()

    if url.password is not None:
        environment["PGPASSWORD"] = (
            url.password
        )

    command = [
        psql_path,
        "-X",
        "-A",
        "-t",
        "-v",
        "ON_ERROR_STOP=1",
        "--dbname",
        safe_dsn,
        "-c",
        sql,
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise RestoreDrillError(
            "psql command timed out"
        ) from exc
    except OSError as exc:
        raise RestoreDrillError(
            "Unable to execute psql"
        ) from exc

    if completed.returncode != 0:
        stderr = (
            completed.stderr
            or ""
        ).strip()

        if stderr:
            raise RestoreDrillError(
                f"psql failed: {stderr}"
            )

        raise RestoreDrillError(
            "psql failed"
        )

    return (
        completed.stdout
        or ""
    ).strip()


def _query_database_state(
    *,
    database_url: str,
    psql_path: str,
    timeout_seconds: int,
    expected_migration_revision: str | None,
) -> RestoreDrillDatabaseVerification:
    query = """
SELECT json_build_object(
    'database_name',
    current_database(),
    'migration_revision',
    (
        SELECT version_num
        FROM alembic_version
        ORDER BY version_num
        LIMIT 1
    ),
    'user_count',
    (SELECT COUNT(*) FROM users),
    'clinic_count',
    (SELECT COUNT(*) FROM clinics),
    'audit_log_count',
    (SELECT COUNT(*) FROM audit_logs),
    'chat_outbox_count',
    (SELECT COUNT(*) FROM chat_outbox),
    'processing_outbox_count',
    (
        SELECT COUNT(*)
        FROM chat_outbox
        WHERE status = 'processing'
    )
)::text;
"""

    raw = _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql=query,
        timeout_seconds=timeout_seconds,
    )

    try:
        payload = json.loads(
            raw
        )
    except json.JSONDecodeError as exc:
        raise RestoreDrillError(
            "Post-restore database verification "
            "returned invalid JSON"
        ) from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise RestoreDrillError(
            "Post-restore database verification "
            "returned an invalid object"
        )

    database_name = payload.get(
        "database_name"
    )

    migration_revision = payload.get(
        "migration_revision"
    )

    user_count = payload.get(
        "user_count"
    )

    clinic_count = payload.get(
        "clinic_count"
    )

    audit_log_count = payload.get(
        "audit_log_count"
    )

    chat_outbox_count = payload.get(
        "chat_outbox_count"
    )

    processing_outbox_count = payload.get(
        "processing_outbox_count"
    )

    if not isinstance(
        database_name,
        str,
    ):
        raise RestoreDrillError(
            "Restored database name could not be verified"
        )

    if migration_revision is not None and not isinstance(
        migration_revision,
        str,
    ):
        raise RestoreDrillError(
            "Restored migration revision is invalid"
        )

    counts = {
        "user_count": user_count,
        "clinic_count": clinic_count,
        "audit_log_count": audit_log_count,
        "chat_outbox_count": chat_outbox_count,
        "processing_outbox_count": (
            processing_outbox_count
        ),
    }

    for field_name, value in counts.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise RestoreDrillError(
                f"Invalid restored database value: "
                f"{field_name}"
            )

    if expected_migration_revision is not None:
        if migration_revision != expected_migration_revision:
            raise RestoreDrillError(
                "Restored migration revision does not "
                "match the backup metadata"
            )

    if processing_outbox_count != 0:
        raise RestoreDrillError(
            "Chat outbox still contains processing "
            "events after restore recovery"
        )

    return RestoreDrillDatabaseVerification(
        database_name=database_name,
        migration_revision=migration_revision,
        user_count=user_count,
        clinic_count=clinic_count,
        audit_log_count=audit_log_count,
        chat_outbox_count=chat_outbox_count,
        processing_outbox_count=(
            processing_outbox_count
        ),
    )


def _invalidate_authentication(
    *,
    database_url: str,
    psql_path: str,
    timeout_seconds: int,
) -> None:
    before_query = """
SELECT json_build_object(
    'user_count',
    COUNT(*),
    'token_sum',
    COALESCE(SUM(token_version), 0)
)::text
FROM users;
"""

    before_raw = _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql=before_query,
        timeout_seconds=timeout_seconds,
    )

    before = json.loads(
        before_raw
    )

    user_count = before.get(
        "user_count"
    )

    token_sum = before.get(
        "token_sum"
    )

    if (
        isinstance(user_count, bool)
        or not isinstance(user_count, int)
        or user_count < 0
    ):
        raise RestoreDrillError(
            "Invalid user count during "
            "authentication invalidation"
        )

    if (
        isinstance(token_sum, bool)
        or not isinstance(token_sum, int)
        or token_sum < 0
    ):
        raise RestoreDrillError(
            "Invalid token version sum during "
            "authentication invalidation"
        )

    _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql="""
UPDATE users
SET token_version = token_version + 1;
""",
        timeout_seconds=timeout_seconds,
    )

    after_query = """
SELECT COALESCE(SUM(token_version), 0)::bigint
FROM users;
"""

    after_raw = _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql=after_query,
        timeout_seconds=timeout_seconds,
    )

    try:
        after_sum = int(
            after_raw.strip()
        )
    except ValueError as exc:
        raise RestoreDrillError(
            "Unable to verify authentication "
            "invalidation"
        ) from exc

    expected_sum = (
        token_sum
        + user_count
    )

    if after_sum != expected_sum:
        raise RestoreDrillError(
            "Authentication token versions were "
            "not invalidated for every restored user"
        )


def _recover_chat_outbox(
    *,
    database_url: str,
    psql_path: str,
    timeout_seconds: int,
) -> None:
    _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql="""
UPDATE chat_outbox
SET
    status = 'pending',
    available_at = NOW(),
    processed_at = NULL,
    last_error = NULL
WHERE status = 'processing';
""",
        timeout_seconds=timeout_seconds,
    )

    remaining = _run_psql(
        database_url=database_url,
        psql_path=psql_path,
        sql="""
SELECT COUNT(*)
FROM chat_outbox
WHERE status = 'processing';
""",
        timeout_seconds=timeout_seconds,
    )

    try:
        remaining_count = int(
            remaining.strip()
        )
    except ValueError as exc:
        raise RestoreDrillError(
            "Unable to verify chat outbox recovery"
        ) from exc

    if remaining_count != 0:
        raise RestoreDrillError(
            "Chat outbox processing events remain after recovery"
        )


def _optional_positive_integer_environment_value(
    name: str,
) -> int | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None

    try:
        value = int(raw.strip())
    except (TypeError, ValueError) as exc:
        raise RestoreDrillError(
            f"{name} must be a positive integer when configured"
        ) from exc

    if value <= 0:
        raise RestoreDrillError(
            f"{name} must be a positive integer when configured"
        )

    return value


def _load_restore_evidence(
    backup_root: Path,
) -> tuple[str | None, datetime | None, bool, int | None]:
    metadata_path = backup_root / "metadata.json"
    try:
        metadata = json.loads(
            metadata_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreDrillError(
            "Unable to read backup metadata for restore evidence"
        ) from exc

    if not isinstance(metadata, dict):
        raise RestoreDrillError(
            "Backup metadata must contain a JSON object"
        )

    database_metadata = metadata.get("database")
    if not isinstance(database_metadata, dict):
        raise RestoreDrillError(
            "Backup database metadata is missing"
        )

    raw_created_at = metadata.get("created_at")
    if not isinstance(raw_created_at, str) or not raw_created_at.strip():
        raw_created_at = database_metadata.get("created_at")

    backup_created_at = None
    backup_created_at_datetime = None
    if isinstance(raw_created_at, str) and raw_created_at.strip():
        try:
            parsed = datetime.fromisoformat(
                raw_created_at.strip().replace("Z", "+00:00")
            )
            if parsed.tzinfo is not None:
                backup_created_at_datetime = parsed.astimezone(timezone.utc)
                backup_created_at = backup_created_at_datetime.isoformat()
        except (OverflowError, OSError, TypeError, ValueError):
            # Older or externally supplied metadata may lack a usable timestamp.
            # Preserve the successful restore, but mark freshness assessment unavailable.
            pass

    encrypted = database_metadata.get("encrypted", False)
    if not isinstance(encrypted, bool):
        raise RestoreDrillError(
            "Backup database encryption marker must be a boolean"
        )

    key_version_hint = None
    if encrypted:
        recorded_path = database_metadata.get("backup_path")
        default_artifact = (backup_root / "database.dump").resolve()
        artifact_path = default_artifact

        if isinstance(recorded_path, str) and recorded_path.strip():
            recorded = Path(recorded_path)
            candidate = recorded.resolve() if recorded.is_absolute() else (backup_root / recorded).resolve()
            try:
                candidate.relative_to(backup_root.resolve())
            except ValueError:
                # Portable backups may retain their original absolute path in metadata.
                # Match restore_service's safe fallback to the standard artifact name.
                candidate = default_artifact
            artifact_path = candidate

        if not artifact_path.is_file() and default_artifact.is_file():
            artifact_path = default_artifact
        if not artifact_path.is_file():
            raise RestoreDrillError(
                "Encrypted database backup artifact was not found for key-version evidence"
            )

        try:
            # The header version is an untrusted lookup hint; successful restore
            # must still authenticate the ciphertext with the matching key.
            key_version_hint = read_backup_artifact_key_version(artifact_path)
        except Exception as exc:
            raise RestoreDrillError(
                "Unable to read encrypted database artifact key-version hint"
            ) from exc

    return (
        backup_created_at,
        backup_created_at_datetime,
        encrypted,
        key_version_hint,
    )


def _build_recovery_objective_evidence(
    *,
    rpo_target_seconds: int | None,
    rto_target_seconds: int | None,
    backup_age_seconds_at_start: float | None,
    duration_seconds: float,
) -> dict[str, Any]:
    if rpo_target_seconds is None:
        rpo_status = "not_configured"
    elif backup_age_seconds_at_start is None:
        rpo_status = "not_assessed"
    elif backup_age_seconds_at_start <= rpo_target_seconds:
        rpo_status = "within_target"
    else:
        rpo_status = "exceeds_target"

    if rto_target_seconds is None:
        rto_status = "not_configured"
    elif duration_seconds <= rto_target_seconds:
        rto_status = "within_target"
    else:
        rto_status = "exceeds_target"

    return {
        "rpo_target_seconds": rpo_target_seconds,
        "backup_age_seconds_at_drill_start": backup_age_seconds_at_start,
        "rpo_freshness_proxy_status": rpo_status,
        "rpo_assessment_basis": (
            "Backup metadata timestamp versus drill start is a freshness proxy, "
            "not an authoritative measurement of data loss in a production incident."
        ),
        "rpo_authoritatively_verified": False,
        "rto_target_seconds": rto_target_seconds,
        "observed_restore_drill_duration_seconds": duration_seconds,
        "rto_status": rto_status,
    }


def _load_expected_migration_revision(
    backup_path: Path,
) -> str | None:
    metadata_path = (
        backup_path
        / "metadata.json"
    )

    if not metadata_path.is_file():
        raise RestoreDrillError(
            "Backup metadata.json was not found"
        )

    try:
        metadata = json.loads(
            metadata_path.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise RestoreDrillError(
            "Unable to read backup metadata"
        ) from exc

    database_metadata = metadata.get(
        "database"
    )

    if not isinstance(
        database_metadata,
        dict,
    ):
        raise RestoreDrillError(
            "Backup database metadata is missing"
        )

    revision = database_metadata.get(
        "migration_revision"
    )

    if revision is None:
        return None

    if not isinstance(
        revision,
        str,
    ) or not revision.strip():
        raise RestoreDrillError(
            "Backup migration revision is invalid"
        )

    return revision.strip()


def run_restore_drill(
    *,
    backup_path: str | Path,
    target_database_url: str,
    target_storage_root: str | Path,
    source_database_url: str | None = None,
    source_storage_root: str | Path | None = None,
    pg_restore_path: str | None = None,
    psql_path: str | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    report_path: str | Path | None = None,
) -> RestoreDrillResult:
    _require_confirmation()
    drill_started_at_datetime = datetime.now(timezone.utc)
    drill_started_at = drill_started_at_datetime.isoformat()
    drill_started_monotonic = time.monotonic()
    rpo_target_seconds = _optional_positive_integer_environment_value(
        "BACKUP_RPO_TARGET_SECONDS"
    )
    rto_target_seconds = _optional_positive_integer_environment_value(
        "BACKUP_RTO_TARGET_SECONDS"
    )

    source_database_url = (
        source_database_url
        or os.environ.get(
            "DATABASE_URL"
        )
    )

    target_database_url = _require_database_url(
        target_database_url,
        field_name="Target database URL",
    )

    source_database_url = _require_database_url(
        source_database_url,
        field_name="Source database URL",
    )

    _validate_dedicated_database(
        source_database_url=source_database_url,
        target_database_url=target_database_url,
    )

    backup_root = Path(
        backup_path
    ).expanduser().resolve()

    if not backup_root.is_dir():
        raise RestoreDrillError(
            "Backup path must be a directory"
        )

    (
        backup_created_at,
        backup_created_at_datetime,
        database_artifact_encrypted,
        database_artifact_key_version_hint,
    ) = _load_restore_evidence(backup_root)

    backup_age_seconds_at_drill_start = None
    if backup_created_at_datetime is not None:
        observed_backup_age = (
            drill_started_at_datetime - backup_created_at_datetime
        ).total_seconds()
        if observed_backup_age >= 0:
            backup_age_seconds_at_drill_start = round(
                observed_backup_age,
                3,
            )

    target_storage = _validate_storage_target(
        target_storage_root=target_storage_root,
        source_storage_root=source_storage_root
        or os.environ.get(
            "STORAGE_ROOT"
        ),
    )

    if (
        isinstance(
            timeout_seconds,
            bool,
        )
        or not isinstance(
            timeout_seconds,
            int,
        )
        or timeout_seconds <= 0
    ):
        raise RestoreDrillError(
            "Restore drill timeout must be a positive integer"
        )

    resolved_pg_restore = _resolve_executable(
        pg_restore_path
        or os.environ.get(
            "PG_RESTORE_PATH"
        ),
        "pg_restore",
        field_name="pg_restore",
    )

    resolved_psql = _resolve_executable(
        psql_path
        or os.environ.get(
            "PSQL_PATH"
        ),
        "psql",
        field_name="psql",
    )

    expected_migration_revision = (
        _load_expected_migration_revision(
            backup_root
        )
    )

    database_verification: (
        RestoreDrillDatabaseVerification
        | None
    ) = None

    def invalidate_authentication() -> None:
        _invalidate_authentication(
            database_url=target_database_url,
            psql_path=resolved_psql,
            timeout_seconds=timeout_seconds,
        )

    def recover_chat_outbox() -> None:
        _recover_chat_outbox(
            database_url=target_database_url,
            psql_path=resolved_psql,
            timeout_seconds=timeout_seconds,
        )

    def post_restore_verification() -> None:
        nonlocal database_verification

        database_verification = (
            _query_database_state(
                database_url=target_database_url,
                psql_path=resolved_psql,
                timeout_seconds=timeout_seconds,
                expected_migration_revision=(
                    expected_migration_revision
                ),
            )
        )

    try:
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=target_database_url,
            storage_root=target_storage,
            pg_restore_path=resolved_pg_restore,
            timeout_seconds=timeout_seconds,
            replace_existing_storage=False,
            invalidate_authentication=(
                invalidate_authentication
            ),
            recover_chat_outbox=(
                recover_chat_outbox
            ),
            post_restore_verification=(
                post_restore_verification
            ),
        )
    except (
        RestoreError,
        RestoreDrillError,
    ):
        raise
    except Exception as exc:
        raise RestoreDrillError(
            f"Restore drill failed: {exc}"
        ) from exc

    if database_verification is None:
        raise RestoreDrillError(
            "Post-restore database verification "
            "did not complete"
        )

    if not result.success:
        raise RestoreDrillError(
            "Restore service reported failure"
        )

    completed_at_datetime = datetime.now(timezone.utc)
    completed_at = completed_at_datetime.isoformat()
    duration_seconds = round(
        max(0.0, time.monotonic() - drill_started_monotonic),
        3,
    )
    database_artifact_decryption_authenticated = (
        True if database_artifact_encrypted else None
    )
    integrity_verification = {
        "database_artifact_size_and_sha256": "passed",
        "file_manifest_and_payload_checksums": "passed",
        "encrypted_database_authenticated_decryption": (
            "passed" if database_artifact_encrypted else "not_applicable"
        ),
    }
    recovery_objectives = _build_recovery_objective_evidence(
        rpo_target_seconds=rpo_target_seconds,
        rto_target_seconds=rto_target_seconds,
        backup_age_seconds_at_start=backup_age_seconds_at_drill_start,
        duration_seconds=duration_seconds,
    )

    report_target = None

    if report_path is not None:
        report_target = (
            Path(
                report_path
            ).expanduser().resolve()
        )

        report_target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    report = {
        "evidence_schema_version": "restore-drill-v2",
        "success": True,
        "drill_started_at": drill_started_at,
        "completed_at": completed_at,
        "duration_seconds": duration_seconds,
        "backup_created_at": backup_created_at,
        "backup_age_seconds_at_drill_start": (
            backup_age_seconds_at_drill_start
        ),
        "database_artifact_encrypted": database_artifact_encrypted,
        "database_artifact_key_version_hint": (
            database_artifact_key_version_hint
        ),
        "database_artifact_decryption_authenticated": (
            database_artifact_decryption_authenticated
        ),
        "integrity_verification": integrity_verification,
        "recovery_objectives": recovery_objectives,
        "backup_id": result.backup_id,
        "backup_path": str(
            backup_root
        ),
        "target_database": (
            result.database.database_name
        ),
        "target_storage_root": (
            result.files.storage_root
        ),
        "restored_file_count": (
            result.files.file_count
        ),
        "restored_total_size_bytes": (
            result.files.total_size_bytes
        ),
        "authentication_invalidated": (
            result.authentication_invalidated
        ),
        "outbox_recovery_completed": (
            result.outbox_recovery_completed
        ),
        "post_restore_verification_completed": (
            result.post_restore_verification_completed
        ),
        "database_verification": asdict(
            database_verification
        ),
    }

    if report_target is not None:
        report_target.write_text(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    return RestoreDrillResult(
        success=True,
        backup_id=result.backup_id,
        backup_path=str(
            backup_root
        ),
        target_database=result.database.database_name,
        target_storage_root=result.files.storage_root,
        restored_file_count=result.files.file_count,
        restored_total_size_bytes=result.files.total_size_bytes,
        authentication_invalidated=(
            result.authentication_invalidated
        ),
        outbox_recovery_completed=(
            result.outbox_recovery_completed
        ),
        post_restore_verification_completed=(
            result.post_restore_verification_completed
        ),
        database_verification=database_verification,
        drill_started_at=drill_started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
        backup_created_at=backup_created_at,
        backup_age_seconds_at_drill_start=(
            backup_age_seconds_at_drill_start
        ),
        database_artifact_encrypted=database_artifact_encrypted,
        database_artifact_key_version_hint=(
            database_artifact_key_version_hint
        ),
        database_artifact_decryption_authenticated=(
            database_artifact_decryption_authenticated
        ),
        integrity_verification=integrity_verification,
        recovery_objectives=recovery_objectives,
        report_path=(
            str(report_target)
            if report_target is not None
            else None
        ),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a controlled Clinic System Pro "
            "backup restore drill."
        )
    )

    parser.add_argument(
        "--backup-path",
        required=True,
        help=(
            "Path to a completed full backup directory"
        ),
    )

    parser.add_argument(
        "--target-storage-root",
        required=True,
        help=(
            "Dedicated filesystem target for "
            "restored application files"
        ),
    )

    parser.add_argument(
        "--report-path",
        default=None,
        help=(
            "Optional JSON report output path"
        ),
    )

    parser.add_argument(
        "--source-database-url",
        default=None,
        help=(
            "Optional source DB URL. Defaults "
            "to DATABASE_URL."
        ),
    )

    parser.add_argument(
        "--target-database-url",
        default=None,
        help=(
            "Dedicated PostgreSQL restore target. "
            "Defaults to RESTORE_DRILL_TARGET_DATABASE_URL."
        ),
    )

    parser.add_argument(
        "--pg-restore-path",
        default=None,
        help=(
            "Optional pg_restore executable path"
        ),
    )

    parser.add_argument(
        "--psql-path",
        default=None,
        help=(
            "Optional psql executable path"
        ),
    )

    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Timeout for PostgreSQL commands"
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    target_database_url = (
        args.target_database_url
        or os.environ.get(
            "RESTORE_DRILL_TARGET_DATABASE_URL"
        )
    )

    try:
        result = run_restore_drill(
            backup_path=args.backup_path,
            target_database_url=(
                target_database_url
            ),
            target_storage_root=(
                args.target_storage_root
            ),
            source_database_url=(
                args.source_database_url
            ),
            pg_restore_path=(
                args.pg_restore_path
            ),
            psql_path=args.psql_path,
            timeout_seconds=(
                args.timeout_seconds
            ),
            report_path=args.report_path,
        )
    except (
        RestoreDrillError,
        RestoreError,
    ) as exc:
        print(
            f"RESTORE DRILL FAILED: {exc}"
        )
        return 1

    payload = asdict(
        result
    )

    print(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
        )
    )

    print(
        "\nRESTORE DRILL PASSED"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )