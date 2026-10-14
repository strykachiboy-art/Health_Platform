from __future__ import annotations

import json
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from flask import current_app

from app.core.backup.backup_storage import (
    BackupStorageError,
    calculate_sha256,
    create_backup_directory,
    write_metadata,
)
from app.core.backup.database_backup import (
    DatabaseBackupError,
    backup_database,
)
from app.core.backup.file_backup import (
    FileBackupError,
    FileBackupResult,
    backup_files,
)


class BackupServiceError(Exception):
    pass


def _result_to_mapping(
    value: Any,
) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)

    if isinstance(
        value,
        Mapping,
    ):
        return dict(value)

    if hasattr(
        value,
        "__dict__",
    ):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }

    raise BackupServiceError(
        "Backup result could not be converted to metadata"
    )


def _file_result_to_mapping(
    value: Any,
    *,
    backup_directory: Path,
) -> dict[str, Any]:
    """Return portable file metadata without per-file details.

    Individual entries contain storage keys and original paths; they remain
    only in the encrypted manifest. The manifest and backup-directory paths
    are stored relative to the full backup root so the backup can be moved.
    """
    result = _result_to_mapping(value)
    result.pop("files", None)

    root = backup_directory.resolve()

    for field_name in ("backup_path", "manifest_path"):
        value_path = result.get(field_name)

        # Test doubles and older external result shapes may omit these fields.
        if value_path is None:
            continue

        if not isinstance(value_path, str) or not value_path.strip():
            raise BackupServiceError(
                f"File backup {field_name} is invalid"
            )

        candidate = Path(value_path).resolve()

        try:
            relative_path = candidate.relative_to(root)
        except ValueError as exc:
            raise BackupServiceError(
                f"File backup {field_name} escapes full backup directory"
            ) from exc

        result[field_name] = relative_path.as_posix()

    return result


def _get_database_url(
    database_url: str | None,
) -> str:
    if database_url is not None:
        if not isinstance(
            database_url,
            str,
        ):
            raise BackupServiceError(
                "Database URL must be a string"
            )

        if not database_url.strip():
            raise BackupServiceError(
                "Database URL cannot be empty"
            )

        return database_url

    configured_url = current_app.config.get(
        "SQLALCHEMY_DATABASE_URI"
    )

    if not isinstance(
        configured_url,
        str,
    ):
        raise BackupServiceError(
            "Configured database URL is invalid"
        )

    if not configured_url.strip():
        raise BackupServiceError(
            "Configured database URL is empty"
        )

    return configured_url


def _get_backup_root(
    backup_root: str | Path | None,
) -> Path | None:
    if backup_root is None:
        return None

    root = Path(
        backup_root
    ).resolve()

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def _cleanup_partial_backup(
    backup_directory: Path,
) -> None:
    shutil.rmtree(
        backup_directory,
        ignore_errors=True,
    )


def _write_full_backup_metadata(
    backup_directory: Path,
    *,
    backup_id: str,
    created_at: str,
    database_result: Any,
    file_result: FileBackupResult,
) -> Path:
    metadata = {
        "version": 1,
        "backup_id": backup_id,
        "created_at": created_at,
        "success": True,
        "database": _result_to_mapping(
            database_result
        ),
        "files": _file_result_to_mapping(
            file_result,
            backup_directory=backup_directory,
        ),
    }

    try:
        metadata_path = write_metadata(
            backup_directory,
            metadata,
        )
    except BackupStorageError as exc:
        raise BackupServiceError(
            f"Unable to write full backup metadata: {exc}"
        ) from exc

    return metadata_path


def _readiness_check(
    database_result: Any,
    file_result: FileBackupResult,
) -> None:
    database_success = getattr(
        database_result,
        "success",
        None,
    )

    file_success = getattr(
        file_result,
        "success",
        None,
    )

    if database_success is not True:
        raise BackupServiceError(
            "Database backup did not complete successfully"
        )

    if file_success is not True:
        raise BackupServiceError(
            "File backup did not complete successfully"
        )


def create_full_backup(
    *,
    database_url: str | None = None,
    backup_root: str | Path | None = None,
    storage_root: str | Path | None = None,
    migration_revision: str | None = None,
    pg_dump_path: str | None = None,
    timeout_seconds: int | None = None,
    metadata_resolver: Callable[
        [str],
        Mapping[str, object] | None,
    ] | None = None,
    database_backup_callable=None,
    file_backup_callable=None,
) -> dict[str, Any]:
    if database_backup_callable is None:
        database_backup_callable = backup_database

    if file_backup_callable is None:
        file_backup_callable = backup_files

    root = _get_backup_root(
        backup_root
    )

    backup_id = (
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%SZ"
        )
        + "-"
        + uuid4().hex
    )

    backup_directory = create_backup_directory(
        relative_path=backup_id,
        backup_root=root,
    )

    database_output = (
        backup_directory
        / "database.dump"
    )

    file_output = (
        backup_directory
        / "files"
    )

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    try:
        resolved_database_url = (
            _get_database_url(
                database_url
            )
        )

        database_kwargs: dict[str, Any] = {
            "output_path": database_output,
            "database_url": resolved_database_url,
        }

        if migration_revision is not None:
            database_kwargs[
                "migration_revision"
            ] = migration_revision

        if pg_dump_path is not None:
            database_kwargs[
                "pg_dump_path"
            ] = pg_dump_path

        if timeout_seconds is not None:
            database_kwargs[
                "timeout_seconds"
            ] = timeout_seconds

        database_result = (
            database_backup_callable(
                **database_kwargs
            )
        )

        file_result = file_backup_callable(
            storage_root=storage_root,
            output_path=file_output,
            metadata_resolver=metadata_resolver,
        )

        _readiness_check(
            database_result,
            file_result,
        )

        metadata_path = (
            _write_full_backup_metadata(
                backup_directory,
                backup_id=backup_id,
                created_at=created_at,
                database_result=database_result,
                file_result=file_result,
            )
        )

        metadata_sha256 = (
            calculate_sha256(
                metadata_path
            )
        )

        return {
            "success": True,
            "backup_id": backup_id,
            "created_at": created_at,
            "backup_path": str(
                backup_directory
            ),
            "database": (
                _result_to_mapping(
                    database_result
                )
            ),
            "files": (
                _file_result_to_mapping(
                    file_result,
                    backup_directory=backup_directory,
                )
            ),
            "metadata_path": str(
                metadata_path
            ),
            "metadata_sha256": metadata_sha256,
        }

    except (
        DatabaseBackupError,
        FileBackupError,
        BackupStorageError,
        BackupServiceError,
    ):
        _cleanup_partial_backup(
            backup_directory
        )
        raise

    except Exception as exc:
        _cleanup_partial_backup(
            backup_directory
        )

        raise BackupServiceError(
            f"Full backup failed: {exc}"
        ) from exc