"""SuperGrok weekly credit quota for the local Grok CLI login.

Ported from Grok App (`src-tauri/src/supergrok_quota.rs`). The access token is read
from the Grok CLI's `auth.json` (written by `grok login`) and is never logged.

Source of truth is the grok.com usage page RPC:
`POST https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig`
(empty gRPC-web request). The JSON billing endpoint the CLI uses is the fallback.
The plan name ("SuperGrok", "SuperGrok Heavy") comes from the CLI proxy's
settings, then its user profile, then the token's `tier` claim.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

BILLING_URL = "https://grok.com/grok_api_v2.GrokBuildBilling/GetGrokCreditsConfig"
CLI_BILLING_URL = "https://cli-chat-proxy.grok.com/v1/billing?format=credits"
CLI_SETTINGS_URL = "https://cli-chat-proxy.grok.com/v1/settings"
CLI_USER_URL = "https://cli-chat-proxy.grok.com/v1/user?include=subscription"
# Empty protobuf message framed as gRPC-web (flags=0, length=0).
EMPTY_GRPC_WEB_FRAME = b"\x00\x00\x00\x00\x00"
TIMEOUT_SECONDS = 12
CACHE_SECONDS = 60

# grok.com usage proto enum. The RPC sends these numbers and no name.
# 1 API, 2 Grok Build, 3 Grok Plugins, 4 Grok Chat, 5 Grok Imagine, 6 Grok Voice.
PRODUCT_LABELS = {
    1: "API",
    2: "Grok Build",
    3: "Grok Plugins",
    4: "Grok Chat",
    5: "Grok Imagine",
    6: "Grok Voice",
}

# Compact wire names (GrokChat, PRODUCT_GROK_BUILD, Api) for the same enum.
_COMPACT_IDS = {
    "api": 1,
    "xaiapi": 1,
    "grokbuild": 2,
    "grokplugins": 3,
    "grokchat": 4,
    "grokimagine": 5,
    "grokvoice": 6,
}

PLAN_CACHE_SECONDS = 30 * 60

_cache: dict[str, Any] = {"at": 0.0, "value": None}
# sha256(token)[:16] → (fetched at, plan name or None)
_plan_cache: dict[str, tuple[float, str | None]] = {}


def invalidate_cache() -> None:
    """Drop the cached quota, e.g. after the Grok CLI login changed."""
    _cache["at"] = 0.0
    _cache["value"] = None


# ── Token discovery ─────────────────────────────────────────


def _auth_json_candidates(extra_homes: list[str] | None = None) -> list[Path]:
    """auth.json paths in the order the Grok agent would use them.

    Agent profile homes come before `~/.grok` because cptr runs the agent with
    that HOME, so on a tie the login the agent actually uses wins.
    """
    paths: list[Path] = []
    grok_home = os.environ.get("GROK_HOME")
    if grok_home:
        paths.append(Path(grok_home).expanduser() / "auth.json")
    for home in extra_homes or []:
        paths.append(Path(home).expanduser() / ".grok" / "auth.json")
    paths.append(Path.home() / ".grok" / "auth.json")
    return list(dict.fromkeys(paths))


def _str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _usable_token(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return None
    for field in ("key", "access_token"):
        value = _str(entry.get(field))
        if value:
            return value
    return None


def _usable_entry(data: object) -> dict | None:
    """The credential entry holding a token.

    auth.json maps issuer::client_id → credential entry; a stale first entry from
    an older login must not mask the valid one.
    """
    if not isinstance(data, dict):
        return None
    entries = [entry for entry in data.values() if isinstance(entry, dict)]
    for entry in entries:
        if _usable_token(entry) or _str(entry.get("refresh_token")):
            return entry
    return entries[0] if entries else None


def _read_auth_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _profile_from_entry(entry: dict | None) -> dict:
    entry = entry or {}
    email = _str(entry.get("email"))
    name = " ".join(
        part for part in (_str(entry.get("first_name")), _str(entry.get("last_name"))) if part
    )
    expires_at = _str(entry.get("expires_at"))
    expires = _parse_rfc3339(expires_at)
    has_refresh = bool(_str(entry.get("refresh_token")))
    return {
        "signed_in": bool(_usable_token(entry)) or has_refresh,
        "email": email,
        "display_name": name or email,
        "user_id": _str(entry.get("user_id")) or _str(entry.get("principal_id")),
        "team_id": _str(entry.get("team_id")),
        "expires_at": expires_at,
        "expired": bool(expires and expires < datetime.now(timezone.utc)),
        "has_refresh": has_refresh,
    }


def _signed_out_profile() -> dict:
    return _profile_from_entry(None)


def best_auth(extra_homes: list[str] | None = None) -> tuple[dict, Path | None, dict | None]:
    """(profile, auth.json path, credential entry) of the strongest Grok CLI login.

    Ranking: signed in, then refreshable, then unexpired; ties keep path order.
    """
    best: tuple[dict, Path | None, dict | None] = (_signed_out_profile(), None, None)
    best_score: tuple[bool, bool, bool] | None = None
    for path in _auth_json_candidates(extra_homes):
        data = _read_auth_json(path)
        if data is None:
            continue
        entry = _usable_entry(data)
        profile = _profile_from_entry(entry)
        score = (profile["signed_in"], profile["has_refresh"], not profile["expired"])
        if best_score is None or score > best_score:
            best, best_score = (profile, path, entry), score
    return best


def read_auth_profile(extra_homes: list[str] | None = None) -> dict:
    """Public profile of the Grok CLI login (never includes tokens)."""
    return best_auth(extra_homes)[0]


def read_access_token(extra_homes: list[str] | None = None) -> str | None:
    """Return the Grok CLI access token, or None when signed out."""
    token = _usable_token(best_auth(extra_homes)[2])
    if token:
        return token
    for path in _auth_json_candidates(extra_homes):
        token = _usable_token(_usable_entry(_read_auth_json(path)))
        if token:
            return token
    return None


async def grok_profile_homes() -> list[str]:
    try:
        from cptr.utils.agents.models import get_raw_agent_profiles

        profiles = await get_raw_agent_profiles()
    except Exception:
        return []
    if not isinstance(profiles, list):
        return []
    return [
        str(profile["home"])
        for profile in profiles
        if isinstance(profile, dict) and profile.get("agent") == "grok" and profile.get("home")
    ]


# ── Protobuf (gRPC-web) parsing ─────────────────────────────


def _read_varint(data: bytes, index: int) -> tuple[int, int] | None:
    result = 0
    shift = 0
    while index < len(data) and shift < 64:
        byte = data[index]
        index += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, index
        shift += 7
    return None


def _fields(message: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    index = 0
    while index < len(message):
        key = _read_varint(message, index)
        if key is None:
            return
        tag, index = key
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            parsed = _read_varint(message, index)
            if parsed is None:
                return
            value, index = parsed
        elif wire == 1:
            value, index = message[index : index + 8], index + 8
        elif wire == 2:
            parsed = _read_varint(message, index)
            if parsed is None:
                return
            length, index = parsed
            value, index = message[index : index + length], index + length
        elif wire == 5:
            value, index = message[index : index + 4], index + 4
        else:
            return
        if index > len(message):
            return
        yield field, wire, value


def _float32(value: bytes) -> float | None:
    if len(value) != 4:
        return None
    number = struct.unpack("<f", value)[0]
    return number if number == number and abs(number) != float("inf") else None


def _timestamp(message: bytes) -> int | None:
    for field, wire, value in _fields(message):
        if field == 1 and wire == 0 and value > 0:
            return int(value)
    return None


def _compact_name(name: str) -> str:
    raw = name.strip()
    if raw.upper().startswith("PRODUCT_"):
        raw = raw[len("PRODUCT_") :]
    return "".join(ch for ch in raw.lower() if ch.isalnum())


def _humanize(raw: str) -> str:
    """Split a wire name into words: GrokAppBuilder → Grok App Builder."""
    chars: list[str] = []
    previous = ""
    for ch in raw.replace("-", " ").replace("_", " "):
        if ch.isupper() and previous.islower():
            chars.append(" ")
        chars.append(ch)
        previous = ch
    words = []
    for word in "".join(chars).split():
        if word.isupper():
            words.append(word[:1] + word[1:].lower())
        elif word[:1].islower():
            words.append(word[:1].upper() + word[1:])
        else:
            words.append(word)
    return " ".join(words)


def _product_id_from_name(name: str) -> int:
    return _COMPACT_IDS.get(_compact_name(name), 0)


def product_label(name: str | None = None, product_id: int | None = None) -> str:
    """Official product name. A number is used only when no name is known."""
    raw = (name or "").strip()
    if raw.upper().startswith("PRODUCT_"):
        raw = raw[len("PRODUCT_") :]
    compact = _compact_name(raw)
    if compact in _COMPACT_IDS:
        return PRODUCT_LABELS[_COMPACT_IDS[compact]]
    if raw:
        return _humanize(raw)
    if product_id in PRODUCT_LABELS:
        return PRODUCT_LABELS[product_id]
    if product_id is not None:
        return str(product_id)
    return ""


def _product(message: bytes) -> dict | None:
    product_id = None
    used = None
    for field, wire, value in _fields(message):
        if field == 1 and wire == 0:
            product_id = int(value)
        elif field == 2 and wire == 5:
            used = _float32(value)
    if product_id is None:
        return None
    return {
        "id": product_id,
        "label": product_label(product_id=product_id),
        "used_percent": _clamp(used or 0.0),
    }


def _grpc_web_frames(data: bytes) -> tuple[list[bytes], dict[str, str]]:
    frames: list[bytes] = []
    trailers: dict[str, str] = {}
    index = 0
    while index + 5 <= len(data):
        flags = data[index]
        length = int.from_bytes(data[index + 1 : index + 5], "big")
        start, end = index + 5, index + 5 + length
        if end > len(data):
            break
        if flags & 0x80:
            for line in data[start:end].decode("utf-8", "replace").splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    trailers[key.strip().lower()] = value.strip()
        else:
            frames.append(data[start:end])
        index = end
    return frames, trailers


def _clamp(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


def _snapshot(
    used: float,
    period_start: int | None,
    resets_at: int | None,
    products: list[dict],
    source: str,
) -> dict:
    used = _clamp(used)
    return {
        "used_percent": used,
        "remaining_percent": round(100.0 - used, 2),
        "period_start": period_start,
        "resets_at": resets_at,
        "products": products,
        "source": source,
        "fetched_at": int(time.time()),
    }


def parse_credits_config(data: bytes) -> dict | None:
    """Parse a GetGrokCreditsConfig gRPC-web response body."""
    frames, trailers = _grpc_web_frames(data)
    status = trailers.get("grpc-status")
    if status and status != "0":
        raise ValueError(f"quota RPC status {status}: {trailers.get('grpc-message', '')}")
    for frame in frames or [data]:
        for field, wire, config in _fields(frame):
            if field != 1 or wire != 2:
                continue
            used = None
            period_start = resets_at = None
            products: list[dict] = []
            for inner_field, inner_wire, value in _fields(config):
                if inner_field == 1 and inner_wire == 5:
                    used = _float32(value)
                elif inner_field == 4 and inner_wire == 2:
                    period_start = _timestamp(value)
                elif inner_field == 5 and inner_wire == 2:
                    resets_at = _timestamp(value)
                elif inner_field == 7 and inner_wire == 2:
                    product = _product(value)
                    if product:
                        products.append(product)
            if used is not None or products:
                return _snapshot(used or 0.0, period_start, resets_at, products, "grpc-web")
    return None


def _iso_seconds(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _cli_product_rows(payload: dict) -> list[dict]:
    root = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    if not isinstance(root, dict):
        return []
    rows = []
    for item in root.get("productUsage") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("product") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": _product_id_from_name(name),
                "label": product_label(name),
                "used_percent": _clamp(item.get("usagePercent") or 0.0),
            }
        )
    return rows


def _label_unnamed_products(products: list[dict], payload: dict) -> None:
    """Fill labels for proto ids outside the known enum, using CLI wire names."""
    unknown = [
        product
        for product in products
        if isinstance(product, dict) and product.get("id") not in PRODUCT_LABELS
    ]
    if not unknown:
        return
    known_ids = {
        product.get("id")
        for product in products
        if isinstance(product, dict) and product.get("id") in PRODUCT_LABELS
    }
    pool = [row for row in _cli_product_rows(payload) if row["id"] not in known_ids]
    for product in unknown:
        matches = [row for row in pool if row["used_percent"] == product.get("used_percent")]
        if len(matches) != 1:
            continue
        product["label"] = matches[0]["label"]
        pool.remove(matches[0])
    still = [
        product
        for product in unknown
        if product.get("label") == str(product.get("id"))
    ]
    if still and len(still) == len(pool):
        same_order = [product.get("used_percent") for product in still] == [
            row["used_percent"] for row in pool
        ]
        if same_order:
            for product, row in zip(still, pool):
                product["label"] = row["label"]


def parse_cli_billing(payload: dict) -> dict:
    """Parse the CLI proxy's JSON billing response."""
    root = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    products = []
    next_unknown = -1
    for row in _cli_product_rows(payload):
        product_id = row["id"]
        # Proto ids start at 1. A wire name with no enum gets its own key.
        if not product_id:
            product_id = next_unknown
            next_unknown -= 1
        products.append(
            {
                "id": product_id,
                "label": row["label"],
                "used_percent": row["used_percent"],
            }
        )
    current = root.get("currentPeriod") if isinstance(root.get("currentPeriod"), dict) else {}
    return _snapshot(
        float(root.get("creditUsagePercent") or 0.0),
        _iso_seconds(root.get("billingPeriodStart") or current.get("start")),
        _iso_seconds(root.get("billingPeriodEnd") or current.get("end")),
        products,
        "cli-chat-proxy",
    )


