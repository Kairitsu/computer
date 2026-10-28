"""CodeBuddy Code ACP adapter.

CodeBuddy runs its own agent loop (`codebuddy --acp`): its system prompt, tools,
skills, MCP servers, permission modes and context handling. cptr relays the turn and
answers what CodeBuddy asks of the client (permissions, questions, plan approval).

It signs in with the login CodeBuddy saved under the user's home
(`~/.local/share/CodeBuddyExtension/Data/Public/auth`) and refreshes it itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from cptr.env import DATA_DIR
from cptr.utils.agents.acp import (
    ACP_PERMISSION_METHOD,
    AcpClient,
    acp_event_stream,
    acp_models_from_setup,
    acp_permission_events,
    acp_text_from_update,
    acp_thought_from_update,
    acp_tool_from_update,
    discard_stale_events,
)
from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.events import (
    AgentAskUser,
    AgentContextUsage,
    AgentDone,
    AgentError,
    AgentEvent,
    AgentReasoningDelta,
    AgentTextDelta,
    AgentToolUpdate,
    AgentTurnStarted,
)
from cptr.utils.agents.params import (
    agent_params,
    highest_reasoning_effort,
    longest_context_window,
)
from cptr.utils.agents.prompts import session_turn_prompt_text
from cptr.utils.identity import env_for, preexec_for

# The install belongs to root, so the CLI cannot update itself; remote control would
# register this server with CodeBuddy's chat channels.
CODEBUDDY_ENV = {"DISABLE_AUTOUPDATER": "1", "CODEBUDDY_DISABLE_REMOTE_CONTROL": "1"}
# Without it session/load replays the whole conversation as session/update notifications.
LOAD_META = {"codebuddy.ai/suppressReplay": True}
# CodeBuddy's AskUserQuestion blocks on this request to the client.
QUESTION_METHOD = "_codebuddy.ai/question"
# Drops a prompt and everything after it from the session. Without a `reason` CodeBuddy
# answers `applied: true` and changes nothing.
ROLLBACK_METHOD = "_codebuddy.ai/session/rollback"
ROLLBACK_REASON = "checkpoint_revert"
EXTENSION_REQUESTS = frozenset({QUESTION_METHOD, ACP_PERMISSION_METHOD})
AUTH_DIR = Path(".local/share/CodeBuddyExtension/Data/Public/auth")
DEFAULT_PERMISSION_MODE = "default"
PLAN_MODE = "plan"
# Agent phases in which an update's `requestId` is the prompt's stored id.
MODEL_PHASES = frozenset({"model_requesting", "model_streaming", "model_done"})
# thought_level value for the model's own default effort.
MODEL_DEFAULT_EFFORT = "enabled"
EXIT_PLAN_MODE_TOOL = "ExitPlanMode"
# Updates CodeBuddy streams while compacting the conversation; the finished summary
# arrives as one message chunk ending in COMPACT_DONE_MARK.
COMPACT_META = "codebuddy.ai/isCompactInternal"
COMPACT_DONE_MARK = "</conversation_history_summary>"
PLAN_APPROVE_LABEL = "Approve"
PLAN_KEEP_PLANNING_LABEL = "Keep planning"
PLAN_EXIT_LABEL = "Exit plan mode"
SESSION_START_TIMEOUT_SECONDS = 60


def codebuddy_home(profile: dict[str, Any] | None = None, identity=None) -> Path:
    """The home CodeBuddy keeps its login and sessions in."""
    if profile and profile.get("home"):
        return Path(os.path.expanduser(str(profile["home"])))
    if identity is not None and getattr(identity, "is_pam", False):
        return Path(identity.home)
    return Path.home()


def codebuddy_env(profile: dict[str, Any], identity=None, workspace: str = "") -> dict[str, str]:
    if identity is not None and identity.is_pam:
        env = env_for(identity, workspace or str(Path.home()))
    else:
        env = os.environ.copy()
    env["HOME"] = str(codebuddy_home(profile, identity))
    env.update(CODEBUDDY_ENV)
    return env


def auth_files(home: Path) -> list[Path]:
    try:
        return sorted((home / AUTH_DIR).glob("*.info"))
    except OSError:
        return []


def signed_in(home: Path) -> bool:
    for path in auth_files(home):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        auth = data.get("auth") if isinstance(data, dict) else None
        if isinstance(auth, dict) and auth.get("accessToken"):
            return True
    return False


def remove_session_files(home: Path, session_id: str) -> None:
    """Delete one CodeBuddy session: `projects/<cwd slug>/<id>.jsonl` and its subagent dir."""
    if not session_id or "/" in session_id or session_id.startswith("."):
        return
    projects = home / ".codebuddy" / "projects"
    for path in projects.glob(f"*/{session_id}.jsonl"):
        with suppress(OSError):
            path.unlink()
    for path in projects.glob(f"*/{session_id}"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _config_option(options: Any, option_id: str) -> dict[str, Any] | None:
    for option in options if isinstance(options, list) else []:
        if isinstance(option, dict) and option.get("id") == option_id:
            return option
    return None


def _choices(option: dict[str, Any] | None) -> list[dict[str, str]]:
    result = []
    for raw in (option or {}).get("options") or []:
        value = raw.get("value") if isinstance(raw, dict) else None
        if not isinstance(value, str) or not value:
            continue
        name = raw.get("name")
        description = raw.get("description")
        result.append(
            {
                "value": value,
                "label": name if isinstance(name, str) and name else value,
                "description": description if isinstance(description, str) else "",
            }
        )
    return result


def _permission_modes(setup: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """Permission choices for the composer menu.

    Plan is not one of them. The plan switch is separate, so a chat can stay on
    full access and plan at the same time. CodeBuddy still enters plan as a
    session mode for that turn only.
    """
    modes = setup.get("modes") if isinstance(setup.get("modes"), dict) else {}
    result = []
    for raw in modes.get("availableModes") or []:
        mode_id = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(mode_id, str) or not mode_id or mode_id == PLAN_MODE:
            continue
        name = raw.get("name")
        description = raw.get("description")
        result.append(
            {
                "value": mode_id,
                "label": name if isinstance(name, str) and name else mode_id,
                "description": description if isinstance(description, str) else "",
            }
        )
    current = modes.get("currentModeId")
    if (
        not isinstance(current, str)
        or not current
        or current == PLAN_MODE
        or not any(item["value"] == current for item in result)
    ):
        current = result[0]["value"] if result else DEFAULT_PERMISSION_MODE
    return result, current


def chat_permission_mode(params: dict[str, Any]) -> str:
    """The permission the user picked. Plan is never stored as that permission."""
    mode = params.get("permission_mode")
    if isinstance(mode, str) and mode and mode != PLAN_MODE:
        return mode
    return DEFAULT_PERMISSION_MODE


def session_mode(params: dict[str, Any]) -> str:
    """Mode for this turn: plan while the plan switch is on, otherwise the permission.

    A chat saved when plan was still a permission entry keeps planning, and the
    permission falls back to CodeBuddy's ordinary default.
    """
    if params.get("plan_mode") is True or params.get("permission_mode") == PLAN_MODE:
        return PLAN_MODE
    return chat_permission_mode(params)


def _model_settings(config_options: Any, max_input_tokens: Any = None) -> dict[str, Any]:
    """Efforts and windows CodeBuddy offers for the session's current model."""
    efforts = [
        choice
        for choice in _choices(_config_option(config_options, "thought_level"))
        if choice["value"] != MODEL_DEFAULT_EFFORT
    ]
    window_option = _config_option(config_options, "context_window")
    windows = [
        window
        for window in (_positive_int(choice["value"]) for choice in _choices(window_option))
        if window
    ]
    current_window = _positive_int((window_option or {}).get("currentValue")) or _positive_int(
        max_input_tokens
    )
    if current_window and current_window not in windows:
        windows.insert(0, current_window)
    return {
        "reasoning_efforts": efforts,
        "default_reasoning_effort": highest_reasoning_effort(efforts),
        "context_windows": windows,
        "default_context_window": longest_context_window(windows) or current_window,
    }


