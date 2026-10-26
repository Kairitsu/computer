"""AnyRouter: the Claude Code profile beside Grok.

AnyRouter (anyrouter.top) is a new-api relay that serves Claude Code requests.
The profile runs its own pinned Claude Code install, config directory and token,
so the machine's own ``claude`` and ``~/.claude`` are never used. Model ids come
from ``GET /v1/models``; nothing sends a model request to find out what works.
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

ANYROUTER_PROFILE_ID = "anyrouter"
# install.sh installs this version into CLAUDE_INSTALL_DIR (ensure_anyrouter_claude).
CLAUDE_CODE_VERSION = "2.1.310"
CLAUDE_INSTALL_DIR = Path.home() / ".local/share/claude-anyrouter"
ANYROUTER_CLAUDE = str(CLAUDE_INSTALL_DIR / "node_modules/.bin/claude")
CLAUDE_HOME = Path.home() / ".claude-anyrouter"
KEY_FILE = CLAUDE_HOME / "anyrouter.key"
CATALOG_FILE = CLAUDE_HOME / "models.json"
# CPTR_ANYROUTER_ORIGIN points tests at a stand-in API.
ANYROUTER_ORIGIN = (os.environ.get("CPTR_ANYROUTER_ORIGIN") or "https://anyrouter.top").rstrip("/")
# AnyRouter answers 400 to a request that does not turn on the 1M context beta.
ANTHROPIC_BETA = "context-1m-2025-08-07"
# Claude Code retries 503/429 ten times by default, which holds a turn for minutes.
MAX_RETRIES = 3

STANDARD_WINDOW = 200_000
LONG_WINDOW = 1_000_000
DEFAULT_REASONING_EFFORT = "high"
PREFERRED_MODELS = ("claude-opus-4-7", "claude-sonnet-4-5-20250929")
DEFAULT_MODEL = PREFERRED_MODELS[0]

_ALL_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# What Claude Code 2.1.310 does with each AnyRouter model, checked against a stand-in
# API: the --effort values it passes on (it lowers the others; an empty tuple means it
# sends no effort), and whether the model takes the 1M window. Ids it does not know,
# such as gpt-6-astra-cc-format, get every effort.
_TRAITS: dict[str, tuple[tuple[str, ...], bool]] = {
    "claude-opus-5-5": (_ALL_EFFORTS, True),
    "claude-opus-4-7": (_ALL_EFFORTS, True),
    "claude-opus-4-6": (("low", "medium", "high", "max"), True),
    "claude-opus-4-5-20251101": (("low", "medium", "high"), False),
    "claude-sonnet-4-5-20250929": ((), True),
    "claude-sonnet-4-20250514": ((), True),
    "claude-haiku-4-5-20251001": ((), False),
    "claude-3-7-sonnet-20250219": ((), False),
    "claude-3-5-sonnet-20241022": ((), False),
    "claude-3-5-haiku-20241022": ((), False),
}
# Claude Code sends these as claude-opus-5-5, so they would only repeat that model.
_REWRITTEN = {"claude-opus-4-20250514", "claude-opus-4-1-20250805"}

_SECRET = re.compile(r"sk-[A-Za-z0-9_\-]+")
_QUOTA_TTL_SECONDS = 60
_CATALOG_TTL_SECONDS = 10 * 60
_CATALOG_RETRY_SECONDS = 60
_quota_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_catalog_cache: dict[str, Any] = {"until": 0.0, "models": None}


def redact(text: str) -> str:
    return _SECRET.sub("sk-redacted", text)


def read_api_key() -> str:
    """Return the AnyRouter token from its private file, or an empty string."""
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_claude_home() -> None:
    CLAUDE_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CLAUDE_HOME, 0o700)


def claude_child_env(base: dict[str, str]) -> dict[str, str]:
    """Environment for Claude Code. Other providers' tokens are removed."""
    env = dict(base)
    for name in (
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_MODEL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_DISABLE_1M_CONTEXT",
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
    ):
        env.pop(name, None)
    ensure_claude_home()
    env["ANTHROPIC_BASE_URL"] = ANYROUTER_ORIGIN
    env["ANTHROPIC_BETAS"] = ANTHROPIC_BETA
    env["CLAUDE_CONFIG_DIR"] = str(CLAUDE_HOME)
    env["CLAUDE_CODE_MAX_RETRIES"] = str(MAX_RETRIES)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    key = read_api_key()
    if key:
        env["ANTHROPIC_AUTH_TOKEN"] = key
    return env


