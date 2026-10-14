from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from app.core.backup.backup_encryption import decrypt_backup_artifact


HASH_CHUNK_SIZE = 1024 * 1024
METADATA_FILENAME = "metadata.json"
MANIFEST_FILENAME = "manifest.json"


class BackupVerificationError(Exception):
    pass


@dataclass(frozen=True)
class DatabaseVerificationResult:
    valid: bool
    backup_path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class FileVerificationResult:
    valid: bool
    manifest_path: str
    file_count: int
    total_size_bytes: int
    manifest_sha256: str


@dataclass(frozen=True)
class BackupVerificationResult:
    success: bool
    backup_path: str
    database: DatabaseVerificationResult
    files: FileVerificationResult
    cross_system_valid: bool


DatabaseReadableValidator = Callable[
    [Path],
    None,
]


def _calculate_sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    try:
        with path.open(
            "rb"
        ) as handle:
            while True:
                chunk = handle.read(
                    HASH_CHUNK_SIZE
                )

                if not chunk:
                    break

                digest.update(
                    chunk
                )
    except OSError as exc:
        raise BackupVerificationError(
            f"Unable to read backup artifact: {exc}"
        ) from exc

    return digest.hexdigest()


def _verify_file_exists(
    path: Path,
    *,
    description: str,
) -> None:
    if not path.exists():
        raise BackupVerificationError(
            f"{description} does not exist"
        )

    if not path.is_file():
        raise BackupVerificationError(
            f"{description} is not a file"
        )


def _read_json_object(
    path: Path,
    *,
    description: str,
) -> dict[str, Any]:
    _verify_file_exists(
        path,
        description=description,
    )

    try:
        with path.open(
            "r",
            encoding="utf-8",
        ) as handle:
            value = json.load(
                handle
            )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise BackupVerificationError(
            f"Unable to read {description}: {exc}"
        ) from exc

    if not isinstance(
        value,
        dict,
    ):
        raise BackupVerificationError(
            f"{description} must contain a JSON object"
        )

    return value


