"""Command sessions and the chat task list.

Both were fed by cptr's built-in tool loop, which is gone; the terminal router and
chat views still read them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


try:
    import fcntl
    import pty
    import signal
    import struct
    import subprocess
    import termios

    _PTY_AVAILABLE = True
except ImportError:
    import signal
    import subprocess

    _PTY_AVAILABLE = False  # Windows


# ── Command session state ───────────────────────────────────

command_sessions: dict[str, dict] = {}
# command_session_id → {
#   "master_fd": int | None,   PTY mode (Unix) — read/write through this fd
#   "proc": Popen | Process,   The child process handle
#   "output": bytearray,       In-memory ring buffer (256KB cap)
#   "command": str,
#   "done": bool,
#   "exit_code": int | None,
#   "log_path": str,
# }
MAX_COMMAND_SESSIONS = 5
_MAX_LOG_SIZE = 50 * 1024 * 1024  # 50MB — rotate when exceeded

VALID_TASK_STATUSES = {"pending", "in_progress", "completed", "cancelled"}
MAX_TASK_ITEMS = 256
MAX_TASK_CONTENT_CHARS = 4000
_TASK_TRUNCATION_MARKER = "... [truncated]"


def _spawn_pty(command: str, cwd: str, env: dict, preexec_fn=None) -> tuple:
    """Spawn a command under a PTY (Unix only). Returns (proc, master_fd)."""
    master_fd, slave_fd = pty.openpty()
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        proc = subprocess.Popen(
            command,
            shell=True,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            cwd=cwd,
            env=env,
            start_new_session=True,
            preexec_fn=preexec_fn,
        )
    except Exception:
        os.close(slave_fd)
        os.close(master_fd)
        raise
    os.close(slave_fd)
    return proc, master_fd


def _kill_process_group(pid: int, force: bool = False) -> None:
    """Send signal to the child's entire process group.

    SIGTERM for graceful shutdown (default), SIGKILL for force.
    Falls back to signalling just the leader if the group is gone.
    """
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _rotate_log(log_path: str, log_file) -> tuple:
    """Keep the newest half of the log file. Returns new (file, bytes_written)."""
    log_file.flush()
    log_file.close()

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    keep = lines[len(lines) // 2 :]

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "log_rotated", "ts": time.time()}) + "\n")
        for line in keep:
            f.write(line)

    new_file = open(log_path, "a", encoding="utf-8")
    new_size = sum(len(line.encode("utf-8", errors="replace")) for line in keep)
    return new_file, new_size


async def stream_command_session_output(command_session_id: str):
    """Read output from a command process into memory + JSONL log."""
    session = command_sessions.get(command_session_id)
    if not session:
        return

    master_fd = session.get("master_fd")
    proc = session["proc"]
    log_path = session.get("log_path")
    log_file = None
    log_bytes = 0
    loop = asyncio.get_event_loop()

    try:
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8")
            entry = (
                json.dumps(
                    {
                        "type": "start",
                        "command": session["command"],
                        "pid": proc.pid,
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
            log_file.write(entry)
            log_file.flush()
            log_bytes += len(entry.encode("utf-8", errors="replace"))

        while True:
            # Read from PTY fd (Unix) or subprocess pipe (Windows fallback)
            if master_fd is not None:
                try:
                    chunk = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    if not chunk:
                        break
                except OSError:
                    break  # EIO when child exits
            else:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break

            session = command_sessions.get(command_session_id)
            if session:
                session["output"].extend(chunk)
                session["total_bytes"] += len(chunk)
                if len(session["output"]) > 256 * 1024:
                    session["output"] = session["output"][-256 * 1024 :]
                async with session["condition"]:
                    session["condition"].notify_all()

            if log_file:
                entry = (
                    json.dumps(
                        {
                            "type": "output",
                            "data": chunk.decode(errors="replace"),
                            "ts": time.time(),
                        }
                    )
                    + "\n"
                )
                entry_size = len(entry.encode("utf-8", errors="replace"))
                if log_bytes + entry_size > _MAX_LOG_SIZE:
                    log_file, log_bytes = _rotate_log(log_path, log_file)
                log_file.write(entry)
                log_file.flush()
                log_bytes += entry_size
    except Exception:
        pass
    finally:
        # Wait for the process to finish and collect exit code
        session = command_sessions.get(command_session_id)
        if master_fd is not None:
            exit_code = await loop.run_in_executor(None, proc.wait)
            try:
                os.close(master_fd)
            except OSError:
                pass
        else:
            await proc.wait()
            exit_code = proc.returncode

        if session:
            session["done"] = True
            session["exit_code"] = exit_code
            session["master_fd"] = None  # fd is closed
            async with session["condition"]:
                session["condition"].notify_all()

        if log_file:
            log_file.write(
                json.dumps({"type": "end", "exit_code": exit_code, "ts": time.time()}) + "\n"
            )
            log_file.close()


# ── Helper ──────────────────────────────────────────────────


def _truncate_output(text: str, max_chars: int = 80_000) -> str:
    """Truncate long output, keeping head and tail."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return text[:half] + "\n\n... (truncated) ...\n\n" + text[-half:]


