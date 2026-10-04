"""Grok ACP adapter."""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from cptr.env import GROK_IDLE_TIMEOUT_SECONDS, GROK_MAX_IDLE_PROCESSES
from cptr.utils.agents.attachments import PreparedAgentAttachments
from cptr.utils.agents.acp import (
    ACP_PERMISSION_METHOD,
    AcpClient,
    acp_event_stream,
    acp_permission_events,
    acp_text_from_update,
    acp_tool_from_update,
)
from cptr.utils.agents.events import (
    AgentAskUser,
    AgentContextUsage,
    AgentDone,
    AgentError,
    AgentEvent,
    AgentTextDelta,
    AgentToolUpdate,
)
from cptr.utils.agents.prompts import turn_prompt_text
from cptr.utils.identity import env_for, preexec_for


# Grok blocks its AskUserQuestion and ExitPlanMode tools on these requests to the client.
XAI_ASK_USER_QUESTION_METHODS = frozenset({"_x.ai/ask_user_question", "x.ai/ask_user_question"})
XAI_EXIT_PLAN_MODE_METHODS = frozenset({"_x.ai/exit_plan_mode", "x.ai/exit_plan_mode"})
XAI_EXTENSION_REQUESTS = XAI_ASK_USER_QUESTION_METHODS | XAI_EXIT_PLAN_MODE_METHODS
# Carries Grok's per-model-call usage and auto-compaction progress.
XAI_SESSION_NOTIFICATIONS = frozenset({"_x.ai/session_notification", "x.ai/session_notification"})
XAI_COMPACT_METHOD = "_x.ai/compact_conversation"
# Grok's default `[session] auto_compact_threshold_percent`.
GROK_AUTO_COMPACT_PERCENT = 85

PLAN_APPROVE_LABEL = "Approve"
PLAN_KEEP_PLANNING_LABEL = "Keep planning"
PLAN_KEEP_PLANNING_FEEDBACK = (
    "The user wants to keep planning. Stay in plan mode and ask what to change."
)


def _ext_params(params: Any) -> dict[str, Any]:
    if isinstance(params, dict) and isinstance(params.get("params"), dict):
        return params["params"]
    return params if isinstance(params, dict) else {}


def _ext_call_id(params: dict[str, Any], request_id: Any) -> str:
    call_id = params.get("toolCallId")
    if isinstance(call_id, str) and call_id.strip():
        return call_id.strip()
    return f"ask-{request_id}"


def _ask_user_question(
    request_id: Any, params: dict[str, Any]
) -> tuple[AgentAskUser, list[str]] | None:
    """Map Grok questions to ask_user questions plus the question texts Grok keys answers by."""
    raw_questions = params.get("questions")
    if not isinstance(raw_questions, list):
        return None
    questions: list[dict[str, Any]] = []
    keys: list[str] = []
    for raw in raw_questions:
        if not isinstance(raw, dict):
            continue
        text = raw.get("question")
        if not isinstance(text, str) or not text.strip():
            continue
        options = []
        for option in raw.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else None
            if isinstance(label, str) and label.strip():
                description = option.get("description")
                options.append(
                    {
                        "label": label.strip(),
                        "description": description.strip() if isinstance(description, str) else "",
                    }
                )
        header = raw.get("header")
        questions.append(
            {
                "id": f"q{len(questions) + 1}",
                "header": header.strip() if isinstance(header, str) else "",
                "question": text.strip(),
                "options": options,
            }
        )
        keys.append(text)
    if not questions:
        return None
    ask = AgentAskUser(
        call_id=_ext_call_id(params, request_id),
        questions=questions,
        auto_resolve=all(question["options"] for question in questions),
    )
    return ask, keys


def _ask_user_question_response(ask: AgentAskUser, keys: list[str]) -> dict[str, Any]:
    if ask.answers is None:
        return {"outcome": "cancelled"}
    answers: dict[str, list[str]] = {}
    annotations: dict[str, dict[str, str]] = {}
    for key, question in zip(keys, ask.questions):
        answer = ask.answers.get(question["id"])
        if not answer:
            continue
        if any(option["label"] == answer for option in question["options"]):
            answers[key] = [answer]
        else:
            # Free-text answers go in as "Other" with the text as notes, like Grok's own UI.
            answers[key] = ["Other"]
            annotations[key] = {"notes": answer}
    response: dict[str, Any] = {"outcome": "accepted", "answers": answers}
    if annotations:
        response["annotations"] = annotations
    return response


