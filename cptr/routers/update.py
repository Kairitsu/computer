"""In-app update API (sidebar → 检查更新). Admin only.

GET compares the install's git checkout with its upstream branch, POST starts
the update, and /status reports its progress until the server restarts.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from cptr.routers.admin import require_admin
from cptr.utils import updater

router = APIRouter(prefix="/api/update", tags=["update"])


@router.get("")
async def check_update(
    request: Request,
    refresh: bool = Query(False, description="Fetch again instead of using the cached check"),
):
    require_admin(request)
    return await updater.check(refresh=refresh)


@router.post("")
async def start_update(request: Request):
    require_admin(request)
    try:
        return updater.start_update()
    except updater.UpdateError as error:
        raise HTTPException(409, error.code) from error


@router.get("/status")
async def update_status(request: Request):
    require_admin(request)
    return updater.status()
