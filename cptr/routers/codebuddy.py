"""CodeBuddy login and usage for Settings → Usage, and process limits for Settings → General.

Everyone can read the login and usage. The process limits cover every user's chats,
so changing them needs admin.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from cptr.utils.agents.codebuddy import (
    codebuddy_process_settings,
    save_codebuddy_process_settings,
)
from cptr.utils.codebuddy_account import get_codebuddy_quota
from cptr.utils.config import AuthResult, check_access

router = APIRouter(prefix="/api/codebuddy", tags=["codebuddy"])
COOKIE_NAME = "cptr_session"


def _get_auth(request: Request) -> AuthResult:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth


class ProcessSettingsRequest(BaseModel):
    max_idle_processes: int | None = Field(None, ge=0)
    idle_timeout_minutes: int | None = Field(None, ge=0)


@router.get("/account")
async def get_account(request: Request):
    """CodeBuddy login (without its token) and this user's CodeBuddy usage."""
    auth = _get_auth(request)
    return await get_codebuddy_quota(auth.user_id)


@router.get("/processes")
async def get_process_settings(request: Request):
    """How many idle CodeBuddy processes chats keep between turns, and for how long."""
    _get_auth(request)
    return await codebuddy_process_settings()


@router.put("/processes")
async def put_process_settings(request: Request, body: ProcessSettingsRequest):
    auth = _get_auth(request)
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return await save_codebuddy_process_settings(body.max_idle_processes, body.idle_timeout_minutes)
