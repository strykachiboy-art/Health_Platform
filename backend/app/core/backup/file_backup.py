from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Callable, Mapping

from flask import current_app

from app.core.backup.backup_encryption import encrypt_backup_artifact


DEFAULT_CHUNK_SIZE = 1024 * 1024
MANIFEST_FILENAME = "manifest.json"
ENCRYPTED_MANIFEST_FILENAME = "manifest.json.enc"
FILES_DIRECTORY_NAME = "files"


class FileBackupError(Exception):
    pass


@dataclass(frozen=True)
class FileBackupEntry:
    storage_key: str
    physical_path: str
    backup_path: str
    sha256: str
    size_bytes: int
    modified_at: str
    clinic_id: int | str | None = None
    entity_type: str | None = None
    entity_id: int | str | None = None


@dataclass(frozen=True)
class FileBackupResult:
    success: bool
    backup_path: str
    manifest_path: str
    manifest_sha256: str
    file_count: int
    total_size_bytes: int
    created_at: str
    files: tuple[FileBackupEntry, ...]
    encrypted: bool = True


MetadataResolver = Callable[
    [str],
    Mapping[str, object] | None,
]


def _get_storage_root() -> Path:
    configured_root = current_app.config.get(
        "STORAGE_ROOT"
    )

    if configured_root:
        root = Path(
            configured_root
        )
    else:
        root = (
            Path(current_app.instance_path)
            / "storage"
        )

    root = root.resolve()

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def _resolve_storage_root(
    storage_root: str | Path | None,
) -> Path:
    if storage_root is None:
        return _get_storage_root()

    root = Path(
        storage_root
    ).resolve()

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def _resolve_backup_root(
    output_path: str | Path,
) -> Path:
    if isinstance(
        output_path,
        (str, Path),
    ):
        backup_root = Path(
            output_path
        ).resolve()
    else:
        raise FileBackupError(
            "Backup output path must be a path"
        )

    if backup_root == Path(
        backup_root.anchor
    ):
        raise FileBackupError(
            "Backup output path is unsafe"
        )

    return backup_root


def _is_relative_to(
    path: Path,
    parent: Path,
) -> bool:
    try:
        path.relative_to(
            parent
        )
        return True
    except ValueError:
        return False


def _validate_output_location(
    storage_root: Path,
    backup_root: Path,
) -> None:
    if backup_root == storage_root:
        raise FileBackupError(
            "Backup output path cannot equal storage root"
        )

    if _is_relative_to(
        backup_root,
        storage_root,
    ):
        raise FileBackupError(
            "Backup output path cannot be inside storage root"
        )


def _discover_files(
    storage_root: Path,
) -> list[Path]:
    try:
        candidates = sorted(
            storage_root.rglob("*"),
            key=lambda value: value.as_posix(),
        )
    except OSError as exc:
        raise FileBackupError(
            f"Unable to inspect storage root: {exc}"
        ) from exc

    files: list[Path] = []

    for path in candidates:
        try:
            if path.is_symlink():
                raise FileBackupError(
                    "Symbolic links are not supported in managed storage: "
                    f"{path}"
                )

            if path.is_file():
                files.append(path)

        except OSError as exc:
            raise FileBackupError(
                f"Unable to inspect storage file: {path}"
            ) from exc

    return files


def _calculate_sha256(
    path: Path,
) -> str:
    digest = hashlib.sha256()

    try:
        with path.open(
            "rb"
        ) as source:
            while True:
                chunk = source.read(
                    DEFAULT_CHUNK_SIZE
                )

                if not chunk:
                    break

                digest.update(
                    chunk
                )

    except OSError as exc:
        raise FileBackupError(
            f"Unable to read file: {path}: {exc}"
        ) from exc

    return digest.hexdigest()


def _encrypt_and_verify_file(
    source_path: Path,
    destination_path: Path,
) -> tuple[str, int]:
    """Encrypt a source file without writing a plaintext backup copy."""
    try:
        expected_sha256 = _calculate_sha256(source_path)
        expected_size = source_path.stat().st_size

        encrypt_backup_artifact(
            source_path,
            destination_path,
        )

        # Detect a source file changing while it is backed up.
        actual_sha256 = _calculate_sha256(source_path)
        actual_size = source_path.stat().st_size

        if (
            actual_sha256 != expected_sha256
            or actual_size != expected_size
        ):
            raise FileBackupError(
                "File checksum mismatch during encryption: "
                f"{source_path}"
            )

        return expected_sha256, expected_size

    except FileBackupError:
        destination_path.unlink(missing_ok=True)
        raise

    except Exception as exc:
        destination_path.unlink(missing_ok=True)
        raise FileBackupError(
            f"Unable to encrypt file {source_path}: {exc}"
        ) from exc


def _format_modified_at(
    timestamp: float,
) -> str:
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).isoformat()


def _resolve_metadata(
    storage_key: str,
    resolver: MetadataResolver | None,
) -> dict[str, object]:
    if resolver is None:
        return {}

    try:
        metadata = resolver(
            storage_key
        )
    except Exception as exc:
        raise FileBackupError(
            "File metadata resolution failed for "
            f"{storage_key}: {exc}"
        ) from exc

    if metadata is None:
        return {}

    if not isinstance(
        metadata,
        Mapping,
    ):
        raise FileBackupError(
            "File metadata resolver must return a mapping"
        )

    allowed_fields = {
        "clinic_id",
        "entity_type",
        "entity_id",
    }

    return {
        key: metadata[key]
        for key in allowed_fields
        if key in metadata
    }


