"""Chat task runner: drives a coding agent (Grok CLI) for one assistant message.

Runs as an asyncio.Task. Streams deltas via Socket.IO, persists to DB.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from cptr.events import EVENTS, publish_event
from cptr.env import CHAT_TOOL_COMMAND_MAX_CHARS, CHAT_TOOL_MAX_CHARS
from cptr.utils.context import build_context_usage, chat_context_window
from cptr.models import (
    Chat,
    ChatMessage,
)
from cptr.socket.main import emit_to_user
from cptr.utils.config import now_ms
from cptr.utils.tools import clear_active_tasks
from cptr.utils.chat_export import export_chat_to_file
from cptr.utils.prompt_templates import load_system_prompt as _load_system_prompt
from cptr.utils.agents.events import (
    AgentAskUser,
    AgentContextUsage,
    AgentDone,
    AgentError,
    AgentPermissionRequest,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolOutputDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.attachments import prepare_agent_attachments
from cptr.utils.model_targets import AgentModelTarget
from cptr.utils.identity import identity_for_context

logger = logging.getLogger(__name__)

ASK_USER_NAME = "ask_user"
DEFAULT_AUTO_RESOLUTION_MS = 120_000


def _collect_ask_user_answers(
    questions: list[dict[str, Any]], answers: dict[str, str] | None
) -> dict[str, Any]:
    """Pair answers with question ids; no answers means the recommended (first) options."""
    result: dict[str, Any] = {}
    for question in questions:
        answer = question["options"][0]["label"] if answers is None else answers.get(question["id"])
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"missing answer for {question['id']}")
        result[question["id"]] = {"answers": [answer.strip()]}
    if answers is not None and set(answers) != set(result):
        raise ValueError("answers must match the requested question ids")
    return {"answers": result}


PLAN_MODE_PROMPT = (
    "[Plan Mode] Research with read-only tools before planning. When a material decision "
    "cannot be discovered, use ask_user with one to three questions, two to three options "
    "each, and the recommended option first. Ask only after research and never alongside "
    "another tool call. Then present a concise plan in your response. Wait for an explicit "
    "approval message before using write tools or implementing."
)


# ── Task registry ───────────────────────────────────────────

_tasks: dict[str, asyncio.Task] = {}  # message_id → asyncio.Task
_task_state: dict[str, dict] = {}  # message_id → {content, output}
_task_chat: dict[str, str] = {}  # message_id → chat_id
_pending_input_locks: dict[str, asyncio.Lock] = {}  # chat_id → Lock
# (message_id, call_id) → (answer future, request) for agent turns blocked on the user
_agent_ask_waiters: dict[
    tuple[str, str], tuple[asyncio.Future, AgentAskUser | AgentPermissionRequest]
] = {}


def get_pending_input_lock(chat_id: str) -> asyncio.Lock:
    return _pending_input_locks.setdefault(chat_id, asyncio.Lock())


def start_task(
    request,
    *,
    message_id: str,
    chat_id: str,
    user_id: str,
    workspace: str,
    target: AgentModelTarget,
    regeneration_prompt: str | None = None,
):
    """Launch the agent turn as a background asyncio.Task."""
    task = asyncio.create_task(
        run_chat_task(
            request,
            message_id=message_id,
            chat_id=chat_id,
            user_id=user_id,
            target=target,
            workspace=workspace,
            regeneration_prompt=regeneration_prompt,
        )
    )
    _tasks[message_id] = task
    _task_chat[message_id] = chat_id

    async def emit_active():
        unread_counts = await Chat.unread_counts_by_workspace(
            user_id, [workspace], get_active_chat_ids()
        )
        await emit_to_user(
            user_id,
            {
                "type": "chat:active",
                "chat_id": chat_id,
                "workspace": workspace,
                "active": True,
                "workspace_unread_count": unread_counts.get(workspace, 0),
            },
        )

    asyncio.create_task(emit_active())


async def cancel_task(message_id: str) -> bool:
    """Cancel a running task. Returns True if found."""
    task = _tasks.get(message_id)
    if task:
        task.cancel()
        return True
    return False


def is_running(message_id: str) -> bool:
    """Check if a task is currently running."""
    task = _tasks.get(message_id)
    return task is not None and not task.done()


def get_live_state(message_id: str) -> dict | None:
    """Get live in-memory state for a running task."""
    return _task_state.get(message_id)


def resolve_agent_ask_user(
    message_id: str, call_id: str, answers: dict[str, str] | None, timed_out: bool
) -> bool:
    """Hand answers to a running agent turn blocked on this question.

    Returns False when no running turn is waiting on it.
    """
    entry = _agent_ask_waiters.get((message_id, call_id))
    if entry is None or entry[0].done() or not isinstance(entry[1], AgentAskUser):
        return False
    waiter, ask = entry
    if timed_out and not ask.auto_resolve:
        raise ValueError("this question needs an answer from the user")
    waiter.set_result(_agent_ask_user_result(ask, None if timed_out else answers))
    return True


def resolve_agent_permission(message_id: str, call_id: str, approved: bool) -> bool:
    """Hand an allow/deny to a running agent turn blocked on this tool call.

    Returns False when no running turn is waiting on it.
    """
    entry = _agent_ask_waiters.get((message_id, call_id))
    if entry is None or entry[0].done() or not isinstance(entry[1], AgentPermissionRequest):
        return False
    entry[0].set_result(approved)
    return True


def _agent_ask_user_result(ask: AgentAskUser, answers: dict[str, str] | None) -> dict[str, Any]:
    result = _collect_ask_user_answers(ask.questions, answers)
    if answers is None:
        result["timed_out"] = True
    return result


async def _wait_for_agent_answer(
    waiter: asyncio.Future, ask: AgentAskUser, user_id: str, chat_id: str
) -> dict[str, Any]:
    """Wait for the user; like ask_user, fall back to the recommended options after the
    chat has been out of view for the auto-resolution window."""
    if not ask.auto_resolve:
        return await waiter
    from cptr.socket.main import is_chat_visible

    remaining = DEFAULT_AUTO_RESOLUTION_MS / 1000
    while remaining > 0:
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout=1)
        except asyncio.TimeoutError:
            if not is_chat_visible(user_id, chat_id):
                remaining -= 1
    return _agent_ask_user_result(ask, None)


def get_active_chat_ids() -> set[str]:
    """Return the set of chat_ids that currently have a running task."""
    return {cid for mid, cid in _task_chat.items() if mid in _tasks and not _tasks[mid].done()}


# Chats whose tab was closed during a turn; their agent processes close once it ends.
_released_chats: set[str] = set()


async def _close_chat_agents(chat: Chat | None) -> None:
    from cptr.utils.agents.grok import close_grok_session, grok_session_ids

    for session_id in grok_session_ids(chat):
        await close_grok_session(session_id, running=False)


async def release_chat_agents(chat_id: str) -> None:
    """Stop the agent processes a chat keeps between turns, as its tab was closed.

    A running turn finishes first, and its process then closes instead of being kept.
    The next turn starts a new process that reloads the agent's saved session.
    """
    chat = await Chat.get_by_id(chat_id)
    if chat is None:
        return
    if chat.id in get_active_chat_ids():
        _released_chats.add(chat.id)
    else:
        _released_chats.discard(chat.id)
        await _close_chat_agents(chat)


async def _close_released_chat_agents(chat_id: str, user_id: str) -> None:
    """Close the processes of a chat whose tab was closed during the turn that just ended."""
    if chat_id not in _released_chats or chat_id in get_active_chat_ids():
        return
    _released_chats.discard(chat_id)
    from cptr.socket.main import is_chat_visible

    # Reopened while the turn ran: keep the process for the next turn.
    if is_chat_visible(user_id, chat_id):
        return
    await _close_chat_agents(await Chat.get_by_id(chat_id))


# ── Pending input processing ────────────────────────────────


def _is_pending_chat_input(message: ChatMessage) -> bool:
    return bool((message.meta or {}).get("queued"))


def _merge_pending_input_meta(messages: list[ChatMessage]) -> dict | None:
    files: list[dict] = []
    for message in messages:
        meta = message.meta or {}
        message_files = meta.get("files")
        if isinstance(message_files, list):
            files.extend(message_files)

    return {"files": files} if files else None


def _pending_input_ready(message: ChatMessage, msg_map: dict[str, ChatMessage]) -> bool:
    parent = msg_map.get(message.parent_id) if message.parent_id else None
    return not (parent and parent.role == "assistant" and not parent.done)


def _first_ready_pending_input_batch(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Return the first ready pending batch on a single branch."""
    msg_map = {m.id: m for m in messages}
    pending_inputs = [m for m in messages if m.role == "user" and _is_pending_chat_input(m)]
    if not pending_inputs:
        return []

    first = next((m for m in pending_inputs if _pending_input_ready(m, msg_map)), None)
    if not first:
        return []

    batch = []
    for message in pending_inputs:
        if message is first:
            batch.append(message)
            continue
        if not batch:
            continue
        if message.parent_id != first.parent_id:
            break
        if message.model != first.model:
            break
        if not _pending_input_ready(message, msg_map):
            break
        batch.append(message)
    return batch


