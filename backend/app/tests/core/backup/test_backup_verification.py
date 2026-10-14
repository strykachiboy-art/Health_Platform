from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from app.core.backup.file_backup import backup_files
from app.core.backup.backup_verification import (
    BackupVerificationError,
    verify_database_backup,
    verify_file_backup,
    verify_full_backup,
)


def _write_file(
    path: Path,
    payload: bytes,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_bytes(
        payload
    )


def _sha256(
    payload: bytes,
) -> str:
    return hashlib.sha256(
        payload
    ).hexdigest()


def test_verify_database_backup_accepts_valid_artifact(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    artifact = (
        root
        / "database.dump"
    )

    payload = (
        b"synthetic-postgresql-dump"
    )

    _write_file(
        artifact,
        payload,
    )

    result = verify_database_backup(
        backup_root=root,
        database_metadata={
            "backup_path": str(
                artifact
            ),
            "backup_size_bytes": len(
                payload
            ),
            "backup_sha256": _sha256(
                payload
            ),
        },
    )

    assert result.valid is True
    assert result.size_bytes == len(
        payload
    )
    assert result.sha256 == _sha256(
        payload
    )


def test_verify_database_backup_rejects_missing_artifact(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    with pytest.raises(
        BackupVerificationError,
        match="does not exist",
    ):
        verify_database_backup(
            backup_root=root,
            database_metadata={
                "backup_path": str(
                    root
                    / "database.dump"
                ),
                "backup_size_bytes": 10,
                "backup_sha256": (
                    "a" * 64
                ),
            },
        )


def test_verify_database_backup_rejects_size_mismatch(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"database"
    )

    artifact = (
        root
        / "database.dump"
    )

    _write_file(
        artifact,
        payload,
    )

    with pytest.raises(
        BackupVerificationError,
        match="size mismatch",
    ):
        verify_database_backup(
            backup_root=root,
            database_metadata={
                "backup_path": str(
                    artifact
                ),
                "backup_size_bytes": 999,
                "backup_sha256": _sha256(
                    payload
                ),
            },
        )


def test_verify_database_backup_rejects_checksum_mismatch(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"database"
    )

    artifact = (
        root
        / "database.dump"
    )

    _write_file(
        artifact,
        payload,
    )

    with pytest.raises(
        BackupVerificationError,
        match="checksum mismatch",
    ):
        verify_database_backup(
            backup_root=root,
            database_metadata={
                "backup_path": str(
                    artifact
                ),
                "backup_size_bytes": len(
                    payload
                ),
                "backup_sha256": (
                    "b" * 64
                ),
            },
        )


def test_verify_database_backup_runs_readability_validator(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"database"
    )

    artifact = (
        root
        / "database.dump"
    )

    _write_file(
        artifact,
        payload,
    )

    observed = {
        "path": None,
    }

    def validator(path):
        observed["path"] = path

    verify_database_backup(
        backup_root=root,
        database_metadata={
            "backup_path": str(
                artifact
            ),
            "backup_size_bytes": len(
                payload
            ),
            "backup_sha256": _sha256(
                payload
            ),
        },
        readable_validator=validator,
    )

    assert observed["path"] == (
        artifact.resolve()
    )


def test_verify_file_backup_accepts_valid_manifest_and_file(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"clinical-file"
    )

    backed_up_file = (
        root
        / "files"
        / "profile_images"
        / "photo.jpg"
    )

    _write_file(
        backed_up_file,
        payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(
            payload
        ),
        "files": [
            {
                "storage_key": (
                    "profile_images/photo.jpg"
                ),
                "physical_path": (
                    "original/photo.jpg"
                ),
                "backup_path": (
                    "files/profile_images/photo.jpg"
                ),
                "sha256": _sha256(
                    payload
                ),
                "size_bytes": len(
                    payload
                ),
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    result = verify_file_backup(
        backup_root=root,
        file_metadata={
            "manifest_path": str(
                manifest_path
            ),
            "manifest_sha256": _sha256(
                manifest_path.read_bytes()
            ),
        },
    )

    assert result.valid is True
    assert result.file_count == 1
    assert result.total_size_bytes == len(
        payload
    )


def test_verify_file_backup_rejects_missing_manifest(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    with pytest.raises(
        BackupVerificationError,
        match="does not exist",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    root
                    / "files"
                    / "manifest.json"
                ),
                "manifest_sha256": (
                    "a" * 64
                ),
            },
        )


def test_verify_file_backup_rejects_corrupted_manifest(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        b'{"corrupted":true}',
    )

    with pytest.raises(
        BackupVerificationError,
        match="manifest checksum mismatch",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": (
                    "a" * 64
                ),
            },
        )


def test_verify_file_backup_rejects_missing_backed_up_file(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": 10,
        "files": [
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "backup_path": (
                    "files/profile/photo.jpg"
                ),
                "sha256": "a" * 64,
                "size_bytes": 10,
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    with pytest.raises(
        BackupVerificationError,
        match="does not exist",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": _sha256(
                    manifest_path.read_bytes()
                ),
            },
        )


def test_verify_file_backup_rejects_file_checksum_mismatch(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"real-content"
    )

    backed_up_file = (
        root
        / "files"
        / "profile/photo.jpg"
    )

    _write_file(
        backed_up_file,
        payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(
            payload
        ),
        "files": [
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "backup_path": (
                    "files/profile/photo.jpg"
                ),
                "sha256": "a" * 64,
                "size_bytes": len(
                    payload
                ),
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    with pytest.raises(
        BackupVerificationError,
        match="checksum mismatch",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": _sha256(
                    manifest_path.read_bytes()
                ),
            },
        )


def test_verify_file_backup_rejects_manifest_count_mismatch(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    manifest = {
        "version": 1,
        "file_count": 2,
        "total_size_bytes": 0,
        "files": [],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    with pytest.raises(
        BackupVerificationError,
        match="file count mismatch",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": _sha256(
                    manifest_path.read_bytes()
                ),
            },
        )


def test_verify_file_backup_validates_database_file_references(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"referenced-file"
    )

    backed_up_file = (
        root
        / "files"
        / "profile/photo.jpg"
    )

    _write_file(
        backed_up_file,
        payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(
            payload
        ),
        "files": [
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "backup_path": (
                    "files/profile/photo.jpg"
                ),
                "sha256": _sha256(
                    payload
                ),
                "size_bytes": len(
                    payload
                ),
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
                "clinic_id": 7,
                "entity_type": "User",
                "entity_id": 42,
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    result = verify_file_backup(
        backup_root=root,
        file_metadata={
            "manifest_path": str(
                manifest_path
            ),
            "manifest_sha256": _sha256(
                manifest_path.read_bytes()
            ),
        },
        expected_references=[
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "clinic_id": 7,
                "entity_type": "User",
                "entity_id": 42,
            }
        ],
    )

    assert result.valid is True


def test_verify_file_backup_rejects_missing_database_reference(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    manifest = {
        "version": 1,
        "file_count": 0,
        "total_size_bytes": 0,
        "files": [],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    with pytest.raises(
        BackupVerificationError,
        match="missing from backup manifest",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": _sha256(
                    manifest_path.read_bytes()
                ),
            },
            expected_references=[
                {
                    "storage_key": (
                        "profile/missing.jpg"
                    )
                }
            ],
        )


def test_verify_file_backup_rejects_database_reference_metadata_mismatch(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    payload = (
        b"referenced-file"
    )

    backed_up_file = (
        root
        / "files"
        / "profile/photo.jpg"
    )

    _write_file(
        backed_up_file,
        payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(
            payload
        ),
        "files": [
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "backup_path": (
                    "files/profile/photo.jpg"
                ),
                "sha256": _sha256(
                    payload
                ),
                "size_bytes": len(
                    payload
                ),
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
                "clinic_id": 7,
                "entity_type": "User",
                "entity_id": 42,
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    with pytest.raises(
        BackupVerificationError,
        match="reference mismatch",
    ):
        verify_file_backup(
            backup_root=root,
            file_metadata={
                "manifest_path": str(
                    manifest_path
                ),
                "manifest_sha256": _sha256(
                    manifest_path.read_bytes()
                ),
            },
            expected_references=[
                {
                    "storage_key": (
                        "profile/photo.jpg"
                    ),
                    "clinic_id": 8,
                    "entity_type": "User",
                    "entity_id": 42,
                }
            ],
        )


def test_verify_full_backup_accepts_valid_database_and_files(
    tmp_path,
):
    root = (
        tmp_path
        / "backup"
    )

    database_payload = (
        b"database-dump"
    )

    database_path = (
        root
        / "database.dump"
    )

    _write_file(
        database_path,
        database_payload,
    )

    file_payload = (
        b"clinical-file"
    )

    backed_up_file = (
        root
        / "files"
        / "profile/photo.jpg"
    )

    _write_file(
        backed_up_file,
        file_payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(
            file_payload
        ),
        "files": [
            {
                "storage_key": (
                    "profile/photo.jpg"
                ),
                "backup_path": (
                    "files/profile/photo.jpg"
                ),
                "sha256": _sha256(
                    file_payload
                ),
                "size_bytes": len(
                    file_payload
                ),
                "modified_at": (
                    "2026-09-24T00:00:00+00:00"
                ),
            }
        ],
    }

    manifest_path = (
        root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        (
            json.dumps(
                manifest,
                sort_keys=True,
            )
            + "\n"
        ).encode(
            "utf-8"
        ),
    )

    result = verify_full_backup(
        backup_path=root,
        database_metadata={
            "backup_path": str(
                database_path
            ),
            "backup_size_bytes": len(
                database_payload
            ),
            "backup_sha256": _sha256(
                database_payload
            ),
        },
        file_metadata={
            "manifest_path": str(
                manifest_path
            ),
            "manifest_sha256": _sha256(
                manifest_path.read_bytes()
            ),
        },
    )

    assert result.success is True
    assert result.cross_system_valid is True
    assert result.database.valid is True
    assert result.files.valid is True


def test_verify_full_backup_rejects_missing_backup_directory(
    tmp_path,
):
    with pytest.raises(
        BackupVerificationError,
        match="does not exist",
    ):
        verify_full_backup(
            backup_path=(
                tmp_path
                / "missing-backup"
            ),
            database_metadata={},
            file_metadata={},
        )
def _build_encrypted_file_backup(app, tmp_path):
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"b" * 32).decode("ascii")
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"i" * 32).decode("ascii")
    )

    storage = tmp_path / "source-storage"
    source = storage / "profile_images" / "private-photo.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic-private-clinical-file-payload")
    backup_root = tmp_path / "encrypted-full-backup"
    output = backup_root / "files"

    with app.app_context():
        result = backup_files(
            storage_root=storage,
            output_path=output,
        )

    metadata = {
        "success": result.success,
        "backup_path": result.backup_path,
        "manifest_path": result.manifest_path,
        "manifest_sha256": result.manifest_sha256,
        "file_count": result.file_count,
        "total_size_bytes": result.total_size_bytes,
        "encrypted": result.encrypted,
    }
    return backup_root, metadata, result


def test_verify_file_backup_accepts_encrypted_artifacts(
    app,
    tmp_path,
):
    output, metadata, backup = _build_encrypted_file_backup(
        app,
        tmp_path,
    )

    with app.app_context():
        result = verify_file_backup(
            backup_root=output,
            file_metadata=metadata,
        )

    assert result.valid is True
    assert result.file_count == 1
    assert result.total_size_bytes == len(
        b"synthetic-private-clinical-file-payload"
    )
    assert result.manifest_sha256 == metadata["manifest_sha256"]
    assert backup.encrypted is True


def test_verify_file_backup_rejects_tampered_encrypted_artifact(
    app,
    tmp_path,
):
    output, metadata, backup = _build_encrypted_file_backup(
        app,
        tmp_path,
    )

    artifact = (
        Path(backup.backup_path)
        / Path(backup.files[0].backup_path)
    )
    corrupted = bytearray(artifact.read_bytes())
    corrupted[-1] ^= 1
    artifact.write_bytes(corrupted)

    with app.app_context():
        with pytest.raises(
            BackupVerificationError,
            match="Unable to decrypt encrypted file artifact",
        ):
            verify_file_backup(
                backup_root=output,
                file_metadata=metadata,
            )


def test_verify_file_backup_rejects_non_boolean_encryption_marker(
    tmp_path,
):
    with pytest.raises(
        BackupVerificationError,
        match="encryption marker must be a boolean",
    ):
        verify_file_backup(
            backup_root=tmp_path,
            file_metadata={
                "manifest_path": "files/manifest.json",
                "manifest_sha256": "a" * 64,
                "encrypted": "true",
            },
        )
