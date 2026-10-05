"""Admin router for cptr. All endpoints require admin role."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from cptr.models import User, Auth, Config
from cptr.utils.config import AuthResult, check_access, hash_password, now_ms
from cptr.utils.agents.detection import get_agent_status, invalidate_agent_detection_cache
from cptr.utils.agents.models import save_agent_profiles

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


# ── Users ────────────────────────────────────────────────────


@router.get("/users")
async def list_users(request: Request):
    """List all users with their roles."""
    require_admin(request)
    return {"users": await User.list_all()}


@router.post("/users")
async def create_user(request: Request, body: CreateUserRequest):
    """Create a new user (admin only)."""
    require_admin(request)

    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if not body.password or len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)
    if body.role not in ("admin", "user", "pending"):
        return JSONResponse({"error": "role must be admin, user, or pending"}, 400)

    username = body.username.strip()
    if await Auth.username_exists(username):
        return JSONResponse({"error": "username taken"}, 409)

    user_id = await User.create(
        username=username,
        password_hash=hash_password(body.password.strip()),
        role=body.role,
        created_at=now_ms(),
    )
    return {"ok": True, "user_id": user_id}


@router.delete("/users/{user_id}")
async def delete_user(request: Request, user_id: str):
    """Delete a user. Cannot delete yourself."""
    auth = require_admin(request)
    if auth.user_id == user_id:
        return JSONResponse({"error": "cannot delete yourself"}, 400)
    await User.delete_user(user_id)
    return {"ok": True}


@router.put("/users/{user_id}/role")
async def update_role(request: Request, user_id: str, body: RoleRequest):
    """Update a user's role."""
    require_admin(request)
    if body.role not in ("admin", "user", "pending"):
        return JSONResponse({"error": "role must be admin, user, or pending"}, 400)
    if not await User.update_role(user_id, body.role):
        return JSONResponse({"error": "user not found"}, 404)
    return {"ok": True}


@router.put("/users/{user_id}/profile")
async def update_user_profile(request: Request, user_id: str, body: UpdateUserProfileRequest):
    """Update a user's display name (admin only)."""
    require_admin(request)
    await User.update_display_name(user_id, body.display_name)
    return {"ok": True, "display_name": body.display_name}


@router.put("/users/{user_id}/password")
async def reset_user_password(request: Request, user_id: str, body: ResetPasswordRequest):
    """Reset a user's password (admin only)."""
    require_admin(request)
    if not body.password or len(body.password.strip()) < 6:
        return JSONResponse({"error": "min 6 characters"}, 400)
    if not await Auth.update_password(user_id, hash_password(body.password.strip())):
        return JSONResponse({"error": "user not found"}, 404)
    return {"ok": True}


@router.put("/users/{user_id}/username")
async def update_username(request: Request, user_id: str, body: UpdateUsernameRequest):
    """Update a user's username (admin only)."""
    require_admin(request)
    if not body.username or not body.username.strip():
        return JSONResponse({"error": "username required"}, 400)
    if not await Auth.update_username(user_id, body.username.strip()):
        return JSONResponse({"error": "username taken or user not found"}, 400)
    return {"ok": True}


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


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class RoleRequest(BaseModel):
    role: str


class UpdateUserProfileRequest(BaseModel):
    display_name: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    password: str


class UpdateUsernameRequest(BaseModel):
    username: str


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