async def process_pending_chat_inputs(request, chat_id: str, user_id: str, workspace: str):
    """Start the next task from user-queued prompts.

    Uses a per-chat lock to prevent concurrent processing from
    both the task's finally block and the API double-check.
    """
    lock = get_pending_input_lock(chat_id)
    async with lock:
        while True:
            all_msgs = await ChatMessage.get_all_by_chat(chat_id)

            input_batch = _first_ready_pending_input_batch(all_msgs)
            if not input_batch:
                return

            combined_content = "\n\n".join(m.content for m in input_batch if m.content)
            combined_meta = _merge_pending_input_meta(input_batch)

            chat = await Chat.get_by_id(chat_id)
            if not chat:
                return

            parent_id = input_batch[0].parent_id

            for m in input_batch:
                await ChatMessage.delete(m.id)

            combined_msg = await ChatMessage.create(
                chat_id=chat_id,
                role="user",
                content=combined_content,
                parent_id=parent_id,
                meta=combined_meta,
                created_at=now_ms(),
            )

            # Resolve model from the queued input, then the chat's last used model.
            model_id = input_batch[0].model or (chat.meta or {}).get("last_model", "")
            if not model_id:
                # Fall back to the model from the last assistant message
                done_assistants = [m for m in all_msgs if m.role == "assistant" and m.done]
                last_asst = done_assistants[-1] if done_assistants else None
                model_id = (last_asst.model if last_asst else "") or ""
            if not model_id:
                logger.error(
                    "[chat-input] No model found for chat %s, cannot process pending input",
                    chat_id,
                )
                return

            # Resolve model target
            try:
                from cptr.utils.model_targets import resolve_model_target

                target = await resolve_model_target(model_id)
            except Exception:
                logger.exception("[chat-input] Failed to resolve model target for %s", model_id)
                return

            # Create assistant placeholder
            assistant_msg = await ChatMessage.create(
                chat_id=chat_id,
                role="assistant",
                content="",
                parent_id=combined_msg.id,
                model=model_id,
                done=False,
                created_at=now_ms(),
            )
            await Chat.update_current_message(chat_id, assistant_msg.id, now_ms())

            # Notify frontend that pending inputs became transcript messages.
            await emit_to_user(
                user_id,
                {
                    "chat_id": chat_id,
                    "message_id": assistant_msg.id,
                    "pending_inputs_processed": True,
                },
            )

            # Start new task and continue draining other ready branch batches.
            start_task(
                request,
                message_id=assistant_msg.id,
                chat_id=chat_id,
                user_id=user_id,
                workspace=workspace,
                target=target,
            )
            logger.info(
                "[chat-input] Processed %d pending input message(s) for chat %s",
                len(input_batch),
                chat_id[:8],
            )


