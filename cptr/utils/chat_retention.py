"""Delete chats with everything tied to them, and expire chats past the history limit.

A chat leaves data in several places besides its DB rows: the exported chat file, the
attachments staged for the agent, uploaded files, the agent processes kept between
turns, and Grok's own copy of the conversation under ``~/.grok/sessions``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import Text, cast, exists, select

from cptr.models import Chat, ChatMessage, Config
from cptr.models.chats import is_internal_chat
from cptr.models.files import File
from cptr.utils.agents.attachments import _safe_segment
from cptr.utils.chat_export import chat_directory
from cptr.utils.config import now_ms
from cptr.utils.db import get_db
from cptr.utils.runtime import FileError, Runtime

log = logging.getLogger(__name__)

CONFIG_KEY_RETENTION_DAYS = "chats.retention_days"
DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 36500
DAY_MS = 24 * 60 * 60 * 1000
FIRST_SWEEP_DELAY_SECONDS = 60
SWEEP_INTERVAL_SECONDS = 60 * 60

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_sweep_lock = asyncio.Lock()


def parse_retention_days(value: Any) -> int | None:
    """A valid day count (0 keeps history forever), or None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_RETENTION_DAYS else None


async def retention_days() -> int:
    days = parse_retention_days(await Config.get(CONFIG_KEY_RETENTION_DAYS))
    return DEFAULT_RETENTION_DAYS if days is None else days


async def expired_chats(days: int) -> list[Chat]:
    """Chats with no activity in the last ``days`` days, oldest first.

    Activity is the chat's last update or its newest message, so a chat whose
    timestamp was lost still counts as recent while it has recent messages.
    """
    if days <= 0:
        return []
    from cptr.utils.chat_task import get_active_chat_ids

    cutoff = now_ms() - days * DAY_MS
    recent_message = exists().where(
        ChatMessage.chat_id == Chat.id, ChatMessage.created_at >= cutoff
    )
    async with await get_db() as db:
        result = await db.execute(
            select(Chat).where(Chat.updated_at < cutoff, ~recent_message).order_by(Chat.updated_at)
        )
        chats = list(result.scalars().all())
    active = get_active_chat_ids()
    return [chat for chat in chats if not is_internal_chat(chat.meta) and chat.id not in active]


async def _meta_mentions(value: str, exclude_chat_ids: set[str]) -> bool:
    """Whether any other chat (or its messages) still refers to an id in its meta."""
    pattern = f"%{value}%"
    async with await get_db() as db:
        chat_hit = await db.execute(
            select(Chat.id)
            .where(cast(Chat.meta, Text).like(pattern), Chat.id.not_in(exclude_chat_ids))
            .limit(1)
        )
        if chat_hit.first():
            return True
        message_hit = await db.execute(
            select(ChatMessage.id)
            .where(
                cast(ChatMessage.meta, Text).like(pattern),
                ChatMessage.chat_id.not_in(exclude_chat_ids),
            )
            .limit(1)
        )
        return message_hit.first() is not None


def _grok_session_ids(chat: Chat, messages: list[ChatMessage]) -> set[str]:
    from cptr.utils.agents.grok import grok_session_ids

    ids = set(grok_session_ids(chat))
    for message in messages:
        turn = (message.meta or {}).get("agent_turn")
        if isinstance(turn, dict) and isinstance(turn.get("session_id"), str):
            ids.add(turn["session_id"])
    return {session_id for session_id in ids if _SESSION_ID_RE.match(session_id)}


def _upload_ids(messages: list[ChatMessage]) -> set[str]:
    ids: set[str] = set()
    for message in messages:
        files = (message.meta or {}).get("files")
        for item in files if isinstance(files, list) else []:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
                ids.add(item["id"])
    return ids


async def _grok_homes(request) -> list[Path]:
    """Where Grok keeps sessions for cptr's processes (see run_grok_agent's env)."""
    from cptr.utils.agents.models import get_raw_agent_profiles, normalize_agent_profiles
    from cptr.utils.identity import identity_for_request

    homes: list[Path] = []
    if os.environ.get("GROK_HOME"):
        homes.append(Path(os.environ["GROK_HOME"]).expanduser())
    try:
        identity = await identity_for_request(request)
        homes.append(Path(identity.home) / ".grok")
    except Exception:
        homes.append(Path.home() / ".grok")
    try:
        profiles = normalize_agent_profiles(await get_raw_agent_profiles())
    except Exception:
        profiles = []
    for profile in profiles:
        if profile.get("agent") == "grok" and profile.get("home"):
            homes.append(Path(str(profile["home"])).expanduser() / ".grok")
    return list(dict.fromkeys(homes))


