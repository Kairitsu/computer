"""The chat task list.

It was fed by cptr's built-in tool loop, which is gone; chat views still read it.
"""

from __future__ import annotations

import json
from typing import Any


VALID_TASK_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
MAX_TASK_ITEMS = 256
MAX_TASK_CONTENT_CHARS = 4000
_TASK_TRUNCATION_MARKER = "... [truncated]"


# ── Chat task list ──────────────────────────────────────────


def _normalize_tasks(tasks: Any, existing_tasks: Any = None, merge: bool = False) -> list[dict]:
    if isinstance(tasks, str):
        try:
            tasks = json.loads(tasks)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(tasks, list):
        return []

    existing = _normalize_tasks(existing_tasks) if merge else []
    by_id: dict[str, dict] = {task["id"]: task for task in existing}
    order: list[str] = [task["id"] for task in existing]
    next_index = len(order)
    for item in tasks:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id", "") or "").strip()
        current = by_id.get(task_id) if merge and task_id else None
        content_value = item.get("content")
        content = str(content_value).strip() if content_value is not None else ""
        if len(content) > MAX_TASK_CONTENT_CHARS:
            keep = MAX_TASK_CONTENT_CHARS - len(_TASK_TRUNCATION_MARKER)
            content = content[:keep] + _TASK_TRUNCATION_MARKER
        if current and not content:
            content = current["content"]
        if not content:
            continue
        status = str(item.get("status", current.get("status") if current else "pending")).lower()
        if status not in VALID_TASK_STATUSES:
            status = "pending"
        task_id = task_id or str(next_index + 1)
        if task_id in by_id and task_id in order:
            order.remove(task_id)
        else:
            next_index += 1
        by_id[task_id] = {"id": task_id, "content": content, "status": status}
        order.append(task_id)
    return [by_id[task_id] for task_id in order][:MAX_TASK_ITEMS]


async def clear_active_tasks(
    chat_id: str, user_id: str | None = None, message_id: str | None = None
) -> None:
    from cptr.models import Chat
    from cptr.socket.main import emit_to_user
    from cptr.utils.config import now_ms

    chat = await Chat.get_by_id(chat_id)
    if not chat:
        return
    meta = dict(chat.meta or {})
    tasks = _normalize_tasks(meta.get("tasks"))
    if not any(task["status"] in {"pending", "in_progress"} for task in tasks):
        return
    meta["tasks"] = []
    await Chat.update_meta(chat_id, meta, now_ms())
    if user_id:
        await emit_to_user(
            user_id,
            {
                "type": "chat:tasks",
                "chat_id": chat_id,
                "message_id": message_id,
                "tasks": [],
                "summary": {
                    "total": 0,
                    "pending": 0,
                    "in_progress": 0,
                    "completed": 0,
                    "cancelled": 0,
                },
            },
        )