async def reconcile_chat_state():
    """Recover from server crash: fix stuck messages and resume pending inputs.

    Called once on startup when ENABLE_CHAT_RECONCILE_ON_STARTUP=true (default).
    Finds:
      1. Assistant messages with done=False that have no running task → mark done
      2. Chats with pending user prompts → process them
    """
    from sqlalchemy import select, and_
    from cptr.utils.db import get_db

    async with await get_db() as db:
        result = await db.execute(
            select(ChatMessage).where(
                and_(
                    ChatMessage.role == "assistant",
                    ChatMessage.done == False,  # noqa: E712
                )
            )
        )
        stuck = list(result.scalars().all())

    healed_chats: set[str] = set()
    for msg in stuck:
        if not is_running(msg.id):
            logger.warning("[reconcile] Marking stuck message %s as done", msg.id)
            meta = dict(msg.meta or {})
            meta["error"] = "interrupted by server restart"
            await ChatMessage.update(msg.id, done=True, meta=meta)
            healed_chats.add(msg.chat_id)

    # Resume pending inputs for healed chats.
    for cid in healed_chats:
        chat = await Chat.get_by_id(cid)
        if chat:
            workspace = (chat.meta or {}).get("workspace", "")
            try:
                from cptr.utils.identity import internal_request_for_user

                request = await internal_request_for_user(None, chat.user_id)
                await process_pending_chat_inputs(request, cid, chat.user_id, workspace)
            except Exception:
                logger.exception("[reconcile] Failed to process pending inputs for chat %s", cid)

    if healed_chats:
        logger.info("[reconcile] Recovered %d chat(s) on startup", len(healed_chats))


# ── Title generation ────────────────────────────────────────


# ── Message history ─────────────────────────────────────────


def _output_items_to_messages(
    output_items: list[dict], message_id: str | None = None
) -> list[dict]:
    """Convert ordered persisted output items into model-visible messages."""
    native_agent_call_ids = {
        item["call_id"]
        for item in output_items
        if item.get("type") == "function_call"
        and item.get("call_id")
        and _is_native_agent_tool_item(item)
    }
    output_call_ids = {
        item["call_id"]
        for item in output_items
        if item.get("type") == "function_call_output"
        and item.get("call_id") not in native_agent_call_ids
    }
    messages: list[dict] = []
    pending_content: list[str] = []
    pending_reasoning_items: list[dict] = []
    pending_tool_calls: list[dict] = []
    call_names: dict[str, str] = {}

    def flush_pending() -> None:
        nonlocal pending_content, pending_reasoning_items, pending_tool_calls
        if not pending_content and not pending_reasoning_items and not pending_tool_calls:
            return
        assistant_msg: dict = {
            "role": "assistant",
            "content": "".join(pending_content),
        }
        if message_id:
            assistant_msg["id"] = message_id
        if pending_tool_calls:
            assistant_msg["tool_calls"] = pending_tool_calls
        if pending_reasoning_items:
            assistant_msg["reasoning_items"] = pending_reasoning_items
        messages.append(assistant_msg)
        pending_content = []
        pending_reasoning_items = []
        pending_tool_calls = []

    for item in output_items:
        itype = item.get("type")
        if itype == "message":
            text = "".join(
                block.get("text") or ""
                for block in item.get("content") or []
                if isinstance(block, dict) and block.get("type") in ("text", "output_text")
            )
            if text:
                pending_content.append(text)
        elif itype == "reasoning":
            if (
                item.get("status") not in (None, "completed")
                or str(item.get("id", "")).startswith("reasoning-")
                or (
                    not item.get("encrypted_content")
                    and not item.get("reasoning_details")
                    and _reasoning_text_len(item) <= 0
                )
            ):
                continue
            pending_reasoning_items.append(item)
        elif itype == "function_call" and item.get("status") in {"completed", "rejected"}:
            call_id = item.get("call_id")
            if not call_id or call_id in native_agent_call_ids:
                continue
            if call_id not in output_call_ids:
                logger.warning(
                    "[history] Skipping orphaned function_call %s (%s) — no matching output",
                    call_id,
                    item.get("name", "?"),
                )
                continue
            arguments = item.get("arguments", {})
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments)
            tc = {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": arguments,
                },
            }
            if item.get("fc_id"):
                tc["fc_id"] = item["fc_id"]
            call_names[call_id] = item.get("name", "")
            pending_tool_calls.append(tc)
        elif itype == "function_call_output" and item.get("call_id") not in native_agent_call_ids:
            call_id = item.get("call_id")
            if call_id not in call_names:
                continue
            flush_pending()
            tool_msg = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": _tool_result_for_model(
                    call_names.get(call_id, ""),
                    item.get("output", ""),
                ),
            }
            if message_id:
                tool_msg["id"] = message_id
            messages.append(tool_msg)

    flush_pending()
    return messages


