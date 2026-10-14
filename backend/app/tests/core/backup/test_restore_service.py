from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.backup.backup_service import create_full_backup
from app.core.backup.file_backup import backup_files

from app.core.backup.backup_encryption import (
    encrypt_backup_artifact,
)

from app.core.backup.restore_service import (
    RestoreError,
    restore_full_backup,
)


def _backup_key() -> str:
    return base64.urlsafe_b64encode(
        b"b" * 32
    ).decode("ascii")


@pytest.fixture(autouse=True)
def _backup_encryption_context(app):
    app.config["BACKUP_ENCRYPTION_KEY"] = _backup_key()
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(
            b"i" * 32
        ).decode("ascii")
    )

    with app.app_context():
        yield

def _sha256(
    payload: bytes,
) -> str:
    return hashlib.sha256(
        payload
    ).hexdigest()


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


def _build_backup(
    tmp_path,
    *,
    database_payload=b"database-dump",
    file_payload=b"clinical-file",
):
    backup_root = (
        tmp_path
        / "backup"
    )

    database_path = (
        backup_root
        / "database.dump"
    )

    database_source_path = (
        backup_root
        / "database-source.dump"
    )

    _write_file(
        database_source_path,
        database_payload,
    )

    encrypt_backup_artifact(
        database_source_path,
        database_path,
    )

    database_source_path.unlink(
        missing_ok=True
    )

    restored_storage_key = (
        "profile_images"
        "/photo.jpg"
    )

    backed_up_file = (
        backup_root
        / "files"
        / Path(
            restored_storage_key
        )
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
                    restored_storage_key
                ),
                "physical_path": (
                    "storage"
                    "/profile_images"
                    "/photo.jpg"
                ),
                "backup_path": (
                    "files"
                    "/profile_images"
                    "/photo.jpg"
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
        backup_root
        / "files"
        / "manifest.json"
    )

    manifest_bytes = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode(
        "utf-8"
    )

    _write_file(
        manifest_path,
        manifest_bytes,
    )

    metadata = {
        "version": 1,
        "backup_id": (
            "backup-test-001"
        ),
        "created_at": (
            "2026-09-24T00:00:00+00:00"
        ),
        "success": True,
        "database": {
            "success": True,
            "database_name": "clinic_system",
            "database_host": "localhost",
            "database_port": 5432,
            "backup_path": str(
                database_path
            ),
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
                backup_root
                / "files"
            ),
            "manifest_path": str(
                manifest_path
            ),
            "manifest_sha256": _sha256(
                manifest_bytes
            ),
            "file_count": 1,
            "total_size_bytes": len(
                file_payload
            ),
        },
    }

    metadata_path = (
        backup_root
        / "metadata.json"
    )

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
        ).encode(
            "utf-8"
        ),
    )

    return (
        backup_root,
        metadata,
    )


def test_restore_full_backup_restores_database_and_files(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        metadata,
    ) = _build_backup(
        tmp_path
    )

    storage_root = (
        tmp_path
        / "restored-storage"
    )

    observed = {
        "command": None,
        "env": None,
    }

    def fake_run(
        command,
        **kwargs,
    ):
        observed["command"] = command
        observed["env"] = kwargs["env"]

        return Mock(
            returncode=0,
            stderr="",
            stdout="",
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: (
            "C:/PostgreSQL/bin/"
            "pg_restore.exe"
        ),
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fake_run,
    )

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup_user:"
                "secret-password"
                "@localhost:5432/"
                "clinic_system"
            ),
            storage_root=storage_root,
        )

    assert result.success is True
    assert result.backup_id == (
        "backup-test-001"
    )
    assert result.database.success is True
    assert result.files.success is True
    assert result.files.file_count == 1

    restored_file = (
        storage_root
        / "profile_images"
        / "photo.jpg"
    )

    assert restored_file.read_bytes() == (
        b"clinical-file"
    )

    assert (
        observed["command"][0]
        == "C:/PostgreSQL/bin/"
        "pg_restore.exe"
    )

    assert (
        "--clean"
        in observed["command"]
    )

    assert (
        observed["command"][-1]
        != str(
            backup_root
            / "database.dump"
        )
    )

    assert not Path(
        observed["command"][-1]
    ).exists()

    assert (
        "--exit-on-error"
        in observed["command"]
    )

    assert (
        "--single-transaction"
        in observed["command"]
    )

    assert (
        "secret-password"
        not in observed["command"]
    )

    assert observed[
        "env"
    ]["PGPASSWORD"] == (
        "secret-password"
    )