def codebuddy_model_options(
    setup: dict[str, Any], per_model: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """What CodeBuddy offers per model: display name, efforts, windows and permission modes.

    `per_model` holds each model's session config options, read by switching the probe
    session to it (local to the CLI, no model call).
    """
    modes, default_mode = _permission_modes(setup)
    models = setup.get("models") if isinstance(setup.get("models"), dict) else {}
    result: dict[str, dict[str, Any]] = {}
    for item in models.get("availableModels") or []:
        model_id = item.get("modelId") if isinstance(item, dict) else None
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}
        name = item.get("name")
        credits = meta.get("credits")
        result[model_id.strip()] = {
            "display_name": name if isinstance(name, str) and name else model_id.strip(),
            **_model_settings(per_model.get(model_id), meta.get("maxInputTokens")),
            "permission_modes": modes,
            "default_permission_mode": default_mode,
            "credits": credits.removesuffix(" credits") if isinstance(credits, str) else "",
        }
    # CodeBuddy gives some models the same name (hy3 and hy3-x are both "Hy3").
    names = [options["display_name"] for options in result.values()]
    for model_id, options in result.items():
        if names.count(options["display_name"]) > 1:
            options["display_name"] = f"{options['display_name']} ({model_id})"
    return result


# ── Model catalog ───────────────────────────────────────────
#
# Reading the catalog starts a CodeBuddy process and one session but sends no prompt.
# It is kept for CATALOG_TTL_SECONDS and saved for when CodeBuddy cannot start; a stale
# catalog is served while a fresh one is read in the background.

