"""User, Auth, and UserStates models with data-access class methods."""

from __future__ import annotations

import json
import time
import uuid

from sqlalchemy import BigInteger, Column, Float, ForeignKey, Text, select, update, delete
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """Profile only. No credentials, no login identity."""

    __tablename__ = "users"

    id = Column(Text, primary_key=True, default=_uuid)
    display_name = Column(Text, nullable=True)
    profile_image_url = Column(Text, nullable=True)
    role = Column(Text, nullable=False, default="pending")  # "admin" | "user" | "pending"
    settings = Column(JSON, nullable=False, default=dict)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=True)
    last_seen_at = Column(BigInteger, nullable=True)

    auth = relationship("Auth", back_populates="user", uselist=False)
    states = relationship("UserStates", back_populates="user", uselist=False)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_id(user_id: str) -> User | None:
        async with await get_db() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()

    @staticmethod
    async def list_all() -> list[dict]:
        """List all users joined with their auth username."""
        async with await get_db() as db:
            result = await db.execute(
                select(
                    User.id,
                    User.display_name,
                    User.profile_image_url,
                    User.role,
                    User.created_at,
                    Auth.username,
                )
                .join(Auth, User.id == Auth.user_id)
                .order_by(User.created_at)
            )
            return [
                {
                    "user_id": row.id,
                    "username": row.username,
                    "display_name": row.display_name,
                    "profile_image_url": row.profile_image_url,
                    "role": row.role,
                    "created_at": row.created_at,
                }
                for row in result.all()
            ]

    @staticmethod
    async def create(
        username: str,
        password_hash: str,
        role: str = "user",
        display_name: str | None = None,
        created_at: int = 0,
    ) -> str:
        """Create a User + Auth row. Returns the new user_id."""
        async with await get_db() as db:
            user = User(created_at=created_at, display_name=display_name, role=role)
            db.add(user)
            await db.flush()
            db.add(Auth(user_id=user.id, username=username, password=password_hash))
            await db.commit()
            return user.id

    @staticmethod
    async def update_role(user_id: str, role: str) -> bool:
        """Update a user's role. Returns True if user existed."""
        async with await get_db() as db:
            result = await db.execute(update(User).where(User.id == user_id).values(role=role))
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete_user(user_id: str) -> None:
        """Delete user and all related rows."""
        async with await get_db() as db:
            await db.execute(delete(UserStates).where(UserStates.user_id == user_id))
            await db.execute(delete(Auth).where(Auth.user_id == user_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()

    @staticmethod
    async def update_profile_image(user_id: str, url: str | None) -> bool:
        """Set or clear profile image URL. Returns True if user existed."""
        async with await get_db() as db:
            result = await db.execute(
                update(User).where(User.id == user_id).values(profile_image_url=url)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def update_display_name(user_id: str, display_name: str | None) -> bool:
        """Set or clear display name. Returns True if user existed."""
        async with await get_db() as db:
            result = await db.execute(
                update(User).where(User.id == user_id).values(display_name=display_name or None)
            )
            await db.commit()
            return result.rowcount > 0


class Auth(Base):
    """Login identity + credentials. 1:1 with User."""

    __tablename__ = "auths"

    user_id = Column(Text, ForeignKey("users.id"), primary_key=True)
    username = Column(Text, unique=True, nullable=False)
    password = Column(Text, nullable=True)  # bcrypt hash, NULL for PAM
    # Two-step sign-in. The secret is set but not enabled while setup awaits a first code.
    totp_secret = Column(Text, nullable=True)
    totp_enabled_at = Column(BigInteger, nullable=True)
    totp_last_step = Column(BigInteger, nullable=True)
    recovery_codes = Column(Text, nullable=True)  # JSON list of sha256 hashes
    # Session tokens issued before this (epoch seconds) are refused.
    sessions_valid_after = Column(Float, nullable=True)

    user = relationship("User", back_populates="auth")

    @property
    def totp_enabled(self) -> bool:
        return bool(self.totp_secret and self.totp_enabled_at)

    @property
    def recovery_code_hashes(self) -> list[str]:
        try:
            return json.loads(self.recovery_codes or "[]")
        except ValueError:
            return []

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_username(username: str) -> Auth | None:
        async with await get_db() as db:
            result = await db.execute(
                select(Auth).where(Auth.username == username, Auth.password.isnot(None))
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_by_user_id(user_id: str) -> Auth | None:
        async with await get_db() as db:
            result = await db.execute(select(Auth).where(Auth.user_id == user_id))
            return result.scalar_one_or_none()

    @staticmethod
    async def username_exists(username: str) -> bool:
        async with await get_db() as db:
            result = await db.execute(select(Auth).where(Auth.username == username))
            return result.scalar_one_or_none() is not None

    @staticmethod
    async def update_password(user_id: str, new_hash: str) -> bool:
        """Update password hash. Returns True if auth row existed."""
        async with await get_db() as db:
            result = await db.execute(select(Auth).where(Auth.user_id == user_id))
            auth = result.scalar_one_or_none()
            if not auth:
                return False
            await db.execute(update(Auth).where(Auth.user_id == user_id).values(password=new_hash))
            await db.commit()
            return True

    @staticmethod
    async def update_username(user_id: str, new_username: str) -> bool:
        """Update username. Returns True if auth row existed."""
        async with await get_db() as db:
            result = await db.execute(select(Auth).where(Auth.user_id == user_id))
            auth = result.scalar_one_or_none()
            if not auth:
                return False
            # Check uniqueness
            existing = await db.execute(
                select(Auth).where(Auth.username == new_username, Auth.user_id != user_id)
            )
            if existing.scalar_one_or_none():
                return False
            await db.execute(
                update(Auth).where(Auth.user_id == user_id).values(username=new_username)
            )
            await db.commit()
            return True

    @staticmethod
    async def get_with_user(username: str) -> tuple[Auth, User] | None:
        """Get auth + user in one query for login."""
        async with await get_db() as db:
            result = await db.execute(
                select(Auth).where(Auth.username == username, Auth.password.isnot(None))
            )
            auth = result.scalar_one_or_none()
            if not auth:
                return None
            user_result = await db.execute(select(User).where(User.id == auth.user_id))
            user = user_result.scalar_one()
            return auth, user

    # ── Two-step sign-in ─────────────────────────────────────

    @staticmethod
    async def _update(user_id: str, *where, **values) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(Auth).where(Auth.user_id == user_id, *where).values(**values)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def start_totp_setup(user_id: str, secret: str) -> bool:
        """Store a secret awaiting its first code. Two-step sign-in stays off until then."""
        return await Auth._update(
            user_id,
            totp_secret=secret,
            totp_enabled_at=None,
            totp_last_step=None,
            recovery_codes=None,
        )

    @staticmethod
    async def enable_totp(user_id: str, step: int, recovery_hashes: list[str]) -> bool:
        return await Auth._update(
            user_id,
            Auth.totp_secret.isnot(None),
            totp_enabled_at=int(time.time() * 1000),
            totp_last_step=step,
            recovery_codes=json.dumps(recovery_hashes),
        )

    @staticmethod
    async def clear_totp(user_id: str) -> bool:
        return await Auth._update(
            user_id,
            totp_secret=None,
            totp_enabled_at=None,
            totp_last_step=None,
            recovery_codes=None,
        )

    @staticmethod
    async def claim_totp_step(user_id: str, step: int) -> bool:
        """Record `step` as used. False if it (or a later one) was already used."""
        return await Auth._update(
            user_id,
            (Auth.totp_last_step.is_(None)) | (Auth.totp_last_step < step),
            totp_last_step=step,
        )

    @staticmethod
    async def claim_recovery_code(auth: Auth, code_hash: str) -> bool:
        """Use up a recovery code. False if it isn't one of the unused codes."""
        hashes = auth.recovery_code_hashes
        if code_hash not in hashes:
            return False
        hashes.remove(code_hash)
        # Only if nobody used a code in between.
        return await Auth._update(
            auth.user_id,
            Auth.recovery_codes == auth.recovery_codes,
            recovery_codes=json.dumps(hashes),
        )

    @staticmethod
    async def set_sessions_valid_after(user_id: str, at: float) -> bool:
        return await Auth._update(user_id, sessions_valid_after=at)

    @staticmethod
    async def sessions_valid_after_all() -> dict[str, float]:
        async with await get_db() as db:
            result = await db.execute(
                select(Auth.user_id, Auth.sessions_valid_after).where(
                    Auth.sessions_valid_after.isnot(None)
                )
            )
            return {user_id: at for user_id, at in result.all()}

    @staticmethod
    async def get_by_username_any(username: str) -> Auth | None:
        """Look up a login by username, PAM users included."""
        async with await get_db() as db:
            result = await db.execute(select(Auth).where(Auth.username == username))
            return result.scalar_one_or_none()


class UserStates(Base):
    """Workspace layout state. 1:1 with User."""

    __tablename__ = "user_states"

    user_id = Column(Text, ForeignKey("users.id"), primary_key=True)
    data = Column(JSON, nullable=False, default=dict)
    updated_at = Column(BigInteger, nullable=True)

    user = relationship("User", back_populates="states")

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_data(user_id: str) -> dict:
        """Get user state data. Returns empty dict if not set."""
        async with await get_db() as db:
            result = await db.execute(select(UserStates).where(UserStates.user_id == user_id))
            row = result.scalar_one_or_none()
            return row.data if row else {}

    @staticmethod
    async def save_data(user_id: str, data: dict) -> None:
        """Upsert user state data."""
        async with await get_db() as db:
            result = await db.execute(select(UserStates).where(UserStates.user_id == user_id))
            row = result.scalar_one_or_none()
            if row:
                row.data = data
            else:
                db.add(UserStates(user_id=user_id, data=data))
            await db.commit()