def usable_models(model_ids: list[str]) -> list[str]:
    """Catalog ids Claude Code can drive: Claude models and the *-cc-format variants.

    Known models come first, newest first, as in the trait table.
    """
    usable = [
        model_id
        for model_id in dict.fromkeys(model_ids)
        if (model_id.startswith("claude-") or model_id.endswith("-cc-format"))
        and model_id not in _REWRITTEN
    ]
    known = list(_TRAITS)
    return sorted(
        usable, key=lambda model_id: known.index(model_id) if model_id in known else len(known)
    )


def _traits(model_id: str) -> tuple[tuple[str, ...], bool]:
    return _TRAITS.get(model_id, (_ALL_EFFORTS, True))


def model_options(model_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Efforts and context windows the composer offers for each model."""
    options: dict[str, dict[str, Any]] = {}
    for model_id in model_ids:
        efforts, long_window = _traits(model_id)
        options[model_id] = {
            # Highest first, like the efforts Grok lists.
            "reasoning_efforts": [
                {"value": value, "label": value, "description": ""} for value in reversed(efforts)
            ],
            "default_reasoning_effort": DEFAULT_REASONING_EFFORT if efforts else None,
            "context_windows": [STANDARD_WINDOW, LONG_WINDOW] if long_window else [STANDARD_WINDOW],
            "default_context_window": STANDARD_WINDOW,
        }
    return options


def turn_reasoning_effort(model_id: str, chat_params: dict[str, Any] | None) -> str | None:
    """--effort for this turn: the chat's choice when the model takes it, else the default."""
    efforts = _traits(model_id)[0]
    if not efforts:
        return None
    chosen = chat_params.get("reasoning_effort") if isinstance(chat_params, dict) else None
    return chosen if chosen in efforts else DEFAULT_REASONING_EFFORT


def turn_context_window(model_id: str, chat_params: dict[str, Any] | None) -> int:
    from cptr.utils.context import chat_context_window

    chosen = chat_context_window(chat_params)
    long_window = _traits(model_id)[1]
    return LONG_WINDOW if chosen == LONG_WINDOW and long_window else STANDARD_WINDOW


def preferred_default(model_ids: list[str]) -> str:
    for model_id in PREFERRED_MODELS:
        if model_id in model_ids:
            return model_id
    return next(
        (model_id for model_id in model_ids if model_id.startswith("claude-")), model_ids[0]
    )


async def _request(path: str, key: str) -> tuple[int, str, bytes]:
    async with httpx.AsyncClient(timeout=5) as client:
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


def _load_catalog() -> list[str]:
    try:
        payload = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return []
    return usable_models([model for model in models if isinstance(model, str)])


def _save_catalog(model_ids: list[str]) -> None:
    ensure_claude_home()
    temporary = CATALOG_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"models": model_ids, "fetched_at": int(time.time())}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(CATALOG_FILE)


async def catalog_models() -> list[str]:
    """Models for the composer, from /v1/models.

    The list is kept ten minutes. When the site cannot be reached, the last saved
    list (or the known ids) stands in, and the site is asked again a minute later.
    """
    now = time.time()
    cached = _catalog_cache.get("models")
    if isinstance(cached, list) and now < float(_catalog_cache.get("until") or 0):
        return cached
    try:
        models = usable_models(await fetch_model_ids())
        _save_catalog(models)
        until = now + _CATALOG_TTL_SECONDS
    except (httpx.HTTPError, OSError, RuntimeError):
        models = _load_catalog() or usable_models(list(_TRAITS))
        until = now + _CATALOG_RETRY_SECONDS
    _catalog_cache.update(until=until, models=models)
    return models


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
