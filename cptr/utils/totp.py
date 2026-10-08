"""Time-based one-time passwords (RFC 6238) and recovery codes for two-step sign-in.

Codes are 6 digits, SHA1, 30-second steps: what Google Authenticator, 1Password,
Authy and the rest all default to.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
# Accept the previous and next step too, for clock drift and slow typing.
WINDOW = 1
RECOVERY_CODE_COUNT = 10
ISSUER = "Computer"


def new_secret() -> str:
    """A random 160-bit secret, base32 without padding (how authenticator apps take it)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def code_at(secret: str, at: float | None = None) -> str:
    """The code shown at time `at` (default: now)."""
    return _hotp(secret, int((time.time() if at is None else at) // PERIOD))


def match_step(secret: str, code: str, after_step: int = -1) -> int | None:
    """Return the time step `code` belongs to, or None if it doesn't match.

    Steps at or before `after_step` are refused, so a code can't be used twice.
    """
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    now = int(time.time() // PERIOD)
    for step in range(now - WINDOW, now + WINDOW + 1):
        if step > after_step and hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str) -> str:
    """The otpauth:// URI an authenticator app reads from the QR code."""
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"


def qr_svg(data: str) -> str:
    """The QR code for `data` as an SVG document."""
    import segno

    return segno.make(data, error="m").svg_inline(
        border=2, dark="#000", light="#fff", omitsize=True
    )


# ── Recovery codes ───────────────────────────────────────────

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def new_recovery_codes() -> list[str]:
    """Fresh one-time recovery codes, like `k7m2p-x9q4r`."""
    return [
        "-".join("".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(2))
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def _normalize_recovery(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(_normalize_recovery(code).encode()).hexdigest()