def _build_manifest(
    *,
    created_at: str,
    storage_root: Path,
    entries: list[FileBackupEntry],
) -> dict[str, object]:
    return {
        "version": 1,
        "created_at": created_at,
        "storage_root": str(
            storage_root
        ),
        "file_count": len(
            entries
        ),
        "total_size_bytes": sum(
            entry.size_bytes
            for entry in entries
        ),
        "files": [
            asdict(entry)
            for entry in entries
        ],
    }


def _write_manifest(
    manifest_path: Path,
    manifest: Mapping[str, object],
) -> None:
    try:
        with manifest_path.open(
            "x",
            encoding="utf-8",
        ) as handle:
            json.dump(
                manifest,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            handle.write(
                "\n"
            )

    except (OSError, TypeError, ValueError) as exc:
        raise FileBackupError(
            f"Unable to write backup manifest: {exc}"
        ) from exc


def backup_files(
    *,
    output_path: str | Path,
    storage_root: str | Path | None = None,
    metadata_resolver: MetadataResolver | None = None,
) -> FileBackupResult:
    source_root = _resolve_storage_root(
        storage_root
    )

    backup_root = _resolve_backup_root(
        output_path
    )

    _validate_output_location(
        source_root,
        backup_root,
    )

    partial_root = Path(
        f"{backup_root}.partial"
    )

    if backup_root.exists():
        raise FileBackupError(
            "Backup output already exists"
        )

    if partial_root.exists():
        raise FileBackupError(
            "Partial backup output already exists"
        )

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    entries: list[FileBackupEntry] = []

    try:
        partial_root.mkdir(
            parents=True,
            exist_ok=False,
        )

        files_root = (
            partial_root
            / FILES_DIRECTORY_NAME
        )

        files_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        source_files = _discover_files(
            source_root
        )

        for source_path in source_files:
            try:
                relative_path = (
                    source_path.relative_to(
                        source_root
                    )
                )
            except ValueError as exc:
                raise FileBackupError(
                    "Storage file escaped storage root: "
                    f"{source_path}"
                ) from exc

            storage_key = (
                relative_path.as_posix()
            )

            # Opaque backup names avoid exposing original storage keys.
            destination_path = (
                files_root
                / f"{uuid4().hex}.enc"
            )

            checksum, size_bytes = (
                _encrypt_and_verify_file(
                    source_path,
                    destination_path,
                )
            )

            try:
                modified_at = (
                    _format_modified_at(
                        source_path.stat().st_mtime
                    )
                )
            except OSError as exc:
                raise FileBackupError(
                    "Unable to read file metadata: "
                    f"{source_path}: {exc}"
                ) from exc

            metadata = _resolve_metadata(
                storage_key,
                metadata_resolver,
            )

            backup_relative_path = (
                Path(FILES_DIRECTORY_NAME)
                / destination_path.name
            ).as_posix()

            entries.append(
                FileBackupEntry(
                    storage_key=storage_key,
                    physical_path=str(
                        source_path.resolve()
                    ),
                    backup_path=backup_relative_path,
                    sha256=checksum,
                    size_bytes=size_bytes,
                    modified_at=modified_at,
                    clinic_id=metadata.get(
                        "clinic_id"
                    ),
                    entity_type=metadata.get(
                        "entity_type"
                    ),
                    entity_id=metadata.get(
                        "entity_id"
                    ),
                )
            )

        manifest = _build_manifest(
            created_at=created_at,
            storage_root=source_root,
            entries=entries,
        )

        # The plaintext manifest exists only in a temporary directory.
        with tempfile.TemporaryDirectory(
            prefix="clinic-file-backup-manifest-"
        ) as temporary_root:
            plaintext_manifest_path = (
                Path(temporary_root)
                / MANIFEST_FILENAME
            )

            _write_manifest(
                plaintext_manifest_path,
                manifest,
            )

            encrypted_manifest_path = (
                partial_root
                / ENCRYPTED_MANIFEST_FILENAME
            )

            encrypt_backup_artifact(
                plaintext_manifest_path,
                encrypted_manifest_path,
            )

        manifest_sha256 = _calculate_sha256(
            encrypted_manifest_path
        )

        partial_root.replace(
            backup_root
        )

    except FileBackupError:
        shutil.rmtree(
            partial_root,
            ignore_errors=True,
        )
        raise

    except Exception as exc:
        shutil.rmtree(
            partial_root,
            ignore_errors=True,
        )

        raise FileBackupError(
            f"File backup failed: {exc}"
        ) from exc

    return FileBackupResult(
        success=True,
        backup_path=str(
            backup_root
        ),
        manifest_path=str(
            backup_root
            / ENCRYPTED_MANIFEST_FILENAME
        ),
        manifest_sha256=manifest_sha256,
        file_count=len(
            entries
        ),
        total_size_bytes=sum(
            entry.size_bytes
            for entry in entries
        ),
        created_at=created_at,
        files=tuple(
            entries
        ),
    )