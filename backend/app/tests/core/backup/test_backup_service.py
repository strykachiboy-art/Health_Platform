from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pytest

from app.core.backup.backup_service import (
    BackupServiceError,
    create_full_backup,
)


@dataclass(frozen=True)
class FakeDatabaseBackupResult:
    success: bool = True
    database_name: str = "clinic_system"
    backup_size_bytes: int = 128
    backup_sha256: str = "database-sha"
    format: str = "custom"


@dataclass(frozen=True)
class FakeFileBackupResult:
    success: bool = True
    file_count: int = 2
    total_size_bytes: int = 256
    manifest_sha256: str = "files-sha"
    files: tuple[dict[str, object], ...] = ()
    backup_path: str | None = None
    manifest_path: str | None = None


def test_create_full_backup_requires_database_and_file_success(
    app,
    tmp_path,
    monkeypatch,
):
    database_calls = []
    file_calls = []

    def fake_database_backup(**kwargs):
        database_calls.append(
            kwargs
        )

        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        file_calls.append(
            kwargs
        )

        output = Path(
            kwargs["output_path"]
        )

        output.mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult()

    monkeypatch.setattr(
        "app.core.backup.backup_service.backup_database",
        fake_database_backup,
    )

    monkeypatch.setattr(
        "app.core.backup.backup_service.backup_files",
        fake_file_backup,
    )

    with app.app_context():
        result = create_full_backup(
            database_url=(
                "postgresql://backup"
            ),
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
        )

    assert result["success"] is True
    assert result["backup_id"]
    assert result["backup_path"]
    assert result["metadata_path"]
    assert result["metadata_sha256"]

    assert len(
        database_calls
    ) == 1

    assert len(
        file_calls
    ) == 1

    backup_directory = Path(
        result["backup_path"]
    )

    assert (
        backup_directory
        / "database.dump"
    ).exists()

    assert (
        backup_directory
        / "metadata.json"
    ).exists()