def _command_session_snapshot(command_session_id: str, session: dict) -> dict[str, Any]:
    return {
        "command_session_id": command_session_id,
        "task_id": command_session_id,  # legacy UI/tool wording
        "workspace": session.get("workspace", ""),
        "chat_id": session.get("chat_id"),
        "message_id": session.get("message_id"),
        "call_id": session.get("call_id"),
        "command": session.get("command", ""),
        "created_at": session.get("created_at", 0),
        "status": "completed" if session.get("done") else "running",
        "done": bool(session.get("done")),
        "exit_code": session.get("exit_code"),
        "total_bytes": int(session.get("total_bytes") or 0),
        "output": bytes(session.get("output") or b"").decode(errors="replace"),
    }


def list_command_sessions(
    request,
    workspace: str | None = None,
    chat_id: str | None = None,
    auth=None,
    context: dict | None = None,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    if context and context.get("request") is not None:
        request = context["request"]
    if request is not None:
        auth = getattr(getattr(request, "state", None), "auth", None)
    user_id = getattr(auth, "user_id", None) if auth is not None else None
    if user_id is None and context:
        user_id = context.get("user_id")
    for command_session_id, session in command_sessions.items():
        if session.get("done"):
            continue
        if workspace and session.get("workspace") != workspace:
            continue
        if chat_id and session.get("chat_id") != chat_id:
            continue
        if user_id is not None and session.get("user_id") != user_id:
            continue
        sessions.append(_command_session_snapshot(command_session_id, session))
    sessions.sort(key=lambda item: (item["status"] != "running", -float(item["created_at"] or 0)))
    return sessions


def get_command_session(
    request,
    command_session_id: str = "",
    auth=None,
    context: dict | None = None,
) -> dict | None:
    session = command_sessions.get(command_session_id)
    if context and context.get("request") is not None:
        request = context["request"]
    if request is not None:
        auth = getattr(getattr(request, "state", None), "auth", None)
    user_id = getattr(auth, "user_id", None) if auth is not None else None
    if user_id is None and context:
        user_id = context.get("user_id")
    if not session or (user_id is not None and session.get("user_id") != user_id):
        return None
    return session


def command_session_bytes_since(session: dict, offset: int) -> tuple[bytes, int]:
    buf = session["output"]
    total = int(session.get("total_bytes") or 0)
    buf_start = total - len(buf)
    if offset <= buf_start:
        raw = bytes(buf)
    else:
        raw = bytes(buf[offset - buf_start :])
    return raw, total


def send_command_session_input(
    request, command_session_id: str, data: bytes, **scope
) -> str | None:
    session = get_command_session(request, command_session_id, **scope)
    if not session:
        return "command session not found"
    if session.get("done"):
        return "command session already exited"

    master_fd = session.get("master_fd")
    if master_fd is not None:
        try:
            os.write(master_fd, data)
        except OSError:
            return "PTY closed"
    else:
        proc = session["proc"]
        if proc.stdin is None:
            return "stdin unavailable"
        try:
            proc.stdin.write(data)
            if hasattr(proc.stdin, "drain"):
                # asyncio subprocess pipe
                return None
        except (BrokenPipeError, ConnectionResetError, OSError):
            return "stdin closed"
    return None


async def drain_command_session_input(request, command_session_id: str, **scope) -> None:
    session = get_command_session(request, command_session_id, **scope)
    proc = session.get("proc") if session else None
    stdin = getattr(proc, "stdin", None)
    if stdin is not None and hasattr(stdin, "drain"):
        await stdin.drain()


def resize_command_session(request, command_session_id: str, rows: int, cols: int, **scope) -> None:
    session = get_command_session(request, command_session_id, **scope)
    if not session or session.get("done"):
        return
    master_fd = session.get("master_fd")
    if master_fd is None or not _PTY_AVAILABLE:
        return
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
    except OSError:
        pass


def stop_command_session(
    request, command_session_id: str, force: bool = False, **scope
) -> str | None:
    session = get_command_session(request, command_session_id, **scope)
    if not session:
        return "command session not found"
    if session.get("done"):
        return None
    _kill_process_group(session["proc"].pid, force=force)
    return None


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
