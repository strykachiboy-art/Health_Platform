from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.backup.backup_encryption import decrypt_backup_artifact

from app.core.backup.file_backup import (
    FileBackupError,
    backup_files,
)


@pytest.fixture(autouse=True)
def _backup_encryption_context(app):
    app.config["BACKUP_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"b" * 32).decode("ascii")
    )
    app.config["BACKUP_ENCRYPTION_KEY_VERSION"] = "1"
    app.config["INTEGRATION_ENCRYPTION_KEY"] = (
        base64.urlsafe_b64encode(b"i" * 32).decode("ascii")
    )
    with app.app_context():
        yield


def _read_manifest(output: Path, tmp_path: Path) -> dict:
    destination = tmp_path / f"manifest-{uuid4().hex}.json"
    try:
        decrypt_backup_artifact(
            output / "manifest.json.enc",
            destination,
        )
        return json.loads(destination.read_text(encoding="utf-8"))
    finally:
        destination.unlink(missing_ok=True)


def _read_backup_file(
    output: Path,
    entry,
    tmp_path: Path,
    *,
    index: int = 0,
) -> bytes:
    destination = tmp_path / f"restored-{index}-{uuid4().hex}.bin"
    try:
        decrypt_backup_artifact(
            output / Path(entry.backup_path),
            destination,
        )
        return destination.read_bytes()
    finally:
        destination.unlink(missing_ok=True)


