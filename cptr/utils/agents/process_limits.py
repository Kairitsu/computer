"""One idle-process limit for every coding agent.

Grok and CodeBuddy each keep a process per chat between turns. Those idle
processes share one count and one timeout from Settings → General. A process
that is serving a turn is left alone and is not counted.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from cptr.models import Config

MAX_IDLE_PROCESSES_KEY = "agents.max_idle_processes"
IDLE_TIMEOUT_MINUTES_KEY = "agents.idle_timeout_minutes"
# Values saved before the two agents shared one limit.
LEGACY_MAX_IDLE_PROCESSES_KEY = "grok.max_idle_processes"
LEGACY_IDLE_TIMEOUT_MINUTES_KEY = "grok.idle_timeout_minutes"
DEFAULT_MAX_IDLE_PROCESSES = 3
DEFAULT_IDLE_TIMEOUT_MINUTES = 30
MAX_IDLE_PROCESSES_LIMIT = 100
IDLE_TIMEOUT_MINUTES_LIMIT = 7 * 24 * 60

# (agent, session id, last_used monotonic, alive)
IdleEntry = tuple[str, str, float, bool]
CloseIdle = Callable[[str], Awaitable[None]]
IdleSnapshot = Callable[[], list[tuple[str, float, bool]]]


def _int_setting(value: Any, default: int, limit: int) -> int:
    if value is None or isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(number, 0), limit)


def idle_to_close(
    entries: list[IdleEntry], max_idle: int, timeout_seconds: float, now: float
) -> list[tuple[str, str]]:
    """Idle processes to close: any past the time, then the oldest past the count.

    ``entries`` are processes that are not serving a turn. Dead ones close too.
    Newest last-used stays when the count is the only reason to close.
    """
    ranked = sorted(entries, key=lambda entry: entry[2], reverse=True)
    closing: list[tuple[str, str]] = []
    for index, (agent, session_id, last_used, alive) in enumerate(ranked):
        if index >= max_idle or now - last_used >= timeout_seconds or not alive:
            closing.append((agent, session_id))
    return closing


async def agent_process_settings() -> dict[str, int]:
    """The shared idle count and timeout. A saved Grok value fills a missing one."""
    try:
        current = await Config.get_namespace("agents")
        legacy = await Config.get_namespace("grok")
    except Exception:  # noqa: BLE001 - the defaults still apply.
        current, legacy = {}, {}
    return {
        "max_idle_processes": _int_setting(
            current.get(MAX_IDLE_PROCESSES_KEY, legacy.get(LEGACY_MAX_IDLE_PROCESSES_KEY)),
            DEFAULT_MAX_IDLE_PROCESSES,
            MAX_IDLE_PROCESSES_LIMIT,
        ),
        "idle_timeout_minutes": _int_setting(
            current.get(IDLE_TIMEOUT_MINUTES_KEY, legacy.get(LEGACY_IDLE_TIMEOUT_MINUTES_KEY)),
            DEFAULT_IDLE_TIMEOUT_MINUTES,
            IDLE_TIMEOUT_MINUTES_LIMIT,
        ),
    }


async def save_agent_process_settings(
    max_idle_processes: int | None = None, idle_timeout_minutes: int | None = None
) -> dict[str, int]:
    """Save the shared limits and apply them to the processes kept now."""
    updates: dict[str, int] = {}
    if max_idle_processes is not None:
        updates[MAX_IDLE_PROCESSES_KEY] = _int_setting(
            max_idle_processes, DEFAULT_MAX_IDLE_PROCESSES, MAX_IDLE_PROCESSES_LIMIT
        )
    if idle_timeout_minutes is not None:
        updates[IDLE_TIMEOUT_MINUTES_KEY] = _int_setting(
            idle_timeout_minutes, DEFAULT_IDLE_TIMEOUT_MINUTES, IDLE_TIMEOUT_MINUTES_LIMIT
        )
    if updates:
        await Config.upsert(updates)
    await trim_idle_processes()
    return await agent_process_settings()


def _agent_sources() -> list[tuple[str, IdleSnapshot, CloseIdle]]:
    from cptr.utils.agents.codebuddy import (
        close_idle_process as close_codebuddy,
        idle_process_snapshots as snap_codebuddy,
    )
    from cptr.utils.agents.grok import (
        close_idle_process as close_grok,
        idle_process_snapshots as snap_grok,
    )

    return [
        ("grok", snap_grok, close_grok),
        ("codebuddy", snap_codebuddy, close_codebuddy),
    ]


async def trim_idle_processes() -> None:
    """Close idle Grok and CodeBuddy processes against the one shared limit."""
    settings = await agent_process_settings()
    now = time.monotonic()
    entries: list[IdleEntry] = []
    closers: dict[str, CloseIdle] = {}
    for agent, snapshot, close in _agent_sources():
        closers[agent] = close
        entries.extend(
            (agent, session_id, last_used, alive)
            for session_id, last_used, alive in snapshot()
        )
    for agent, session_id in idle_to_close(
        entries,
        settings["max_idle_processes"],
        settings["idle_timeout_minutes"] * 60,
        now,
    ):
        await closers[agent](session_id)
