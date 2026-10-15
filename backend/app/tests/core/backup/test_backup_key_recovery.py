from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.backup.backup_encryption import (
    encrypt_backup_artifact,
    read_backup_artifact_key_version,
)
from app.core.backup.restore_service import (
    RestoreError,
    restore_full_backup,
)


def _backup_key() -> str:
    return base64.urlsafe_b64encode(
        b"b" * 32
    ).decode("ascii")


def _integration_key() -> str:
    return base64.urlsafe_b64encode(
        b"i" * 32
    ).decode("ascii")


@pytest.fixture(autouse=True)
def _backup_encryption_context(app):
    app.config["BACKUP_ENCRYPTION_KEY"] = _backup_key()
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        _integration_key()
    )

    with app.app_context():
        yield


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_file(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _build_encrypted_backup(
    tmp_path,
    *,
    database_payload=b"database-dump",
):
    backup_root = tmp_path / "backup"
    database_path = backup_root / "database.dump"
    source_path = backup_root / "database-source.dump"

    _write_file(
        source_path,
        database_payload,
    )

    encrypt_backup_artifact(
        source_path,
        database_path,
    )

    source_path.unlink()

    restored_storage_key = "profile_images/photo.jpg"
    file_payload = b"clinical-file"

    backup_file = (
        backup_root
        / "files"
        / restored_storage_key
    )

    _write_file(
        backup_file,
        file_payload,
    )

    manifest = {
        "version": 1,
        "file_count": 1,
        "total_size_bytes": len(file_payload),
        "files": [
            {
                "storage_key": restored_storage_key,
                "physical_path": (
                    "storage/profile_images/photo.jpg"
                ),
                "backup_path": (
                    "files/profile_images/photo.jpg"
                ),
                "sha256": _sha256(file_payload),
                "size_bytes": len(file_payload),
                "modified_at": (
                    "2026-10-08T00:00:00+00:00"
                ),
            }
        ],
    }

    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    manifest_path = (
        backup_root
        / "files"
        / "manifest.json"
    )

    _write_file(
        manifest_path,
        manifest_bytes,
    )

    metadata = {
        "version": 1,
        "backup_id": "backup-recovery-test-001",
        "created_at": (
            "2026-10-08T00:00:00+00:00"
        ),
        "success": True,
        "database": {
            "success": True,
            "database_name": "clinic_system",
            "database_host": "localhost",
            "database_port": 5432,
            "backup_path": str(database_path),
            "backup_size_bytes": database_path.stat().st_size,
            "backup_sha256": _sha256(
                database_path.read_bytes()
            ),
            "format": "custom",
            "encrypted": True,
        },
        "files": {
            "success": True,
            "backup_path": str(
                backup_root / "files"
            ),
            "manifest_path": str(manifest_path),
            "manifest_sha256": _sha256(
                manifest_bytes
            ),
            "file_count": 1,
            "total_size_bytes": len(file_payload),
        },
    }

    metadata_path = backup_root / "metadata.json"

    _write_file(
        metadata_path,
        (
            json.dumps(
                metadata,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    )

    return backup_root


def test_backup_key_recovery_restores_after_primary_key_loss(
    app,
    tmp_path,
    monkeypatch,
):
    backup_root = _build_encrypted_backup(tmp_path)

    recovery_secret_store = {
        "backup_key_version": "1",
        "backup_encryption_key": _backup_key(),
    }

    app.config.pop(
        "BACKUP_ENCRYPTION_KEY",
        None,
    )

    def fail_restore(*args, **kwargs):
        raise AssertionError(
            "pg_restore must not run while the primary backup key is lost"
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fail_restore,
    )

    with pytest.raises(
        RestoreError,
        match="Unable to decrypt database backup",
    ):
        restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=tmp_path / "failed-storage",
        )

    app.config["BACKUP_ENCRYPTION_KEY"] = (
        recovery_secret_store[
            "backup_encryption_key"
        ]
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = (
        recovery_secret_store[
            "backup_key_version"
        ]
    )

    observed = {
        "command": None,
    }

    def fake_restore(command, **kwargs):
        observed["command"] = command
        return Mock(
            returncode=0,
            stderr="",
            stdout="",
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fake_restore,
    )

    storage_root = tmp_path / "restored-storage"

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=storage_root,
        )

    assert result.success is True
    assert result.database.success is True
    assert result.files.success is True
    assert result.files.file_count == 1

    assert observed["command"] is not None
    assert (
        observed["command"][-1]
        != str(backup_root / "database.dump")
    )
    assert not Path(
        observed["command"][-1]
    ).exists()

    assert (
        storage_root
        / "profile_images"
        / "photo.jpg"
    ).read_bytes() == b"clinical-file"


def test_backup_key_recovery_restores_historical_key_after_rotation(
    app,
    tmp_path,
    monkeypatch,
):
    backup_root = _build_encrypted_backup(tmp_path)
    artifact_path = backup_root / "database.dump"

    stored_version = read_backup_artifact_key_version(artifact_path)
    assert stored_version == 1

    # This map represents an externally managed recovery-key inventory.
    recovery_key_inventory = {
        str(stored_version): _backup_key(),
    }

    # Simulate rotation: the active key/version can no longer decrypt v1.
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(
            b"c" * 32
        ).decode("ascii")
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "2"

    def fail_restore(*args, **kwargs):
        raise AssertionError(
            "pg_restore must not run before the historical key is restored"
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )
    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fail_restore,
    )

    with pytest.raises(
        RestoreError,
        match="Unable to decrypt database backup",
    ):
        restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=tmp_path / "failed-after-rotation",
        )

    # Recover the key selected by the version hint. Authenticated decryption
    # still remains mandatory before restore may proceed.
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        recovery_key_inventory[str(stored_version)]
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = str(stored_version)

    observed = {
        "command": None,
    }

    def fake_restore(command, **kwargs):
        observed["command"] = command
        return Mock(
            returncode=0,
            stderr="",
            stdout="",
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fake_restore,
    )

    storage_root = tmp_path / "restored-after-rotation"

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=storage_root,
        )

    assert result.success is True
    assert result.database.success is True
    assert result.files.success is True
    assert result.files.file_count == 1
    assert observed["command"] is not None

    assert (
        storage_root
        / "profile_images"
        / "photo.jpg"
    ).read_bytes() == b"clinical-file"


