"""Pure, side-effect-free TOTP and recovery-code cryptographic primitives.

No Flask session, network or persistent storage is accessed here. The caller
must enforce enrollment ownership, rate limits and durable replay protection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time


TOTP_PERIOD = 30
TOTP_DIGITS = 6


def generate_totp_secret() -> str:
    """Create a 256-bit RFC-compatible Base32 authenticator secret."""
    return base64.b32encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    if not isinstance(secret, str) or len(secret) != 52:
        raise ValueError("Invalid authenticator secret")
    if not all(ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for ch in secret):
        raise ValueError("Invalid authenticator secret")
    try:
        raw = base64.b32decode(secret + "====", casefold=False)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("Invalid authenticator secret") from exc
    if len(raw) != 32:
        raise ValueError("Invalid authenticator secret")
    return raw


def totp_at_step(secret: str, step: int) -> str:
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise ValueError("Invalid time step")
    key = _secret_bytes(secret)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF)
    return str(code % (10 ** TOTP_DIGITS)).zfill(TOTP_DIGITS)


def match_totp_step(
    *,
    secret: str,
    code: str,
    now: int | float | None = None,
    last_accepted_step: int | None = None,
    tolerance_steps: int = 1,
) -> int | None:
    """Return an eligible TOTP step without persisting a replay counter.

    Caller must atomically store the returned step under a DB row lock or an
    equivalent compare-and-swap; concurrent validation is not safe otherwise.
    """
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    if tolerance_steps not in (0, 1):
        raise ValueError("Invalid TOTP tolerance")
    if last_accepted_step is not None and (
        not isinstance(last_accepted_step, int) or last_accepted_step < 0
    ):
        raise ValueError("Invalid replay step")
    epoch = time.time() if now is None else now
    if not isinstance(epoch, (int, float)) or isinstance(epoch, bool) or epoch < 0:
        raise ValueError("Invalid timestamp")
    central = int(epoch // TOTP_PERIOD)
    for step in range(max(0, central - tolerance_steps), central + tolerance_steps + 1):
        if last_accepted_step is not None and step <= last_accepted_step:
            continue
        if hmac.compare_digest(totp_at_step(secret, step), code):
            return step
    return None


def generate_recovery_codes(count: int = 10) -> list[str]:
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 20:
        raise ValueError("Invalid recovery-code count")
    return [base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
            for _ in range(count)]


def recovery_code_digest(*, code: str, pepper: str) -> str:
    if not isinstance(code, str) or len(code) != 32 or not all(
        ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for ch in code
    ):
        raise ValueError("Invalid recovery code")
    if not isinstance(pepper, str) or len(pepper) < 32:
        raise ValueError("Recovery pepper is not configured securely")
    return hmac.new(pepper.encode("utf-8"), code.encode("ascii"), hashlib.sha256).hexdigest()