def test_restore_full_backup_invalidates_authentication(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    observed = {
        "called": False,
    }

    def invalidate():
        observed[
            "called"
        ] = True

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=(
                tmp_path
                / "storage"
            ),
            invalidate_authentication=(
                invalidate
            ),
        )

    assert observed[
        "called"
    ] is True

    assert result.authentication_invalidated is True


def test_restore_full_backup_recovers_chat_outbox(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    observed = {
        "called": False,
    }

    def recover():
        observed[
            "called"
        ] = True

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=(
                tmp_path
                / "storage"
            ),
            recover_chat_outbox=recover,
        )

    assert observed[
        "called"
    ] is True

    assert result.outbox_recovery_completed is True


def test_restore_full_backup_runs_post_restore_verification(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    observed = {
        "called": False,
    }

    def verify():
        observed[
            "called"
        ] = True

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=(
                tmp_path
                / "storage"
            ),
            post_restore_verification=verify,
        )

    assert observed[
        "called"
    ] is True

    assert result.post_restore_verification_completed is True


def test_restore_full_backup_rejects_invalid_backup_before_pg_restore(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    database_dump = (
        backup_root
        / "database.dump"
    )

    database_dump.write_bytes(
        b"corrupted"
    )

    pg_restore_called = {
        "called": False,
    }

    def fake_run(*args, **kwargs):
        pg_restore_called[
            "called"
        ] = True

        return Mock(
            returncode=0,
            stderr="",
            stdout="",
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fake_run,
    )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="Pre-restore verification failed",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=(
                    tmp_path
                    / "storage"
                ),
            )

    assert pg_restore_called[
        "called"
    ] is False


def test_restore_full_backup_rejects_pg_restore_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=1,
            stderr="restore permission denied",
            stdout="",
        ),
    )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="pg_restore failed: restore permission denied",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=(
                    tmp_path
                    / "storage"
                ),
            )


def test_restore_full_backup_cleans_staging_on_file_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    storage_root = (
        tmp_path
        / "storage"
    )

    manifest_path = (
        backup_root
        / "files"
        / "manifest.json"
    )

    manifest = json.loads(
        manifest_path.read_text(
            encoding="utf-8"
        )
    )

    manifest["files"][0][
        "sha256"
    ] = "a" * 64

    manifest_path.write_text(
        json.dumps(
            manifest,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with app.app_context():
        with pytest.raises(
            RestoreError,
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=storage_root,
            )

    assert not storage_root.exists()

    staging_paths = list(
        tmp_path.glob(
            "storage.restore-*"
        )
    )

    assert staging_paths == []


def test_restore_full_backup_rejects_existing_storage_without_replace_flag(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    storage_root = (
        tmp_path
        / "storage"
    )

    _write_file(
        storage_root
        / "existing.txt",
        b"existing",
    )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="already exists",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=storage_root,
            )

    assert (
        storage_root
        / "existing.txt"
    ).read_bytes() == b"existing"


def test_restore_full_backup_can_replace_existing_storage(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    storage_root = (
        tmp_path
        / "storage"
    )

    _write_file(
        storage_root
        / "old.txt",
        b"old",
    )

    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=storage_root,
            replace_existing_storage=True,
        )

    assert result.success is True

    assert not (
        storage_root
        / "old.txt"
    ).exists()

    assert (
        storage_root
        / "profile_images"
        / "photo.jpg"
    ).read_bytes() == (
        b"clinical-file"
    )


def test_restore_full_backup_rejects_authentication_hook_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="Authentication invalidation failed",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                invalidate_authentication=(
                    lambda: (
                        (_ for _ in ()).throw(
                            RuntimeError(
                                "synthetic auth failure"
                            )
                        )
                    )
                ),
            )


def test_restore_full_backup_rejects_outbox_hook_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    def fail_recovery():
        raise RuntimeError(
            "synthetic outbox failure"
        )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="Chat outbox recovery failed",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                recover_chat_outbox=(
                    fail_recovery
                ),
            )