def test_create_full_backup_passes_database_configuration(
    app,
    tmp_path,
):
    calls = {
        "database": None,
        "files": None,
    }

    def fake_database_backup(**kwargs):
        calls["database"] = kwargs

        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        calls["files"] = kwargs

        Path(
            kwargs["output_path"]
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult()

    with app.app_context():
        result = create_full_backup(
            database_url=(
                "postgresql://configured"
            ),
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
            migration_revision="abc123",
            pg_dump_path="pg_dump",
            timeout_seconds=600,
            database_backup_callable=(
                fake_database_backup
            ),
            file_backup_callable=(
                fake_file_backup
            ),
        )

    assert result["success"] is True

    assert calls["database"][
        "database_url"
    ] == (
        "postgresql://configured"
    )

    assert calls["database"][
        "migration_revision"
    ] == "abc123"

    assert calls["database"][
        "pg_dump_path"
    ] == "pg_dump"

    assert calls["database"][
        "timeout_seconds"
    ] == 600


def test_create_full_backup_uses_configured_database_url(
    app,
    tmp_path,
):
    observed = {
        "database_url": None,
    }

    def fake_database_backup(**kwargs):
        observed[
            "database_url"
        ] = kwargs[
            "database_url"
        ]

        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult()

    app.config[
        "SQLALCHEMY_DATABASE_URI"
    ] = (
        "postgresql://configured"
    )

    with app.app_context():
        result = create_full_backup(
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
            database_backup_callable=(
                fake_database_backup
            ),
            file_backup_callable=(
                fake_file_backup
            ),
        )

    assert result["success"] is True
    assert observed[
        "database_url"
    ] == (
        "postgresql://configured"
    )


def test_create_full_backup_rejects_database_failure(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"partial"
        )

        return FakeDatabaseBackupResult(
            success=False
        )

    def fake_file_backup(**kwargs):
        return FakeFileBackupResult()

    with app.app_context():
        with pytest.raises(
            BackupServiceError,
            match="Database backup did not complete successfully",
        ):
            create_full_backup(
                database_url=(
                    "postgresql://backup"
                ),
                backup_root=tmp_path,
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                database_backup_callable=(
                    fake_database_backup
                ),
                file_backup_callable=(
                    fake_file_backup
                ),
            )

    assert not any(
        tmp_path.iterdir()
    )


def test_create_full_backup_rejects_file_failure(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        raise RuntimeError(
            "synthetic file failure"
        )

    with app.app_context():
        with pytest.raises(
            BackupServiceError,
            match="Full backup failed",
        ):
            create_full_backup(
                database_url=(
                    "postgresql://backup"
                ),
                backup_root=tmp_path,
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                database_backup_callable=(
                    fake_database_backup
                ),
                file_backup_callable=(
                    fake_file_backup
                ),
            )

    assert not any(
        tmp_path.iterdir()
    )


def test_create_full_backup_rejects_partial_file_result(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult(
            success=False
        )

    with app.app_context():
        with pytest.raises(
            BackupServiceError,
            match="File backup did not complete successfully",
        ):
            create_full_backup(
                database_url=(
                    "postgresql://backup"
                ),
                backup_root=tmp_path,
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                database_backup_callable=(
                    fake_database_backup
                ),
                file_backup_callable=(
                    fake_file_backup
                ),
            )

    assert not any(
        tmp_path.iterdir()
    )


def test_create_full_backup_writes_authoritative_metadata(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        Path(
            kwargs["output_path"]
        ).write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult()

    with app.app_context():
        result = create_full_backup(
            database_url=(
                "postgresql://backup"
            ),
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
            database_backup_callable=(
                fake_database_backup
            ),
            file_backup_callable=(
                fake_file_backup
            ),
        )

    metadata_path = Path(
        result["metadata_path"]
    )

    assert metadata_path.exists()

    metadata_text = (
        metadata_path.read_text(
            encoding="utf-8"
        )
    )

    assert '"success": true' in (
        metadata_text.lower()
    )

    assert (
        result["backup_id"]
        in metadata_text
    )


def test_create_full_backup_creates_unique_backup_directories(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        output = Path(
            kwargs["output_path"]
        )

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        output.write_bytes(
            b"database"
        )

        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        Path(
            kwargs["output_path"]
        ).mkdir(
            parents=True,
            exist_ok=True,
        )

        return FakeFileBackupResult()

    with app.app_context():
        first = create_full_backup(
            database_url=(
                "postgresql://backup"
            ),
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
            database_backup_callable=(
                fake_database_backup
            ),
            file_backup_callable=(
                fake_file_backup
            ),
        )

        second = create_full_backup(
            database_url=(
                "postgresql://backup"
            ),
            backup_root=tmp_path,
            storage_root=(
                tmp_path
                / "storage"
            ),
            database_backup_callable=(
                fake_database_backup
            ),
            file_backup_callable=(
                fake_file_backup
            ),
        )

    assert first[
        "backup_id"
    ] != second[
        "backup_id"
    ]

    assert Path(
        first["backup_path"]
    ).exists()

    assert Path(
        second["backup_path"]
    ).exists()
def test_create_full_backup_does_not_persist_file_entry_details(
    app,
    tmp_path,
):
    private_filename = "private-patient-photo.png"
    private_storage_key = f"profile_images/{private_filename}"
    private_physical_path = "C:/private/clinic-storage/" + private_filename

    def fake_database_backup(**kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic-database")
        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        Path(kwargs["output_path"]).mkdir(parents=True, exist_ok=True)
        return FakeFileBackupResult(
            files=(
                {
                    "storage_key": private_storage_key,
                    "physical_path": private_physical_path,
                    "backup_path": "files/opaque-test-name.enc",
                    "sha256": "a" * 64,
                    "size_bytes": 12,
                    "modified_at": "2026-10-14T00:00:00+00:00",
                    "clinic_id": 7,
                    "entity_type": "User",
                    "entity_id": 42,
                },
            )
        )

    with app.app_context():
        result = create_full_backup(
            database_url="postgresql://backup",
            backup_root=tmp_path,
            storage_root=tmp_path / "storage",
            database_backup_callable=fake_database_backup,
            file_backup_callable=fake_file_backup,
        )

    metadata_text = Path(result["metadata_path"]).read_text(
        encoding="utf-8"
    )

    assert private_filename not in metadata_text
    assert private_storage_key not in metadata_text
    assert private_physical_path not in metadata_text
    assert '"storage_key"' not in metadata_text
    assert '"physical_path"' not in metadata_text

    # Runtime metadata used by verification must also omit the listing.
    assert "files" not in result["files"]
    assert result["files"]["file_count"] == 2
    assert result["files"]["manifest_sha256"] == "files-sha"
def test_create_full_backup_stores_file_paths_relative_to_backup_root(
    app,
    tmp_path,
):
    def fake_database_backup(**kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic-database")
        return FakeDatabaseBackupResult()

    def fake_file_backup(**kwargs):
        output = Path(kwargs["output_path"])
        output.mkdir(parents=True, exist_ok=True)

        return FakeFileBackupResult(
            backup_path=str(output),
            manifest_path=str(output / "manifest.json.enc"),
        )

    with app.app_context():
        result = create_full_backup(
            database_url="postgresql://backup",
            backup_root=tmp_path,
            storage_root=tmp_path / "storage",
            database_backup_callable=fake_database_backup,
            file_backup_callable=fake_file_backup,
        )

    assert result["files"]["backup_path"] == "files"
    assert result["files"]["manifest_path"] == "files/manifest.json.enc"
    assert not Path(result["files"]["backup_path"]).is_absolute()
    assert not Path(result["files"]["manifest_path"]).is_absolute()

    metadata = json.loads(
        Path(result["metadata_path"]).read_text(encoding="utf-8")
    )
    assert metadata["files"]["backup_path"] == "files"
    assert metadata["files"]["manifest_path"] == "files/manifest.json.enc"