async def _load_message_history(chat_id: str, message_id: str) -> tuple[list[dict], str | None]:
    """Load the ancestor chain from message_id to root as LLM messages.

    Walks up via parent_id so only the active branch is included.
    The current message (message_id) is always included even if done=False,
    since it may contain completed tool calls from prior approval rounds.

    If any message in the chain has a chat_summary, everything before it
    is skipped and the summary is returned separately for the system prompt.

    Returns (messages, chat_summary_or_None).
    """
    all_msgs = await ChatMessage.get_all_by_chat(chat_id)
    msg_map = {m.id: m for m in all_msgs}

    # Trace from message_id up to root
    chain: list = []
    cur = msg_map.get(message_id)
    while cur:
        chain.append(cur)
        cur = msg_map.get(cur.parent_id) if cur.parent_id else None
    chain.reverse()  # root → leaf

    # Find the most recent message with a chat_summary
    existing_summary = None
    for i, m in enumerate(chain):
        if m.chat_summary:
            chain = chain[i:]  # keep this message and everything after
            existing_summary = m.chat_summary
            break

    result = []
    for m in chain:
        # Skip in-progress assistant placeholders, but NOT the current
        # message being continued, which may have accumulated tool call
        # results from prior approval rounds that the LLM needs to see.
        if m.role == "assistant" and not m.done and m.id != message_id:
            continue
        # For the current message, skip if it has no content and no output
        # (truly empty placeholder on first run)
        if m.id == message_id and not m.done and not m.content and not m.output:
            continue
        entry: dict = {"id": m.id, "role": m.role, "content": m.content or ""}

        # Transform uploaded images into base64 multimodal blocks; inline text files
        if m.role == "user":
            attached_files = (m.meta or {}).get("files", [])
            images = [
                f
                for f in attached_files
                if isinstance(f, dict)
                and (f.get("type") == "image" or (f.get("content_type") or "").startswith("image/"))
            ]
            non_images = [f for f in attached_files if isinstance(f, dict) and f not in images]

            if images or non_images:
                from cptr.utils.storage import get_storage
                import base64

                text_content = entry["content"]

                # Append file:// references so the AI can read them with read_file
                if non_images:
                    from cptr.utils.storage import UPLOADS_DIR

                    file_refs = []
                    for f in non_images:
                        file_id = f.get("id")
                        if not file_id:
                            continue
                        name = f.get("name", "file")
                        file_path = UPLOADS_DIR / file_id
                        file_refs.append(f"[{name}](file://{file_path})")
                    if file_refs:
                        text_content += "\n\nAttached files:\n" + "\n".join(file_refs)

                content_blocks = [{"type": "text", "text": text_content}] if text_content else []

                for img in images:
                    file_id = img.get("id")
                    if not file_id:
                        continue
                    data = await get_storage().get(file_id)
                    if data:
                        b64_str = base64.b64encode(data).decode("utf-8")
                        ctype = img.get("content_type") or "image/png"
                        content_blocks.append(
                            {"type": "image", "media_type": ctype, "base64": b64_str}
                        )

                if len(content_blocks) > (1 if text_content else 0):
                    entry["content"] = content_blocks
                elif text_content != entry["content"]:
                    entry["content"] = text_content

        # Reconstruct model-visible messages from the ordered output stream.
        # `m.output` is the source of truth: assistant text/reasoning,
        # function_call, function_call_output, then possibly more text.
        if m.output:
            output_messages = _output_items_to_messages(m.output, m.id)
            if output_messages:
                result.extend(output_messages)
                continue

        result.append(entry)

    # ── Final sanitization: ensure every tool_call has a matching tool result ──
    # This catches edge cases from compaction, DB corruption, or partial persistence.
    result = _sanitize_tool_pairs(result)

    return result, existing_summary


def _sanitize_tool_pairs(messages: list[dict]) -> list[dict]:
    """Ensure every tool_call in an assistant message has a matching tool result.

    Walks the message list and collects all tool result call_ids.  Then
    strips any tool_call entries from assistant messages that have no
    matching result.  Also removes orphaned tool-result messages.

    This is the last line of defence against 400 errors from providers
    that require strict tool_call ↔ tool_result pairing (OpenAI).
    """
    # Collect all tool-result call_ids in the conversation
    tool_result_ids = {
        m["tool_call_id"] for m in messages if m.get("role") == "tool" and m.get("tool_call_id")
    }

    # Collect all tool_call ids declared by assistant messages
    tool_call_ids = set()
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                tool_call_ids.add(tc.get("id", ""))

    sanitized = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # Keep only tool_calls that have a matching tool result
            kept = [tc for tc in m["tool_calls"] if tc.get("id") in tool_result_ids]
            dropped = len(m["tool_calls"]) - len(kept)
            if dropped:
                logger.warning(
                    "[sanitize] Dropped %d orphaned tool_call(s) from assistant message",
                    dropped,
                )
            if kept:
                m = dict(m)
                m["tool_calls"] = kept
                sanitized.append(m)
            else:
                # No tool_calls left — keep as plain text if there's content
                m = dict(m)
                del m["tool_calls"]
                m.pop("reasoning_items", None)
                if m.get("content"):
                    sanitized.append(m)
                # else: drop empty assistant message entirely
        elif m.get("role") == "tool":
            # Drop tool results that have no matching tool_call
            if m.get("tool_call_id") not in tool_call_ids:
                logger.warning(
                    "[sanitize] Dropped orphaned tool result for call_id=%s",
                    m.get("tool_call_id", "?"),
                )
                continue
            sanitized.append(m)
        else:
            sanitized.append(m)

    return sanitized


def _tool_result_for_model(tool_name: str, result: str) -> str:
    """Return the tool result text to send back to the model."""
    if tool_name != "image_generate":
        return result

    try:
        meta = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return result

    images = meta.get("images")
    if not isinstance(images, list):
        return result

    image_files = [
        {
            "path": image.get("path"),
            "name": image.get("name"),
            "content_type": image.get("content_type"),
        }
        for image in images
        if isinstance(image, dict) and image.get("path")
    ]

    return json.dumps(
        {
            "status": meta.get("status", "success"),
            "displayed_to_user": False,
            "must_call": "display_file",
            "instruction": (
                "Do not answer the user yet. The image file is saved but not rendered. "
                "Call display_file once for each path below."
            ),
            "display_file_calls": [
                {
                    "name": "display_file",
                    "arguments": {"path": image["path"]},
                }
                for image in image_files
            ],
            "images": image_files,
        }
    )


def _scrub_incomplete_items(output_items: list[dict]) -> None:
    """Mark any in-progress function_call items as 'failed'.

    Called before persisting output_items after an error or cancellation.
    Without this, "in_progress" items stay in the DB.  While
    _load_message_history skips non-"completed" items today, marking
    them explicitly prevents any future code from misinterpreting them.
    """
    for item in output_items:
        if item.get("type") == "function_call" and item.get("status") in (
            "in_progress",
            "pending",
            "queued",
        ):
            item["status"] = "failed"


