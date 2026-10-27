"""CodeBuddy login and usage for Settings → Usage and the composer.

The login is the one CodeBuddy saved under the server user's home. Its token is only
read to call CodeBuddy's own billing endpoint and to tell whether the login is still
valid. It is never logged or returned.

The balance is `POST /v2/billing/meter/get-user-resource` on the login's site
(www.codebuddy.cn for this install). Each active credit package is one row.
`CapacityUsed` stays at the package face value and does not move as credits are
spent; the current cycle (`CycleCapacity*`) is what decreases. `usage` is separate:
the credits CodeBuddy reported for model calls made from this server's chats.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from cptr.models import Chat, ChatMessage
from cptr.utils.agents.codebuddy import auth_files, codebuddy_home
from cptr.utils.db import get_db

log = logging.getLogger(__name__)

MODEL_PREFIX = "agent:codebuddy/"
DEFAULT_DOMAIN = "www.codebuddy.cn"
RESOURCE_PATH = "/v2/billing/meter/get-user-resource"
TIMEOUT_SECONDS = 12
CACHE_SECONDS = 60
# CycleEndTime has no timezone. CodeBuddy's site shows Beijing time.
_BEIJING = timezone(timedelta(hours=8))

# token hash → (fetched at, balance dict or {"error": ...})
_balance_cache: dict[str, tuple[float, dict[str, Any]]] = {}


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


def _amount(account: dict[str, Any], precise: str, whole: str) -> float | None:
    """A credit amount. Precise fields arrive as strings; missing is not zero."""
    for key in (precise, whole):
        value = account.get(key)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            try:
                return float(text)
            except ValueError:
                continue
    return None


def _package_figures(account: dict[str, Any]) -> tuple[float, float, float]:
    """Size, used, and remaining for the cycle that spending actually draws down."""
    cycle_total = _amount(account, "CycleCapacitySizePrecise", "CycleCapacitySize")
    cycle_used = _amount(account, "CycleCapacityUsedPrecise", "CycleCapacityUsed")
    if cycle_used is None:
        cycle_used = _amount(account, "ActualCycleCapacityUsed", "ActualCycleCapacityUsed")
    cycle_remain = _amount(account, "CycleCapacityRemainPrecise", "CycleCapacityRemain")
    if (
        cycle_total is not None
        and cycle_total > 0
        and cycle_used is not None
        and cycle_remain is not None
    ):
        return cycle_total, cycle_used, cycle_remain
    return (
        _amount(account, "CapacitySizePrecise", "CapacitySize") or 0.0,
        _amount(account, "CapacityUsedPrecise", "CapacityUsed") or 0.0,
        _amount(account, "CapacityRemainPrecise", "CapacityRemain") or 0.0,
    )


def _round_credits(value: float) -> float:
    return round(value, 4)


def _percent(used: float, total: float) -> float:
    if total <= 0:
        return 0.0
    return round(min(100.0, max(0.0, used / total * 100.0)), 2)


def _cycle_end_seconds(value: Any) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.strptime(value.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return int(parsed.replace(tzinfo=_BEIJING).timestamp())


def _site_host(domain: str) -> str:
    host = domain.strip().split("://")[-1].split("/")[0] or DEFAULT_DOMAIN
    if host.count(".") == 1:
        host = f"www.{host}"
    return host


def parse_user_resource(payload: Any) -> dict[str, Any]:
    """Turn a get-user-resource body into totals plus one row per active package."""
    if not isinstance(payload, dict):
        raise ValueError("quota response was not an object")
    code = payload.get("code")
    if code not in (0, "0", None):
        raise ValueError(str(payload.get("msg") or "quota request failed")[:200])
    data = payload.get("data")
    response = data.get("Response") if isinstance(data, dict) else None
    if isinstance(response, dict) and response.get("Error"):
        error = response["Error"]
        message = error.get("Message") if isinstance(error, dict) else error
        raise ValueError(str(message or "quota request failed")[:200])
    body = response.get("Data") if isinstance(response, dict) else None
    accounts = body.get("Accounts") if isinstance(body, dict) else None
    if not isinstance(accounts, list):
        raise ValueError("quota response had no resource list")

    packages: list[dict[str, Any]] = []
    for index, account in enumerate(accounts):
        if not isinstance(account, dict) or account.get("Status") not in (0, "0"):
            continue
        total, used, remain = _package_figures(account)
        if total <= 0 and used <= 0 and remain <= 0:
            continue
        name = account.get("PackageName")
        if not isinstance(name, str) or not name.strip():
            name = account.get("SubProductName")
        label = name.strip() if isinstance(name, str) and name.strip() else "CodeBuddy"
        resets_at = _cycle_end_seconds(account.get("CycleEndTime"))
        resource = account.get("ResourceId")
        package_code = account.get("PackageCode")
        if isinstance(resource, str) and resource:
            package_id = resource
        elif isinstance(package_code, str) and package_code:
            package_id = f"{package_code}:{resets_at or index}"
        else:
            package_id = f"{label}:{resets_at or index}"
        packages.append(
            {
                "id": package_id,
                "label": label,
                "total": _round_credits(total),
                "used": _round_credits(used),
                "remain": _round_credits(remain),
                "used_percent": _percent(used, total),
                "resets_at": resets_at,
            }
        )
    packages.sort(
        key=lambda item: (item["resets_at"] is None, item["resets_at"] or 0, item["label"])
    )

    total = _round_credits(sum(item["total"] for item in packages))
    used = _round_credits(sum(item["used"] for item in packages))
    remain = _round_credits(sum(item["remain"] for item in packages))
    ends = [item["resets_at"] for item in packages if item["resets_at"]]
    used_percent = _percent(used, total)
    return {
        "total": total,
        "used": used,
        "remain": remain,
        "used_percent": used_percent,
        "remaining_percent": round(100.0 - used_percent, 2) if total > 0 else 0.0,
        "resets_at": min(ends) if ends else None,
        "packages": packages,
    }


def _credentials(home: Path | None = None) -> tuple[str, str, str] | None:
    """Access token, user id, and site host. The token stays in this process."""
    data = _read_login(home or codebuddy_home())
    if data is None:
        return None
    auth = data["auth"]
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    token = auth.get("accessToken")
    uid = account.get("uid")
    domain = auth.get("domain") if isinstance(auth.get("domain"), str) else ""
    if not isinstance(token, str) or not token or not isinstance(uid, str) or not uid:
        return None
    return token, uid, _site_host(domain)


async def _fetch_balance(token: str, uid: str, host: str) -> dict[str, Any]:
    x_domain = host.removeprefix("www.")
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"https://{host}{RESOURCE_PATH}",
            json={},
            headers={
                "Authorization": f"Bearer {token}",
                "X-User-Id": uid,
                "X-Domain": x_domain,
                "Accept": "application/json",
                "User-Agent": "CodeBuddy/2.177.0",
            },
        )
    if not response.is_success:
        raise ValueError(f"quota endpoint returned {response.status_code}")
    text = response.text.lstrip()
    if "json" not in response.headers.get("content-type", "") and not text.startswith("{"):
        raise ValueError("quota endpoint returned HTML")
    return parse_user_resource(response.json())


async def get_codebuddy_balance(force: bool = False) -> dict[str, Any] | None:
    """Active credit packages, None when signed out, or {"error": ...}."""
    creds = _credentials()
    if creds is None:
        return None
    token, uid, host = creds
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    now = time.monotonic()
    cached = _balance_cache.get(key)
    if not force and cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        value: dict[str, Any] = await _fetch_balance(token, uid, host)
    except Exception as error:
        log.warning("CodeBuddy quota unavailable: %s", type(error).__name__)
        value = {"error": f"{type(error).__name__}: {error}"[:200]}
    _balance_cache.clear()
    _balance_cache[key] = (now, value)
    return value


async def get_codebuddy_quota(user_id: str, force: bool = False) -> dict[str, Any]:
    """Login, official credit packages, and usage from this server's chats."""
    status = login_status()
    balance = None
    error = None
    if status.get("signed_in"):
        snapshot = await get_codebuddy_balance(force=force)
        if snapshot is None:
            error = "CodeBuddy quota unavailable"
        elif "error" in snapshot:
            error = snapshot["error"]
        else:
            balance = snapshot
    return {
        **status,
        "usage": await usage_summary(user_id),
        "balance": balance,
        "error": error,
        "fetched_at": int(time.time()),
    }