def _validate_safe_relative_path(
    value: Any,
    *,
    field_name: str,
) -> Path:
    if not isinstance(
        value,
        str,
    ):
        raise BackupVerificationError(
            f"{field_name} must be a string"
        )

    if not value.strip():
        raise BackupVerificationError(
            f"{field_name} cannot be blank"
        )

    path = Path(
        value
    )

    if path.is_absolute():
        raise BackupVerificationError(
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
        raise BackupVerificationError(
            f"Invalid {field_name}"
        )

    return path


def _resolve_relative_backup_file(
    backup_root: Path,
    relative_path: Any,
    *,
    field_name: str,
) -> Path:
    path = _validate_safe_relative_path(
        relative_path,
        field_name=field_name,
    )

    target = (
        backup_root
        / path
    ).resolve()

    try:
        target.relative_to(
            backup_root.resolve()
        )
    except ValueError as exc:
        raise BackupVerificationError(
            f"{field_name} escapes backup directory"
        ) from exc

    return target


def _require_integer(
    value: Any,
    *,
    field_name: str,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
    ):
        raise BackupVerificationError(
            f"{field_name} must be an integer"
        )

    if value < 0:
        raise BackupVerificationError(
            f"{field_name} cannot be negative"
        )

    return value


def _require_sha256(
    value: Any,
    *,
    field_name: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise BackupVerificationError(
            f"{field_name} must be a string"
        )

    value = value.strip().lower()

    if len(value) != 64:
        raise BackupVerificationError(
            f"{field_name} must be a SHA-256 checksum"
        )

    try:
        int(
            value,
            16,
        )
    except ValueError as exc:
        raise BackupVerificationError(
            f"{field_name} must be a SHA-256 checksum"
        ) from exc

    return value


def _validate_database_metadata(
    database_metadata: Mapping[str, Any],
) -> tuple[str, int, str]:
    if not isinstance(
        database_metadata,
        Mapping,
    ):
        raise BackupVerificationError(
            "Database backup metadata must be an object"
        )

    backup_path = database_metadata.get(
        "backup_path"
    )

    backup_size = database_metadata.get(
        "backup_size_bytes"
    )

    backup_sha256 = database_metadata.get(
        "backup_sha256"
    )

    if not isinstance(
        backup_path,
        str,
    ) or not backup_path.strip():
        raise BackupVerificationError(
            "Database backup path is missing"
        )

    size_bytes = _require_integer(
        backup_size,
        field_name=(
            "Database backup size"
        ),
    )

    sha256 = _require_sha256(
        backup_sha256,
        field_name=(
            "Database backup checksum"
        ),
    )

    return (
        backup_path,
        size_bytes,
        sha256,
    )


def verify_database_backup(
    *,
    backup_root: str | Path,
    database_metadata: Mapping[str, Any],
    readable_validator: (
        DatabaseReadableValidator | None
    ) = None,
) -> DatabaseVerificationResult:
    root = Path(
        backup_root
    ).resolve()

    (
        recorded_path,
        expected_size,
        expected_sha256,
    ) = _validate_database_metadata(
        database_metadata
    )

    recorded = Path(
        recorded_path
    )

    if recorded.is_absolute():
        database_path = recorded.resolve()

        try:
            database_path.relative_to(
                root
            )
        except ValueError as exc:
            raise BackupVerificationError(
                "Database backup path escapes backup directory"
            ) from exc
    else:
        database_path = (
            root
            / _validate_safe_relative_path(
                recorded_path,
                field_name=(
                    "Database backup path"
                ),
            )
        ).resolve()

    _verify_file_exists(
        database_path,
        description="Database backup artifact",
    )

    try:
        actual_size = (
            database_path.stat().st_size
        )
    except OSError as exc:
        raise BackupVerificationError(
            f"Unable to inspect database backup: {exc}"
        ) from exc

    if actual_size <= 0:
        raise BackupVerificationError(
            "Database backup artifact is empty"
        )

    if actual_size != expected_size:
        raise BackupVerificationError(
            "Database backup size mismatch"
        )

    actual_sha256 = _calculate_sha256(
        database_path
    )

    if actual_sha256 != expected_sha256:
        raise BackupVerificationError(
            "Database backup checksum mismatch"
        )

    if readable_validator is not None:
        try:
            readable_validator(
                database_path
            )
        except BackupVerificationError:
            raise
        except Exception as exc:
            raise BackupVerificationError(
                "Database backup readability verification failed"
            ) from exc

    return DatabaseVerificationResult(
        valid=True,
        backup_path=str(
            database_path
        ),
        size_bytes=actual_size,
        sha256=actual_sha256,
    )


def _validate_manifest(
    manifest: Mapping[str, Any],
) -> tuple[
    int,
    int,
    list[Mapping[str, Any]],
]:
    version = manifest.get(
        "version"
    )

    if version != 1:
        raise BackupVerificationError(
            "Unsupported file backup manifest version"
        )

    file_count = _require_integer(
        manifest.get(
            "file_count"
        ),
        field_name="Manifest file count",
    )

    total_size_bytes = _require_integer(
        manifest.get(
            "total_size_bytes"
        ),
        field_name=(
            "Manifest total size"
        ),
    )

    entries = manifest.get(
        "files"
    )

    if not isinstance(
        entries,
        list,
    ):
        raise BackupVerificationError(
            "Manifest files must be a list"
        )

    if len(entries) != file_count:
        raise BackupVerificationError(
            "Manifest file count mismatch"
        )

    normalized_entries: list[
        Mapping[str, Any]
    ] = []

    for entry in entries:
        if not isinstance(
            entry,
            Mapping,
        ):
            raise BackupVerificationError(
                "Manifest file entry must be an object"
            )

        normalized_entries.append(
            entry
        )

    return (
        file_count,
        total_size_bytes,
        normalized_entries,
    )


def verify_file_backup(
    *,
    backup_root: str | Path,
    file_metadata: Mapping[str, Any],
    expected_references: (
        Iterable[Mapping[str, Any]]
        | None
    ) = None,
) -> FileVerificationResult:
    root = Path(
        backup_root
    ).resolve()

    if not isinstance(
        file_metadata,
        Mapping,
    ):
        raise BackupVerificationError(
            "File backup metadata must be an object"
        )

    encrypted_value = file_metadata.get("encrypted", False)
    if not isinstance(encrypted_value, bool):
        raise BackupVerificationError(
            "File backup encryption marker must be a boolean"
        )
    encrypted = encrypted_value

    file_backup_root = root
    if encrypted:
        backup_path_value = file_metadata.get("backup_path")
        if (
            not isinstance(backup_path_value, str)
            or not backup_path_value.strip()
        ):
            raise BackupVerificationError(
                "Encrypted file backup path is missing"
            )

        candidate = Path(backup_path_value)
        if candidate.is_absolute():
            file_backup_root = candidate.resolve()
        else:
            file_backup_root = (
                root
                / _validate_safe_relative_path(
                    backup_path_value,
                    field_name="File backup path",
                )
            ).resolve()

        try:
            file_backup_root.relative_to(root)
        except ValueError as exc:
            raise BackupVerificationError(
                "File backup path escapes backup directory"
            ) from exc

        if not file_backup_root.is_dir():
            raise BackupVerificationError(
                "Encrypted file backup directory does not exist"
            )

    manifest_path_value = file_metadata.get(
        "manifest_path"
    )

    if not isinstance(
        manifest_path_value,
        str,
    ) or not manifest_path_value.strip():
        raise BackupVerificationError(
            "File manifest path is missing"
        )

    recorded_manifest = Path(
        manifest_path_value
    )

    if recorded_manifest.is_absolute():
        manifest_path = (
            recorded_manifest.resolve()
        )

        try:
            manifest_path.relative_to(
                root
            )
        except ValueError as exc:
            raise BackupVerificationError(
                "Manifest path escapes backup directory"
            ) from exc
    else:
        manifest_path = (
            root
            / _validate_safe_relative_path(
                manifest_path_value,
                field_name=(
                    "Manifest path"
                ),
            )
        ).resolve()

    manifest_sha256 = file_metadata.get(
        "manifest_sha256"
    )

    expected_manifest_sha256 = _require_sha256(
        manifest_sha256,
        field_name=(
            "Manifest checksum"
        ),
    )

    _verify_file_exists(
        manifest_path,
        description="Backup manifest",
    )

    actual_manifest_sha256 = _calculate_sha256(
        manifest_path
    )

    if (
        actual_manifest_sha256
        != expected_manifest_sha256
    ):
        raise BackupVerificationError(
            "Backup manifest checksum mismatch"
        )

    if encrypted:
        try:
            with tempfile.TemporaryDirectory(
                prefix="clinic-file-manifest-verify-"
            ) as temporary_root:
                plaintext_manifest_path = (
                    Path(temporary_root) / "manifest.json"
                )
                decrypt_backup_artifact(
                    manifest_path,
                    plaintext_manifest_path,
                )
                manifest = _read_json_object(
                    plaintext_manifest_path,
                    description="Decrypted backup manifest",
                )
        except BackupVerificationError:
            raise
        except Exception as exc:
            raise BackupVerificationError(
                "Unable to decrypt encrypted file manifest"
            ) from exc
    else:
        manifest = _read_json_object(
            manifest_path,
            description="Backup manifest",
        )

    (
        file_count,
        total_size_bytes,
        entries,
    ) = _validate_manifest(
        manifest
    )

    actual_total_size = 0
    manifest_storage_keys: dict[
        str,
        Mapping[str, Any],
    ] = {}

    for entry in entries:
        storage_key = entry.get(
            "storage_key"
        )

        if not isinstance(
            storage_key,
            str,
        ) or not storage_key.strip():
            raise BackupVerificationError(
                "Manifest storage key is missing"
            )

        if storage_key in manifest_storage_keys:
            raise BackupVerificationError(
                "Duplicate storage key in manifest"
            )

        manifest_storage_keys[
            storage_key
        ] = entry

        backup_path_value = entry.get(
            "backup_path"
        )

        backup_file = (
            _resolve_relative_backup_file(
                file_backup_root,
                backup_path_value,
                field_name=(
                    "Manifest backup path"
                ),
            )
        )

        _verify_file_exists(
            backup_file,
            description=(
                "Backed-up file"
            ),
        )

        expected_size = _require_integer(
            entry.get(
                "size_bytes"
            ),
            field_name=(
                "Manifest file size"
            ),
        )

        expected_sha256 = _require_sha256(
            entry.get(
                "sha256"
            ),
            field_name=(
                "Manifest file checksum"
            ),
        )

        if encrypted:
            try:
                with tempfile.TemporaryDirectory(
                    prefix="clinic-file-artifact-verify-"
                ) as temporary_root:
                    plaintext_path = (
                        Path(temporary_root) / "artifact"
                    )
                    decrypt_backup_artifact(
                        backup_file,
                        plaintext_path,
                    )
                    actual_size = plaintext_path.stat().st_size
                    actual_sha256 = _calculate_sha256(
                        plaintext_path
                    )
            except BackupVerificationError:
                raise
            except Exception as exc:
                raise BackupVerificationError(
                    "Unable to decrypt encrypted file artifact"
                ) from exc
        else:
            try:
                actual_size = (
                    backup_file.stat().st_size
                )
            except OSError as exc:
                raise BackupVerificationError(
                    "Unable to inspect backed-up file"
                ) from exc

            actual_sha256 = _calculate_sha256(
                backup_file
            )

        if actual_size != expected_size:
            raise BackupVerificationError(
                "Backed-up file size mismatch"
            )

        if actual_sha256 != expected_sha256:
            raise BackupVerificationError(
                "Backed-up file checksum mismatch"
            )

        actual_total_size += (
            actual_size
        )

    if actual_total_size != total_size_bytes:
        raise BackupVerificationError(
            "Manifest total size mismatch"
        )

    if expected_references is not None:
        for reference in expected_references:
            if not isinstance(
                reference,
                Mapping,
            ):
                raise BackupVerificationError(
                    "File reference must be an object"
                )

            storage_key = reference.get(
                "storage_key"
            )

            if storage_key not in (
                manifest_storage_keys
            ):
                raise BackupVerificationError(
                    "Database file reference is missing from backup manifest"
                )

            manifest_entry = (
                manifest_storage_keys[
                    storage_key
                ]
            )

            for field_name in (
                "clinic_id",
                "entity_type",
                "entity_id",
            ):
                expected_value = (
                    reference.get(
                        field_name
                    )
                )

                if (
                    expected_value is not None
                    and manifest_entry.get(
                        field_name
                    ) != expected_value
                ):
                    raise BackupVerificationError(
                        "Backup manifest file reference mismatch"
                    )

    return FileVerificationResult(
        valid=True,
        manifest_path=str(
            manifest_path
        ),
        file_count=file_count,
        total_size_bytes=actual_total_size,
        manifest_sha256=(
            actual_manifest_sha256
        ),
    )


def verify_full_backup(
    *,
    backup_path: str | Path,
    database_metadata: Mapping[str, Any],
    file_metadata: Mapping[str, Any],
    expected_file_references: (
        Iterable[Mapping[str, Any]]
        | None
    ) = None,
    database_readable_validator: (
        DatabaseReadableValidator | None
    ) = None,
) -> BackupVerificationResult:
    root = Path(
        backup_path
    ).resolve()

    if not root.exists():
        raise BackupVerificationError(
            "Backup directory does not exist"
        )

    if not root.is_dir():
        raise BackupVerificationError(
            "Backup path is not a directory"
        )

    database_result = (
        verify_database_backup(
            backup_root=root,
            database_metadata=(
                database_metadata
            ),
            readable_validator=(
                database_readable_validator
            ),
        )
    )

    file_result = verify_file_backup(
        backup_root=root,
        file_metadata=file_metadata,
        expected_references=(
            expected_file_references
        ),
    )

    return BackupVerificationResult(
        success=True,
        backup_path=str(
            root
        ),
        database=database_result,
        files=file_result,
        cross_system_valid=True,
    )