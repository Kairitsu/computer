"""AnyRouter token catalog and balance.

The relay is a new-api compatible host. Model ids come from ``GET /v1/models``.
The OpenAI billing subscription endpoint returns a placeholder cap, so it is
never shown as a balance. The token-usage endpoint is the balance source when
this token can actually read it.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

ANYROUTER_HOME = Path("/home/ubuntu/.codex-anyrouter")
ANYROUTER_PROFILE_ID = "anyrouter"
ANYROUTER_CODEX = "/home/ubuntu/.local/bin/codex"
ANYROUTER_ORIGIN = "https://anyrouter.top"
ANYROUTER_BASE_URL = f"{ANYROUTER_ORIGIN}/v1"
KEY_FILE = ANYROUTER_HOME / "anyrouter.key"
PROBE_FILE = ANYROUTER_HOME / "model-probe.json"
ENV_KEY = "ANYROUTER_API_KEY"
DEFAULT_MODEL = "gpt-6.1-sol"

_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]+")
_QUOTA_TTL_SECONDS = 60
_PROBE_RETRY_SECONDS = 6 * 60 * 60
_quota_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def redact(text: str) -> str:
    return _SECRET.sub("sk-redacted", text)


def read_api_key() -> str:
    """Return the AnyRouter token from its private file, or an empty string."""
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def codex_child_env(base: dict[str, str]) -> dict[str, str]:
    """Environment for a Codex process that must not see other providers' tokens."""
    env = dict(base)
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CODEX_ACCESS_TOKEN",
        "XAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        env.pop(name, None)
    env["CODEX_HOME"] = str(ANYROUTER_HOME)
    env["HOME"] = str(Path.home())
    key = read_api_key()
    if key:
        env[ENV_KEY] = key
    return env


def load_probe() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(PROBE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict):
        return {}
    clean: dict[str, dict[str, Any]] = {}
    for model, entry in results.items():
        if isinstance(model, str) and isinstance(entry, dict):
            clean[model] = entry
    return clean


def save_probe(results: dict[str, dict[str, Any]]) -> None:
    ANYROUTER_HOME.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ANYROUTER_HOME, 0o700)
    temporary = PROBE_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(PROBE_FILE)


def classify_failure(text: str) -> str:
    """Map an upstream error to accepted (capacity), rejected (protocol/model), or error."""
    lowered = redact(text).lower()
    hard = (
        "invalid codex",
        "不支持",
        "unsupported",
        "model_not_found",
        "model not found",
        "does not exist",
        "unknown model",
        "invalid model",
        "no such model",
        "not allowed for",
    )
    if any(phrase in lowered for phrase in hard):
        return "rejected"
    capacity = (
        "负载",
        "overload",
        "capacity",
        "rate limit",
        "too many",
        " 500",
        " 502",
        " 503",
        "status 500",
        "status 502",
        "status 503",
    )
    if any(phrase in lowered for phrase in capacity) or lowered.startswith("500"):
        return "accepted"
    if any(phrase in lowered for phrase in ("401", "403", "invalid api key", "incorrect api key")):
        return "error"
    return "error"


def _needs_probe(entry: dict[str, Any] | None, now: float) -> bool:
    if not entry:
        return True
    status = entry.get("status")
    if status in {"accepted", "rejected"}:
        return False
    probed_at = entry.get("probed_at")
    return not isinstance(probed_at, (int, float)) or now - probed_at > _PROBE_RETRY_SECONDS


async def _request(path: str, key: str) -> tuple[int, str, bytes]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            f"{ANYROUTER_ORIGIN}{path}",
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "cptr-anyrouter",
            },
        )
        return response.status_code, response.headers.get("content-type", ""), response.content