def test_restore_full_backup_rejects_post_restore_verification_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    def fail_verification():
        raise RuntimeError(
            "synthetic verification failure"
        )

    with app.app_context():
        with pytest.raises(
            RestoreError,
            match="Post-restore verification failed",
        ):
            restore_full_backup(
                backup_path=backup_root,
                database_url=(
                    "postgresql://backup@localhost/"
                    "clinic_system"
                ),
                storage_root=(
                    tmp_path
                    / "storage"
                ),
                post_restore_verification=(
                    fail_verification
                ),
            )
def test_restore_full_backup_rejects_wrong_backup_key(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(
            b"c" * 32
        ).decode("ascii")
    )

    def fail_restore(*args, **kwargs):
        raise AssertionError(
            "pg_restore must not run after decrypt failure"
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
            storage_root=(
                tmp_path
                / "storage"
            ),
        )


def test_restore_full_backup_rejects_missing_backup_key(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    app.config.pop(
        "BACKUP_ENCRYPTION_KEY",
        None,
    )

    def fail_restore(*args, **kwargs):
        raise AssertionError(
            "pg_restore must not run without backup key"
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
            storage_root=(
                tmp_path
                / "storage"
            ),
        )


def test_restore_full_backup_cleans_decrypted_artifact_on_pg_restore_failure(
    app,
    tmp_path,
    monkeypatch,
):
    (
        backup_root,
        _,
    ) = _build_backup(
        tmp_path
    )

    observed = {
        "restore_path": None,
    }

    def fake_restore(
        command,
        **kwargs,
    ):
        observed[
            "restore_path"
        ] = Path(
            command[-1]
        )

        assert observed[
            "restore_path"
        ].exists()

        return Mock(
            returncode=1,
            stderr="synthetic pg_restore failure",
            stdout="",
        )

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )

    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        fake_restore,
    )

    with pytest.raises(
        RestoreError,
        match="pg_restore failed: synthetic pg_restore failure",
    ):
        restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup@localhost/"
                "clinic_system"
            ),
            storage_root=(
                tmp_path
                / "storage"
            ),
        )

    assert observed[
        "restore_path"
    ] is not None

    assert not observed[
        "restore_path"
    ].exists()

    assert not list(
        Path(
            observed[
                "restore_path"
            ].parent
        ).glob("*")
    )
