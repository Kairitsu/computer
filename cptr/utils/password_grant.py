"""One-time permission to set a new password after the account holder proves it.

The proof is the current password, an authenticator code, or a recovery code.
The grant stays only in this process, and only for a few minutes. A new grant
for the same user replaces the previous one.
"""

from __future__ import annotations

import hashlib
import secrets
import time

GRANT_TTL = 10 * 60

# sha256(token) -> (user_id, expires_at as time.monotonic())
_grants: dict[str, tuple[str, float]] = {}


def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _purge(now: float) -> None:
    expired = [key for key, (_, exp) in _grants.items() if exp <= now]
    for key in expired:
        del _grants[key]


def issue_password_grant(user_id: str) -> str:
    """Return a fresh grant and drop any earlier one for this user."""
    now = time.monotonic()
    _purge(now)
    stale = [key for key, (uid, _) in _grants.items() if uid == user_id]
    for key in stale:
        del _grants[key]
    token = secrets.token_urlsafe(32)
    _grants[_key(token)] = (user_id, now + GRANT_TTL)
    return token


def consume_password_grant(token: str, user_id: str) -> bool:
    """Use the grant once. False if it is missing, expired, or for someone else."""
    if not token:
        return False
    now = time.monotonic()
    _purge(now)
    row = _grants.pop(_key(token), None)
    if row is None:
        return False
    uid, exp = row
    return uid == user_id and exp > now
