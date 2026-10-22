from __future__ import annotations

import json
from typing import Any


MAX_PROVIDER_RESPONSE_BYTES = 1024 * 1024
PROVIDER_RESPONSE_CHUNK_SIZE_BYTES = 64 * 1024


class ProviderResponseTooLargeError(ValueError):
    """A provider response exceeded the configured byte limit."""


def _close_response(response: Any) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Preserve the original parsing/size exception.
            pass


def read_response_body_limited(
    response: Any,
    *,
    max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
) -> bytes:
    """Read a response incrementally, enforcing a decoded-byte limit."""
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
    ):
        raise ValueError("Response byte limit must be a positive integer")

    headers = getattr(response, "headers", {}) or {}
    raw_length = None
    try:
        for name, value in headers.items():
            if str(name).lower() == "content-length":
                raw_length = value
                break
    except (AttributeError, TypeError, ValueError):
        raw_length = None

    if raw_length is not None:
        try:
            declared_length = int(str(raw_length).strip())
        except (TypeError, ValueError, OverflowError):
            declared_length = None

        if declared_length is not None and declared_length > max_bytes:
            _close_response(response)
            raise ProviderResponseTooLargeError(
                "Provider response exceeded the byte limit"
            )

    iterator = getattr(response, "iter_content", None)
    if not callable(iterator):
        _close_response(response)
        raise ValueError("Provider response does not support bounded streaming")

    chunks: list[bytes] = []
    total_bytes = 0
    try:
        for chunk in iterator(chunk_size=PROVIDER_RESPONSE_CHUNK_SIZE_BYTES):
            if chunk is None or chunk == b"":
                continue
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                raise ValueError("Provider response yielded an invalid body chunk")

            chunk_bytes = bytes(chunk)
            if len(chunk_bytes) > max_bytes - total_bytes:
                raise ProviderResponseTooLargeError(
                    "Provider response exceeded the byte limit"
                )

            total_bytes += len(chunk_bytes)
            chunks.append(chunk_bytes)

        return b"".join(chunks)
    finally:
        _close_response(response)


def read_json_response_limited(
    response: Any,
    *,
    max_bytes: int = MAX_PROVIDER_RESPONSE_BYTES,
) -> Any:
    """Parse JSON only after the streamed response passes the byte limit."""
    return json.loads(read_response_body_limited(response, max_bytes=max_bytes))