def _replace_file_backup_with_encrypted_artifacts(
    app,
    tmp_path,
    backup_root: Path,
    metadata: dict,
    file_payload: bytes,
) -> None:
    file_output = backup_root / "files"
    shutil.rmtree(file_output)

    storage_root = tmp_path / "encrypted-source-storage"
    source = storage_root / "profile_images" / "photo.jpg"
    _write_file(source, file_payload)

    with app.app_context():
        result = backup_files(
            storage_root=storage_root,
            output_path=file_output,
        )

    metadata["files"] = {
        "success": result.success,
        "backup_path": result.backup_path,
        "manifest_path": result.manifest_path,
        "manifest_sha256": result.manifest_sha256,
        "file_count": result.file_count,
        "total_size_bytes": result.total_size_bytes,
        "created_at": result.created_at,
        "encrypted": result.encrypted,
    }

    metadata_path = backup_root / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def test_restore_full_backup_restores_encrypted_files(
    app,
    tmp_path,
    monkeypatch,
):
    backup_root, metadata = _build_backup(tmp_path)
    payload = b"encrypted-clinical-file-content"
    _replace_file_backup_with_encrypted_artifacts(
        app,
        tmp_path,
        backup_root,
        metadata,
        payload,
    )

    artifact_root = Path(metadata["files"]["backup_path"])
    manifest_artifact = Path(metadata["files"]["manifest_path"])
    encrypted_artifacts = list(artifact_root.rglob("*.enc"))

    assert manifest_artifact.is_file()
    assert encrypted_artifacts
    assert all(payload not in path.read_bytes() for path in encrypted_artifacts)

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )
    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    storage_root = tmp_path / "encrypted-restored-storage"
    with app.app_context():
        result = restore_full_backup(
            backup_path=backup_root,
            database_url=(
                "postgresql://backup_user:secret-password"
                "@localhost:5432/clinic_system"
            ),
            storage_root=storage_root,
        )

    assert result.success is True
    assert result.files.success is True
    assert result.files.file_count == 1
    assert (
        storage_root / "profile_images" / "photo.jpg"
    ).read_bytes() == payload

    # The staged plaintext copy is moved to the destination; temporary
    # decryption files should not be left beside the backup artifacts.
    assert not list(artifact_root.rglob("*.partial"))
def test_create_verify_and_restore_full_backup_with_portable_encrypted_files(
    app,
    tmp_path,
    monkeypatch,
):
    payload = b"synthetic-private-clinical-attachment"
    source_root = tmp_path / "source-storage"
    source_file = source_root / "profile_images" / "private-photo.jpg"
    _write_file(source_file, payload)

    def fake_database_backup(**kwargs):
        output = Path(kwargs["output_path"])
        database_payload = b"synthetic-database-dump"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(database_payload)

        return SimpleNamespace(
            success=True,
            database_name="clinic_system",
            database_host="localhost",
            database_port=5432,
            backup_path=str(output),
            backup_size_bytes=len(database_payload),
            backup_sha256=_sha256(database_payload),
            format="custom",
            encrypted=False,
        )

    with app.app_context():
        created = create_full_backup(
            database_url="postgresql://backup_user@localhost/clinic_system",
            backup_root=tmp_path / "backups",
            storage_root=source_root,
            database_backup_callable=fake_database_backup,
        )

    assert created["success"] is True
    assert created["files"]["encrypted"] is True
    assert created["files"]["backup_path"] == "files"
    assert created["files"]["manifest_path"] == "files/manifest.json.enc"

    backup_root = Path(created["backup_path"])
    metadata_path = backup_root / "metadata.json"
    persisted = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert persisted["files"]["backup_path"] == "files"
    assert persisted["files"]["manifest_path"] == "files/manifest.json.enc"
    assert "files" not in persisted["files"]

    artifacts = [item for item in backup_root.rglob("*") if item.is_file()]
    combined_bytes = b"".join(item.read_bytes() for item in artifacts)

    assert b"private-photo.jpg" not in combined_bytes
    assert payload not in combined_bytes
    assert '"physical_path"' not in metadata_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "app.core.backup.restore_service.shutil.which",
        lambda value: "pg_restore",
    )
    monkeypatch.setattr(
        "app.core.backup.restore_service.subprocess.run",
        lambda *args, **kwargs: Mock(
            returncode=0,
            stderr="",
            stdout="",
        ),
    )

    restored_storage = tmp_path / "restored-storage"
    with app.app_context():
        restored = restore_full_backup(
            backup_path=backup_root,
            database_url="postgresql://backup_user@localhost/clinic_system",
            storage_root=restored_storage,
        )

    assert restored.success is True
    assert restored.files.success is True
    assert restored.files.file_count == 1
    assert (
        restored_storage / "profile_images" / "private-photo.jpg"
    ).read_bytes() == payload
    assert not list(backup_root.rglob("*.partial"))
