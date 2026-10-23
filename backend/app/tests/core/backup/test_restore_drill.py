from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.backup.restore_drill import (
    RestoreDrillError,
    run_restore_drill,
)


def _write_backup_metadata(
    backup_root: Path,
    migration_revision: str | None = "abc123",
) -> None:
    backup_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    (
        backup_root
        / "metadata.json"
    ).write_text(
        json.dumps(
            {
                "database": {
                    "migration_revision": (
                        migration_revision
                    ),
                },
                "files": {},
                "success": True,
            }
        ),
        encoding="utf-8",
    )


def test_restore_drill_requires_confirmation(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv(
        "RESTORE_DRILL_CONFIRMATION",
        raising=False,
    )

    backup_root = (
        tmp_path
        / "backup"
    )

    _write_backup_metadata(
        backup_root
    )

    with pytest.raises(
        RestoreDrillError,
        match="RESTORE_DRILL_CONFIRMATION",
    ):
        run_restore_drill(
            backup_path=backup_root,
            source_database_url=(
                "postgresql://localhost/"
                "clinic_system"
            ),
            target_database_url=(
                "postgresql://localhost/"
                "clinic_system_restore_drill"
            ),
            target_storage_root=(
                tmp_path
                / "restore-storage"
            ),
            pg_restore_path="pg_restore",
            psql_path="psql",
        )


def test_restore_drill_rejects_same_database(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "RESTORE_DRILL_CONFIRMATION",
        "RESTORE_DRILL_APPROVED",
    )

    backup_root = (
        tmp_path
        / "backup"
    )

    _write_backup_metadata(
        backup_root
    )

    with pytest.raises(
        RestoreDrillError,
        match="differ from the source database",
    ):
        run_restore_drill(
            backup_path=backup_root,
            source_database_url=(
                "postgresql://localhost/"
                "clinic_system"
            ),
            target_database_url=(
                "postgresql://localhost/"
                "clinic_system"
            ),
            target_storage_root=(
                tmp_path
                / "restore-storage"
            ),
            pg_restore_path="pg_restore",
            psql_path="psql",
        )


def test_restore_drill_rejects_existing_storage_target(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "RESTORE_DRILL_CONFIRMATION",
        "RESTORE_DRILL_APPROVED",
    )

    backup_root = (
        tmp_path
        / "backup"
    )

    _write_backup_metadata(
        backup_root
    )

    storage_root = (
        tmp_path
        / "restore-storage"
    )

    storage_root.mkdir()

    with pytest.raises(
        RestoreDrillError,
        match="must not already exist",
    ):
        run_restore_drill(
            backup_path=backup_root,
            source_database_url=(
                "postgresql://localhost/"
                "clinic_system"
            ),
            target_database_url=(
                "postgresql://localhost/"
                "clinic_system_restore_drill"
            ),
            target_storage_root=storage_root,
            pg_restore_path="pg_restore",
            psql_path="psql",
        )


def test_restore_drill_executes_full_controlled_flow(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "RESTORE_DRILL_CONFIRMATION",
        "RESTORE_DRILL_APPROVED",
    )
    monkeypatch.delenv("BACKUP_RPO_TARGET_SECONDS", raising=False)
    monkeypatch.delenv("BACKUP_RTO_TARGET_SECONDS", raising=False)

    backup_root = (
        tmp_path
        / "backup"
    )

    _write_backup_metadata(
        backup_root
    )

    restore_result = Mock(
        success=True,
        backup_id="backup-001",
    )

    restore_result.database.database_name = (
        "clinic_system_restore_drill"
    )

    restore_result.files.storage_root = str(
        tmp_path
        / "restore-storage"
    )

    restore_result.files.file_count = 3
    restore_result.files.total_size_bytes = 900
    restore_result.authentication_invalidated = True
    restore_result.outbox_recovery_completed = True
    restore_result.post_restore_verification_completed = True

    monkeypatch.setattr(
        "app.core.backup.restore_drill.restore_full_backup",
        lambda **kwargs: (
            kwargs["invalidate_authentication"](),
            kwargs["recover_chat_outbox"](),
            kwargs["post_restore_verification"](),
            restore_result,
        )[-1],
    )

    def fake_resolve(
        configured_path,
        default_name,
        *,
        field_name,
    ):
        return default_name

    monkeypatch.setattr(
        "app.core.backup.restore_drill._resolve_executable",
        fake_resolve,
    )

    calls = []

    def fake_psql(
        *,
        database_url,
        psql_path,
        sql,
        timeout_seconds,
    ):
        calls.append(sql)

        if "current_database()" in sql:
            return json.dumps(
                {
                    "database_name": (
                        "clinic_system_restore_drill"
                    ),
                    "migration_revision": "abc123",
                    "user_count": 5,
                    "clinic_count": 2,
                    "audit_log_count": 20,
                    "chat_outbox_count": 7,
                    "processing_outbox_count": 0,
                }
            )

        if "json_build_object(" in sql:
            return json.dumps(
                {
                    "user_count": 10,
                    "token_sum": 5,
                }
            )

        if "COALESCE(SUM(token_version)" in sql:
            return "15"

        return "0"

    monkeypatch.setattr(
        "app.core.backup.restore_drill._run_psql",
        fake_psql,
    )

    storage_root = (
        tmp_path
        / "restore-storage"
    )

    result = run_restore_drill(
        backup_path=backup_root,
        source_database_url=(
            "postgresql://localhost/"
            "clinic_system"
        ),
        target_database_url=(
            "postgresql://localhost/"
            "clinic_system_restore_drill"
        ),
        target_storage_root=storage_root,
        pg_restore_path="pg_restore",
        psql_path="psql",
    )

    assert result.success is True
    assert result.backup_id == (
        "backup-001"
    )
    assert result.target_database == (
        "clinic_system_restore_drill"
    )
    assert result.restored_file_count == 3
    assert result.authentication_invalidated is True
    assert result.outbox_recovery_completed is True
    assert result.post_restore_verification_completed is True
    assert result.database_verification.user_count == 5
    assert result.database_verification.clinic_count == 2
    assert result.database_verification.migration_revision == (
        "abc123"
    )
    assert result.recovery_objectives["rpo_freshness_proxy_status"] == "not_configured"
    assert result.recovery_objectives["rto_status"] == "not_configured"
    assert calls


def test_restore_drill_writes_json_report(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(
        "RESTORE_DRILL_CONFIRMATION",
        "RESTORE_DRILL_APPROVED",
    )

    backup_root = (
        tmp_path
        / "backup"
    )

    _write_backup_metadata(
        backup_root
    )

    metadata_path = backup_root / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["created_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    metadata["database"]["encrypted"] = True
    metadata["database"]["backup_path"] = "database.dump"
    metadata_path.write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )
    (backup_root / "database.dump").write_bytes(b"synthetic-ciphertext")

    monkeypatch.setattr(
        "app.core.backup.restore_drill.read_backup_artifact_key_version",
        lambda _path: 7,
    )
    monkeypatch.setenv("BACKUP_RPO_TARGET_SECONDS", "3600")
    monkeypatch.setenv("BACKUP_RTO_TARGET_SECONDS", "3600")

    restore_result = Mock(
        success=True,
        backup_id="backup-002",
    )

    restore_result.database.database_name = (
        "clinic_system_restore_drill"
    )

    restore_result.files.storage_root = str(
        tmp_path
        / "restore-storage"
    )

    restore_result.files.file_count = 1
    restore_result.files.total_size_bytes = 100
    restore_result.authentication_invalidated = True
    restore_result.outbox_recovery_completed = True
    restore_result.post_restore_verification_completed = True

    monkeypatch.setattr(
        "app.core.backup.restore_drill.restore_full_backup",
        lambda **kwargs: (
            kwargs["invalidate_authentication"](),
            kwargs["recover_chat_outbox"](),
            kwargs["post_restore_verification"](),
            restore_result,
        )[-1],
    )

    monkeypatch.setattr(
        "app.core.backup.restore_drill._resolve_executable",
        lambda *args, **kwargs: "tool",
    )

    def fake_psql(
        *,
        database_url,
        psql_path,
        sql,
        timeout_seconds,
    ):
        if "current_database()" in sql:
            return json.dumps(
                {
                    "database_name": (
                        "clinic_system_restore_drill"
                    ),
                    "migration_revision": "abc123",
                    "user_count": 1,
                    "clinic_count": 1,
                    "audit_log_count": 1,
                    "chat_outbox_count": 0,
                    "processing_outbox_count": 0,
                }
            )

        if "json_build_object(" in sql:
            return json.dumps(
                {
                    "user_count": 1,
                    "token_sum": 0,
                }
            )

        if "COALESCE(SUM(token_version)" in sql:
            return "1"

        return "0"

    monkeypatch.setattr(
        "app.core.backup.restore_drill._run_psql",
        fake_psql,
    )

    report_path = (
        tmp_path
        / "restore-drill-report.json"
    )

    result = run_restore_drill(
        backup_path=backup_root,
        source_database_url=(
            "postgresql://localhost/"
            "clinic_system"
        ),
        target_database_url=(
            "postgresql://localhost/"
            "clinic_system_restore_drill"
        ),
        target_storage_root=(
            tmp_path
            / "restore-storage"
        ),
        pg_restore_path="pg_restore",
        psql_path="psql",
        report_path=report_path,
    )

    assert result.report_path == str(
        report_path.resolve()
    )

    payload = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    assert payload["success"] is True
    assert payload["backup_id"] == (
        "backup-002"
    )
    assert payload[
        "database_verification"
    ]["migration_revision"] == (
        "abc123"
    )
    assert payload["evidence_schema_version"] == "restore-drill-v2"
    assert payload["drill_started_at"]
    assert payload["completed_at"]
    assert payload["duration_seconds"] >= 0
    assert payload["backup_age_seconds_at_drill_start"] >= 7190
    assert payload["database_artifact_encrypted"] is True
    assert payload["database_artifact_key_version_hint"] == 7
    assert payload["database_artifact_decryption_authenticated"] is True
    assert payload["integrity_verification"] == {
        "database_artifact_size_and_sha256": "passed",
        "file_manifest_and_payload_checksums": "passed",
        "encrypted_database_authenticated_decryption": "passed",
    }
    assert payload["recovery_objectives"]["rpo_freshness_proxy_status"] == "exceeds_target"
    assert payload["recovery_objectives"]["rpo_authoritatively_verified"] is False
    assert payload["recovery_objectives"]["rto_status"] == "within_target"
    assert payload["recovery_objectives"]["rto_target_seconds"] == 3600