def test_backup_files_handles_empty_storage(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    result = backup_files(
        storage_root=storage,
        output_path=output,
    )

    assert result.success is True
    assert result.file_count == 0
    assert result.total_size_bytes == 0
    assert output.exists()
    assert (
        output
        / "manifest.json.enc"
    ).exists()

    manifest = _read_manifest(output, tmp_path)

    assert manifest["version"] == 1
    assert manifest["file_count"] == 0
    assert manifest["total_size_bytes"] == 0
    assert manifest["files"] == []


def test_backup_files_copies_single_file_and_records_checksum(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    source = (
        storage
        / "profile_images"
        / "patient-1"
        / "image.jpg"
    )

    payload = (
        b"synthetic-medical-file"
    )

    source.parent.mkdir(
        parents=True
    )

    source.write_bytes(
        payload
    )

    output = (
        tmp_path
        / "file-backup"
    )

    result = backup_files(
        storage_root=storage,
        output_path=output,
    )

    expected_checksum = (
        hashlib.sha256(
            payload
        ).hexdigest()
    )

    assert result.success is True
    assert result.file_count == 1
    assert result.total_size_bytes == len(
        payload
    )

    entry = result.files[0]

    assert entry.storage_key == (
        "profile_images/patient-1/image.jpg"
    )
    assert entry.sha256 == (
        expected_checksum
    )
    assert entry.size_bytes == (
        len(payload)
    )
    assert entry.backup_path.startswith("files/")
    assert entry.backup_path.endswith(".enc")
    assert "profile_images" not in entry.backup_path

    copied = output / Path(entry.backup_path)
    ciphertext = copied.read_bytes()

    assert payload not in ciphertext
    assert _read_backup_file(
        output,
        entry,
        tmp_path,
    ) == payload


def test_backup_files_preserves_multiple_storage_keys_and_order(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    files = {
        "chat/a/message.txt": b"message",
        "profile_images/b/avatar.png": b"avatar",
        "reports/z/report.pdf": b"report",
    }

    for storage_key, payload in files.items():
        path = (
            storage
            / Path(storage_key)
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_bytes(
            payload
        )

    output = (
        tmp_path
        / "file-backup"
    )

    result = backup_files(
        storage_root=storage,
        output_path=output,
    )

    assert result.file_count == 3

    assert [
        entry.storage_key
        for entry in result.files
    ] == sorted(
        files
    )

    for index, entry in enumerate(result.files):
        assert _read_backup_file(
            output,
            entry,
            tmp_path,
            index=index,
        ) == files[entry.storage_key]
        assert entry.backup_path.endswith(".enc")
        assert entry.storage_key not in entry.backup_path


def test_backup_files_generates_machine_readable_manifest(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    source = (
        storage
        / "profile_images"
        / "photo.png"
    )

    source.parent.mkdir(
        parents=True
    )

    source.write_bytes(
        b"png-payload"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    result = backup_files(
        storage_root=storage,
        output_path=output,
        metadata_resolver=lambda storage_key: {
            "clinic_id": 7,
            "entity_type": "User",
            "entity_id": 42,
        },
    )

    manifest_path = output / "manifest.json.enc"
    manifest = _read_manifest(output, tmp_path)

    assert b"profile_images/photo.png" not in manifest_path.read_bytes()

    assert manifest["version"] == 1
    assert manifest["file_count"] == 1
    assert manifest["total_size_bytes"] == (
        len(b"png-payload")
    )

    entry = manifest["files"][0]

    assert entry["storage_key"] == (
        "profile_images/photo.png"
    )
    assert entry["clinic_id"] == 7
    assert entry["entity_type"] == "User"
    assert entry["entity_id"] == 42

    assert result.manifest_sha256 == (
        hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
    )


def test_backup_files_rejects_output_inside_storage_root(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    output = (
        storage
        / "backups"
    )

    with pytest.raises(
        FileBackupError,
        match="cannot be inside storage root",
    ):
        backup_files(
            storage_root=storage,
            output_path=output,
        )


def test_backup_files_rejects_existing_output(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    output.mkdir(
        parents=True
    )

    with pytest.raises(
        FileBackupError,
        match="Backup output already exists",
    ):
        backup_files(
            storage_root=storage,
            output_path=output,
        )


def test_backup_files_cleans_partial_output_on_encryption_failure(
    tmp_path,
    monkeypatch,
):
    storage = (
        tmp_path
        / "storage"
    )

    source = (
        storage
        / "profile_images"
        / "photo.jpg"
    )

    source.parent.mkdir(
        parents=True
    )

    source.write_bytes(
        b"payload"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    from app.core.backup import file_backup

    def fail_encryption(
        source_path,
        destination_path,
    ):
        raise FileBackupError(
            "synthetic encryption failure"
        )

    monkeypatch.setattr(
        file_backup,
        "_encrypt_and_verify_file",
        fail_encryption,
    )

    with pytest.raises(
        FileBackupError,
        match="synthetic encryption failure",
    ):
        backup_files(
            storage_root=storage,
            output_path=output,
        )

    assert not output.exists()

    assert not Path(
        f"{output}.partial"
    ).exists()


def test_backup_files_detects_checksum_mismatch(
    tmp_path,
    monkeypatch,
):
    storage = (
        tmp_path
        / "storage"
    )

    source = (
        storage
        / "profile_images"
        / "photo.jpg"
    )

    source.parent.mkdir(
        parents=True
    )

    source.write_bytes(
        b"correct-payload"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    from app.core.backup import file_backup

    checksums = iter(["a" * 64, "b" * 64])

    monkeypatch.setattr(
        file_backup,
        "_calculate_sha256",
        lambda path: next(checksums),
    )

    with pytest.raises(
        FileBackupError,
        match="checksum mismatch",
    ):
        backup_files(
            storage_root=storage,
            output_path=output,
        )

    assert not output.exists()

    assert not Path(
        f"{output}.partial"
    ).exists()


def test_backup_files_includes_modification_metadata(
    tmp_path,
):
    storage = (
        tmp_path
        / "storage"
    )

    source = (
        storage
        / "chat"
        / "attachment.bin"
    )

    source.parent.mkdir(
        parents=True
    )

    source.write_bytes(
        b"attachment"
    )

    output = (
        tmp_path
        / "file-backup"
    )

    result = backup_files(
        storage_root=storage,
        output_path=output,
    )

    entry = result.files[0]

    assert entry.modified_at
    assert entry.modified_at.endswith(
        "+00:00"
    )