def _grok_session_dirs(homes: list[Path], session_id: str) -> list[Path]:
    """The session's folders, plus those of the subagents it started."""
    found: list[Path] = []
    pending = [session_id]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen or not _SESSION_ID_RE.match(current):
            continue
        seen.add(current)
        for home in homes:
            for session_dir in (home / "sessions").glob(f"*/{current}"):
                if not session_dir.is_dir():
                    continue
                found.append(session_dir)
                for meta_file in (session_dir / "subagents").glob("*/meta.json"):
                    try:
                        child = json.loads(meta_file.read_text()).get("child_session_id")
                    except (OSError, ValueError, AttributeError):
                        continue
                    if isinstance(child, str):
                        pending.append(child)
    return found


async def _delete_path(request, path: Path) -> None:
    try:
        await Runtime.delete_item(request, str(path))
    except FileError:
        pass


async def purge_chat(request, chat: Chat) -> None:
    """Delete a chat and everything tied to it.

    Covers its internal child chats, their messages (with tool calls and reasoning),
    exported chat files, staged attachments, uploads no other chat uses, kept agent
    processes, and Grok session folders no other chat uses.
    """
    from cptr.socket.main import emit_to_user
    from cptr.utils.agents.grok import close_grok_session, grok_session_ids
    from cptr.utils.chat_task import get_active_chat_ids
    from cptr.utils.storage import get_storage

    owners = [chat, *await Chat.get_internal_descendants(chat.id)]
    owner_ids = {owner.id for owner in owners}
    messages: list[ChatMessage] = []
    session_ids: set[str] = set()
    for owner in owners:
        owner_messages = await ChatMessage.get_all_by_chat(owner.id)
        messages.extend(owner_messages)
        session_ids |= _grok_session_ids(owner, owner_messages)
        # Stop the Grok processes these chats kept between turns.
        for session_id in grok_session_ids(owner):
            await close_grok_session(session_id)
    upload_ids = _upload_ids(messages)

    # Files first: the chat list re-imports chat files that have no DB row.
    for owner in owners:
        workspace = (owner.meta or {}).get("workspace")
        chat_file = chat_directory(workspace) / f"{owner.id}.json"
        if workspace:
            await _delete_path(request, chat_file)
        else:
            await asyncio.to_thread(chat_file.unlink, True)  # missing_ok=True
        attachments = (
            Path(workspace or str(Path.home())) / ".cptr" / "attachments" / _safe_segment(owner.id)
        )
        await _delete_path(request, attachments)

    await Chat.delete(chat.id)

    for file_id in upload_ids:
        if await _meta_mentions(file_id, owner_ids):
            continue
        record = await File.get_by_id(file_id)
        if record is None:
            continue
        await get_storage().delete(record.id)
        await File.delete_by_id(record.id)

    if session_ids:
        homes = await _grok_homes(request)
        for session_id in session_ids:
            if await _meta_mentions(session_id, owner_ids):
                continue
            for session_dir in _grok_session_dirs(homes, session_id):
                await _delete_path(request, session_dir)

    workspace = (chat.meta or {}).get("workspace") or ""
    unread_counts = await Chat.unread_counts_by_workspace(
        chat.user_id, [workspace], get_active_chat_ids()
    )
    await emit_to_user(
        chat.user_id,
        {
            "chat_id": chat.id,
            "workspace": workspace,
            "workspace_unread_count": unread_counts.get(workspace, 0),
        },
    )


async def sweep_expired_chats(app) -> int:
    """Delete every chat past the history limit. Returns how many were deleted."""
    from cptr.utils.identity import internal_request_for_user

    async with _sweep_lock:
        days = await retention_days()
        chats = await expired_chats(days)
        requests: dict[str, Any] = {}
        deleted = 0
        for chat in chats:
            try:
                if chat.user_id not in requests:
                    requests[chat.user_id] = await internal_request_for_user(app, chat.user_id)
                await purge_chat(requests[chat.user_id], chat)
                deleted += 1
            except Exception:
                log.exception("[retention] failed to delete chat %s", chat.id)
        if deleted:
            log.info("[retention] deleted %d chats idle for over %d days", deleted, days)
        return deleted


async def retention_loop(app) -> None:
    """Expire old chats shortly after startup, then every hour."""
    await asyncio.sleep(FIRST_SWEEP_DELAY_SECONDS)
    while True:
        try:
            await sweep_expired_chats(app)
        except Exception:
            log.exception("[retention] sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
