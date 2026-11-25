from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import re

from flask import current_app
from werkzeug.datastructures import FileStorage

from app.core.exceptions import ValidationError


ALLOWED_IMAGE_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

DEFAULT_MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024
READ_CHUNK_SIZE = 64 * 1024


def _get_max_file_size() -> int:
    value = current_app.config.get(
        "MAX_PROFILE_IMAGE_SIZE_BYTES",
        DEFAULT_MAX_FILE_SIZE_BYTES,
    )

    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            "Invalid profile image size configuration"
        )

    if value <= 0:
        raise ValidationError(
            "Invalid profile image size configuration"
        )

    return value


def _get_storage_root() -> Path:
    configured_root = current_app.config.get(
        "STORAGE_ROOT"
    )

    if configured_root:
        root = Path(configured_root)
    else:
        root = Path(
            current_app.instance_path
        ) / "storage"

    root = root.resolve()

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    return root


def _validate_upload(file: FileStorage) -> tuple[str, str]:
    if not isinstance(file, FileStorage):
        raise ValidationError(
            "A valid uploaded file is required"
        )

    if not file.filename:
        raise ValidationError(
            "Uploaded file must have a filename"
        )

    client_mime_type = (
        file.mimetype or ""
    ).strip().lower()

    if client_mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ValidationError(
            "Unsupported profile image type"
        )

    content_length = file.content_length

    if content_length is not None:
        if content_length <= 0:
            raise ValidationError(
                "Profile image cannot be empty"
            )

        if content_length > _get_max_file_size():
            raise ValidationError(
                "Profile image exceeds the maximum allowed size"
            )

    stream = file.stream

    try:
        original_position = stream.tell()
    except (AttributeError, OSError):
        original_position = 0

    try:
        stream.seek(0)
    except (AttributeError, OSError):
        raise ValidationError(
            "Uploaded file stream is not seekable"
        )

    max_size = _get_max_file_size()
    total_read = 0
    header = b""

    while True:
        chunk = stream.read(
            READ_CHUNK_SIZE
        )

        if not chunk:
            break

        if not header:
            header = chunk[:32]

        total_read += len(chunk)

        if total_read > max_size:
            stream.seek(original_position)

            raise ValidationError(
                "Profile image exceeds the maximum allowed size"
            )

    if total_read == 0:
        stream.seek(original_position)

        raise ValidationError(
            "Profile image cannot be empty"
        )

    stream.seek(original_position)

    detected_mime_type = _detect_image_mime(
        header
    )

    if detected_mime_type is None:
        raise ValidationError(
            "Uploaded file is not a supported image"
        )

    if detected_mime_type != client_mime_type:
        raise ValidationError(
            "Uploaded file MIME type does not match its actual content"
        )

    extension = ALLOWED_IMAGE_MIME_TYPES[
        detected_mime_type
    ]

    return detected_mime_type, extension


def _detect_image_mime(
    header: bytes,
) -> str | None:
    if header.startswith(
        b"\xFF\xD8\xFF"
    ):
        return "image/jpeg"

    if header.startswith(
        b"\x89PNG\r\n\x1a\n"
    ):
        return "image/png"

    if (
        len(header) >= 12
        and header[:4] == b"RIFF"
        and header[8:12] == b"WEBP"
    ):
        return "image/webp"

    return None


def _build_storage_key(
    *,
    category: str,
    extension: str,
) -> str:
    category = category.strip().strip("/")

    if not category:
        raise ValidationError(
            "Storage category is required"
        )

    if any(
        part in {".", ".."}
        for part in Path(category).parts
    ):
        raise ValidationError(
            "Invalid storage category"
        )

    filename = (
        f"{uuid4().hex}"
        f"{extension}"
    )

    return f"{category}/{filename}"


def _resolve_storage_path(
    storage_key: str,
) -> Path:
    if not isinstance(
        storage_key,
        str,
    ):
        raise ValidationError(
            "Storage key must be a string"
        )

    storage_key = storage_key.strip()

    if not storage_key:
        raise ValidationError(
            "Storage key cannot be blank"
        )

    relative_path = Path(
        storage_key
    )

    if relative_path.is_absolute():
        raise ValidationError(
            "Invalid storage key"
        )

    if any(
        part in {"", ".", ".."}
        for part in relative_path.parts
    ):
        raise ValidationError(
            "Invalid storage key"
        )

    root = _get_storage_root()

    target = (
        root / relative_path
    ).resolve()

    try:
        target.relative_to(root)
    except ValueError:
        raise ValidationError(
            "Invalid storage key"
        )

    return target


def save_profile_image(
    file: FileStorage,
) -> str:
    """
    Validate and persist a profile image.

    Returns an internal storage key.
    """

    _, extension = _validate_upload(
        file
    )

    storage_key = _build_storage_key(
        category="profile_images",
        extension=extension,
    )

    target = _resolve_storage_path(
        storage_key
    )

    target.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    stream = file.stream

    try:
        stream.seek(0)
    except (AttributeError, OSError):
        raise ValidationError(
            "Uploaded file stream is not seekable"
        )

    max_size = _get_max_file_size()
    total_written = 0

    try:
        with target.open(
            "xb"
        ) as destination:
            while True:
                chunk = stream.read(
                    READ_CHUNK_SIZE
                )

                if not chunk:
                    break

                total_written += len(chunk)

                if total_written > max_size:
                    raise ValidationError(
                        "Profile image exceeds the maximum allowed size"
                    )

                destination.write(
                    chunk
                )

    except ValidationError:
        target.unlink(
            missing_ok=True
        )
        raise

    except Exception:
        target.unlink(
            missing_ok=True
        )
        raise

    if total_written == 0:
        target.unlink(
            missing_ok=True
        )

        raise ValidationError(
            "Profile image cannot be empty"
        )

    return storage_key


def delete_file(
    storage_key: str,
) -> None:
    """
    Delete a previously stored file using its internal storage key.
    """

    target = _resolve_storage_path(
        storage_key
    )

    if not target.exists():
        return

    if not target.is_file():
        raise ValidationError(
            "Storage target is not a file"
        )

    target.unlink()


def profile_image_url(
    storage_key: str | None,
) -> str | None:
    """
    Build the public URL for a stored profile image.

    The application must expose the configured storage path.
    """

    if storage_key is None:
        return None

    if not isinstance(storage_key, str):
        raise ValidationError("Invalid public profile image storage key")

    storage_key = storage_key.strip()
    if not storage_key:
        return None

    # No path traversal, query strings, fragments, alternate URL schemes,
    # clinic document keys, encoded separators, or cross-category links.
    # Legacy harmless profile_images/public.png remains compatible.
    if re.fullmatch(
        r"profile_images/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:jpg|png|webp)",
        storage_key,
        flags=re.ASCII,
    ) is None:
        raise ValidationError("Invalid public profile image storage key")

    base_url = current_app.config.get(
        "STORAGE_PUBLIC_BASE_URL"
    )

    if not base_url:
        raise ValidationError(
            "Profile image public URL is not configured"
        )

    return (
        base_url.rstrip("/")
        + "/"
        + storage_key.lstrip("/")
    )