# ── Fetch ───────────────────────────────────────────────────


async def _fetch_grpc(client: httpx.AsyncClient, token: str) -> dict:
    response = await client.post(
        BILLING_URL,
        content=EMPTY_GRPC_WEB_FRAME,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "Origin": "https://grok.com",
            "Referer": "https://grok.com/?_s=usage",
            "Accept": "*/*",
        },
    )
    status = response.headers.get("grpc-status")
    if status and status != "0":
        raise ValueError(f"quota RPC status {status}")
    response.raise_for_status()
    if response.content.lstrip()[:1] == b"<":
        raise ValueError("quota endpoint returned HTML")
    snapshot = parse_credits_config(response.content)
    if snapshot is None:
        raise ValueError("quota response had no credits config")
    return snapshot


async def _fetch_cli(client: httpx.AsyncClient, token: str) -> dict:
    response = await client.get(
        CLI_BILLING_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "x-grok-client-mode": "cli",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    return parse_cli_billing(response.json())


async def _fill_unnamed_product_labels(
    client: httpx.AsyncClient, token: str, snapshot: dict
) -> None:
    """Name a proto id the known enum does not cover.

    The grok.com RPC sends only a number. The CLI billing JSON has the
    original product string, matched by the same usage percent.
    """
    products = snapshot.get("products")
    if not isinstance(products, list):
        return
    if not any(
        isinstance(item, dict) and item.get("id") not in PRODUCT_LABELS for item in products
    ):
        return
    try:
        response = await client.get(
            CLI_BILLING_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "x-grok-client-mode": "cli",
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        log.info("SuperGrok product names unavailable: %s", error)
        return
    if isinstance(payload, dict):
        _label_unnamed_products(products, payload)


# ── Plan name ───────────────────────────────────────────────


def plan_from_code(code: str | None) -> str | None:
    """Map a subscription tier enum ("SuperGrokPro") to its display name."""
    raw = (code or "").strip()
    compact = raw.lower().replace("_", "").replace(" ", "").replace("-", "")
    if not compact or compact in {"free", "basic", "none", "null", "anonymous"}:
        return None
    if compact in {"supergrokpro", "supergrokheavy", "heavy"}:
        return "SuperGrok Heavy"
    if compact in {"supergrok", "supergroklite", "grokpro"}:
        return "SuperGrok"
    if compact in {"xpremiumplus", "premiumplus"}:
        return "X Premium+"
    if compact in {"xpremium", "premium"}:
        return "X Premium"
    return raw


def _plan_from_jwt(token: str) -> str | None:
    """Fallback from the token's unverified `tier` claim (display only)."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        tier = int(payload.get("tier"))
    except (ValueError, TypeError, AttributeError):
        return None
    # Observed: SuperGrok-capable accounts have tier ≥ 2, Heavy accounts 5.
    if tier >= 5:
        return "SuperGrok Heavy"
    if tier >= 2:
        return "SuperGrok"
    return None


async def _fetch_plan(client: httpx.AsyncClient, token: str) -> str | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "x-grok-client-mode": "cli",
        "Accept": "application/json",
    }
    try:
        response = await client.get(CLI_SETTINGS_URL, headers=headers)
        if response.is_success:
            data = response.json()
            display = _str(
                data.get("subscription_tier_display") or data.get("subscriptionTierDisplay")
            )
            if display:
                return display
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        log.info("Grok plan settings unavailable: %s", error)
    try:
        response = await client.get(CLI_USER_URL, headers=headers)
        if response.is_success:
            data = response.json()
            plan = plan_from_code(data.get("subscriptionTier") or data.get("subscription_tier"))
            if plan:
                return plan
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        log.info("Grok plan profile unavailable: %s", error)
    return _plan_from_jwt(token)


async def _plan(client: httpx.AsyncClient, token: str) -> str | None:
    key = hashlib.sha256(token.encode()).hexdigest()[:16]
    cached = _plan_cache.get(key)
    if cached and time.monotonic() - cached[0] < PLAN_CACHE_SECONDS:
        return cached[1]
    plan = await _fetch_plan(client, token)
    _plan_cache.clear()
    _plan_cache[key] = (time.monotonic(), plan)
    return plan


async def get_supergrok_quota(force: bool = False) -> dict | None:
    """Return the SuperGrok quota snapshot, None when signed out, or {"error": ...}."""
    now = time.monotonic()
    if not force and _cache["at"] and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]

    token = read_access_token(await grok_profile_homes())
    if not token:
        value = None
    else:
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS, headers={"User-Agent": "cptr (unofficial)"}
        ) as client:
            try:
                value = await _fetch_grpc(client, token)
            except Exception as grpc_error:
                log.info("SuperGrok quota RPC failed (%s); trying CLI billing", grpc_error)
                try:
                    value = await _fetch_cli(client, token)
                except Exception as cli_error:
                    log.warning("SuperGrok quota unavailable: %s", cli_error)
                    value = {
                        "error": f"{type(cli_error).__name__}: {cli_error}"[:200],
                        "fetched_at": int(time.time()),
                    }
            else:
                await _fill_unnamed_product_labels(client, token, value)
            value["plan"] = await _plan(client, token)

    _cache["at"] = now
    _cache["value"] = value
    return value