def test_backup_key_recovery_rejects_wrong_recovery_secret(
    app,
    tmp_path,
    monkeypatch,
):
    backup_root = _build_encrypted_backup(tmp_path)

    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(
            b"c" * 32
        ).decode("ascii")
    )

    def fail_restore(*args, **kwargs):
        raise AssertionError(
            "pg_restore must not run with a wrong recovery secret"
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fail_restore,
    )

    with pytest.raises(
        RestoreError,
        match="Unable to decrypt database backup",
    ):
        restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=tmp_path / "wrong-key-storage",
        )


def test_backup_key_recovery_material_is_not_in_backup_metadata(
    app,
    tmp_path,
):
    backup_root = _build_encrypted_backup(tmp_path)

    metadata_bytes = (
        (backup_root / "metadata.json")
        .read_bytes()
    )

    artifact_bytes = (
        (backup_root / "database.dump")
        .read_bytes()
    )

    backup_key = _backup_key()
    raw_backup_key = base64.urlsafe_b64decode(
        backup_key.encode("ascii")
    )

    assert backup_key.encode("ascii") not in metadata_bytes
    assert raw_backup_key not in metadata_bytes

    assert backup_key.encode("ascii") not in artifact_bytes
    assert raw_backup_key not in artifact_bytes

    metadata = json.loads(
        metadata_bytes.decode("utf-8")
    )

    database_metadata = metadata["database"]

    assert (
        "backup_encryption_key"
        not in json.dumps(metadata)
    )

    assert (
        "recovery_key"
        not in json.dumps(metadata)
    )

    assert (
        "recovery_secret"
        not in json.dumps(metadata)
    )

    assert (
        "recovery_secret_store"
        not in json.dumps(metadata)
    )

    assert (
        "backup_encryption_key"
        not in database_metadata
    )