def _reasoning_text_len(item: dict) -> int:
    """Return displayable reasoning text length for diagnostics."""
    total = 0
    for blocks in (item.get("summary"), item.get("content")):
        if isinstance(blocks, str):
            total += len(blocks)
        elif isinstance(blocks, list):
            total += sum(
                len(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict)
                and block.get("type") in ("text", "output_text", "summary_text")
            )
    return total


def _output_debug_stats(output_items: list[dict] | None) -> tuple[dict[str, int], int, int]:
    """Return output type counts plus reasoning count/text length."""
    counts: dict[str, int] = {}
    reasoning_count = 0
    reasoning_chars = 0
    for item in output_items or []:
        itype = item.get("type", "?")
        counts[itype] = counts.get(itype, 0) + 1
        if itype == "reasoning":
            reasoning_count += 1
            reasoning_chars += _reasoning_text_len(item)
    return counts, reasoning_count, reasoning_chars


def _output_item_key(item: dict) -> tuple | None:
    """Stable key for Responses-style output item updates."""
    if item.get("id"):
        return ("id", item["id"])
    if item.get("type") and item.get("call_id"):
        return ("call", item["type"], item["call_id"])
    return None


def _upsert_output_item(items: list[dict], item: dict) -> bool:
    """Insert or replace a Responses-style output item by id/call_id."""
    key = _output_item_key(item)
    if key is not None:
        for idx, existing in enumerate(items):
            if _output_item_key(existing) == key:
                items[idx] = item
                return False
    items.append(item)
    return True


def _safe_tool_name(name: str | None) -> str:
    if not name:
        return "agent_tool"
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_")
    return value or "agent_tool"


def _is_native_agent_tool_item(item: dict) -> bool:
    return bool(
        item.get("native_agent")
        or (isinstance(item.get("id"), str) and item["id"].startswith("agent-"))
    )


def _append_capped_output(existing: str, delta: str, max_chars: int) -> str:
    text = f"{existing or ''}{delta}"
    if len(text) <= max_chars:
        return text
    marker = "\n\n...(truncated native agent output)...\n\n"
    if max_chars <= len(marker) + 2:
        return text[:max_chars]
    keep = max_chars - len(marker)
    head = keep // 2
    tail = keep - head
    return f"{text[:head]}{marker}{text[-tail:]}"


def _append_prompt_suffix(messages: list[dict], suffix: str) -> None:
    if not suffix:
        return
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    block["text"] = f"{block.get('text') or ''}{suffix}"
                    return
            content.append({"type": "text", "text": suffix.strip()})
        else:
            message["content"] = f"{content or ''}{suffix}"
        return


# ── Connection resolution ───────────────────────────────────


# ── The agentic loop ────────────────────────────────────────


