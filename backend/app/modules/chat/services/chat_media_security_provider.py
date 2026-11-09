"""CHAT-F3.S1: fail-closed, no-plaintext-on-disk media inspection adapters.

ClamAV clamd INSTREAM sends data to the explicitly configured internal scanner.
FFprobe receives media only on stdin. Neither provider writes a plaintext file.
Security gate: no configured/available provider means NO media acceptance.
"""
from __future__ import annotations

import json
import math
import socket
import subprocess

_MAX_INPUT = 15 * 1024 * 1024
_CHUNK = 64 * 1024
_TIMEOUT = 8.0


def scan_with_clamd(data: bytes, mime: str, *, socket_path: str | None = None,
                    host: str | None = None, port: int = 3310) -> bool:
    """Accept only an explicit clamd 'stream: OK'; all other outcomes deny.

    Prefer a local Unix socket. If TCP is used, operators must provision an
    internal, non-public clamd endpoint; INSTREAM does not encrypt transport.
    """
    if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_INPUT:
        return False
    if not isinstance(mime, str) or not mime:
        return False
    if bool(socket_path) == bool(host):
        return False
    if socket_path is not None and (not isinstance(socket_path, str) or not socket_path.strip()):
        return False
    if host is not None and (not isinstance(host, str) or not host.strip()):
        return False
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    try:
        if socket_path:
            unix_family = getattr(socket, "AF_UNIX", None)
            if unix_family is None:
                return False  # Unsupported on this host; never bypass scanning.
            connection = socket.socket(unix_family, socket.SOCK_STREAM)
            connection.settimeout(_TIMEOUT)
            try:
                connection.connect(socket_path)
            except BaseException:
                connection.close()
                raise
        else:
            connection = socket.create_connection((host, port), timeout=_TIMEOUT)
        with connection as stream:
            stream.settimeout(_TIMEOUT)
            stream.sendall(b"zINSTREAM\0")
            for index in range(0, len(data), _CHUNK):
                chunk = data[index:index + _CHUNK]
                stream.sendall(len(chunk).to_bytes(4, "big") + chunk)
            stream.sendall(b"\x00\x00\x00\x00")
            answer = stream.recv(1024)
            # Do not accept truncation, ambiguity, scan errors or malware hits.
            if not answer or not (b"\0" in answer or b"\n" in answer):
                return False
            first = answer.split(b"\0", 1)[0].split(b"\n", 1)[0]
            return first == b"stream: OK"
    except (OSError, TimeoutError, ValueError, OverflowError):
        return False


def probe_audio_with_ffprobe(data: bytes, mime: str, *, binary: str = "ffprobe") -> float | None:
    """Parse verified audio-stream duration over a pipe; never write PHI to disk."""
    if not isinstance(data, bytes) or not 0 < len(data) <= _MAX_INPUT:
        return None
    if mime not in {"audio/ogg", "audio/wav", "audio/mp4"}:
        return None
    if not isinstance(binary, str) or not binary.strip():
        return None
    try:
        result = subprocess.run(
            [binary, "-hide_banner", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type", "-of", "json", "-i", "pipe:0"],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=_TIMEOUT, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 65536:
            return None
        parsed = json.loads(result.stdout)
        if not isinstance(parsed, dict):
            return None
        streams = parsed.get("streams")
        if not isinstance(streams, list) or not any(
            isinstance(item, dict) and item.get("codec_type") == "audio"
            for item in streams
        ):
            return None
        formatted = parsed.get("format")
        if not isinstance(formatted, dict):
            return None
        seconds = float(formatted.get("duration"))
        if not math.isfinite(seconds) or not 0 < seconds <= 180:
            return None
        return seconds
    except (OSError, TimeoutError, subprocess.TimeoutExpired,
            ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return None