async def fetch_model_ids() -> list[str]:
    key = read_api_key()
    if not key:
        raise RuntimeError("AnyRouter key file is missing")
    status, content_type, body = await _request("/v1/models", key)
    if status != 200 or "json" not in content_type.lower():
        raise RuntimeError(f"AnyRouter model list failed ({status})")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AnyRouter model list was not JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("AnyRouter model list had no data")
    ids: list[str] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip():
            ids.append(row["id"].strip())
    return list(dict.fromkeys(ids))


def selectable_from_probe(live_ids: list[str], probe: dict[str, dict[str, Any]]) -> list[str]:
    """Keep catalog order. A model is selectable once a Codex-shaped request is accepted."""
    return [
        model
        for model in live_ids
        if (probe.get(model) or {}).get("status") == "accepted"
    ]


async def selectable_model_ids() -> list[str]:
    live = await fetch_model_ids()
    probe = load_probe()
    now = time.time()
    missing = [model for model in live if _needs_probe(probe.get(model), now)]
    if missing:
        from cptr.utils.agents.codex import probe_models

        probed = await probe_models(missing)
        for model, entry in probed.items():
            probe[model] = {**entry, "probed_at": now}
        save_probe(probe)
    return selectable_from_probe(live, probe)


def _json_object(content_type: str, body: bytes) -> dict[str, Any] | None:
    if "json" not in content_type.lower():
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def token_balance(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a balance only when the token-usage payload has a remaining amount."""
    if not payload:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict) or "total_available" not in data:
        return None
    try:
        available = int(data.get("total_available") or 0)
        granted = int(data.get("total_granted") or 0)
        used = int(data.get("total_used") or 0)
    except (TypeError, ValueError):
        return None
    return {
        "available": available,
        "granted": granted,
        "used": used,
        "unlimited": bool(data.get("unlimited_quota")),
    }


def spent_usd(payload: dict[str, Any] | None) -> float | None:
    """``total_usage`` is cumulative spend in hundredths of a dollar, not a balance."""
    if not payload or "total_usage" not in payload:
        return None
    if payload.get("hard_limit_usd") is not None or payload.get("soft_limit_usd") is not None:
        return None
    try:
        return float(payload["total_usage"]) / 100
    except (TypeError, ValueError):
        return None


async def get_anyrouter_quota(force: bool = False) -> dict[str, Any]:
    now = time.time()
    cached = _quota_cache.get("payload")
    if (
        not force
        and isinstance(cached, dict)
        and now - float(_quota_cache.get("at") or 0) < _QUOTA_TTL_SECONDS
    ):
        return cached

    fetched_at = int(now)
    key = read_api_key()
    if not key:
        payload = {
            "readable": False,
            "reason": "missing_key",
            "spent_usd": None,
            "fetched_at": fetched_at,
        }
        _quota_cache.update(at=now, payload=payload)
        return payload

    balance = None
    for path in ("/api/usage/token", "/api/usage/token/"):
        try:
            status, content_type, body = await _request(path, key)
        except httpx.HTTPError:
            continue
        if status == 200:
            balance = token_balance(_json_object(content_type, body))
            if balance:
                break

    spent = None
    try:
        status, content_type, body = await _request("/v1/dashboard/billing/usage", key)
        if status == 200:
            spent = spent_usd(_json_object(content_type, body))
    except httpx.HTTPError:
        spent = None

    if balance:
        granted = balance["granted"]
        available = balance["available"]
        remaining = None
        if not balance["unlimited"] and granted > 0:
            remaining = round(max(0, available) / granted * 1000) / 10
        payload = {
            "readable": True,
            "reason": None,
            "unlimited": balance["unlimited"],
            "available": available,
            "granted": granted,
            "used": balance["used"],
            "remaining_percent": remaining,
            "spent_usd": spent,
            "fetched_at": fetched_at,
        }
    else:
        payload = {
            "readable": False,
            "reason": "unavailable",
            "unlimited": False,
            "available": None,
            "granted": None,
            "used": None,
            "remaining_percent": None,
            "spent_usd": spent,
            "fetched_at": fetched_at,
        }
    _quota_cache.update(at=now, payload=payload)
    return payload
