"""System events WebSocket: filesystem watching.

Replaces the old watch.py with a single multiplexed event stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import sys
import threading
from pathlib import Path
from typing import Optional, NamedTuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers.polling import PollingObserver

from cptr.utils.config import check_access

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])

# ── Filesystem watcher ────────────────────────────────────────────


def _create_observer():
    if platform.system() == "Darwin":
        # Prefer the native FSEvents observer on macOS – it fires only on
        # real filesystem changes instead of polling (which generates
        # constant false-positive "modified" events from stat calls).
        try:
            from watchdog.observers.fsevents import FSEventsObserver

            return FSEventsObserver()
        except ImportError:
            # If the C-extension isn't available, fall back to polling
            # with a longer interval to reduce noise.
            return PollingObserver(timeout=3)
    return Observer()


class _ChangeCollector(FileSystemEventHandler):
    """Collects fs events into a set for debounced delivery."""

    def __init__(self) -> None:
        self._changes: set[str] = set()
        self._lock = threading.Lock()

    # Paths that should never trigger a user-visible refresh
    _IGNORED_SEGMENTS = {".git", "__pycache__", ".DS_Store", "node_modules"}

    def _record(self, event: FileSystemEvent) -> None:
        path = event.src_path
        # Skip noisy internal paths that shouldn't trigger file browser refreshes
        parts = Path(path).parts
        if any(seg in self._IGNORED_SEGMENTS for seg in parts):
            return
        with self._lock:
            self._changes.add(str(Path(path).parent))
            self._changes.add(path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._record(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._record(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        # Ignore directory-modified events – these fire on any child
        # change (which we already capture via on_created/on_deleted)
        # and on metadata-only updates like atime from stat calls.
        if event.is_directory:
            return
        self._record(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._record(event)
        if hasattr(event, "dest_path"):
            with self._lock:
                self._changes.add(str(Path(event.dest_path).parent))
                self._changes.add(event.dest_path)

    def drain(self) -> list[str]:
        with self._lock:
            paths = list(self._changes)
            self._changes.clear()
        return paths


class _WatchEntry(NamedTuple):
    observer: Observer
    handler: _ChangeCollector
    watch: object
    subscribers: set[asyncio.Queue[list[str]]]


_watch_registry: dict[str, _WatchEntry] = {}
_watch_registry_lock = asyncio.Lock()


async def _subscribe_to_path(path: str) -> tuple[str, asyncio.Queue[list[str]]]:
    """Subscribe this connection to a shared filesystem watch."""
    resolved = str(Path(path).resolve())
    queue: asyncio.Queue[list[str]] = asyncio.Queue(maxsize=32)

    async with _watch_registry_lock:
        entry = _watch_registry.get(resolved)
        if entry is None:
            observer = _create_observer()
            observer.daemon = True
            handler = _ChangeCollector()
            watch = observer.schedule(handler, resolved, recursive=True)
            observer.start()
            entry = _WatchEntry(observer, handler, watch, set())
            _watch_registry[resolved] = entry
        entry.subscribers.add(queue)

    return resolved, queue


async def _unsubscribe_from_path(path: str, queue: asyncio.Queue[list[str]]) -> None:
    """Remove a filesystem watch subscription and stop idle observers."""
    async with _watch_registry_lock:
        entry = _watch_registry.get(path)
        if entry is None:
            return

        entry.subscribers.discard(queue)
        if entry.subscribers:
            return

        _watch_registry.pop(path, None)
        try:
            entry.observer.unschedule(entry.watch)
        except Exception:
            pass
        try:
            entry.observer.stop()
            entry.observer.join(timeout=2)
        except Exception:
            pass


async def _dispatch_fs_changes() -> None:
    """Fan out debounced filesystem changes from shared watchers."""
    while True:
        await asyncio.sleep(1.0)

        async with _watch_registry_lock:
            entries = list(_watch_registry.items())

        for path, entry in entries:
            paths = entry.handler.drain()
            if not paths:
                continue

            if sys.platform == "win32":
                paths = [p.replace("\\", "/") for p in paths]

            for queue in list(entry.subscribers):
                try:
                    queue.put_nowait(paths)
                except asyncio.QueueFull:
                    logger.debug("Dropped fs_change event for slow subscriber on %s", path)


_fs_dispatch_task: Optional[asyncio.Task] = None


async def _ensure_fs_dispatcher() -> None:
    global _fs_dispatch_task
    if _fs_dispatch_task is None or _fs_dispatch_task.done():
        _fs_dispatch_task = asyncio.create_task(_dispatch_fs_changes())


async def _fs_watcher_loop(ws: WebSocket, initial_path: str, path_holder: dict) -> None:
    """Watch filesystem and push fs_change events."""
    target = str(Path(initial_path).resolve())
    path_holder["current"] = target

    try:
        await _ensure_fs_dispatcher()
        current_path, queue = await _subscribe_to_path(target)
    except Exception as e:
        logger.warning(f"Failed to watch {target}: {e}")
        return

    try:
        while True:
            # Check if path changed (from receive loop)
            new_path = path_holder.get("pending")
            if new_path:
                del path_holder["pending"]
                resolved = str(Path(new_path).resolve())
                if resolved != path_holder["current"] and Path(resolved).is_dir():
                    await _unsubscribe_from_path(current_path, queue)
                    current_path, queue = await _subscribe_to_path(resolved)
                    path_holder["current"] = resolved
                    logger.info(f"FS watch path changed to {resolved}")

            try:
                paths = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await ws.send_json({"type": "fs_change", "paths": paths})
            except Exception:
                break
    finally:
        await _unsubscribe_from_path(current_path, queue)


# ── Receive loop ──────────────────────────────────────────────────


async def _receive_loop(ws: WebSocket, path_holder: dict) -> None:
    """Handle incoming messages from the client."""
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "watch_path":
                new_path = msg.get("path")
                if new_path:
                    path_holder["pending"] = new_path
    except (WebSocketDisconnect, Exception):
        pass


# ── WebSocket endpoint ────────────────────────────────────────────


@router.websocket("/ws")
async def events_ws(
    websocket: WebSocket, path: str = Query("/", description="Initial fs watch path")
):
    """Unified system events WebSocket.

    Pushes:
      - {"type": "fs_change", "paths": [...]}

    Receives:
      - {"type": "watch_path", "path": "..."}: change fs watch directory
    """
    target = Path(path).resolve()
    if not target.is_dir():
        await websocket.close(code=4000, reason="Not a directory")
        return

    # Auth check for WebSocket
    client_host = websocket.client.host if websocket.client else "127.0.0.1"
    token = websocket.cookies.get("cptr_session") or websocket.query_params.get("token")
    auth = check_access(client_host=client_host, jwt_token=token)
    if auth is None:
        await websocket.close(code=4001, reason="unauthorized")
        return

    await websocket.accept()
    logger.info(f"Events WS connected, watching {target}")

    path_holder: dict = {}

    fs_task = asyncio.create_task(_fs_watcher_loop(websocket, str(target), path_holder))
    recv_task = asyncio.create_task(_receive_loop(websocket, path_holder))

    try:
        done, pending = await asyncio.wait(
            [fs_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    except Exception as e:
        logger.error(f"Events WS error: {e}")
    finally:
        logger.info("Events WS disconnected")
