"""Admin router for cptr. All endpoints require admin role."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional

from cptr.models import Config
from cptr.utils.config import AuthResult, check_access
from cptr.utils.agents.detection import get_agent_status, invalidate_agent_detection_cache
from cptr.utils.agents.models import save_agent_profiles
from cptr.utils.chat_retention import (
    CONFIG_KEY_RETENTION_DAYS,
    MAX_RETENTION_DAYS,
    expired_chats,
    parse_retention_days,
    retention_days,
    sweep_expired_chats,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

COOKIE_NAME = "cptr_session"


def require_admin(request: Request) -> AuthResult:
    """Extract auth from cookie, raise 403 if not admin."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth


# ── Config ───────────────────────────────────────────────────


@router.get("/config")
async def get_all_config(request: Request):
    """Get all instance config."""
    require_admin(request)
    return {"config": await Config.get_all()}


@router.get("/config/{namespace}")
async def get_config_namespace(request: Request, namespace: str):
    """Get config keys for a namespace (e.g. 'auth' → all 'auth.*' keys)."""
    require_admin(request)
    return {"config": await Config.get_namespace(namespace)}


@router.put("/config")
async def put_config(request: Request, body: ConfigUpdateRequest):
    """Update config keys. Upserts each key."""
    require_admin(request)
    await Config.upsert(body.config)
    return {"ok": True}


# ── Chat history retention ──────────────────────────────────


class ChatRetentionRequest(BaseModel):
    days: int


_retention_sweeps: set[asyncio.Task] = set()


@router.get("/chat-retention")
async def get_chat_retention(request: Request):
    """Days of inactivity after which a chat is deleted; 0 keeps chats forever."""
    require_admin(request)
    return {"days": await retention_days(), "max_days": MAX_RETENTION_DAYS}


@router.get("/chat-retention/preview")
async def preview_chat_retention(
    request: Request, days: int = Query(..., ge=0, le=MAX_RETENTION_DAYS)
):
    """How many chats a limit of ``days`` would delete right now."""
    require_admin(request)
    return {"count": len(await expired_chats(days))}


@router.put("/chat-retention")
async def put_chat_retention(request: Request, body: ChatRetentionRequest):
    """Save the limit and delete the chats it puts out of range."""
    require_admin(request)
    days = parse_retention_days(body.days)
    if days is None:
        raise HTTPException(422, f"days must be between 0 and {MAX_RETENTION_DAYS}")
    await Config.upsert({CONFIG_KEY_RETENTION_DAYS: days})
    task = asyncio.create_task(sweep_expired_chats(request.app))
    _retention_sweeps.add(task)
    task.add_done_callback(_retention_sweeps.discard)
    return {"days": days, "max_days": MAX_RETENTION_DAYS}


# ── Agents ──────────────────────────────────────────────────


class AgentsUpdateRequest(BaseModel):
    profiles: list[dict]


@router.get("/agents")
async def get_agents(request: Request):
    """Get configured agent profiles plus live detection status."""
    require_admin(request)
    return await get_agent_status(request.app.state)


@router.put("/agents")
async def update_agents(request: Request, body: AgentsUpdateRequest):
    """Replace configured agent profiles."""
    require_admin(request)
    await save_agent_profiles(body.profiles)
    invalidate_agent_detection_cache(request.app.state)
    return await get_agent_status(request.app.state, refresh=True)


@router.post("/agents/refresh")
async def refresh_agents(request: Request):
    """Refresh agent detection status."""
    require_admin(request)
    invalidate_agent_detection_cache(request.app.state)
    return await get_agent_status(request.app.state, refresh=True)


# ── Request Models ───────────────────────────────────────────


class ConfigUpdateRequest(BaseModel):
    config: dict


# ── Model config ─────────────────────────────────────────────

CONFIG_KEY_CHAT_MODELS = "chat.models"


@router.get("/models/config")
async def get_model_config(request: Request):
    """Get per-model config and full model list (including inactive) for the admin Models tab."""
    require_admin(request)
    return await _build_model_config(request)


async def _build_model_config(request: Request):
    """Build model configuration response for the admin Models tab."""
    config = await Config.get(CONFIG_KEY_CHAT_MODELS) or {}

    # Same list as chat.py get_models, but without filtering inactive models.
    from cptr.utils.agents.detection import get_available_agent_model_entries

    models = await get_available_agent_model_entries(request.app.state)
    return {"config": config, "models": models}


@router.post("/models/refresh")
async def refresh_model_list(request: Request):
    """Re-detect the agents' models and return the refreshed model list."""
    require_admin(request)
    invalidate_agent_detection_cache(request.app.state)
    return await _build_model_config(request)


class UpdateModelConfigRequest(BaseModel):
    is_active: Optional[bool] = None
    params: Optional[dict] = None


@router.put("/models/{model_id:path}/config")
async def update_model_config(request: Request, model_id: str, body: UpdateModelConfigRequest):
    """Update config for a specific model (or '*' for global defaults)."""
    require_admin(request)
    all_config = await Config.get(CONFIG_KEY_CHAT_MODELS) or {}

    entry = all_config.get(model_id, {})

    if body.is_active is not None:
        entry["is_active"] = body.is_active

    if body.params is not None:
        entry["params"] = body.params

    # Clean up empty entries
    if not entry or (entry.keys() <= {"is_active"} and entry.get("is_active") is not False):
        if not entry.get("params"):
            all_config.pop(model_id, None)
        else:
            all_config[model_id] = entry
    else:
        all_config[model_id] = entry

    await Config.upsert({CONFIG_KEY_CHAT_MODELS: all_config})
    return {"ok": True}
