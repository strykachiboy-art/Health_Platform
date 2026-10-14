from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from flask import current_app
from sqlalchemy.engine import make_url

from app.core.backup.backup_encryption import (
    decrypt_backup_artifact,
)

from app.core.backup.backup_storage import (
    BackupStorageError,
    calculate_sha256,
    read_metadata,
    verify_artifact_exists,
)
from app.core.backup.backup_verification import (
    BackupVerificationError,
    verify_full_backup,
)


DEFAULT_RESTORE_TIMEOUT_SECONDS = 1800
HASH_CHUNK_SIZE = 1024 * 1024


class RestoreError(Exception):
    pass


@dataclass(frozen=True)
class DatabaseRestoreResult:
    success: bool
    backup_path: str
    database_name: str
    database_host: str | None
    database_port: int | None


@dataclass(frozen=True)
class FileRestoreResult:
    success: bool
    storage_root: str
    file_count: int
    total_size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    success: bool
    backup_id: str
    backup_path: str
    database: DatabaseRestoreResult
    files: FileRestoreResult
    authentication_invalidated: bool
    outbox_recovery_completed: bool
    post_restore_verification_completed: bool


RestoreHook = Callable[[], None]


def _get_database_url(
    database_url: str | None,
) -> Any:
    if database_url is None:
        database_url = current_app.config.get(
            "SQLALCHEMY_DATABASE_URI"
        )

    if not isinstance(
        database_url,
        str,
    ):
        raise RestoreError(
            "Database URL must be a string"
        )

    database_url = database_url.strip()

    if not database_url:
        raise RestoreError(
            "Database URL cannot be empty"
        )

    try:
        url = make_url(
            database_url
        )
    except Exception as exc:
        raise RestoreError(
            "Invalid database URL"
        ) from exc

    if url.get_backend_name() != "postgresql":
        raise RestoreError(
            "Database restore requires a PostgreSQL database"
        )

    if not url.database:
        raise RestoreError(
            "PostgreSQL database name is required"
        )

    return url


def _get_pg_restore_path(
    pg_restore_path: str | None,
) -> str:
    configured = (
        pg_restore_path
        or current_app.config.get(
            "PG_RESTORE_PATH"
        )
    )

    if configured is not None:
        if not isinstance(
            configured,
            str,
        ):
            raise RestoreError(
                "pg_restore path must be a string"
            )

        configured = configured.strip()

        if not configured:
            raise RestoreError(
                "pg_restore path cannot be empty"
            )

        executable = (
            shutil.which(
                configured
            )
            if Path(
                configured
            ).name == configured
            else configured
        )

        if executable:
            return executable

        configured_path = Path(
            configured
        )

        if (
            configured_path.exists()
            and configured_path.is_file()
        ):
            return str(
                configured_path
            )

        raise RestoreError(
            "pg_restore executable was not found"
        )

    executable = shutil.which(
        "pg_restore"
    )

    if executable is None:
        raise RestoreError(
            "pg_restore executable was not found"
        )

    return executable


def _get_timeout_seconds(
    timeout_seconds: int | None,
) -> int:
    if timeout_seconds is None:
        timeout_seconds = current_app.config.get(
            "BACKUP_RESTORE_TIMEOUT_SECONDS",
            DEFAULT_RESTORE_TIMEOUT_SECONDS,
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
    ):
        raise RestoreError(
            "Restore timeout must be a positive integer"
        )

    if timeout_seconds <= 0:
        raise RestoreError(
            "Restore timeout must be a positive integer"
        )

    return timeout_seconds


def _validate_backup_directory(
    backup_path: str | Path,
) -> Path:
    root = Path(
        backup_path
    ).resolve()

    if not root.exists():
        raise RestoreError(
            "Backup directory does not exist"
        )

    if not root.is_dir():
        raise RestoreError(
            "Backup path is not a directory"
        )

    return root