async def run_chat_task(
    request,
    *,
    message_id: str,
    chat_id: str,
    user_id: str,
    target: AgentModelTarget,
    workspace: str,
    regeneration_prompt: str | None = None,
):
    """Run one assistant turn through the selected coding agent."""
    if request is None:
        try:
            from cptr.app import app as cptr_app
            from cptr.utils.identity import internal_request_for_user

            request = await internal_request_for_user(cptr_app, user_id)
        except Exception:
            logger.debug(
                "[task %s] internal request creation failed", message_id[:8], exc_info=True
            )

    async def emit(**data):
        """Stream an output delta to the user."""
        try:
            await emit_to_user(user_id, {"chat_id": chat_id, "message_id": message_id, **data})
        except Exception:
            logger.debug("[task %s] emit_to_user failed", message_id[:8], exc_info=True)

    async def _emit_done():
        """Emit done=True enriched with chat title and content preview."""
        try:
            chat_obj = await Chat.get_by_id(chat_id)
            title = chat_obj.title if chat_obj else "Chat"
        except Exception:
            title = "Chat"
        preview = content[:300] if content else ""
        ws_name = workspace.rstrip("/").rsplit("/", 1)[-1] if workspace else ""
        try:
            await clear_active_tasks(chat_id, user_id, message_id)
        except Exception:
            logger.debug("[task %s] clear_active_tasks failed", message_id[:8], exc_info=True)
        await emit(
            done=True,
            title=title,
            content=preview,
            workspace=workspace,
            workspace_name=ws_name,
        )

    event_workspace = (
        {"id": workspace, "name": workspace.rstrip("/").rsplit("/", 1)[-1]} if workspace else None
    )

    # Load existing state so continuations don't overwrite previous output
    msg = await ChatMessage.get_by_id(message_id)
    content = (msg.content or "") if msg else ""
    output_items: list[dict] = list(msg.output or []) if msg else []
    text_buffer = ""  # Accumulates text between tool calls

    logger.info(
        "[task %s] start: existing content=%d chars, output=%d items",
        message_id[:8],
        len(content),
        len(output_items),
    )

    def _flush_text() -> dict | None:
        """Flush accumulated text into a message output item."""
        nonlocal text_buffer
        if not text_buffer:
            return None
        if not text_buffer.strip():
            text_buffer = ""
            return None
        logger.info(
            "[task %s] flush_text: %d chars into message item", message_id[:8], len(text_buffer)
        )
        item = {
            "type": "message",
            "id": str(uuid.uuid4()),
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text_buffer}],
        }
        output_items.append(item)
        text_buffer = ""
        _sync_state()
        return item

    def _sync_state():
        """Update in-memory state so API can serve it on refresh."""
        _task_state[message_id] = {"content": content, "output": output_items}

    async def _save_message(save_reason: str, **kwargs) -> bool:
        """Persist a message update and log enough detail to debug skipped saves."""
        saved_output = kwargs.get("output", output_items)
        saved_content = kwargs.get("content", content)
        counts, reasoning_count, reasoning_chars = _output_debug_stats(saved_output)
        logger.info(
            "[task %s] db save (%s) begin: done=%s content=%d chars output=%d items reasoning=%d items/%d chars types=%s",
            message_id[:8],
            save_reason,
            kwargs.get("done", "<unchanged>"),
            len(saved_content or ""),
            len(saved_output or []),
            reasoning_count,
            reasoning_chars,
            counts,
        )
        saved = await ChatMessage.update(message_id, **kwargs)
        log = logger.info if saved else logger.warning
        log("[task %s] db save (%s) result: updated=%s", message_id[:8], save_reason, saved)
        return saved

    async def save_session(agent_target: AgentModelTarget, resume_state: dict | None):
        if not resume_state:
            return
        chat = await Chat.get_by_id(chat_id)
        if not chat:
            return
        meta = dict(chat.meta or {})
        sessions = dict(meta.get("agent_sessions") or {})
        sessions[agent_target.profile_id] = {
            **resume_state,
            "profile_id": agent_target.profile_id,
            "agent": agent_target.agent,
            "model": agent_target.model,
            "workspace": workspace,
            "updated_at": now_ms(),
        }
        meta["agent_sessions"] = sessions
        await Chat.update_meta(chat_id, meta, now_ms())

    async def _run_agent_target(agent_target: AgentModelTarget):
        nonlocal content, text_buffer
        from cptr.utils.agents.claude_code import run_claude_code_agent
        from cptr.utils.agents.cline import run_cline_agent
        from cptr.utils.agents.codex import run_codex_agent
        from cptr.utils.agents.cursor import run_cursor_agent
        from cptr.utils.agents.gemini import run_gemini_agent
        from cptr.utils.agents.grok import run_grok_agent
        from cptr.utils.agents.opencode import run_opencode_agent
        from cptr.utils.agents.pi import run_pi_agent

        chat_obj = await Chat.get_by_id(chat_id)
        identity = await identity_for_context({"request": request, "user_id": user_id})
        chat_params = (chat_obj.meta or {}).get("params", {}) if chat_obj else {}
        agent_workspace = workspace or str(Path.home())
        messages, loaded_summary = await _load_message_history(chat_id, message_id)
        system = await _load_system_prompt(
            request, agent_workspace, agent_target.full_model_id, user_id=user_id
        )
        if loaded_summary:
            system += f"\n\n[CONVERSATION SUMMARY]\n{loaded_summary}"
        current_user_files = []
        if msg and msg.parent_id:
            parent_msg = await ChatMessage.get_by_id(msg.parent_id)
            meta_files = (parent_msg.meta or {}).get("files") if parent_msg else None
            if isinstance(meta_files, list):
                current_user_files = meta_files
        agent_attachments = await prepare_agent_attachments(
            request,
            workspace=agent_workspace,
            user_id=user_id,
            chat_id=chat_id,
            message_id=(msg.parent_id if msg and msg.parent_id else message_id),
            files=current_user_files,
        )
        _append_prompt_suffix(messages, agent_attachments.prompt_suffix)
        if regeneration_prompt:
            messages.append({"role": "user", "content": regeneration_prompt})
        if chat_params.get("plan_mode", False):
            # A resumed agent session only receives the latest user message, so append the
            # instruction to it rather than adding a message that would replace it.
            _append_prompt_suffix(messages, f"\n\n{PLAN_MODE_PROMPT}")

        resume_state = None
        if chat_obj:
            sessions = (chat_obj.meta or {}).get("agent_sessions") or {}
            if isinstance(sessions, dict):
                candidate = sessions.get(agent_target.profile_id)
                if isinstance(candidate, dict):
                    resume_state = candidate

        runners = {
            "codex": run_codex_agent,
            "claude_code": run_claude_code_agent,
            "cursor": run_cursor_agent,
            "grok": run_grok_agent,
            "opencode": run_opencode_agent,
            "cline": run_cline_agent,
            "gemini": run_gemini_agent,
            "pi": run_pi_agent,
        }
        runner = runners.get(agent_target.agent)
        if runner is None:
            raise RuntimeError(f"Unsupported agent type: {agent_target.agent}")

        def _active_reasoning_item():
            return next(
                (
                    item
                    for item in reversed(output_items)
                    if item.get("type") == "reasoning" and item.get("status") == "in_progress"
                ),
                None,
            )

        async def _finish_reasoning_item():
            existing = _active_reasoning_item()
            if not existing:
                return
            item = {**existing, "status": "completed"}
            _upsert_output_item(output_items, item)
            await emit(output=item)
            _sync_state()

        async def _ask_agent_user(ask: AgentAskUser) -> dict[str, str]:
            """Show the agent's question in the ask_user card and wait for the answers."""
            # The agent usually announced the question as a tool call; turn that into the card.
            existing = next(
                (
                    item
                    for item in output_items
                    if item.get("type") == "function_call" and item.get("call_id") == ask.call_id
                ),
                {},
            )
            arguments: dict[str, Any] = {"questions": ask.questions}
            call_item = {
                "type": "function_call",
                "id": existing.get("id") or f"agent-{ask.call_id}",
                "call_id": ask.call_id,
                "name": ASK_USER_NAME,
                "native_agent": True,
                "arguments": arguments,
                "status": "pending",
            }
            if ask.auto_resolve:
                arguments["autoResolutionMs"] = DEFAULT_AUTO_RESOLUTION_MS
                call_item["expires_at"] = now_ms() + DEFAULT_AUTO_RESOLUTION_MS
            key = (message_id, ask.call_id)
            waiter = asyncio.get_running_loop().create_future()
            _agent_ask_waiters[key] = (waiter, ask)
            try:
                _upsert_output_item(output_items, call_item)
                _sync_state()
                await _save_message(
                    "agent ask_user", content=content, output=output_items, done=False
                )
                await emit(output=call_item)
                result = await _wait_for_agent_answer(waiter, ask, user_id, chat_id)
            finally:
                _agent_ask_waiters.pop(key, None)
            call_item["status"] = "completed"
            call_item["timed_out"] = bool(result.get("timed_out"))
            result_item = {
                "type": "function_call_output",
                "call_id": ask.call_id,
                "native_agent": True,
                "output": json.dumps(result),
            }
            _upsert_output_item(output_items, result_item)
            await emit(output=call_item)
            await emit(output=result_item)
            _sync_state()
            await _save_message(
                "agent ask_user answered", content=content, output=output_items, done=False
            )
            return {qid: entry["answers"][0] for qid, entry in result["answers"].items()}

        async def _ask_agent_permission(request: AgentPermissionRequest) -> bool:
            """Show Allow/Deny on the agent's tool call and wait for the user's choice."""
            existing = next(
                (
                    item
                    for item in output_items
                    if item.get("type") == "function_call"
                    and item.get("call_id") == request.call_id
                ),
                {},
            )
            call_item = {
                **existing,
                "type": "function_call",
                "id": existing.get("id") or f"agent-{request.call_id}",
                "call_id": request.call_id,
                "name": existing.get("name") or _safe_tool_name(request.name),
                "native_agent": True,
                "arguments": existing.get("arguments") or request.arguments,
                "status": "pending",
            }
            key = (message_id, request.call_id)
            waiter = asyncio.get_running_loop().create_future()
            _agent_ask_waiters[key] = (waiter, request)
            try:
                _upsert_output_item(output_items, call_item)
                _sync_state()
                await _save_message(
                    "agent permission", content=content, output=output_items, done=False
                )
                await emit(output=call_item)
                approved = bool(await waiter)
            finally:
                _agent_ask_waiters.pop(key, None)
            call_item["approved"] = approved
            call_item["status"] = "in_progress" if approved else "rejected"
            _upsert_output_item(output_items, call_item)
            await emit(output=call_item)
            _sync_state()
            await _save_message(
                "agent permission answered", content=content, output=output_items, done=False
            )
            return approved

        agent_events = runner(
            profile=agent_target.config,
            model=agent_target.model,
            workspace=agent_workspace,
            messages=messages,
            system_prompt=system,
            chat_params=chat_params,
            resume_state=resume_state,
            attachments=agent_attachments,
            identity=identity,
        )
        async for event in agent_events:
            if isinstance(event, AgentTextDelta):
                await _finish_reasoning_item()
                content += event.text
                text_buffer += event.text
                await emit(delta=event.text)
                _sync_state()
            elif isinstance(event, AgentReasoningDelta):
                existing = _active_reasoning_item()
                if existing:
                    blocks = existing.get("content") or []
                    text = next(
                        (
                            block.get("text", "")
                            for block in blocks
                            if isinstance(block, dict) and block.get("type") == "reasoning_text"
                        ),
                        "",
                    )
                    item = {
                        **existing,
                        "content": [{"type": "reasoning_text", "text": f"{text}{event.text}"}],
                    }
                else:
                    section = sum(item.get("type") == "reasoning" for item in output_items) + 1
                    item = {
                        "type": "reasoning",
                        "id": f"reasoning-{message_id}-{section}",
                        "status": "in_progress",
                        "content": [{"type": "reasoning_text", "text": event.text}],
                    }
                _upsert_output_item(output_items, item)
                await emit(output=item)
                _sync_state()
            elif isinstance(event, AgentToolUpdate):
                await _finish_reasoning_item()
                flushed_item = _flush_text()
                if flushed_item:
                    await emit(output=flushed_item)
                existing = next(
                    (
                        item
                        for item in output_items
                        if item.get("type") == "function_call"
                        and item.get("call_id") == event.call_id
                    ),
                    {},
                )
                if existing.get("name") == ASK_USER_NAME:
                    # The question card owns this call; the agent's own updates would clobber it.
                    continue
                call_item = {
                    "type": "function_call",
                    "id": existing.get("id") or f"agent-{event.call_id}",
                    "call_id": event.call_id,
                    "name": _safe_tool_name(event.name or existing.get("name")),
                    "native_agent": True,
                    "arguments": {
                        **(existing.get("arguments") or {}),
                        **(event.arguments or {}),
                    },
                    "status": event.status,
                }
                _upsert_output_item(output_items, call_item)
                await emit(output=call_item)
                if event.output is not None:
                    capped_event_output = None
                    output_limit = (
                        CHAT_TOOL_COMMAND_MAX_CHARS
                        if call_item["name"] == "run_command"
                        else CHAT_TOOL_MAX_CHARS
                    )
                    capped_event_output = _append_capped_output("", event.output, output_limit)
                    existing_output = next(
                        (
                            item
                            for item in output_items
                            if item.get("type") == "function_call_output"
                            and item.get("call_id") == event.call_id
                        ),
                        None,
                    )
                    if (
                        existing_output
                        and existing_output.get("output")
                        and capped_event_output is not None
                    ):
                        _sync_state()
                        continue
                    output_item = {
                        "type": "function_call_output",
                        "call_id": event.call_id,
                        "native_agent": True,
                        "output": capped_event_output or "",
                    }
                    _upsert_output_item(output_items, output_item)
                    await emit(output=output_item)
                _sync_state()
            elif isinstance(event, AgentToolOutputDelta):
                await _finish_reasoning_item()
                flushed_item = _flush_text()
                if flushed_item:
                    await emit(output=flushed_item)
                existing_call = next(
                    (
                        item
                        for item in output_items
                        if item.get("type") == "function_call"
                        and item.get("call_id") == event.call_id
                    ),
                    None,
                )
                if existing_call is None:
                    existing_call = {
                        "type": "function_call",
                        "id": f"agent-{event.call_id}",
                        "call_id": event.call_id,
                        "name": "run_command"
                        if event.stream_kind == "command_output"
                        else "agent_tool",
                        "native_agent": True,
                        "arguments": {},
                        "status": "in_progress",
                    }
                    _upsert_output_item(output_items, existing_call)
                    await emit(output=existing_call)
                existing_output = next(
                    (
                        item
                        for item in output_items
                        if item.get("type") == "function_call_output"
                        and item.get("call_id") == event.call_id
                    ),
                    {},
                )
                max_chars = (
                    CHAT_TOOL_COMMAND_MAX_CHARS
                    if event.stream_kind == "command_output"
                    else CHAT_TOOL_MAX_CHARS
                )
                output_item = {
                    "type": "function_call_output",
                    "call_id": event.call_id,
                    "native_agent": True,
                    "output": _append_capped_output(
                        "" if event.replace else str(existing_output.get("output") or ""),
                        event.delta,
                        max_chars,
                    ),
                }
                _upsert_output_item(output_items, output_item)
                await emit(output=output_item)
                _sync_state()
            elif isinstance(event, AgentAskUser):
                await _finish_reasoning_item()
                flushed_item = _flush_text()
                if flushed_item:
                    await emit(output=flushed_item)
                try:
                    event.answers = await _ask_agent_user(event)
                except asyncio.CancelledError:
                    # Stopped while waiting: shut the agent down now rather than at GC.
                    await agent_events.aclose()
                    raise
            elif isinstance(event, AgentPermissionRequest):
                await _finish_reasoning_item()
                flushed_item = _flush_text()
                if flushed_item:
                    await emit(output=flushed_item)
                try:
                    event.approved = await _ask_agent_permission(event)
                except asyncio.CancelledError:
                    await agent_events.aclose()
                    raise
            elif isinstance(event, AgentContextUsage):
                await emit(
                    context_usage=build_context_usage(
                        event.tokens,
                        threshold=event.window or chat_context_window(chat_params),
                    )
                )
            elif isinstance(event, AgentError):
                raise RuntimeError(event.message)
            elif isinstance(event, AgentDone):
                await _finish_reasoning_item()
                flushed_item = _flush_text()
                if flushed_item:
                    await emit(output=flushed_item)
                await save_session(agent_target, event.resume_state)
                await _save_message(
                    "agent done",
                    content=content,
                    output=output_items,
                    usage=event.usage,
                    done=True,
                )
                _task_state.pop(message_id, None)
                await _emit_done()
                preview = content[:300] if content else ""
                await publish_event(
                    EVENTS.CHAT_FINISHED,
                    actor={"id": user_id},
                    subject_id=chat_id,
                    subject_type="chat",
                    source="chat_task",
                    data={"workspace": event_workspace, "preview": preview},
                    message=preview,
                )
                return

        flushed_item = _flush_text()
        if flushed_item:
            await emit(output=flushed_item)
        await _finish_reasoning_item()
        await _save_message("agent stream ended", content=content, output=output_items, done=True)
        _task_state.pop(message_id, None)
        await _emit_done()
        preview = content[:300] if content else ""
        await publish_event(
            EVENTS.CHAT_FINISHED,
            actor={"id": user_id},
            subject_id=chat_id,
            subject_type="chat",
            source="chat_task",
            data={"workspace": event_workspace, "preview": preview},
            message=preview,
        )
        return

    try:
        await _run_agent_target(target)

    except asyncio.CancelledError:
        _flush_text()
        _scrub_incomplete_items(output_items)
        await _save_message("cancelled", content=content, output=output_items, done=True)
        _task_state.pop(message_id, None)
        await _emit_done()
    except Exception as e:
        logger.exception(f"Chat task error for message {message_id}")
        _flush_text()
        error_msg = str(e)
        # Try to extract API error body for more detail
        if hasattr(e, "response"):
            try:
                body = e.response.text or ""
                if body:
                    import json as _json

                    err_data = _json.loads(body)
                    api_msg = err_data.get("error", {}).get("message", "")
                    if api_msg:
                        error_msg = api_msg
            except Exception:
                pass
        # Append error to content so it's visible in the chat
        error_block = f"\n\n> **Error:** {error_msg}"
        content += error_block
        text_buffer += error_block
        flushed_item = _flush_text()
        if flushed_item:
            await emit(output=flushed_item)
        _scrub_incomplete_items(output_items)
        await _save_message(
            "error",
            content=content,
            output=output_items,
            done=True,
            meta={"error": error_msg},
        )
        _task_state.pop(message_id, None)
        await emit(done=True, error=error_msg)
        await publish_event(
            EVENTS.CHAT_FAILED,
            actor={"id": user_id},
            subject_id=chat_id,
            subject_type="chat",
            source="chat_task",
            data={"workspace": event_workspace, "preview": error_msg[:300] if error_msg else ""},
            message=error_msg[:300] if error_msg else "",
        )
    finally:
        _tasks.pop(message_id, None)
        _task_state.pop(message_id, None)
        _task_chat.pop(message_id, None)
        if chat_id not in get_active_chat_ids():
            try:
                await Chat.touch(chat_id, now_ms())
                chat = await Chat.get_by_id(chat_id)
                unread_counts = await Chat.unread_counts_by_workspace(
                    user_id, [workspace], get_active_chat_ids()
                )
                await emit_to_user(
                    user_id,
                    {
                        "type": "chat:active",
                        "chat_id": chat_id,
                        "workspace": workspace,
                        "active": False,
                        "updated_at": chat.updated_at if chat else None,
                        "workspace_unread_count": unread_counts.get(workspace, 0),
                    },
                )
            except Exception:
                logger.debug("[task %s] active-state emit failed", message_id[:8], exc_info=True)
        try:
            await _close_released_chat_agents(chat_id, user_id)
        except Exception:
            logger.debug("[task %s] closing released agents failed", message_id[:8], exc_info=True)
        try:
            await export_chat_to_file(request, chat_id)
        except Exception:
            logger.exception(f"Failed to export chat {chat_id}")
        # Process any pending user prompts.
        try:
            await process_pending_chat_inputs(request, chat_id, user_id, workspace)
        except Exception:
            logger.exception(f"Failed to process pending inputs for chat {chat_id}")