CATALOG_TTL_SECONDS = 600
CATALOG_FILE = DATA_DIR / "codebuddy-models.json"
PROBE_DIR = DATA_DIR / "agent-probe"
_catalog: dict[str, Any] | None = None
_catalog_at = 0.0
_catalog_refresh: asyncio.Task | None = None
_catalog_lock = asyncio.Lock()


def _load_saved_catalog() -> dict[str, Any] | None:
    try:
        data = json.loads(CATALOG_FILE.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("models"), list) and data["models"]:
        return data
    return None


def _save_catalog(catalog: dict[str, Any]) -> None:
    with suppress(OSError):
        CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False))


async def _read_catalog(command: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    home = codebuddy_home(profile)
    client = AcpClient(
        command=command,
        args=["--acp"],
        cwd=str(PROBE_DIR),
        env=codebuddy_env(profile),
        auth_method_id=None,
        authenticate=False,
    )
    try:
        await asyncio.wait_for(client.start(), timeout=SESSION_START_TIMEOUT_SECONDS)
        setup = client.setup_result
        models = acp_models_from_setup(setup)
        # The model CodeBuddy starts sessions on goes first, so it is the profile default.
        current = (setup.get("models") or {}).get("currentModelId")
        if current in models:
            models = [current, *(model for model in models if model != current)]
        per_model: dict[str, Any] = {}
        for model in models:
            result = await asyncio.wait_for(
                client.request(
                    "session/set_config_option",
                    {"sessionId": client.session_id, "configId": "model", "value": model},
                ),
                timeout=10,
            )
            per_model[model] = result.get("configOptions")
        return {"models": models, "options": codebuddy_model_options(setup, per_model)}
    except Exception:
        return None
    finally:
        session_id = client.session_id
        await client.close()
        if session_id:
            remove_session_files(home, session_id)


async def _refresh_catalog(command: str, profile: dict[str, Any]) -> dict[str, Any] | None:
    global _catalog, _catalog_at
    async with _catalog_lock:
        catalog = await _read_catalog(command, profile)
        if catalog and catalog["models"]:
            _catalog, _catalog_at = catalog, time.monotonic()
            _save_catalog(catalog)
        return catalog


async def codebuddy_catalog(
    command: str, profile: dict[str, Any], refresh: bool = False
) -> dict[str, Any] | None:
    """Model ids and per-model options, as CodeBuddy lists them for the signed-in account."""
    global _catalog, _catalog_at, _catalog_refresh
    if _catalog is None and not refresh:
        _catalog = _load_saved_catalog()
        _catalog_at = 0.0
    if _catalog is None or refresh:
        return await _refresh_catalog(command, profile) or _catalog
    if time.monotonic() - _catalog_at >= CATALOG_TTL_SECONDS and (
        _catalog_refresh is None or _catalog_refresh.done()
    ):
        _catalog_refresh = asyncio.create_task(_refresh_catalog(command, profile))
    return _catalog


def cached_codebuddy_options(model: str) -> dict[str, Any]:
    return ((_catalog or {}).get("options") or {}).get(model, {})


# ── One live `codebuddy --acp` process per chat ─────────────
#
# A process keeps the session loaded, so a turn starts without CodeBuddy restoring
# the session and refreshing its login again. Each process holds about 450 MB. Idle
# Grok and CodeBuddy processes share one count and one timeout.

async def codebuddy_process_settings() -> dict[str, int]:
    """The shared idle-process count and timeout."""
    from cptr.utils.agents.process_limits import agent_process_settings

    return await agent_process_settings()


async def save_codebuddy_process_settings(
    max_idle_processes: int | None = None, idle_timeout_minutes: int | None = None
) -> dict[str, int]:
    """Save the shared limits and apply them to the processes kept now."""
    from cptr.utils.agents.process_limits import save_agent_process_settings

    return await save_agent_process_settings(max_idle_processes, idle_timeout_minutes)


@dataclass
class _LiveCodeBuddy:
    client: AcpClient
    key: tuple[Any, ...]
    # (session id, model, effort, window) the session was last set up with.
    settings: tuple[Any, ...] | None = None
    context_window: int | None = None
    mode: str | None = None
    # The mode CodeBuddy goes back to when it leaves plan mode (its prePlanPermissionMode).
    pre_plan_mode: str | None = None
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def alive(self) -> bool:
        proc = self.client.proc
        return proc is not None and proc.returncode is None


_live_sessions: dict[str, _LiveCodeBuddy] = {}
_background_tasks: set[asyncio.Task] = set()
_reaper: asyncio.Task | None = None


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _close_live(session_id: str, live: _LiveCodeBuddy) -> None:
    if _live_sessions.get(session_id) is live:
        _live_sessions.pop(session_id)
    await live.client.close()


async def close_codebuddy_session(session_id: str, *, running: bool = True) -> None:
    """Stop the process kept for one session; with ``running=False`` a turn finishes first."""
    live = _live_sessions.get(session_id)
    if live and (running or not live.lock.locked()):
        await _close_live(session_id, live)


async def close_all_codebuddy_sessions() -> None:
    for session_id, live in list(_live_sessions.items()):
        await _close_live(session_id, live)


def codebuddy_session_ids(chat: Any) -> list[str]:
    """The CodeBuddy sessions a chat has saved for its next turns."""
    sessions = ((getattr(chat, "meta", None) or {}).get("agent_sessions")) or {}
    if not isinstance(sessions, dict):
        return []
    return [
        session["session_id"]
        for session in sessions.values()
        if isinstance(session, dict)
        and session.get("agent") == "codebuddy"
        and isinstance(session.get("session_id"), str)
    ]


async def _checkout(session_id: str | None, key: tuple[Any, ...]) -> _LiveCodeBuddy | None:
    live = _live_sessions.get(session_id) if session_id else None
    if live is None:
        return None
    await live.lock.acquire()
    if _live_sessions.get(session_id) is live and live.key == key and live.alive():
        return live
    live.lock.release()
    await _close_live(session_id, live)
    return None


def idle_process_snapshots() -> list[tuple[str, float, bool]]:
    """Idle CodeBuddy processes: session id, last used, and whether it is alive."""
    return [
        (session_id, live.last_used, live.alive())
        for session_id, live in _live_sessions.items()
        if not live.lock.locked()
    ]


async def close_idle_process(session_id: str) -> None:
    """Close one idle CodeBuddy process. A process in a turn is left running."""
    live = _live_sessions.get(session_id)
    if live is None or live.lock.locked():
        return
    await _close_live(session_id, live)


async def _trim_idle() -> None:
    """Apply the shared idle limit to Grok and CodeBuddy together."""
    from cptr.utils.agents.process_limits import trim_idle_processes

    await trim_idle_processes()


async def _reap_idle_sessions() -> None:
    while _live_sessions:
        await asyncio.sleep(60)
        await _trim_idle()


def _check_in(session_id: str, live: _LiveCodeBuddy) -> None:
    global _reaper
    live.last_used = time.monotonic()
    previous = _live_sessions.get(session_id)
    if previous is not None and previous is not live:
        _spawn(previous.client.close())
    _live_sessions[session_id] = live
    if live.lock.locked():
        live.lock.release()
    _spawn(_trim_idle())
    if _reaper is None or _reaper.done():
        _reaper = asyncio.create_task(_reap_idle_sessions())


# ── Turn helpers ────────────────────────────────────────────


async def _set_mode(client: AcpClient, mode: str) -> None:
    await client.request("session/set_mode", {"sessionId": client.session_id, "modeId": mode})


async def _apply_settings(
    client: AcpClient,
    model: str,
    params: dict[str, Any],
    mode: str,
    *,
    permission: str,
) -> int | None:
    """Put the session on the chat's model, effort, window and mode.

    session/load and a model switch reset the effort and window, so they are set
    again after the model. A missing or leftover choice uses the strongest effort and
    the longest window CodeBuddy lists. Plan mode is a session mode on top of the
    chosen permission: that permission is set first, so leaving the plan returns to
    it (for example full access) instead of whatever the session used before.
    Returns the window the session runs with.
    """
    request = {"sessionId": client.session_id, "configId": "model", "value": model}
    result = await client.request("session/set_config_option", request)
    options = _model_settings(result.get("configOptions"))
    efforts = options["reasoning_efforts"]
    effort = params.get("reasoning_effort")
    if not any(choice["value"] == effort for choice in efforts):
        effort = highest_reasoning_effort(efforts)
    if effort:
        await client.set_config_option("thought_level", effort)
    windows = options["context_windows"]
    window = _positive_int(params.get("context_window"))
    if window not in windows:
        window = longest_context_window(windows) or options.get("default_context_window")
    if window and (not windows or window in windows):
        await client.set_config_option("context_window", str(window))
    if mode == PLAN_MODE and permission != PLAN_MODE:
        await _set_mode(client, permission)
    await _set_mode(client, mode)
    return window


async def _rollback(client: AcpClient, request_id: str) -> bool:
    """Drop a turn the user took back (see the chat retract endpoint) from the session."""
    try:
        result = await client.request(
            ROLLBACK_METHOD,
            {
                "sessionId": client.session_id,
                "targetRequestId": request_id,
                "reason": ROLLBACK_REASON,
            },
        )
    except Exception:
        return False
    return result.get("applied") is True


def _turn_ids(update: dict[str, Any]) -> tuple[str | None, str | None]:
    """(message request id, prompt request id) an update names, as far as it does.

    Updates for one prompt carry the client message's `messageRequestId`. Until
    CodeBuddy calls the model, their `requestId` is that id or still the previous
    prompt's. From the model call on, `conversationRequestId` (on streamed chunks) and
    `requestId` (on the model phases) name the id the prompt is stored under, which a
    rollback targets.
    """
    meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
    message_id = meta.get("codebuddy.ai/messageRequestId")
    message_id = message_id if isinstance(message_id, str) and message_id else None
    request_id = meta.get("codebuddy.ai/conversationRequestId")
    if not request_id:
        phase = meta.get("codebuddy.ai/agentPhase")
        if isinstance(phase, dict) and phase.get("phase") in MODEL_PHASES:
            request_id = meta.get("codebuddy.ai/requestId")
    if not isinstance(request_id, str) or not request_id or request_id == message_id:
        request_id = None
    return message_id, request_id


def _question_ask(request_id: Any, params: dict[str, Any]) -> AgentAskUser | None:
    schema = params.get("schema") if isinstance(params.get("schema"), dict) else {}
    questions = []
    for raw in schema.get("questions") or []:
        if not isinstance(raw, dict):
            continue
        question_id = raw.get("id")
        text = raw.get("question")
        if not isinstance(question_id, str) or not isinstance(text, str) or not text.strip():
            continue
        options = []
        for option in raw.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            if isinstance(label, str) and label.strip():
                description = option.get("description") if isinstance(option, dict) else ""
                options.append(
                    {
                        "label": label.strip(),
                        "description": description if isinstance(description, str) else "",
                    }
                )
        header = raw.get("header")
        questions.append(
            {
                "id": question_id,
                "header": header.strip() if isinstance(header, str) else "",
                "question": text.strip(),
                "options": options,
            }
        )
    if not questions:
        return None
    call_id = params.get("toolCallId")
    return AgentAskUser(
        call_id=call_id if isinstance(call_id, str) and call_id else f"ask-{request_id}",
        questions=questions,
        auto_resolve=all(question["options"] for question in questions),
    )


async def _question_events(client: AcpClient, message: dict[str, Any]) -> AsyncIterator[AgentEvent]:
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    ask = _question_ask(message.get("id"), params)
    if ask is None:
        await client.respond(message.get("id"), {"outcome": "cancelled"})
        return
    yield ask
    answers = {qid: answer for qid, answer in (ask.answers or {}).items() if answer}
    outcome = {"outcome": "submitted", "answers": answers} if answers else {"outcome": "cancelled"}
    await client.respond(message.get("id"), outcome)


def _exit_plan_request(params: dict[str, Any]) -> bool:
    tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
    meta = tool_call.get("_meta") if isinstance(tool_call.get("_meta"), dict) else {}
    if meta.get("codebuddy.ai/toolName") == EXIT_PLAN_MODE_TOOL:
        return True
    return any(
        isinstance(option, dict) and option.get("optionId") == "reject_and_exit_plan"
        for option in params.get("options") or []
    )


def _plan_text(params: dict[str, Any]) -> str:
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
    call_meta = tool_call.get("_meta") if isinstance(tool_call.get("_meta"), dict) else {}
    raw_input = tool_call.get("rawInput") if isinstance(tool_call.get("rawInput"), dict) else {}
    for value in (
        call_meta.get("codebuddy.ai/plan"),
        meta.get("codebuddy.ai/plan"),
        raw_input.get("plan"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


async def _plan_approval_events(
    client: AcpClient, message: dict[str, Any]
) -> AsyncIterator[AgentEvent]:
    """Show CodeBuddy's plan in the question card and pass the user's choice back."""
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
    call_id = tool_call.get("toolCallId")
    ask = AgentAskUser(
        call_id=call_id if isinstance(call_id, str) and call_id else f"plan-{message.get('id')}",
        questions=[
            {
                "id": "plan",
                "header": "Plan ready",
                "question": _plan_text(params)
                or "The agent finished planning without writing a plan.",
                "options": [
                    {
                        "label": PLAN_APPROVE_LABEL,
                        "description": "Leave plan mode and start implementing.",
                    },
                    {
                        "label": PLAN_KEEP_PLANNING_LABEL,
                        "description": "Stay in plan mode and keep refining the plan.",
                    },
                    {
                        "label": PLAN_EXIT_LABEL,
                        "description": "Leave plan mode without implementing the plan.",
                    },
                ],
            }
        ],
        # Leaving plan mode unlocks edits, so never pick an answer for the user.
        auto_resolve=False,
    )
    yield ask
    answer = (ask.answers or {}).get("plan")
    option_id = {PLAN_APPROVE_LABEL: "allow", PLAN_EXIT_LABEL: "reject_and_exit_plan"}.get(
        answer or "", "reject"
    )
    await client.respond(
        message.get("id"), {"outcome": {"outcome": "selected", "optionId": option_id}}
    )


def _tool_update(params: dict[str, Any]) -> dict[str, Any] | None:
    """One CodeBuddy tool call update as cptr shows it, or None to skip it."""
    update = params.get("update") if isinstance(params.get("update"), dict) else {}
    kind = update.get("sessionUpdate")
    if kind == "tool_call_update" and "status" not in update and "rawOutput" not in update:
        # CodeBuddy streams a call's arguments as the model writes them; the complete
        # call follows as its own tool_call.
        return None
    tool = acp_tool_from_update(params)
    if tool is None:
        return None
    if kind == "tool_call_update" and not update.get("rawInput"):
        # A status or result: keep the name and arguments the call already shows.
        tool["name"] = None
        tool["arguments"] = {}
    raw_output = update.get("rawOutput")
    if isinstance(raw_output, dict) and isinstance(raw_output.get("text"), str):
        tool["output"] = raw_output["text"]
    return tool


def _image_block(image) -> dict[str, str]:
    return {"data": image.base64, "mimeType": image.mime_type}


async def run_codebuddy_agent(
    *,
    profile: dict[str, Any],
    model: str,
    workspace: str,
    messages: list[dict[str, Any]],
    chat_params: dict[str, Any],
    resume_state: dict[str, Any] | None,
    attachments: PreparedAgentAttachments,
    identity=None,
) -> AsyncIterator[AgentEvent]:
    params = agent_params(chat_params, profile["id"])
    env = codebuddy_env(profile, identity, workspace)
    permission = chat_permission_mode(params)
    mode = session_mode(params)

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        session_id = resume_state["session_id"]
    rewind = (resume_state or {}).get("rewind")
    if not isinstance(rewind, dict) or rewind.get("session_id") != session_id:
        rewind = None
    if rewind and not isinstance(rewind.get("request_id"), str):
        # The turn was stopped before CodeBuddy named its prompt, so it cannot be
        # rolled back by id: start a new session, which gets the chat transcript.
        await close_codebuddy_session(session_id)
        session_id, rewind = None, None

    command = str(profile["command"])
    key = (command, workspace, env.get("HOME"), identity.username if identity else None)
    live = await _checkout(session_id, key)
    if live is None:
        live = _LiveCodeBuddy(
            client=AcpClient(
                command=command,
                args=["--acp"],
                cwd=workspace,
                env=env,
                auth_method_id=None,
                authenticate=False,
                resume_session_id=session_id,
                extension_requests=EXTENSION_REQUESTS,
                session_meta=LOAD_META,
                preexec_fn=preexec_for(identity) if identity and identity.is_pam else None,
            ),
            key=key,
        )
        reused = False
    else:
        reused = True
    client = live.client
    kept = False
    try:
        if reused:
            await discard_stale_events(client)
        else:
            await asyncio.wait_for(client.start(), timeout=SESSION_START_TIMEOUT_SECONDS)
        # A session CodeBuddy can no longer load starts over, with the chat transcript.
        resumed = bool(session_id) and client.session_id == session_id
        if resumed and rewind and not await _rollback(client, rewind["request_id"]):
            await client.new_session()
            resumed = False

        settings = (
            client.session_id,
            model,
            params.get("reasoning_effort"),
            params.get("context_window"),
        )
        permission_changed = mode == PLAN_MODE and live.pre_plan_mode != permission
        if live.settings != settings or live.mode != mode or permission_changed:
            if mode == PLAN_MODE:
                # Remember the menu's permission. CodeBuddy restores the mode set
                # just before plan, which _apply_settings sets to this value.
                live.pre_plan_mode = permission
            live.context_window = await _apply_settings(
                client, model, params, mode, permission=permission
            )
            live.settings, live.mode = settings, mode
            # Drop the updates these settings caused, so none reads as CodeBuddy's own change.
            await asyncio.sleep(0.1)
            await discard_stale_events(client)
        context_window = live.context_window
        context_tokens: int | None = None
        compactions = (resume_state or {}).get("compactions") if resumed else None
        compactions = compactions if isinstance(compactions, int) else 0
        # Credits per model call, by CodeBuddy's message id (a call can report twice).
        credits: dict[str, float] = {}
        input_tokens = output_tokens = 0
        # This turn's client message id, then the id CodeBuddy stores the prompt under.
        turn_message_id: str | None = None
        request_id: str | None = None
        agent_mode: str | None = None

        prompt = session_turn_prompt_text(messages, resumed=resumed)
        images = [_image_block(image) for image in attachments.images]
        yield AgentTurnStarted(session_id=client.session_id)
        prompt_task = asyncio.create_task(client.prompt(prompt, images=images))
        try:
            async for event in acp_event_stream(client, prompt_task):
                method = event.get("method")
                params_ = event.get("params") if isinstance(event.get("params"), dict) else {}
                if "id" in event and method == ACP_PERMISSION_METHOD:
                    if not _exit_plan_request(params_):
                        async for permission_event in acp_permission_events(client, event):
                            yield permission_event
                        continue
                    leaves_plan = False
                    async for plan_event in _plan_approval_events(client, event):
                        yield plan_event
                        answer = (getattr(plan_event, "answers", None) or {}).get("plan")
                        leaves_plan = answer in (PLAN_APPROVE_LABEL, PLAN_EXIT_LABEL)
                    if leaves_plan and live.mode == PLAN_MODE:
                        # Back to the permission the menu still shows. CodeBuddy also
                        # restores the mode it left, which was set to this permission.
                        restore = live.pre_plan_mode or permission
                        live.mode = agent_mode = restore
                        await _set_mode(client, restore)
                    continue
                if "id" in event and method == QUESTION_METHOD:
                    async for ask_event in _question_events(client, event):
                        yield ask_event
                    continue
                # Subagents stream on the same pipe under their own session ids; only this
                # session's text belongs in the chat's reply.
                own_session = params_.get("sessionId") in (None, client.session_id)
                update = params_.get("update") if isinstance(params_.get("update"), dict) else {}
                kind = update.get("sessionUpdate")
                if own_session and request_id is None:
                    message_id, named_id = _turn_ids(update)
                    turn_message_id = turn_message_id or message_id
                    if named_id and message_id and message_id == turn_message_id:
                        request_id = named_id
                        yield AgentTurnStarted(session_id=client.session_id, request_id=request_id)
                if kind == "usage_update":
                    if not own_session:
                        continue
                    context_window = _positive_int(update.get("size")) or context_window
                    meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
                    usage = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
                    if usage:
                        prompt_tokens = _positive_int(usage.get("prompt_tokens")) or 0
                        completion_tokens = _positive_int(usage.get("completion_tokens")) or 0
                        message_id = meta.get("codebuddy.ai/messageId")
                        key_ = message_id if isinstance(message_id, str) else str(len(credits))
                        credit = usage.get("credit")
                        if isinstance(credit, (int, float)) and not isinstance(credit, bool):
                            credits[key_] = float(credit)
                        input_tokens += prompt_tokens
                        output_tokens += completion_tokens
                        # What the conversation holds after this call: its prompt and reply.
                        if prompt_tokens:
                            context_tokens = prompt_tokens + completion_tokens
                            yield AgentContextUsage(
                                tokens=context_tokens,
                                window=context_window,
                                compactions=compactions,
                            )
                    continue
                if kind in ("current_mode_update", "config_option_update") and own_session:
                    current = update.get("currentModeId")
                    if kind == "config_option_update":
                        option = _config_option(update.get("configOptions"), "mode") or {}
                        current = option.get("currentValue")
                    if isinstance(current, str) and current and current != live.mode:
                        if current == PLAN_MODE:
                            # Plan is the separate switch, not the chat's permission.
                            live.mode = PLAN_MODE
                        else:
                            # CodeBuddy changed it, e.g. leaving plan mode once the plan
                            # was approved; the chat takes the new mode.
                            live.mode = agent_mode = current
                    continue
                meta = update.get("_meta") if isinstance(update.get("_meta"), dict) else {}
                if meta.get(COMPACT_META) is True:
                    # CodeBuddy summarising the conversation to free its window: its own
                    # bookkeeping, not part of the reply.
                    text = acp_text_from_update(params_) or ""
                    if own_session and COMPACT_DONE_MARK in text:
                        compactions += 1
                    continue
                # A subagent's reply reaches the chat through its tool call's result.
                own_text = own_session and not (
                    meta.get("codebuddy.ai/parentToolCallId") or meta.get("codebuddy.ai/isSubAgent")
                )
                text = acp_text_from_update(params_)
                if text and own_text:
                    yield AgentTextDelta(text)
                thought = acp_thought_from_update(params_)
                if thought and own_text:
                    yield AgentReasoningDelta(thought)
                tool = _tool_update(params_)
                if tool:
                    yield AgentToolUpdate(**tool)
            await prompt_task
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task

        usage: dict[str, Any] = {}
        if input_tokens:
            usage["input_tokens"] = input_tokens
        if output_tokens:
            usage["output_tokens"] = output_tokens
        if context_tokens:
            usage["context_tokens"] = context_tokens
        if context_window:
            usage["context_window"] = context_window
        if credits:
            usage["credits"] = round(sum(credits.values()), 4)
        if client.session_id:
            _check_in(client.session_id, live)
            kept = True
        saved_tokens = context_tokens or (
            (resume_state or {}).get("context_tokens") if resumed else None
        )
        yield AgentDone(
            usage=usage or None,
            resume_state={
                "profile_id": profile["id"],
                "session_id": client.session_id,
                "workspace": workspace,
                "model": model,
                **({"permission_mode": agent_mode} if agent_mode and agent_mode != PLAN_MODE else {}),
                "context_tokens": saved_tokens,
                "context_window": context_window,
                "compactions": compactions,
            },
        )
    except asyncio.CancelledError:
        await client.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(_error_text(exc, codebuddy_home(profile, identity)))
    finally:
        # A stopped or failed turn can leave CodeBuddy mid-turn or waiting on an answer,
        # so the process is not reused; the next turn reloads the session.
        if not kept:
            if live.lock.locked():
                live.lock.release()
            await _close_live(client.session_id or "", live)


def _error_text(exc: Exception, home: Path) -> str:
    message = str(exc) or exc.__class__.__name__
    if isinstance(exc, asyncio.TimeoutError):
        return "CodeBuddy did not start a session in time. Try again."
    if not signed_in(home):
        return f"CodeBuddy is not signed in for this user ({home / AUTH_DIR}). {message}"
    return message