def _read_backup_metadata(
    backup_root: Path,
) -> dict[str, Any]:
    try:
        metadata = read_metadata(
            backup_root
        )
    except BackupStorageError as exc:
        raise RestoreError(
            f"Unable to read backup metadata: {exc}"
        ) from exc

    if metadata.get(
        "success"
    ) is not True:
        raise RestoreError(
            "Backup metadata does not describe a successful backup"
        )

    backup_id = metadata.get(
        "backup_id"
    )

    if not isinstance(
        backup_id,
        str,
    ) or not backup_id.strip():
        raise RestoreError(
            "Backup metadata is missing backup_id"
        )

    if not isinstance(
        metadata.get(
            "database"
        ),
        Mapping,
    ):
        raise RestoreError(
            "Backup metadata is missing database metadata"
        )

    if not isinstance(
        metadata.get(
            "files"
        ),
        Mapping,
    ):
        raise RestoreError(
            "Backup metadata is missing file metadata"
        )

    return metadata


def _safe_relative_path(
    value: Any,
    *,
    field_name: str,
) -> Path:
    if not isinstance(
        value,
        str,
    ):
        raise RestoreError(
            f"{field_name} must be a string"
        )

    value = value.strip()

    if not value:
        raise RestoreError(
            f"{field_name} cannot be blank"
        )

    path = Path(
        value
    )

    if path.is_absolute():
        raise RestoreError(
            f"{field_name} must be relative"
        )

    if any(
        part in {
            "",
            ".",
            "..",
        }
        for part in path.parts
    ):
        raise RestoreError(
            f"Invalid {field_name}"
        )

    return path


def _resolve_backup_file(
    backup_root: Path,
    relative_path: Any,
    *,
    field_name: str,
) -> Path:
    path = _safe_relative_path(
        relative_path,
        field_name=field_name,
    )

    target = (
        backup_root
        / path
    ).resolve()

    try:
        target.relative_to(
            backup_root
        )
    except ValueError as exc:
        raise RestoreError(
            f"{field_name} escapes backup directory"
        ) from exc

    return target


def _calculate_sha256(
    path: Path,
) -> str:
    return calculate_sha256(
        path
    )


def _restore_database(
    *,
    backup_root: Path,
    database_metadata: Mapping[str, Any],
    database_url: str | None,
    pg_restore_path: str | None,
    timeout_seconds: int | None,
) -> DatabaseRestoreResult:
    recorded_path = database_metadata.get(
        "backup_path"
    )

    database_path: Path

    if isinstance(
        recorded_path,
        str,
    ) and recorded_path.strip():
        recorded = Path(
            recorded_path
        )

        if recorded.is_absolute():
            database_path = (
                recorded.resolve()
            )

            try:
                database_path.relative_to(
                    backup_root
                )
            except ValueError:
                fallback = (
                    backup_root
                    / "database.dump"
                ).resolve()

                if fallback.exists():
                    database_path = fallback
                else:
                    raise RestoreError(
                        "Database backup path escapes backup directory"
                    )
        else:
            database_path = (
                backup_root
                / _safe_relative_path(
                    recorded_path,
                    field_name=(
                        "Database backup path"
                    ),
                )
            ).resolve()
    else:
        database_path = (
            backup_root
            / "database.dump"
        ).resolve()

    verify_artifact_exists(
        database_path
    )

    expected_size = database_metadata.get(
        "backup_size_bytes"
    )

    expected_sha256 = database_metadata.get(
        "backup_sha256"
    )

    if (
        isinstance(
            expected_size,
            bool,
        )
        or not isinstance(
            expected_size,
            int,
        )
        or expected_size < 0
    ):
        raise RestoreError(
            "Invalid database backup size metadata"
        )

    if not isinstance(
        expected_sha256,
        str,
    ):
        raise RestoreError(
            "Invalid database backup checksum metadata"
        )

    actual_size = (
        database_path.stat().st_size
    )

    if actual_size != expected_size:
        raise RestoreError(
            "Database backup size mismatch"
        )

    actual_sha256 = _calculate_sha256(
        database_path
    )

    if actual_sha256 != expected_sha256:
        raise RestoreError(
            "Database backup checksum mismatch"
        )

    temporary_restore_directory = None
    restore_database_path = database_path

    try:
        encrypted = database_metadata.get(
            "encrypted",
            False,
        )

        if encrypted is True:
            try:
                temporary_restore_directory = Path(
                    tempfile.mkdtemp(
                        prefix="clinic-system-backup-restore-"
                    )
                )

                restore_database_path = (
                    temporary_restore_directory
                    / "database.dump"
                )

                decrypt_backup_artifact(
                    database_path,
                    restore_database_path,
                )

                if not restore_database_path.is_file():
                    raise RestoreError(
                        "Encrypted database restore artifact was not created"
                    )

                if restore_database_path.stat().st_size <= 0:
                    raise RestoreError(
                        "Encrypted database restore artifact is empty"
                    )

            except RestoreError:
                raise

            except Exception as exc:
                raise RestoreError(
                    "Unable to decrypt database backup"
                ) from exc

        elif encrypted is not False:
            raise RestoreError(
                "Invalid database encryption metadata"
            )

        url = _get_database_url(
            database_url
        )

        executable = _get_pg_restore_path(
            pg_restore_path
        )

        timeout = _get_timeout_seconds(
            timeout_seconds
        )

        safe_url = url.set(
            password=None
        ).render_as_string(
            hide_password=False
        )

        env = dict()

        import os

        env.update(
            os.environ
        )

        if url.password is not None:
            env["PGPASSWORD"] = (
                url.password
            )

        command = [
            executable,
            "--clean",
            "--if-exists",
            "--exit-on-error",
            "--single-transaction",
            "--dbname",
            safe_url,
            str(
                restore_database_path
            ),
        ]

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RestoreError(
                "pg_restore timed out"
            ) from exc
        except OSError as exc:
            raise RestoreError(
                "Unable to execute pg_restore"
            ) from exc

        if completed.returncode != 0:
            stderr = (
                completed.stderr.strip()
            )

            if stderr:
                raise RestoreError(
                    f"pg_restore failed: {stderr}"
                )

            raise RestoreError(
                "pg_restore failed"
            )

        return DatabaseRestoreResult(
            success=True,
            backup_path=str(
                database_path
            ),
            database_name=(
                url.database
            ),
            database_host=(
                url.host
            ),
            database_port=(
                url.port
            ),
        )

    finally:
        if temporary_restore_directory is not None:
            shutil.rmtree(
                temporary_restore_directory,
                ignore_errors=True,
            )


