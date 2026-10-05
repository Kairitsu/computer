"""Grok CLI account API for Settings → Usage.

Everyone can read the login and SuperGrok quota. Signing in or out and switching
accounts change the server's Grok CLI login for every user, so they need admin.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.utils import grok_account
from cptr.utils.config import AuthResult, check_access
from cptr.utils.supergrok import get_supergrok_quota, grok_profile_homes

router = APIRouter(prefix="/api/grok", tags=["grok"])
COOKIE_NAME = "cptr_session"


def _get_auth(request: Request) -> AuthResult:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth


def _require_admin(request: Request) -> AuthResult:
    auth = _get_auth(request)
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth


class LoginRequest(BaseModel):
    method: Literal["oauth", "device"] = "oauth"


class LoginCodeRequest(BaseModel):
    code: str


async def _call(coro):
    try:
        return await coro
    except grok_account.GrokAccountError as error:
        raise HTTPException(error.status, str(error)) from error


@router.get("/account")
async def get_account(
    request: Request,
    refresh: bool = Query(False, description="Bypass the SuperGrok quota cache"),
):
    """Grok CLI login, SuperGrok quota and saved accounts."""
    auth = _get_auth(request)
    status = await grok_account.account_status()
    can_manage = auth.role == "admin"
    return {
        **status,
        "accounts": status["accounts"] if can_manage else [],
        "login": status["login"] if can_manage else {"state": "idle"},
        "supergrok": await get_supergrok_quota(force=refresh),
        "can_manage": can_manage,
    }


@router.post("/login")
async def start_login(request: Request, body: LoginRequest):
    _require_admin(request)
    return await _call(grok_account.start_login(body.method))


@router.get("/login")
async def get_login(request: Request):
    _require_admin(request)
    return grok_account.login_status()


@router.post("/login/code")
async def submit_login_code(request: Request, body: LoginCodeRequest):
    _require_admin(request)
    if not body.code.strip():
        raise HTTPException(400, "code required")
    return await _call(grok_account.submit_login_code(body.code))


@router.post("/login/cancel")
async def cancel_login(request: Request):
    _require_admin(request)
    return grok_account.cancel_login()


@router.post("/logout")
async def logout(request: Request):
    _require_admin(request)
    await grok_account.logout()
    return {"ok": True}


@router.post("/accounts")
async def save_account(request: Request):
    """Save the signed-in login so it can be switched back to."""
    _require_admin(request)
    try:
        item = grok_account.save_current_account(await grok_profile_homes())
    except grok_account.GrokAccountError as error:
        raise HTTPException(error.status, str(error)) from error
    return {"id": item["id"]}


@router.post("/accounts/{account_id}/switch")
async def switch_account(request: Request, account_id: str):
    _require_admin(request)
    return {"profile": await _call(grok_account.switch_account(account_id))}


@router.delete("/accounts/{account_id}")
async def remove_account(request: Request, account_id: str):
    _require_admin(request)
    try:
        grok_account.remove_saved_account(account_id)
    except grok_account.GrokAccountError as error:
        raise HTTPException(error.status, str(error)) from error
    return {"ok": True}
