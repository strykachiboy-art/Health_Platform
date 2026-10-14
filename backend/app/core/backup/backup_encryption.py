from __future__ import annotations

import base64
import os
import struct
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app


_MAGIC = b"CSBE"
_FORMAT_VERSION = 1
_ALGORITHM = b"A256GCM"
_NONCE_SIZE = 12
_KEY_SIZE = 32
_TAG_SIZE = 16
_DEFAULT_CHUNK_SIZE = 1024 * 1024
_MAX_CHUNK_SIZE = 64 * 1024 * 1024

# magic, format_version, algorithm_length, key_version, base_nonce, chunk_size
_HEADER = struct.Struct(">4sBBH12sQ")
_LENGTH = struct.Struct(">Q")


def _get_key() -> bytes:
    encoded = current_app.config.get("BACKUP_ENCRYPTION_KEY")

    if not encoded:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY is required for backup encryption."
        )

    try:
        key = base64.urlsafe_b64decode(encoded.strip().encode("ascii"))
    except Exception as exc:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY must be valid URL-safe base64."
        ) from exc

    if len(key) != _KEY_SIZE:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes."
        )

    integration_encoded = current_app.config.get(
        "INTEGRATION_ENCRYPTION_KEY"
    )

    if integration_encoded:
        try:
            integration_key = (
                base64.urlsafe_b64decode(
                    str(
                        integration_encoded
                    ).strip().encode("ascii")
                )
            )
        except Exception:
            integration_key = None

        if integration_key == key:
            raise RuntimeError(
                "BACKUP_ENCRYPTION_KEY must be distinct from "
                "INTEGRATION_ENCRYPTION_KEY."
            )

    return key


def _get_key_version() -> int:
    raw = current_app.config.get("BACKUP_ENCRYPTION_KEY_VERSION", "1")

    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY_VERSION must be an integer."
        ) from exc

    if not 1 <= version <= 65535:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_KEY_VERSION must be between 1 and 65535."
        )

    return version


def validate_backup_encryption_configuration() -> None:
    """Validate the configured key and version without exposing secret material."""
    _get_key()
    _get_key_version()


def _derive_nonce(base_nonce: bytes, chunk_index: int) -> bytes:
    """base_nonce + chunk_index as a 96-bit big-endian counter (mod 2**96).

    Wrapping instead of raising means a random base nonce close to the top
    of the range can never make an otherwise valid backup fail.
    """
    if len(base_nonce) != _NONCE_SIZE:
        raise ValueError("Invalid base nonce.")

    if chunk_index < 0:
        raise ValueError("Chunk index out of range.")

    value = (int.from_bytes(base_nonce, "big") + chunk_index) % (
        1 << (8 * _NONCE_SIZE)
    )
    return value.to_bytes(_NONCE_SIZE, "big")


def _aad(
    *,
    key_version: int,
    chunk_index: int,
    final: bool,
) -> bytes:
    # The final flag is authenticated, so cutting a file off at a chunk
    # boundary (dropping trailing chunks) fails authentication instead of
    # silently restoring a shorter backup.
    return (
        _MAGIC
        + bytes([_FORMAT_VERSION])
        + key_version.to_bytes(2, "big")
        + (b"\x01" if final else b"\x00")
        + chunk_index.to_bytes(8, "big")
    )


def _write_header(
    destination,
    *,
    key_version: int,
    nonce: bytes,
    chunk_size: int,
) -> None:
    destination.write(
        _HEADER.pack(
            _MAGIC,
            _FORMAT_VERSION,
            len(_ALGORITHM),
            key_version,
            nonce,
            chunk_size,
        )
    )
    destination.write(_ALGORITHM)


