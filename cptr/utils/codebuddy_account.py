"""CodeBuddy login and usage for Settings → Usage and the composer.

The login is the one CodeBuddy saved under the server user's home. Its token is only
read to tell whether the login is still valid; it is never logged or returned.

CodeBuddy does not let its command-line login read the account's remaining credits:
codebuddy.cn's billing API only accepts the website's own session. So the balance
stays on codebuddy.cn (`usage_url`), and the usage shown here is the credits CodeBuddy
reported for each model call made from this server's chats.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from cptr.models import Chat, ChatMessage
from cptr.utils.agents.codebuddy import auth_files, codebuddy_home
from cptr.utils.db import get_db

MODEL_PREFIX = "agent:codebuddy/"
DEFAULT_DOMAIN = "www.codebuddy.cn"


def _read_login(home: Path) -> dict[str, Any] | None:
    for path in auth_files(home):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("auth"), dict):
            return data
    return None


def login_status(home: Path | None = None) -> dict[str, Any]:
    """Who CodeBuddy is signed in as, without the token."""
    data = _read_login(home or codebuddy_home())
    if data is None:
        return {"signed_in": False}
    auth = data["auth"]
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    expires_at = auth.get("expiresAt")
    expires = (
        int(expires_at) // 1000
        if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool)
        else None
    )
    domain = auth.get("domain") if isinstance(auth.get("domain"), str) else ""
    return {
        "signed_in": bool(auth.get("accessToken")),
        # CodeBuddy refreshes the login itself as it starts, so a passed expiry only
        # means it has not run since.
        "expires_at": expires,
        "nickname": account.get("nickname") if isinstance(account.get("nickname"), str) else "",
        "account_type": account.get("type") if isinstance(account.get("type"), str) else "",
        "domain": domain or DEFAULT_DOMAIN,
        "usage_url": f"https://{domain or DEFAULT_DOMAIN}/profile/usage",
    }


def _created_seconds(value: Any) -> int:
    try:
        created = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return created // 1000 if created > 10_000_000_000 else created


def _credits(usage: Any) -> float:
    value = usage.get("credits") if isinstance(usage, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


async def usage_summary(user_id: str, now: datetime | None = None) -> dict[str, Any]:
    """Credits and replies of this user's CodeBuddy turns: today, this week, this month (UTC)."""
    now = now or datetime.now(timezone.utc)
    today = datetime.combine(now.date(), datetime.min.time(), tzinfo=timezone.utc)
    starts = {
        "day": today,
        "week": today - timedelta(days=today.weekday()),
        "month": today.replace(day=1),
    }
    earliest = int(min(starts.values()).timestamp())
    async with await get_db() as db:
        chat_ids = [
            row[0] for row in (await db.execute(select(Chat.id).where(Chat.user_id == user_id)))
        ]
        rows = []
        if chat_ids:
            result = await db.execute(
                select(ChatMessage.created_at, ChatMessage.usage).where(
                    ChatMessage.chat_id.in_(chat_ids),
                    ChatMessage.model.like(f"{MODEL_PREFIX}%"),
                    ChatMessage.role == "assistant",
                )
            )
            rows = [
                (seconds, usage)
                for created_at, usage in result.all()
                if (seconds := _created_seconds(created_at)) >= earliest
            ]
    periods = {}
    for name, start in starts.items():
        start_seconds = int(start.timestamp())
        selected = [usage for seconds, usage in rows if seconds >= start_seconds]
        periods[name] = {
            "credits": round(sum(_credits(usage) for usage in selected), 4),
            "replies": len(selected),
            "period_start": start_seconds,
        }
    return periods


async def get_codebuddy_quota(user_id: str) -> dict[str, Any]:
    """Login, usage from this server's chats, and where to see the balance."""
    return {
        **login_status(),
        "usage": await usage_summary(user_id),
        # Not readable with CodeBuddy's command-line login; see the module docstring.
        "balance": None,
        "fetched_at": int(time.time()),
    }