def _exit_plan_mode_question(request_id: Any, params: dict[str, Any]) -> AgentAskUser:
    plan = params.get("planContent")
    plan = plan.strip() if isinstance(plan, str) else ""
    return AgentAskUser(
        call_id=_ext_call_id(params, request_id),
        questions=[
            {
                "id": "plan",
                "header": "Plan ready",
                "question": plan or "The agent finished planning without writing a plan.",
                "options": [
                    {
                        "label": PLAN_APPROVE_LABEL,
                        "description": "Leave plan mode and start implementing.",
                    },
                    {
                        "label": PLAN_KEEP_PLANNING_LABEL,
                        "description": "Stay in plan mode and keep refining the plan.",
                    },
                ],
            }
        ],
        # Leaving plan mode unlocks edits, so never pick an answer for the user.
        auto_resolve=False,
    )


def _exit_plan_mode_response(ask: AgentAskUser) -> dict[str, Any]:
    answer = (ask.answers or {}).get("plan")
    if answer == PLAN_APPROVE_LABEL:
        return {"outcome": "approved"}
    if not answer or answer == PLAN_KEEP_PLANNING_LABEL:
        return {"outcome": "abandoned", "feedback": PLAN_KEEP_PLANNING_FEEDBACK}
    return {"outcome": "request_changes", "feedback": answer}


async def _ask_user_events(client: AcpClient, message: dict[str, Any]) -> AsyncIterator[AgentEvent]:
    """Surface one Grok question or plan approval request, then reply to Grok."""
    request_id = message.get("id")
    method = message.get("method")
    params = _ext_params(message.get("params"))
    if method in XAI_EXIT_PLAN_MODE_METHODS:
        ask = _exit_plan_mode_question(request_id, params)
        yield ask
        await client.respond(request_id, _exit_plan_mode_response(ask))
        return
    parsed = _ask_user_question(request_id, params)
    if parsed is None:
        await client.respond_error(request_id, -32602, f"Invalid params for {method}")
        return
    ask, keys = parsed
    yield ask
    await client.respond(request_id, _ask_user_question_response(ask, keys))


def _auth_method(env: dict[str, str]) -> str:
    return "xai.api_key" if env.get("XAI_API_KEY", "").strip() else "cached_token"


def _approval_mode(chat_params: dict[str, Any]) -> str:
    mode = chat_params.get("tool_approval_mode")
    if mode in {"ask", "auto", "full"}:
        return mode
    return "full" if chat_params.get("auto_approve_tools") else "auto"