def _read_header(source):
    raw = source.read(_HEADER.size)

    if len(raw) != _HEADER.size:
        raise ValueError("Backup artifact header is truncated.")

    (
        magic,
        format_version,
        algorithm_length,
        key_version,
        nonce,
        chunk_size,
    ) = _HEADER.unpack(raw)

    if magic != _MAGIC:
        raise ValueError("Invalid backup artifact magic.")

    if format_version != _FORMAT_VERSION:
        raise ValueError(
            f"Unsupported backup artifact format: {format_version}"
        )

    if algorithm_length != len(_ALGORITHM):
        raise ValueError("Invalid backup artifact algorithm metadata.")

    algorithm = source.read(algorithm_length)

    if algorithm != _ALGORITHM:
        raise ValueError("Unsupported backup artifact algorithm.")

    # Upper bound stops a corrupt header from driving a huge read().
    if not 0 < chunk_size <= _MAX_CHUNK_SIZE:
        raise ValueError("Invalid backup artifact chunk size.")

    return key_version, nonce, chunk_size


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def encrypt_backup_artifact(
    source: str | Path,
    destination: str | Path,
    *,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> int:
    """Encrypt source into destination. Returns the number of chunks.

    An empty source produces exactly one (empty, final) chunk so that an
    empty artifact is still authenticated.
    """
    source = Path(source)
    destination = Path(destination)

    if not source.is_file():
        raise FileNotFoundError(source)

    if not 0 < chunk_size <= _MAX_CHUNK_SIZE:
        raise ValueError(
            f"chunk_size must be between 1 and {_MAX_CHUNK_SIZE}."
        )

    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing destination: {destination}"
        )

    key = _get_key()
    key_version = _get_key_version()

    partial = destination.with_name(destination.name + ".partial")

    if partial.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing partial file: {partial}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    base_nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    chunk_count = 0

    try:
        with source.open("rb") as src, partial.open("wb") as dst:
            _write_header(
                dst,
                key_version=key_version,
                nonce=base_nonce,
                chunk_size=chunk_size,
            )

            current = src.read(chunk_size)

            while True:
                upcoming = src.read(chunk_size)
                is_final = not upcoming

                ciphertext = aesgcm.encrypt(
                    _derive_nonce(base_nonce, chunk_count),
                    current,
                    _aad(
                        key_version=key_version,
                        chunk_index=chunk_count,
                        final=is_final,
                    ),
                )

                dst.write(_LENGTH.pack(len(ciphertext)))
                dst.write(ciphertext)
                chunk_count += 1

                if is_final:
                    break

                current = upcoming

            dst.flush()
            os.fsync(dst.fileno())

        partial.replace(destination)

    except Exception:
        _discard(partial)
        raise

    return chunk_count


def decrypt_backup_artifact(
    source: str | Path,
    destination: str | Path,
) -> int:
    """Decrypt source into destination. Returns the number of chunks.

    Nothing appears at destination unless every chunk authenticates and the
    artifact ends with a properly flagged final chunk.
    """
    source = Path(source)
    destination = Path(destination)

    if not source.is_file():
        raise FileNotFoundError(source)

    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing destination: {destination}"
        )

    key = _get_key()
    configured_version = _get_key_version()

    partial = destination.with_name(destination.name + ".partial")

    if partial.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing partial file: {partial}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    chunk_count = 0

    try:
        with source.open("rb") as src, partial.open("wb") as dst:
            stored_version, base_nonce, chunk_size = _read_header(src)

            if stored_version != configured_version:
                raise RuntimeError(
                    "Backup artifact key version does not match "
                    "BACKUP_ENCRYPTION_KEY_VERSION."
                )

            aesgcm = AESGCM(key)

            length_raw = src.read(_LENGTH.size)

            if not length_raw:
                raise ValueError(
                    "Backup artifact contains no data chunks (truncated)."
                )

            while True:
                if len(length_raw) != _LENGTH.size:
                    raise ValueError(
                        "Backup artifact chunk length is truncated."
                    )

                (ciphertext_length,) = _LENGTH.unpack(length_raw)

                if ciphertext_length < _TAG_SIZE:
                    raise ValueError(
                        "Backup artifact ciphertext is invalid."
                    )

                if ciphertext_length > chunk_size + _TAG_SIZE:
                    raise ValueError(
                        "Backup artifact ciphertext exceeds chunk size."
                    )

                ciphertext = src.read(ciphertext_length)

                if len(ciphertext) != ciphertext_length:
                    raise ValueError(
                        "Backup artifact ciphertext is truncated."
                    )

                next_length_raw = src.read(_LENGTH.size)
                is_final = not next_length_raw

                plaintext = aesgcm.decrypt(
                    _derive_nonce(base_nonce, chunk_count),
                    ciphertext,
                    _aad(
                        key_version=stored_version,
                        chunk_index=chunk_count,
                        final=is_final,
                    ),
                )

                dst.write(plaintext)
                chunk_count += 1

                if is_final:
                    break

                length_raw = next_length_raw

            dst.flush()
            os.fsync(dst.fileno())

        partial.replace(destination)

    except InvalidTag as exc:
        _discard(partial)
        raise ValueError(
            "Backup artifact authentication failed."
        ) from exc

    except Exception:
        _discard(partial)
        raise

    return chunk_count