def _load_manifest(
    backup_root: Path,
    file_metadata: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    encrypted_value = file_metadata.get("encrypted", False)
    if not isinstance(encrypted_value, bool):
        raise RestoreError(
            "File backup encryption marker must be a boolean"
        )
    encrypted = encrypted_value

    manifest_path_value = (
        file_metadata.get(
            "manifest_path"
        )
    )

    if isinstance(
        manifest_path_value,
        str,
    ) and manifest_path_value.strip():
        manifest_candidate = Path(
            manifest_path_value
        )

        if manifest_candidate.is_absolute():
            manifest_path = (
                manifest_candidate.resolve()
            )

            try:
                manifest_path.relative_to(
                    backup_root
                )
            except ValueError:
                fallback = (
                    backup_root
                    / "files"
                    / "manifest.json"
                ).resolve()

                if fallback.exists():
                    manifest_path = fallback
                else:
                    raise RestoreError(
                        "File manifest path escapes backup directory"
                    )
        else:
            manifest_path = _resolve_backup_file(
                backup_root,
                manifest_path_value,
                field_name="Manifest path",
            )
    else:
        manifest_path = (
            backup_root
            / "files"
            / "manifest.json"
        ).resolve()

    verify_artifact_exists(
        manifest_path
    )

    expected_manifest_sha256 = (
        file_metadata.get(
            "manifest_sha256"
        )
    )

    if not isinstance(
        expected_manifest_sha256,
        str,
    ):
        raise RestoreError(
            "Invalid manifest checksum metadata"
        )

    actual_manifest_sha256 = (
        _calculate_sha256(
            manifest_path
        )
    )

    if (
        actual_manifest_sha256
        != expected_manifest_sha256
    ):
        raise RestoreError(
            "Backup manifest checksum mismatch"
        )

    if encrypted:
        try:
            with tempfile.TemporaryDirectory(
                prefix="clinic-file-manifest-restore-"
            ) as temporary_root:
                plaintext_manifest_path = (
                    Path(temporary_root) / "manifest.json"
                )
                decrypt_backup_artifact(
                    manifest_path,
                    plaintext_manifest_path,
                )
                with plaintext_manifest_path.open(
                    "r",
                    encoding="utf-8",
                ) as handle:
                    manifest = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError(
                "Unable to read decrypted file backup manifest"
            ) from exc
        except Exception as exc:
            raise RestoreError(
                "Unable to decrypt encrypted file backup manifest"
            ) from exc
    else:
        try:
            with manifest_path.open(
                "r",
                encoding="utf-8",
            ) as handle:
                manifest = json.load(
                    handle
                )
        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise RestoreError(
                f"Unable to read backup manifest: {exc}"
            ) from exc

    if not isinstance(
        manifest,
        dict,
    ):
        raise RestoreError(
            "Backup manifest must contain an object"
        )

    if manifest.get(
        "version"
    ) != 1:
        raise RestoreError(
            "Unsupported backup manifest version"
        )

    if not isinstance(
        manifest.get(
            "files"
        ),
        list,
    ):
        raise RestoreError(
            "Backup manifest files must be a list"
        )

    return (
        manifest_path,
        manifest,
    )


def _resolve_file_backup_root(
    backup_root: Path,
    file_metadata: Mapping[str, Any],
    *,
    encrypted: bool,
) -> Path | None:
    if not encrypted:
        return None

    value = file_metadata.get("backup_path")
    if not isinstance(value, str) or not value.strip():
        raise RestoreError(
            "Encrypted file backup path is missing"
        )

    candidate = Path(value)
    if candidate.is_absolute():
        file_root = candidate.resolve()
    else:
        file_root = _resolve_backup_file(
            backup_root,
            value,
            field_name="File backup path",
        )

    try:
        file_root.relative_to(backup_root.resolve())
    except ValueError as exc:
        raise RestoreError(
            "File backup path escapes backup directory"
        ) from exc

    if not file_root.is_dir():
        raise RestoreError(
            "Encrypted file backup directory does not exist"
        )

    return file_root


def _validate_storage_target(
    storage_root: str | Path,
) -> Path:
    target = Path(
        storage_root
    ).resolve()

    if target == Path(
        target.anchor
    ):
        raise RestoreError(
            "Storage restore target is unsafe"
        )

    return target


def _copy_manifest_files_to_stage(
    *,
    backup_root: Path,
    manifest: Mapping[str, Any],
    staging_root: Path,
    file_backup_root: Path | None = None,
    encrypted: bool = False,
) -> tuple[int, int]:
    entries = manifest.get(
        "files"
    )

    if not isinstance(
        entries,
        list,
    ):
        raise RestoreError(
            "Backup manifest files must be a list"
        )

    total_size = 0

    copied = 0

    for entry in entries:
        if not isinstance(
            entry,
            Mapping,
        ):
            raise RestoreError(
                "Backup manifest entry must be an object"
            )

        storage_key = _safe_relative_path(
            entry.get(
                "storage_key"
            ),
            field_name="Storage key",
        )

        artifact_root = (
            file_backup_root
            if encrypted and file_backup_root is not None
            else backup_root
        )
        backup_file = _resolve_backup_file(
            artifact_root,
            entry.get(
                "backup_path"
            ),
            field_name=(
                "Manifest backup path"
            ),
        )

        verify_artifact_exists(
            backup_file
        )

        expected_size = entry.get(
            "size_bytes"
        )

        if (
            isinstance(
                expected_size,
                bool,
            )
            or not isinstance(
                expected_size,
                int,
            )
            or expected_size < 0
        ):
            raise RestoreError(
                "Invalid file size in backup manifest"
            )

        expected_sha256 = entry.get(
            "sha256"
        )

        if not isinstance(
            expected_sha256,
            str,
        ):
            raise RestoreError(
                "Invalid file checksum in backup manifest"
            )

        # Encrypted artifact size/hash describe plaintext, not ciphertext.
        # For legacy files retain the original pre-copy integrity checks.
        if not encrypted:
            actual_size = backup_file.stat().st_size
            if actual_size != expected_size:
                raise RestoreError(
                    "Backup file size mismatch"
                )

            actual_sha256 = _calculate_sha256(backup_file)
            if actual_sha256 != expected_sha256:
                raise RestoreError(
                    "Backup file checksum mismatch"
                )

        destination = (
            staging_root
            / storage_key
        ).resolve()

        try:
            destination.relative_to(
                staging_root
            )
        except ValueError as exc:
            raise RestoreError(
                "Storage key escapes restore target"
            ) from exc

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if encrypted:
            try:
                decrypt_backup_artifact(
                    backup_file,
                    destination,
                )
            except Exception as exc:
                destination.unlink(missing_ok=True)
                raise RestoreError(
                    f"Unable to decrypt restored file {storage_key}"
                ) from exc
        else:
            try:
                shutil.copy2(
                    backup_file,
                    destination,
                )
            except OSError as exc:
                raise RestoreError(
                    f"Unable to restore file {storage_key}: {exc}"
                ) from exc

        restored_size = (
            destination.stat().st_size
        )

        if restored_size != expected_size:
            raise RestoreError(
                "Restored file size mismatch"
            )

        restored_sha256 = (
            _calculate_sha256(
                destination
            )
        )

        if restored_sha256 != expected_sha256:
            raise RestoreError(
                "Restored file checksum mismatch"
            )

        total_size += (
            expected_size
        )

        copied += 1

    return (
        copied,
        total_size,
    )


def _commit_storage_restore(
    *,
    staging_root: Path,
    storage_root: Path,
    replace_existing: bool,
) -> None:
    if storage_root.exists() and not replace_existing:
        raise RestoreError(
            "Storage restore target already exists"
        )

    rollback_root = Path(
        f"{storage_root}.restore-old-{uuid4().hex}"
    )

    moved_existing = False

    try:
        if storage_root.exists():
            shutil.move(
                str(
                    storage_root
                ),
                str(
                    rollback_root
                ),
            )

            moved_existing = True

        shutil.move(
            str(
                staging_root
            ),
            str(
                storage_root
            ),
        )

    except Exception as exc:
        if moved_existing:
            try:
                if storage_root.exists():
                    shutil.rmtree(
                        storage_root,
                        ignore_errors=True,
                    )

                shutil.move(
                    str(
                        rollback_root
                    ),
                    str(
                        storage_root
                    ),
                )
            except Exception:
                raise RestoreError(
                    "Storage restore failed and rollback was unsuccessful"
                ) from exc

        raise RestoreError(
            f"Unable to commit storage restore: {exc}"
        ) from exc

    if moved_existing:
        shutil.rmtree(
            rollback_root,
            ignore_errors=True,
        )


def _verify_restored_storage(
    *,
    storage_root: Path,
    manifest: Mapping[str, Any],
) -> tuple[int, int]:
    entries = manifest.get(
        "files"
    )

    if not isinstance(
        entries,
        list,
    ):
        raise RestoreError(
            "Backup manifest files must be a list"
        )

    total_size = 0

    for entry in entries:
        if not isinstance(
            entry,
            Mapping,
        ):
            raise RestoreError(
                "Backup manifest entry must be an object"
            )

        storage_key = _safe_relative_path(
            entry.get(
                "storage_key"
            ),
            field_name="Storage key",
        )

        expected_size = entry.get(
            "size_bytes"
        )

        expected_sha256 = entry.get(
            "sha256"
        )

        if (
            isinstance(
                expected_size,
                bool,
            )
            or not isinstance(
                expected_size,
                int,
            )
        ):
            raise RestoreError(
                "Invalid restored file size metadata"
            )

        if not isinstance(
            expected_sha256,
            str,
        ):
            raise RestoreError(
                "Invalid restored file checksum metadata"
            )

        restored_file = (
            storage_root
            / storage_key
        ).resolve()

        try:
            restored_file.relative_to(
                storage_root
            )
        except ValueError as exc:
            raise RestoreError(
                "Restored storage key escapes storage root"
            ) from exc

        verify_artifact_exists(
            restored_file
        )

        actual_size = (
            restored_file.stat().st_size
        )

        if actual_size != expected_size:
            raise RestoreError(
                "Restored file size mismatch"
            )

        actual_sha256 = (
            _calculate_sha256(
                restored_file
            )
        )

        if actual_sha256 != expected_sha256:
            raise RestoreError(
                "Restored file checksum mismatch"
            )

        total_size += (
            actual_size
        )

    return (
        len(entries),
        total_size,
    )


def restore_full_backup(
    *,
    backup_path: str | Path,
    database_url: str | None = None,
    storage_root: str | Path,
    pg_restore_path: str | None = None,
    timeout_seconds: int | None = None,
    replace_existing_storage: bool = False,
    invalidate_authentication: RestoreHook | None = None,
    recover_chat_outbox: RestoreHook | None = None,
    post_restore_verification: RestoreHook | None = None,
) -> RestoreResult:
    backup_root = _validate_backup_directory(
        backup_path
    )

    metadata = _read_backup_metadata(
        backup_root
    )

    database_metadata = metadata[
        "database"
    ]

    file_metadata = metadata[
        "files"
    ]

    try:
        verify_full_backup(
            backup_path=backup_root,
            database_metadata=(
                database_metadata
            ),
            file_metadata=file_metadata,
        )
    except BackupVerificationError as exc:
        raise RestoreError(
            f"Pre-restore verification failed: {exc}"
        ) from exc

    database_result = _restore_database(
        backup_root=backup_root,
        database_metadata=(
            database_metadata
        ),
        database_url=database_url,
        pg_restore_path=pg_restore_path,
        timeout_seconds=timeout_seconds,
    )

    authentication_invalidated = False

    if invalidate_authentication is not None:
        try:
            invalidate_authentication()
        except Exception as exc:
            raise RestoreError(
                "Authentication invalidation failed after database restore"
            ) from exc

        authentication_invalidated = True

    _, manifest = _load_manifest(
        backup_root,
        file_metadata,
    )

    encrypted_files = file_metadata.get("encrypted", False)
    if not isinstance(encrypted_files, bool):
        raise RestoreError(
            "File backup encryption marker must be a boolean"
        )

    file_backup_root = _resolve_file_backup_root(
        backup_root,
        file_metadata,
        encrypted=encrypted_files,
    )

    target_storage = _validate_storage_target(
        storage_root
    )

    staging_root = Path(
        f"{target_storage}.restore-{uuid4().hex}"
    )

    try:
        staging_root.mkdir(
            parents=True,
            exist_ok=False,
        )

        file_count, total_size = (
            _copy_manifest_files_to_stage(
                backup_root=backup_root,
                manifest=manifest,
                staging_root=staging_root,
                file_backup_root=file_backup_root,
                encrypted=encrypted_files,
            )
        )

        _commit_storage_restore(
            staging_root=staging_root,
            storage_root=target_storage,
            replace_existing=(
                replace_existing_storage
            ),
        )

    except RestoreError:
        shutil.rmtree(
            staging_root,
            ignore_errors=True,
        )
        raise

    except Exception as exc:
        shutil.rmtree(
            staging_root,
            ignore_errors=True,
        )

        raise RestoreError(
            f"File restore failed: {exc}"
        ) from exc

    restored_count, restored_size = (
        _verify_restored_storage(
            storage_root=target_storage,
            manifest=manifest,
        )
    )

    if restored_count != file_count:
        raise RestoreError(
            "Restored file count mismatch"
        )

    if restored_size != total_size:
        raise RestoreError(
            "Restored file size total mismatch"
        )

    outbox_recovery_completed = False

    if recover_chat_outbox is not None:
        try:
            recover_chat_outbox()
        except Exception as exc:
            raise RestoreError(
                "Chat outbox recovery failed"
            ) from exc

        outbox_recovery_completed = True

    post_restore_verification_completed = False

    if post_restore_verification is not None:
        try:
            post_restore_verification()
        except Exception as exc:
            raise RestoreError(
                "Post-restore verification failed"
            ) from exc

        post_restore_verification_completed = True

    return RestoreResult(
        success=True,
        backup_id=str(
            metadata[
                "backup_id"
            ]
        ),
        backup_path=str(
            backup_root
        ),
        database=database_result,
        files=FileRestoreResult(
            success=True,
            storage_root=str(
                target_storage
            ),
            file_count=restored_count,
            total_size_bytes=restored_size,
        ),
        authentication_invalidated=(
            authentication_invalidated
        ),
        outbox_recovery_completed=(
            outbox_recovery_completed
        ),
        post_restore_verification_completed=(
            post_restore_verification_completed
        ),
    )