def _session_meta(approval_mode: str) -> dict[str, bool]:
    """Grok's permission mode for the chat's approval mode.

    Sent on every session/new and session/load, so `[ui] permission_mode` in Grok's
    config cannot override it. `auto` is Grok's auto-review: routine calls run, and
    calls its classifier will not allow are blocked and reported to the model.
    """
    if approval_mode == "full":
        return {"yoloMode": True}
    return {"yoloMode": False, "autoMode": approval_mode == "auto"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _context_update(update: Any) -> tuple[int | None, int | None]:
    """Context tokens and window from one Grok session notification.

    `response_completed` reports each model call: its prompt (`input_tokens` excludes
    the cached part) plus its reply is what the conversation holds after the call.
    Auto-compaction reports the size it shrank the conversation to.
    """
    if not isinstance(update, dict):
        return None, None
    kind = update.get("sessionUpdate")
    if kind == "response_completed":
        usage = update.get("usage") if isinstance(update.get("usage"), dict) else {}
        tokens = sum(
            _positive_int(usage.get(key)) or 0
            for key in (
                "input_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "output_tokens",
            )
        )
        return tokens or None, None
    if kind == "auto_compact_started":
        return None, _positive_int(update.get("context_window"))
    if kind == "auto_compact_completed":
        return _positive_int(update.get("tokens_after")), None
    return None, None


def _turn_usage(
    prompt_result: Any, context_tokens: int | None, context_window: int | None
) -> dict[str, Any] | None:
    """Usage for the chat message: the turn's billed totals plus the context it ended with."""
    meta = prompt_result.get("_meta") if isinstance(prompt_result, dict) else None
    totals = meta.get("usage") if isinstance(meta, dict) else None
    usage: dict[str, Any] = {}
    if isinstance(totals, dict):
        for source, target in (
            ("inputTokens", "input_tokens"),
            ("outputTokens", "output_tokens"),
            ("totalTokens", "total_tokens"),
            ("cachedReadTokens", "cache_read_input_tokens"),
            ("reasoningTokens", "reasoning_tokens"),
        ):
            value = _positive_int(totals.get(source))
            if value:
                usage[target] = value
    if context_tokens:
        usage["context_tokens"] = context_tokens
    if context_window:
        usage["context_window"] = context_window
    return usage or None


def _model_state(setup: dict[str, Any], initialize_meta: Any) -> dict[str, Any]:
    """`models` from session setup, else the copy Grok puts in its initialize `_meta`."""
    models = setup.get("models")
    if not isinstance(models, dict) and isinstance(initialize_meta, dict):
        models = initialize_meta.get("modelState")
    return models if isinstance(models, dict) else {}


def _session_reasoning_effort(setup: dict[str, Any]) -> str | None:
    for option in setup.get("configOptions") or []:
        if isinstance(option, dict) and option.get("id") == "reasoning_effort":
            value = option.get("currentValue")
            return value if isinstance(value, str) and value else None
    return None


def grok_model_options(
    setup: dict[str, Any], initialize_meta: Any = None
) -> dict[str, dict[str, Any]]:
    """Reasoning efforts and context windows Grok offers per model, with Grok's defaults.

    The default effort is the session's (`[models].default_reasoning_effort` in Grok's
    config) when the model offers it, else the model's own default. The default context
    window is the model's catalog window.
    """
    session_effort = _session_reasoning_effort(setup)
    result: dict[str, dict[str, Any]] = {}
    for item in _model_state(setup, initialize_meta).get("availableModels") or []:
        model_id = item.get("modelId") if isinstance(item, dict) else None
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        meta = item.get("_meta") if isinstance(item.get("_meta"), dict) else {}

        efforts: list[dict[str, str]] = []
        recommended = None
        if meta.get("supportsReasoningEffort") is True:
            for raw in meta.get("reasoningEfforts") or []:
                value = raw.get("value") if isinstance(raw, dict) else None
                if not isinstance(value, str) or not value:
                    continue
                label = raw.get("label")
                description = raw.get("description")
                efforts.append(
                    {
                        "value": value,
                        "label": label if isinstance(label, str) and label else value,
                        "description": description if isinstance(description, str) else "",
                    }
                )
                if raw.get("default") is True:
                    recommended = value
        values = [effort["value"] for effort in efforts]
        default_effort = next(
            (
                value
                for value in (session_effort, meta.get("reasoningEffort"), recommended)
                if value in values
            ),
            values[0] if values else None,
        )

        windows = [
            window
            for window in (_positive_int(raw) for raw in meta.get("contextWindows") or [])
            if window
        ]
        default_window = _positive_int(meta.get("totalContextTokens"))
        if default_window and default_window not in windows:
            windows.insert(0, default_window)
        default_window = default_window or (windows[0] if windows else None)

        result[model_id.strip()] = {
            "reasoning_efforts": efforts,
            "default_reasoning_effort": default_effort,
            "context_windows": windows,
            "default_context_window": default_window,
        }
    return result


async def _apply_session_settings(
    client: AcpClient, model: str, chat_params: dict[str, Any]
) -> int | None:
    """Carry the composer's model, context window and reasoning effort into the session.

    Only values Grok offers for the model are sent, so a leftover choice made for another
    model falls back to Grok's own default. `grok agent --reasoning-effort` does not reach
    ACP sessions, which is why the effort goes through session/set_config_option.
    Returns the context window the session runs with, when Grok lists it.
    """
    setup = client.setup_result
    initialize_meta = client.initialize_result.get("_meta")
    target = model
    if model == "default":
        current = _model_state(setup, initialize_meta).get("currentModelId")
        target = current if isinstance(current, str) else ""
    options = grok_model_options(setup, initialize_meta).get(target, {})

    window = _positive_int(chat_params.get("context_window"))
    meta = {"contextWindow": window} if window in options.get("context_windows", []) else None
    if target and (model != "default" or meta):
        await client.set_model(target, meta=meta)

    effort = chat_params.get("reasoning_effort")
    if any(option["value"] == effort for option in options.get("reasoning_efforts", [])):
        await client.set_config_option("reasoning_effort", effort)
    return meta["contextWindow"] if meta else options.get("default_context_window")


# ── One live `grok agent` process per chat ──────────────────
#
# Grok tracks how full its context window is inside the process. A process that only
# reloads the session (session/load) starts that count near zero, so Grok's turn-start
# auto-compaction never fires. Keeping each chat's process between turns keeps the
# count; idle processes are closed after GROK_IDLE_TIMEOUT_SECONDS and beyond the
# GROK_MAX_IDLE_PROCESSES most recently used ones, since each holds its MCP servers.


@dataclass
class _LiveGrok:
    client: AcpClient
    # Launch inputs and the permission mode, which Grok only takes at session start.
    key: tuple[Any, ...]
    settings: tuple[Any, ...] | None = None
    context_window: int | None = None
    last_used: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def alive(self) -> bool:
        proc = self.client.proc
        return proc is not None and proc.returncode is None


_live_sessions: dict[str, _LiveGrok] = {}
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _close_live(session_id: str, live: _LiveGrok) -> None:
    if _live_sessions.get(session_id) is live:
        _live_sessions.pop(session_id)
    await live.client.close()


async def close_grok_session(session_id: str) -> None:
    """Stop the Grok process kept for one session, e.g. when its chat is deleted."""
    live = _live_sessions.get(session_id)
    if live:
        await _close_live(session_id, live)


async def close_all_grok_sessions() -> None:
    for session_id, live in list(_live_sessions.items()):
        await _close_live(session_id, live)


async def _checkout(session_id: str | None, key: tuple[Any, ...]) -> _LiveGrok | None:
    """The chat's running process, held for one turn, if it can serve this turn."""
    live = _live_sessions.get(session_id) if session_id else None
    if live is None:
        return None
    await live.lock.acquire()
    if _live_sessions.get(session_id) is live and live.key == key and live.alive():
        return live
    # The approval mode or launch inputs changed, or the process died: start over.
    live.lock.release()
    await _close_live(session_id, live)
    return None


async def _trim_idle() -> None:
    """Close processes idle too long, and the oldest beyond the idle limit."""
    now = time.monotonic()
    idle = sorted(
        (
            (session_id, live)
            for session_id, live in _live_sessions.items()
            if not live.lock.locked()
        ),
        key=lambda item: item[1].last_used,
        reverse=True,
    )
    for index, (session_id, live) in enumerate(idle):
        if (
            index >= GROK_MAX_IDLE_PROCESSES
            or now - live.last_used >= GROK_IDLE_TIMEOUT_SECONDS
            or not live.alive()
        ):
            await _close_live(session_id, live)


async def _reap_idle_sessions() -> None:
    while _live_sessions:
        await asyncio.sleep(60)
        await _trim_idle()


_reaper: asyncio.Task | None = None


def _check_in(session_id: str, live: _LiveGrok) -> None:
    """Keep the process for the chat's next turn."""
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


async def _discard_stale_events(client: AcpClient) -> None:
    """Drop what Grok sent between turns; answer requests so nothing waits on them."""
    while not client.events.empty():
        message = client.events.get_nowait()
        if "id" not in message or not message.get("method"):
            continue
        if message["method"] == ACP_PERMISSION_METHOD:
            await client.respond(message["id"], {"outcome": {"outcome": "cancelled"}})
        else:
            await client.respond_error(message["id"], -32603, "No turn is running")


async def _compact_if_full(
    client: AcpClient, resume_state: dict[str, Any] | None, context_window: int | None
) -> None:
    """Compact a reloaded session that ended its last turn past Grok's threshold.

    A reloaded session's context count starts near zero, so Grok would not compact
    before this turn's first model call on its own.
    """
    tokens = _positive_int((resume_state or {}).get("context_tokens"))
    if tokens and context_window and tokens * 100 >= context_window * GROK_AUTO_COMPACT_PERCENT:
        with suppress(Exception):
            await client.request(XAI_COMPACT_METHOD, {"sessionId": client.session_id})


async def run_grok_agent(
    *,
    profile: dict[str, Any],
    model: str,
    workspace: str,
    messages: list[dict[str, Any]],
    system_prompt: str,
    chat_params: dict[str, Any],
    resume_state: dict[str, Any] | None,
    attachments: PreparedAgentAttachments,
    identity=None,
) -> AsyncIterator[AgentEvent]:
    env = env_for(identity, workspace) if identity and identity.is_pam else os.environ.copy()
    if profile.get("home"):
        env["HOME"] = os.path.expanduser(str(profile["home"]))
    env["GROK_OAUTH2_REFERRER"] = "cptr"

    session_id = None
    if resume_state and isinstance(resume_state.get("session_id"), str):
        session_id = resume_state["session_id"]
    approval_mode = _approval_mode(chat_params)
    command = str(profile["command"])
    key = (
        command,
        workspace,
        env.get("HOME"),
        identity.username if identity else None,
        approval_mode,
    )
    settings = (model, chat_params.get("context_window"), chat_params.get("reasoning_effort"))

    live = await _checkout(session_id, key)
    if live is None:
        live = _LiveGrok(
            client=AcpClient(
                command=command,
                args=["agent", "stdio"],
                cwd=workspace,
                env=env,
                auth_method_id=_auth_method(env),
                resume_session_id=session_id,
                auto_approve_permissions=approval_mode == "full",
                extension_requests=XAI_EXTENSION_REQUESTS | {ACP_PERMISSION_METHOD},
                session_meta=_session_meta(approval_mode),
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
            await _discard_stale_events(client)
        else:
            await client.start()
        if live.settings == settings:
            context_window = live.context_window
        else:
            context_window = await _apply_session_settings(client, model, chat_params)
            live.settings, live.context_window = settings, context_window
        if session_id and not reused:
            await _compact_if_full(client, resume_state, context_window)
        context_tokens: int | None = None

        prompt = turn_prompt_text(messages, system_prompt, resumed=bool(session_id))

        images = [
            {"data": image.base64, "mimeType": image.mime_type} for image in attachments.images
        ]
        prompt_task = asyncio.create_task(client.prompt(prompt, images=images))
        try:
            async for event in acp_event_stream(client, prompt_task):
                if "id" in event and event.get("method") == ACP_PERMISSION_METHOD:
                    async for permission_event in acp_permission_events(client, event):
                        yield permission_event
                    continue
                if "id" in event and event.get("method") in XAI_EXTENSION_REQUESTS:
                    async for ask_event in _ask_user_events(client, event):
                        yield ask_event
                    continue
                params = event.get("params") if isinstance(event.get("params"), dict) else {}
                if event.get("method") in XAI_SESSION_NOTIFICATIONS:
                    # Subagents run in their own sessions; only this one fills the chat's window.
                    if params.get("sessionId") == client.session_id:
                        tokens, window = _context_update(params.get("update"))
                        context_window = window or context_window
                        if tokens:
                            context_tokens = tokens
                            yield AgentContextUsage(tokens=tokens, window=context_window)
                    continue
                text = acp_text_from_update(params)
                if text:
                    yield AgentTextDelta(text)
                tool = acp_tool_from_update(params)
                if tool:
                    yield AgentToolUpdate(**tool)
            prompt_result = await prompt_task
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
                with suppress(asyncio.CancelledError):
                    await prompt_task

        usage = _turn_usage(prompt_result, context_tokens, context_window)
        # Checked in before AgentDone: the consumer stops reading at AgentDone.
        if client.session_id:
            _check_in(client.session_id, live)
            kept = True
        yield AgentDone(
            usage=usage,
            resume_state={
                "profile_id": profile["id"],
                "session_id": client.session_id,
                "workspace": workspace,
                "model": model,
                # For a later process that reloads the session; see _compact_if_full.
                "context_tokens": context_tokens
                or _positive_int((resume_state or {}).get("context_tokens")),
                "context_window": context_window,
            },
        )
    except asyncio.CancelledError:
        await client.cancel()
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced in chat.
        yield AgentError(str(exc))
    finally:
        # A stopped or failed turn can leave Grok mid-turn or waiting on an answer, so
        # the process is not reused; the next turn reloads the session.
        if not kept:
            if live.lock.locked():
                live.lock.release()
            await _close_live(client.session_id or "", live)
